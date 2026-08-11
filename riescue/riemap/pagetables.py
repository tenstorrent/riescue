# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
"""Construct RISC-V page-table entries and table trees."""

import collections
import functools
import logging
from typing import TYPE_CHECKING, Iterable, Optional

import riescue.lib.common as common
import riescue.lib.enums as RV
import riescue.lib.raw_attributes as raw_attributes
from riescue.lib.rand import RandNum
from riescue.riemap.config import PagingParams
from riescue.riemap.attributes import PTE_BASES, pt_attrs_schema
from riescue.riemap.addrgen import AddrGen
from riescue.riemap import resolve
from riescue.riemap.request import Choice

if TYPE_CHECKING:
    from riescue.riemap.page_map import Page, PageMap

log = logging.getLogger(__name__)

# Natural non-leaf (pointer) PTE value: valid, no permissions. Matches the builder's
# attribute-based coloring default (``v=1``, rest 0) and :func:`resolve._is_default_pointer_bit`.
_DEFAULT_POINTER_PTE_VALUE = 0x1

# STEE's secure alias bit, set on the physical target of a leaf drawn from secure memory.
_SECURE_ALIAS_BIT = 0x0080000000000000


def gstage_nonleaf_geometry(page: "Page", g_mode: RV.RiscvPagingModes, pt_level: int) -> "tuple[int, int, RV.RiscvPageSizes]":
    """Address constraint ``(size, mask, pagesize)`` for the frame a VS node needs.

    Under a g-stage the frame is a GPA that must itself be translated, so its geometry is the
    geometry of that translation: the :class:`~riescue.riemap.request.PTGPage` declared for this
    VS level carries the pagesize, which decides how many g-stage levels translate it. An
    undeclared node (a single-stage tree, or a g-stage map whose own frames are plain HPAs) takes
    4 KiB.

    ``PTGPage`` PTE bits are attributes, not an address reservation. A consumer that needs a
    runtime-rewritten pointer PTE to own its full span states that separately with
    ``Page.reserve_size``; synthesized frames keep their declared pagesize geometry.

    Module-level because both sides of the split need it: the BUILDER places the frame (and so
    needs the size/mask to draw it) and the WALKER emits its g-stage identity (and so needs the
    pagesize). They must agree, so there is one implementation."""
    node = page.gstage_nodes.get(pt_level) if g_mode != RV.RiscvPagingModes.DISABLE else None
    if node is None:
        return (RV.RiscvPageSizes.memory(RV.RiscvPageSizes.S4KB), RV.RiscvPageSizes.address_mask(RV.RiscvPageSizes.S4KB), RV.RiscvPageSizes.S4KB)
    pagesize = page.resolved_gstage_pagesizes.get(pt_level)
    if pagesize is None:
        pagesize = node.pagesize.preferred if isinstance(node.pagesize, Choice) else node.pagesize
    return (
        RV.RiscvPageSizes.memory(pagesize),
        RV.RiscvPageSizes.address_mask(pagesize),
        pagesize,
    )


@functools.lru_cache(maxsize=None)
def _level_attr_names(level: int) -> "tuple[tuple[str, str], ...]":
    """``(attr, attr_level{level})`` pairs a :class:`PTAttrs` at ``level`` copies down."""
    return tuple((attr, f"{attr}_level{level}") for attr in PTE_BASES)


class PTTable:
    """
    Actually holds all the pagetables entries and pointer to the next table
    """

    def __init__(
        self,
        base_addr: int,
        leaf: bool = False,
        capacity: int = 512,
        input_addr: "Optional[int]" = None,
    ):
        # ``base_addr`` is where the table bytes live. ``input_addr`` is the
        # address encoded in the parent PTE (a GPA for a VS table). They happen
        # to be equal for single-stage and explicitly identity-backed frames.
        self.base_addr: int = base_addr
        self.input_addr: int = base_addr if input_addr is None else input_addr
        self.leaf: bool = leaf
        # Max distinct slots this frame can hold (2 ** index-field width). A frame packed
        # by several aliased walk positions past this is over-packed -> raise.
        self.capacity: int = capacity
        self.table: collections.OrderedDict[int, PTEntry] = collections.OrderedDict()

    def __str__(self) -> str:
        strn = ""
        for index, entry in self.table.items():
            strn += f"index: 0x{index:x} -> {self.get_entry(index).get_value():x}\n"

        return strn

    def insert_entry(self, entry: "PTEntry", index: int) -> None:
        """
        Insert an entry at given index
        """
        if index not in self.table and len(self.table) >= self.capacity:
            raise ValueError(f"page-table frame at 0x{self.base_addr:x} over-packed: >{self.capacity} distinct slots")
        self.table[index] = entry

    def entry_exists(self, index: int) -> bool:
        if index in self.table:
            return True
        else:
            return False

    def get_entry(self, index: int) -> "PTEntry":
        """
        Return the entry at given index
        """
        return self.table[index]

    def get_entries(self) -> Iterable["PTEntry"]:
        return self.table.values()


class PTAttrs(raw_attributes.RawAttributes):
    """
    Model pagetable attributes for a given page

    Only the PTE's own fields are materialized. The complete schema is the
    ``{attr} x level x glevel`` cross product, while one PTE reads only a
    small subset. :meth:`__getattr__` resolves other schema keys from the
    declaring page on demand using page-value-then-schema-default precedence.
    """

    base_attrs: dict[str, Optional[int]] = pt_attrs_schema()
    # The PTE's own fields: everything in the schema that is not a per-level declaration.
    own_attrs: dict[str, Optional[int]] = {key: value for key, value in base_attrs.items() if "level" not in key}

    def __init__(self, rng: RandNum, featmgr: PagingParams, level: int, page: "Optional[Page]" = None, leaf: bool = False):
        page_attrs = page.attrs if page is not None else {}
        values = self.__dict__
        # Set before anything can miss on an attribute lookup: __getattr__ reads it.
        values["_page_attrs"] = page_attrs
        # What ``RawAttributes.__init__`` published for ``get``/``set``/``set_*`` validation.
        raw_attributes.RawAttributes.valid_attrs = PTAttrs.base_attrs
        values.update(PTAttrs.own_attrs)
        self.featmgr: PagingParams = featmgr
        self.leaf: bool = leaf

        for attr_name in PTAttrs.own_attrs:
            value = page_attrs.get(attr_name)
            # None means that the schema default remains in effect.
            if value is not None:
                values[attr_name] = value

        # Resolve each PTE field from its level-specific declaration. A/D and
        # PBMT policy is established by the declaring layer; table construction
        # remains deterministic.
        defaults = PTAttrs.base_attrs
        for attr, level_attr in _level_attr_names(level):
            value = page_attrs.get(level_attr)
            values[attr] = defaults[level_attr] if value is None else value

    def __getattr__(self, name):
        """Resolve a per-level schema key that ``__init__`` did not materialize."""
        if name in PTAttrs.base_attrs:
            value = self.__dict__["_page_attrs"].get(name)
            return PTAttrs.base_attrs[name] if value is None else value
        return super().__getattr__(name)

    def __str__(self) -> str:
        return ", ".join(f"{attr}={int(self.get(attr))}" for attr in PTAttrs.own_attrs)

    def get_value(self) -> int:
        value = 0
        if TYPE_CHECKING:
            assert isinstance(self.rsw, int)
            assert isinstance(self.reserved, int)
            assert isinstance(self.pbmt, int)
            assert isinstance(self.n, int)
            assert isinstance(self.v, int)
            assert isinstance(self.r, int)
            assert isinstance(self.w, int)
            assert isinstance(self.x, int)
            assert isinstance(self.u, int)
            assert isinstance(self.g, int)
            assert isinstance(self.a, int)
            assert isinstance(self.d, int)

        value |= common.set_bitn(original=value, bit=0, value=bool(self.v))
        value |= common.set_bitn(original=value, bit=1, value=bool(self.r))
        value |= common.set_bitn(original=value, bit=2, value=bool(self.w))
        value |= common.set_bitn(original=value, bit=3, value=bool(self.x))
        value |= common.set_bitn(original=value, bit=4, value=bool(self.u))
        value |= common.set_bitn(original=value, bit=5, value=bool(self.g))
        value |= common.set_bitn(original=value, bit=6, value=bool(self.a))
        value |= common.set_bitn(original=value, bit=7, value=bool(self.d))
        value |= common.set_bits(original_value=value, bit_hi=9, bit_lo=8, value=self.rsw)
        value |= common.set_bits(original_value=value, bit_hi=60, bit_lo=54, value=self.reserved)
        value |= common.set_bits(original_value=value, bit_hi=62, bit_lo=61, value=self.pbmt)
        value |= common.set_bitn(original=value, bit=63, value=bool(self.n))

        return value


class PTEntry:
    """
    Model and store each pagetable entry here
    It holds following information
    """

    def __init__(
        self,
        basetable: PTTable,
        pt_attr: PTAttrs,
        level: int,
        target_addr: "Optional[int]" = None,
    ):
        if basetable.base_addr is None:
            raise ValueError("basetable cannot be None for PTEntry")
        self.basetable: PTTable = basetable
        self.target_addr = basetable.input_addr if target_addr is None else target_addr
        self.pt_attr: PTAttrs = pt_attr
        self.level: int = level
        self.leaf: bool = False
        # For a VS-stage non-leaf pointer whose synthesized g-stage identity was declared by
        # a PTGPage: the PTGPage + the child table's resolved GPA, surfaced via WalkStep so a
        # consumer can recover the declared g-stage node. None for auto / non-VS entries.
        self.ptg_page: "Optional[object]" = None
        self.ptg_gpa: "Optional[int]" = None

    def get_base_addr(self) -> int:
        return self.target_addr

    def get_pt_attrs(self) -> PTAttrs:
        return self.pt_attr

    def get_value(self) -> int:
        log.debug(f"PTEntry: get_value: {self.target_addr:x}, " f"{self.pt_attr.get_value():x}")
        val = ((self.target_addr >> 12) << 10) | self.pt_attr.get_value()

        return val


class Pagetables:
    def __init__(self, page: "Page", page_map: "PageMap", featmgr: PagingParams, addrgen: AddrGen):
        """
        Create pagetables for "page" in given "page_map"
          - return a list of PTEntry(s) with entires at each level
          - update the page_map.base_table with the newly created pagetables
        """
        self.page: "Page" = page
        self.page_map: "PageMap" = page_map

        self.featmgr: PagingParams = featmgr
        self.addrgen: AddrGen = addrgen

        self.set_level()

    def set_level(self) -> None:
        self.max_levels = self.page_map.max_levels

    def create_pagetables(self, rng: RandNum) -> None:
        """
        For a given linear address and physical address, create pagetables
        for a page in given page_map
        Following things we need to know:
          - linear_addr
          - physical_addr
          - page_size
          - page_map mode (sv32, sv39, sv48, sv57)
          - number of levels of pagetables to create
          - pagetable attributes that are specified in the page_mapping()
        """
        basetable = self.page_map.basetable
        if basetable is None:
            raise ValueError("PageMap basetable is None - initialize() must be called before create_pagetables()")

        current_level = self.max_levels - 1
        for _ in range(self.max_levels - 1):
            if current_level == self.page.pt_leaf_level or basetable is None:
                break
            basetable = self._create_pt_non_leaf(rng, pt_level=current_level, base_table=basetable)
            current_level -= 1

        # Create the leaf entry
        # basrtable can be None if we had an overlap with another address with different pagesize
        if basetable is not None:
            self._create_pt_leaf(rng, base_table=basetable, pt_level=current_level)

    def _create_pt_non_leaf(self, rng: RandNum, pt_level: int, base_table: PTTable) -> Optional[PTTable]:
        """
        Create the non-leaf pt_entry
          - no-leaf entry is marked with XWR=3'b000 in the entry
        """
        # Since we are creating non-leaf entry, clear X/W/R bits
        pt_attr = PTAttrs(rng=rng, featmgr=self.featmgr, level=pt_level, page=self.page)

        # Locate the slot for this walk level.
        index = self.calc_index_from_va(level=pt_level)

        # The builder places this level's child table at a fixed base.
        pinned_base = self.page.pinned_frame_bases.get(pt_level)
        pinned_backing = self.page.pinned_frame_backings.get(
            pt_level,
            pinned_base,
        )
        # Attribution only: the object the consumer declared, not the merged per-frame identity
        # in ``gstage_nodes`` (which every sharer of the frame holds, so it cannot tell one
        # declaration from another).
        declared_ptg = self.page.gstage_node_declarations.get(pt_level)

        if base_table.entry_exists(index):
            # This (frame, slot) already holds a pointer PTE. Reuse it when the child base
            # matches and the PTE bits agree -- or when one walk only restates the natural
            # pointer default (v=1, rest 0) and the other forces non-default bits. Declared
            # forces no longer reserve the pointer's whole span (attribute-based coloring isolates
            # conflicting FORCES); a structural/root self-map that falls under a forced
            # pointer still has to walk through it, and it only needs a valid pointer, not
            # the default bits. Upgrade the stored attrs to the forced value so later
            # readers see one coherent PTE. Two unequal non-default forces remain a hard
            # conflict (coloring should have split them).
            pt_entry = base_table.get_entry(index)
            if pt_entry.leaf:
                # Coarse leaf already covers this slot. Same target is a restatement;
                # a different target or different leaf bits is a hard conflict.
                if not self._coarse_leaf_covers(pt_entry, pt_level):
                    raise ValueError(
                        f"page-table leaf/deeper conflict at index 0x{index:x} in frame 0x{base_table.base_addr:x} "
                        f"(level {pt_level}, page 0x{self.page.lin_addr:x}->0x{self.page.phys_addr:x}): "
                        f"an existing level-{pt_level} leaf (base 0x{pt_entry.get_base_addr():x}) already maps this "
                        "span to a different target, so the deeper mapping cannot be installed"
                    )
                intended_leaf = PTAttrs(rng=rng, featmgr=self.featmgr, level=pt_level, page=self.page, leaf=True)
                if pt_entry.pt_attr.get_value() != intended_leaf.get_value():
                    raise ValueError(
                        f"page-table leaf/deeper conflict at index 0x{index:x} in frame 0x{base_table.base_addr:x} "
                        f"(level {pt_level}, page 0x{self.page.lin_addr:x}->0x{self.page.phys_addr:x}): "
                        f"an existing level-{pt_level} leaf maps the same target with attrs 0x{pt_entry.pt_attr.get_value():x} "
                        f"but this mapping requires 0x{intended_leaf.get_value():x}"
                    )
                return None
            intended_child = pinned_base if pinned_base is not None else pt_entry.get_base_addr()
            if pt_entry.get_base_addr() != intended_child:
                raise ValueError(
                    f"page-table non-leaf slot conflict at index 0x{index:x} in frame 0x{base_table.base_addr:x} "
                    f"(level {pt_level}, page 0x{self.page.lin_addr:x}->0x{self.page.phys_addr:x}): "
                    f"existing (base 0x{pt_entry.get_base_addr():x}, attr 0x{pt_entry.pt_attr.get_value():x}) "
                    f"vs new (base 0x{intended_child:x}, attr 0x{pt_attr.get_value():x})"
                )
            existing_attr = pt_entry.pt_attr.get_value()
            new_attr = pt_attr.get_value()
            if existing_attr != new_attr:
                default_attr = _DEFAULT_POINTER_PTE_VALUE
                if existing_attr == default_attr:
                    pt_entry.pt_attr = pt_attr
                elif new_attr != default_attr:
                    raise ValueError(
                        f"page-table non-leaf slot conflict at index 0x{index:x} in frame 0x{base_table.base_addr:x} "
                        f"(level {pt_level}, page 0x{self.page.lin_addr:x}->0x{self.page.phys_addr:x}): "
                        f"existing (base 0x{pt_entry.basetable.base_addr:x}, attr 0x{existing_attr:x}) "
                        f"vs new (base 0x{intended_child:x}, attr 0x{new_attr:x})"
                    )
            base_table = pt_entry.basetable
            # This pointer can be shared by several mappings. Preserve declaration-object
            # attribution only while it is genuinely unique; reporting whichever PTGPage
            # happened to construct an interned edge first is misleading. The canonical
            # emitted geometry remains available from the g-stage SpaceResult by GPA.
            if pt_entry.ptg_page is not declared_ptg:
                pt_entry.ptg_page = None
                # The shared frame identity is still unambiguous even though its declaring
                # object is not. Keep the GPA so result readers can resolve canonical metadata.
                pt_entry.ptg_gpa = pt_entry.get_base_addr()
        else:
            # Every frame this walk needs was placed by the builder before the tree was built
            # (``PageTableBuilder._place_pt_node_frames``): the walker is purely constructive and
            # draws no addresses of its own. A frame is also a mapped address in the domain it is
            # identity-mapped into, and only the solve can see that domain's reservations -- a
            # draw made here, after the solve, cannot.
            if pinned_base is None:
                raise RuntimeError(
                    f"no frame placed for the level-{pt_level} node of 0x{self.page.lin_addr:x} "
                    f"(pinned levels: {sorted(self.page.pinned_frame_bases)}): the builder must place every "
                    "page-table node frame before create_pagetables()"
                )
            input_addr = pinned_base
            backing_addr = pinned_backing
            # Whether that frame was drawn as secure (STEE) travels with the pin: it decides the
            # attrs of the g-stage identity emitted for it below, so it cannot be re-rolled here.
            secure_access_generated = self.page.pinned_frame_secure.get(pt_level, False)
            pagesize = self._gstage_nonleaf_pagesize(pt_level)

            # Intern the child table by base address: a fresh (auto) draw is always unique
            # so a new table is made and its structural side effects run once; a pinned or
            # aliased frame reuses the existing table object (packing) and skips the side
            # effects (already emitted for that frame).
            next_basetable = self.page_map.tables_by_base.get(backing_addr)
            if next_basetable is None:
                if declared_ptg is not None and not self.page_map.g_map and self.page_map.paging_g_mode != RV.RiscvPagingModes.DISABLE:
                    self._emit_gstage_identity(
                        base_addr=input_addr,
                        backing_addr=backing_addr,
                        pt_level=pt_level,
                        pagesize=pagesize,
                        secure=secure_access_generated,
                    )

                next_basetable = PTTable(
                    base_addr=backing_addr,
                    input_addr=input_addr,
                    capacity=self.page_map._entries_per_table(pt_level - 1),
                )
                self.page_map.tables_by_base[backing_addr] = next_basetable

            pt_entry = PTEntry(
                basetable=next_basetable,
                pt_attr=pt_attr,
                level=pt_level,
                target_addr=input_addr,
            )
            # Surface a declared PTGPage for this VS node (read-back via WalkStep).
            if declared_ptg is not None:
                pt_entry.ptg_page = declared_ptg
                pt_entry.ptg_gpa = input_addr
            base_table.insert_entry(entry=pt_entry, index=index)

            # Update the base_table to return for the next level of pagetables
            base_table = next_basetable

        return base_table

    def _coarse_leaf_covers(self, existing: "PTEntry", pt_level: int) -> bool:
        """Whether an existing coarse leaf at ``pt_level`` already translates this page's PA.

        Address agreement alone is not enough to install nothing -- the caller also
        compares packed leaf PTE bits so a same-target restatement cannot hide a
        permission disagreement.
        """
        lin_addr = self.page.lin_addr
        phys_addr = self.page.phys_addr
        if lin_addr is None or phys_addr is None:
            return False
        _index_hi, index_lo = RV.RiscvPagingModes.index_bits(mode=self.page_map.paging_mode, level=pt_level)
        offset_mask = (1 << index_lo) - 1
        translated = (existing.get_base_addr() & ~offset_mask) | (lin_addr & offset_mask)
        # STEE alias bit is a property of the frame, not the span geometry.
        return (translated & ~_SECURE_ALIAS_BIT) == (phys_addr & ~_SECURE_ALIAS_BIT)

    def _create_pt_leaf(self, rng: RandNum, base_table: PTTable, pt_level: int) -> None:
        """
        Create the leaf pt_entry
        """
        # PBMT policy is supplied through the page's ``pbmt`` and
        # ``pbmt_level*`` attributes, keeping table construction deterministic.
        pt_attr = PTAttrs(rng=rng, featmgr=self.featmgr, level=pt_level, page=self.page, leaf=True)

        index = self.calc_index_from_va(level=pt_level)

        phys_addr = self.page.phys_addr
        if phys_addr is None:
            raise ValueError("Physical address is None for page")
        if pt_attr.secure and (self.page_map.g_map or self.page_map.paging_g_mode == RV.RiscvPagingModes.DISABLE):
            phys_addr |= 0x0080000000000000

        # A 64 KiB page is always 16 contiguous 4 KiB leaf PTEs. Whether they are a *NAPOT*
        # block (Svnapot, N=1) decides what PPN they carry, so the two must move together.
        if self.page.pagesize == RV.RiscvPageSizes.S64KB:
            # Auto-set N unless the page resolved n=0. The base ``n`` key is the authoritative
            # answer (``_install_pt_node_attrs`` writes the resolved 0/1 for every 64 KiB
            # mapping); a missing key means no mapping declared one.
            if self.page.attrs.get("n") is None or self.page.attrs.get("n") == 1:
                pt_attr.n = 1  # type: ignore[attr-defined]

            # 16 contiguous PTEs starting at the 16-entry aligned boundary; the block is the
            # packing unit -- an occupied slot must match (idempotent) or it is a conflict.
            base_index = index & ~0xF
            napot = bool(pt_attr.n)  # type: ignore[attr-defined]
            # With N=1 every PTE in the block is identical and carries the NAPOT encoding
            # PPN[3:0] = 0x8 (bits [15:12]); the hardware substitutes VPN[3:0] for those bits
            # at translation time. With N=0 there is no Svnapot: the encoding would be read as
            # a literal PPN and every VA in the block would translate 0x8000 off its PA, so the
            # block must instead be a plain 16 x 4 KiB mapping, PTE i carrying PA + i * 4 KiB.
            # (An n=0 64 KiB page is a deliberate "no Svnapot" request, not a malformed PTE.)
            napot_base = (phys_addr & ~0xFFFF) if not napot else (phys_addr & ~0xF000) | 0x8000
            napot_entry: Optional[PTEntry] = None
            for i in range(16):
                napot_index = base_index + i
                entry_phys = napot_base if napot else napot_base + (i << 12)
                napot_leaf_basetable = PTTable(base_addr=entry_phys, leaf=True)
                napot_pt_attr = PTAttrs(rng=rng, featmgr=self.featmgr, level=pt_level, page=self.page, leaf=True)
                napot_pt_attr.n = pt_attr.n  # type: ignore[attr-defined]
                napot_entry = PTEntry(basetable=napot_leaf_basetable, pt_attr=napot_pt_attr, level=pt_level)
                napot_entry.leaf = True
                if base_table.entry_exists(napot_index):
                    napot_entry = self._pack_leaf(base_table, napot_index, napot_entry, entry_phys, pt_level)
                else:
                    base_table.insert_entry(entry=napot_entry, index=napot_index)

            log.debug(f"insert 64KB leaf entries base_index {base_index:x} at level {pt_level} into {base_table.base_addr:x}, N={pt_attr.n}")  # type: ignore[attr-defined]
            assert napot_entry is not None
            pt_entry = napot_entry
        else:
            leaf_basetable = PTTable(base_addr=phys_addr, leaf=True)
            pt_entry = PTEntry(basetable=leaf_basetable, pt_attr=pt_attr, level=pt_level)
            # Also mark the pt_entry as leaf, so we can error out if any other address tried to use this as non-leaf
            pt_entry.leaf = True
            if base_table.entry_exists(index):
                pt_entry = self._pack_leaf(base_table, index, pt_entry, phys_addr, pt_level)
            else:
                base_table.insert_entry(entry=pt_entry, index=index)
            log.debug(f"insert leaf entry {pt_entry.get_base_addr():x} at index {index*8:x} at level {pt_level} into {base_table.base_addr:x}")

    def _pack_leaf(self, base_table: PTTable, index: int, candidate: "PTEntry", phys_addr: int, pt_level: int) -> "PTEntry":
        """Pack a leaf PTE into an already-occupied slot: reuse the existing entry when it
        is byte-identical (same target + bits), else raise a frame+slot conflict."""
        existing = base_table.get_entry(index)
        if existing.leaf and existing.get_base_addr() == phys_addr and existing.pt_attr.get_value() == candidate.pt_attr.get_value():
            return existing
        raise ValueError(
            f"leaf PTE slot conflict at index 0x{index:x} in frame 0x{base_table.base_addr:x} "
            f"(level {pt_level}, page 0x{self.page.lin_addr:x}->0x{self.page.phys_addr:x}): "
            f"existing (base 0x{existing.get_base_addr():x}, attr 0x{existing.pt_attr.get_value():x}) "
            f"vs new (base 0x{phys_addr:x}, attr 0x{candidate.pt_attr.get_value():x})"
        )

    def _emit_gstage_identity(
        self,
        base_addr: "Optional[int]",
        backing_addr: "Optional[int]",
        pt_level: int,
        pagesize: RV.RiscvPageSizes,
        secure: bool,
    ) -> None:
        """Populate the G-stage space with the mapping for a VS-stage PT node.

        The GPA being mapped and the host-physical frame backing the VS-stage PT
        node are preallocated by the builder. They are equal for an identity
        ``PTGPage`` and independently allocated otherwise. The G-stage PTE attributes
        start from the walker's
        default identity matrix for this VS level -- read live from ``page.attrs``
        through :func:`resolve.gstage_frame_pt_node_levels` (the one place the
        ``_glevel`` string grammar lives, so the core walker parses none itself) as
        ``{g_level: {base: val}}`` -- and are OVERRIDDEN by any declared ``PTGPage`` for
        this VS level (``page.gstage_nodes``, the consumer's explicit g-forcing), whose own
        per-g-level ``pt_nodes`` come flattened to the same shape by ``level_attrs()``.

        The resulting mapping is handed to ``page_map.gstage_emitter``, which a
        builder wires to route it into the builder's G-stage space. The
        emitter must be set on any two-stage source map; a missing one is a
        builder wiring bug, not a user error. Per-target dedup (the same (page,
        level) pair only ever needs one identity leaf per target) lives in the
        target ``PageMap.add_raw_pt_page``, keyed on the resolved address.
        """
        # A non-identity G-stage space is populated with explicit requests; do not
        # synthesize identity mappings for it.
        if not self.page_map.emit_gstage_identity:
            return

        if base_addr is None or backing_addr is None:
            raise ValueError("GPA or physical backing is None for g-stage frame emission")

        addr = base_addr & RV.RiscvPageSizes.address_mask(pagesize)
        physical_addr = backing_addr & RV.RiscvPageSizes.address_mask(pagesize)
        attrs: dict[str, int] = {"v": 1, "x": 1}
        if secure:
            attrs["secure"] = 1
        # Build defaults from this frame's resolved geometry. A mapping-level scalar cannot
        # describe different PTGPage geometries at different VS levels, so reading defaults
        # back from page.attrs here would reintroduce that silent merge.
        leaf_level = RV.RiscvPageSizes.pt_leaf_level(pagesize)
        matrix = {
            g_level: {
                "v": 1,
                **{base: 1 if g_level == leaf_level else 0 for base in ("u", "r", "w", "x", "a", "d")},
            }
            for g_level in range(
                leaf_level,
                RV.RiscvPagingModes.max_levels(self.page_map.paging_g_mode),
            )
        }
        node = self.page.gstage_nodes.get(pt_level)
        if node is not None:
            for g_level, bits in node.level_attrs().items():
                matrix.setdefault(g_level, {}).update(bits)
        for g_level, bits in matrix.items():
            for base, value in bits.items():
                if value is not None:
                    attrs[f"{base}_level{g_level}"] = value

        emitter = self.page_map.gstage_emitter
        if emitter is None:
            raise RuntimeError("gstage_emitter not wired for two-stage map: builder wiring bug")
        # A PTGPage may also pin the frames of this identity's OWN g-stage tree (its pt_nodes'
        # pages, resolved by the builder onto ``page.gstage_node_pins``).
        emitter(gpa=addr, hpa=physical_addr, attrs=attrs, pagesize=pagesize, pinned_frame_bases=self.page.gstage_node_pins.get(pt_level))

    def _gstage_nonleaf_pagesize(self, pt_level: int) -> RV.RiscvPageSizes:
        """Pagesize of the g-stage translation fronting the frame this VS node uses."""
        return gstage_nonleaf_geometry(self.page, self.page_map.paging_g_mode, pt_level)[2]

    def calc_index_from_va(self, level: int) -> int:
        """
        Calculate the index for a given level of pagetable
        """
        index_bits_result = RV.RiscvPagingModes.index_bits(mode=self.page_map.paging_mode, level=level)
        if index_bits_result is None:
            raise ValueError(f"index_bits returned None for paging_mode={self.page_map.paging_mode}, level={level}")
        index_hi, index_lo = index_bits_result

        lin_addr = self.page.lin_addr
        if lin_addr is None:
            raise ValueError("Linear address is None for page")
        index = common.bits(value=lin_addr, bit_hi=index_hi, bit_lo=index_lo)

        return index

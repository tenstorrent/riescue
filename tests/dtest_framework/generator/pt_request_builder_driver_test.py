# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Drive the riemap builder from a real RiescueD parse and check the allocation.

This *runs* the build through :func:`build_page_tables` -- the composition seam wired
into ``generate()`` -- and checks the resulting allocation is internally consistent:
every translated name resolves to an address, page relations hold (aliases share a
physical page, linked children sit at parent+offset), physical spans do not overlap,
and PMA region members land inside their placed region.

It stops short of populating the pool / emitting assembly; it validates the
builder-driven allocation in isolation.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.dtest_framework.generator.generator import Generator
from riescue.dtest_framework.generator.pt_request_builder import build_page_tables, _root_entry_span
from riescue.dtest_framework.pool import Pool
from riescue.dtest_framework.parser import Parser
from riescue.dtest_framework.config import FeatMgr
from riescue.dtest_framework.lib.discrete_test import DiscreteTest
from riescue.riemap.memory import CustomRange, Memory, DramRange

_TESTS = Path(__file__).resolve().parents[3] / "riescue" / "dtest_framework" / "tests"


def _featmgr(paging_mode=RV.RiscvPagingModes.SV39, paging_g_mode=RV.RiscvPagingModes.DISABLE, env=RV.RiscvTestEnv.TEST_ENV_BARE_METAL):
    fm = MagicMock(spec=FeatMgr)
    fm.memory = Memory(dram_ranges=(DramRange(start=0x80000000, size=0x100000000),))
    fm.cpu_config = None
    fm.addrgen_limit_indices = False
    fm.addrgen_limit_way_predictor_multihit = False
    fm.feature = MagicMock()
    fm.feature.is_enabled = MagicMock(return_value=True)
    fm.num_cpus = 1
    fm.hart_ids = None
    fm.get_hart_ids.return_value = [0]
    fm.discontiguous_hartids.return_value = False
    fm.paging_mode = paging_mode
    fm.paging_g_mode = paging_g_mode
    fm.env = env
    fm.enable_pma_randomization = False
    fm.pma_random_regions = 8
    fm.pma_random_mask_pct = 25
    fm.pma_carveout_mask_pct = 0
    fm.user_programmable_pmacfg = 0
    fm.configure_mock(bringup_pagetables=False)
    fm.private_maps = False
    fm.reserve_partial_phys_memory = False
    fm.physical_addr_bits = 56
    fm.priv_mode = RV.RiscvPrivileges.SUPER
    fm.pbmt_ncio = False
    fm.all_4kb_pages = False
    fm.svadu = False
    fm.secure_mode = RV.RiscvSecureModes.NON_SECURE
    fm.secure_pt_probability = 0
    fm.secure_access_probability = 0
    return fm


def _featmgr_custom(**kw):
    """A featmgr carrying the test_custom_mem.s custom regions (mmap.custom)."""
    fm = _featmgr(**kw)
    fm.memory = Memory(
        dram_ranges=(DramRange(start=0x80000000, size=0x100000000),),
        custom_ranges=(
            CustomRange(name="probe_buf", start=0x60000000, size=0x1000000),
            CustomRange(name="probe_buf_io", start=0x9C00000, size=0x10000),
            CustomRange(name="probe_buf_rw", start=0x61000000, size=0x100000),
            CustomRange(name="probe_buf_ro", start=0x62000000, size=0x100000),
        ),
    )
    return fm


def _prepare(test_path: Path, fm):
    """Parse and resolve pagesizes up front; return the generator and its pool."""
    pool = Pool()
    pool.discrete_tests["dummy"] = DiscreteTest(name="dummy", priv=RV.RiscvPrivileges.MACHINE)
    Parser(test_path, pool).parse()
    gen = Generator(RandNum(seed=1), pool, fm)
    gen.process_raw_parsed_page_mappings()
    gen.add_page_maps()
    # Pagesize policy stays in RiescueD and is resolved up front (the switch runs
    # this pass before translating; the translator reads the resolved fields).
    for ppm in pool.get_parsed_page_mappings().values():
        gen.randomize_pagesize(ppm)
    return gen, pool


def _build(test_path: Path, fm):
    """Parse, resolve pagesizes up front, then drive the riemap builder."""
    gen, pool = _prepare(test_path, fm)
    result, translation = build_page_tables(pool, fm, RandNum(seed=1))
    return result, translation


def _build_and_readback(test_path: Path, fm):
    """As :func:`_build`, plus the pool read-back ``generate()`` performs next."""
    gen, pool = _prepare(test_path, fm)
    result, translation = build_page_tables(pool, fm, RandNum(seed=1))
    gen._readback_allocation(result, translation)
    return pool, result, translation


def _spans_overlap(spans):
    ordered = sorted(spans)
    for (_, e1), (s2, _) in zip(ordered, ordered[1:]):
        if s2 < e1:
            return True
    return False


class TestBuildDriver(unittest.TestCase):
    def _check_consistent(self, test_path: Path, fm):
        result, translation = _build(test_path, fm)

        # Every mapped page resolves to a concrete VA and PA.
        # A linked child (OffsetFrom the parent, on the source page's own addr) and a
        # bare-VS/g-stage identity page deliberately sit inside their anchor's reserved
        # span; skip those (the source page's own relation shows it). A cross-map/alias
        # share instead ties the PA on an unnamed *destination* page, invisible from the
        # source alone -- rather than trying to detect that structurally, dedupe by the
        # resolved PA value itself: two spans starting at the exact same address are
        # always legitimate sharing, never the overlap bug this guards against.
        #
        # ``page`` here is the SOURCE/VA-side declaration -- its own ``reserve_size`` is
        # the *linear*-side reservation (e.g. a modify_pt page widens its VA span to
        # isolate its own PT nodes, independent of its physical footprint), not the PA
        # span. The destination page (holding the real physical reservation) isn't named
        # by ``Translation``, so use the leaf's actual pagesize -- the true minimum
        # physical footprint every real leaf occupies -- to bound the physical check.
        reserved_by_pa: dict = {}
        for page in translation.page_names:
            va, pa = result.address_of(page)
            self.assertIsNotNone(va, f"{translation.page_names[page]} VA unresolved")
            self.assertIsNotNone(pa, f"{translation.page_names[page]} PA unresolved")
            if page.addr.relation is not None:
                continue
            reserved = RV.RiscvPageSizes.memory(page.pagesize)
            reserved_by_pa[pa] = max(reserved_by_pa.get(pa, 0), reserved)
        pa_spans = [(pa, pa + reserved) for pa, reserved in reserved_by_pa.items()]
        self.assertFalse(_spans_overlap(pa_spans), f"physical spans overlap in {test_path.name}")

        # Every bare address resolves.
        for page, name in translation.addr_names.items():
            self.assertIsNotNone(result.address(page), f"{name} unresolved")

    def _check_region_members_inside(self, test_path: Path, fm):
        # Shared by both in_pma (region_pma-tracked) and custom_region (untracked --
        # region_pma is PMA-only) callers, so it only asserts what both guarantee:
        # every region-constrained page actually lands inside its declared region.
        result, translation = _build(test_path, fm)
        self.assertTrue(translation.page_regions, "expected region-constrained pages")
        for page, region in translation.page_regions.items():
            base = result.region_base(region)
            addr = result.address(page)
            self.assertTrue(base <= addr < base + region.size, f"page outside its region [0x{base:x}, 0x{base + region.size:x})")

    def test_explicit_lin_phys_pairs(self):
        self._check_consistent(_TESTS / "test_macros.s", _featmgr())

    def test_random_fixed_linked(self):
        self._check_consistent(_TESTS / "bfs.s", _featmgr())

    def test_user_page_maps_and_fanout(self):
        self._check_consistent(_TESTS / "skip_instr.s", _featmgr())

    def test_two_stage_identity_gstage(self):
        self._check_consistent(_TESTS / "test_vs.s", _featmgr(paging_g_mode=RV.RiscvPagingModes.SV39, env=RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED))

    def test_bare_os_map_with_enabled_private_maps_two_stage(self):
        # map_os bare-VS (guest walks hgatp directly) while user page_maps run enabled
        # VS modes: a page shared into both kinds targets the bare owner's GPA-domain
        # page directly -- the bare owner has no separate __gpa page to reference.
        fm = _featmgr(paging_mode=RV.RiscvPagingModes.DISABLE, paging_g_mode=RV.RiscvPagingModes.SV39, env=RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED)
        self._check_consistent(_TESTS / "test.s", fm)

    def test_in_pma_regions(self):
        self._check_consistent(_TESTS / "test_pma_hint.s", _featmgr())
        self._check_region_members_inside(_TESTS / "test_pma_hint.s", _featmgr())
        _result, translation = _build(_TESTS / "test_pma_hint.s", _featmgr())
        self.assertTrue(translation.region_pma, "expected PMA regions tracked in region_pma")

    def test_in_pma_readback_registers_new_regions_exactly_once(self):
        # test_pma_hint.s mixes brand-new in_pma regions (no matching hint --
        # register_on_readback=True) with hint-region reuse and same-batch sharing
        # (register_on_readback=False, already tracked in pool.pma_regions before
        # read-back ever runs). Read-back must add every new region exactly once and
        # must never re-insert a reused/shared one -- no duplicates either way.
        fm = _featmgr()
        gen, pool = _prepare(_TESTS / "test_pma_hint.s", fm)
        pre_readback_ids = [id(r) for r in pool.pma_regions.consolidated_entries(merge_named=False)]
        self.assertEqual(len(pre_readback_ids), len(set(pre_readback_ids)), "pre-allocation must not itself duplicate a region")
        new_ids = {id(b.info) for b in gen._pma_region_bindings.values() if b.register_on_readback}
        reused_ids = {id(b.info) for b in gen._pma_region_bindings.values() if not b.register_on_readback}
        self.assertTrue(new_ids, "expected at least one brand-new in_pma region")
        self.assertTrue(reused_ids, "expected at least one reused/shared in_pma region")
        self.assertFalse(new_ids & set(pre_readback_ids), "a new region must not already be in the pool before read-back")
        self.assertTrue(reused_ids.issubset(pre_readback_ids), "a reused/shared region must already be tracked before read-back")

        result, translation = build_page_tables(pool, fm, RandNum(seed=1))
        gen._readback_allocation(result, translation)

        post_ids = [id(r) for r in pool.pma_regions.consolidated_entries(merge_named=False)]
        self.assertEqual(len(post_ids), len(set(post_ids)), "read-back must not duplicate any region")
        post_id_set = set(post_ids)
        self.assertTrue(new_ids.issubset(post_id_set), "every new region must be registered after read-back")
        for region_id in reused_ids:
            self.assertEqual(post_ids.count(region_id), 1, "a reused/shared region must not be re-inserted on read-back")

    # -- modify_pt / modify_nonleaf_pt declarative pins ---------------------------
    #
    # These tests preserve slot-precise read-back, frame exclusivity, and requested PTE
    # permission bits. Allocation addresses may vary.
    # Scenarios live as inline asm here rather than mutating shared algorithm .s fixtures.

    # Leaf permission bits (low byte of a PTE): V R W X U G A D.
    _V, _R, _W, _X, _U, _G, _A, _D = (1 << i for i in range(8))

    def _asm_path(self, asm: str) -> Path:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".s", delete=False) as fh:
            fh.write(asm)
            path = Path(fh.name)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return path

    def _single_stage_modify_pt(self):
        """Free-VA modify_pt page (``lin6``) plus an unrelated plain sibling."""
        asm = """
;#test.name       single_stage_modify_pt
;#test.author     ysohail@tenstorrent.com
;#test.arch       rv64
;#test.priv       supervisor
;#test.env        bare_metal
;#test.cpus       1
;#test.paging     sv39
;#test.category   arch
;#test.class      paging
;#test.tags       paging

;#random_addr(name=lin6, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin6, phys_name=&random, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'], modify_pt=1)

;#random_addr(name=plain, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=plain, phys_name=&random, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'])

.section .code, "ax"
    nop
"""
        return _build(self._asm_path(asm), _featmgr())

    def _leaf_perm_bits(self, result, translation, lin_name: str) -> int:
        """Low-byte permission bits of ``lin_name``'s leaf PTE (its own source space)."""
        for space in result.spaces():
            pte_by_addr = dict(space.pte_entries())
            for page, names in translation.page_names.items():
                if names[0] != lin_name or page.space is not space.space:
                    continue
                va, _pa = result.address_of(page)
                steps, _ = space.walk(va)
                leaf = next((s for s in steps if s.leaf), None)
                if leaf is not None and leaf.pte_addr in pte_by_addr:
                    return pte_by_addr[leaf.pte_addr] & 0xFF
        raise AssertionError(f"no leaf PTE found for {lin_name}")

    def test_modify_pt_readback_and_leaf_bits(self):
        result, translation = self._single_stage_modify_pt()
        levels_by_name: dict = {}
        for src_page, windows in translation.pt_windows.items():
            name = translation.page_names.get(src_page, ("?",))[0]
            for level, _window_page, _frame_page in windows:
                levels_by_name.setdefault(name, set()).add(level)
        # F1: the free-VA modify_pt page has a per-level window at every SV39 walk level.
        self.assertEqual(levels_by_name.get("lin6"), {0, 1, 2}, "modify_pt F1 read-back must fire at every level")
        # Leaf PTE bits: v/r/w/a/d set (lin6 declares v=r=w=a=d=1, no x).
        bits = self._leaf_perm_bits(result, translation, "lin6")
        for name, bit in (("V", self._V), ("R", self._R), ("W", self._W), ("A", self._A), ("D", self._D)):
            self.assertTrue(bits & bit, f"lin6 leaf {name} bit must be set (0x{bits:x})")

    def test_single_stage_modify_pt_full_walk_is_exclusive(self):
        result, translation = self._single_stage_modify_pt()
        space = self._space_of(result, gstage=False)
        owner = self._page_named(translation, "lin6", space)
        owner_va, _ = result.address_of(owner)
        unrelated = []
        for page, names in translation.page_names.items():
            if page.space is not space.space or page is owner:
                continue
            va, _ = result.address_of(page)
            unrelated.append((names[0], va))
        self._assert_walk_exclusive(space, owner_va, unrelated, "lin6")

    def test_two_stage_modify_pt_vs_walk_is_exclusive(self):
        result, translation = self._two_stage_modify_nonleaf()
        vs = self._space_of(result, gstage=False)
        owner = self._page_named(translation, "lin_b", vs)
        owner_va, _ = result.address_of(owner)
        unrelated = []
        for page, names in translation.page_names.items():
            if page.space is not vs.space or page is owner:
                continue
            va, _ = result.address_of(page)
            unrelated.append((names[0], va))
        self._assert_walk_exclusive(vs, owner_va, unrelated, "lin_b")

    def test_pinned_pa_declared_lin_modify_pt_avoids_index0_collision(self):
        # Nightly svpbmt1: pinned phys_addr + declared free linear random_addr + modify_pt
        # under SV57 must not force VA==PA. An exact OS-style page at L4 index 0 with the
        # default signature would otherwise collide with the modify_pt child_frame signature
        # when the VA is wrongly pinned at 0x200c000.
        asm = """
;#test.name       pinned_pa_declared_lin
;#test.author     ysohail@tenstorrent.com
;#test.arch       rv64
;#test.priv       supervisor
;#test.env        bare_metal
;#test.cpus       1
;#test.paging     sv57
;#test.category   arch
;#test.class      paging
;#test.tags       paging

;#page_mapping(lin_addr=0x1080000, phys_addr=0x1080000, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'])

;#random_addr(name=lin3, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
;#reserve_memory(start_addr=0x200c000, addr_type=physical, size=0x1000)
;#page_mapping(lin_name=lin3, phys_addr=0x200c000, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'], g=1, modify_pt=1)

.section .code, "ax"
    nop
"""
        path = self._asm_path(asm)
        fm = _featmgr(paging_mode=RV.RiscvPagingModes.SV57)
        result, translation = _build(path, fm)
        lin3 = next(page for page, names in translation.page_names.items() if names[0] == "lin3")
        va, pa = result.address_of(lin3)
        self.assertEqual(pa, 0x200C000)
        self.assertNotEqual(va, 0x200C000, "declared linear random_addr must remain movable")
        # SV57 L4 index uses bits [55:48]; index 0 would collide with the exact OS page.
        self.assertNotEqual((va >> 48) & 0xFF, 0, "modify_pt page must color away from L4 index 0")

    def test_modify_nonleaf_two_stage_builds_with_stable_leaf_bits(self):
        # Two-stage modify_nonleaf_pt + modify_pt + a plain page: builds under color=True and
        # the modify_nonleaf leaf keeps its declared permissions (v/r/w/x/a/d).
        # Use the same featmgr as the other two-stage modify_nonleaf cases: 2MB g-stage
        # frames need more than the default 4 GiB DRAM once root-entry granules retire.
        result, translation = self._two_stage_modify_nonleaf()
        bits = self._leaf_perm_bits(result, translation, "lin_a")
        for name, bit in (("V", self._V), ("R", self._R), ("W", self._W), ("X", self._X), ("A", self._A), ("D", self._D)):
            self.assertTrue(bits & bit, f"lin_a leaf {name} bit must be set (0x{bits:x})")

    # -- modify_nonleaf_pt g-stage forcing on the pinned PT-node frames ----------
    #
    # A ``{base}_nonleaf_g*`` force names a VS PT-node FRAME by the level of the pointer PTE
    # that targets it, so ``v_nonleaf_gleaf=0`` on a 4 KiB page invalidates the g-stage leaf
    # of the frame holding that page's LEAF VS-stage PTE -- the implicit-PTW fault shape. That
    # frame is pinned (``modify_nonleaf_pt``), so the force cannot ride the VS node's PTGPage
    # (RieMap synthesizes no identity for a GPA a consumer already translates, and the pinned
    # frame occupies the single ``PTNode.page`` slot): it rides the frame's OWN GPA -> HPA
    # mapping. These pin that behaviour, the frame's exclusivity, and its g-stage geometry.

    def _space_of(self, result, gstage: bool):
        return next(s for s in result.spaces() if s.is_gstage is gstage)

    def _page_named(self, translation, lin_name: str, space):
        for page, names in translation.page_names.items():
            if names[0] == lin_name and page.space is space.space:
                return page
        raise AssertionError(f"no page named {lin_name} in {'g-stage' if space.is_gstage else 'VS'} space")

    def _leaf_step(self, space, addr: int):
        steps, _ = space.walk(addr)
        for step in steps:
            if step.leaf:
                return step
        raise AssertionError(f"no leaf PTE reached for 0x{addr:x}")

    def _leaf_pte_frame(self, result, translation, lin_name: str) -> int:
        """The GPA of the 4 KiB VS-stage table frame that holds ``lin_name``'s leaf PTE."""
        vs = self._space_of(result, gstage=False)
        va, _pa = result.address_of(self._page_named(translation, lin_name, vs))
        return self._leaf_step(vs, va).pte_addr & ~0xFFF

    @staticmethod
    def _walk_pte_addrs(space, address: int, *, max_level: "int | None" = None):
        """PTE addresses on ``address``'s walk, optionally capped at ``max_level``.

        ``modify_leaf_pt`` / ``modify_nonleaf_pt`` own one G-stage level above the leaf
        (a next-level granule), so only levels ``<= leaf.level + 1`` must be exclusive;
        ancestors above that (e.g. the Sv39 root for a 4 KiB leaf) may still be shared.
        ``modify_pt`` owns a full root-entry span, so callers leave ``max_level`` unset.
        """
        steps, _ = space.walk(address)
        return {step.pte_addr for step in steps if max_level is None or step.level <= max_level}

    def _assert_walk_exclusive(
        self,
        space,
        owner_address: int,
        unrelated_addresses,
        owner_name: str,
        *,
        max_level: "int | None" = None,
    ):
        owner_ptes = self._walk_pte_addrs(space, owner_address, max_level=max_level)
        self.assertTrue(owner_ptes, f"{owner_name} has no walk")
        for other_name, other_address in unrelated_addresses:
            if other_address == owner_address:
                continue
            shared = owner_ptes & self._walk_pte_addrs(space, other_address, max_level=max_level)
            self.assertFalse(
                shared,
                f"{owner_name} shares PTE addresses with {other_name}: " f"{sorted(hex(address) for address in shared)}",
            )

    def _gstage_leaf(self, result, gpa: int):
        """``(pte_value, level)`` of the g-stage leaf PTE translating ``gpa``."""
        g = self._space_of(result, gstage=True)
        step = self._leaf_step(g, gpa)
        return dict(g.pte_entries())[step.pte_addr], step.level

    def _two_stage_modify_nonleaf(self):
        fm = _featmgr(paging_g_mode=RV.RiscvPagingModes.SV39, env=RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED)
        # The MagicMock featmgr would otherwise report a truthy --all_4kb_pages and flatten
        # every declared g-stage non-leaf superpage back to 4KB. A superpage g-stage non-leaf
        # frame also needs more than the default 4 GiB of DRAM to align in.
        fm.all_4kb_pages = False
        fm.memory = Memory(dram_ranges=(DramRange(start=0x80000000, size=0x8000000000),))
        asm = """
;#test.name       modify_nonleaf
;#test.author     ysohail@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine super
;#test.env        virtualized
;#test.cpus       1
;#test.paging     sv39
;#test.category   arch
;#test.class      paging
;#test.tags       paging

;#random_addr(name=lin_a, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin_a, phys_name=&random, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'], modify_nonleaf_pt=1)

;#random_addr(name=lin_b, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin_b, phys_name=&random, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'], modify_pt=1)

;#random_addr(name=lin_c, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin_c, phys_name=&random, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'])

;#random_addr(name=lin_d, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin_d, phys_name=&random, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'], gstage_vs_nonleaf_pagesize=['4kb'], modify_nonleaf_pt=1, v_leaf_gleaf=1, r_leaf_gleaf=1, w_leaf_gleaf=1, a_leaf_gleaf=1, d_leaf_gleaf=1, v_nonleaf_gleaf=0)  # noqa: E501

;#random_addr(name=lin_e, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin_e, phys_name=&random, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'], modify_nonleaf_pt=1, gstage_vs_nonleaf_pagesize=['2mb'])

.section .code, "ax"
    nop
"""
        return _build(self._asm_path(asm), fm)

    def test_modify_nonleaf_gleaf_force_invalidates_the_leaf_pte_frame(self):
        # v_nonleaf_gleaf=0 must reach the g-stage leaf of lin_d's leaf-PTE frame...
        result, translation = self._two_stage_modify_nonleaf()
        pte, _level = self._gstage_leaf(result, self._leaf_pte_frame(result, translation, "lin_d"))
        self.assertFalse(pte & self._V, f"lin_d leaf-PTE frame g-stage leaf must be invalid (0x{pte:x})")
        # ...and only there: the data page's own g-stage leaf keeps v_leaf_gleaf=1, else the
        # access would fault on the data leaf instead of during the implicit VS-stage walk.
        vs = self._space_of(result, gstage=False)
        va, _pa = result.address_of(self._page_named(translation, "lin_d", vs))
        _steps, gpa = vs.walk(va)
        self.assertIsNotNone(gpa, "lin_d's VS-stage walk must translate")
        data_pte, _ = self._gstage_leaf(result, gpa or 0)
        self.assertTrue(data_pte & self._V, f"lin_d data page g-stage leaf must stay valid (0x{data_pte:x})")

    def test_modify_nonleaf_leaf_pte_frame_holds_no_other_leaf(self):
        # The forced-invalid frame must hold no other page's leaf PTE -- else invalidating it
        # faults unrelated pages. (The declarative pin that guarantees this is asserted in
        # pt_request_builder_test.TestModifyPtNodeFrames; this checks the built tree.)
        result, translation = self._two_stage_modify_nonleaf()
        frame = self._leaf_pte_frame(result, translation, "lin_d")
        vs = self._space_of(result, gstage=False)
        for page, names in translation.page_names.items():
            if names[0] == "lin_d" or page.space is not vs.space:
                continue
            va, _pa = result.address_of(page)
            steps, _ = vs.walk(va)
            leaf = next((s for s in steps if s.leaf), None)
            if leaf is None:
                continue
            self.assertNotEqual(leaf.pte_addr & ~0xFFF, frame, f"{names[0]} shares lin_d's exclusive leaf-PTE frame")

    def test_modify_nonleaf_frame_owned_gstage_levels_are_exclusive(self):
        # modify_nonleaf_pt owns one G-stage level above the frame's leaf geometry -- not
        # the whole walk. A 4 KiB frame shares the Sv39 root with siblings in the same 1 GiB.
        result, translation = self._two_stage_modify_nonleaf()
        vs = self._space_of(result, gstage=False)
        gstage = self._space_of(result, gstage=True)
        frame_gpa = self._leaf_pte_frame(result, translation, "lin_d")
        unrelated = []
        for page, names in translation.page_names.items():
            if page.space is not vs.space:
                continue
            va, _ = result.address_of(page)
            _steps, gpa = vs.walk(va)
            if gpa is not None:
                unrelated.append((f"{names[0]} data", gpa))
        for identity in gstage.gstage_identities():
            unrelated.append((f"frame {identity.gpa:#x}", identity.gpa))
        owned = self._leaf_step(gstage, frame_gpa).level + 1
        self._assert_walk_exclusive(
            gstage,
            frame_gpa,
            unrelated,
            "lin_d leaf-PTE frame",
            max_level=owned,
        )

    def test_modify_nonleaf_frame_keeps_gstage_nonleaf_pagesize(self):
        # A pinned frame keeps the page's g-stage non-leaf geometry (what the PTGPage's
        # ``size`` carried): lin_e asks for 2MB g-stage non-leaf leaves, so the leaf PTE
        # translating its leaf-PTE frame sits at g-stage level 1, not level 0. A
        # misaligned-superpage g-stage test misaligns that very PTE's PPN.
        result, translation = self._two_stage_modify_nonleaf()
        _pte, level = self._gstage_leaf(result, self._leaf_pte_frame(result, translation, "lin_e"))
        self.assertEqual(level, 1, "lin_e's PT-node frame must be fronted by a 2MB g-stage leaf")
        # A page that declared 4KB keeps a 4KB (level-0) g-stage leaf.
        _pte, level = self._gstage_leaf(result, self._leaf_pte_frame(result, translation, "lin_d"))
        self.assertEqual(level, 0, "lin_d's PT-node frame must keep its declared 4KB g-stage leaf")

    def test_modify_leaf_owned_gstage_levels_are_exclusive(self):
        # modify_leaf_pt owns one G-stage level above the data leaf (2 MiB for a 4 KiB
        # leaf under Sv39). The root PTE may still be shared with siblings in the same
        # 1 GiB -- that is intentional, not a granule miss.
        asm = """
;#test.name       modify_leaf_owned_levels
;#test.author     ysohail@tenstorrent.com
;#test.arch       rv64
;#test.priv       supervisor
;#test.env        virtualized
;#test.cpus       1
;#test.paging     sv39
;#test.category   arch
;#test.class      paging
;#test.tags       paging

;#page_mapping(lin_name=target, phys_name=&random, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'], modify_leaf_pt=1)
;#page_mapping(lin_name=plain, phys_name=&random, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'])

.section .code, "ax"
    nop
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".s", delete=False) as fh:
            fh.write(asm)
            path = Path(fh.name)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        fm = _featmgr(
            paging_g_mode=RV.RiscvPagingModes.SV39,
            env=RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED,
        )
        result, translation = _build(path, fm)
        vs = self._space_of(result, gstage=False)
        gstage = self._space_of(result, gstage=True)
        owner = self._page_named(translation, "target", vs)
        owner_va, _ = result.address_of(owner)
        _steps, owner_gpa = vs.walk(owner_va)
        self.assertIsNotNone(owner_gpa)
        unrelated = []
        for page, names in translation.page_names.items():
            if page.space is not vs.space or page is owner:
                continue
            va, _ = result.address_of(page)
            _steps, gpa = vs.walk(va)
            if gpa is not None:
                unrelated.append((names[0], gpa))
        for identity in gstage.gstage_identities():
            unrelated.append((f"frame {identity.gpa:#x}", identity.gpa))
        owned = self._leaf_step(gstage, owner_gpa or 0).level + 1
        self._assert_walk_exclusive(
            gstage,
            owner_gpa or 0,
            unrelated,
            "target GPA",
            max_level=owned,
        )

    # -- the __vsleaf{N} / __vslevel{N} g-stage equate families -------------------
    #
    # A hypervisor test names the guest physical addresses its own VS-stage walk touches:
    # its data page's GPA (``__vsleaf{leaf}``) and every VS-stage table frame's GPA
    # (``__vslevel{L}``). test_excp.s:342 compares ``htval`` against
    # ``lin8__vsleaf0__gpa``, so a missing family is an assembler failure, not a wrong
    # PTE -- and it is invisible to every other test here because ``paging_g_mode``
    # defaults to a singleton DISABLE.

    def _two_stage_pool(self):
        fm = _featmgr(paging_g_mode=RV.RiscvPagingModes.SV39, env=RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED)
        return _build_and_readback(_TESTS / "test_vs.s", fm)

    def test_gstage_equate_families_are_registered(self):
        pool, _result, _translation = self._two_stage_pool()
        names = pool.random_addrs
        leaves = [n for n in names if "__vsleaf" in n]
        levels = [n for n in names if "__vslevel" in n]
        self.assertTrue(leaves, "no __vsleaf equates registered for a two-stage build")
        self.assertTrue(levels, "no __vslevel equates registered for a two-stage build")
        # The exact name test_excp.s:342 assembles against.
        self.assertIn("lin1_io__vsleaf0__gpa", names)

    def test_gstage_equates_publish_a_gpa_phys_pair(self):
        pool, _result, _translation = self._two_stage_pool()
        names = pool.random_addrs
        pairs = [n for n in names if ("__vsleaf" in n or "__vslevel" in n) and n.endswith("__gpa")]
        self.assertTrue(pairs)
        for gpa_name in pairs:
            phys_name = gpa_name[: -len("__gpa")] + "__phys"
            self.assertIn(phys_name, names, f"{gpa_name} has no __phys sibling")
            # Identity-mapped, so the two carry the same value -- but different types: a GPA
            # is linear (and zero-extends, so it is never canonicalized), the HPA physical.
            self.assertEqual(names[gpa_name].address, names[phys_name].address)
            self.assertEqual(names[gpa_name].type, RV.AddressType.LINEAR)
            self.assertEqual(names[phys_name].type, RV.AddressType.PHYSICAL)

    def test_gstage_level_equates_name_the_pointed_to_frame(self):
        # Each __vslevel{L} value is the frame the level-L pointer PTE targets, masked to the
        # alignment of the g-stage identity page fronting it (what the pre-refactor generator
        # published, and the GPA the test's g-stage forcing acts on). The frame itself is
        # recomputed here from the walk with a literal 4 KiB mask -- the production code
        # derives that width from index_bits -- and cross-checked against the tree's real
        # table bases.
        pool, result, translation = self._two_stage_pool()
        vs = self._space_of(result, gstage=False)
        table_bases = {view.addr for view in vs.tables()}
        checked = 0
        for page, names in translation.page_names.items():
            if page.space is not vs.space:
                continue
            base = names[0].replace("+", "_")
            nonleaf_ps = result.page_meta(page).gstage_vs_nonleaf_pagesize or RV.RiscvPageSizes.S4KB
            va, _pa = result.address_of(page)
            steps, _ = vs.walk(va)
            for step, child in zip(steps, steps[1:]):
                name = f"{base}__vslevel{step.level}__gpa"
                if name not in pool.random_addrs:
                    continue  # another map registered this name first (--private_maps)
                frame = child.pte_addr & ~0xFFF
                self.assertIn(frame, table_bases, f"{name} does not name a real table frame")
                self.assertEqual(pool.random_addrs[name].address, frame & RV.RiscvPageSizes.address_mask(nonleaf_ps), name)
                checked += 1
        self.assertGreater(checked, 0, "no __vslevel equate was cross-checked")

    def test_gstage_leaf_equate_is_the_pages_gpa(self):
        pool, result, translation = self._two_stage_pool()
        vs = self._space_of(result, gstage=False)
        checked = 0
        for page, names in translation.page_names.items():
            if page.space is not vs.space:
                continue
            leaf_level = RV.RiscvPageSizes.pt_leaf_level(result.page_meta(page).pagesize)
            name = f"{names[0].replace('+', '_')}__vsleaf{leaf_level}__gpa"
            if name not in pool.random_addrs:
                continue
            _va, gpa = result.address_of(page)
            leaf_ps = result.page_meta(page).gstage_vs_leaf_pagesize or RV.RiscvPageSizes.S4KB
            self.assertEqual(pool.random_addrs[name].address, gpa & RV.RiscvPageSizes.address_mask(leaf_ps), name)
            checked += 1
        self.assertGreater(checked, 0, "no __vsleaf equate was cross-checked")

    def test_no_gstage_equates_without_a_gstage(self):
        pool, _result, _translation = _build_and_readback(_TESTS / "test_vs.s", _featmgr())
        self.assertFalse([n for n in pool.random_addrs if "__vsleaf" in n or "__vslevel" in n])

    # -- --pbmt_ncio on the g-stage leaf of a two-stage page --------------------
    #
    # Master applied the NC/IO roll in _create_pt_leaf, after PTAttrs.__init__, so it beat
    # every pbmt_level* for every leaf in every map. RiescueD now has to declare those bits,
    # and the only proof is the built g-stage tree. The identities fronting the VS-stage
    # PT-node frames are deliberately NOT covered -- see _roll_gstage_pbmt for why.

    def test_pbmt_ncio_reaches_the_gstage_data_leaf(self):
        fm = _featmgr(paging_g_mode=RV.RiscvPagingModes.SV39, env=RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED)
        fm.pbmt_ncio = True
        result, translation = _build(_TESTS / "test_vs.s", fm)
        vs = self._space_of(result, gstage=False)
        g = self._space_of(result, gstage=True)
        g_ptes = dict(g.pte_entries())
        checked = 0
        for page, names in translation.page_names.items():
            if page.space is not vs.space:
                continue
            _va, gpa = result.address_of(page)
            pbmt = (g_ptes[self._leaf_step(g, gpa).pte_addr] >> 61) & 3
            self.assertIn(pbmt, (1, 2), f"{names[0]}'s GPA translates through an ordinary-memory g-stage leaf")
            checked += 1
        self.assertGreater(checked, 0)

    def test_pbmt_ncio_reaches_frame_identities_with_variety(self):
        fm = _featmgr(paging_g_mode=RV.RiscvPagingModes.SV39, env=RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED)
        fm.pbmt_ncio = True
        result, _translation = _build(_TESTS / "test_vs.s", fm)
        g = self._space_of(result, gstage=True)
        identities = list(g.gstage_identities())
        self.assertGreater(len(identities), 0, "no synthesized VS-frame identities were emitted")
        values = {(dict(g.pte_entries())[self._leaf_step(g, identity.gpa).pte_addr] >> 61) & 3 for identity in identities}
        self.assertTrue(
            values & {1, 2},
            f"frame identities lost NC/IO typing: {values}",
        )

        leaves = [entry for space in result.spaces() for table in space.tables() for entry in table.entries if entry.leaf]
        typed = [entry for entry in leaves if ((entry.value >> 61) & 3) in (1, 2)]
        self.assertEqual(
            {(entry.value >> 61) & 3 for entry in leaves} & {1, 2},
            {1, 2},
            "emitted leaves lost NC/IO variety",
        )
        self.assertGreaterEqual(len(typed), len(leaves) // 4, "too few emitted leaves carry NC/IO PBMT")

    def test_pbmt_ncio_reaches_the_vs_stage_leaf(self):
        fm = _featmgr(paging_g_mode=RV.RiscvPagingModes.SV39, env=RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED)
        fm.pbmt_ncio = True
        result, translation = _build(_TESTS / "test_vs.s", fm)
        vs = self._space_of(result, gstage=False)
        vs_ptes = dict(vs.pte_entries())
        checked = 0
        for page, names in translation.page_names.items():
            if page.space is not vs.space or names[0].startswith("sec::"):
                continue
            va, _gpa = result.address_of(page)
            pbmt = (vs_ptes[self._leaf_step(vs, va).pte_addr] >> 61) & 3
            self.assertIn(pbmt, (1, 2), f"{names[0]}'s VS-stage leaf is ordinary memory")
            checked += 1
        self.assertGreater(checked, 0)

    def test_pbmt_stays_zero_on_every_gstage_non_leaf(self):
        # PBMT is a leaf-only field; a pointer PTE must never carry one.
        fm = _featmgr(paging_g_mode=RV.RiscvPagingModes.SV39, env=RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED)
        fm.pbmt_ncio = True
        result, _translation = _build(_TESTS / "test_vs.s", fm)
        for space in result.spaces():
            for view in space.tables():
                for entry in view.entries:
                    if not entry.leaf:
                        self.assertEqual((entry.value >> 61) & 3, 0, f"non-leaf PTE at {view.addr:#x}[{entry.index}] carries a PBMT")

    def test_no_pbmt_anywhere_with_the_knob_off(self):
        result, _translation = _build(_TESTS / "test_vs.s", _featmgr(paging_g_mode=RV.RiscvPagingModes.SV39, env=RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED))
        for space in result.spaces():
            for view in space.tables():
                for entry in view.entries:
                    self.assertEqual((entry.value >> 61) & 3, 0)

    def test_custom_region_pages_land_in_region(self):
        # A page whose phys_name references a custom_region random_addr must place its PA
        # inside the named region (the CASE 4a bug: it used to draw freely). Also asserts
        # the two-pages-per-region cases (test02/test04) do not overlap.
        self._check_consistent(_TESTS / "test_custom_mem.s", _featmgr_custom())
        self._check_region_members_inside(_TESTS / "test_custom_mem.s", _featmgr_custom())
        # Custom regions are always fixed (base pinned to the named range); a floating
        # PMA region has no base until placed. Sanity-check we actually exercised the
        # custom-region path rather than silently placing nothing.
        _result, translation = _build(_TESTS / "test_custom_mem.s", _featmgr_custom())
        custom_pages = sum(1 for region in translation.page_regions.values() if region.base is not None)
        self.assertGreaterEqual(custom_pages, 8, "expected every custom-region page mapping to be placed in_region")
        # A custom_region MemoryRegion carries no PmaInfo -- its PMA setup is the
        # consumer's own responsibility -- so it is absent from region_pma entirely.
        self.assertFalse(translation.region_pma, "custom_region must not appear in region_pma")

    def test_disable_bare_linear_addr_builds(self):
        # Nightly floatingpoint2: paging DISABLE collapses mapped pages into phys (no
        # Mapping whose src is map_os), but a bare linear random_addr still lives in
        # map_os. The builder must create a pool for that non-source space.
        asm = """
;#test.name       disable_bare_linear
;#test.author     ysohail@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine
;#test.env        bare_metal
;#test.cpus       1
;#test.paging     disable
;#test.category   arch
;#test.class      paging
;#test.tags       paging

;#random_addr(name=lin1, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys1, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin1, phys_name=phys1, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'])

;#random_addr(name=reset_regs_location, type=linear, size=0x1000, and_mask=0xfffffffffffff000)

.section .code, "ax"
    nop
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".s", delete=False) as fh:
            fh.write(asm)
            path = Path(fh.name)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        fm = _featmgr(paging_mode=RV.RiscvPagingModes.DISABLE)
        pool, result, translation = _build_and_readback(path, fm)
        bare = next(page for page, name in translation.addr_names.items() if name == "reset_regs_location")
        self.assertIsNotNone(result.address(bare))
        self.assertIn("reset_regs_location", pool.random_addrs)

    def test_disable_bare_linear_addr_with_io_builds(self):
        # A bare LINEAR random_addr declared io=1 (e.g. an MMIO-adjacent scratch VA) must
        # not carry ADDRESS_MMIO into its AddressConstraint: the LINEAR address space
        # defines no MMIO segment (only PHYSICAL does), so a constraint requesting
        # type=LINEAR + ADDRESS_MMIO has nothing to draw from and build() raises. io is a
        # physical address-map claim only (mirrors master's handle_random_addr, which
        # stamps ADDRESS_MMIO/ADDRESS_SECURE solely in its PHYSICAL branch).
        asm = """
;#test.name       disable_bare_linear_io
;#test.author     ysohail@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine
;#test.env        bare_metal
;#test.cpus       1
;#test.paging     disable
;#test.category   arch
;#test.class      paging
;#test.tags       paging

;#random_addr(name=lin1, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys1, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin1, phys_name=phys1, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'])

;#random_addr(name=reset_regs_location, type=linear, size=0x1000, and_mask=0xfffffffffffff000, io=1)

.section .code, "ax"
    nop
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".s", delete=False) as fh:
            fh.write(asm)
            path = Path(fh.name)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        fm = _featmgr(paging_mode=RV.RiscvPagingModes.DISABLE)
        pool, result, translation = _build_and_readback(path, fm)
        bare = next(page for page, name in translation.addr_names.items() if name == "reset_regs_location")
        self.assertIsNotNone(result.address(bare))
        self.assertIn("reset_regs_location", pool.random_addrs)

    def test_randomize_pagesize_prefers_a_size_the_exact_addr_already_satisfies(self):
        # lin_addr=0x805001000 is 4KB- but not 2MB-aligned; with both offered, the picker
        # must not choose 2MB (align-down in pt_request_builder would then silently move
        # the declared address to 0x805000000).
        asm = """
;#test.name       exact_addr_pagesize_pref
;#test.author     ysohail@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine
;#test.env        bare_metal
;#test.cpus       1
;#test.paging     sv39
;#test.category   arch
;#test.class      paging
;#test.tags       paging

;#page_mapping(lin_addr=0x805001000, phys_addr=0x900000000, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['2mb', '4kb'])

.section .code, "ax"
    nop
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".s", delete=False) as fh:
            fh.write(asm)
            path = Path(fh.name)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        fm = _featmgr(paging_mode=RV.RiscvPagingModes.SV39)
        _gen, pool = _prepare(path, fm)
        (ppm,) = pool.get_parsed_page_mappings().values()
        self.assertEqual(ppm.final_pagesize, RV.RiscvPageSizes.S4KB)

    def test_pgx_style_random_addr_offset_pages_allocate(self):
        # Cluster pgx_hetero / pgx_v2 4KB→2MB crosser: window random_addrs with only
        # ``lin_name=base+offset`` page_mappings (no base page_mapping for the bare
        # name). Parse → process_raw → build must place both leaves inside the window.
        asm = """
;#test.name       pgx_style_offset
;#test.author     ysohail@tenstorrent.com
;#test.arch       rv64
;#test.priv       super
;#test.env        bare_metal
;#test.cpus       1
;#test.paging     sv57
;#test.category   arch
;#test.class      paging
;#test.tags       paging

;#random_addr(name=px4kb2mb, type=linear, size=0x400000, and_mask=0xffffffffffe00000)
;#random_addr(name=px4kb2mb_p, type=physical, size=0x400000, and_mask=0xffffffffffe00000)
;#page_mapping(lin_name=px4kb2mb+0x1ff000, phys_name=px4kb2mb_p+0x1ff000, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'])
;#page_mapping(lin_name=px4kb2mb+0x200000, phys_name=px4kb2mb_p+0x200000, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['2mb'])

.section .code, "ax"
    nop
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".s", delete=False) as fh:
            fh.write(asm)
            path = Path(fh.name)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        fm = _featmgr(paging_mode=RV.RiscvPagingModes.SV57)
        pool, result, translation = _build_and_readback(path, fm)

        ppms = list(pool.get_parsed_page_mappings().values())
        self.assertEqual(len(ppms), 2)
        by_lin = {ppm.lin_name: ppm for ppm in ppms}
        self.assertEqual(by_lin["px4kb2mb+0x1ff000"].lin_addr_link, ("px4kb2mb", 0x1FF000))
        self.assertEqual(by_lin["px4kb2mb+0x1ff000"].phys_addr_link, ("px4kb2mb_p", 0x1FF000))
        self.assertEqual(by_lin["px4kb2mb+0x200000"].lin_addr_link, ("px4kb2mb", 0x200000))

        base_va = pool.random_addrs["px4kb2mb"].address
        base_pa = pool.random_addrs["px4kb2mb_p"].address
        self.assertEqual(base_va & 0x1FFFFF, 0)
        self.assertEqual(base_pa & 0x1FFFFF, 0)

        pages = {names[0]: page for page, names in translation.page_names.items()}
        va4k, pa4k = result.address_of(pages["px4kb2mb+0x1ff000"])
        va2m, pa2m = result.address_of(pages["px4kb2mb+0x200000"])
        self.assertEqual(va4k, base_va + 0x1FF000)
        self.assertEqual(pa4k, base_pa + 0x1FF000)
        self.assertEqual(va2m, base_va + 0x200000)
        self.assertEqual(pa2m, base_pa + 0x200000)
        self.assertNotIn("px4kb2mb+0x1ff000", pool.random_addrs)
        self.assertIn("px4kb2mb", pool.random_addrs)

    def test_secure_bare_root_and_mapping_promotion_emit_secure_ptes(self):
        # Both declaration directions lower to one root-owned qualifier:
        # (1) random_addr secure=1 with a plain mapping, and
        # (2) page_mapping secure=1 promoting an otherwise-plain bare physical root.
        asm = """
;#test.name       secure_root_ownership
;#test.author     ysohail@tenstorrent.com
;#test.arch       rv64
;#test.priv       super
;#test.env        bare_metal
;#test.cpus       1
;#test.paging     sv39
;#test.category   arch
;#test.class      paging
;#test.tags       paging

;#random_addr(name=from_root, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=from_root_pa, type=physical, size=0x1000, and_mask=0xfffffffffffff000, secure=1)
;#page_mapping(lin_name=from_root, phys_name=from_root_pa, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'])

;#random_addr(name=from_mapping, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=from_mapping_pa, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=from_mapping, phys_name=from_mapping_pa, v=1, r=1, w=1, x=1, a=1, d=1, secure=1, pagesize=['4kb'])

.section .code, "ax"
    nop
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".s", delete=False) as fh:
            fh.write(asm)
            path = Path(fh.name)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        fm = _featmgr()
        fm.memory = Memory.from_dict(
            {
                "dram": {
                    "normal": {
                        "address": "0x80000000",
                        "size": "0x3ffff80000000",
                        "cacheable": True,
                        "configurable": True,
                    },
                    "secure": {
                        "address": "0x4000000000000",
                        "size": "0x4000000000000",
                        "cacheable": True,
                        "configurable": True,
                        "secure": True,
                    },
                }
            }
        )
        _pool, result, translation = _build_and_readback(path, fm)
        pages = {names[0]: page for page, names in translation.page_names.items()}
        space_result = result.space(pages["from_root"].space)
        secure_bit = 1 << 55
        for name in ("from_root", "from_mapping"):
            va, _pa = result.address_of(pages[name])
            translated = space_result.walk(va)[1]
            self.assertIsNotNone(translated, f"{name} did not translate")
            assert translated is not None
            self.assertTrue(
                translated & secure_bit,
                f"{name} leaf did not encode the secure address bit",
            )

    def test_htif_section_coexists_with_a_free_modify_pt_page(self):
        # Coverage-only: an exact identity HTIF section (VA==PA pinned at io_htif_addr)
        # must stay pinned there when an unrelated, independently free-drawn modify_pt=1
        # page mapping is also in the test. modify_pt's existing collision-avoidance
        # (root-entry coloring) must keep the two out of the same root PT slot; no
        # ordering/extra_reserved_spans/riemap change is exercised here.
        asm = """
;#test.name       htif_and_modify_pt
;#test.author     ysohail@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine
;#test.env        bare_metal
;#test.cpus       1
;#test.paging     sv39
;#test.category   arch
;#test.class      paging
;#test.tags       paging

;#random_addr(name=mp_lin, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=mp_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=mp_lin, phys_name=mp_phys, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'], modify_pt=1)

.section .code, "ax"
    nop
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".s", delete=False) as fh:
            fh.write(asm)
            path = Path(fh.name)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        fm = _featmgr(paging_mode=RV.RiscvPagingModes.SV39)
        fm.io_htif_addr = 0x70000000
        gen, pool = _prepare(path, fm)
        # Same call the generator's own ``handle_sections("io_htif")`` branch makes.
        gen.add_section_handler(
            name="io_htif",
            size=0x80,
            iscode=False,
            identity_map=True,
            start_addr=fm.io_htif_addr,
        )
        result, translation = build_page_tables(pool, fm, RandNum(seed=1))

        htif_page = next(page for page, names in translation.page_names.items() if names[0] == "io_htif")
        htif_va, htif_pa = result.address_of(htif_page)
        self.assertEqual(htif_va, fm.io_htif_addr)
        self.assertEqual(htif_pa, fm.io_htif_addr)

        mp_page = next(page for page, names in translation.page_names.items() if names[0] == "mp_lin")
        mp_va, _mp_pa = result.address_of(mp_page)

        root_span = _root_entry_span(RV.RiscvPagingModes.SV39)
        self.assertIsNotNone(root_span)
        assert root_span is not None
        self.assertNotEqual(mp_va // root_span, htif_va // root_span, "modify_pt page must color away from the HTIF section's root PT slot")


if __name__ == "__main__":
    unittest.main()

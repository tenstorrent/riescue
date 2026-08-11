# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the dormant parsed-directive -> riemap-constraint translator."""

import unittest
from unittest.mock import MagicMock

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.dtest_framework.config import FeatMgr
from riescue.dtest_framework.pool import Pool
from riescue.dtest_framework.parser import ParsedPageMapping, ParsedRandomAddress, ParsedReserveMemory, PmaInfo
from riescue.dtest_framework.generator.pt_request_builder import (
    PageTableRequestBuilder,
    GSTAGE_MAP_ID,
    DEFAULT_MAP_ID,
    _PHYS,
    _PageReq,
    _Placement,
    _RawPageSpec,
    SectionSpec,
    _emit_vs_two_stage,
    _materialize,
    _root_entry_span,
    _next_level_span,
    _modify_pt_nodes,
    _va_family_root,
    _vs_gstage_pt_nodes,
    RecipeAddrSpec,
    RecipeSameAs,
    RecipeOffsetFrom,
    RecipeDerivedFrom,
)
from riescue.riemap import resolve
from riescue.riemap.builder import _ptgpage_sig
from riescue.riemap.memory import CustomRange, DramRange, Memory
from riescue.riemap.request import Choice, LEAF, AddrSpec, DerivedFrom, MemoryRegion, OffsetFrom, PTGPage, SameAs, Space, Stage


def _rel(spec, cls):
    """Narrow a recipe AddrSpec's relation to a specific Recipe* relation class."""
    r = spec.relation
    assert isinstance(r, cls), f"expected {cls.__name__}, got {type(r).__name__}"
    return r


def _glevel_keys(attrs):
    """The ``{base}_level{vs}_glevel{g}`` subset of ``attrs``.

    A test that hand-writes one of those concrete keys is stating a real g-stage force, which on
    a ``_PageReq`` rides ``gstage_forced_attrs`` -- the test-named subset the PTGPages are built
    from. ``attrs`` keeps the whole set (it also seeds the walker's default identity matrix), so
    the helpers below pass both, mirroring what the translator produces from a page mapping."""
    return {k: v for k, v in (attrs or {}).items() if "_glevel" in k}


def _featmgr(
    paging_mode=RV.RiscvPagingModes.SV39,
    paging_g_mode=RV.RiscvPagingModes.DISABLE,
    env=RV.RiscvTestEnv.TEST_ENV_BARE_METAL,
    secure_mode=RV.RiscvSecureModes.NON_SECURE,
    secure_access_probability=0,
    secure_pt_probability=0,
    memory=None,
):
    fm = MagicMock(spec=FeatMgr)
    fm.env = env
    fm.paging_mode = paging_mode
    fm.paging_g_mode = paging_g_mode
    fm.physical_addr_bits = 56
    fm.priv_mode = RV.RiscvPrivileges.SUPER
    fm.pbmt_ncio = False
    fm.all_4kb_pages = False
    fm.svadu = False
    fm.secure_mode = secure_mode
    fm.secure_pt_probability = secure_pt_probability
    fm.secure_access_probability = secure_access_probability
    fm.reserve_partial_phys_memory = False
    fm.memory = memory if memory is not None else Memory(dram_ranges=(DramRange(start=0x80000000, size=0x100000000),))
    return fm


def _mapping(lin_name, phys_name, **kw):
    ppm = ParsedPageMapping(lin_name=lin_name, phys_name=phys_name, **kw)
    if ppm.final_pagesize is None:
        ppm.final_pagesize = RV.RiscvPageSizes.S4KB
    return ppm


class TestConfigAndSpaces(unittest.TestCase):
    def test_unresolved_pagesize_defaults_to_4kb(self):
        pool = Pool()
        ppm = ParsedPageMapping(lin_name="code", phys_name="code_pa")
        self.assertIsNone(ppm.final_pagesize)
        pool.add_parsed_page_mapping(ppm)

        translated = PageTableRequestBuilder(pool, _featmgr(paging_mode=RV.RiscvPagingModes.DISABLE)).build()

        request = translated.page_reqs_by_map[DEFAULT_MAP_ID][0]
        self.assertEqual(request.pagesize, RV.RiscvPageSizes.S4KB)

    def test_single_stage_config_and_one_space(self):
        pool = Pool()
        pool.add_parsed_page_mapping(_mapping("code", "code_pa"))
        t = PageTableRequestBuilder(pool, _featmgr()).build()
        # One VA space (map_os); the engine owns its physical leaf domain.
        self.assertEqual(list(t.spaces_by_name.keys()), [DEFAULT_MAP_ID])
        os_space = t.spaces_by_name[DEFAULT_MAP_ID]
        self.assertEqual(os_space.paging_mode, RV.RiscvPagingModes.SV39)
        self.assertEqual(os_space.priv_mode, RV.RiscvPrivileges.SUPER)
        self.assertEqual(os_space.stage, Stage.SINGLE)

    def test_private_maps_add_spaces(self):
        pool = Pool()
        ppm = _mapping("shared", "shared_pa", in_private_map=True, page_maps=["map_a", "map_b"])
        pool.add_parsed_page_mapping(ppm)
        t = PageTableRequestBuilder(pool, _featmgr()).build()
        ids = set(t.spaces_by_name.keys())
        self.assertEqual(ids, {DEFAULT_MAP_ID, "map_a", "map_b"})

    def test_two_stage_adds_gstage_space(self):
        pool = Pool()
        pool.add_parsed_page_mapping(_mapping("code", "code_pa"))
        fm = _featmgr(paging_g_mode=RV.RiscvPagingModes.SV39, env=RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED)
        t = PageTableRequestBuilder(pool, fm).build()
        self.assertIn(GSTAGE_MAP_ID, t.spaces_by_name)
        g_space = t.spaces_by_name[GSTAGE_MAP_ID]
        # VS-enabled two-stage: map_hyp is the G-stage domain (identity is declared per
        # mapping when the VA -> GPA mappings are emitted, not on the space).
        self.assertEqual(g_space.stage, Stage.G)
        self.assertEqual(g_space.paging_mode, RV.RiscvPagingModes.SV39)
        vs = t.spaces_by_name[DEFAULT_MAP_ID]
        self.assertEqual(vs.paging_mode, RV.RiscvPagingModes.SV39)
        self.assertEqual(vs.stage, Stage.VS)


class TestPageRequests(unittest.TestCase):
    def test_one_request_per_mapping(self):
        pool = Pool()
        pool.add_parsed_page_mapping(_mapping("a", "a_pa"))
        pool.add_parsed_page_mapping(_mapping("b", "b_pa"))
        b = PageTableRequestBuilder(pool, _featmgr())
        b.build()
        reqs = b.page_reqs_by_map[DEFAULT_MAP_ID]
        self.assertEqual(len(reqs), 2)
        # Every request id is globally unique.
        ids = [r.page_id for r in reqs]
        self.assertEqual(len(ids), len(set(ids)))

    def test_fixed_va_and_pa_become_exact(self):
        pool = Pool()
        # The parser mints the auto name off the literal but keeps the literal string on
        # ``lin_addr`` / ``phys_addr``; the translator pins from that literal, not the name.
        ppm = _mapping("__auto_lin_0x50000000", "__auto_phys_0x80001000", lin_addr_specified=True, phys_addr_specified=True)
        ppm.lin_addr = "0x50000000"
        ppm.phys_addr = "0x80001000"
        pool.add_parsed_page_mapping(ppm)
        b = PageTableRequestBuilder(pool, _featmgr())
        b.build()
        req = b.page_reqs_by_map[DEFAULT_MAP_ID][0]
        self.assertEqual(req.va.exact, 0x50000000)
        self.assertEqual(req.pa.exact, 0x80001000)

    def test_fixed_addr_pins_underscored_hex_and_decimal(self):
        # The literal is carried numerically (int(literal, 0)); an underscored hex pins the
        # exact value, and a decimal literal is decimal -- never re-read as hex off the name.
        pool = Pool()
        hexppm = _mapping("__auto_lin_0x8000_0000", "__auto_phys_0x8000_0000", lin_addr_specified=True, phys_addr_specified=True)
        hexppm.lin_addr = "0x8000_0000"
        hexppm.phys_addr = "0x8000_0000"
        pool.add_parsed_page_mapping(hexppm)
        decppm = _mapping("__auto_lin_4096", "__auto_phys_4096", lin_addr_specified=True, phys_addr_specified=True)
        decppm.lin_addr = "4096"
        decppm.phys_addr = "4096"
        pool.add_parsed_page_mapping(decppm)
        b = PageTableRequestBuilder(pool, _featmgr())
        t = b.build()
        by_lin = {t.names_by_id[r.page_id][0]: r for r in b.page_reqs_by_map[DEFAULT_MAP_ID]}
        self.assertEqual(by_lin["__auto_lin_0x8000_0000"].va.exact, 0x80000000)
        self.assertEqual(by_lin["__auto_lin_0x8000_0000"].pa.exact, 0x80000000)
        # Decimal 4096 is 0x1000, not hexadecimal 0x4096.
        self.assertEqual(by_lin["__auto_lin_4096"].va.exact, 4096)
        self.assertEqual(by_lin["__auto_lin_4096"].pa.exact, 4096)

    def test_exact_va_aligns_down_to_final_pagesize(self):
        # An exact lin_addr that is not itself pagesize-aligned must be rounded down to
        # the mapping's leaf pagesize; the engine rejects an exact placement that is not
        # a multiple of its own leaf size (see riemap allocator's alignment check).
        ppm = _mapping("__auto_lin_0x805001000", "va_align_pa", lin_addr_specified=True)
        ppm.lin_addr = "0x805001000"
        ppm.final_pagesize = RV.RiscvPageSizes.S2MB
        pool = Pool()
        pool.add_parsed_page_mapping(ppm)
        b = PageTableRequestBuilder(pool, _featmgr())
        b.build()
        req = b.page_reqs_by_map[DEFAULT_MAP_ID][0]
        self.assertEqual(req.va.exact, 0x805000000)

    def test_exact_pa_aligns_down_to_final_pagesize(self):
        # Same as the VA case, but for a pinned phys_addr; pin lin_addr separately
        # (already pagesize-aligned) so this isolates the PA align-down path.
        ppm = _mapping("pa_align_lin", "pa_align_pa", lin_addr_specified=True, phys_addr_specified=True)
        ppm.lin_addr = "0x50000000"
        ppm.phys_addr = "0x805001000"
        ppm.final_pagesize = RV.RiscvPageSizes.S2MB
        pool = Pool()
        pool.add_parsed_page_mapping(ppm)
        b = PageTableRequestBuilder(pool, _featmgr())
        b.build()
        req = b.page_reqs_by_map[DEFAULT_MAP_ID][0]
        self.assertEqual(req.pa.exact, 0x805000000)

    def test_identity_default_exact_aligns_down_to_final_pagesize(self):
        # phys_addr_specified + lin unspecified takes the identity-default VA==PA path;
        # the align-down must apply there too.
        ppm = _mapping("mmio_align", "mmio_align", phys_addr_specified=True)
        ppm.phys_addr = "0x805001000"
        ppm.final_pagesize = RV.RiscvPageSizes.S2MB
        pool = Pool()
        pool.add_parsed_page_mapping(ppm)
        b = PageTableRequestBuilder(pool, _featmgr())
        b.build()
        req = b.page_reqs_by_map[DEFAULT_MAP_ID][0]
        self.assertEqual(req.va.exact, 0x805000000)
        self.assertEqual(req.pa.exact, 0x805000000)

    def test_free_va_pa_carry_masks(self):
        pool = Pool()
        pool.add_parsed_page_mapping(_mapping("free", "free_pa", address_mask=0xFFFFFFFFFFE00000, phys_address_mask=0xFFFFFFFFFFE00000))
        b = PageTableRequestBuilder(pool, _featmgr())
        b.build()
        req = b.page_reqs_by_map[DEFAULT_MAP_ID][0]
        self.assertIsNone(req.va.exact)
        self.assertEqual(req.va.and_mask, 0xFFFFFFFFFFE00000)
        self.assertEqual(req.pa.and_mask, 0xFFFFFFFFFFE00000)

    def test_phys_pinned_unspecified_lin_is_identity(self):
        # An MMIO-style mapping declares phys_addr with no lin_addr; the runtime reaches
        # it at its raw physical address, so the VA must default to the PA (VA == PA).
        pool = Pool()
        ppm = _mapping("mmio", "mmio", phys_addr_specified=True)
        ppm.phys_addr = "0x44000000"
        pool.add_parsed_page_mapping(ppm)
        b = PageTableRequestBuilder(pool, _featmgr())
        b.build()
        req = b.page_reqs_by_map[DEFAULT_MAP_ID][0]
        self.assertEqual(req.va.exact, 0x44000000)
        self.assertEqual(req.pa.exact, 0x44000000)

    def test_phys_pinned_declared_lin_random_addr_stays_movable(self):
        # Nightly svpbmt1: a mapping with phys_addr= pinned and a free
        # ;#random_addr(type=linear) must keep a movable VA. The identity default is only
        # for lin-unspecified MMIO pages; applying it here pins the VA onto the PA and
        # collides with modify_pt coloring against other exact index-0 pages.
        # Under unified ownership the bare addr::lin3 owns the free draw and the page
        # SameAs it (still movable, not VA==PA).
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="lin3", type=RV.AddressType.LINEAR, size=0x1000, and_mask=0xFFFFFFFFFFFFF000))
        ppm = _mapping("lin3", "lin3", phys_addr_specified=True, address_mask=0xFFFFFFFFFFFFF000, modify_pt=True)
        ppm.phys_addr = "0x200c000"
        pool.add_parsed_page_mapping(ppm)
        b = PageTableRequestBuilder(pool, _featmgr(paging_mode=RV.RiscvPagingModes.SV57))
        t = b.build()
        req = b.page_reqs_by_map[DEFAULT_MAP_ID][0]
        self.assertIsNone(req.va.exact, "declared linear random_addr must not become VA==PA")
        self.assertEqual(_rel(req.va, RecipeSameAs).target, "addr::lin3")
        self.assertEqual(req.pa.exact, 0x200C000)
        bare = next(r for r in t.address_reqs if t.addr_names_by_id.get(r.request_id) == "lin3")
        self.assertIsNotNone(bare.addr.and_mask)
        assert bare.addr.and_mask is not None
        self.assertEqual(bare.addr.and_mask & 0xFFF, 0)

    def test_page_mapping_va_follows_derive_from(self):
        # A page-mapping VA that names a derive_from random_addr (a PMA buddy pinned at
        # src ^ 0x1000) SameAs the bare addr:: recipe that carries the derivation.
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="src", type=RV.AddressType.LINEAR, size=0x1000, and_mask=0xFFFFFFFFFFFFE000))
        pool.add_parsed_addr(ParsedRandomAddress(name="bud", type=RV.AddressType.LINEAR, size=0x1000, derive_from="src", derive_not_mask=0x1000))
        pool.add_parsed_page_mapping(_mapping("bud", "bud_pa"))
        b = PageTableRequestBuilder(pool, _featmgr())
        t = b.build()
        req = b.page_reqs_by_map[DEFAULT_MAP_ID][0]
        self.assertEqual(_rel(req.va, RecipeSameAs).target, "addr::bud")
        bare = next(r for r in t.address_reqs if t.addr_names_by_id.get(r.request_id) == "bud")
        self.assertEqual(_rel(bare.addr, RecipeOffsetFrom).delta, 0x1000)
        self.assertEqual(_rel(bare.addr, RecipeOffsetFrom).target, "addr::src")

    def test_page_mapping_va_derives_from_another_page_va(self):
        # Buddy and source are both page-mapped random_addrs: each page SameAs its
        # bare addr::, and the buddy bare recipe OffsetFrom the source bare recipe.
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="src", type=RV.AddressType.LINEAR, size=0x1000, and_mask=0xFFFFFFFFFFFFE000))
        pool.add_parsed_addr(ParsedRandomAddress(name="bud", type=RV.AddressType.LINEAR, size=0x1000, derive_from="src", derive_not_mask=0x1000))
        pool.add_parsed_page_mapping(_mapping("src", "src_pa"))
        pool.add_parsed_page_mapping(_mapping("bud", "bud_pa"))
        b = PageTableRequestBuilder(pool, _featmgr())
        t = b.build()
        by_lin = {b.names_by_id[r.page_id][0]: r for r in b.page_reqs_by_map[DEFAULT_MAP_ID]}
        bud_req = by_lin["bud"]
        self.assertEqual(_rel(bud_req.va, RecipeSameAs).target, "addr::bud")
        bare_bud = next(r for r in t.address_reqs if t.addr_names_by_id.get(r.request_id) == "bud")
        self.assertEqual(_rel(bare_bud.addr, RecipeOffsetFrom).target, "addr::src")

    def test_secure_pa_gets_qualifier(self):
        pool = Pool()
        pool.add_parsed_page_mapping(_mapping("s", "s_pa", secure=True))
        b = PageTableRequestBuilder(pool, _featmgr())
        b.build()
        req = b.page_reqs_by_map[DEFAULT_MAP_ID][0]
        self.assertIn(RV.AddressQualifiers.ADDRESS_SECURE, req.pa.qualifiers)

    def test_alias_becomes_same_as_on_pa(self):
        pool = Pool()
        pool.add_parsed_page_mapping(_mapping("orig", "shared_pa"))
        pool.add_parsed_page_mapping(_mapping("aliasp", "shared_pa", alias=True))
        b = PageTableRequestBuilder(pool, _featmgr())
        t = b.build()
        by_lin = {t.names_by_id[r.page_id][0]: r for r in b.page_reqs_by_map[DEFAULT_MAP_ID]}
        alias_req = by_lin["aliasp"]
        self.assertEqual(_rel(alias_req.pa, RecipeSameAs).target, by_lin["orig"].page_id)

    def test_gstage_page_sizes_only_in_two_stage(self):
        pool = Pool()
        ppm = _mapping("g", "g_pa")
        ppm.gstage_vs_leaf_final_pagesize = RV.RiscvPageSizes.S2MB
        pool.add_parsed_page_mapping(ppm)
        # single-stage: g-stage sizes dropped
        single = PageTableRequestBuilder(pool, _featmgr())
        single.build()
        self.assertIsNone(single.page_reqs_by_map[DEFAULT_MAP_ID][0].gstage_vs_leaf_size)
        # two-stage: carried through
        fm = _featmgr(paging_g_mode=RV.RiscvPagingModes.SV39, env=RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED)
        two = PageTableRequestBuilder(pool, fm)
        two.build()
        self.assertEqual(two.page_reqs_by_map[DEFAULT_MAP_ID][0].gstage_vs_leaf_size, RV.RiscvPageSizes.S2MB)


class TestPageAddrDomains(unittest.TestCase):
    """The shared page-address classifier (:meth:`_page_addr_domains`) tags each name a
    page mapping owns by domain (lin/phys) and records its owning map_os page id."""

    def test_classifies_lin_and_phys_with_owning_page(self):
        pool = Pool()
        pool.add_parsed_page_mapping(_mapping("parent", "parent_pa"))
        pool.add_parsed_page_mapping(_mapping("child", "child_pa"))
        b = PageTableRequestBuilder(pool, _featmgr())
        b.build()
        domains = b._page_addr_domains()
        self.assertEqual(domains["child"], ("lin", b._page_id("child", DEFAULT_MAP_ID)))
        self.assertEqual(domains["child_pa"], ("phys", b._page_id("child", DEFAULT_MAP_ID)))
        self.assertEqual(domains["parent"], ("lin", b._page_id("parent", DEFAULT_MAP_ID)))
        self.assertEqual(domains["parent_pa"], ("phys", b._page_id("parent", DEFAULT_MAP_ID)))

    def test_private_only_page_uses_its_actual_owner_map(self):
        pool = Pool()
        pool.add_parsed_addr(
            ParsedRandomAddress(
                name="src",
                type=RV.AddressType.LINEAR,
                size=0x1000,
            )
        )
        pool.add_parsed_addr(
            ParsedRandomAddress(
                name="derived",
                type=RV.AddressType.LINEAR,
                size=0x1000,
                derive_from="src",
                derive_or_mask=0x2000,
            )
        )
        pool.add_parsed_page_mapping(
            _mapping(
                "src",
                "src_pa",
                in_private_map=True,
                page_maps=["map_a"],
            )
        )

        translated = PageTableRequestBuilder(pool, _featmgr()).build()
        derived = next(req for req in translated.address_reqs if translated.addr_names_by_id.get(req.request_id) == "derived")

        self.assertEqual(_rel(derived.addr, RecipeDerivedFrom).target, "addr::src")


class TestPhysicalDeriveSource(unittest.TestCase):
    """A physical random_addr deriving from a page's phys name must target the page's
    PA/destination slot -- the page owns its PA (no bare addr:: request), so a bare
    ``addr::<name>`` target would dangle and blow up in the fixed-point materialize."""

    def _drv(self):
        return ParsedRandomAddress(name="drv", type=RV.AddressType.PHYSICAL, size=0x1000, derive_from="phys_src", derive_or_mask=0x2000, derive_not_mask=0x4000)

    def test_targets_dst_slot_single_stage_enabled(self):
        # Single-stage enabled: the PA lives on the page's __dst destination page.
        pool = Pool()
        pool.add_parsed_page_mapping(_mapping("lin", "phys_src"))
        pool.add_parsed_addr(self._drv())
        b = PageTableRequestBuilder(pool, _featmgr(paging_mode=RV.RiscvPagingModes.SV39))
        t = b.build()
        by_lin = {t.names_by_id[r.page_id][0]: r for r in b.page_reqs_by_map[DEFAULT_MAP_ID]}
        drv = next(r for r in t.address_reqs if t.addr_names_by_id.get(r.request_id) == "drv")
        self.assertEqual(_rel(drv.addr, RecipeDerivedFrom).target, by_lin["lin"].page_id + "__dst")

    def test_targets_page_itself_paging_disabled(self):
        # Paging disabled (VA == PA): the page IS its own physical page, so the PA slot is
        # the plain page id (no __dst suffix).
        pool = Pool()
        pool.add_parsed_page_mapping(_mapping("lin", "phys_src"))
        pool.add_parsed_addr(self._drv())
        b = PageTableRequestBuilder(pool, _featmgr(paging_mode=RV.RiscvPagingModes.DISABLE))
        t = b.build()
        by_lin = {t.names_by_id[r.page_id][0]: r for r in b.page_reqs_by_map[DEFAULT_MAP_ID]}
        drv = next(r for r in t.address_reqs if t.addr_names_by_id.get(r.request_id) == "drv")
        self.assertEqual(_rel(drv.addr, RecipeDerivedFrom).target, by_lin["lin"].page_id)


class TestAddrOffsetFromRandomAddr(unittest.TestCase):
    """``lin_name``/``phys_name`` as ``random_addr+offset`` OffsetFrom the bare recipe.

    Mirrors the cluster pgx_hetero / pgx_v2 4KB→2MB crosser: a window
    ``;#random_addr`` with only offset ``page_mapping`` rows (no base
    ``page_mapping(lin_name=px4kb2mb)``). The bare ``addr::`` owns the window;
    each leaf OffsetFrom it and reserves only its pagesize footprint.
    """

    def test_offset_child_offsets_from_bare_addr(self):
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="parent", type=RV.AddressType.LINEAR, size=0x2000, and_mask=0xFFFFFFFFFFFFE000))
        pool.add_parsed_addr(ParsedRandomAddress(name="parent_pa", type=RV.AddressType.PHYSICAL, size=0x2000, and_mask=0xFFFFFFFFFFFFE000))
        pool.add_parsed_page_mapping(_mapping("parent", "parent_pa"))
        child = _mapping("parent+0x1000", "parent_pa+0x1000")
        child.lin_addr_link = ("parent", 0x1000)
        child.phys_addr_link = ("parent_pa", 0x1000)
        pool.add_parsed_page_mapping(child)
        b = PageTableRequestBuilder(pool, _featmgr())
        t = b.build()
        reqs = b.page_reqs_by_map[DEFAULT_MAP_ID]
        self.assertEqual(len(reqs), 2)
        by_lin = {t.names_by_id[r.page_id][0]: r for r in reqs}
        child_req = by_lin["parent+0x1000"]
        for side in (child_req.va, child_req.pa):
            self.assertEqual(_rel(side, RecipeOffsetFrom).delta, 0x1000)
        self.assertEqual(_rel(child_req.va, RecipeOffsetFrom).target, "addr::parent")
        self.assertEqual(_rel(child_req.pa, RecipeOffsetFrom).target, "addr::parent_pa")

    def test_pgx_style_offset_only_pages_offset_from_bare_addr(self):
        pool = Pool()
        pool.add_parsed_addr(
            ParsedRandomAddress(
                name="px4kb2mb",
                type=RV.AddressType.LINEAR,
                size=0x400000,
                and_mask=0xFFFFFFFFFFE00000,
            )
        )
        pool.add_parsed_addr(
            ParsedRandomAddress(
                name="px4kb2mb_p",
                type=RV.AddressType.PHYSICAL,
                size=0x400000,
                and_mask=0xFFFFFFFFFFE00000,
            )
        )
        leaf4k = _mapping(
            "px4kb2mb+0x1ff000",
            "px4kb2mb_p+0x1ff000",
            final_pagesize=RV.RiscvPageSizes.S4KB,
            address_size=0x1000,
            phys_address_size=0x1000,
        )
        leaf4k.lin_addr_link = ("px4kb2mb", 0x1FF000)
        leaf4k.phys_addr_link = ("px4kb2mb_p", 0x1FF000)
        leaf2m = _mapping(
            "px4kb2mb+0x200000",
            "px4kb2mb_p+0x200000",
            final_pagesize=RV.RiscvPageSizes.S2MB,
            address_size=0x200000,
            phys_address_size=0x200000,
        )
        leaf2m.lin_addr_link = ("px4kb2mb", 0x200000)
        leaf2m.phys_addr_link = ("px4kb2mb_p", 0x200000)
        pool.add_parsed_page_mapping(leaf4k)
        pool.add_parsed_page_mapping(leaf2m)

        b = PageTableRequestBuilder(pool, _featmgr(paging_mode=RV.RiscvPagingModes.SV57))
        t = b.build()
        by_lin = {t.names_by_id[r.page_id][0]: r for r in b.page_reqs_by_map[DEFAULT_MAP_ID]}
        self.assertEqual(set(by_lin), {"px4kb2mb+0x1ff000", "px4kb2mb+0x200000"})

        for lin, offset, pagesize in (
            ("px4kb2mb+0x1ff000", 0x1FF000, RV.RiscvPageSizes.S4KB),
            ("px4kb2mb+0x200000", 0x200000, RV.RiscvPageSizes.S2MB),
        ):
            req = by_lin[lin]
            self.assertEqual(_rel(req.va, RecipeOffsetFrom).target, "addr::px4kb2mb")
            self.assertEqual(_rel(req.va, RecipeOffsetFrom).delta, offset)
            self.assertEqual(_rel(req.pa, RecipeOffsetFrom).target, "addr::px4kb2mb_p")
            self.assertEqual(_rel(req.pa, RecipeOffsetFrom).delta, offset)
            leaf = RV.RiscvPageSizes.memory(pagesize)
            self.assertEqual(req.va_reserve_size, leaf)
            self.assertEqual(req.pa_reserve_size, leaf)

        bare = {t.addr_names_by_id[r.request_id]: r for r in t.address_reqs}
        self.assertEqual(set(bare), {"px4kb2mb", "px4kb2mb_p"})
        self.assertEqual(bare["px4kb2mb"].size, 0x400000)
        self.assertEqual(bare["px4kb2mb"].addr.and_mask, 0xFFFFFFFFFFE00000)
        self.assertEqual(bare["px4kb2mb_p"].size, 0x400000)

    def test_base_page_sameas_and_sibling_offset_from_same_addr(self):
        # pgx_v2-style: a base page_mapping(lin_name=v4kb) plus lin_name=v4kb+0x1000.
        # Both attach to the same bare addr::; neither nests under the other page.
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="v4kb", type=RV.AddressType.LINEAR, size=0x2000, and_mask=0xFFFFFFFFFFFFE000))
        pool.add_parsed_addr(ParsedRandomAddress(name="v4kb_p", type=RV.AddressType.PHYSICAL, size=0x2000, and_mask=0xFFFFFFFFFFFFE000))
        base = _mapping("v4kb", "v4kb_p")
        child = _mapping("v4kb+0x1000", "v4kb_p+0x1000")
        child.lin_addr_link = ("v4kb", 0x1000)
        child.phys_addr_link = ("v4kb_p", 0x1000)
        pool.add_parsed_page_mapping(base)
        pool.add_parsed_page_mapping(child)

        b = PageTableRequestBuilder(pool, _featmgr())
        t = b.build()
        by_lin = {t.names_by_id[r.page_id][0]: r for r in b.page_reqs_by_map[DEFAULT_MAP_ID]}
        self.assertEqual(_rel(by_lin["v4kb"].va, RecipeSameAs).target, "addr::v4kb")
        self.assertIsNone(by_lin["v4kb"].va_reserve_size)
        self.assertEqual(_rel(by_lin["v4kb"].pa, RecipeSameAs).target, "addr::v4kb_p")
        self.assertIsNone(by_lin["v4kb"].pa_reserve_size)
        self.assertEqual(_rel(by_lin["v4kb+0x1000"].va, RecipeOffsetFrom).target, "addr::v4kb")
        self.assertEqual(_rel(by_lin["v4kb+0x1000"].va, RecipeOffsetFrom).delta, 0x1000)
        self.assertEqual(by_lin["v4kb+0x1000"].va_reserve_size, 0x1000)
        bare_names = {t.addr_names_by_id[r.request_id] for r in t.address_reqs}
        self.assertEqual(bare_names, {"v4kb", "v4kb_p"})

    def test_sameas_page_widens_undersized_bare_addr(self):
        # ;#random_addr(size=256) + 4KB page_mapping SameAs addr:: — bare must widen
        # to pagesize so riemap can fold the page's default 4KB claim into the window.
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="tiny", type=RV.AddressType.LINEAR, size=256, and_mask=0xFFFFFFFFFFFFF000))
        pool.add_parsed_addr(ParsedRandomAddress(name="tiny_p", type=RV.AddressType.PHYSICAL, size=256, and_mask=0xFFFFFFFFFFFFF000))
        pool.add_parsed_page_mapping(_mapping("tiny", "tiny_p"))

        b = PageTableRequestBuilder(pool, _featmgr())
        t = b.build()
        by_lin = {t.names_by_id[r.page_id][0]: r for r in b.page_reqs_by_map[DEFAULT_MAP_ID]}
        self.assertIsInstance(by_lin["tiny"].va.relation, RecipeSameAs)
        self.assertIsNone(by_lin["tiny"].va_reserve_size)
        self.assertIsNone(by_lin["tiny"].pa_reserve_size)

        bare = {t.addr_names_by_id[r.request_id]: r for r in t.address_reqs}
        self.assertGreaterEqual(bare["tiny"].size, 0x1000)
        self.assertGreaterEqual(bare["tiny_p"].size, 0x1000)


class TestSameAsUnifyFallouts(unittest.TestCase):
    """Regressions from free-draw pages early-returning SameAs/OffsetFrom(addr::)."""

    def test_free_draw_secure_sameas_pa(self):
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="s", type=RV.AddressType.LINEAR, size=0x1000))
        pool.add_parsed_addr(ParsedRandomAddress(name="s_pa", type=RV.AddressType.PHYSICAL, size=0x1000))
        pool.add_parsed_page_mapping(_mapping("s", "s_pa", secure=True))
        b = PageTableRequestBuilder(pool, _featmgr())
        t = b.build()
        req = b.page_reqs_by_map[DEFAULT_MAP_ID][0]
        self.assertEqual(_rel(req.pa, RecipeSameAs).target, "addr::s_pa")
        self.assertFalse(req.pa.qualifiers, "a relational follower must not duplicate root qualifiers")
        bare = next(r for r in t.address_reqs if t.addr_names_by_id.get(r.request_id) == "s_pa")
        self.assertIn(RV.AddressQualifiers.ADDRESS_SECURE, bare.addr.qualifiers)

    def test_offset_from_secure_pa(self):
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="base", type=RV.AddressType.LINEAR, size=0x2000))
        pool.add_parsed_addr(ParsedRandomAddress(name="base_pa", type=RV.AddressType.PHYSICAL, size=0x2000))
        base = _mapping("base", "base_pa")
        child = _mapping("base+0x1000", "base_pa+0x1000", secure=True)
        child.lin_addr_link = ("base", 0x1000)
        child.phys_addr_link = ("base_pa", 0x1000)
        pool.add_parsed_page_mapping(base)
        pool.add_parsed_page_mapping(child)
        b = PageTableRequestBuilder(pool, _featmgr())
        t = b.build()
        by_lin = {t.names_by_id[r.page_id][0]: r for r in b.page_reqs_by_map[DEFAULT_MAP_ID]}
        child_req = by_lin["base+0x1000"]
        self.assertEqual(_rel(child_req.pa, RecipeOffsetFrom).target, "addr::base_pa")
        self.assertFalse(child_req.pa.qualifiers, "a relational follower must not duplicate root qualifiers")
        bare = next(r for r in t.address_reqs if t.addr_names_by_id.get(r.request_id) == "base_pa")
        self.assertIn(RV.AddressQualifiers.ADDRESS_SECURE, bare.addr.qualifiers)

    def test_ppm_only_secure_promotes_bare_phys(self):
        # page secure=1, random_addr without secure=1 → bare phys still ADDRESS_SECURE.
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="p", type=RV.AddressType.LINEAR, size=0x1000))
        pool.add_parsed_addr(ParsedRandomAddress(name="p_pa", type=RV.AddressType.PHYSICAL, size=0x1000, secure=False))
        pool.add_parsed_page_mapping(_mapping("p", "p_pa", secure=True))
        t = PageTableRequestBuilder(pool, _featmgr()).build()
        bare = next(r for r in t.address_reqs if t.addr_names_by_id.get(r.request_id) == "p_pa")
        self.assertIn(RV.AddressQualifiers.ADDRESS_SECURE, bare.addr.qualifiers)

    def test_bare_secure_phys_is_the_only_qualified_sameas_owner(self):
        # random_addr secure=1, page_mapping secure=0: the bare root owns the class and
        # the follower carries only SameAs. RieMap derives the secure PTE from that root.
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="p", type=RV.AddressType.LINEAR, size=0x1000))
        pool.add_parsed_addr(
            ParsedRandomAddress(
                name="p_pa",
                type=RV.AddressType.PHYSICAL,
                size=0x1000,
                secure=True,
            )
        )
        pool.add_parsed_page_mapping(_mapping("p", "p_pa", secure=False))
        b = PageTableRequestBuilder(pool, _featmgr())
        t = b.build()
        req = b.page_reqs_by_map[DEFAULT_MAP_ID][0]
        self.assertIsInstance(req.pa.relation, RecipeSameAs)
        self.assertFalse(req.pa.qualifiers)
        bare = next(r for r in t.address_reqs if t.addr_names_by_id.get(r.request_id) == "p_pa")
        self.assertIn(RV.AddressQualifiers.ADDRESS_SECURE, bare.addr.qualifiers)

    def test_modify_nonleaf_aligns_bare_va_mask(self):
        # SV39 4KB: leaf-table span is 2 MiB; bare addr:: must clear those low bits.
        span = 0x200000
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="m", type=RV.AddressType.LINEAR, size=0x1000, and_mask=0xFFFFFFFFFFFFF000))
        pool.add_parsed_addr(ParsedRandomAddress(name="m_pa", type=RV.AddressType.PHYSICAL, size=0x1000, and_mask=0xFFFFFFFFFFFFF000))
        pool.add_parsed_page_mapping(_mapping("m", "m_pa", modify_nonleaf_pt=True))
        t = PageTableRequestBuilder(pool, _featmgr(paging_mode=RV.RiscvPagingModes.SV39)).build()
        bare = next(r for r in t.address_reqs if t.addr_names_by_id.get(r.request_id) == "m")
        self.assertIsNotNone(bare.addr.and_mask)
        assert bare.addr.and_mask is not None
        self.assertEqual(bare.addr.and_mask & (span - 1), 0)

    def test_twostage_gleaf_widens_bare_phys(self):
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="g", type=RV.AddressType.LINEAR, size=0x1000))
        pool.add_parsed_addr(ParsedRandomAddress(name="g_pa", type=RV.AddressType.PHYSICAL, size=0x1000))
        ppm = _mapping("g", "g_pa")
        ppm.gstage_vs_leaf_final_pagesize = RV.RiscvPageSizes.S2MB
        pool.add_parsed_page_mapping(ppm)
        fm = _featmgr(paging_g_mode=RV.RiscvPagingModes.SV39, env=RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED)
        fm.reserve_partial_phys_memory = False
        t = PageTableRequestBuilder(pool, fm).build()
        bare = next(r for r in t.address_reqs if t.addr_names_by_id.get(r.request_id) == "g_pa")
        self.assertGreaterEqual(bare.size, 0x200000)

        fm.reserve_partial_phys_memory = True
        t_flag = PageTableRequestBuilder(pool, fm).build()
        bare_flag = next(r for r in t_flag.address_reqs if t_flag.addr_names_by_id.get(r.request_id) == "g_pa")
        self.assertEqual(bare_flag.size, 0x1000)


class TestAddressAndReserved(unittest.TestCase):
    def test_free_random_addr(self):
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="buf", type=RV.AddressType.PHYSICAL, size=0x2000, addr_bits=40, and_mask=0xFFFFFFFFFFFFF000))
        t = PageTableRequestBuilder(pool, _featmgr()).build()
        self.assertEqual(len(t.address_reqs), 1)
        req = t.address_reqs[0]
        self.assertEqual(req.addr_type, RV.AddressType.PHYSICAL)
        self.assertEqual(req.size, 0x2000)
        self.assertEqual(req.addr.bits, 40)
        self.assertEqual(req.addr.and_mask, 0xFFFFFFFFFFFFF000)
        self.assertEqual(t.addr_names_by_id[req.request_id], "buf")

    def test_fixed_random_addr(self):
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="pin", type=RV.AddressType.PHYSICAL, fixed_addr=0x90000000))
        t = PageTableRequestBuilder(pool, _featmgr()).build()
        self.assertEqual(t.address_reqs[0].addr.exact, 0x90000000)

    def test_buddy_becomes_offset_from_source(self):
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="src", type=RV.AddressType.LINEAR, size=0x1000, and_mask=0xFFFFFFFFFFFFE000))
        pool.add_parsed_addr(ParsedRandomAddress(name="bud", type=RV.AddressType.LINEAR, size=0x1000, derive_from="src", derive_not_mask=0x1000))
        t = PageTableRequestBuilder(pool, _featmgr()).build()
        by_name = {t.addr_names_by_id[r.request_id]: r for r in t.address_reqs}
        rel = _rel(by_name["bud"].addr, RecipeOffsetFrom)
        self.assertEqual(rel.target, by_name["src"].request_id)
        self.assertEqual(rel.delta, 0x1000)

    def test_deterministic_derived_becomes_derived_from(self):
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="src", type=RV.AddressType.LINEAR, size=0x1000))
        pool.add_parsed_addr(ParsedRandomAddress(name="drv", type=RV.AddressType.LINEAR, size=0x1000, derive_from="src", derive_or_mask=0x2000, derive_not_mask=0x4000))
        t = PageTableRequestBuilder(pool, _featmgr()).build()
        by_name = {t.addr_names_by_id[r.request_id]: r for r in t.address_reqs}
        rel = _rel(by_name["drv"].addr, RecipeDerivedFrom)
        self.assertEqual(rel.target, by_name["src"].request_id)
        self.assertEqual(rel.or_mask, 0x2000)
        self.assertEqual(rel.not_mask, 0x4000)

    def test_partial_select_mask_derived_carries_random_mask(self):
        # A partial select mask pins the selected bits and randomizes the rest: the
        # translator emits a DerivedFrom carrying random_mask (the unselected bits),
        # plus the addr's own alignment mask/bits for the free draw.
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="src", type=RV.AddressType.LINEAR, size=0x1000))
        pool.add_parsed_addr(
            ParsedRandomAddress(
                name="drv",
                type=RV.AddressType.LINEAR,
                size=0x1000,
                derive_from="src",
                derive_and_mask=0xFFFF0000,
                derive_or_mask=0x2000,
                derive_not_mask=0x40000000,
                addr_bits=32,
                and_mask=0xFFFFFFFFFFFFF000,
            )
        )
        t = PageTableRequestBuilder(pool, _featmgr()).build()
        by_name = {t.addr_names_by_id[r.request_id]: r for r in t.address_reqs}
        spec = by_name["drv"].addr
        rel = _rel(spec, RecipeDerivedFrom)
        self.assertEqual(rel.and_mask, 0xFFFF0000)
        self.assertEqual(rel.or_mask, 0x2000)
        self.assertEqual(rel.not_mask, 0x40000000)
        self.assertEqual(rel.random_mask, (~0xFFFF0000) & 0xFFFFFFFF)  # unselected bits within addr width
        self.assertEqual(spec.and_mask, 0xFFFFFFFFFFFFF000)
        self.assertEqual(spec.bits, 32)

    def test_reserved_span(self):
        # The parser stores addr_type as a string ("physical"/"linear"); the
        # translator maps it to the AddressType the builder's addrgen expects.
        pool = Pool()
        pool.add_parsed_res_mem(ParsedReserveMemory(name="r", addr_type="physical", size=0x4000, start_addr=0x80000000))
        t = PageTableRequestBuilder(pool, _featmgr()).build()
        self.assertEqual(t.reserved_spans, [(RV.AddressType.PHYSICAL, 0x80000000, 0x4000)])


class TestCustomRegion(unittest.TestCase):
    def _fm(self):
        mem = Memory(
            dram_ranges=(DramRange(start=0x80000000, size=0x100000000),),
            custom_ranges=(CustomRange(name="probe", start=0x60000000, size=0x1000000),),
        )
        return _featmgr(memory=mem)

    def test_custom_region_addr_placed_in_region(self):
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="buf", type=RV.AddressType.PHYSICAL, size=0x1000, custom_region="probe"))
        t = PageTableRequestBuilder(pool, self._fm()).build()
        # A fixed MemoryRegion is registered for the referenced range...
        region = t.regions_by_id["custom::probe"]
        self.assertEqual(region.base, 0x60000000)
        self.assertEqual(region.size, 0x1000000)
        # ...and the address constrains itself in_region rather than drawing freely.
        req = next(r for r in t.address_reqs if t.addr_names_by_id.get(r.request_id) == "buf")
        self.assertIs(req.addr.region, region)

    def test_custom_region_carries_masks_and_width_into_placement(self):
        # A custom_region member is placed inside its window, but the masks and the
        # address width are part of that placement -- not just the AND mask.
        pool = Pool()
        pool.add_parsed_addr(
            ParsedRandomAddress(
                name="buf",
                type=RV.AddressType.PHYSICAL,
                size=0x1000,
                custom_region="probe",
                and_mask=0xFFFFFFFFFFFF0000,
                or_mask=0x8000,
                addr_bits=32,
            )
        )
        t = PageTableRequestBuilder(pool, self._fm()).build()
        req = next(r for r in t.address_reqs if t.addr_names_by_id.get(r.request_id) == "buf")
        self.assertIs(req.addr.region, t.regions_by_id["custom::probe"])
        self.assertEqual(req.addr.and_mask, 0xFFFFFFFFFFFF0000)
        self.assertEqual(req.addr.or_mask, 0x8000)
        self.assertEqual(req.addr.bits, 32)

    def test_linear_custom_region_is_free_draw(self):
        # custom_region only bounds a physical draw; a linear one is ignored (free draw).
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="lbuf", type=RV.AddressType.LINEAR, size=0x1000, custom_region="probe"))
        t = PageTableRequestBuilder(pool, self._fm()).build()
        self.assertNotIn("custom::probe", t.regions_by_id)
        req = next(r for r in t.address_reqs if t.addr_names_by_id.get(r.request_id) == "lbuf")
        self.assertIsNone(req.addr.region)

    def test_in_pma_with_custom_region_raises(self):
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="bad", type=RV.AddressType.PHYSICAL, size=0x1000, custom_region="probe", in_pma=True))
        with self.assertRaises(ValueError):
            PageTableRequestBuilder(pool, self._fm()).build()

    def test_custom_region_page_pa_placed_in_region(self):
        # A page whose phys_name references a physical custom_region random_addr places
        # its PA in_region (page-owned -- no duplicate bare request), matching the
        # region-member claim path riemap requires.
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="lin", type=RV.AddressType.LINEAR, size=0x1000, and_mask=0xFFFFFFFFFFFFF000))
        pool.add_parsed_addr(ParsedRandomAddress(name="phys", type=RV.AddressType.PHYSICAL, size=0x1000, and_mask=0xFFFFFFFFFFFFF000, custom_region="probe"))
        pool.add_parsed_page_mapping(_mapping("lin", "phys", v=1, r=1, w=1))
        builder = PageTableRequestBuilder(pool, self._fm())
        t = builder.build()
        req = builder.page_reqs_by_map[DEFAULT_MAP_ID][0]
        self.assertIs(req.pa.region, builder.regions_by_id["custom::probe"])
        self.assertEqual(req.pa.and_mask, 0xFFFFFFFFFFFFF000)
        self.assertEqual(req.pa.qualifiers, set())  # no secure/mmio qualifier on a custom-region draw
        self.assertNotIn("phys", [t.addr_names_by_id.get(r.request_id) for r in t.address_reqs])

    def test_linear_custom_region_page_va_stays_free(self):
        # A linear custom_region is a no-op: a page VA referencing one draws freely
        # (no in_region), and the linear name keeps its own bare request.
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="lin", type=RV.AddressType.LINEAR, size=0x1000, custom_region="probe"))
        pool.add_parsed_addr(ParsedRandomAddress(name="phys", type=RV.AddressType.PHYSICAL, size=0x1000))
        pool.add_parsed_page_mapping(_mapping("lin", "phys", v=1, r=1, w=1))
        builder = PageTableRequestBuilder(pool, self._fm())
        builder.build()
        req = builder.page_reqs_by_map[DEFAULT_MAP_ID][0]
        self.assertIsNone(req.va.region)


class TestRegionPmaProvenance(unittest.TestCase):
    """``Translation.region_pma`` provenance: custom_region is absent; an in_pma region
    without an explicit ``pma_region_bindings`` map (standalone construction) defaults to
    ``register_on_readback=True``; a caller-supplied binding is threaded through verbatim."""

    def test_custom_region_absent_from_region_pma(self):
        mem = Memory(
            dram_ranges=(DramRange(start=0x80000000, size=0x100000000),),
            custom_ranges=(CustomRange(name="probe", start=0x60000000, size=0x1000000),),
        )
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="buf", type=RV.AddressType.PHYSICAL, size=0x1000, custom_region="probe"))
        t = PageTableRequestBuilder(pool, _featmgr(memory=mem)).build()
        custom_region = t.regions_by_id["custom::probe"]
        self.assertNotIn(custom_region, t.region_pma)

    def test_standalone_construction_defaults_to_register_on_readback(self):
        # No pma_region_bindings passed in: the builder has no other provenance to trust,
        # so it defaults to registering the region once.
        pma_info = PmaInfo(pma_name="pma_buf", pma_address=0, pma_size=0x1000, pma_valid=True)
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="buf", type=RV.AddressType.PHYSICAL, size=0x1000, in_pma=True, pma_info=pma_info))
        t = PageTableRequestBuilder(pool, _featmgr()).build()
        region_id = t._pma_region_of("buf")
        self.assertIsNotNone(region_id)
        assert region_id is not None
        region = t.regions_by_id[region_id]
        binding = t.region_pma[region]
        self.assertIs(binding.info, pma_info)
        self.assertTrue(binding.register_on_readback)

    def test_caller_supplied_binding_is_threaded_through(self):
        from riescue.dtest_framework.generator.generator import PmaRegionBinding

        pma_info = PmaInfo(pma_name="dram_hint", pma_address=0x9000_0000, pma_size=0x1000, pma_valid=True)
        binding = PmaRegionBinding(info=pma_info, register_on_readback=False)
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="buf", type=RV.AddressType.PHYSICAL, size=0x1000, in_pma=True, pma_info=pma_info))
        t = PageTableRequestBuilder(pool, _featmgr(), pma_region_bindings={id(pma_info): binding}).build()
        region_id = t._pma_region_of("buf")
        self.assertIsNotNone(region_id)
        assert region_id is not None
        region = t.regions_by_id[region_id]
        self.assertIs(t.region_pma[region], binding)


class TestPmaRegion(unittest.TestCase):
    def test_existing_based_pma_still_constrains_member_placement(self):
        pma = MagicMock(
            pma_name="dram_hint",
            pma_address=0x9000_0000,
            pma_size=0x20_0000,
            pma_memory_type="memory",
        )
        pool = Pool()
        pool.add_parsed_addr(
            ParsedRandomAddress(
                name="buf",
                type=RV.AddressType.PHYSICAL,
                size=0x1000,
                in_pma=True,
                pma_info=pma,
            )
        )

        translated = PageTableRequestBuilder(pool, _featmgr()).build()
        request = next(req for req in translated.address_reqs if translated.addr_names_by_id.get(req.request_id) == "buf")

        self.assertIsNotNone(request.addr.region)
        assert request.addr.region is not None
        self.assertEqual(request.addr.region.base, pma.pma_address)
        self.assertEqual(request.addr.region.size, pma.pma_size)


class TestInPmaDecoyExclusion(unittest.TestCase):
    """The three in_pma AddrSpec(region=...) sites disable the exclusion set (``exclude=()``)
    exactly when the region is an adopted randomized decoy -- never for a reused hint."""

    def _build_one_in_pma(self, pma_info: PmaInfo, addr_type=RV.AddressType.PHYSICAL, **addr_kw):
        pool = Pool()
        pool.add_parsed_addr(
            ParsedRandomAddress(
                name="buf",
                type=addr_type,
                size=0x1000,
                in_pma=True,
                pma_info=pma_info,
                **addr_kw,
            )
        )
        translated = PageTableRequestBuilder(pool, _featmgr()).build()
        return next(req for req in translated.address_reqs if translated.addr_names_by_id.get(req.request_id) == "buf")

    def test_decoy_adoption_yields_empty_exclude(self):
        # An adopted randomized decoy is itself one of the builder's declared exclusion
        # windows; a member drawn inside it must not veto its own placement.
        decoy = PmaInfo(pma_name="pma_rand_0", pma_address=0x9000_0000, pma_size=0x1000, pma_valid=True, pma_randomized=True)
        request = self._build_one_in_pma(decoy)
        self.assertEqual(request.addr.exclude, ())

    def test_hint_region_keeps_default_exclude(self):
        # A reused (non-decoy) hint region keeps the normal exclusion set.
        hint = PmaInfo(pma_name="dram_hint", pma_address=0x9000_0000, pma_size=0x20_0000, pma_valid=True, pma_randomized=False)
        request = self._build_one_in_pma(hint)
        self.assertIsNone(request.addr.exclude)

    def test_new_floating_region_keeps_default_exclude(self):
        # A brand-new floating region (pma_address still 0) is not a decoy either.
        new_region = PmaInfo(pma_name="pma_buf", pma_address=0, pma_size=0x1000, pma_valid=True)
        request = self._build_one_in_pma(new_region)
        self.assertIsNone(request.addr.exclude)

    def test_fixed_io_decoy_region_has_no_mmio_qualifier(self):
        # Master places inside fixed windows by geometry only. PMA memory_type=io must not
        # stamp ADDRESS_MMIO onto an already-anchored decoy (that empties bounds∩MMIO).
        decoy = PmaInfo(
            pma_name="pma_rand_io",
            pma_address=0x6147_DB84_00000,
            pma_size=0x40_0000,
            pma_memory_type="io",
            pma_valid=True,
            pma_randomized=True,
        )
        request = self._build_one_in_pma(decoy)
        self.assertEqual(request.addr.exclude, ())
        self.assertIsNotNone(request.addr.region)
        assert request.addr.region is not None
        self.assertNotIn(RV.AddressQualifiers.ADDRESS_MMIO, request.addr.region.qualifiers)

    def test_floating_region_mmio_follows_member_io_not_pma_type(self):
        # Master anchors floating regions from random_addr.io, never from pma_memory_type.
        type_only = PmaInfo(pma_name="pma_buf", pma_address=0, pma_size=0x1000, pma_memory_type="io", pma_valid=True)
        req_type_only = self._build_one_in_pma(type_only, io=False)
        self.assertIsNotNone(req_type_only.addr.region)
        assert req_type_only.addr.region is not None
        self.assertNotIn(RV.AddressQualifiers.ADDRESS_MMIO, req_type_only.addr.region.qualifiers)

        with_io = PmaInfo(pma_name="pma_mmio", pma_address=0, pma_size=0x1000, pma_memory_type="memory", pma_valid=True)
        req_io = self._build_one_in_pma(with_io, io=True)
        self.assertIsNotNone(req_io.addr.region)
        assert req_io.addr.region is not None
        self.assertIn(RV.AddressQualifiers.ADDRESS_MMIO, req_io.addr.region.qualifiers)


class TestBareAddrWidth(unittest.TestCase):
    def test_bare_linear_addr_defaults_to_paging_width(self):
        # A linear random_addr with no addr_bits inherits the test's linear-address width
        # (sv39 -> 39) rather than drawing a full 64-bit address.
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="lin", type=RV.AddressType.LINEAR, size=0x1000))
        t = PageTableRequestBuilder(pool, _featmgr(paging_mode=RV.RiscvPagingModes.SV39)).build()
        req = next(r for r in t.address_reqs if t.addr_names_by_id.get(r.request_id) == "lin")
        self.assertEqual(req.addr.bits, RV.RiscvPagingModes.linear_addr_bits(RV.RiscvPagingModes.SV39))

    def test_bare_physical_addr_defaults_to_phys_width(self):
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="phys", type=RV.AddressType.PHYSICAL, size=0x1000))
        t = PageTableRequestBuilder(pool, _featmgr()).build()
        req = next(r for r in t.address_reqs if t.addr_names_by_id.get(r.request_id) == "phys")
        self.assertEqual(req.addr.bits, 56)


class TestSecureRandomization(unittest.TestCase):
    def test_page_pa_rolled_secure_in_secure_mode(self):
        pool = Pool()
        pool.add_parsed_page_mapping(_mapping("p", "p_pa"))  # not explicitly secure
        fm = _featmgr(secure_mode=RV.RiscvSecureModes.SECURE, secure_access_probability=100)
        b = PageTableRequestBuilder(pool, fm, RandNum(seed=1))
        b.build()
        req = b.page_reqs_by_map[DEFAULT_MAP_ID][0]
        self.assertIn(RV.AddressQualifiers.ADDRESS_SECURE, req.pa.qualifiers)

    def test_page_pa_not_rolled_when_secure_mode_off(self):
        pool = Pool()
        pool.add_parsed_page_mapping(_mapping("p", "p_pa"))
        fm = _featmgr(secure_mode=RV.RiscvSecureModes.NON_SECURE, secure_access_probability=100)
        b = PageTableRequestBuilder(pool, fm, RandNum(seed=1))
        b.build()
        req = b.page_reqs_by_map[DEFAULT_MAP_ID][0]
        self.assertNotIn(RV.AddressQualifiers.ADDRESS_SECURE, req.pa.qualifiers)

    def test_bare_physical_addr_rolled_secure(self):
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="buf", type=RV.AddressType.PHYSICAL, size=0x1000))
        fm = _featmgr(secure_mode=RV.RiscvSecureModes.SECURE, secure_access_probability=100)
        t = PageTableRequestBuilder(pool, fm, RandNum(seed=1)).build()
        req = next(r for r in t.address_reqs if t.addr_names_by_id.get(r.request_id) == "buf")
        self.assertIn(RV.AddressQualifiers.ADDRESS_SECURE, req.addr.qualifiers)

    def test_io_addr_never_secure(self):
        # io wins over the secure roll (mirrors handle_random_addr).
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="mmio", type=RV.AddressType.PHYSICAL, size=0x1000, io=True))
        fm = _featmgr(secure_mode=RV.RiscvSecureModes.SECURE, secure_access_probability=100)
        t = PageTableRequestBuilder(pool, fm, RandNum(seed=1)).build()
        req = next(r for r in t.address_reqs if t.addr_names_by_id.get(r.request_id) == "mmio")
        self.assertIn(RV.AddressQualifiers.ADDRESS_MMIO, req.addr.qualifiers)
        self.assertNotIn(RV.AddressQualifiers.ADDRESS_SECURE, req.addr.qualifiers)

    def test_linear_io_addr_never_gets_mmio(self):
        # master's handle_random_addr only ever stamps ADDRESS_MMIO/ADDRESS_SECURE in its
        # PHYSICAL branch; a bare LINEAR draw with io=1 must not pick either qualifier up.
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="lin_io", type=RV.AddressType.LINEAR, size=0x1000, io=True))
        t = PageTableRequestBuilder(pool, _featmgr()).build()
        req = next(r for r in t.address_reqs if t.addr_names_by_id.get(r.request_id) == "lin_io")
        self.assertNotIn(RV.AddressQualifiers.ADDRESS_MMIO, req.addr.qualifiers)
        self.assertNotIn(RV.AddressQualifiers.ADDRESS_SECURE, req.addr.qualifiers)


class TestSecurePtProbabilityForwarded(unittest.TestCase):
    """``--secure_pt_probability`` reaches every declared space, gated on secure mode.

    A regression fence against the whole bug class: this knob was silently dropped from
    ``_space_env``, so every space took riemap's default 0 and no page-table node frame was
    ever drawn from secure memory -- the opposite of master, which rolled it in
    ``_create_pt_non_leaf``. The gate lives on this side because "am I in secure mode" is the
    consumer's state; riemap honors whatever probability it is handed.
    """

    def _spaces(self, **kw):
        pool = Pool()
        pool.add_parsed_page_mapping(_mapping("p", "p_pa"))
        fm = _featmgr(paging_g_mode=RV.RiscvPagingModes.SV39, env=RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED, **kw)
        t = PageTableRequestBuilder(pool, fm, RandNum(seed=1)).build()
        return t.spaces_by_name

    def test_forwarded_to_every_space_in_secure_mode(self):
        spaces = self._spaces(secure_mode=RV.RiscvSecureModes.SECURE, secure_pt_probability=100)
        # Both the VS map and the shared g-stage map, not just the first one.
        self.assertIn(GSTAGE_MAP_ID, spaces)
        for name, space in spaces.items():
            self.assertEqual(space.secure_pt_probability, 100, name)

    def test_zero_when_not_in_secure_mode(self):
        spaces = self._spaces(secure_mode=RV.RiscvSecureModes.NON_SECURE, secure_pt_probability=100)
        for name, space in spaces.items():
            self.assertEqual(space.secure_pt_probability, 0, name)


class TestPhysReservationPolicy(unittest.TestCase):
    """RiescueD's physical-reservation policy is handed to riemap as authoritative
    reservation bytes on the emitted page request -- the engine derives bit widths and
    alignment, but *how much* physical memory a big page occupies is consumer policy.
    """

    def _big_page(self, pagesize=RV.RiscvPageSizes.S1GB, phys_name="&random"):
        ppm = _mapping("big", phys_name)
        ppm.final_pagesize = pagesize
        ppm.address_mask = RV.RiscvPageSizes.address_mask(pagesize)
        ppm.phys_address_mask = RV.RiscvPageSizes.address_mask(pagesize)
        pool = Pool()
        pool.add_parsed_page_mapping(ppm)
        return pool

    def _req(self, fm, pool=None):
        pool = pool if pool is not None else self._big_page()
        b = PageTableRequestBuilder(pool, fm)
        b.build()
        return b.page_reqs_by_map[DEFAULT_MAP_ID][0]

    def test_single_stage_reserves_full_page_by_default(self):
        req = self._req(_featmgr())
        self.assertEqual(req.pa_reserve_size, RV.RiscvPageSizes.memory(RV.RiscvPageSizes.S1GB))
        self.assertEqual(req.va_reserve_size, RV.RiscvPageSizes.memory(RV.RiscvPageSizes.S1GB))

    def test_single_stage_flag_clamps_phys_to_4kb(self):
        # --reserve_partial_phys_memory clamps the physical reservation to one 4 KiB page;
        # the linear side is untouched, and the 1 GiB alignment is still handed to riemap.
        fm = _featmgr()
        fm.reserve_partial_phys_memory = True
        req = self._req(fm)
        self.assertEqual(req.pa_reserve_size, 0x1000)
        self.assertEqual(req.va_reserve_size, RV.RiscvPageSizes.memory(RV.RiscvPageSizes.S1GB))
        self.assertEqual(req.pa.and_mask, RV.RiscvPageSizes.address_mask(RV.RiscvPageSizes.S1GB))

    def test_gstage_random_reserves_one_leaf_window_by_default(self):
        # Under g-stage a &random big VS page's PA becomes a GPA fronted by g-stage-leaf
        # frames, so only one 4 KiB window is reserved regardless of the (1 GiB) VS
        # pagesize, allowing many sparse 1 GiB virtual mappings in a compact mmap.
        fm = _featmgr(paging_g_mode=RV.RiscvPagingModes.SV39, env=RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED)
        req = self._req(fm)
        self.assertEqual(req.pa_reserve_size, 0x1000)
        # The VS-pagesize alignment is still handed to riemap (engine mins it with the
        # g-stage leaf mask), so the GPA stays superpage-aligned.
        self.assertEqual(req.pa.and_mask, RV.RiscvPageSizes.address_mask(RV.RiscvPageSizes.S1GB))

    def test_gstage_random_flag_widens_phys_to_leaf_size(self):
        # Under g-stage the flag *widens* a &random page (opposite to single-stage): the
        # reservation grows to the g-stage leaf window (2 MiB here), not clamped to 4 KiB.
        ppm = _mapping("big", "&random")
        ppm.final_pagesize = RV.RiscvPageSizes.S1GB
        ppm.phys_address_mask = RV.RiscvPageSizes.address_mask(RV.RiscvPageSizes.S1GB)
        ppm.gstage_vs_leaf_final_pagesize = RV.RiscvPageSizes.S2MB
        pool = Pool()
        pool.add_parsed_page_mapping(ppm)
        fm = _featmgr(paging_g_mode=RV.RiscvPagingModes.SV39, env=RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED)
        fm.reserve_partial_phys_memory = True
        req = self._req(fm, pool)
        self.assertEqual(req.pa_reserve_size, RV.RiscvPageSizes.memory(RV.RiscvPageSizes.S2MB))

    def test_gstage_named_phys_keeps_full_span(self):
        # A named (non-&random) phys page under g-stage keeps its full pagesize span,
        # widened to the g-stage leaf window.
        fm = _featmgr(paging_g_mode=RV.RiscvPagingModes.SV39, env=RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED)
        req = self._req(fm, self._big_page(phys_name="big_pa"))
        self.assertEqual(req.pa_reserve_size, RV.RiscvPageSizes.memory(RV.RiscvPageSizes.S1GB))

    def test_gstage_named_phys_flag_clamps_to_4kb(self):
        fm = _featmgr(paging_g_mode=RV.RiscvPagingModes.SV39, env=RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED)
        fm.reserve_partial_phys_memory = True
        req = self._req(fm, self._big_page(phys_name="big_pa"))
        self.assertEqual(req.pa_reserve_size, 0x1000)


class TestPerLevelForcingForwarded(unittest.TestCase):
    """generator.randomize_pt_attrs expands a scenario's single-/VS-stage {base}/{base}_nonleaf
    forcing into concrete ``{base}_level{n}`` keys on the ParsedPageMapping. _attrs must forward
    them to the builder, or the forced PTE (e.g. an invalid VS non-leaf) is silently dropped and
    the walk wrongly succeeds -- hypervisor_paging_faults_vs SID_HPBVMS_018_nonleaf."""

    def test_attrs_forwards_vs_per_level_keys(self):
        pool = Pool()
        b = PageTableRequestBuilder(pool, _featmgr())
        ppm = _mapping("m", "m_phys")
        ppm.v_level1 = False  # forced invalid VS non-leaf PTE at level 1
        ppm.x_level0 = False  # forced non-executable leaf
        attrs = b._attrs(ppm)
        self.assertEqual(attrs.get("v_level1"), False, "VS per-level v_level1 forcing not forwarded")
        self.assertEqual(attrs.get("x_level0"), False, "VS per-level x_level0 forcing not forwarded")

    def test_an_explicit_level_force_beats_the_bare_base_at_the_leaf(self):
        """RiescueD's leaf precedence is FORCE-WINS, and must stay that way.

        ``ParsedPageMapping`` always emits both a bare base and a full ``{base}_level{n}`` set,
        so on this path the concrete key is the intent and the bare base is only a default --
        ``resolve.pt_node_levels_with_leaf`` folds a bare base onto the leaf *only* where no
        explicit force sits there. The JSON frontend's vocabulary is the opposite (a bare base
        is the user's leaf intent, a ``_level`` key the exception), which is why base-wins is
        restored there by a frontend pre-pass rather than by changing the shared resolver.

        This is the tripwire against someone later "unifying" the two rules: with base-wins
        applied here, ``v=1`` (the ppm default) would overwrite a scenario's ``v_level0 = 0``
        and hypervisor_tlb_fence SID_HFTLB_07 would silently stop faulting."""
        pool = Pool()
        ppm = _mapping("m", "m_phys")
        ppm.v_level0 = False  # forced invalid LEAF PTE; ppm.v stays at its default of True
        pool.add_parsed_page_mapping(ppm)
        b = PageTableRequestBuilder(pool, _featmgr(), RandNum(seed=1))
        b.build()
        req = b.page_reqs_by_map[DEFAULT_MAP_ID][0]
        self.assertEqual(req.attrs["v"], True, "the bare base default must still be forwarded")
        pt_nodes = _vs_gstage_pt_nodes(req)
        self.assertEqual(pt_nodes[LEAF].attrs["v"], False, "a bare base overwrote an explicit v_level0=False force at the leaf")

    def test_attrs_forwards_a_zero_valued_base_bit(self):
        # _attrs skips only None (unset) fields, never falsy ones -- a base bit forced to 0
        # is a real force. ``n`` is the sharp case: on a 64 KiB page the builder auto-sets
        # NAPOT unless it sees n=0, so dropping the 0 here silently turns NAPOT back on
        # (the same bug builder_test.TestNapot64KB guards on the engine side).
        pool = Pool()
        b = PageTableRequestBuilder(pool, _featmgr())
        ppm = _mapping("m", "m_phys")
        ppm.n = 0
        ppm.w = False
        attrs = b._attrs(ppm)
        self.assertEqual(attrs.get("n"), 0, "base n=0 dropped -- a 64 KiB page would silently stay NAPOT")
        self.assertEqual(attrs.get("w"), False, "base w=False dropped")


class TestSvaduAdRandomization(unittest.TestCase):
    """``--svadu`` A/D randomization is RiescueD policy, rolled once per page in ``_attrs``.

    ``_attrs`` runs once per page, and a 64 KiB page
    builds 16 ``PTAttrs`` objects whose packed values ``_pack_leaf`` compares, so rolling any
    lower down would produce malformed NAPOT blocks.
    """

    def _attrs_over_pages(self, n=24, **fm_kw):
        pool = Pool()
        for i in range(n):
            pool.add_parsed_page_mapping(_mapping(f"p{i}", f"p{i}_pa"))
        fm = _featmgr()
        for key, val in fm_kw.items():
            setattr(fm, key, val)
        b = PageTableRequestBuilder(pool, fm, RandNum(seed=1))
        b.build()
        return [req.attrs for req in b.page_reqs_by_map[DEFAULT_MAP_ID]]

    def test_svadu_on_randomizes_a_and_d(self):
        attrs = self._attrs_over_pages(svadu=True)
        self.assertEqual({a["a"] for a in attrs}, {0, 1}, "svadu A is constant across 24 pages")
        self.assertEqual({a["d"] for a in attrs}, {0, 1}, "svadu D is constant across 24 pages")

    def test_the_roll_reaches_the_leaf_pt_node(self):
        # The value must reach the leaf node's attrs, which the builder writes
        # to a_level{leaf}.
        attrs = self._attrs_over_pages(svadu=True)
        leaf_a = {resolve.pt_node_levels_with_leaf(a, 0)[0]["a"] for a in attrs}
        self.assertEqual(leaf_a, {0, 1}, "the rolled A never reaches the leaf page-table node")

    def test_svadu_off_declares_neither_bit(self):
        for a in self._attrs_over_pages(svadu=False):
            self.assertNotIn("a", a)
            self.assertNotIn("d", a)

    def test_an_explicit_force_wins(self):
        pool = Pool()
        ppm = _mapping("m", "m_phys")
        ppm.a = True
        ppm.d = False
        fm = _featmgr()
        fm.svadu = True
        b = PageTableRequestBuilder(pool, fm, RandNum(seed=1))
        # Roll many times over the same forced mapping: no seed may move a declared bit.
        for _ in range(16):
            attrs = b._attrs(ppm)
            self.assertEqual(attrs["a"], True)
            self.assertEqual(attrs["d"], 0)


class TestPbmtNcio(unittest.TestCase):
    """``--pbmt_ncio``: an NC(1)/IO(2) memory type on every mapped leaf, and on the g-stage
    leaf translating that page's own GPA.

    RiescueD declares PBMT policy in a form the builder's attribute-aware
    coloring can satisfy.
    """

    def _reqs(self, n=24, pbmt_ncio=True, **fm_kw):
        pool = Pool()
        for i in range(n):
            pool.add_parsed_page_mapping(_mapping(f"p{i}", f"p{i}_pa"))
        fm = _featmgr(**fm_kw)
        fm.pbmt_ncio = pbmt_ncio
        b = PageTableRequestBuilder(pool, fm, RandNum(seed=1))
        b.build()
        return b

    def _two_stage(self, **kw):
        return self._reqs(paging_g_mode=RV.RiscvPagingModes.SV39, env=RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED, **kw)

    def test_every_page_declares_a_bare_nc_or_io(self):
        for req in self._reqs().page_reqs_by_map[DEFAULT_MAP_ID]:
            choice = req.attrs.get("pbmt")
            self.assertIsInstance(choice, Choice, req.page_id)
            assert isinstance(choice, Choice)
            self.assertEqual(set(choice.options), {1, 2}, req.page_id)

    def test_the_bare_roll_also_lands_on_the_concrete_leaf_key(self):
        # ParsedPageMapping declares pbmt_level0..4 with a DEFAULT of 0, _attrs forwards every
        # non-None field, and pt_node_levels_with_leaf lets an explicit {base}_level{leaf} beat
        # a bare base -- so writing only the bare key left the forwarded 0 winning and the roll
        # never reached a PTE at all. Both must be written, and agree.
        for req in self._reqs().page_reqs_by_map[DEFAULT_MAP_ID]:
            self.assertEqual(req.attrs["pbmt_level0"], req.attrs["pbmt"], req.page_id)
            self.assertEqual(resolve.pt_node_levels_with_leaf(req.attrs, 0)[0]["pbmt"], req.attrs["pbmt"])

    def test_pages_have_per_page_preferred_memory_types(self):
        preferred = {v.preferred for req in self._reqs().page_reqs_by_map[DEFAULT_MAP_ID] if isinstance((v := req.attrs["pbmt"]), Choice)}
        self.assertEqual(preferred, {1, 2}, f"per-page NC/IO variety was lost: {preferred}")

    def test_nothing_is_nc_or_io_when_off(self):
        # ``pbmt`` carries a declared default of 0 (ordinary memory) either way; what must not
        # appear with the knob off is a nonzero memory type, anywhere.
        for req in self._two_stage(pbmt_ncio=False).page_reqs_by_map[DEFAULT_MAP_ID]:
            hot = {k: v for k, v in req.attrs.items() if k.split("_")[0] == "pbmt" and v}
            self.assertFalse(hot, f"{req.page_id} declares a memory type with --pbmt_ncio off: {hot}")

    def test_gstage_keys_cover_data_and_pt_node_frame_leaves(self):
        for req in self._two_stage().page_reqs_by_map[DEFAULT_MAP_ID]:
            vs_leaf = RV.RiscvPageSizes.pt_leaf_level(req.pagesize)
            data_g_leaf = RV.RiscvPageSizes.pt_leaf_level(req.gstage_vs_leaf_size or RV.RiscvPageSizes.S4KB)
            frame_g_leaf = RV.RiscvPageSizes.pt_leaf_level(req.gstage_vs_nonleaf_size or RV.RiscvPageSizes.S4KB)
            hot = {k for k, v in req.attrs.items() if k.startswith("pbmt_level") and "_glevel" in k and isinstance(v, Choice)}
            expected = {f"pbmt_level{vs_leaf}_glevel{data_g_leaf}"}
            expected.update(f"pbmt_level{vs_level}_glevel{frame_g_leaf}" for vs_level in range(vs_leaf + 1, RV.RiscvPagingModes.max_levels(RV.RiscvPagingModes.SV39)))
            self.assertEqual(hot, expected, req.page_id)

    def test_frame_identity_choices_reach_ptgpages(self):
        for req in self._two_stage().page_reqs_by_map[DEFAULT_MAP_ID]:
            hot = {k: v for k, v in req.gstage_forced_attrs.items() if k.startswith("pbmt") and isinstance(v, Choice)}
            self.assertTrue(hot, f"{req.page_id} did not type its PT-node frame identities")
            self.assertTrue(all(set(v.options) == {1, 2} for v in hot.values() if isinstance(v, Choice)))

    def test_a_test_named_force_wins(self):
        pool = Pool()
        ppm = _mapping("m", "m_phys")
        ppm.pbmt_leaf_gleaf = 3  # not a real encoding; a sentinel the roll can never produce
        pool.add_parsed_page_mapping(ppm)
        fm = _featmgr(paging_g_mode=RV.RiscvPagingModes.SV39, env=RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED)
        fm.pbmt_ncio = True
        b = PageTableRequestBuilder(pool, fm, RandNum(seed=1))
        b.build()
        req = b.page_reqs_by_map[DEFAULT_MAP_ID][0]
        self.assertEqual(req.attrs["pbmt_level0_glevel0"], 3, "a named pbmt_leaf_gleaf force was overwritten by the roll")

    def test_a_section_leaf_stays_ordinary_memory(self):
        # Sections never reach _attrs. Their own leaves remain ordinary memory
        # because an instruction fetch from an IO-typed page does not survive
        # the ISS; the fronting g-stage leaf still receives PBMT policy.
        pool = Pool()
        pool.section_specs.append(SectionSpec(name="code", phys_name="code_pa", size=0x1000, phys=_Placement(), lin=_Placement(), iscode=True))
        fm = _featmgr(paging_g_mode=RV.RiscvPagingModes.SV39, env=RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED)
        fm.pbmt_ncio = True
        b = PageTableRequestBuilder(pool, fm, RandNum(seed=1))
        b.build()
        req = next(r for r in b.page_reqs_by_map[DEFAULT_MAP_ID] if r.page_id.startswith("sec::code"))
        self.assertIsNone(req.attrs.get("pbmt"), "a section's own leaf must stay ordinary memory")
        choice = req.attrs.get("pbmt_level0_glevel0")
        self.assertIsInstance(choice, Choice, "a section's g-stage leaf must still get the memory type")
        assert isinstance(choice, Choice)
        self.assertEqual(set(choice.options), {1, 2})


class TestModifyPtNodeFrames(unittest.TestCase):
    """``modify_pt`` / ``modify_nonleaf_pt`` PT-node frame recipes.

    A ``{base}_nonleaf_g*`` force names a VS PT-node FRAME by the level of the pointer PTE
    that targets it (``vs == level + 1``), so on a 4 KiB page ``v_nonleaf_gleaf=0`` names the
    frame holding the LEAF VS-stage PTE. That frame must therefore be OWNED (pinned) by the
    page -- invalidating a shared frame's g-stage translation would fault unrelated pages --
    and, being a real declared :class:`Page`, it must carry the forced g-stage bits on its own
    GPA -> HPA mapping: RieMap synthesizes no identity leaf for a GPA a consumer already
    translates, and the pinned frame occupies the single ``PTNode.page`` slot the VS node's
    PTGPage would otherwise use. hypervisor_paging_faults_vs SID_HPBVMS_018_implicit_trap."""

    def _recipe(self, modify_nonleaf=True, modify_pt=False, attrs=None, pagesize=RV.RiscvPageSizes.S4KB):
        """``_modify_pt_nodes`` for one two-stage page: (frames, pages, mappings, pt_nodes)."""
        req = _PageReq(
            page_id="p",
            pagesize=pagesize,
            attrs={"v": 1, "r": 1, "w": 1, "a": 1, "d": 1, **({"modify_pt": 1} if modify_pt else {}), **(attrs or {})},
            gstage_forced_attrs=_glevel_keys(attrs),
            gstage_vs_leaf_size=RV.RiscvPageSizes.S4KB,
            gstage_vs_nonleaf_size=RV.RiscvPageSizes.S4KB,
            modify_nonleaf_pt=modify_nonleaf,
        )
        pages: dict = {}
        mappings: list = []
        frames = _modify_pt_nodes(req, DEFAULT_MAP_ID, RV.RiscvPagingModes.SV39, True, False, pages, mappings, [], {req.page_id: req})
        return frames, pages, mappings, _vs_gstage_pt_nodes(req, frames)

    def test_modify_nonleaf_owns_the_leaf_pte_frame(self):
        frames, _pages, _mappings, _pt_nodes = self._recipe()
        # SV39 4KB: every walk level leaf(0)..root(2) owns its frame; LEAF is the frame that
        # holds the leaf VS-stage PTE -- the one a *_nonleaf_g* force invalidates.
        self.assertEqual(set(frames), {LEAF, 1, 2}, "modify_nonleaf_pt must own the leaf-PTE frame too")

    def test_modify_pt_declares_every_walk_node_frame(self):
        frames, _pages, _mappings, _pt_nodes = self._recipe(
            modify_nonleaf=False,
            modify_pt=True,
        )
        self.assertEqual(
            set(frames),
            {
                LEAF,
                1,
                2,
            },
            "modify_pt must retain handles for every node from leaf through root",
        )

    def test_gstage_force_rides_the_frames_own_mapping(self):
        frames, pages, mappings, pt_nodes = self._recipe(attrs={"v_level1_glevel0": 0})
        frame_id = frames[LEAF]
        frame_maps = [m for m in mappings if m.src_id == frame_id]
        self.assertEqual(len(frame_maps), 1, "the forced frame must declare its own GPA -> HPA mapping")
        m = frame_maps[0]
        self.assertIn(m.dst_id, pages, "the frame's HPA page must be declared")
        self.assertIsInstance(pages[m.dst_id].addr.relation, RecipeSameAs)
        leaf = m.pt_nodes[LEAF]
        self.assertEqual(leaf.attrs.get("v"), 0, "forced g-stage v=0 must reach the frame's g-stage leaf PTE")
        # The identity defaults survive alongside the force, else the frame is unreachable for
        # reasons other than the one the test forces.
        for base in ("r", "w", "x", "a", "d"):
            self.assertEqual(leaf.attrs.get(base), 1, f"frame g-stage leaf {base} default lost")

    def test_superseded_ptgpage_is_dropped_but_vs_bits_kept(self):
        # The PTGPage at VS key 1 describes the frame now pinned at key LEAF; keeping it would
        # collide with that frame in PTNode.page. Its level's VS PTE bits must survive.
        _frames, _pages, _mappings, pt_nodes = self._recipe(attrs={"v_level1_glevel0": 0, "v_level1": 1})
        self.assertIsNone(pt_nodes[1].page, "a superseded PTGPage must be dropped")
        self.assertEqual(pt_nodes[1].attrs.get("v"), 1, "the VS node's own PTE bits must survive")

    def test_unpinned_page_keeps_its_ptgpage(self):
        # No modify flag -> no frames -> the force still rides the VS node's PTGPage, which is
        # how RieMap forces a *synthesized* (shared) g-stage identity node.
        req = _PageReq(
            page_id="p",
            pagesize=RV.RiscvPageSizes.S4KB,
            attrs={"v": 1, "r": 1, "w": 1, "a": 1, "d": 1, "v_level1_glevel0": 0},
            gstage_forced_attrs={"v_level1_glevel0": 0},
            gstage_vs_leaf_size=RV.RiscvPageSizes.S4KB,
            gstage_vs_nonleaf_size=RV.RiscvPageSizes.S4KB,
        )
        pt_nodes = _vs_gstage_pt_nodes(req, {})
        self.assertIsInstance(pt_nodes[0].page, PTGPage)
        assert isinstance(pt_nodes[0].page, PTGPage)
        self.assertEqual(pt_nodes[0].page.level_attrs(), {0: {"v": 0}})

    def test_root_frame_never_carries_a_gstage_force(self):
        # The root frame is shared by every page in the space (one satp/vsatp), so it must
        # never take a per-page g-stage force -- even a key that names it (the resolver caps
        # vs at the root level, so this one is synthetic).
        frames, _pages, mappings, _pt_nodes = self._recipe(attrs={"v_level1_glevel0": 0, "v_level3_glevel0": 0})
        self.assertEqual(frames[2], f"__ptroot::{DEFAULT_MAP_ID}")
        root_maps = [mapping for mapping in mappings if mapping.src_id == frames[2]]
        self.assertEqual(
            len(root_maps),
            1,
            "the frontend must explicitly declare the VS root identity",
        )
        self.assertEqual(root_maps[0].pt_nodes[LEAF].attrs["v"], 1)


class TestUnpinnedGstageForceIsColorable(unittest.TestCase):
    """An UNPINNED page's g-stage force rides a synthesized :class:`PTGPage`, and the frame
    that PTGPage describes is shared by every page sharing the VS node above it -- with only
    the first walk to reach it emitting its identity. So coloring, not a pin, is what keeps
    the force intact, and coloring can only see what ``builder._ptgpage_sig`` signs.

    That makes the translator's PTGPage shape and the signature one contract: these pin the
    RiescueD half of it. hypervisor_tlb_fence SID_HFTLB_78 declares exactly this shape
    (``a_nonleaf_gleaf=0``, no modify flag) and read A=1 back when the signature ignored it.
    The built-tree half is tests/riemap/coloring_test.TestGstageIdentitySignsApart."""

    _SV39_LEVELS = 3

    def _pt_nodes(self, gstage_attrs):
        req = _PageReq(
            page_id="p",
            pagesize=RV.RiscvPageSizes.S4KB,
            attrs={"v": 1, "r": 1, "w": 1, "a": 1, "d": 1, **gstage_attrs},
            gstage_forced_attrs=_glevel_keys(gstage_attrs),
            gstage_vs_leaf_size=RV.RiscvPageSizes.S4KB,
            gstage_vs_nonleaf_size=RV.RiscvPageSizes.S4KB,
        )
        # max_levels is what makes the translator attach the geometry-only PTGPages the plain
        # case relies on, so pass the real SV39 count rather than the 0 default.
        return _vs_gstage_pt_nodes(req, {}, self._SV39_LEVELS)

    def _sig(self, gstage_attrs, level):
        return _ptgpage_sig(self._pt_nodes(gstage_attrs)[level].page)

    def test_forced_node_signs_non_empty(self):
        # a_nonleaf_gleaf=0 resolves to a_level1_glevel0=0: the VS level-1 node's PTGPage,
        # describing the frame that holds level-1 VS PTEs.
        sig = self._sig({"a_level1_glevel0": 0}, 0)
        self.assertIn(("g", 0, "a", ("exact", ("int", 0))), sig, "the forced g-stage bit must reach the coloring signature")

    def test_plain_geometry_node_signs_its_exact_pagesize(self):
        for level in range(0, self._SV39_LEVELS - 1):
            self.assertTrue(self._sig({}, level), f"the geometry-only PTGPage at level {level} lost its exact pagesize")

    def test_forced_and_plain_nodes_sign_differently(self):
        self.assertNotEqual(self._sig({"a_level1_glevel0": 0}, 0), self._sig({}, 0))

    def test_identical_forces_sign_the_same(self):
        self.assertEqual(self._sig({"a_level1_glevel0": 0}, 0), self._sig({"a_level1_glevel0": 0}, 0))

    def test_force_only_signs_the_level_it_names(self):
        # The force names one frame; signing it at other levels would split subtrees that
        # have no reason to separate.
        self.assertFalse(any(part[:3] == ("g", 0, "a") for part in self._sig({"a_level1_glevel0": 0}, 1)))


class TestModifyNonleafOwnsItsGstagePointer(unittest.TestCase):
    """Runtime g-stage PTE rewrites own one level above the chosen geometry (master bump)."""

    # Sv39/Sv48/Sv57: next level after 4 KiB is 2 MiB; after 2 MiB is 1 GiB.
    NEXT_AFTER_4KB = 0x200000
    NEXT_AFTER_2MB = 0x40000000

    def _frame_spec(self, nonleaf_ps, g_mode=RV.RiscvPagingModes.SV39, modify_nonleaf=True, twostage=True):
        req = _PageReq(
            page_id="p",
            pagesize=RV.RiscvPageSizes.S4KB,
            attrs={"v": 1, "r": 1, "w": 1, "a": 1, "d": 1},
            gstage_vs_leaf_size=RV.RiscvPageSizes.S4KB,
            gstage_vs_nonleaf_size=nonleaf_ps,
            modify_nonleaf_pt=modify_nonleaf,
        )
        pages: dict = {}
        frames = _modify_pt_nodes(req, DEFAULT_MAP_ID, RV.RiscvPagingModes.SV39, twostage, False, pages, [], [], {req.page_id: req}, g_mode)
        return frames, pages

    def test_leaf_pte_frame_gets_next_level_granule_not_large_backing(self):
        for frame_size, granule in (
            (RV.RiscvPageSizes.S4KB, self.NEXT_AFTER_4KB),
            (RV.RiscvPageSizes.S2MB, self.NEXT_AFTER_2MB),
        ):
            with self.subTest(frame_size=frame_size):
                frames, pages = self._frame_spec(frame_size)
                frame = pages[frames[LEAF]]
                self.assertIsNone(frame.reserve_size)
                self.assertEqual(frame.reserve_granule, granule)
                self.assertIsNone(frame.addr.and_mask)
                self.assertEqual(frame.pagesize, frame_size)

    def test_only_the_leaf_pte_frame_takes_the_span(self):
        # The *_nonleaf_g* forcing names only the frame holding the LEAF VS-stage PTE; growing
        # every level would demand a 1 GiB reservation per non-leaf node.
        frames, pages = self._frame_spec(RV.RiscvPageSizes.S2MB)
        higher = pages[frames[1]]
        self.assertIsNone(
            higher.reserve_granule,
            "only the frame holding the VS leaf PTE gets g-stage exclusivity",
        )

    def test_no_span_without_the_flag(self):
        frames, pages = self._frame_spec(RV.RiscvPageSizes.S2MB, modify_nonleaf=False)
        self.assertEqual(frames, {}, "no modify flag declares no frames at all")

    def test_no_span_single_stage(self):
        # Single-stage has no g-stage pointer to own.
        frames, pages = self._frame_spec(RV.RiscvPageSizes.S4KB, g_mode=RV.RiscvPagingModes.DISABLE, twostage=False)
        self.assertIsNone(pages[frames[LEAF]].reserve_granule)

    def test_modify_leaf_pt_owns_the_pointer_above_its_own_gstage_leaf(self):
        # Next-level granule belongs to the final GPA only; HPA backing stays 4 KiB.
        req = _PageReq(
            page_id="p",
            pagesize=RV.RiscvPageSizes.S4KB,
            attrs={"v": 1, "r": 1, "w": 1, "a": 1, "d": 1},
            pa=RecipeAddrSpec(and_mask=RV.RiscvPageSizes.address_mask(RV.RiscvPageSizes.S4KB)),
            gstage_vs_leaf_size=RV.RiscvPageSizes.S2MB,
            gstage_vs_nonleaf_size=RV.RiscvPageSizes.S4KB,
            pa_reserve_size=0x1000,
            modify_leaf_pt=True,
        )
        pages: dict = {}
        _emit_vs_two_stage(req, DEFAULT_MAP_ID, RV.RiscvPagingModes.SV39, RV.RiscvPagingModes.SV39, {"p"}, {"p": req}, {"p": RV.RiscvPagingModes.SV39}, True, req.va, req.pa, pages, [], False, [])
        hpa = next(spec for pid, spec in pages.items() if pid.endswith("__dst"))
        gpa = next(spec for pid, spec in pages.items() if pid.endswith("__gpa"))
        self.assertEqual(hpa.reserve_size, 0x1000)
        self.assertIsNone(hpa.reserve_granule)
        self.assertEqual(gpa.reserve_size, 0x1000)
        self.assertEqual(gpa.reserve_granule, self.NEXT_AFTER_2MB)

    def test_gpa_granule_tracks_gstage_leaf_size(self):
        for leaf_size, granule in (
            (RV.RiscvPageSizes.S4KB, self.NEXT_AFTER_4KB),
            (RV.RiscvPageSizes.S2MB, self.NEXT_AFTER_2MB),
        ):
            with self.subTest(leaf_size=leaf_size):
                _hpa, gpa = self._emit_leaf_pt(RecipeAddrSpec(), gleaf=leaf_size)
                self.assertEqual(gpa.reserve_granule, granule)

    def test_sv57_4kb_leaf_gets_2mb_granule_not_root(self):
        # Identity GPA==HPA under Sv57 cannot afford a 256 TiB granule per modify_leaf_pt page.
        req = _PageReq(
            page_id="p",
            pagesize=RV.RiscvPageSizes.S4KB,
            attrs={"v": 1, "r": 1, "w": 1, "a": 1, "d": 1},
            pa=RecipeAddrSpec(),
            gstage_vs_leaf_size=RV.RiscvPageSizes.S4KB,
            gstage_vs_nonleaf_size=RV.RiscvPageSizes.S4KB,
            pa_reserve_size=0x1000,
            modify_leaf_pt=True,
        )
        pages: dict = {}
        _emit_vs_two_stage(
            req,
            DEFAULT_MAP_ID,
            RV.RiscvPagingModes.SV39,
            RV.RiscvPagingModes.SV57,
            {"p"},
            {"p": req},
            {"p": RV.RiscvPagingModes.SV39},
            True,
            req.va,
            req.pa,
            pages,
            [],
            False,
            [],
        )
        gpa = next(spec for pid, spec in pages.items() if pid.endswith("__gpa"))
        self.assertEqual(gpa.reserve_granule, self.NEXT_AFTER_4KB)
        self.assertNotEqual(gpa.reserve_granule, 1 << 48)

    def _emit_leaf_pt(self, pa_spec: RecipeAddrSpec, gleaf=RV.RiscvPageSizes.S2MB, g_mode=RV.RiscvPagingModes.SV39):
        req = _PageReq(
            page_id="p",
            pagesize=RV.RiscvPageSizes.S4KB,
            attrs={"v": 1, "r": 1, "w": 1, "a": 1, "d": 1},
            pa=pa_spec,
            gstage_vs_leaf_size=gleaf,
            gstage_vs_nonleaf_size=RV.RiscvPageSizes.S4KB,
            pa_reserve_size=0x1000,
            modify_leaf_pt=True,
        )
        pages: dict = {}
        _emit_vs_two_stage(req, DEFAULT_MAP_ID, RV.RiscvPagingModes.SV39, g_mode, {"p"}, {"p": req}, {"p": RV.RiscvPagingModes.SV39}, True, req.va, req.pa, pages, [], False, [])
        hpa = next(spec for pid, spec in pages.items() if pid.endswith("__dst"))
        gpa = next(spec for pid, spec in pages.items() if pid.endswith("__gpa"))
        return hpa, gpa

    def test_an_exact_pa_keeps_its_address(self):
        hpa, gpa = self._emit_leaf_pt(RecipeAddrSpec(exact=0x80001000))
        self.assertEqual(hpa.addr.exact, 0x80001000)
        self.assertIsNone(hpa.addr.and_mask, "an exact PA must not acquire an alignment mask")
        self.assertIsNone(hpa.reserve_granule)
        self.assertEqual(gpa.reserve_granule, self.NEXT_AFTER_2MB)

    def test_an_in_region_pa_keeps_its_region(self):
        region = MemoryRegion(size=0x200000)
        hpa, gpa = self._emit_leaf_pt(RecipeAddrSpec(region=region))
        self.assertIs(hpa.addr.region, region)
        self.assertIsNone(hpa.addr.and_mask, "an in-region PA is placed by the region, not by a mask")
        self.assertEqual(gpa.reserve_granule, self.NEXT_AFTER_2MB)

    def test_a_relational_pa_keeps_its_relation(self):
        hpa, gpa = self._emit_leaf_pt(RecipeAddrSpec(relation=RecipeSameAs("other")))
        self.assertIsInstance(hpa.addr.relation, RecipeSameAs)
        self.assertIsNone(hpa.addr.and_mask, "a relational PA derives its address, so no mask applies")
        self.assertEqual(gpa.reserve_granule, self.NEXT_AFTER_2MB)

    def test_root_entry_spans_for_all_modes(self):
        self.assertEqual(_root_entry_span(RV.RiscvPagingModes.SV39), 1 << 30)
        self.assertEqual(_root_entry_span(RV.RiscvPagingModes.SV48), 1 << 39)
        self.assertEqual(_root_entry_span(RV.RiscvPagingModes.SV57), 1 << 48)

    def test_next_level_spans(self):
        self.assertEqual(_next_level_span(RV.RiscvPagingModes.SV39, RV.RiscvPageSizes.S4KB), 0x200000)
        self.assertEqual(_next_level_span(RV.RiscvPagingModes.SV39, RV.RiscvPageSizes.S2MB), 0x40000000)
        self.assertEqual(_next_level_span(RV.RiscvPagingModes.SV57, RV.RiscvPageSizes.S4KB), 0x200000)
        self.assertIsNone(_next_level_span(RV.RiscvPagingModes.DISABLE, RV.RiscvPageSizes.S4KB))


class TestOffsetFamilySharesPtNodeFrames(unittest.TestCase):
    """An offset family's ``modify_pt`` PT-node frames belong to the FAMILY, not to each page.

    A ``;#page_mapping(lin_name=anchor+0xN000, ...)`` child has its VA forced to
    ``anchor + delta``, so it indexes the same page-table nodes as its anchor at every level the
    delta does not move. One pointer PTE cannot name two frames, so per-page frames are
    unsatisfiable: hypervisor_tlb_fence's SVNAPOT scenario declares 16 pages 0x1000 apart, each
    with ``modify_pt=1``, and the tree build reported ``page-table non-leaf slot conflict at
    index 0x1f6 ... existing (base 0xf77ab000) vs new (base 0x2cef1f000)`` on every paging-mode
    combination. Keying the frames by the family root makes the members pin identical bases,
    which the walker accepts as idempotent."""

    MODE = RV.RiscvPagingModes.SV39

    def _req(self, page_id, delta=None, nonleaf=RV.RiscvPageSizes.S4KB):
        va = RecipeAddrSpec() if delta is None else RecipeAddrSpec(relation=RecipeOffsetFrom("anchor", delta))
        return _PageReq(
            page_id=page_id,
            pagesize=RV.RiscvPageSizes.S4KB,
            attrs={"v": 1, "r": 1, "w": 1, "a": 1, "d": 1, "modify_pt": 1},
            va=va,
            gstage_vs_leaf_size=RV.RiscvPageSizes.S4KB,
            gstage_vs_nonleaf_size=nonleaf,
        )

    def _emit(self, reqs, twostage=True):
        """Run ``_modify_pt_nodes`` for every req into one shared recipe, in the order given."""
        reqs_by_id = {r.page_id: r for r in reqs}
        pages: dict = {}
        mappings: list = []
        windows: list = []
        frames = {r.page_id: _modify_pt_nodes(r, DEFAULT_MAP_ID, self.MODE, twostage, False, pages, mappings, windows, reqs_by_id) for r in reqs}
        return frames, pages, mappings, windows

    def test_children_share_the_anchors_frames(self):
        reqs = [self._req("anchor"), *(self._req(f"anchor+0x{i:x}000", delta=i * 0x1000) for i in range(1, 16))]
        frames, _pages, _mappings, _windows = self._emit(reqs)
        anchor = frames["anchor"]
        for r in reqs[1:]:
            self.assertEqual(frames[r.page_id], anchor, f"{r.page_id} must pin the family's frames, not its own")

    def test_only_the_family_root_names_the_frames(self):
        # The recipe must not contain a per-child frame page at all -- an unused one would still
        # draw an address and reserve memory.
        reqs = [self._req("anchor"), self._req("anchor+0x1000", delta=0x1000)]
        _frames, pages, _mappings, _windows = self._emit(reqs)
        strays = [pid for pid in pages if "__ptframe" in pid and not pid.startswith("anchor__ptframe")]
        self.assertEqual(strays, [], "a child must declare no PT-node frame of its own")

    def test_frame_geometry_comes_from_the_anchor_not_emission_order(self):
        # The shared frame has one g-stage pagesize, so it must be the anchor's even when a child
        # (whose own pick_pagesize draw differs) is emitted first.
        child = self._req("anchor+0x1000", delta=0x1000, nonleaf=RV.RiscvPageSizes.S4KB)
        anchor = self._req("anchor", nonleaf=RV.RiscvPageSizes.S2MB)
        frames, pages, _mappings, _windows = self._emit([child, anchor])
        self.assertEqual(pages[frames["anchor"][LEAF]].pagesize, RV.RiscvPageSizes.S2MB, "child emitted first must not decide the shared frame's geometry")

    def test_gstage_force_on_the_shared_frame_comes_from_the_anchor(self):
        # Only the anchor carries the *_g* forcing attrs, so the frame's own GPA -> HPA mapping
        # must be built from the anchor's attrs regardless of which member created the frame.
        child = self._req("anchor+0x1000", delta=0x1000)
        anchor = self._req("anchor")
        anchor.attrs["v_level1_glevel0"] = 0
        frames, _pages, mappings, _windows = self._emit([child, anchor])
        frame_id = frames["anchor"][LEAF]
        frame_maps = [m for m in mappings if m.src_id == frame_id]
        self.assertEqual(len(frame_maps), 1, "the shared frame must declare exactly one GPA -> HPA mapping")
        self.assertEqual(frame_maps[0].pt_nodes[LEAF].attrs.get("v"), 0, "the anchor's g-stage force must reach the shared frame")

    def test_only_the_anchor_declares_readback_windows(self):
        # A child's read-back symbol would be ``anchor+0xN000__pt_level{N}`` -- a '+' is illegal
        # in an assembler label -- and no directive ever names a child (the runtime reaches a
        # child's PTE from the anchor's VA plus napot_offset).
        reqs = [self._req("anchor"), self._req("anchor+0x1000", delta=0x1000)]
        _frames, _pages, _mappings, windows = self._emit(reqs)
        self.assertEqual({src for src, _lvl, _win, _frame in windows}, {"anchor"})

    def test_unrelated_pages_still_own_separate_frames(self):
        # Only an OffsetFrom family shares. Two independently-drawn modify_pt pages are separate
        # owners and must keep separate frames (coloring keeps their VAs in different nodes).
        frames, _pages, _mappings, _windows = self._emit([self._req("anchor"), self._req("other")])
        self.assertNotEqual(frames["anchor"][LEAF], frames["other"][LEAF])

    def test_single_stage_family_shares_too(self):
        # The conflict is a VS/single-stage pointer-PTE conflict; it is not g-stage specific.
        reqs = [self._req("anchor"), self._req("anchor+0x1000", delta=0x1000)]
        frames, _pages, _mappings, _windows = self._emit(reqs, twostage=False)
        self.assertEqual(frames["anchor+0x1000"], frames["anchor"])

    def test_addr_offset_family_shares_pt_frames(self):
        # Production shape after unify: SameAs(addr::) base + OffsetFrom(addr::) child.
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="v4kb", type=RV.AddressType.LINEAR, size=0x2000, and_mask=0xFFFFFFFFFFFFE000))
        pool.add_parsed_addr(ParsedRandomAddress(name="v4kb_p", type=RV.AddressType.PHYSICAL, size=0x2000, and_mask=0xFFFFFFFFFFFFE000))
        base = _mapping("v4kb", "v4kb_p", modify_pt=True)
        child = _mapping("v4kb+0x1000", "v4kb_p+0x1000", modify_pt=True)
        child.lin_addr_link = ("v4kb", 0x1000)
        child.phys_addr_link = ("v4kb_p", 0x1000)
        pool.add_parsed_page_mapping(base)
        pool.add_parsed_page_mapping(child)

        b = PageTableRequestBuilder(pool, _featmgr())
        b.build()
        reqs = list(b.page_reqs_by_map[DEFAULT_MAP_ID])
        reqs_by_id = {r.page_id: r for r in reqs}
        by_lin = {b.names_by_id[r.page_id][0]: r for r in reqs}
        base_id = by_lin["v4kb"].page_id
        child_id = by_lin["v4kb+0x1000"].page_id
        self.assertEqual(_va_family_root(child_id, reqs_by_id), base_id)
        self.assertEqual(_va_family_root(base_id, reqs_by_id), base_id)

        frames, pages, _mappings, _windows = self._emit(reqs)
        self.assertEqual(frames[child_id], frames[base_id])
        strays = [pid for pid in pages if "__ptframe" in pid and not pid.startswith(f"{base_id}__ptframe")]
        self.assertEqual(strays, [], "addr:: OffsetFrom child must pin the SameAs base's frames")

    def test_offset_only_family_roots_at_bare_addr(self):
        # pgx-style: no base page_mapping; OffsetFrom siblings still share one pin key.
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="win", type=RV.AddressType.LINEAR, size=0x2000, and_mask=0xFFFFFFFFFFFFE000))
        pool.add_parsed_addr(ParsedRandomAddress(name="win_p", type=RV.AddressType.PHYSICAL, size=0x2000, and_mask=0xFFFFFFFFFFFFE000))
        for offset in (0x0, 0x1000):
            leaf = _mapping(f"win+0x{offset:x}", f"win_p+0x{offset:x}", modify_pt=True)
            leaf.lin_addr_link = ("win", offset)
            leaf.phys_addr_link = ("win_p", offset)
            pool.add_parsed_page_mapping(leaf)

        b = PageTableRequestBuilder(pool, _featmgr())
        b.build()
        reqs = list(b.page_reqs_by_map[DEFAULT_MAP_ID])
        reqs_by_id = {r.page_id: r for r in reqs}
        for r in reqs:
            self.assertEqual(_va_family_root(r.page_id, reqs_by_id), "addr::win")

        frames, pages, _mappings, _windows = self._emit(reqs)
        shared = next(iter(frames.values()))
        for r in reqs:
            self.assertEqual(frames[r.page_id], shared)
        strays = [pid for pid in pages if "__ptframe" in pid and not pid.startswith("addr::win__ptframe")]
        self.assertEqual(strays, [])


class TestModifyPtOwnsItsLeafPteNode(unittest.TestCase):
    """Runtime PTE rewrites reserve their complete root-entry VA family."""

    SPAN_4KB = 0x200000  # sv39/sv48/sv57: VAs sharing the level-0 table span 2 MiB

    def _req(self, pagesize=RV.RiscvPageSizes.S4KB, paging_mode=RV.RiscvPagingModes.SV39, **kw):
        ppm = _mapping("m", "m_pa", **kw)
        ppm.final_pagesize = pagesize
        ppm.address_mask = RV.RiscvPageSizes.address_mask(pagesize)
        ppm.phys_address_mask = RV.RiscvPageSizes.address_mask(pagesize)
        pool = Pool()
        pool.add_parsed_page_mapping(ppm)
        b = PageTableRequestBuilder(pool, _featmgr(paging_mode=paging_mode))
        b.build()
        return b.page_reqs_by_map[DEFAULT_MAP_ID][0]

    def test_modify_pt_gets_root_granule_without_growing_footprint(self):
        for mode, granule in (
            (RV.RiscvPagingModes.SV39, 1 << 30),
            (RV.RiscvPagingModes.SV48, 1 << 39),
            (RV.RiscvPagingModes.SV57, 1 << 48),
        ):
            with self.subTest(mode=mode):
                req = self._req(
                    paging_mode=mode,
                    modify_pt=True,
                )
                self.assertEqual(req.va_reserve_size, 0x1000)
                self.assertEqual(req.va_reserve_granule, granule)
                self.assertEqual(
                    req.va.and_mask,
                    RV.RiscvPageSizes.address_mask(RV.RiscvPageSizes.S4KB),
                )

    def test_modify_nonleaf_pt_gets_the_same_span(self):
        req = self._req(modify_nonleaf_pt=True)
        self.assertIsNotNone(req.va_reserve_size)
        assert req.va_reserve_size is not None
        self.assertGreaterEqual(req.va_reserve_size, self.SPAN_4KB)
        self.assertIsNotNone(req.va.and_mask)
        assert req.va.and_mask is not None
        self.assertEqual(req.va.and_mask & (self.SPAN_4KB - 1), 0)
        self.assertIsNone(req.va_reserve_granule)

    def test_plain_page_is_untouched(self):
        req = self._req()
        self.assertEqual(req.va_reserve_size, 0x1000)
        self.assertIsNone(req.va_reserve_granule)
        self.assertEqual(req.va.and_mask, RV.RiscvPageSizes.address_mask(RV.RiscvPageSizes.S4KB))

    def test_addr_offset_child_does_not_grow(self):
        # The child sits inside the bare addr:: window via OffsetFrom, so its own
        # reservation must stay its pagesize -- growing it would over-reserve the
        # root-slot span per member of a 16-page family.
        pool = Pool()
        pool.add_parsed_addr(ParsedRandomAddress(name="m", type=RV.AddressType.LINEAR, size=0x2000, and_mask=0xFFFFFFFFFFFFE000))
        pool.add_parsed_addr(ParsedRandomAddress(name="m_pa", type=RV.AddressType.PHYSICAL, size=0x2000, and_mask=0xFFFFFFFFFFFFE000))
        anchor = _mapping("m", "m_pa", modify_pt=True)
        child = _mapping("m+0x1000", "m_pa+0x1000", modify_pt=True)
        child.lin_addr_link = ("m", 0x1000)
        child.phys_addr_link = ("m_pa", 0x1000)
        pool.add_parsed_page_mapping(anchor)
        pool.add_parsed_page_mapping(child)
        b = PageTableRequestBuilder(pool, _featmgr())
        b.build()
        reqs = {r.page_id: r for r in b.page_reqs_by_map[DEFAULT_MAP_ID]}
        child_req = next(r for pid, r in reqs.items() if pid.endswith("m+0x1000"))
        anchor_req = next(r for pid, r in reqs.items() if pid.endswith("::m"))
        # SameAs(addr::) leaves ordinary reserve unset; granule still on the anchor.
        self.assertIsNone(anchor_req.va_reserve_size)
        self.assertEqual(anchor_req.va_reserve_granule, 1 << 30)
        self.assertEqual(child_req.va_reserve_size, 0x1000)
        self.assertIsNone(child_req.va_reserve_granule)

    def test_root_level_leaf_owns_its_root_slot(self):
        req = self._req(pagesize=RV.RiscvPageSizes.S1GB, modify_pt=True)
        self.assertEqual(req.va_reserve_size, RV.RiscvPageSizes.memory(RV.RiscvPageSizes.S1GB))
        self.assertEqual(req.va_reserve_granule, 1 << 30)
        self.assertEqual(req.va.and_mask, RV.RiscvPageSizes.address_mask(RV.RiscvPageSizes.S1GB))

    def test_exact_va_keeps_its_address(self):
        # An exact VA is placed where the consumer asked; the span cannot move it.
        req = self._req(modify_pt=True, lin_addr="0x80001000", lin_addr_specified=True)
        self.assertEqual(req.va.exact, 0x80001000)
        self.assertEqual(req.va_reserve_granule, 1 << 30)


class TestNapotNBitIsNotDefaultForced(unittest.TestCase):
    """An unspecified NAPOT ``n`` must reach riemap as ABSENT, not as ``n=0``.

    ``n`` is the one PTE bit whose unset and 0 differ: riemap reads an absent ``n`` on a 64 KiB
    page as "use Svnapot" and an explicit ``n=0`` as "do not".
    """

    def _leaf_attrs(self, **kw):
        ppm = _mapping("p", "p_pa", pagesizes=["64kb"], v=1, r=1, w=1, a=1, d=1, **kw)
        ppm.final_pagesize = RV.RiscvPageSizes.S64KB
        builder = PageTableRequestBuilder(featmgr=_featmgr(), rng=RandNum(seed=1), pool=Pool())
        return builder._attrs(ppm)

    def test_unspecified_n_is_absent(self):
        self.assertNotIn("n", self._leaf_attrs(), "an undeclared n must not reach riemap at all")
        self.assertNotIn("n_level0", self._leaf_attrs(), "an undeclared n_level0 must not reach riemap as a force")

    def test_explicit_n_zero_is_forwarded(self):
        # A genuine "no Svnapot" request must still survive -- that is what riemap honors.
        self.assertEqual(self._leaf_attrs(n=0).get("n"), 0)

    def test_explicit_n_level_force_is_forwarded(self):
        self.assertEqual(self._leaf_attrs(n_level0=0).get("n_level0"), 0)


class TestVsNonLeafGstageGeometry(unittest.TestCase):
    """A ``Mapping`` carries no g-stage geometry, so every non-leaf VS node must declare its
    own -- a ``PTGPage`` when RieMap synthesizes the frame's identity, or the pinned frame
    ``Page`` itself when the consumer owns it."""

    def _req(self, nonleaf=RV.RiscvPageSizes.S4KB, attrs=None, modify_nonleaf=False):
        return _PageReq(
            page_id="p",
            pagesize=RV.RiscvPageSizes.S4KB,
            attrs={"v": 1, "r": 1, "w": 1, "a": 1, "d": 1, **(attrs or {})},
            gstage_forced_attrs=_glevel_keys(attrs),
            gstage_vs_leaf_size=RV.RiscvPageSizes.S4KB,
            gstage_vs_nonleaf_size=nonleaf,
            modify_nonleaf_pt=modify_nonleaf,
        )

    def test_every_nonleaf_level_declares_the_nonleaf_pagesize(self):
        # SV39 4KB has table frames at levels 0 and 1 below the
        # frontend-owned level-2 root. Each generated frame declares its
        # geometry; LEAF remains the leaf-PTE attribute declaration.
        pt_nodes = _vs_gstage_pt_nodes(self._req(nonleaf=RV.RiscvPageSizes.S2MB), {}, 3)
        for level in (0, 1):
            page = pt_nodes[level].page
            self.assertIsInstance(page, PTGPage, f"VS level {level} must declare its g-stage frame geometry")
            assert isinstance(page, PTGPage)
            self.assertEqual(page.pagesize, RV.RiscvPageSizes.S2MB)
        self.assertIsNone(pt_nodes[LEAF].page)

    def test_forced_level_keeps_its_bits_and_gets_the_geometry(self):
        pt_nodes = _vs_gstage_pt_nodes(self._req(nonleaf=RV.RiscvPageSizes.S2MB, attrs={"v_level1_glevel0": 0}), {}, 3)
        self.assertIsInstance(pt_nodes[0].page, PTGPage)
        assert isinstance(pt_nodes[0].page, PTGPage)
        self.assertEqual(pt_nodes[0].page.level_attrs(), {0: {"v": 0}})
        self.assertEqual(pt_nodes[0].page.pagesize, RV.RiscvPageSizes.S2MB)

    def test_a_pinned_frame_owns_its_level_instead_of_a_ptgpage(self):
        # A pinned frame is a real declared Page carrying the same pagesize, and RieMap
        # synthesizes no identity for a GPA the consumer already translates -- so no PTGPage
        # may occupy the single PTNode.page slot there.
        req = self._req(nonleaf=RV.RiscvPageSizes.S2MB, modify_nonleaf=True)
        pages: dict = {}
        mappings: list = []
        frames = _modify_pt_nodes(req, DEFAULT_MAP_ID, RV.RiscvPagingModes.SV39, True, False, pages, mappings, [], {req.page_id: req})
        pt_nodes = _vs_gstage_pt_nodes(req, frames, 3)
        self.assertEqual(set(frames), {LEAF, 1, 2}, "modify_nonleaf_pt owns every walk level's frame")
        for level in (0, 1):
            self.assertIsNone(pt_nodes[level].page, f"VS level {level}'s PTGPage must yield to the pinned frame")
        # The frame Page carries the geometry instead (the root frame is shared, so it stays 4KB).
        self.assertEqual(pages[frames[LEAF]].pagesize, RV.RiscvPageSizes.S2MB)
        self.assertEqual(pages[frames[1]].pagesize, RV.RiscvPageSizes.S2MB)

    def test_max_levels_zero_declares_nothing(self):
        # A single-stage / bare-VS source owns no synthesized g-stage identity, so it must not
        # declare geometry it does not have.
        pt_nodes = _vs_gstage_pt_nodes(self._req(nonleaf=RV.RiscvPageSizes.S2MB), {})
        self.assertEqual([k for k, n in pt_nodes.items() if n.page is not None], [])


class TestMaterializeResolver(unittest.TestCase):
    """The recipe -> object pass: topological resolution, with failure diagnostics that
    distinguish a missing relation target from a dependency cycle."""

    def setUp(self):
        self.phys = Space(paging_mode=RV.RiscvPagingModes.DISABLE)
        self.va_space = Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.SINGLE)
        self.spaces = {DEFAULT_MAP_ID: self.va_space}

    @staticmethod
    def _spec(relation=None, space_name=DEFAULT_MAP_ID):
        return _RawPageSpec(space_name=space_name, pagesize=RV.RiscvPageSizes.S4KB, addr=RecipeAddrSpec(relation=relation))

    def _build(self, specs):
        built, _mappings = _materialize(specs, [], self.spaces, self.phys)
        return built

    def test_forward_chain_resolves_regardless_of_declaration_order(self):
        # Declared child-first: every relation references a recipe declared LATER.
        specs = {
            "chain_c": self._spec(RecipeSameAs("chain_b")),
            "chain_b": self._spec(RecipeOffsetFrom("chain_a", 0x2000)),
            "chain_a": self._spec(),
        }
        built = self._build(specs)
        self.assertEqual(set(built), {"chain_a", "chain_b", "chain_c"})
        rel_b = _rel(built["chain_b"].addr, OffsetFrom)
        self.assertIs(rel_b.target, built["chain_a"])
        self.assertEqual(rel_b.delta, 0x2000)
        rel_c = _rel(built["chain_c"].addr, SameAs)
        self.assertIs(rel_c.target, built["chain_b"])

    def test_derived_from_converts_with_its_masks(self):
        specs = {
            "derive_d": self._spec(RecipeDerivedFrom("derive_a", and_mask=0xFFFF0000, or_mask=0x10, not_mask=0x20, random_mask=0xF000)),
            "derive_a": self._spec(),
        }
        built = self._build(specs)
        rel = _rel(built["derive_d"].addr, DerivedFrom)
        self.assertIs(rel.target, built["derive_a"])
        self.assertEqual(rel.and_mask, 0xFFFF0000)
        self.assertEqual(rel.or_mask, 0x10)
        self.assertEqual(rel.not_mask, 0x20)
        self.assertEqual(rel.random_mask, 0xF000)

    def test_relation_free_recipes_resolve_to_no_relation(self):
        built = self._build({"free_a": self._spec(), "free_b": self._spec()})
        self.assertEqual(set(built), {"free_a", "free_b"})
        for page in built.values():
            self.assertIsNone(page.addr.relation)

    def test_space_resolution_uses_phys_for_the_sentinel(self):
        built = self._build({"va_page": self._spec(), "pa_page": self._spec(space_name=_PHYS)})
        self.assertIs(built["va_page"].space, self.va_space)
        self.assertIs(built["pa_page"].space, self.phys)

    def test_missing_target_names_the_unresolved_id_and_its_target(self):
        specs = {
            "ok_page": self._spec(),
            "orphan": self._spec(RecipeSameAs("ghost")),
        }
        with self.assertRaises(ValueError) as ctx:
            self._build(specs)
        message = str(ctx.exception)
        self.assertIn("not declared", message)
        self.assertIn("'orphan'", message)
        self.assertIn("'ghost'", message)
        self.assertNotIn("cycle", message.lower())

    def test_dependency_cycle_reports_the_cycle_path(self):
        specs = {
            "cycle_a": self._spec(RecipeSameAs("cycle_c")),
            "cycle_b": self._spec(RecipeOffsetFrom("cycle_a", 0x1000)),
            "cycle_c": self._spec(RecipeDerivedFrom("cycle_b")),
        }
        with self.assertRaises(ValueError) as ctx:
            self._build(specs)
        message = str(ctx.exception)
        self.assertIn("dependency cycle", message)
        for id_ in ("cycle_a", "cycle_b", "cycle_c"):
            self.assertIn(id_, message)
        self.assertNotIn("not declared", message)

    def test_self_dependency_is_reported_as_a_cycle(self):
        specs = {"selfish": self._spec(RecipeSameAs("selfish"))}
        with self.assertRaises(ValueError) as ctx:
            self._build(specs)
        message = str(ctx.exception)
        self.assertIn("dependency cycle", message)
        self.assertIn("selfish", message)


if __name__ == "__main__":
    unittest.main()

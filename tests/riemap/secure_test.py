# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""STEE secure-tag (bit 55) placement in the builder."""

import unittest

import riescue.lib.enums as RV
from riescue.lib.rand import RandNum
from riescue.riemap.memory import Memory
from riescue.riemap.builder import PageTableBuilder
from riescue.riemap.request import AddrSpec, LEAF, Mapping, OffsetFrom, Page, PTNode, SameAs, Space
from riescue.riemap.addrgen.address_space import AddressSpace
from riescue.riemap.addrgen.types import AddressConstraint

_SECURE_BIT = 0x0080000000000000


def _memory():
    return Memory.from_dict(
        {
            "dram": {
                "dram0": {"address": "0x80000000", "size": "0x3FFFF80000000", "cacheable": True, "configurable": True},
                "dram_sec": {"address": "0x4000000000000", "size": "0x4000000000000", "cacheable": True, "configurable": True, "secure": True},
            }
        }
    )


def _leaf_attrs(**extra):
    return {"v": 1, "r": 1, "w": 1, "x": 1, "a": 1, "d": 1, **extra}


def _leaf_nodes(**extra):
    return {LEAF: PTNode(attrs=_leaf_attrs(**extra))}


class TestSecureTagging(unittest.TestCase):
    def test_bare_secure_page_carries_bit55(self):
        """A secure page with no mapping (paging disabled: VA==PA, no leaf PTE to
        carry the tag) must get bit 55 in its own resolved address."""
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        page = b.add_page(Page(space=b.phys, addr=AddrSpec(qualifiers={RV.AddressQualifiers.ADDRESS_SECURE})))
        result = b.build()
        addr = result.address(page)
        self.assertTrue(addr & _SECURE_BIT, f"bare secure page address 0x{addr:x} missing bit 55")

    def test_bare_nonsecure_page_has_no_bit55(self):
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        page = b.add_page(Page(space=b.phys))
        result = b.build()
        self.assertFalse(result.address(page) & _SECURE_BIT)

    def test_mapped_secure_leaf_pte_carries_bit55(self):
        """A secure page reached through a leaf PTE gets bit 55 from the PTE PPN
        (the address itself stays clean)."""
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        src = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x1000)))
        dst = b.add_page(Page(space=b.phys, addr=AddrSpec(qualifiers={RV.AddressQualifiers.ADDRESS_SECURE})))
        # secure is signalled by the dst frame's ADDRESS_SECURE qualifier (set above), not attrs.
        b.add_mapping(Mapping(src=src, dst=dst, pt_nodes=_leaf_nodes()))
        result = b.build()
        va, pa = result.address_of(src)
        _steps, translated = result.space(va_space).walk(va)
        self.assertTrue(translated & _SECURE_BIT, f"secure leaf PTE translated to 0x{translated:x} missing bit 55")
        self.assertFalse(pa & _SECURE_BIT, "mapped secure page's own address should stay clean")

    def test_same_as_follower_uses_secure_allocation_root(self):
        """A relational destination carries no duplicate qualifier. Its leaf derives the
        secure output bit from the free physical page that actually owns the draw."""
        b = PageTableBuilder(rng=RandNum(seed=1), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        src = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x1000)))
        root = b.add_page(
            Page(
                space=b.phys,
                addr=AddrSpec(qualifiers={RV.AddressQualifiers.ADDRESS_SECURE}),
            )
        )
        dst = b.add_page(Page(space=b.phys, addr=AddrSpec(relation=SameAs(root))))
        self.assertFalse(dst.addr.qualifiers)
        b.add_mapping(Mapping(src=src, dst=dst, pt_nodes=_leaf_nodes()))

        result = b.build()
        va, _pa = result.address_of(src)
        _steps, translated = result.space(va_space).walk(va)
        self.assertTrue(translated & _SECURE_BIT)
        self.assertEqual(result.address(dst) | _SECURE_BIT, result.address(root))

    def test_transitive_same_as_uses_secure_allocation_root(self):
        b = PageTableBuilder(rng=RandNum(seed=2), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        src = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x2000)))
        root = b.add_page(
            Page(
                space=b.phys,
                addr=AddrSpec(qualifiers={RV.AddressQualifiers.ADDRESS_SECURE}),
            )
        )
        middle = b.add_page(Page(space=b.phys, addr=AddrSpec(relation=SameAs(root))))
        dst = b.add_page(Page(space=b.phys, addr=AddrSpec(relation=SameAs(middle))))
        b.add_mapping(Mapping(src=src, dst=dst, pt_nodes=_leaf_nodes()))

        result = b.build()
        va, _pa = result.address_of(src)
        self.assertTrue(result.space(va_space).walk(va)[1] & _SECURE_BIT)

    def test_offset_family_uses_root_address_class(self):
        b = PageTableBuilder(rng=RandNum(seed=3), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
        src = b.add_page(Page(space=va_space, addr=AddrSpec(exact=0x3000)))
        root = b.add_page(
            Page(
                space=b.phys,
                addr=AddrSpec(
                    qualifiers={RV.AddressQualifiers.ADDRESS_SECURE},
                ),
                reserve_size=0x2000,
            )
        )
        dst = b.add_page(
            Page(
                space=b.phys,
                addr=AddrSpec(relation=OffsetFrom(root, 0x1000)),
            )
        )
        b.add_mapping(Mapping(src=src, dst=dst, pt_nodes=_leaf_nodes()))

        result = b.build()
        va, _pa = result.address_of(src)
        self.assertTrue(result.space(va_space).walk(va)[1] & _SECURE_BIT)
        self.assertEqual(result.address(dst) | _SECURE_BIT, result.address(root) + 0x1000)


class TestSecurePtProbability(unittest.TestCase):
    """``Space.secure_pt_probability`` alone decides whether page-table node frames are
    drawn from secure memory. There is no separate secure-mode gate: choosing a frame's
    memory pool is allocation policy (riemap's), while a consumer that is not in secure
    mode simply passes 0."""

    def _drawn_pt_frame_addrs(self, probability: int, seed: int = 1):
        """Bases of auto-allocated page-table frames below the root."""
        b = PageTableBuilder(rng=RandNum(seed=seed), memory=_memory())
        va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, secure_pt_probability=probability))
        for _ in range(8):
            src = b.add_page(Page(space=va_space))
            dst = b.add_page(Page(space=b.phys))
            b.add_mapping(Mapping(src=src, dst=dst, pt_nodes=_leaf_nodes()))
        result = b.build()
        space_result = result.space(va_space)
        root = space_result.root_addr
        return [view.addr for view in space_result.tables() if view.addr != root]

    def test_full_probability_puts_pt_frames_in_secure_memory(self):
        addrs = self._drawn_pt_frame_addrs(100)
        self.assertTrue(addrs, "expected at least one drawn page-table node frame")
        for addr in addrs:
            self.assertTrue(addr & _SECURE_BIT, f"PT frame 0x{addr:x} missing STEE bit 55 at probability 100")

    def test_zero_probability_keeps_pt_frames_out_of_secure_memory(self):
        addrs = self._drawn_pt_frame_addrs(0)
        self.assertTrue(addrs, "expected at least one drawn page-table node frame")
        for addr in addrs:
            self.assertFalse(addr & _SECURE_BIT, f"PT frame 0x{addr:x} sets STEE bit 55 at probability 0")


class TestBit55ClusterExclusion(unittest.TestCase):
    """Bit 55 (cluster 55) is the STEE secure marker. A non-secure physical draw must
    never land there (centrally enforced in AddressSpace.find_clusters); a secure
    (ADDRESS_SECURE) draw intentionally keeps cluster 55."""

    def _space(self):
        space = AddressSpace(rng=RandNum(seed=1), address_type=RV.AddressType.PHYSICAL)
        # A segment that spans clusters well past bit 55 (0x100000000000000-1 == cluster 55),
        # for both DRAM and SECURE qualifiers.
        span = (0x80000000, 0xFFFFFFFFFFFFFF)
        space.define_segment(RV.AddressQualifiers.ADDRESS_DRAM, *span)
        space.define_segment(RV.AddressQualifiers.ADDRESS_SECURE, *span)
        return space

    def _constraint(self, qualifier):
        return AddressConstraint(
            type=RV.AddressType.PHYSICAL,
            bits=57,  # wide enough that cluster 55 is otherwise in range
            mask=0xFFFFFFFFFFFFF000,  # bit 55 set -> cluster 55 possible from the mask
            size=0x1000,
            qualifiers={qualifier},
        )

    def test_nonsecure_physical_draw_excludes_cluster_55(self):
        space = self._space()
        clusters = space.find_clusters(self._constraint(RV.AddressQualifiers.ADDRESS_DRAM))
        self.assertNotIn(55, clusters, "non-secure physical draw must not include cluster 55")

    def test_secure_physical_draw_keeps_cluster_55(self):
        space = self._space()
        clusters = space.find_clusters(self._constraint(RV.AddressQualifiers.ADDRESS_SECURE))
        self.assertIn(55, clusters, "secure physical draw must keep cluster 55")


class TestNonSecureDrawNeverOnBit55(unittest.TestCase):
    """End-to-end: repeated non-secure physical draws never yield an address with bit 55
    set, even when DRAM extends past bit 55 and the constraint width allows it."""

    def _wide_memory(self):
        # DRAM extends past bit 55 (0x80000000 .. ~0xFF00000000000000).
        return Memory.from_dict({"dram": {"dram0": {"address": "0x80000000", "size": "0xFE00000000000000", "cacheable": True, "configurable": True}}})

    def test_free_physical_draws_avoid_bit55(self):
        for seed in range(16):
            b = PageTableBuilder(rng=RandNum(seed=seed), memory=self._wide_memory(), physical_addr_bits=57)
            va_space = b.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
            va = b.add_page(Page(space=va_space))
            pa = b.add_page(Page(space=b.phys))  # free non-secure physical draw
            b.add_mapping(Mapping(src=va, dst=pa, pt_nodes=_leaf_nodes()))
            result = b.build()
            _va, pa_addr = result.address_of(va)
            self.assertFalse(pa_addr & _SECURE_BIT, f"seed {seed}: non-secure PA 0x{pa_addr:x} sets STEE bit 55")


if __name__ == "__main__":
    unittest.main(verbosity=2)

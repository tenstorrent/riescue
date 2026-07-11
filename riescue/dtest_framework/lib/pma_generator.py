# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import logging
from typing import Optional

import riescue.lib.common as common
from riescue.dtest_framework.lib.pma import PmaInfo
from riescue.dtest_framework.parser import ParsedPmaHint
from riescue.dtest_framework.config.pma_config import PmaConfig, PmaRegionConfig
from riescue.dtest_framework.config.memory import Memory
from riescue.lib.rand import RandNum

log = logging.getLogger(__name__)


class PmaGenerator:
    """
    Generates PMA regions from hints and configuration.
    Respects the PMA region limit from ``PmaConfig.max_regions``.

    :param pma_config: PMA configuration from cpuconfig.json (optional)
    :param memory: Memory configuration with DRAM/IO ranges
    :param rng: Random number generator for address allocation
    """

    DEFAULT_REGION_SIZE = 0x1000000  # 16MB default

    def __init__(self, pma_config: Optional[PmaConfig], memory: Memory, rng: RandNum):
        self.pma_config = pma_config or PmaConfig()
        self.memory = memory
        self.rng = rng
        self.used_regions = 0
        self.max_regions = self.pma_config.max_regions
        self.generated_regions: list[PmaInfo] = []
        self.last_region: Optional[PmaInfo] = None

    def generate_all(self, hints: list[ParsedPmaHint]) -> list[PmaInfo]:
        """
        Generate all PMA regions from hints and config.

        Order of generation:
        1. Explicitly configured regions (not auto_generate)
        2. Regions from hints (assembly + JSON)
        3. Auto-generated regions from config

        :param hints: List of parsed PMA hints from test file
        :return: List of generated PmaInfo objects
        """
        all_regions = []

        # First, add explicitly configured regions (not auto_generate)
        for region_cfg in self.pma_config.regions:
            if not region_cfg.auto_generate:
                pma_info = self._create_pma_from_config(region_cfg)
                all_regions.append(pma_info)
                self.generated_regions.append(pma_info)  # Track for adjacent_to lookups
                self.used_regions += 1
                self.last_region = pma_info
                log.debug(f"Added configured PMA region: {pma_info.pma_name} at 0x{pma_info.pma_address:x}")

        # Convert JSON hints to ParsedPmaHint and add to hints list
        from riescue.dtest_framework.config.pma_config import PmaHintConfig

        json_hints = [self._convert_json_hint_to_parsed(hint_cfg) for hint_cfg in self.pma_config.hints]
        all_hints = hints + json_hints

        # Then, generate from hints
        for hint in all_hints:
            regions = self.generate_from_hint(hint)
            all_regions.extend(regions)

        # Finally, add auto-generated regions from config
        for region_cfg in self.pma_config.regions:
            if region_cfg.auto_generate:
                pma_info = self._create_pma_from_config(region_cfg)
                all_regions.append(pma_info)
                self.used_regions += 1
                self.last_region = pma_info
                log.debug(f"Added auto-generated PMA region: {pma_info.pma_name} at 0x{pma_info.pma_address:x}")

        self.generated_regions = all_regions
        return all_regions

    def generate_from_hint(self, hint: ParsedPmaHint) -> list[PmaInfo]:
        """
        Generate PMA regions from a single hint.

        :param hint: Parsed PMA hint
        :return: List of generated PmaInfo objects
        """
        # Expand combinations
        combinations = self._expand_combinations(hint)

        # Apply min/max limits
        if hint.min_regions is not None:
            if len(combinations) < hint.min_regions:
                log.warning(f"PMA hint '{hint.name}' requests min_regions={hint.min_regions}, " f"but only {len(combinations)} combinations available. Using all combinations.")
            else:
                # Ensure we have at least min_regions
                pass  # We'll use all available combinations

        if hint.max_regions is not None:
            combinations = combinations[: hint.max_regions]

        # Check available slots
        available = self.max_regions - self.used_regions
        if len(combinations) > available:
            log.warning(f"PMA hint '{hint.name}' requests {len(combinations)} regions, " f"but only {available} slots available. Truncating to {available} regions.")
            combinations = combinations[:available]

        if not combinations:
            log.warning(f"PMA hint '{hint.name}' has no valid combinations, skipping")
            return []

        # Generate regions for each combination
        regions = []
        for idx, combo in enumerate(combinations):
            region = self._generate_region(combo, hint, idx)
            regions.append(region)
            self.used_regions += 1
            self.last_region = region
            log.debug(f"Generated PMA region from hint '{hint.name}': {region.pma_name} " f"at 0x{region.pma_address:x}, size 0x{region.pma_size:x}")

        return regions

    def _expand_combinations(self, hint: ParsedPmaHint) -> list[dict]:
        """
        Expand hint into all possible combinations.

        If specific combinations are provided, use those.
        Otherwise, generate cartesian product of all attribute lists.

        :param hint: Parsed PMA hint
        :return: List of combination dictionaries
        """
        # If specific combinations provided, use those
        if hint.combinations:
            return hint.combinations

        # Otherwise, generate cartesian product
        combinations = []

        # Get all attribute lists with defaults
        memory_types = hint.memory_types or ["memory"]
        cacheability = hint.cacheability or ["cacheable"]
        combining = hint.combining or ["noncombining"]
        rwx_combos = hint.rwx_combos or ["rwx"]
        amo_types = hint.amo_types or ["arithmetic"]
        routing = hint.routing or ["coherent"]

        # Generate all combinations
        for mem_type in memory_types:
            for rwx in rwx_combos:
                for amo in amo_types:
                    for route in routing:
                        if mem_type == "memory":
                            for cache in cacheability:
                                combo = {"memory_type": mem_type, "cacheability": cache, "rwx": rwx, "amo_type": amo, "routing": route}
                                combinations.append(combo)
                        elif mem_type == "io":
                            for comb in combining:
                                combo = {"memory_type": mem_type, "combining": comb, "rwx": rwx, "amo_type": amo, "routing": route}
                                combinations.append(combo)
                        else:  # ch0, ch1
                            combo = {"memory_type": mem_type, "rwx": rwx, "amo_type": amo, "routing": route}
                            combinations.append(combo)

        return combinations

    def _generate_region(self, combo: dict, hint: ParsedPmaHint, idx: int) -> PmaInfo:
        """
        Generate a single PMA region from combination.

        :param combo: Combination dictionary with PMA attributes
        :param hint: Original hint (for adjacent placement)
        :param idx: Index of this combination in the hint
        :return: PmaInfo object
        """
        # Find address space
        base, size = self._find_address_space(combo, hint)

        # Parse RWX
        rwx = combo.get("rwx", "rwx")
        read = "r" in rwx
        write = "w" in rwx
        execute = "x" in rwx

        # Generate name
        name = combo.get("name", f"pma_{hint.name}_{idx}")

        # Create PmaInfo
        pma_info = PmaInfo(
            pma_name=name,
            pma_address=base,
            pma_size=size,
            pma_memory_type=combo["memory_type"],
            pma_read=read,
            pma_write=write,
            pma_execute=execute,
            pma_amo_type=combo.get("amo_type", "arithmetic"),
            pma_cacheability=combo.get("cacheability", "cacheable"),
            pma_combining=combo.get("combining", "noncombining"),
            pma_routing_to=combo.get("routing", "coherent"),
            pma_valid=True,
        )

        return pma_info

    def _find_address_space(self, combo: dict, hint: ParsedPmaHint) -> tuple[int, int]:
        """
        Find available address space for PMA region.

        :param combo: Combination dictionary
        :param hint: Original hint (for adjacent placement)
        :return: Tuple of (base_address, size)
        """
        # Use hint size if specified, otherwise default
        size = hint.size if hint.size is not None else self.DEFAULT_REGION_SIZE

        # If adjacent requested, place next to last region
        if hint.adjacent and self.last_region:
            base = self.last_region.pma_address + self.last_region.pma_size
            # Align to 4KB
            base = (base + 0xFFF) & ~0xFFF
            log.debug(f"Placing adjacent PMA region at 0x{base:x}")
            return base, size

        # Otherwise, find space in appropriate memory type
        if combo["memory_type"] == "memory":
            # Use DRAM range
            if self.memory.dram_ranges:
                dram_range = self.memory.dram_ranges[0]
                # Find space avoiding existing regions
                base = self._find_free_space(dram_range.start, dram_range.size, size)
            else:
                base = 0x80000000  # Default DRAM address
                log.warning("No DRAM range available, using default address 0x80000000")
        else:
            # Use IO range
            if self.memory.io_ranges:
                io_range = self.memory.io_ranges[0]
                base = self._find_free_space(io_range.start, io_range.size, size)
            else:
                base = 0x10000000  # Default IO address
                log.warning("No IO range available, using default address 0x10000000")

        # Align to 4KB
        base = (base + 0xFFF) & ~0xFFF
        return base, size

    def _find_free_space(self, start: int, range_size: int, needed_size: int) -> int:
        """
        Find free space in address range, avoiding existing regions.

        :param start: Start of address range
        :param range_size: Size of address range
        :param needed_size: Size needed for new region
        :return: Base address for new region
        """
        # Simple implementation: find space after existing regions
        if self.generated_regions:
            # Find the highest end address
            last_end = max(r.pma_address + r.pma_size for r in self.generated_regions)
            # Check if we have space after last region
            if last_end + needed_size < start + range_size:
                return last_end

        # Otherwise, use random location within range
        max_start = start + range_size - needed_size
        if max_start < start:
            # Range too small
            return start

        # Generate random address aligned to 4KB
        num_4k_pages = (max_start - start) // 0x1000
        if num_4k_pages <= 0:
            return start

        random_page = self.rng.random_in_range(0, num_4k_pages - 1)
        return start + (random_page * 0x1000)

    def _create_pma_from_config(self, region_cfg: PmaRegionConfig) -> PmaInfo:
        """
        Create PmaInfo from configuration.

        :param region_cfg: PMA region configuration
        :return: PmaInfo object
        """
        # Get base and size
        base = region_cfg.base
        size = region_cfg.size or self.DEFAULT_REGION_SIZE

        # If adjacent_to specified, find that region
        if region_cfg.adjacent_to:
            for region in self.generated_regions:
                if region.pma_name == region_cfg.adjacent_to:
                    base = region.pma_address + region.pma_size
                    base = (base + 0xFFF) & ~0xFFF  # Align to 4KB
                    log.debug(f"Placing region '{region_cfg.name}' adjacent to '{region_cfg.adjacent_to}' at 0x{base:x}")
                    break
            else:
                log.warning(f"Region '{region_cfg.name}' specified adjacent_to '{region_cfg.adjacent_to}' but region not found")

        # If base not set, find space
        if base is None:
            # Create a dummy hint for address space finding
            dummy_hint = ParsedPmaHint()
            # Convert attributes to combo format expected by _find_address_space
            attrs = region_cfg.attributes
            combo = {
                "memory_type": attrs.memory_type,
                "cacheability": attrs.cacheability,
                "combining": attrs.combining,
            }
            base, size = self._find_address_space(combo, dummy_hint)

        # Create PmaInfo
        attrs_dict = region_cfg.attributes.to_pma_info_dict()
        pma_info = PmaInfo(pma_name=region_cfg.name, pma_address=base, pma_size=size, pma_valid=True, **attrs_dict)

        return pma_info

    def _convert_json_hint_to_parsed(self, hint_cfg) -> ParsedPmaHint:
        """
        Convert PmaHintConfig from JSON to ParsedPmaHint.

        :param hint_cfg: PmaHintConfig from JSON
        :return: ParsedPmaHint object
        """
        from riescue.dtest_framework.config.pma_config import PmaHintConfig

        # Create ParsedPmaHint with all fields from JSON hint
        # Note: PmaHintConfig currently only stores combinations, not the attribute lists
        # format (memory_types, cacheability, etc.). If JSON hints use that format,
        # they would need to be expanded to combinations during PmaHintConfig.from_dict()
        parsed_hint = ParsedPmaHint(
            name=hint_cfg.name,
            combinations=hint_cfg.combinations,
            adjacent=hint_cfg.adjacent,
            min_regions=hint_cfg.min_regions,
            max_regions=hint_cfg.max_regions,
            size=hint_cfg.size,  # Size support added
        )

        return parsed_hint


class PmaRandomizer:
    """
    Generates randomized decoy PMA regions with whisper-legal pmacfg/pmamask values.

    Regions are NAPOT-placed in holes of the physical address space (callers pass every occupied
    interval via ``blocked``) so they are live under first-match-wins entry priority. A subset of
    regions gets a random pmamask; masked regions match scattered windows and are decoy-only.

    :param rng: Random number generator (all draws are seeded/deterministic)
    :param mask_pct: Percent probability [0-100] that a region gets a nonzero pmamask
    :param phys_addr_bits: Physical address width; placement stays below 2**phys_addr_bits
    """

    MIN_SIZE_LOG2 = 12  # 4KB, whisper minimum legal region size
    MAX_SIZE_LOG2 = 30  # 1GB cap keeps decoys from swallowing the address space
    MASKED_MIN_COMPARE_BITS = 8  # bounds scattered-match density to <= 2^-8 of pages
    PLACEMENT_RETRIES = 64
    MASK_ATTEMPTS = 8
    # Mask shape strategies: structured patterns exercise more of the pmamask compare space
    MASK_STRATEGY_WEIGHTS = {"subset": 35, "contiguous": 20, "single_bit": 15, "dense": 10, "sparse_low": 10, "sparse_high": 10}
    MEMORY_TYPE_WEIGHTS = {"memory": 50, "io": 30, "ch0": 10, "ch1": 10}
    # IO-type legal rwx combos: whisper rejects write-without-read
    IO_RWX_CHOICES = [(0, 0, 0), (1, 0, 0), (0, 0, 1), (1, 0, 1), (1, 1, 0), (1, 1, 1)]

    def __init__(self, rng: RandNum, mask_pct: int, phys_addr_bits: int):
        self.rng = rng
        self.mask_pct = mask_pct
        self.phys_addr_bits = phys_addr_bits

    def generate(self, count: int, blocked: list[tuple[int, int]]) -> list[PmaInfo]:
        """
        Generate up to ``count`` randomized regions avoiding ``blocked`` [start, end) intervals.

        :param count: Requested number of regions (truncated with a warning on placement exhaustion)
        :param blocked: Occupied physical intervals the decoys must not overlap
        :return: List of PmaInfo with pma_randomized=True
        """
        regions: list[PmaInfo] = []
        occupied = list(blocked)
        for idx in range(count):
            placement = self._place_napot(occupied)
            if placement is None:
                log.warning(f"PMA randomizer: no free NAPOT hole after {self.PLACEMENT_RETRIES} tries; " f"truncating to {len(regions)} of {count} regions")
                break
            base, size = placement
            pma_info = PmaInfo(pma_name=f"pma_rand_{idx}", pma_address=base, pma_size=size, pma_valid=True, pma_randomized=True, **self._random_legal_attributes())
            # Check masks against occupied (not just blocked) so a masked decoy's scattered
            # windows cannot shadow an earlier decoy under first-match-wins priority
            self.apply_random_mask(pma_info, occupied)
            occupied.append((base, base + size))
            regions.append(pma_info)
            log.info(f"Randomized PMA region: {pma_info} mask=0x{pma_info.pma_mask:x}")
        return regions

    def _random_legal_attributes(self) -> dict:
        """Pick a random attribute combo satisfying whisper isLegalPmacfg (faulting flavors included)."""
        memory_type = self.rng.random_choice_weighted(self.MEMORY_TYPE_WEIGHTS)
        if memory_type == "memory":
            read, write, execute = self.rng.random_entry_in([(1, 1, 1), (0, 0, 0)])
            cacheability = self.rng.random_entry_in(["cacheable", "noncacheable"])
            if cacheability == "cacheable":
                amo_type, routing_to = "arithmetic", "coherent"
            else:
                amo_type = "none"
                routing_to = self.rng.random_entry_in(["coherent", "noncoherent"])
            combining = "noncombining"
        else:
            read, write, execute = self.rng.random_entry_in(self.IO_RWX_CHOICES)
            cacheability = "cacheable"  # unused: bit 7 encodes combining for non-memory types
            amo_type, routing_to = "none", "noncoherent"
            combining = self.rng.random_entry_in(["combining", "noncombining"])
        return {
            "pma_memory_type": memory_type,
            "pma_read": read,
            "pma_write": write,
            "pma_execute": execute,
            "pma_amo_type": amo_type,
            "pma_cacheability": cacheability,
            "pma_combining": combining,
            "pma_routing_to": routing_to,
        }

    def _place_napot(self, occupied: list[tuple[int, int]]) -> Optional[tuple[int, int]]:
        """Find a size-aligned NAPOT (base, size) below 2^phys_addr_bits avoiding occupied intervals."""
        max_size_log2 = min(self.MAX_SIZE_LOG2, self.phys_addr_bits - 2)
        for _ in range(self.PLACEMENT_RETRIES):
            size = 1 << self.rng.random_in_range(self.MIN_SIZE_LOG2, max_size_log2 + 1)
            base = self.rng.random_in_range(0, (1 << self.phys_addr_bits) // size) * size
            end = base + size
            if any(base < blk_end and blk_start < end for blk_start, blk_end in occupied):
                continue
            return base, size
        return None

    def apply_random_mask(self, pma_info: PmaInfo, blocked: list[tuple[int, int]], force: bool = False) -> bool:
        """
        Roll mask_pct (unless forced); set pmamask bits above region size via a random shape strategy.

        A masked region matches scattered congruence windows across all memory, so candidate masks
        whose windows cover any blocked interval (loader/OS/fixed pages) are rejected and retried.
        The region's own span must NOT be in ``blocked``: every mask matches its own base window.

        :param pma_info: Region to mask in place; ``pma_mask`` is left 0 when no safe mask is found
        :param blocked: Occupied [start, end) physical intervals no scattered window may cover
        :param force: Skip the mask_pct roll and always attempt masking (pma_masked requests)
        :return: True if a nonzero mask was applied
        """
        if not force and not self.rng.with_probability_of(self.mask_pct):
            return False
        size_bits = common.msb(pma_info.pma_size)
        max_maskable = min(self.phys_addr_bits - self.MASKED_MIN_COMPARE_BITS, 52)
        if size_bits >= max_maskable:
            return False  # region too large to keep enough compare bits; leave unmasked
        candidate_bits = list(range(size_bits, max_maskable))
        for _ in range(self.MASK_ATTEMPTS):
            strategy = self.rng.random_choice_weighted(self.MASK_STRATEGY_WEIGHTS)
            pma_info.pma_mask = self._build_mask(strategy, candidate_bits)
            if not any(pma_info.matches_phys_range(start, end - start) for start, end in blocked):
                return True
        log.debug(f"PMA randomizer: no safe mask found for {pma_info.pma_name}; leaving unmasked")
        pma_info.pma_mask = 0
        return False

    def _build_mask(self, strategy: str, candidate_bits: list[int]) -> int:
        """Build a nonzero mask from candidate_bits per the given shape strategy."""
        n = len(candidate_bits)
        if strategy == "contiguous":
            run = self.rng.random_in_range(1, n + 1)
            start = self.rng.random_in_range(0, n - run + 1)
            chosen = candidate_bits[start : start + run]
        elif strategy == "single_bit":
            chosen = [self.rng.random_entry_in(candidate_bits)]
        elif strategy == "dense":
            drop = self.rng.sample(candidate_bits, self.rng.random_in_range(0, min(3, n - 1) + 1))
            chosen = [bit for bit in candidate_bits if bit not in drop]
        elif strategy == "sparse_low":
            pool = candidate_bits[: max(1, n // 2)]
            chosen = self.rng.sample(pool, self.rng.random_in_range(1, max(2, n // 4 + 1)))
        elif strategy == "sparse_high":
            pool = candidate_bits[n // 2 :]
            chosen = self.rng.sample(pool, self.rng.random_in_range(1, max(2, n // 4 + 1)))
        else:  # subset
            chosen = self.rng.sample(candidate_bits, self.rng.random_in_range(1, n + 1))
        return sum(1 << bit for bit in chosen)

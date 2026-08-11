# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Command-line entry point for the standalone page table generator (``riemap``).

Reads a JSON config, generates page tables via the builder-backed JSON frontend,
and writes the resulting PTEs and per-page walks to a JSON file.
"""

import argparse
import logging
from pathlib import Path

from riescue.riemap.json_frontend import PageTableConfig, generate_page_tables

log = logging.getLogger("riescue.riemap")
# The address generator logs a line per probed cluster and per range split, so it drowns
# everything else: a two-page config emits ~1200 addrgen records against ~30 from the rest
# of riemap, and a realistic one runs to tens of thousands. It is dampened to its own level
# so --log-level DEBUG shows what the GENERATOR decided; --addrgen-log-level opts back in
# when the address search itself is what needs debugging.
_ADDRGEN_LOGGER = "riescue.riemap.addrgen"

_LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser.

    ``--seed`` defaults to 1 so an invocation is reproducible unless the caller
    explicitly selects another seed.
    """
    parser = argparse.ArgumentParser(description="Generate page tables using the riemap page table builder")
    parser.add_argument("input_config", type=Path, help="Path to the PT config file")
    parser.add_argument("output_file", type=Path, help="Path to the output JSON file")
    parser.add_argument("--seed", type=int, default=1, help="Random seed for reproducibility (default: 1)")
    parser.add_argument(
        "--log-level",
        type=str,
        default="WARNING",
        choices=_LOG_LEVELS,
        help="Logging level for the generator (default: WARNING = silent)",
    )
    parser.add_argument(
        "--addrgen-log-level",
        type=str,
        default="WARNING",
        choices=_LOG_LEVELS,
        help="Logging level for the address generator alone, which is far more verbose than the rest of riemap (default: WARNING)",
    )
    return parser


def main() -> None:
    """CLI entry point for the page table generator."""
    args = _build_parser().parse_args()

    # Configure only our logger - don't affect riescued's loggers.
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    log.addHandler(handler)
    log.setLevel(getattr(logging, args.log_level))
    log.propagate = False
    # A level on the addrgen subtree overrides the inherited one (records still reach this
    # handler by propagation, they are just filtered at the source).
    logging.getLogger(_ADDRGEN_LOGGER).setLevel(getattr(logging, args.addrgen_log_level))

    log.info("Page table generator started: input=%s, output=%s, seed=%d", args.input_config, args.output_file, args.seed)

    config = PageTableConfig.from_json_file(args.input_config)
    log.debug("Loaded configuration: %d spaces, %d memory regions", len(config.spaces), len(config.mmap))
    args.output_file.parent.mkdir(parents=True, exist_ok=True)

    output = generate_page_tables(config, args.seed)
    output.to_json_file(args.output_file)
    log.info("Page tables written to %s: %d PTEs", args.output_file, len(output.entries))

    print(f"Generated page tables at {args.output_file}")
    print(f"Total PTEs (merged): {len(output.entries)}")
    print(f"Total spaces: {len(output.spaces)}")
    for space_id, space_output in output.spaces.items():
        print(f"\nSpace '{space_id}':")
        print(f"  Paging mode: {space_output.paging_mode}")
        if space_output.top_base_addr is not None:
            print(f"  Top base address: 0x{space_output.top_base_addr:016x}")
        if space_output.gstage_paging_mode is not None:
            print(f"  G-stage paging mode: {space_output.gstage_paging_mode}")
            if space_output.gstage_top_base_addr is not None:
                print(f"  G-stage top base address: 0x{space_output.gstage_top_base_addr:016x}")
        total_pages = sum(len(va_map) for va_map in space_output.pages.values())
        print(f"  Total pages: {total_pages}")


if __name__ == "__main__":
    main()

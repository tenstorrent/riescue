# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import TYPE_CHECKING, Callable, Optional

from coretp import Instruction, StepIR
from coretp.step import RetrieveAddress

from riescue.compliance.test_plan.actions import Action
from riescue.compliance.test_plan.context import LoweringContext
from riescue.dtest_framework.config import FeatMgr


def _imsic_vs_base(featmgr: FeatMgr) -> Optional[int]:
    """First VS-mode IMSIC interrupt-file address.

    Prefers an explicit ``imsic_vsfile`` entry in cpu_config.json (mapped to
    ``featmgr.io_imsic_vsfile_addr``) for platforms whose VS file lives in a
    distinct region from the S file. If no explicit field is set, falls back
    to the AIA-spec default ``sfile_addr + sfile_stride`` (first guest, N=0,
    where files for guest index N are at ``sbase + (N + 1) * stride``).
    """
    explicit = getattr(featmgr, "io_imsic_vsfile_addr", None)
    if explicit is not None:
        return explicit
    base = featmgr.io_imsic_sfile_addr
    stride = featmgr.io_imsic_sfile_stride
    if base is None or stride is None:
        return None
    return base + stride


def _imsic_vs_stride(featmgr: FeatMgr) -> Optional[int]:
    """VS-mode IMSIC stride. Prefers explicit cpu_config.json entry, falls back
    to the supervisor stride."""
    explicit = getattr(featmgr, "io_imsic_vsfile_stride", None)
    if explicit is not None:
        return explicit
    return featmgr.io_imsic_sfile_stride


_KEY_RESOLVERS: dict[str, Callable[[FeatMgr], Optional[int]]] = {
    "imsic_m_base": lambda fm: getattr(fm, "io_imsic_mfile_addr", None),
    "imsic_s_base": lambda fm: getattr(fm, "io_imsic_sfile_addr", None),
    "imsic_vs_base": _imsic_vs_base,
    "imsic_m_stride": lambda fm: fm.io_imsic_mfile_stride,
    "imsic_s_stride": lambda fm: fm.io_imsic_sfile_stride,
    "imsic_vs_stride": _imsic_vs_stride,
}


class RetrieveAddressAction(Action):
    """
    Lowers :class:`RetrieveAddress` to ``li rd, <addr>`` where ``addr`` is
    resolved from the framework's FeatMgr via the step's ``key``.

    Supported keys are listed in :data:`_KEY_RESOLVERS`. If the FeatMgr field
    backing a key is ``None`` (cpu_config.json missing the entry), this raises
    ``ValueError`` at lowering time rather than silently emitting a zero.
    """

    register_fields: list[str] = []

    def __init__(self, step_id: str, key: str, **kwargs):
        super().__init__(step_id=step_id)
        self.key = key

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "RetrieveAddressAction":
        if TYPE_CHECKING:
            assert isinstance(step.step, RetrieveAddress)
        return cls(step_id=step_id, key=step.step.key, **kwargs)

    def repr_info(self) -> str:
        return f"key={self.key}"

    def resolve(self, ctx: LoweringContext) -> int:
        """Resolve this step's key to a concrete address via ``ctx.featmgr``."""
        resolver = _KEY_RESOLVERS.get(self.key)
        if resolver is None:
            raise ValueError(f"RetrieveAddress: unknown key '{self.key}'. Supported: {sorted(_KEY_RESOLVERS.keys())}")
        value = resolver(ctx.featmgr)
        if value is None:
            raise ValueError(f"RetrieveAddress: FeatMgr field backing key '{self.key}' is None. " f"Populate cpu_config.json (mmap.io.imsic_mfile / imsic_sfile).")
        return value

    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        value = self.resolve(ctx)
        li = ctx.instruction_catalog.get_instruction("li")
        if len(li.source) != 1:
            raise ValueError(f"Expected li to have 1 source operand, got {li.source}")
        li.source[0].val = value
        return li

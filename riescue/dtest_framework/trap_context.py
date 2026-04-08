# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Privilege-mode–aware CSR name bundle for trap/interrupt handler assembly.

:class:`TrapContext` is a frozen dataclass that maps logical x-prefixed register
names to the concrete CSR names for a given privilege level (M or S).  Two
ready-made singletons — :data:`MACHINE_CTX` and :data:`SUPERVISOR_CTX` — cover
the common cases.

Users write **one** handler callable that accepts a ``TrapContext`` and returns
assembly.  The framework selects the correct context at generation time based on
the per-vector delegation state (``mideleg`` bit), so the same handler body
works whether the vector is handled in M-mode or S-mode:

.. code-block:: python

    from riescue import TrapContext

    def my_ssi_handler(ctx: TrapContext) -> str:
        return f\"\"\"
            csrr t0, {ctx.xip}
            li   t1, ~(1 << 1)
            and  t0, t0, t1
            csrw {ctx.xip}, t0
            {ctx.xret}
        \"\"\"

The callable type alias :data:`TrapHookable` is the canonical signature for
handler functions passed to :meth:`~riescue.dtest_framework.config.FeatMgr.register_default_handler`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal


@dataclass(frozen=True)
class TrapContext:
    """Immutable bundle of privilege-level–specific CSR names for a trap handler.

    All fields are concrete RISC-V CSR name strings suitable for direct
    interpolation into assembly (e.g. ``csrr t0, {ctx.xcause}``).

    :param xcause:   Cause CSR  (``mcause`` / ``scause``)
    :param xepc:     EPC CSR    (``mepc``   / ``sepc``)
    :param xret:     Return instruction (``mret`` / ``sret``)
    :param xtval:    Trap value CSR     (``mtval``  / ``stval``)
    :param xip:      Interrupt-pending CSR (``mip`` / ``sip``)
    :param xie:      Interrupt-enable CSR  (``mie`` / ``sie``)
    :param xtvec:    Trap-vector CSR    (``mtvec``  / ``stvec``)
    :param xscratch: Scratch CSR        (``mscratch`` / ``sscratch``)
    :param priv:     Privilege level string (``"machine"`` or ``"supervisor"``).
    """

    xcause: str
    xepc: str
    xret: str
    xtval: str
    xip: str
    xie: str
    xtvec: str
    xscratch: str
    priv: Literal["machine", "supervisor"]


MACHINE_CTX = TrapContext(
    xcause="mcause",
    xepc="mepc",
    xret="mret",
    xtval="mtval",
    xip="mip",
    xie="mie",
    xtvec="mtvec",
    xscratch="mscratch",
    priv="machine",
)

SUPERVISOR_CTX = TrapContext(
    xcause="scause",
    xepc="sepc",
    xret="sret",
    xtval="stval",
    xip="sip",
    xie="sie",
    xtvec="stvec",
    xscratch="sscratch",
    priv="supervisor",
)

#: Canonical type for interrupt handler callables passed to
#: :meth:`~riescue.dtest_framework.config.FeatMgr.register_default_handler`.
TrapHookable = Callable[[TrapContext], str]

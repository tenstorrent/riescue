# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Sdtrig (Trigger Module) helper for Riescue-D.

Encodes tdata1 values for mcontrol6, icount, itrigger, and etrigger trigger
types per RISC-V Debug Spec.
"""

from enum import Enum
from typing import Sequence

# Trigger type encodings (tdata1[63:60])
TDATA1_TYPE_MCONTROL6 = 6
TDATA1_TYPE_ICOUNT = 3
TDATA1_TYPE_ITRIGGER = 4
TDATA1_TYPE_ETRIGGER = 5

# Size encoding: 0=1B, 1=2B, 2=4B, 3=8B
SIZE_1B = 0
SIZE_2B = 1
SIZE_4B = 2
SIZE_8B = 3


class TriggerType(Enum):
    """RISC-V Debug Spec trigger type identifiers."""

    EXECUTE = "execute"
    LOAD = "load"
    STORE = "store"
    LOAD_STORE = "load_store"
    ICOUNT = "icount"
    ITRIGGER = "itrigger"
    ETRIGGER = "etrigger"

    @classmethod
    def str_to_enum(cls, s: str) -> "TriggerType":
        try:
            return cls(s.lower())
        except ValueError:
            raise ValueError(f"Unknown trigger type: {s!r}. Valid: {[e.value for e in cls]}")


class TriggerAction(Enum):
    """Trigger action encodings (tdata1 action field value).

    The enum value IS the tdata1 bit encoding:
      mcontrol6: bits [15:12], icount/itrigger/etrigger: bits [3:0]
    """

    BREAKPOINT = 0
    DEBUG_MODE = 1
    TRACE_ON = 2
    TRACE_OFF = 3
    TRACE_NOTIFY = 4

    @classmethod
    def str_to_enum(cls, s: str) -> "TriggerAction":
        _map = {
            "breakpoint": cls.BREAKPOINT,
            "debug_mode": cls.DEBUG_MODE,
            "trace_on": cls.TRACE_ON,
            "trace_off": cls.TRACE_OFF,
            "trace_notify": cls.TRACE_NOTIFY,
        }
        try:
            return _map[s.lower()]
        except KeyError:
            raise ValueError(f"Unknown trigger action: {s!r}. Valid: {list(_map)}")


class TriggerMatch(Enum):
    """mcontrol6 match type encodings (tdata1[10:7]).

    The enum value IS the tdata1 bit encoding.
    """

    EQUAL = 0
    NAPOT = 1
    LT = 2
    GE = 3
    MASK_LOW = 4
    MASK_HIGH = 5
    NE = 8
    NOT_NAPOT = 9
    NOT_MASK_LOW = 12
    NOT_MASK_HIGH = 13

    @classmethod
    def str_to_enum(cls, s: str) -> "TriggerMatch":
        _map = {
            "equal": cls.EQUAL,
            "napot": cls.NAPOT,
            "lt": cls.LT,
            "ge": cls.GE,
            "mask_low": cls.MASK_LOW,
            "mask_high": cls.MASK_HIGH,
            "ne": cls.NE,
            "not_napot": cls.NOT_NAPOT,
            "not_mask_low": cls.NOT_MASK_LOW,
            "not_mask_high": cls.NOT_MASK_HIGH,
        }
        try:
            return _map[s.lower()]
        except KeyError:
            raise ValueError(f"Unknown trigger match: {s!r}. Valid: {list(_map)}")


# Valid privilege mode tokens
_VALID_MODES = {"m", "s", "u", "vs", "vu"}
_ALL_MODES = ("m", "s", "u", "vs", "vu")
_DEFAULT_PRIV_MODE = ("m", "s", "u")


def modes_to_priv_bits(priv_mode: Sequence[str]) -> dict:
    """
    Convert a list of privilege mode strings to a dict of individual enable bits.

    Accepts ``"any"`` as a shorthand for all five modes.

    :param priv_mode: sequence such as ``["m", "s", "u"]`` or ``["any"]``
    :returns: dict with keys ``m, s, u, vs, vu``, each 0 or 1
    :raises ValueError: for unknown mode tokens
    """
    expanded: set[str] = set()
    for token in priv_mode:
        if token == "any":
            expanded.update(_ALL_MODES)
        elif token in _VALID_MODES:
            expanded.add(token)
        else:
            raise ValueError(f"Unknown priv_mode token: {token!r}. Valid: {list(_VALID_MODES)} or 'any'")
    return {mode: (1 if mode in expanded else 0) for mode in _ALL_MODES}


def size_to_encoding(size: int) -> int:
    """Map access size (1,2,4,8) to tdata1 size field."""
    if size == 1:
        return SIZE_1B
    if size == 2:
        return SIZE_2B
    if size == 4:
        return SIZE_4B
    if size == 8:
        return SIZE_8B
    return SIZE_4B  # default


def _encode_action(action: TriggerAction) -> int:
    """Return the tdata1 bit encoding for a TriggerAction."""
    return action.value


def build_tdata1_mcontrol6(
    trigger_type: TriggerType,
    action: TriggerAction = TriggerAction.BREAKPOINT,
    size: int = 4,
    chain: int = 0,
    match: TriggerMatch = TriggerMatch.EQUAL,
    priv_mode: Sequence[str] = _DEFAULT_PRIV_MODE,
) -> int:
    """
    Build tdata1 value for mcontrol6 trigger.

    :param trigger_type: TriggerType.EXECUTE, LOAD, STORE, or LOAD_STORE
    :param action: TriggerAction enum value
    :param size: Access size 1, 2, 4, or 8 bytes
    :param chain: 0 or 1
    :param match: TriggerMatch enum value
    :param priv_mode: privilege modes in which the trigger is active, e.g. ["m", "s", "u"].
                      Use ["any"] to enable all modes.
    :returns: 64-bit tdata1 value
    """
    bits = modes_to_priv_bits(priv_mode)

    # type[63:60] = 6
    val = TDATA1_TYPE_MCONTROL6 << 60
    # dmode[59] = 0
    # action[15:12]
    val |= (_encode_action(action) & 0xF) << 12
    # size[18:16]
    val |= (size_to_encoding(size) & 7) << 16
    # chain[11]
    val |= (chain & 1) << 11
    # match[10:7]
    val |= (match.value & 0xF) << 7
    # privilege mode bits: m[6], vs[5], s[4], u[3], vu[2] per Debug Spec mcontrol6 layout
    val |= bits["m"] << 6
    val |= bits["vs"] << 5
    val |= bits["s"] << 4
    val |= bits["u"] << 3
    val |= bits["vu"] << 2
    # load[0], store[1], execute[2]
    if trigger_type == TriggerType.EXECUTE:
        val |= 1 << 2
    elif trigger_type == TriggerType.LOAD:
        val |= 1 << 0
    elif trigger_type == TriggerType.STORE:
        val |= 1 << 1
    elif trigger_type == TriggerType.LOAD_STORE:
        val |= (1 << 0) | (1 << 1)
    else:
        raise ValueError(f"Unsupported trigger_type for mcontrol6: {trigger_type!r}")
    return val & 0xFFFFFFFFFFFFFFFF


def build_tdata1_icount(
    count: int,
    action: TriggerAction = TriggerAction.BREAKPOINT,
    priv_mode: Sequence[str] = _DEFAULT_PRIV_MODE,
    pending: int = 0,
) -> int:
    """
    Build tdata1 value for icount trigger (type=3).

    Fires after *count* instructions retire in the enabled privilege modes.

    :param count: Instruction count (14-bit value, bits [23:10])
    :param action: TriggerAction enum value
    :param priv_mode: privilege modes in which instructions are counted, e.g. ["m", "s", "u"].
                      Use ["any"] to count in all modes.
    :param pending: Pending bit — holds trigger for one extra cycle before firing
    :returns: 64-bit tdata1 value
    """
    bits = modes_to_priv_bits(priv_mode)

    # type[63:60] = 3
    val = TDATA1_TYPE_ICOUNT << 60
    # dmode[59] = 0
    # count[23:10] (14 bits)
    val |= (count & 0x3FFF) << 10
    # m[9], pending[8], s[7], u[6], vs[5], vu[4]
    val |= bits["m"] << 9
    val |= (pending & 1) << 8
    val |= bits["s"] << 7
    val |= bits["u"] << 6
    val |= bits["vs"] << 5
    val |= bits["vu"] << 4
    # action[3:0]
    val |= _encode_action(action) & 0xF
    return val & 0xFFFFFFFFFFFFFFFF


def build_tdata1_itrigger(
    action: TriggerAction = TriggerAction.BREAKPOINT,
    priv_mode: Sequence[str] = _DEFAULT_PRIV_MODE,
) -> int:
    """
    Build tdata1 value for itrigger (interrupt trigger, type=4).

    tdata2 holds the interrupt cause bitmask (bit N = cause N).

    :param action: TriggerAction enum value
    :param priv_mode: privilege modes in which the trigger is active.
    :returns: 64-bit tdata1 value
    """
    bits = modes_to_priv_bits(priv_mode)

    # type[63:60] = 4
    val = TDATA1_TYPE_ITRIGGER << 60
    # dmode[59] = 0
    # m[9], s[7], u[6], vs[5], vu[4]
    val |= bits["m"] << 9
    val |= bits["s"] << 7
    val |= bits["u"] << 6
    val |= bits["vs"] << 5
    val |= bits["vu"] << 4
    # action[3:0]
    val |= _encode_action(action) & 0xF
    return val & 0xFFFFFFFFFFFFFFFF


def build_tdata1_etrigger(
    action: TriggerAction = TriggerAction.BREAKPOINT,
    priv_mode: Sequence[str] = _DEFAULT_PRIV_MODE,
) -> int:
    """
    Build tdata1 value for etrigger (exception trigger, type=5).

    tdata2 holds the exception cause bitmask (bit N = cause N).

    :param action: TriggerAction enum value
    :param priv_mode: privilege modes in which the trigger is active.
    :returns: 64-bit tdata1 value
    """
    bits = modes_to_priv_bits(priv_mode)

    # type[63:60] = 5
    val = TDATA1_TYPE_ETRIGGER << 60
    # dmode[59] = 0
    # m[9], s[7], u[6], vs[5], vu[4]
    val |= bits["m"] << 9
    val |= bits["s"] << 7
    val |= bits["u"] << 6
    val |= bits["vs"] << 5
    val |= bits["vu"] << 4
    # action[3:0]
    val |= _encode_action(action) & 0xF
    return val & 0xFFFFFFFFFFFFFFFF

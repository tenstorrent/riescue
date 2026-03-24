# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for ISS availability fallback logic in _apply_iss_fallback() (ctk.py)
and optional ISS construction in Toolchain.from_clargs() (toolchain.py).

Behaviour under test:
  - No flag + whisper only   → whisper + disable_pass
  - No flag + spike only     → spike + disable_pass
  - No flag + neither        → SystemExit
  - No flag + both           → no change (existing two-pass)
  - --first_pass_iss whisper + whisper available   → whisper + disable_pass
  - --first_pass_iss spike   + spike available     → spike + disable_pass
  - --first_pass_iss whisper + whisper unavailable → SystemExit
  - --first_pass_iss spike   + spike unavailable   → SystemExit
"""

import argparse
import pytest
from unittest.mock import MagicMock, patch

from riescue.ctk import _apply_iss_fallback
from riescue.lib.toolchain.toolchain import Toolchain
from riescue.lib.toolchain.tool import Compiler, Disassembler, Spike
from riescue.lib.toolchain.whisper import Whisper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_args(first_pass_iss=None, disable_pass=False):
    """Build a minimal argparse Namespace mirroring what ctk run_cli() produces."""
    ns = argparse.Namespace()
    ns.first_pass_iss = first_pass_iss
    ns.disable_pass = disable_pass
    return ns


def _make_toolchain(whisper=True, spike=True):
    """Build a Toolchain with mock ISS objects as requested."""
    tc = MagicMock(spec=Toolchain)
    tc.whisper = MagicMock(spec=Whisper) if whisper else None
    tc.spike = MagicMock(spec=Spike) if spike else None
    return tc


# ---------------------------------------------------------------------------
# No explicit --first_pass_iss flag
# ---------------------------------------------------------------------------


class TestNoIssFlag:
    def test_whisper_only_sets_whisper_and_disable_pass(self):
        args = _make_args()
        tc = _make_toolchain(whisper=True, spike=False)
        _apply_iss_fallback(args, tc)
        assert args.first_pass_iss == "whisper"
        assert args.disable_pass is True

    def test_spike_only_sets_spike_and_disable_pass(self):
        args = _make_args()
        tc = _make_toolchain(whisper=False, spike=True)
        _apply_iss_fallback(args, tc)
        assert args.first_pass_iss == "spike"
        assert args.disable_pass is True

    def test_neither_available_raises(self):
        args = _make_args()
        tc = _make_toolchain(whisper=False, spike=False)
        with pytest.raises(SystemExit):
            _apply_iss_fallback(args, tc)

    def test_both_available_no_change(self):
        args = _make_args()
        tc = _make_toolchain(whisper=True, spike=True)
        _apply_iss_fallback(args, tc)
        assert args.first_pass_iss is None  # unchanged — two-pass flow
        assert args.disable_pass is False  # unchanged


# ---------------------------------------------------------------------------
# Explicit --first_pass_iss flag
# ---------------------------------------------------------------------------


class TestExplicitIssFlag:
    def test_whisper_flag_whisper_available_sets_disable_pass(self):
        args = _make_args(first_pass_iss="whisper")
        tc = _make_toolchain(whisper=True, spike=False)
        _apply_iss_fallback(args, tc)
        assert args.first_pass_iss == "whisper"
        assert args.disable_pass is True

    def test_spike_flag_spike_available_sets_disable_pass(self):
        args = _make_args(first_pass_iss="spike")
        tc = _make_toolchain(whisper=False, spike=True)
        _apply_iss_fallback(args, tc)
        assert args.first_pass_iss == "spike"
        assert args.disable_pass is True

    def test_whisper_flag_whisper_unavailable_raises(self):
        args = _make_args(first_pass_iss="whisper")
        tc = _make_toolchain(whisper=False, spike=True)
        with pytest.raises(SystemExit):
            _apply_iss_fallback(args, tc)

    def test_spike_flag_spike_unavailable_raises(self):
        args = _make_args(first_pass_iss="spike")
        tc = _make_toolchain(whisper=True, spike=False)
        with pytest.raises(SystemExit):
            _apply_iss_fallback(args, tc)

    def test_whisper_flag_both_available_sets_disable_pass(self):
        args = _make_args(first_pass_iss="whisper")
        tc = _make_toolchain(whisper=True, spike=True)
        _apply_iss_fallback(args, tc)
        assert args.first_pass_iss == "whisper"
        assert args.disable_pass is True

    def test_spike_flag_both_available_sets_disable_pass(self):
        args = _make_args(first_pass_iss="spike")
        tc = _make_toolchain(whisper=True, spike=True)
        _apply_iss_fallback(args, tc)
        assert args.first_pass_iss == "spike"
        assert args.disable_pass is True


# ---------------------------------------------------------------------------
# Toolchain.from_clargs(build_both=True) — optional ISS construction
# ---------------------------------------------------------------------------


class TestToolchainOptionalIss:
    def _make_clargs(self):
        ns = argparse.Namespace()
        ns.whisper_path = None
        ns.whisper_config_json = None
        ns.spike_path = None
        ns.spike_args = []
        ns.spike_isa = None
        ns.third_party_spike = False
        ns.spike_max_instr = 2000000
        ns.rv_gcc = None
        ns.march = None
        ns.compiler_path = None
        ns.compiler_args = []
        ns.disassembler_path = None
        return ns

    def test_spike_not_found_returns_none_spike(self):
        args = self._make_clargs()
        with (
            patch("riescue.lib.toolchain.toolchain.Whisper.from_clargs") as mock_w,
            patch("riescue.lib.toolchain.toolchain.Spike.from_clargs", side_effect=FileNotFoundError("spike not found")),
            patch("riescue.lib.toolchain.toolchain.Compiler.from_clargs", return_value=MagicMock()),
            patch("riescue.lib.toolchain.toolchain.Disassembler.from_clargs", return_value=MagicMock()),
        ):
            mock_w.return_value = MagicMock(spec=Whisper)
            tc = Toolchain.from_clargs(args, build_both=True)
        assert tc.spike is None
        assert tc.whisper is not None

    def test_whisper_not_found_returns_none_whisper(self):
        args = self._make_clargs()
        with (
            patch("riescue.lib.toolchain.toolchain.Whisper.from_clargs", side_effect=FileNotFoundError("whisper not found")),
            patch("riescue.lib.toolchain.toolchain.Spike.from_clargs") as mock_s,
            patch("riescue.lib.toolchain.toolchain.Compiler.from_clargs", return_value=MagicMock()),
            patch("riescue.lib.toolchain.toolchain.Disassembler.from_clargs", return_value=MagicMock()),
        ):
            mock_s.return_value = MagicMock(spec=Spike)
            tc = Toolchain.from_clargs(args, build_both=True)
        assert tc.whisper is None
        assert tc.spike is not None

    def test_neither_found_returns_both_none(self):
        args = self._make_clargs()
        with (
            patch("riescue.lib.toolchain.toolchain.Whisper.from_clargs", side_effect=FileNotFoundError),
            patch("riescue.lib.toolchain.toolchain.Spike.from_clargs", side_effect=FileNotFoundError),
            patch("riescue.lib.toolchain.toolchain.Compiler.from_clargs", return_value=MagicMock()),
            patch("riescue.lib.toolchain.toolchain.Disassembler.from_clargs", return_value=MagicMock()),
        ):
            tc = Toolchain.from_clargs(args, build_both=True)
        assert tc.whisper is None
        assert tc.spike is None

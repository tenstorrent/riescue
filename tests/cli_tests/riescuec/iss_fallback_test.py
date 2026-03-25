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

Also covers:
  - TpArgsAdapter.apply() uses Toolchain.from_clargs(build_both=True) so a
    missing spike/whisper does not crash before fallback logic can run.
  - experimental_toolchain_from_args() likewise uses build_both=True.
"""

import argparse
import pytest
from unittest.mock import MagicMock, patch

from riescue.ctk import _apply_iss_fallback
from riescue.lib.toolchain.toolchain import Toolchain
from riescue.lib.toolchain.tool import Compiler, Disassembler, Spike
from riescue.lib.toolchain.whisper import Whisper
from riescue.compliance.config.adapters.tp_args_adapter import TpArgsAdapter
from riescue.compliance.config.experimental_toolchain import experimental_toolchain_from_args


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


# ---------------------------------------------------------------------------
# TpArgsAdapter — must not crash when spike or whisper is missing
# ---------------------------------------------------------------------------


class TestTpArgsAdapterOptionalIss:
    """
    TpArgsAdapter.apply() previously called Spike.from_clargs() and
    Whisper.from_clargs() directly, crashing with FileNotFoundError before
    the caller's fallback logic could run.  Now it delegates to
    Toolchain.from_clargs(build_both=True) which catches FileNotFoundError
    and sets the missing ISS to None.
    """

    def _make_minimal_args(self):
        ns = argparse.Namespace()
        ns.isa = "rv64imf"
        ns.test_plan_name = "test"
        ns.cpuconfig = None
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
        # FeatMgrBuilder minimal args
        ns.sv39 = False
        ns.sv48 = False
        ns.sv57 = False
        ns.paging_modes = []
        ns.priv_modes = []
        return ns

    def test_spike_missing_does_not_raise(self):
        """Adapter must not crash when spike is absent."""
        args = self._make_minimal_args()
        with (
            patch("riescue.lib.toolchain.toolchain.Whisper.from_clargs", return_value=MagicMock(spec=Whisper)),
            patch("riescue.lib.toolchain.toolchain.Spike.from_clargs", side_effect=FileNotFoundError("spike not found")),
            patch("riescue.lib.toolchain.toolchain.Compiler.from_clargs", return_value=MagicMock()),
            patch("riescue.lib.toolchain.toolchain.Disassembler.from_clargs", return_value=MagicMock()),
        ):
            builder = MagicMock()
            builder.cfg = MagicMock()
            builder.featmgr_builder = MagicMock()
            result = TpArgsAdapter().apply(builder, args)
        # Should complete without exception; spike slot is None
        assert builder.cfg.toolchain.spike is None
        assert builder.cfg.toolchain.whisper is not None

    def test_whisper_missing_does_not_raise(self):
        """Adapter must not crash when whisper is absent."""
        args = self._make_minimal_args()
        with (
            patch("riescue.lib.toolchain.toolchain.Whisper.from_clargs", side_effect=FileNotFoundError("whisper not found")),
            patch("riescue.lib.toolchain.toolchain.Spike.from_clargs", return_value=MagicMock(spec=Spike)),
            patch("riescue.lib.toolchain.toolchain.Compiler.from_clargs", return_value=MagicMock()),
            patch("riescue.lib.toolchain.toolchain.Disassembler.from_clargs", return_value=MagicMock()),
        ):
            builder = MagicMock()
            builder.cfg = MagicMock()
            builder.featmgr_builder = MagicMock()
            result = TpArgsAdapter().apply(builder, args)
        assert builder.cfg.toolchain.whisper is None
        assert builder.cfg.toolchain.spike is not None


# ---------------------------------------------------------------------------
# experimental_toolchain_from_args — must not crash when spike or whisper is missing
# ---------------------------------------------------------------------------


class TestExperimentalToolchainOptionalIss:
    """
    experimental_toolchain_from_args() previously called Spike.from_clargs()
    and Whisper.from_clargs() directly.  Now it delegates to
    Toolchain.from_clargs(build_both=True).
    """

    def _make_minimal_args(self):
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
        ns.compiler_opts = []
        ns.test_equates = []
        ns.disassembler_path = None
        ns.experimental_compiler = None
        ns.experimental_objdump = None
        # experimental flags — all off
        ns.rv_zvknhb_experimental = False
        ns.rv_zvkg_experimental = False
        ns.rv_zvbc_experimental = False
        ns.rv_zvfbfwma_experimental = False
        ns.rv_zvfbfmin_experimental = False
        ns.rv_zfbfmin_experimental = False
        ns.rv_zvbb_experimental = False
        return ns

    def test_spike_missing_does_not_raise(self):
        args = self._make_minimal_args()
        with (
            patch("riescue.lib.toolchain.toolchain.Whisper.from_clargs", return_value=MagicMock(spec=Whisper)),
            patch("riescue.lib.toolchain.toolchain.Spike.from_clargs", side_effect=FileNotFoundError("spike not found")),
            patch("riescue.lib.toolchain.toolchain.Compiler.from_clargs", return_value=MagicMock()),
            patch("riescue.lib.toolchain.toolchain.Disassembler.from_clargs", return_value=MagicMock()),
            patch("riescue.compliance.config.experimental_toolchain.Compiler.from_clargs", return_value=MagicMock()),
            patch("riescue.compliance.config.experimental_toolchain.Disassembler.from_clargs", return_value=MagicMock()),
        ):
            tc = experimental_toolchain_from_args(args)
        assert tc.spike is None
        assert tc.whisper is not None

    def test_whisper_missing_does_not_raise(self):
        args = self._make_minimal_args()
        with (
            patch("riescue.lib.toolchain.toolchain.Whisper.from_clargs", side_effect=FileNotFoundError("whisper not found")),
            patch("riescue.lib.toolchain.toolchain.Spike.from_clargs", return_value=MagicMock(spec=Spike)),
            patch("riescue.lib.toolchain.toolchain.Compiler.from_clargs", return_value=MagicMock()),
            patch("riescue.lib.toolchain.toolchain.Disassembler.from_clargs", return_value=MagicMock()),
            patch("riescue.compliance.config.experimental_toolchain.Compiler.from_clargs", return_value=MagicMock()),
            patch("riescue.compliance.config.experimental_toolchain.Disassembler.from_clargs", return_value=MagicMock()),
        ):
            tc = experimental_toolchain_from_args(args)
        assert tc.whisper is None
        assert tc.spike is not None

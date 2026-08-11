# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class InstallExcpHandlerTests(BaseRiescuedTest):
    """
    CLI tests for the runtime-installed exception handler macros
    (``OS_INSTALL_EXCP_HANDLER`` / ``OS_UNINSTALL_EXCP_HANDLER``).

    Single-core: an armed handler with a cause match takes the custom handler;
    cause mismatch, arming for a different cause, uninstall, and a wrong expected
    mode (mode gate) all fall back to the original exception path.
    MP: the arming state is hart-local, so only the hart that armed the handler
    takes it.
    Paged S-mode: the M-mode dispatch relocates the stored handler VA to a PA
    before jumping (M-mode instruction fetches are never translated).
    """

    def test_install_excp_handler(self):
        testname = "dtest_framework/tests/non_instr_tests/install_excp_handler.s"
        args = ["--run_iss", "--deleg_excp_to=machine"]
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_install_excp_handler_vectored_mode(self):
        """Verify the installed-handler dispatch works when mtvec is in vectored MODE.

        RISC-V spec: exceptions always dispatch to mtvec BASE regardless of MODE;
        only interrupts use BASE + 4*cause in vectored mode. The fixture sets
        SET_VECTORED_INTERRUPTS before triggering the exception to prove the
        dispatch at exception_path runs identically in both modes."""
        testname = "dtest_framework/tests/non_instr_tests/install_excp_handler.s"
        args = ["--run_iss", "--deleg_excp_to=machine", "-teq", "USE_VECTORED_MODE=1"]
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_install_excp_handler_mp(self):
        testname = "dtest_framework/tests/non_instr_tests/install_excp_handler_mp.s"
        args = ["--run_iss", "--deleg_excp_to=machine"]
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_install_excp_handler_paged_smode(self):
        """M-mode dispatch must fetch the handler by PA when paging is enabled.

        The test runs S-mode with sv39 paging, so OS_INSTALL_EXCP_HANDLER stores a
        virtual address. With --deleg_excp_to=machine the ebreak lands in the
        M-mode handler, whose dispatch must relocate VA -> PA (.code bases) before
        jumping; without relocation the handler fetch would go to the wrong address.
        """
        testname = "dtest_framework/tests/non_instr_tests/install_excp_handler_s.s"
        args = ["--run_iss", "--deleg_excp_to=machine"]
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)


if __name__ == "__main__":
    unittest.main()

# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from riescue.compliance.test_plan.types import TextSegment, TextBlock, TestCase


class TestHarness:
    """
    Provides boilerplate test harness code for a given test
    """

    def add_test_harness(self, test_segment: TextSegment) -> TextSegment:
        """
        Adds test harness code. Modifies test segment in place.

        Each test needs a test_setup, test_cleanup, and local_test_failed
        """
        new_blocks = [
            self._test_setup(),
            *test_segment.blocks,
            self._post_test(),
        ]
        test_segment.blocks = new_blocks
        return test_segment

    def test_passed(self, test_name: str) -> TextBlock:
        """
        Returns a subroutine that jumps to test passed
        """
        return TextBlock(label=f"{test_name}_passed", text=self._jump_to_test_passed())

    def _test_setup(self) -> TestCase:
        """
        Generates test setup code. For now just jumps to test passed
        """

        return TestCase([TextBlock(label="test_setup", text=self._jump_to_test_passed())])

    def _post_test(self) -> TestCase:
        """
        Generates post test code. For now just jumps to test passed
        """
        return TestCase(
            [
                self._test_cleanup(),
                self._local_test_failed(),
            ]
        )

    def _test_cleanup(self) -> TextBlock:
        """
        Generates test cleanup code. For now just jumps to test passed
        """
        return TextBlock(label="test_cleanup", text=self._jump_to_test_passed())

    def _local_test_failed(self) -> TextBlock:
        """
        Local test failed subroutine, lets actions jump to failure using j test_failed
        """
        return TextBlock(
            label="local_test_failed",
            text=[
                "li t0, failed_addr",
                "ld t1, 0(t0)",
                "jalr ra, 0(t1)",
            ],
        )

    # def _local_test_failed(self) -> TextBlock:
    #     """
    #     Local test failed subroutine, lets actions jump to failure using j test_failed
    #     """
    #     return TextBlock(
    #         label="test_failed_mem",
    #         text=[
    #             ".dword tohost"
    #         ],
    # )

    def _jump_to_test_passed(self) -> list[str]:
        "Creates jump to passed address instructions"
        return [
            "li t0, passed_addr",
            "ld t1, 0(t0)",
            "jalr ra, 0(t1)",
        ]

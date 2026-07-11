# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from riescue.dtest_framework.config import FeatMgr
from riescue.dtest_framework.generator.assembly_writer import AssemblyWriter
from riescue.lib.enums import HookPoint


class HookPointEnumTest(unittest.TestCase):
    """The two new discrete-test hook points exist with the expected values."""

    def test_discrete_test_hookpoint_values(self):
        self.assertEqual(HookPoint.PRE_DISCRETE_TEST.value, "pre_discrete_test")
        self.assertEqual(HookPoint.POST_DISCRETE_TEST.value, "post_discrete_test")

    def test_call_hook_returns_registered_code(self):
        fm = FeatMgr()
        fm.register_hook(HookPoint.PRE_DISCRETE_TEST, lambda _: "# pre")
        fm.register_hook(HookPoint.POST_DISCRETE_TEST, lambda _: "# post")
        self.assertEqual(fm.call_hook(HookPoint.PRE_DISCRETE_TEST), "# pre")
        self.assertEqual(fm.call_hook(HookPoint.POST_DISCRETE_TEST), "# post")

    def test_call_hook_joins_multiple_hooks_with_newline(self):
        fm = FeatMgr()
        fm.register_hook(HookPoint.PRE_DISCRETE_TEST, lambda _: "# a")
        fm.register_hook(HookPoint.PRE_DISCRETE_TEST, lambda _: "# b")
        self.assertEqual(fm.call_hook(HookPoint.PRE_DISCRETE_TEST), "# a\n# b")

    def test_call_hook_unregistered_returns_empty(self):
        self.assertEqual(FeatMgr().call_hook(HookPoint.PRE_DISCRETE_TEST), "")


class WeaveDiscreteTestHooksTest(unittest.TestCase):
    """
    Tests for the pure text weaver ``AssemblyWriter._weave_discrete_test_hooks``.

    Structure mirrors a real test section: a ``test_setup`` block, two discrete tests
    (the first with two ``;#test_passed()`` pass paths), and a ``test_cleanup`` block,
    followed by a data section. Both ``test_setup`` and ``test_cleanup`` contain their own
    ``;#test_passed()`` which must NOT be wrapped.
    """

    PRE = "# PRE_HOOK"
    POST = "# POST_HOOK"

    def _section(self):
        return [
            '.section .code, "ax"',
            "test_setup:",
            "    li x1, 0",
            "    ;#test_passed()",  # setup pass -- must NOT get POST
            ";#discrete_test(test=test01)",
            "test01:",  # PRE goes right after this label
            "    nop",
            "    ;#test_passed()",  # POST goes right before this
            "    li t0, 1",
            "    ;#test_passed()  # second pass path",  # POST goes right before this too
            ";#discrete_test(test=test02)",
            "test02:",  # PRE goes right after this label
            "    addi x0, x0, 0",
            "    ;#test_passed()",  # POST goes right before this
            "test_cleanup:",  # region ends here
            "    li x1, 2",
            "    ;#test_passed()",  # cleanup pass -- must NOT get POST
            ".section .data",
            "my_data:",
            "    .dword 0x1",
        ]

    def _weave(self, section=None, pre=None, post=None):
        return AssemblyWriter._weave_discrete_test_hooks(
            self._section() if section is None else section,
            self.PRE if pre is None else pre,
            self.POST if post is None else post,
        )

    def test_pre_inserted_immediately_after_each_label(self):
        result = self._weave()
        self.assertEqual(result.count(self.PRE + "\n"), 2, "one PRE per discrete test")
        for label in ("test01:", "test02:"):
            idx = result.index(label)
            self.assertEqual(result[idx + 1], self.PRE + "\n", f"PRE must be right after {label}")

    def test_post_inserted_immediately_before_every_test_passed(self):
        result = self._weave()
        self.assertEqual(result.count(self.POST + "\n"), 3, "one POST per pass path in a discrete test")
        for i, line in enumerate(result):
            if line == self.POST + "\n":
                self.assertTrue(
                    result[i + 1].strip().startswith(";#test_passed"),
                    "POST must be immediately before a ;#test_passed() line",
                )

    def test_setup_and_cleanup_are_not_wrapped(self):
        result = self._weave()
        # All five original ;#test_passed() lines survive (setup x1, test01 x2, test02 x1, cleanup x1);
        # only the three inside discrete tests get POST.
        passed_idxs = [i for i, l in enumerate(result) if l.strip().startswith(";#test_passed")]
        self.assertEqual(len(passed_idxs), 5)
        preceded_by_post = sum(1 for i in passed_idxs if result[i - 1] == self.POST + "\n")
        self.assertEqual(preceded_by_post, 3, "setup/cleanup ;#test_passed() must not get POST")
        # No PRE bleeds into setup/cleanup (which have no ;#discrete_test directive).
        self.assertEqual(result.count(self.PRE + "\n"), 2)

    def test_discrete_debug_test_is_not_wrapped(self):
        section = [
            ";#discrete_debug_test",
            "debug_body:",
            "    nop",
            "    ;#test_passed()",
        ]
        result = self._weave(section=section)
        self.assertEqual(result, section, ";#discrete_debug_test must be left untouched")

    def test_only_pre_registered(self):
        result = self._weave(post="")
        self.assertEqual(result.count(self.PRE + "\n"), 2)
        self.assertNotIn(self.POST + "\n", result)

    def test_only_post_registered(self):
        result = self._weave(pre="")
        self.assertEqual(result.count(self.POST + "\n"), 3)
        self.assertNotIn(self.PRE + "\n", result)


class WrapDiscreteTestsMethodTest(unittest.TestCase):
    """
    Tests for the instance method ``AssemblyWriter._wrap_discrete_tests`` -- verifies the
    enum -> ``call_hook`` -> weaver path end to end. ``AssemblyWriter.__new__`` is used to build a
    bare instance (only ``featmgr`` is needed), avoiding a full Pool/Runtime construction.
    """

    def _writer(self):
        writer = AssemblyWriter.__new__(AssemblyWriter)
        writer.featmgr = FeatMgr()
        return writer

    def _section(self):
        return [
            '.section .code, "ax"',
            ";#discrete_test(test=test01)",
            "test01:",
            "    nop",
            "    ;#test_passed()",
            "test_cleanup:",
            "    ;#test_passed()",
        ]

    def test_noop_returns_input_unchanged_when_no_hooks(self):
        writer = self._writer()
        section = self._section()
        result = writer._wrap_discrete_tests(section)
        self.assertIs(result, section, "no hooks registered -> return the input list untouched")

    def test_uses_registered_hooks(self):
        writer = self._writer()
        writer.featmgr.register_hook(HookPoint.PRE_DISCRETE_TEST, lambda _: "# pre")
        writer.featmgr.register_hook(HookPoint.POST_DISCRETE_TEST, lambda _: "# post")
        result = writer._wrap_discrete_tests(self._section())

        # PRE right after the test01 label
        idx = result.index("test01:")
        self.assertEqual(result[idx + 1], "# pre\n")
        # Exactly one PRE and one POST (the cleanup ;#test_passed() is excluded)
        self.assertEqual(result.count("# pre\n"), 1)
        self.assertEqual(result.count("# post\n"), 1)
        # POST is right before the discrete test's ;#test_passed(), not the cleanup one
        post_idx = result.index("# post\n")
        self.assertTrue(result[post_idx + 1].strip().startswith(";#test_passed"))
        # The cleanup pass is not preceded by POST
        cleanup_idx = result.index("test_cleanup:")
        self.assertNotIn("# post\n", result[cleanup_idx:])


if __name__ == "__main__":
    unittest.main()

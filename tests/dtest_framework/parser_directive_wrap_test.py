# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Multi-line ``;#directive(...)`` folding (Parser._join_wrapped_directives).

Every ``parse_*`` matches its directive with a single-line regex, so a wrapped directive used to match
nothing and be dropped silently -- which is why all six ``;#pma_hint`` in test_pma_hint.s never ran.
"""

import pathlib
import tempfile
import unittest

from riescue.dtest_framework.parser import Parser
from riescue.dtest_framework.pool import Pool


class DirectiveWrapTest(unittest.TestCase):
    def _hints(self, text: str) -> dict:
        with tempfile.NamedTemporaryFile("w", suffix=".s", delete=False) as handle:
            handle.write(text)
            path = handle.name
        self.addCleanup(pathlib.Path(path).unlink)
        parser = Parser(filename=pathlib.Path(path), pool=Pool())
        parser.parse()
        return parser.pool.get_parsed_pma_hints()

    def test_wrapped_directive_is_parsed(self):
        hints = self._hints(";#pma_hint(name=w,\n    memory_types=[io],\n    amo_types=[none, swap]\n)\n")
        self.assertEqual(list(hints), ["w"])
        self.assertEqual(hints["w"].memory_types, ["io"])
        self.assertEqual(hints["w"].amo_types, ["none", "swap"])

    def test_single_line_directive_unchanged(self):
        hints = self._hints(";#pma_hint(name=s, memory_types=[io], amo_types=[none])\n")
        self.assertEqual(hints["s"].amo_types, ["none"])

    def test_comment_inside_wrap_is_not_folded_in(self):
        hints = self._hints(";#pma_hint(name=c,\n    # explain\n    memory_types=[io]\n)\n")
        self.assertEqual(hints["c"].memory_types, ["io"])

    def test_prose_directive_with_stray_paren_is_left_alone(self):
        """;#test.summary text may carry an unbalanced '(' -- it must not swallow the next directive"""
        hints = self._hints(";#test.summary uses a 2MB page (largest that fits\n;#pma_hint(name=p, memory_types=[io])\n")
        self.assertEqual(list(hints), ["p"])

    def test_unterminated_directive_does_not_swallow_the_file(self):
        hints = self._hints(';#pma_hint(name=u,\n    memory_types=[io]\n.section .code, "ax"\n')
        self.assertEqual(list(hints), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

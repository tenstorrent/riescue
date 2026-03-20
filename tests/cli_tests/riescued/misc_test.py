# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import unittest

from tests.cli_tests.riescued.base_riescued import BaseRiescuedTest


class MiscellaneousTests(BaseRiescuedTest):
    """
    Miscellaneous tests not covered by other test files
    """

    def test_test_template(self):
        "Test template"
        args = ["--run_iss", "--test_priv_mode", "super", "--test_paging_mode", "sv57"]
        testname = "riescue/dtest_framework/tests/test_template.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_check_excp(self):
        "Exception checking test"
        args = ["--run_iss", "--test_priv_mode", "user"]
        testname = "riescue/dtest_framework/tests/check_excp.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    # def test_exceptions(self):
    #     "Exceptions test"
    #     args = ["--run_iss", "--test_paging_mode", "sv39", "--test_priv_mode", "super"]
    #     testname = "riescue/dtest_framework/tests/exceptions.s"
    #     self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_svinval(self):
        "Svinval extension test"
        args = ["--run_iss", "--test_paging_mode", "sv57"]
        testname = "riescue/dtest_framework/tests/svinval.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_vs_gstage_svadu(self):
        "Virtual supervisor G-stage with Svadu test"
        args = ["--run_iss", "--test_paging_mode", "sv57", "--test_paging_g_mode", "sv57", "--test_env", "virtualized", "--test_priv_mode", "super"]
        testname = "riescue/dtest_framework/tests/test_vs_gstage_svadu.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    # def test_wysiwyg(self):
    #     "WYSIWYG test"
    #     args = ["--run_iss", "--wysiwyg"]
    #     testname = "riescue/dtest_framework/tests/test_wysiwyg.s"
    #     self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_fact(self):
        "Factorial test"
        args = ["--run_iss", "--test_paging_mode", "sv57"]
        testname = "riescue/dtest_framework/tests/fact.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_fib(self):
        "Fibonacci test"
        args = ["--run_iss", "--test_paging_mode", "sv57"]
        testname = "riescue/dtest_framework/tests/fib.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_bfs(self):
        "Breadth-first search test"
        args = ["--run_iss", "--test_paging_mode", "sv57"]
        testname = "riescue/dtest_framework/tests/bfs.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_dfs(self):
        "Depth-first search test"
        args = ["--run_iss", "--test_paging_mode", "sv57"]
        testname = "riescue/dtest_framework/tests/dfs.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    # def test_dijkstras(self):
    #     "Dijkstra's algorithm test"
    #     args = ["--run_iss", "--test_paging_mode", "sv57"]
    #     testname = "riescue/dtest_framework/tests/dijkstras.s"
    #     self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_floyd_warshall(self):
        "Floyd-Warshall algorithm test"
        args = ["--run_iss", "--test_paging_mode", "sv57"]
        testname = "riescue/dtest_framework/tests/floyd_warshall.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_prim_tree(self):
        "Prim's algorithm test"
        args = ["--run_iss", "--test_paging_mode", "sv57"]
        testname = "riescue/dtest_framework/tests/prim_tree.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_union_find(self):
        "Union-find algorithm test"
        args = ["--run_iss", "--test_paging_mode", "sv57"]
        testname = "riescue/dtest_framework/tests/union_find.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_mp_2p_petersons(self):
        "2-processor Peterson's algorithm test"
        args = ["--run_iss", "--deleg_excp_to", "machine", "--repeat_times", "2", "--test_priv_mode", "machine", "--mp", "on", "--num_cpus", "2"]
        testname = "riescue/dtest_framework/tests/mp_2p_petersons.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)

    def test_mp_5p_lr_sc(self):
        "5-processor load-reserved/store-conditional test"
        args = ["--run_iss", "--deleg_excp_to", "machine", "--repeat_times", "2", "--test_priv_mode", "machine", "--mp", "on", "--num_cpus", "5"]
        testname = "riescue/dtest_framework/tests/mp_5p_lr_sc.s"
        self.run_riescued(testname=testname, cli_args=args, iterations=self.iterations)


if __name__ == "__main__":
    unittest.main(verbosity=2)

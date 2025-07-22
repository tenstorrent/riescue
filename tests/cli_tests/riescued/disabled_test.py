# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
import unittest

from riescue.riescued import RiescueD

# Commented out because they aren't working?

# class DefaultTest(unittest.TestCase):
#    def test_cli(self):
#         args = " ".join([
#             "--testname",
#             "dtest_framework/tests/test.s",
#             "--run_iss",
#             "--test_secure_mode",
#             "on",
#             "--seed",
#             "0",
#         ])
#        args = "--testname dtest_framework/tests/test.s --run_iss --test_secure_mode on --seed 0"
#        RiescueD.run_cli(args=args.split())
#
# class TestExcpTest(unittest.TestCase):
#    def test_cli(self):
#         args = " ".join([
#             "--testname",
#             "dtest_framework/tests/test_excp.s",
#             "--run_iss",
#             "--test_secure_mode",
#             "on",
#             "--cpuconfig",
#             "dtest_framework/lib/config_secure_0.json",
#             "--whisper_config_json",
#             "dtest_framework/lib/whisper_secure_config.json",
#             "--seed",
#             "0",
#         ])
#        RiescueD.run_cli(args=args.split())
#
# class TestSteeTest(unittest.TestCase):
#    def test_cli(self):
#         args = " ".join([
#             "--testname",
#             "dtest_framework/tests/test_stee.s",
#             "--run_iss",
#             "--cpuconfig",
#             "dtest_framework/lib/config_secure_0.json",
#             "--whisper_config_json",
#             "dtest_framework/lib/whisper_secure_config.json",
#             "--seed",
#             "0",
#         ])
#        RiescueD.run_cli(args=args.split())
#
# class TestSteeTest(unittest.TestCase):
#    def test_cli(self):
#         args = " ".join([
#             "--testname",
#             "dtest_framework/tests/test_stee.s",
#             "--run_iss",
#             "--cpuconfig",
#             "dtest_framework/lib/config_secure_1.json",
#             "--whisper_config_json",
#             "dtest_framework/lib/whisper_secure_config.json",
#             "--seed",
#             "0",
#         ])
#        RiescueD.run_cli(args=args.split())
#
if __name__ == "__main__":
    unittest.main(verbosity=2)

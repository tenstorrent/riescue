# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
import unittest
import random

from riescue.lib.rand import RandNum


class RandNumTest(unittest.TestCase):
    def test_random_with_seed(self):
        "Want to be sure that RandNum is setting seed"
        seed = 0
        rand = RandNum(seed=seed)
        expected_float = 0.8444218515250481
        rand_float = rand.random()
        self.assertEqual(expected_float, rand_float, f"With seed {seed}, expected float is {expected_float}")

    def test_random_with_seed_with_random_random_calls(self):
        "Check that global random isn't getting changed by RandNum"
        seed = 0
        expected_float = 0.8444218515250481

        global_random = random.Random(seed)
        global_random_float = global_random.random()
        self.assertEqual(expected_float, global_random_float, f"With seed {seed}, expected float is {global_random_float}")

        rand = RandNum(seed=seed)
        # This shouldn't affect the RandNum instance's random
        random.seed(seed)
        global_random_float = random.random()
        rand_float = rand.random()
        self.assertEqual(expected_float, rand_float, f"With seed {seed}, expected float is {expected_float}")

        global_random = random.Random(seed)
        self.assertEqual(expected_float, global_random_float, f"With seed {seed}, expected float is {global_random_float}")

    def test_indepedent_random_instances(self):
        """
        Test that two independent RandNum instances are independent
        """
        seed = 0
        rand1 = RandNum(seed=seed)
        rand2 = RandNum(seed=seed)

        expected_float = 0.8444218515250481
        rand1_float = rand1.random()
        self.assertEqual(expected_float, rand1_float, f"With seed {seed}, expected float is {expected_float}")
        # Additional call should advance rand1 seed, but not rand2
        rand1_float = rand1.random()

        rand2_float = rand2.random()
        self.assertEqual(expected_float, rand2_float, f"rand1 and rand2 should be independent, but rand2 returned {rand2_float} instead of {expected_float}")

        # Using the same starting seed should still produce the same float
        rand2_float = rand2.random()
        self.assertEqual(rand1_float, rand2_float, "rand1 and rand2 using same seed should produce same float")

    def test_random_in_range(self):
        seed = 1
        rand = RandNum(seed=seed)
        range_lo, range_hi = 10, 20
        rand_int = rand.random_in_range(range_lo, range_hi)
        self.assertTrue(range_lo <= rand_int < range_hi, f"Random integer {rand_int} not in range [{range_lo}, {range_hi})")

    def test_random_index_in(self):
        seed = 2
        rand = RandNum(seed=seed)
        test_list = [10, 20, 30, 40]
        index = rand.random_index_in(test_list)
        self.assertTrue(0 <= index < len(test_list), f"Random index {index} not valid for list of size {len(test_list)}")

    def test_random_entry_in(self):
        seed = 3
        rand = RandNum(seed=seed)
        test_list = [10, 20, 30, 40]
        entry = rand.random_entry_in(test_list)
        self.assertIn(entry, test_list, f"Random entry {entry} not in list {test_list}")

    def test_sample(self):
        seed = 4
        rand = RandNum(seed=seed)
        test_list = [1, 2, 3, 4, 5]
        num_samples = 3
        sample = rand.sample(test_list, num_samples)
        self.assertEqual(len(sample), num_samples, f"Sample size {len(sample)} does not match requested size {num_samples}")
        for item in sample:
            self.assertIn(item, test_list, f"Sampled item {item} not in original list {test_list}")

    def test_random_nbit(self):
        seed = 5
        rand = RandNum(seed=seed)
        bits = 4
        rand_int = rand.random_nbit(bits)
        self.assertTrue(0 <= rand_int < (1 << bits), f"Random n-bit integer {rand_int} not in range [0, {1 << bits})")

    def test_with_probability_of(self):
        seed = 6
        rand = RandNum(seed=seed)
        probability = 50
        result = rand.with_probability_of(probability)
        self.assertIn(result, [True, False], f"Result {result} is not a boolean")

    def test_random_choice_weighted(self):
        seed = 7
        rand = RandNum(seed=seed)
        weights = {"a": 1, "b": 2, "c": 3}
        choice = rand.random_choice_weighted(weights)
        self.assertIn(choice, weights.keys(), f"Weighted choice {choice} not in keys {list(weights.keys())}")

    def test_shuffle(self):
        seed = 8
        rand = RandNum(seed=seed)
        test_list = [1, 2, 3, 4, 5]
        original_list = test_list[:]
        rand.shuffle(test_list)
        self.assertCountEqual(test_list, original_list, f"Shuffled list {test_list} does not contain the same elements as original {original_list}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

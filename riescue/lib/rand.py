# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import random
import abc
from typing import TypeVar, Optional, Sequence

T = TypeVar("T")


def initial_random_seed() -> int:
    """
    Generate a random seed for the random number generator. Used when random seed is not provided.
    Ensures classes aren't importing random directly.
    """
    return random.randrange(2**32)


class RandNum:
    """Random number generator with configurable distributions.

    Extends Python's random module to support multiple probability distributions with a consistent interface.

    :param seed: Seed value for reproducible randomness, required to reinforce creatingonce and passing around instance
    :type seed: int
    :param distribution: Probability distribution to use, defaults to "uniform"
    :type distribution: str, optional

    Supported distributions:
      * "uniform" - Uniform distribution in range [0.0, 1.0)
      * "triangular" - Triangular distribution with mode=0.5
      * "beta" - Beta distribution with alpha=2.0, beta=2.0
      * "exponential" - Exponential distribution with lambda=1.0
      * "log" - Log-normal distribution with mu=-2.0, sigma=0.5
      * "gaussian" - Gaussian (normal) distribution with mu=-0.5, sigma=0.5

    :raises ValueError: If an invalid distribution type is specified

    .. code-block:: python

        rand = RandNum(seed=42)
        rand.random_in_range(1, 10)  # Returns 7
    """

    def __init__(self, seed, distribution="uniform"):
        self.rand = random.Random(seed)
        self._seed = seed
        if seed is not None:
            self.seed_set = True
        else:
            self.seed_set = False

        self.distribution = DistributionFactory.create(distribution, self.rand)

    def seed(self, seed: int):
        """Set the internal random number generator's seed. Can only set seed once.

        :param seed: Seed value for reproducible randomness
        :type seed: int
        :returns: None

        :raises ValueError: If seed is already set
        """
        if self.seed_set:
            raise ValueError("Seed already set")
        self.seed_set = True
        self._seed = seed  # Book keeping for get_seed()
        self.rand.seed(seed)

    def get_seed(self) -> int:
        """
        Get the current seed value.
        """
        if self._seed is None:
            raise ValueError("Seed was not set. Call RandNum.seed() first.")
        return self._seed

    def set_master_seed(self, seed) -> None:
        """
        Set the seed for compatibility with older seed method usage.
        """
        print("Warning: Deprecated set_master_seed() method. Initialize RandNum with seed or use RandNum.seed() method. Setting seed")
        self.seed(seed)

    def random(self) -> float:
        """Generate a random float from the chosen distribution.

        :returns: A random float in the range [0.0, 1.0), distributed according
                  to the probability distribution specified during initialization
        :rtype: float
        """
        return self.distribution.random()

    def random_in_range(self, range_lo: int, range_hi: int, range_step=1) -> int:
        """Generate a random integer within the specified range.

        :param range_lo: Lower bound of the range (inclusive)
        :type range_lo: int
        :param range_hi: Upper bound of the range (exclusive)
        :type range_hi: int
        :param range_step: Step size between values, defaults to 1
        :type range_step: int, optional
        :returns: A random integer between range_lo and range_hi - 1,
                  that is divisible by range_step
        :rtype: int

        :raises ValueError: If range_lo, range_hi, or range_step are not integers
        :raises ValueError: If range_hi <= range_lo
        :raises ValueError: If range_step <= 0
        """
        choice = self.distribution.random_in_range(range_lo, range_hi, range_step)
        return choice

    def random_index_in(self, x: list):
        """Return a random valid index from the given list.

        :param x: List to select index from
        :type x: list
        :returns: Random valid index
        :rtype: int
        :raises ValueError: If the list is empty
        """
        if len(x) == 0:
            raise ValueError("Cannot select random index from empty list")
        return self.random_in_range(0, len(x))

    def random_entry_in(self, x: list[T]) -> T:
        """Return a random entry from the given list.

        :param x: List from which to select an element
        :type x: list
        :returns: Randomly selected element from the list
        :raises ValueError: If the list is empty

        .. code-block:: python

            rand = RandNum(seed=42)
            rand.random_entry_in(["apple", "banana", "cherry"])  # Returns 'banana'
        """
        rand_index = self.random_index_in(x)
        return x[rand_index]

    def sample(self, x: list, num_samples: int):
        """
        Return a sample of the specified size from the given list.

        :param x: List to sample from
        :type x: list
        :param num_samples: Number of samples to take
        :type num_samples: int
        :returns: Random sample from list
        :rtype: list
        :raises ValueError: If num_samples > len(x)
        """
        if not x:
            raise ValueError("Cannot sample from empty list")
        if num_samples > len(x):
            raise ValueError(f"Sample size {num_samples} greater than collection size {len(x)}")
        return self.rand.sample(x, num_samples)

    def random_in_bitrange(self, bitrange_lo, bitrange_hi, bitrange_step=1) -> int:
        """Return a random integer using bit manipulation within the specified bit range.

        :param bitrange_lo: Lower bound of the bit range (inclusive)
        :type bitrange_lo: int
        :param bitrange_hi: Upper bound of the bit range (exclusive)
        :type bitrange_hi: int
        :param bitrange_step: Step size for alignment, defaults to 1
        :type bitrange_step: int, optional
        :returns: Random integer with random bit length in the specified range
        :rtype: int

        .. code-block:: python

            rand = RandNum(seed=42)
            rand.random_in_bitrange(3, 8)  # Returns a random 3-7 bit number (e.g., 42)
        """
        choice = self.distribution.random_in_range(bitrange_lo, bitrange_hi)
        return self.rand.getrandbits(choice) & (-bitrange_step)

    def random_nbit(self, bits) -> int:
        """Return a random integer with the specified number of bits.

        :param bits: Number of bits in the generated integer
        :type bits: int
        :returns: Random integer with the specified bit length
        :rtype: int

        .. code-block:: python

            rand = RandNum(seed=42)
            rand.random_nbit(8)  # Returns a random 8-bit number (0-255, e.g., 142)
        """
        return self.rand.getrandbits(bits)

    def get_rand_bits(self, num_bits: int) -> int:
        """
        Return a random integer generated from the specified number of bits.
        """
        return self.rand.getrandbits(num_bits)

    def randint(self, a: int, b: int) -> int:
        """
        Return a random integer between a and b inclusive.
        """
        return self.rand.randint(a, b)

    def percent(self) -> int:
        """
        Return a random integer between 0 and 99 inclusive.
        """
        return self.random_in_range(0, 100)

    def with_probability_of(self, percent) -> bool:
        """Return True with the specified percent likelihood.

        :param percent: Probability percentage (0-100)
        :type percent: int|float
        :returns: True with probability of percent/100, False otherwise
        :rtype: bool

        .. code-block:: python

            rand = RandNum(seed=42)
            rand.with_probability_of(75)  # Returns True with 75% probability
        """
        if self.random_in_range(0, 100) < percent:
            return True

        return False

    def random_key_value(self, d: dict):
        """Return a random key and its corresponding value from the dictionary.

        :param d: Dictionary to select from
        :type d: dict
        :returns: Tuple containing (list with selected key, corresponding value)
        :rtype: tuple(list, any)
        :raises ValueError: If dictionary is empty

        .. code-block:: python

            rand = RandNum(seed=42)
            rand.random_key_value({"a": 1, "b": 2, "c": 3})  # Returns (['b'], 2)
        """
        if not d:
            raise ValueError("Cannot select from empty dictionary")
        key = self.rand.choices(list(d.keys()))
        val = d[key[0]]
        return key, val

    def random_key_in(self, d: dict):
        """Return a list containing a randomly selected key from the dictionary.

        :param d: Dictionary to select from
        :type d: dict
        :returns: List containing a single randomly selected key
        :rtype: list
        :raises ValueError: If dictionary is empty

        .. code-block:: python

            rand = RandNum(seed=42)
            rand.random_key_in({"a": 1, "b": 2, "c": 3})  # Returns ['b']
        """
        if not d:
            raise ValueError("Cannot select from empty dictionary")
        retval = self.rand.choices(list(d.keys()), tuple(d.values()), k=1)
        return retval

    def random_choice_weighted(self, x: dict):
        """Return a randomly selected key from the dictionary, weighted by its value.

        Keys with higher values have a higher probability of being selected.

        :param x: Dictionary with keys as choices and values as weights
        :type x: dict
        :returns: A randomly selected key, with selection probability proportional to its value
        :raises ValueError: If dictionary is empty or contains negative weights

        .. code-block:: python

            rand = RandNum(seed=42)
            rand.random_choice_weighted({"a": 10, "b": 1, "c": 5})  # Returns 'a'
            # 'a' has higher probability due to weight 10
        """
        if not x:
            raise ValueError("Cannot select from empty dictionary")
        return self.rand.choices(list(x.keys()), weights=list(x.values()), k=1)[0]

    def shuffle(self, lst: list):
        """Randomly reorder the elements in the list in-place.

        :param lst: List to be shuffled
        :type lst: list
        :returns: None (shuffles in-place)

        .. code-block:: python

            rand = RandNum(seed=42)
            nums = [1, 2, 3, 4, 5]
            rand.shuffle(nums)  # nums becomes [2, 5, 1, 3, 4]
        """
        self.rand.shuffle(lst)

    def choice(self, x: Sequence[T]) -> T:
        """
        Return a random element from the list x. Uses random.Random.choice()

        :param x: List to select from
        :type x: list
        :returns: Randomly selected element from the list
        :rtype: any
        :raises IndexError: If the list is empty

        .. code-block:: python

            rand = RandNum(seed=42)
            rand.choice([1, 2, 3, 4, 5])  # Returns 3
        """
        return self.rand.choice(x)

    def choices(self, x, weights=None, k=1):
        """
        Return a list of k unique random elements from the list x.  Uses random.Random.choices()

        :param weights: Weights for each element in x, defaults to None
        :type weights: list, optional
        :param k: Number of elements to select, defaults to 1
        :type k: int, optional
        :returns: List of k unique random elements from x
        :rtype: list
        :raises IndexError: If the list is empty
        :raises ValueError: If weights are negative

        .. code-block:: python

            rand = RandNum(seed=42)
            rand.choices([1, 2, 3, 4, 5], weights=[1, 2, 3, 4, 5], k=3)  # Returns [2, 4, 5]
        """
        return self.rand.choices(x, weights=weights, k=k)

    def randrange(self, start: int, stop: Optional[int] = None, step: int = 1) -> int:
        """
        Return a random integer between start and stop, inclusive.

        :param start: Start of the range (inclusive)
        :type start: int
        :param stop: End of the range (exclusive)
        :type stop: int, optional
        :param step: Step size between values, defaults to 1
        :type step: int, optional
        :returns: A random integer between start and stop, inclusive

        .. code-block:: python

            rand = RandNum(seed=42)
            rand.randrange(1, 10)  # Returns a random number between 1 and 9
        """
        return self.rand.randrange(start, stop, step)


class RandomDistribution(abc.ABC):
    """Base class for random distribution strategies.

    :param rand: Python's random number generator instance
    :type rand: random.Random
    """

    def __init__(self, rand: random.Random):
        self.rand = rand

    def _clamp(self, value):
        if value < 0.0:
            # Return a positive value that's likely to be close to 0.0
            value = self.rand.betavariate(0.2, 5.0)
        elif value >= 1.0:
            # Return a value that is less than 1.0 and likely to be close to
            # 1.0
            value = self.rand.betavariate(5.0, 0.2)

        return value

    def random_in_range(self, lo, hi, step=1) -> int:
        if not (isinstance(lo, int) and isinstance(hi, int) and isinstance(step, int) and (hi > lo) and (step > 0)):
            raise ValueError("lo, hi, and step must be integers and hi > lo and step > 0")
        value = int((hi - lo) * (self.rand.random()))
        value -= value % step
        return lo + value

    @abc.abstractmethod
    def random(self) -> float:
        "Distribution's method of calling random"
        pass


class RandomUniform(RandomDistribution):
    def random(self) -> float:
        return self.rand.uniform(0.0, 1.0)


class RandomTriangular(RandomDistribution):
    def random(self) -> float:
        return self.rand.triangular(0.0, 1.0)


class RandomBeta(RandomDistribution):
    def __init__(self, rand: random.Random, alpha=2.0, beta=2.0):
        super().__init__(rand)
        self.alpha = alpha
        self.beta = beta

    def random(self) -> float:
        return self.rand.betavariate(self.alpha, self.beta)


class RandomExponential(RandomDistribution):
    def __init__(self, rand: random.Random, lambd=1.0):
        super().__init__(rand)
        self.lambd = lambd

    def random(self) -> float:
        return self._clamp(self.rand.expovariate(self.lambd))


class RandomLogNormal(RandomDistribution):
    def __init__(self, rand: random.Random, mu=-2.0, sigma=0.5):
        super().__init__(rand)
        self.mu = mu
        self.sigma = sigma

    def random(self) -> float:
        return self._clamp(self.rand.lognormvariate(self.mu, self.sigma))


class RandomGaussian(RandomDistribution):
    def __init__(self, rand: random.Random, mu=-0.5, sigma=0.5):
        super().__init__(rand)
        self.mu = mu
        self.sigma = sigma

    def random(self) -> float:
        return self._clamp(self.rand.gauss(self.mu, self.sigma))


class DistributionFactory:
    distributions = {"uniform": RandomUniform, "triangular": RandomTriangular, "beta": RandomBeta, "exponential": RandomExponential, "log": RandomLogNormal, "gaussian": RandomGaussian}

    @classmethod
    def create(cls, distribution_type: str, rand: random.Random) -> RandomDistribution:
        if distribution_type not in cls.distributions:
            raise ValueError(f"Invalid distribution type: {distribution_type}. Available types: {', '.join(cls.distributions.keys())}")
        return cls.distributions[distribution_type](rand)

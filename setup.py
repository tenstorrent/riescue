# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
setup.py exists to:
  1. Provide a clearer error when the wrong Python version is used.
  2. Populate install_requires dynamically (pyproject.toml declares
     ``dynamic = ["dependencies"]``).
"""

import sys

if sys.version_info < (3, 9):
    sys.exit("Python 3.9 or greater required")

from setuptools import setup

CORETP_GIT_DEP = "coretp @ git+https://git@github.com/tenstorrent/riscv-coretp" "@c11048c0a6c1973fbeb8850bcd0776f3d585f229"

install_requires = [
    "sortedcontainers",
    "pyyaml",
    "numpy",
    "sphinx>=7.4.0",
    "sphinx-rtd-theme",
    "sphinxcontrib.mermaid",
    "flake8",
    "black==25.1.0",
    "intervaltree",
    "coverage",
    "pyright[nodejs]",
    "pyelftools",
    CORETP_GIT_DEP,
]


setup(install_requires=install_requires)

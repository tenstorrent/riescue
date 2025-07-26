# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import sys

"""
This is to provide a more infomrative error message when the user tries to install RiESCUE without the right version of python
"""
if sys.version_info < (3, 9):
    sys.exit("Python 3.9 or greater required")

from setuptools import setup

setup()

# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Test fixture Conf that declares M-mode enable/disable snippets for ``zacas``.

The snippets are marker comments rather than real CSR writes: the tests only
check that the configured text reaches the generated assembly, and a fixture is
not the place to name implementation-defined CSRs.
"""

from riescue.dtest_framework.config import Conf

ENABLE_SNIPPET = "# ZACAS_ENABLE_MARKER\nnop"
DISABLE_SNIPPET = "# ZACAS_DISABLE_MARKER\nnop"


class ZacasEnablementConf(Conf):
    def get_extension_enablement(self) -> dict[str, dict[str, str]]:
        return {
            "ext_Zacas": {
                "enable": ENABLE_SNIPPET,
                "disable": DISABLE_SNIPPET,
            }
        }


def setup() -> Conf:
    return ZacasEnablementConf()

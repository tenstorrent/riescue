# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

from __future__ import annotations
import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from .builder import FeatMgrBuilder
    from .featmanager import FeatMgr


class Conf:
    """
    User configuration for Riescue. Provides dynamic configuration for test environment and generation options.

    Allows for users to write hooks into a file and use them to modify the test environment before or after building, or to modify some generated code, like the end of test code.

    E.g. to add an end of test write to a custom address:


    .. code-block:: python

        from riescue import RiescueD, FeatMgr, Conf
        from riescue.lib.rand import RandNum
        import riescue.lib.enums as RV

        def end_test(featmgr: FeatMgr) -> str:
            return '''
                li t0, 0x10000000
                sw t0, 0(t0)
            '''

        class MyEotConf(Conf):
            def add_hooks(self, featmgr: FeatMgr) -> None:
                featmgr.register_hook(RV.HookPoint.PRE_HALT, end_test)

        rd = RiescueD(testfile="test.rasm", cpuconfig="cpu_config.json")
        conf = MyEotConf()
        rd.run(conf=conf)

    E.g. to force all tests to run in MACHINE mode:

    .. code-block:: python

        from riescue import RiescueD, FeatMgr, Conf
        from riescue.lib.rand import RandNum
        import riescue.lib.enums as RV


        class MyConf(Conf):
            def post_build(self, featmgr: FeatMgr) -> None:
                # always make it MACHINE
                featmgr.priv_mode = RV.RiscvPrivileges.MACHINE


        rd = RiescueD(testfile="test.rasm", cpuconfig="cpu_config.json")
        rd.configure(conf=MyConf())
        rd.generate()
        rd.build()
        rd.simulate()

    """

    def __init__(self):
        pass

    def pre_build(self, featmgr_builder: FeatMgrBuilder) -> None:
        """
        Called at start of FeatMgrBuilder.build(), before FeatMgr is built.

        :param featmgr_builder: The FeatMgrBuilder to build
        """
        pass

    def post_build(self, featmgr: FeatMgr) -> None:
        """
        Called at end of FeatMgrBuilder.build(), after FeatMgr is built.

        :param featmgr: The FeatMgr to build
        """

    def add_hooks(self, featmgr: FeatMgr) -> None:
        """
        Used to add hooks to the ``FeatMgr``. Called after post_build().

        Call with :py:meth:`riescue.FeatMgr.register_hook`

        :param featmgr: Built FeatMgr
        """
        pass

    @staticmethod
    def load_conf_from_path(path: Path) -> Conf:
        """
        Dynamically loads Conf class from to a ``.py`` script.
        File must contain a ``Conf`` class definition and a setup() method that returns an initialized ``Conf`` object.

        Methods is used by ``RiescueD`` to load from the CLI ``--conf`` command-line option.

        E.g.

        .. code-block:: python

            class MyConf(Conf):
                def __init__(self):
                    self.custom_hook_comment = "# a different comment"

                def custom_hook(self, featmgr: FeatMgr) -> str:
                    return f'''
                        {self.custom_hook_comment}
                        nop
                    '''

                def add_hooks(self, featmgr: FeatMgr) -> None:
                    featmgr.register_hook(HookPoint.PRE_HALT, self.custom_hook)
            def setup() -> Conf:
                return MyConf()


        :raises FileNotFoundError: If the configuration file does not exist
        :raises ImportError: If the configuration module cannot be imported
        :raises RuntimeError: If the configuration module does not contain a a ``setup()`` method that returns a ``Conf`` object.

        """
        if not path.exists():
            raise FileNotFoundError(f"Configuration file {path} does not exist")

        spec = importlib.util.spec_from_file_location("conf", str(path))
        if spec is None or spec.loader is None:
            raise ImportError(f"Configuration file {path} cannot be imported: {spec=}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if not hasattr(module, "setup"):
            raise RuntimeError(f"Configuration file {path} does not contain a setup() method. Define a setup() method that returns a Conf object.")
        conf_obj = module.setup()
        if not isinstance(conf_obj, Conf):
            raise RuntimeError(f"Configuration file {path} setup() method did not return a Conf object. Returned {type(conf_obj)}")
        return conf_obj

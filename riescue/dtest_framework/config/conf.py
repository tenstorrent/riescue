# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict

from __future__ import annotations
import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Any, Iterable, Mapping, Union, cast


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

    def get_mapping(self) -> Optional[list[Any]]:
        """
        Returns a custom mapping of TestStep to Action.

        Only called at TP configuration
        """
        pass

    def get_extension_enablement(self) -> dict[str, dict[str, str]]:
        """
        Returns a mapping of ISA extension name to M-mode enable/disable assembly snippets.

        Default is an empty mapping. Subclasses override to declare per-extension
        controls consumed by RiescueD ``;#enable_ext`` / ``;#disable_ext``, e.g.::

            def get_extension_enablement(self) -> dict[str, dict[str, str]]:
                return {
                    "zacas": {
                        "enable": "li t2, <bit>\\ncsrs <csr>, t2",
                        "disable": "li t2, <bit>\\ncsrc <csr>, t2",
                    }
                }

        The CSR and bit that gate an extension are implementation-defined, so
        substitute the ones your core documents.

        Names may use any casing and an optional ``ext_`` prefix. Each value is a
        mapping with non-empty ``enable`` and ``disable`` strings, or a 2-tuple of
        those strings. Consumers should pass the result through
        :meth:`normalize_extension_enablement`.

        Snippets execute in M-mode (inlined when the test privilege is machine,
        otherwise via the RiescueD extension-control syscall) and must therefore
        be self-contained. They may use ``t2`` as scratch; syscall plumbing uses
        ``t0``, ``t1``, ``t3``, and ``x31``.
        """
        return {}

    @staticmethod
    def normalize_extension_enablement(mapping: object) -> dict[str, dict[str, str]]:
        """
        Normalize and validate a :meth:`get_extension_enablement` mapping.

        Suitable for RiescueD to call after loading ``Conf`` objects:

        * extension names are lowercased
        * an optional ``ext_`` prefix is stripped (``ext_zacas`` and ``Zacas`` both
          become ``zacas``)
        * both ``enable`` and ``disable`` snippets must be non-empty strings
        * malformed entries and conflicting aliases raise ``ValueError``

        :param mapping: Raw mapping from a ``Conf``, or ``None`` (treated as empty).
        :returns: Mapping of canonical extension name to ``{"enable", "disable"}``.
        :raises ValueError: If the mapping or any entry is malformed, incomplete,
            empty, or aliases the same extension to conflicting snippets.
        """
        if mapping is None:
            return {}
        if not isinstance(mapping, Mapping):
            raise ValueError(f"extension enablement must be a mapping, got {type(mapping).__name__}")

        normalized: dict[str, dict[str, str]] = {}
        typed_mapping = cast(Mapping[object, object], mapping)
        for raw_name, value in typed_mapping.items():
            name = Conf.normalize_extension_name(raw_name)
            snippets = Conf._parse_extension_snippets(name, value)
            existing = normalized.get(name)
            if existing is not None and existing != snippets:
                raise ValueError(f"conflicting extension enablement for {name!r}")
            normalized[name] = snippets
        return normalized

    @staticmethod
    def normalize_extension_name(name: Any) -> str:
        if not isinstance(name, str):
            raise ValueError(f"malformed extension enablement entry: name must be a string, got {type(name).__name__}")
        canonical = name.strip().lower()
        if canonical.startswith("ext_"):
            canonical = canonical[4:]
        canonical = canonical.strip()
        if not canonical:
            raise ValueError("malformed extension enablement entry: extension name is empty after normalization")
        return canonical

    @staticmethod
    def _parse_extension_snippets(extension: str, value: object) -> dict[str, str]:
        enable: object
        disable: object
        if isinstance(value, Mapping):
            if "enable" not in value or "disable" not in value:
                raise ValueError(f"malformed extension enablement entry for {extension!r}: mapping must include 'enable' and 'disable'")
            enable = cast(object, value["enable"])
            disable = cast(object, value["disable"])
        elif isinstance(value, (list, tuple)):
            items = cast(Union[list[object], tuple[object, ...]], value)
            if len(items) != 2:
                raise ValueError(f"malformed extension enablement entry for {extension!r}: expected mapping with 'enable'/'disable' or a 2-tuple of strings")
            enable = items[0]
            disable = items[1]
        else:
            raise ValueError(f"malformed extension enablement entry for {extension!r}: expected mapping with 'enable'/'disable' or a 2-tuple of strings")

        if not isinstance(enable, str) or not isinstance(disable, str):
            raise ValueError(f"malformed extension enablement entry for {extension!r}: 'enable' and 'disable' must be strings")
        if not enable.strip() or not disable.strip():
            raise ValueError(f"malformed extension enablement entry for {extension!r}: 'enable' and 'disable' must be non-empty strings")
        return {"enable": enable, "disable": disable}

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

    @staticmethod
    def split_conf_paths(conf_args: Iterable[Union[str, Path]]) -> list[Path]:
        """
        Flatten raw ``--conf`` values into an ordered list of ``Path`` objects.

        Supports both repeated ``--conf`` flags and comma-separated lists, preserving the
        order the files were listed::

            --conf a.py,b.py --conf c.py   ->   [Path("a.py"), Path("b.py"), Path("c.py")]

        Each element of ``conf_args`` may be a ``str`` or ``Path`` and may itself be a
        comma-separated list of paths. Blank/whitespace-only entries are dropped. Order is
        preserved so that conf hooks are registered (and therefore injected) in the order the
        files appear on the command line. Real conf file paths are not expected to contain
        commas.

        :param conf_args: Raw ``--conf`` values (e.g. ``args.conf``).
        :returns: Ordered, flattened list of conf file paths.
        """
        paths: list[Path] = []
        for item in conf_args:
            for part in str(item).split(","):
                part = part.strip()
                if part:
                    paths.append(Path(part))
        return paths

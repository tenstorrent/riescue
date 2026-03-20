
FeatMgr
--------
``FeatMgr`` is a configuration dataclass for Riescue. It contains all configuration needed for ``RiescueD`` to generate a test.
It specifies the enabled features, environment, and generation options.


.. note::
    Generally, users will not need to construct a ``FeatMgr`` directly. Instead, they will use the :doc:`FeatMgrBuilder <FeatMgrBuilder>` to construct a ``FeatMgr``.



.. code-block:: python

   from riescue import FeatMgr

   featmgr = FeatMgr() # build default FeatMgr



.. autoclass:: riescue.FeatMgr()
   :members:


Hooks
_______

Hooks are available for inserting additional code at different points in the code.
Hooks take an instance of the :py:class:`riescue.FeatMgr` class as an argument and return a string of assembly code.
This can be used to add additional code at different points in the Runtime.

E.g.

.. code-block:: python

    def hook(featmgr: FeatMgr) -> str:
        return "nop"
    featmgr.register_hook(RV.HookPoint.PRE_PASS, hook)

The available hooks are defined in the :py:class:`riescue.lib.enums.HookPoint` enum.

.. autoclass:: riescue.lib.enums.HookPoint
   :members:

Hooks can be added using the :py:meth:`riescue.FeatMgr.register_hook` method with a valid enum value.


Multiple hooks can be registered for the same hook point. The order of hooks registered is preserved.

Hooks can also be added using the :py:class:`riescue.dtest_framework.config.Conf` class.
See the :doc:`Conf <../python_api/Conf>` reference for more information on how to use the ``Conf`` class.


Default Interrupt Handler Override
___________________________________

The ``register_default_handler`` method lets a :doc:`Conf <../python_api/Conf>` override the
test-wide default handler for a specific interrupt vector.  The replacement handler is
active for the whole test and does **not** require the per-segment PROLOGUE/EPILOGUE
pointer-swap mechanism.

.. code-block:: python

    from riescue import Conf, FeatMgr

    def my_ssi_handler(featmgr: FeatMgr) -> str:
        """Custom handler for SSI (vec 1).  Clears SSIP and returns."""
        return """
        csrci mip, 2          # clear SSIP (bit 1)
        mret
    """

    class MyConf(Conf):
        def add_hooks(self, featmgr: FeatMgr) -> None:
            featmgr.register_default_handler(
                vec=1,
                label="my_ssi_handler",
                assembly=my_ssi_handler,
            )

    def setup() -> Conf:
        return MyConf()

The handler assembly callable receives the ``FeatMgr`` instance and returns a raw
assembly string.  The string must end with the appropriate return instruction
(``mret`` for machine-mode vectors, ``sret`` for supervisor-mode vectors).

The framework emits the handler body into the ``.runtime`` section and wires the
vector into the M-mode vector dispatch table.  S-mode delegated vector overrides
are not yet supported.

See ``riescue/dtest_framework/tests/non_instr_tests/default_handler_override.s``
and ``default_handler_override_conf.py`` for a complete working example.

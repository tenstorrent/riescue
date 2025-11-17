
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

Hooks can also be added using the :py:class:`riescue.dtest_framework.config.Conf` class.
See the :doc:`Conf <../python_api/Conf>` reference for more information on how to use the ``Conf`` class.

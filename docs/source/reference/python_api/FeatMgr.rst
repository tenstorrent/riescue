
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



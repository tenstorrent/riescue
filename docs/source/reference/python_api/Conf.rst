Conf
=====

:py:class:`riescue.Conf` provides a way to modify the :py:class:`riescue.FeatMgr` configuration and adds hookable methods.
Configuration is done by modifying the :py:class:`riescue.FeatMgr` and/or :py:class:`riescue.FeatMgrBuilder` objects in place before or after building.

A custom ``Conf`` class can be instantiated and passed into the py:meth:`riescue.RiescueD.run` or py:meth:`riescue.RiescueD.configure` methods.

.. autoclass:: riescue.dtest_framework.config.Conf
   :members:


``Conf`` scripts and the CLI
-------------------------------

``Conf`` classes can also be saved to a file and loaded from the command line. This allows for custom behavior to be added without having to use the Python API.

:py:class:`riescue.RiescueD` has the ``--conf`` command-line option to load a ``Conf`` class from a script.

The only requirements for a ``Conf`` script passed through using ``--conf`` is

- The script must contain a ``setup()`` method
- The ``setup()`` method must return an instance of a ``Conf`` subclass

Users can override one or any of the methods.



Adding Runtime Hooks using a ``Conf`` script
-----------------------------------------------

Users can add runtime hooks to the test by overriding the :py:meth:`riescue.Conf.add_hooks` method using one of the :py:class:`riescue.lib.enums.HookPoint` enum values.

E.g. a valid ``Conf`` script can look like:


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

    def setup() -> Conf:
        return MyEotConf()

{%- set class_name = fullname.split('.')[-1] %}
{{ class_name | escape | underline}}

.. currentmodule:: {{ module }}

.. _{{ fullname }}:

.. autoclass:: {{ fullname }}
   :members:
   :undoc-members:
   :show-inheritance:
   :inherited-members:
   :member-order: bysource
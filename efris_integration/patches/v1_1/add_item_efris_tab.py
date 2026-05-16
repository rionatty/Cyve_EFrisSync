"""
Patch v1.1 — Add full EFRIS tab to Item master.

Converts the EFRIS section on Item from a Section Break to a Tab Break
and installs all new SAP B1 UDF-equivalent fields:
  Upload Type On EFRIS, Goods Type, Tax Rate, Unit Price, Reference No.,
  Commodity Category Id, Stock Prewarning, Have Piece Unit, Have Excise Tax,
  Excise Duty Category, Piece UOM, Piece Unit Price, Excise Weight,
  Piece Scaled Value, Excise Rate, Excise Rate USD/Tonne, Remark, Stick.
"""

from efris_integration.setup.install import after_install


def execute():
    after_install()

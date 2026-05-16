"""
Patch v1.1 — Add "URA Setting" tab to Item master.

Converts the EFRIS section on Item from a Section Break to a Tab Break
(placed before the Inventory tab via insert_after="item_defaults") and
installs all new SAP B1 UDF-equivalent fields plus T130/T131 upload support:

  Upload Type On EFRIS (Add / Update → drives T130 / T131)
  Goods Type, Tax Rate, Unit Price, Reference No.,
  Commodity Category Id, Stock Prewarning, Have Piece Unit, Have Excise Tax,
  Excise Duty Category, Piece UOM, Piece Unit Price, Excise Weight,
  Piece Scaled Value, Excise Rate, Excise Rate USD/Tonne, Remark, Stick.
"""

from efris_integration.setup.install import after_install


def execute():
    after_install()

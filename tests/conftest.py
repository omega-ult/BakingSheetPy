"""Pytest config."""
from tests import sheets

# Schema fixture classes named Test* must not be collected as test classes.
for _name in dir(sheets):
    _obj = getattr(sheets, _name)
    if isinstance(_obj, type) and _name.startswith("Test"):
        _obj.__test__ = False

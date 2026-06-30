from .csv_converter import CsvSheetConverter
from .json_converter import JsonSheetExporter
from .excel_converter import ExcelSheetConverter
from . import _json_contract


def GoogleSheetConverter(*args, **kwargs):  # pragma: no cover - lazy extra
    """Lazy accessor for the optional Google Sheet converter.

    Raises ``ImportError`` with a helpful message if the ``google`` extra is not
    installed.
    """
    try:
        from .google_converter import GoogleSheetConverter as _G
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "Google Sheet support requires the 'google' extra: "
            "pip install bakingsheet[google]"
        ) from e
    return _G(*args, **kwargs)

"""Google Sheet importer (optional extra, lazy-loaded).

Port of BakingSheet.Converters.Google/GoogleSheetConverter.cs. Requires the
``bakingsheet[google]`` extra (``google-api-python-client``, ``google-auth``).
Imports only (no export), like the C# version.

Each spreadsheet tab is a sheet (tab name may be ``SheetName.SubName``).
"""
from __future__ import annotations

from typing import Any, List, Optional

from .._internal.config import parse_sheet_name
from ..raw.importer import RawSheetImporter, RawSheetImporterPage


class _GoogleImporterPage(RawSheetImporterPage):
    def __init__(self, values: "list[list[Any]]", sub_name: Optional[str]) -> None:
        self.sub_name = sub_name
        self._cells = [[_to_str(v) for v in row] for row in values]

    def get_cell(self, col: int, row: int) -> Optional[str]:
        if row < 0 or row >= len(self._cells):
            return None
        r = self._cells[row]
        if col < 0 or col >= len(r):
            return None
        return r[col]


def _to_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    return str(v)


class GoogleSheetConverter(RawSheetImporter):
    """Import from a Google Sheet. Port of ``GoogleSheetConverter`` (import only)."""

    def __init__(
        self,
        spreadsheet_id: str,
        credential_json: str,
        timezone=None,
        format_provider=None,
    ) -> None:
        super().__init__(timezone, format_provider)
        self._spreadsheet_id = spreadsheet_id
        self._credential_json = credential_json
        self._service = None
        self._tabs: "list[tuple[str, Optional[str], list]]" = []

    def _build_service(self):
        from google.oauth2 import service_account  # type: ignore
        from googleapiclient.discovery import build  # type: ignore

        import json

        info = json.loads(self._credential_json) if isinstance(self._credential_json, str) else self._credential_json
        scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
        creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
        return build("sheets", "v4", credentials=creds)

    def load_data(self) -> bool:
        if self._service is None:
            self._service = self._build_service()
        meta = self._service.spreadsheets().get(spreadsheetId=self._spreadsheet_id).execute()
        for sheet in meta.get("sheets", []):
            title = sheet.get("properties", {}).get("title", "")
            if title.startswith("$"):
                continue
            result = (
                self._service.spreadsheets()
                .values()
                .get(spreadsheetId=self._spreadsheet_id, range=f"'{title}'")
                .execute()
            )
            values = result.get("values", [])
            sheet_name, sub_name = parse_sheet_name(title)
            self._tabs.append((sheet_name, sub_name, values))
        return True

    def get_pages(self, sheet_name: str) -> List[RawSheetImporterPage]:
        return [
            _GoogleImporterPage(values, sub_name)
            for (name, sub_name, values) in self._tabs
            if name == sheet_name
        ]

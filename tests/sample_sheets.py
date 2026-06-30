"""Sample schema for CLI/config end-to-end tests.

Lives in an importable module so ``load_container`` can import it by dotted path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from bakingsheet import Sheet, SheetContainerBase, SheetRow


class ConsumableSheet(Sheet["ConsumableSheet.Row"]):
    @dataclass
    class Row(SheetRow):
        Name: Optional[str] = None
        Price: int = 0


@dataclass
class GameContainer(SheetContainerBase):
    Consumables: Optional[ConsumableSheet] = None

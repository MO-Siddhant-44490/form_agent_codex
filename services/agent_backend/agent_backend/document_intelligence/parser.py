"""Parser-neutral parsed-document model and adapter protocol (plan.md §10.1).
Regions keep page + bounding box so every extracted fact stays traceable to
its source (invariant 11)."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class ParserFailure(Exception):
    """Classified parse failure (encrypted, corrupt, unsupported)."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


class RegionKind(StrEnum):
    TEXT_LINE = "text_line"
    TABLE_CELL = "table_cell"
    IMAGE = "image"


@dataclass(frozen=True)
class ParsedRegion:
    page: int  # 1-based
    kind: RegionKind
    text: str
    bounding_box: tuple[float, float, float, float]  # x0, y0, x1, y1 in page units
    confidence: float  # 1.0 for digital text; OCR supplies its own


@dataclass(frozen=True)
class ParsedPage:
    page: int
    width: float
    height: float
    regions: tuple[ParsedRegion, ...]


@dataclass(frozen=True)
class ParsedDocument:
    document_id: str
    parser: str
    pages: tuple[ParsedPage, ...]
    warnings: tuple[str, ...] = field(default=())


class ParserAdapter(Protocol):
    name: str

    def parse(self, document_id: str, data: bytes, mime_type: str) -> ParsedDocument: ...

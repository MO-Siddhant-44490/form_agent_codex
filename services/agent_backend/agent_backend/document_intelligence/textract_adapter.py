"""AWS Textract parser adapter (managed API — no GPU, no self-hosting).
AnalyzeDocument FORMS gives key:value pairs across arbitrary layouts;
TABLES gives cell structure. Both come back with geometry, so every region
keeps a real bounding box for provenance (invariant 11).

boto3 is optional (server extra) and the client is injectable for tests."""

from typing import Any

from .parser import ParsedDocument, ParsedPage, ParsedRegion, ParserFailure, RegionKind

# Textract synchronous AnalyzeDocument accepts PNG, JPEG, and single-page PDF
# bytes. Multi-page PDFs go through the async S3 path (not needed for Slice 5).
_IMAGE_MIME = frozenset({"image/png", "image/jpeg", "application/pdf"})


class TextractParserAdapter:
    name = "textract"

    def __init__(
        self,
        region: str = "ap-south-1",
        profile: str | None = None,
        client: Any | None = None,
        feature_types: tuple[str, ...] = ("FORMS",),
    ) -> None:
        self._region = region
        self._profile = profile
        self._client = client
        self._feature_types = feature_types

    def _ensure_client(self) -> Any:
        if self._client is None:
            import boto3

            session = boto3.Session(profile_name=self._profile)
            self._client = session.client("textract", region_name=self._region)
        return self._client

    def parse(self, document_id: str, data: bytes, mime_type: str) -> ParsedDocument:
        if mime_type not in _IMAGE_MIME:
            raise ParserFailure("unsupported_type", f"textract cannot parse {mime_type}")
        client = self._ensure_client()
        try:
            response = client.analyze_document(
                Document={"Bytes": data}, FeatureTypes=list(self._feature_types)
            )
        except Exception as error:
            name = type(error).__name__
            if "AccessDenied" in name:
                raise ParserFailure("parser_unavailable", str(error)) from error
            raise ParserFailure("parse_error", str(error)) from error
        return self._to_document(document_id, response)

    # -- response mapping --------------------------------------------------

    def _to_document(self, document_id: str, response: dict) -> ParsedDocument:
        blocks = {b["Id"]: b for b in response["Blocks"]}
        page_dims = self._page_dims(response["Blocks"])
        regions: list[ParsedRegion] = []

        for block in response["Blocks"]:
            if block["BlockType"] == "KEY_VALUE_SET" and "KEY" in block.get("EntityTypes", []):
                regions.append(self._kv_region(block, blocks, page_dims))
            elif block["BlockType"] == "CELL":
                cell = self._cell_region(block, blocks, page_dims)
                if cell is not None:
                    regions.append(cell)

        pages = self._group_pages(regions, page_dims)
        warnings = tuple(
            f"page {p} low mean confidence" for p, c in self._page_conf(regions).items() if c < 80
        )
        return ParsedDocument(
            document_id=document_id, parser=self.name, pages=pages, warnings=warnings
        )

    def _text_of(self, block: dict, blocks: dict) -> tuple[str, float]:
        words: list[str] = []
        confs: list[float] = []
        for rel in block.get("Relationships", []):
            if rel["Type"] != "CHILD":
                continue
            for cid in rel["Ids"]:
                child = blocks[cid]
                if child["BlockType"] == "WORD":
                    words.append(child["Text"])
                    confs.append(child.get("Confidence", 0.0))
                elif child["BlockType"] == "SELECTION_ELEMENT":
                    if child.get("SelectionStatus") == "SELECTED":
                        words.append("[X]")
        conf = sum(confs) / len(confs) if confs else block.get("Confidence", 0.0)
        return " ".join(words), conf

    def _bbox(self, block: dict, page_dims: dict) -> tuple[float, float, float, float]:
        # Textract geometry is normalized (0-1); scale to page points for a
        # comparable bounding box.
        page = block.get("Page", 1)
        w, h = page_dims.get(page, (1000.0, 1000.0))
        bb = block["Geometry"]["BoundingBox"]
        x0 = bb["Left"] * w
        y0 = bb["Top"] * h
        return (x0, y0, x0 + bb["Width"] * w, y0 + bb["Height"] * h)

    def _kv_region(self, key_block: dict, blocks: dict, page_dims: dict) -> ParsedRegion:
        key_text, key_conf = self._text_of(key_block, blocks)
        value_text, value_conf = "", key_conf
        for rel in key_block.get("Relationships", []):
            if rel["Type"] == "VALUE":
                value_text, value_conf = self._text_of(blocks[rel["Ids"][0]], blocks)
        text = f"{key_text.rstrip(':')}: {value_text}".strip()
        return ParsedRegion(
            page=key_block.get("Page", 1),
            kind=RegionKind.TEXT_LINE,
            text=text,
            bounding_box=self._bbox(key_block, page_dims),
            confidence=min(key_conf, value_conf) / 100.0,
        )

    def _cell_region(self, cell: dict, blocks: dict, page_dims: dict) -> ParsedRegion | None:
        text, conf = self._text_of(cell, blocks)
        if not text:
            return None
        row, col = cell.get("RowIndex", 0), cell.get("ColumnIndex", 0)
        return ParsedRegion(
            page=cell.get("Page", 1),
            kind=RegionKind.TABLE_CELL,
            text=f"[r{row}c{col}] {text}",
            bounding_box=self._bbox(cell, page_dims),
            confidence=conf / 100.0,
        )

    def _page_dims(self, blocks: list) -> dict[int, tuple[float, float]]:
        dims: dict[int, tuple[float, float]] = {}
        for b in blocks:
            if b["BlockType"] == "PAGE":
                bb = b["Geometry"]["BoundingBox"]
                # Use a nominal A4-ish point size scaled by the page box.
                dims[b.get("Page", 1)] = (612.0 * bb["Width"], 792.0 * bb["Height"])
        return dims or {1: (612.0, 792.0)}

    def _group_pages(self, regions: list, page_dims: dict) -> tuple[ParsedPage, ...]:
        by_page: dict[int, list[ParsedRegion]] = {}
        for r in regions:
            by_page.setdefault(r.page, []).append(r)
        for page in page_dims:
            by_page.setdefault(page, [])
        return tuple(
            ParsedPage(
                page=page,
                width=page_dims.get(page, (612.0, 792.0))[0],
                height=page_dims.get(page, (612.0, 792.0))[1],
                regions=tuple(regs),
            )
            for page, regs in sorted(by_page.items())
        )

    def _page_conf(self, regions: list) -> dict[int, float]:
        by_page: dict[int, list[float]] = {}
        for r in regions:
            by_page.setdefault(r.page, []).append(r.confidence * 100)
        return {p: sum(c) / len(c) for p, c in by_page.items() if c}

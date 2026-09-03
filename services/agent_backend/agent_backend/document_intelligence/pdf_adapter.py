"""Deterministic digital-PDF parser built on PyMuPDF: text lines with
bounding boxes, no ML. The initial workhorse for born-digital documents;
Docling handles layout-heavy/scanned input (docling_adapter)."""

import pymupdf

from .parser import ParsedDocument, ParsedPage, ParsedRegion, ParserFailure, RegionKind


class PyMuPdfParserAdapter:
    name = "pymupdf"

    def parse(self, document_id: str, data: bytes, mime_type: str) -> ParsedDocument:
        if mime_type != "application/pdf":
            raise ParserFailure("unsupported_type", f"{self.name} parses PDFs only")
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception as error:  # pymupdf raises various internal types
            raise ParserFailure("corrupt_document", str(error)) from error
        try:
            if doc.needs_pass:
                raise ParserFailure("encrypted_document", "password-protected PDF")
            pages = tuple(self._parse_page(page, index + 1) for index, page in enumerate(doc))
        finally:
            doc.close()
        return ParsedDocument(document_id=document_id, parser=self.name, pages=pages)

    def _parse_page(self, page: "pymupdf.Page", number: int) -> ParsedPage:
        regions: list[ParsedRegion] = []
        layout = page.get_text("dict")
        for block in layout.get("blocks", []):
            if block.get("type") != 0:  # 0 = text
                continue
            for line in block.get("lines", []):
                text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                if not text:
                    continue
                x0, y0, x1, y1 = line["bbox"]
                regions.append(
                    ParsedRegion(
                        page=number,
                        kind=RegionKind.TEXT_LINE,
                        text=text,
                        bounding_box=(x0, y0, x1, y1),
                        confidence=1.0,  # digital text layer, not OCR
                    )
                )
        return ParsedPage(
            page=number, width=page.rect.width, height=page.rect.height, regions=tuple(regions)
        )

"""Docling parser adapter (plan.md §10.2). Docling and its models are heavy,
so the import is lazy and the dependency optional (`uv sync --extra docling`);
nothing else in the backend pays its cost. Benchmark against pymupdf on the
golden set before routing traffic to it."""

from .parser import ParsedDocument, ParsedPage, ParsedRegion, ParserFailure, RegionKind


class DoclingParserAdapter:
    name = "docling"

    def parse(self, document_id: str, data: bytes, mime_type: str) -> ParsedDocument:
        try:
            from docling.document_converter import DocumentConverter
            from docling_core.types.io import DocumentStream
        except ImportError as error:
            raise ParserFailure(
                "parser_unavailable",
                "docling is not installed (uv sync --extra docling)",
            ) from error

        import io

        stream = DocumentStream(name=document_id, stream=io.BytesIO(data))
        try:
            result = DocumentConverter().convert(stream)
        except Exception as error:
            raise ParserFailure("parse_error", str(error)) from error

        doc = result.document
        pages: dict[int, list[ParsedRegion]] = {}
        sizes: dict[int, tuple[float, float]] = {}
        for page_no, page in doc.pages.items():
            sizes[page_no] = (page.size.width, page.size.height)
            pages[page_no] = []
        for item, _level in doc.iterate_items():
            text = getattr(item, "text", "") or ""
            if not text.strip():
                continue
            for prov in getattr(item, "prov", []):
                bbox = prov.bbox
                pages.setdefault(prov.page_no, []).append(
                    ParsedRegion(
                        page=prov.page_no,
                        kind=RegionKind.TEXT_LINE,
                        text=text.strip(),
                        bounding_box=(bbox.l, bbox.t, bbox.r, bbox.b),
                        confidence=1.0,
                    )
                )
        parsed_pages = tuple(
            ParsedPage(
                page=page_no,
                width=sizes.get(page_no, (0.0, 0.0))[0],
                height=sizes.get(page_no, (0.0, 0.0))[1],
                regions=tuple(regions),
            )
            for page_no, regions in sorted(pages.items())
        )
        return ParsedDocument(document_id=document_id, parser=self.name, pages=parsed_pages)

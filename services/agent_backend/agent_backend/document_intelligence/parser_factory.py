"""Parser selection from the environment. PyMuPDF (fast, digital-PDF, no cost)
is the default; DOC_PARSER=textract uses AWS Textract (tables + forms across
scans and arbitrary layouts, per-page cost). Both satisfy the ParserAdapter
protocol, so the pipeline is unchanged."""

import os

from .parser import ParserAdapter
from .pdf_adapter import PyMuPdfParserAdapter


def build_parser_from_env() -> ParserAdapter:
    which = os.environ.get("DOC_PARSER", "pymupdf").lower()
    if which == "textract":
        from .textract_adapter import TextractParserAdapter

        features = tuple(os.environ.get("TEXTRACT_FEATURES", "FORMS").split(","))
        return TextractParserAdapter(
            region=os.environ.get("AWS_REGION", "ap-south-1"),
            profile=os.environ.get("AWS_PROFILE"),
            feature_types=features,
        )
    if which == "docling":
        from .docling_adapter import DoclingParserAdapter

        return DoclingParserAdapter()
    return PyMuPdfParserAdapter()

"""End-to-end document pipeline: upload -> parse -> quality -> extract ->
fact store. The single entry point Slice 2 exposes to the rest of the
backend."""

from dataclasses import dataclass

from .extract import ExtractionReport, extract_facts
from .fact_store import FactStore
from .parser import ParserAdapter
from .store import DocumentRecord, DocumentStore


@dataclass
class DocumentPipeline:
    store: DocumentStore
    parser: ParserAdapter
    facts: FactStore

    def ingest(
        self, data: bytes, filename: str, mime_type: str
    ) -> tuple[DocumentRecord, ExtractionReport]:
        record = self.store.upload(data, filename, mime_type)
        parsed = self.parser.parse(
            record.document_id, self.store.blob(record.document_id), record.mime_type
        )
        report = extract_facts(parsed)
        self.facts.add_all(list(report.facts))
        return record, report

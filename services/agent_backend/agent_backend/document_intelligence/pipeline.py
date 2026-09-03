"""End-to-end document pipeline: upload -> parse -> quality -> extract ->
(optional) derive indirect values -> fact store. The single entry point the
backend exposes."""

from dataclasses import dataclass, field
from datetime import date

from form_contracts import ModelCallMetadata

from ..model_gateway.base import DerivationTarget
from .derivation import DerivationEngine
from .extract import ExtractionReport, extract_facts
from .fact_store import FactStore
from .parser import ParserAdapter
from .store import DocumentRecord, DocumentStore


@dataclass
class DocumentPipeline:
    store: DocumentStore
    parser: ParserAdapter
    facts: FactStore
    # Optional derivation stage: when set, after extraction the engine computes
    # any configured targets not directly present (age, totals, tenure, ...).
    derivation: DerivationEngine | None = None
    derivation_targets: list[DerivationTarget] = field(default_factory=list)

    def ingest(
        self, data: bytes, filename: str, mime_type: str
    ) -> tuple[DocumentRecord, ExtractionReport, list[ModelCallMetadata]]:
        record = self.store.upload(data, filename, mime_type)
        parsed = self.parser.parse(
            record.document_id, self.store.blob(record.document_id), record.mime_type
        )
        report = extract_facts(parsed, keep_unknown=True)
        self.facts.add_all(list(report.facts))

        model_calls: list[ModelCallMetadata] = []
        if self.derivation is not None and self.derivation_targets:
            available = list(self.facts.active().values())
            outcome = self.derivation.derive(available, self.derivation_targets, today=date.today())
            self.facts.add_all(outcome.facts)
            model_calls = outcome.model_calls
        return record, report, model_calls

"""Deterministic fact extraction: 'Label: value' lines matched against a
canonical key/alias table, normalized per type, with confidence and full
provenance. Low-quality pages are skipped (abstention beats guessing);
model-assisted extraction for hard layouts arrives in later slices."""

from dataclasses import dataclass

from form_contracts import DocumentFact, FactSource, FactStatus, FactValueType, Sensitivity

from .normalize import (
    normalize_date,
    normalize_email,
    normalize_number,
    normalize_phone,
    normalize_string,
)
from .parser import ParsedDocument, ParsedRegion
from .quality import PageQuality, assess_page


@dataclass(frozen=True)
class KeySpec:
    key: str
    value_type: FactValueType
    sensitivity: Sensitivity
    aliases: tuple[str, ...]


KEY_SPECS: tuple[KeySpec, ...] = (
    KeySpec(
        "full_name",
        FactValueType.STRING,
        Sensitivity.PERSONAL,
        ("full name", "name", "applicant name", "candidate name"),
    ),
    KeySpec(
        "email",
        FactValueType.EMAIL,
        Sensitivity.PERSONAL,
        ("email", "email address", "e-mail", "e-mail address"),
    ),
    KeySpec(
        "phone",
        FactValueType.PHONE,
        Sensitivity.PERSONAL,
        ("phone", "phone number", "mobile", "mobile number", "contact number"),
    ),
    KeySpec(
        "date_of_birth",
        FactValueType.DATE,
        Sensitivity.PERSONAL,
        ("date of birth", "dob", "birth date", "birthdate"),
    ),
    KeySpec(
        "country", FactValueType.STRING, Sensitivity.PERSONAL, ("country", "country of residence")
    ),
    KeySpec(
        "years_experience",
        FactValueType.NUMBER,
        Sensitivity.PUBLIC,
        ("years of experience", "experience (years)", "total experience"),
    ),
)

_ALIAS_INDEX = {alias: spec for spec in KEY_SPECS for alias in spec.aliases}

_NORMALIZERS = {
    FactValueType.DATE: normalize_date,
    FactValueType.PHONE: normalize_phone,
    FactValueType.EMAIL: normalize_email,
    FactValueType.NUMBER: normalize_number,
}

# Digital text layer is highly reliable; normalization failures abstain, so a
# produced fact reflects both parse and normalization succeeding.
BASE_CONFIDENCE_DIGITAL = 0.95


@dataclass(frozen=True)
class ExtractionReport:
    facts: tuple[DocumentFact, ...]
    skipped_pages: tuple[PageQuality, ...]
    unparsed_values: tuple[str, ...]  # matched labels whose values failed normalization


def _split_label_value(text: str) -> tuple[str, str] | None:
    for separator in (":", " - "):
        if separator in text:
            label, _, value = text.partition(separator)
            label = label.strip().lower()
            value = value.strip()
            if label and value:
                return label, value
    return None


def _slug(label: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def _fact_from_region(
    document_id: str, parser: str, region: ParsedRegion, counter: int, keep_unknown: bool
) -> DocumentFact | str | None:
    split = _split_label_value(region.text)
    if split is None:
        return None
    label, raw_value = split
    spec = _ALIAS_INDEX.get(label)
    if spec is None:
        if not keep_unknown:
            return None
        # Keep every extracted key-value as a generic string fact so the
        # derivation stage has the full material (e.g. salary components to
        # sum). Provenance is preserved; normalization applies only to known
        # keys.
        key = _slug(label)
        if not key:
            return None
        return DocumentFact(
            fact_id=f"{document_id}-{key}-{counter}",
            key=key,
            value=normalize_string(raw_value),
            value_type=FactValueType.STRING,
            confidence=BASE_CONFIDENCE_DIGITAL * region.confidence,
            sensitivity=Sensitivity.PERSONAL,
            status=FactStatus.EXTRACTED,
            source=FactSource(
                document_id=document_id,
                page=region.page,
                bounding_box=region.bounding_box,
                raw_text=region.text,
                parser=parser,
            ),
        )
    normalizer = _NORMALIZERS.get(spec.value_type)
    if normalizer is None:
        value = normalize_string(raw_value)
    else:
        normalized = normalizer(raw_value)
        if normalized is None:
            return f"{spec.key}: could not normalize {raw_value!r}"
        value = normalized
    return DocumentFact(
        fact_id=f"{document_id}-{spec.key}-{counter}",
        key=spec.key,
        value=value,
        value_type=spec.value_type,
        confidence=BASE_CONFIDENCE_DIGITAL * region.confidence,
        sensitivity=spec.sensitivity,
        status=FactStatus.EXTRACTED,
        source=FactSource(
            document_id=document_id,
            page=region.page,
            bounding_box=region.bounding_box,
            raw_text=region.text,
            parser=parser,
        ),
    )


def extract_facts(parsed: ParsedDocument, *, keep_unknown: bool = False) -> ExtractionReport:
    facts: list[DocumentFact] = []
    skipped: list[PageQuality] = []
    unparsed: list[str] = []
    counter = 0
    for page in parsed.pages:
        quality = assess_page(page)
        if not quality.acceptable:
            skipped.append(quality)
            continue
        for region in page.regions:
            counter += 1
            produced = _fact_from_region(
                parsed.document_id, parsed.parser, region, counter, keep_unknown
            )
            if isinstance(produced, DocumentFact):
                facts.append(produced)
            elif isinstance(produced, str):
                unparsed.append(produced)
    return ExtractionReport(
        facts=tuple(facts), skipped_pages=tuple(skipped), unparsed_values=tuple(unparsed)
    )

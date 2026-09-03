"""Deterministic value normalization (plan.md §10.4): normalized value for
matching/filling, raw value preserved in provenance. No model calls."""

import re
from datetime import datetime

_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        start=1,
    )
}

_DATE_PATTERNS = [
    ("%Y-%m-%d", re.compile(r"^\d{4}-\d{2}-\d{2}$")),
    ("%d/%m/%Y", re.compile(r"^\d{2}/\d{2}/\d{4}$")),
    ("%d-%m-%Y", re.compile(r"^\d{2}-\d{2}-\d{4}$")),
]
_TEXTUAL_DATE = re.compile(r"^(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})$")
_TEXTUAL_DATE_US = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})$")


def normalize_date(raw: str) -> str | None:
    """To ISO yyyy-mm-dd; None when the format is not confidently parseable.
    Ambiguous numeric forms are read day-first (dd/mm/yyyy) — a deliberate,
    documented convention, revisited with per-locale config later."""
    text = raw.strip()
    for fmt, pattern in _DATE_PATTERNS:
        if pattern.match(text):
            try:
                return datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                return None
    for pattern, order in ((_TEXTUAL_DATE, "dmy"), (_TEXTUAL_DATE_US, "mdy")):
        match = pattern.match(text)
        if match:
            if order == "dmy":
                day, month_name, year = match.groups()
            else:
                month_name, day, year = match.groups()
            month = _MONTHS.get(month_name[:3].lower())
            if month is None:
                return None
            try:
                return datetime(int(year), month, int(day)).date().isoformat()
            except ValueError:
                return None
    return None


_PHONE_ALLOWED = re.compile(r"^[+\d][\d\s().-]{6,19}$")


def normalize_phone(raw: str) -> str | None:
    """Keep leading + and digits; reject strings that are not phone-shaped."""
    text = raw.strip()
    if not _PHONE_ALLOWED.match(text):
        return None
    digits = re.sub(r"[^\d]", "", text)
    if not 7 <= len(digits) <= 15:
        return None
    return f"+{digits}" if text.startswith("+") else digits


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(raw: str) -> str | None:
    text = raw.strip().lower()
    return text if _EMAIL.match(text) else None


def normalize_number(raw: str) -> str | None:
    text = raw.strip().replace(",", "")
    try:
        value = float(text)
    except ValueError:
        return None
    return str(int(value)) if value.is_integer() else str(value)


def normalize_string(raw: str) -> str:
    return re.sub(r"\s+", " ", raw).strip()

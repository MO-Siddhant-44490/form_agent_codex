"""Page/region quality assessment (plan.md §10.1): the pipeline reports
quality signals and abstains on poor pages instead of pretending every parse
is reliable. Poor pages are flagged for the fallback parser chain."""

from dataclasses import dataclass

from .parser import ParsedPage

MIN_CHARS_PER_PAGE = 40
MIN_MEAN_CONFIDENCE = 0.6


@dataclass(frozen=True)
class PageQuality:
    page: int
    char_count: int
    region_count: int
    mean_confidence: float
    acceptable: bool
    reasons: tuple[str, ...]


def assess_page(page: ParsedPage) -> PageQuality:
    char_count = sum(len(r.text) for r in page.regions)
    region_count = len(page.regions)
    mean_confidence = (
        sum(r.confidence for r in page.regions) / region_count if region_count else 0.0
    )
    reasons: list[str] = []
    if char_count < MIN_CHARS_PER_PAGE:
        reasons.append(f"only {char_count} characters extracted")
    if region_count and mean_confidence < MIN_MEAN_CONFIDENCE:
        reasons.append(f"mean region confidence {mean_confidence:.2f}")
    return PageQuality(
        page=page.page,
        char_count=char_count,
        region_count=region_count,
        mean_confidence=mean_confidence,
        acceptable=not reasons,
        reasons=tuple(reasons),
    )

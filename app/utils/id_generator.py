"""Centralised, deterministic identifier generation and validation.

Every identifier in InsightPulse is produced by this module so that formats are
consistent across the generator, database, API and exports.

Conventions::

    Customer        CUS-000001
    Product         PRD-000001
    Channel         CHN-000001
    Survey          SUR-2026-000001
    SurveyResponse  RESP-SUR-2026-000001-001      (counter is per survey)
    Review          REV-2026-000001
    SupportTicket   TKT-2026-000001
    SupportMessage  MSG-TKT-2026-000001-001       (counter is per ticket)
    Theme           THEME-000001
    ActionItem      ACT-2026-000001
    Annotation      ANN-2026-000001
    FeedbackItem    FB-<source record id>         (e.g. FB-REV-2026-000001)
    DataQuality     DQ-000001
    Anomaly (GT)    ANOM-000001
    Pipeline run    RUN-000001

IDs are immutable: nothing in the codebase re-numbers an existing record.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

DEFAULT_YEAR = 2026

_SIMPLE = {
    "customer": ("CUS", 6),
    "product": ("PRD", 6),
    "channel": ("CHN", 6),
    "theme": ("THEME", 6),
    "dq_issue": ("DQ", 6),
    "anomaly": ("ANOM", 6),
    "pipeline_run": ("RUN", 6),
}
_YEARLY = {
    "survey": ("SUR", 6),
    "review": ("REV", 6),
    "ticket": ("TKT", 6),
    "action": ("ACT", 6),
    "annotation": ("ANN", 6),
}

ID_PATTERNS: dict[str, re.Pattern[str]] = {
    "customer": re.compile(r"^CUS-\d{6}$"),
    "product": re.compile(r"^PRD-\d{6}$"),
    "channel": re.compile(r"^CHN-\d{6}$"),
    "theme": re.compile(r"^THEME-\d{6}$"),
    "dq_issue": re.compile(r"^DQ-\d{6}$"),
    "anomaly": re.compile(r"^ANOM-\d{6}$"),
    "pipeline_run": re.compile(r"^RUN-\d{6}$"),
    "survey": re.compile(r"^SUR-\d{4}-\d{6}$"),
    "review": re.compile(r"^REV-\d{4}-\d{6}$"),
    "ticket": re.compile(r"^TKT-\d{4}-\d{6}$"),
    "action": re.compile(r"^ACT-\d{4}-\d{6}$"),
    "annotation": re.compile(r"^ANN-\d{4}-\d{6}$"),
    "survey_response": re.compile(r"^RESP-SUR-\d{4}-\d{6}-\d{3}$"),
    "support_message": re.compile(r"^MSG-TKT-\d{4}-\d{6}-\d{3}$"),
    "feedback": re.compile(r"^FB-(RESP-SUR-\d{4}-\d{6}-\d{3}|REV-\d{4}-\d{6}|MSG-TKT-\d{4}-\d{6}-\d{3})$"),
}


class IdError(ValueError):
    """Raised for invalid counters or malformed identifiers."""


def _fmt(prefix: str, width: int, n: int) -> str:
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise IdError(f"Counter must be a positive integer, got {n!r}")
    if n >= 10**width:
        raise IdError(f"Counter {n} overflows {width}-digit {prefix} identifier")
    return f"{prefix}-{n:0{width}d}"


def make_id(entity: str, n: int, year: int = DEFAULT_YEAR) -> str:
    """Format a simple or yearly identifier for ``entity`` with counter ``n``."""
    if entity in _SIMPLE:
        prefix, width = _SIMPLE[entity]
        return _fmt(prefix, width, n)
    if entity in _YEARLY:
        prefix, width = _YEARLY[entity]
        if not 1000 <= year <= 9999:
            raise IdError(f"Invalid year {year}")
        return _fmt(f"{prefix}-{year}", width, n)
    raise IdError(f"Unknown entity type {entity!r}")


def customer_id(n: int) -> str:
    return make_id("customer", n)


def product_id(n: int) -> str:
    return make_id("product", n)


def channel_id(n: int) -> str:
    return make_id("channel", n)


def theme_id(n: int) -> str:
    return make_id("theme", n)


def survey_id(n: int, year: int = DEFAULT_YEAR) -> str:
    return make_id("survey", n, year)


def review_id(n: int, year: int = DEFAULT_YEAR) -> str:
    return make_id("review", n, year)


def ticket_id(n: int, year: int = DEFAULT_YEAR) -> str:
    return make_id("ticket", n, year)


def action_id(n: int, year: int = DEFAULT_YEAR) -> str:
    return make_id("action", n, year)


def annotation_id(n: int, year: int = DEFAULT_YEAR) -> str:
    return make_id("annotation", n, year)


def response_id(parent_survey_id: str, n: int) -> str:
    """RESP-<survey-id>-001 (counter is scoped to the parent survey)."""
    if not ID_PATTERNS["survey"].match(parent_survey_id):
        raise IdError(f"Invalid parent survey id {parent_survey_id!r}")
    return _fmt(f"RESP-{parent_survey_id}", 3, n)


def message_id(parent_ticket_id: str, n: int) -> str:
    """MSG-<ticket-id>-001 (counter is scoped to the parent ticket)."""
    if not ID_PATTERNS["ticket"].match(parent_ticket_id):
        raise IdError(f"Invalid parent ticket id {parent_ticket_id!r}")
    return _fmt(f"MSG-{parent_ticket_id}", 3, n)


def feedback_id(source_record_id: str) -> str:
    """Deterministic FeedbackItem id derived from its source record id."""
    fid = f"FB-{source_record_id}"
    if not ID_PATTERNS["feedback"].match(fid):
        raise IdError(f"Cannot derive feedback id from {source_record_id!r}")
    return fid


def source_id_from_feedback_id(fid: str) -> str:
    if not ID_PATTERNS["feedback"].match(fid):
        raise IdError(f"Invalid feedback id {fid!r}")
    return fid[3:]


def source_type_of(source_record_id: str) -> str:
    """Infer the feedback source type from a source record id."""
    if ID_PATTERNS["survey_response"].match(source_record_id):
        return "SURVEY_RESPONSE"
    if ID_PATTERNS["review"].match(source_record_id):
        return "REVIEW"
    if ID_PATTERNS["support_message"].match(source_record_id):
        return "SUPPORT_MESSAGE"
    raise IdError(f"Unrecognised source record id {source_record_id!r}")


def parent_of(child_id: str) -> str:
    """Return the parent survey/ticket id embedded in a response/message id."""
    if ID_PATTERNS["survey_response"].match(child_id) or ID_PATTERNS["support_message"].match(child_id):
        return child_id.split("-", 1)[1].rsplit("-", 1)[0]
    raise IdError(f"{child_id!r} has no embedded parent id")


def is_valid_id(entity: str, value: object) -> bool:
    pattern = ID_PATTERNS.get(entity)
    if pattern is None:
        raise IdError(f"Unknown entity type {entity!r}")
    return isinstance(value, str) and bool(pattern.match(value))


def counter_of(value: str) -> int:
    """Return the trailing numeric counter of an identifier."""
    m = re.search(r"(\d+)$", value)
    if not m:
        raise IdError(f"No counter in {value!r}")
    return int(m.group(1))


@dataclass
class IdSequence:
    """Deterministic counter for one entity type (optionally scoped to a parent).

    >>> seq = IdSequence("review"); seq.next(), seq.next()
    ('REV-2026-000001', 'REV-2026-000002')
    """

    entity: str
    year: int = DEFAULT_YEAR
    start: int = 1
    _n: int = field(init=False)

    def __post_init__(self) -> None:
        self._n = self.start - 1

    def next(self) -> str:
        self._n += 1
        return make_id(self.entity, self._n, self.year)

    @property
    def last(self) -> int:
        return self._n


@dataclass
class ChildIdSequence:
    """Per-parent counters for responses (per survey) and messages (per ticket)."""

    kind: str  # "response" | "message"
    _counters: dict[str, int] = field(default_factory=dict)

    def next(self, parent: str) -> str:
        n = self._counters.get(parent, 0) + 1
        self._counters[parent] = n
        return response_id(parent, n) if self.kind == "response" else message_id(parent, n)

    def count(self, parent: str) -> int:
        return self._counters.get(parent, 0)

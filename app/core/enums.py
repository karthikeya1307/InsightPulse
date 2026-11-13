"""Enumerations and lifecycle state machines shared across layers."""
from __future__ import annotations

from enum import StrEnum


class SourceType(StrEnum):
    SURVEY_RESPONSE = "SURVEY_RESPONSE"
    REVIEW = "REVIEW"
    SUPPORT_MESSAGE = "SUPPORT_MESSAGE"


class Language(StrEnum):
    EN = "en"
    HI = "hi"
    TE = "te"


class CustomerSegment(StrEnum):
    CONSUMER = "CONSUMER"
    SMB = "SMB"
    ENTERPRISE = "ENTERPRISE"


class CustomerStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    CHURNED = "CHURNED"


class ProductStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISCONTINUED = "DISCONTINUED"


class ChannelType(StrEnum):
    WEB = "WEB"
    MOBILE_APP = "MOBILE_APP"
    MARKETPLACE = "MARKETPLACE"
    EMAIL = "EMAIL"
    SMS = "SMS"
    PHONE = "PHONE"
    CHAT = "CHAT"
    SOCIAL = "SOCIAL"


class SurveyType(StrEnum):
    CSAT = "CSAT"
    NPS = "NPS"
    PRODUCT_FEEDBACK = "PRODUCT_FEEDBACK"
    POST_SUPPORT = "POST_SUPPORT"


class SurveyStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class TicketStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    PENDING_CUSTOMER = "PENDING_CUSTOMER"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class TicketPriority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class SenderType(StrEnum):
    CUSTOMER = "CUSTOMER"
    AGENT = "AGENT"


class FeedbackStatus(StrEnum):
    INGESTED = "INGESTED"
    VALIDATED = "VALIDATED"
    PROCESSED = "PROCESSED"
    DUPLICATE = "DUPLICATE"
    QUARANTINED = "QUARANTINED"


class DatasetSplit(StrEnum):
    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    TEST = "TEST"


class TrainingStatus(StrEnum):
    PENDING = "PENDING"
    ELIGIBLE = "ELIGIBLE"
    EXCLUDED = "EXCLUDED"


class ActionStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    DISMISSED = "DISMISSED"


class Priority(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Severity(StrEnum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


ACTIVE_ACTION_STATUSES = frozenset({ActionStatus.OPEN, ActionStatus.IN_PROGRESS, ActionStatus.BLOCKED})

ACTION_TRANSITIONS: dict[str, frozenset[str]] = {
    "OPEN": frozenset({"IN_PROGRESS", "DISMISSED"}),
    "IN_PROGRESS": frozenset({"BLOCKED", "RESOLVED", "OPEN"}),
    "BLOCKED": frozenset({"IN_PROGRESS", "DISMISSED"}),
    "RESOLVED": frozenset({"CLOSED", "IN_PROGRESS"}),
    "CLOSED": frozenset(),
    "DISMISSED": frozenset(),
}

TICKET_TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"OPEN"}),
    "OPEN": frozenset({"IN_PROGRESS", "CLOSED"}),
    "IN_PROGRESS": frozenset({"PENDING_CUSTOMER", "RESOLVED"}),
    "PENDING_CUSTOMER": frozenset({"IN_PROGRESS", "RESOLVED"}),
    "RESOLVED": frozenset({"CLOSED", "IN_PROGRESS"}),
    "CLOSED": frozenset(),
}

FEEDBACK_TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"INGESTED"}),
    "INGESTED": frozenset({"VALIDATED", "DUPLICATE", "QUARANTINED"}),
    "VALIDATED": frozenset({"PROCESSED", "DUPLICATE", "QUARANTINED"}),
    "PROCESSED": frozenset({"QUARANTINED", "DUPLICATE"}),
    "DUPLICATE": frozenset(),
    "QUARANTINED": frozenset({"VALIDATED"}),
}


def is_valid_transition(machine: dict, from_state: str | None, to_state: str) -> bool:
    """Return True when ``from_state -> to_state`` is allowed by ``machine``."""
    return to_state in machine.get(from_state, frozenset())

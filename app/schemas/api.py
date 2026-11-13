"""Pydantic request/response schemas for the REST API."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.taxonomy import ALLOWED_THEME_IDS
from app.schemas.common import PageMeta

SourceTypeLit = Literal["SURVEY_RESPONSE", "REVIEW", "SUPPORT_MESSAGE"]
SentimentLit = Literal["POSITIVE", "NEGATIVE", "NEUTRAL", "MIXED"]
PriorityLit = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
ActionStatusLit = Literal["OPEN", "IN_PROGRESS", "BLOCKED", "RESOLVED", "CLOSED", "DISMISSED"]


# ---------------------------------------------------------------- feedback --
class ThemeScore(BaseModel):
    theme_id: str
    theme_name: str
    confidence: float


class SentimentOut(BaseModel):
    label: SentimentLit
    confidence: float
    needs_human_review: bool
    review_reason: str | None = None
    model_version: str
    prediction_method: str
    run_id: str


class FeedbackSummary(BaseModel):
    feedback_id: str
    source_type: SourceTypeLit
    source_record_id: str
    customer_id: str
    product_id: str
    product_name: str
    channel_id: str
    channel_name: str
    feedback_date: datetime
    rating: int | None
    language: str
    text: str = Field(description="Display text; PII is redacted for quarantined records")
    processing_status: str
    sentiment: SentimentOut | None = None
    themes: list[ThemeScore] = []


class FeedbackPage(BaseModel):
    items: list[FeedbackSummary]
    meta: PageMeta


class PredictionHistoryItem(BaseModel):
    run_id: str
    sentiment_label: str
    confidence: float
    model_version: str
    predicted_at: datetime
    needs_human_review: bool
    review_reason: str | None
    prediction_method: str
    themes: list[ThemeScore]


class FeedbackDetail(FeedbackSummary):
    source_details: dict[str, Any]
    prediction_history: list[PredictionHistoryItem]
    data_quality_issues: list[dict[str, Any]]
    linked_actions: list[dict[str, Any]]
    status_history: list[dict[str, Any]]


class FeedbackCreate(BaseModel):
    """Submit new customer feedback. The source record and the unified feedback item
    are created in one transaction, validated, and scored with the active models."""

    model_config = ConfigDict(json_schema_extra={"example": {
        "source_type": "REVIEW", "customer_id": "CUS-000001", "product_id": "PRD-000001",
        "channel_id": "CHN-000001", "rating": 2, "text": "Battery died after two days, very disappointed."}})

    source_type: SourceTypeLit
    customer_id: str = Field(pattern=r"^CUS-\d{6}$")
    product_id: str | None = Field(default=None, pattern=r"^PRD-\d{6}$")
    channel_id: str | None = Field(default=None, pattern=r"^CHN-\d{6}$")
    text: str = Field(min_length=2, max_length=5000)
    rating: int | None = Field(default=None, ge=1, le=5)
    nps_score: int | None = Field(default=None, ge=0, le=10)
    review_title: str | None = Field(default=None, max_length=160)
    survey_id: str | None = Field(default=None, pattern=r"^SUR-\d{4}-\d{6}$")
    ticket_id: str | None = Field(default=None, pattern=r"^TKT-\d{4}-\d{6}$")
    language: Literal["en", "hi", "te"] | None = None
    verified_purchase: bool = False
    feedback_date: datetime | None = Field(
        default=None, description="REVIEW only: when the review was written (defaults to now). "
                                  "Must not be in the future or before the product's launch date.")

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must not be blank")
        return v

    @model_validator(mode="after")
    def source_requirements(self) -> "FeedbackCreate":
        if self.source_type in ("REVIEW", "SURVEY_RESPONSE") and self.rating is None:
            raise ValueError(f"rating is required for {self.source_type}")
        if self.source_type in ("REVIEW", "SURVEY_RESPONSE") and (self.product_id is None or self.channel_id is None):
            raise ValueError(f"product_id and channel_id are required for {self.source_type}")
        if self.source_type == "SURVEY_RESPONSE" and not self.survey_id:
            raise ValueError("survey_id is required for SURVEY_RESPONSE")
        if self.source_type == "SUPPORT_MESSAGE":
            if not self.ticket_id:
                raise ValueError("ticket_id is required for SUPPORT_MESSAGE")
            if self.rating is not None:
                raise ValueError("support messages do not carry a rating")
        if self.feedback_date is not None:
            if self.source_type != "REVIEW":
                raise ValueError("feedback_date can only be set for REVIEW (survey and support dates come from "
                                 "the survey window and the ticket)")
            fd = self.feedback_date if self.feedback_date.tzinfo else self.feedback_date.replace(tzinfo=timezone.utc)
            if fd > datetime.now(timezone.utc):
                raise ValueError("feedback_date must not be in the future")
            self.feedback_date = fd
        return self


class UploadRowResult(BaseModel):
    row: int = Field(description="Line number in the CSV (the header is line 1)")
    status: Literal["CREATED", "QUARANTINED", "DUPLICATE", "REJECTED"]
    feedback_id: str | None = None
    source_type: SourceTypeLit | None = None
    product_id: str | None = None
    product_name: str | None = None
    text: str
    language: str | None = None
    sentiment: SentimentLit | None = None
    confidence: float | None = None
    needs_human_review: bool | None = None
    themes: list[str] = Field(default_factory=list)
    error: str | None = None


class UploadResult(BaseModel):
    run_id: str
    filename: str
    rows_total: int
    created: int
    quarantined: int
    duplicates: int
    rejected: int
    results: list[UploadRowResult]


# ----------------------------------------------------------------- actions --
class ActionSummary(BaseModel):
    action_id: str
    title: str
    description: str
    product_id: str | None
    product_name: str | None
    theme_id: str
    theme_name: str
    rule_code: str
    priority: PriorityLit
    status: ActionStatusLit
    evidence_count: int
    negative_count: int
    total_count: int
    negative_share: float | None
    recommended_action: str
    owner: str
    origin: str
    due_date: date
    created_at: datetime
    updated_at: datetime
    window_start: date | None
    window_end: date | None


class ActionPage(BaseModel):
    items: list[ActionSummary]
    meta: PageMeta


class ActionDetail(ActionSummary):
    metrics: dict[str, Any]
    duplicate_reason: str | None
    status_history: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    evidence_total: int


class ActionCreate(BaseModel):
    title: str = Field(min_length=5, max_length=200)
    description: str = Field(min_length=5)
    theme_id: str
    product_id: str | None = Field(default=None, pattern=r"^PRD-\d{6}$")
    priority: PriorityLit = "MEDIUM"
    owner: str = Field(min_length=2, max_length=80)
    recommended_action: str = Field(min_length=5)
    due_date: date | None = None
    feedback_ids: list[str] = Field(min_length=1, max_length=5000)
    created_by: str = Field(min_length=2, max_length=80)
    duplicate_reason: str | None = Field(default=None, description="Required to open a 2nd active action for the same issue")

    @field_validator("theme_id")
    @classmethod
    def theme_in_taxonomy(cls, v: str) -> str:
        if v not in ALLOWED_THEME_IDS:
            raise ValueError(f"theme_id {v!r} is outside the allowed taxonomy")
        return v


class ActionStatusUpdate(BaseModel):
    status: ActionStatusLit
    changed_by: str = Field(min_length=2, max_length=80)
    note: str | None = Field(default=None, max_length=2000)


# ------------------------------------------------------- validation / runs --
class IssueOut(BaseModel):
    issue_id: str
    run_id: str | None
    rule_code: str
    severity: str
    entity_type: str
    record_id: str
    field_name: str | None
    message: str
    details: dict[str, Any]
    resolution_action: str
    status: str
    detected_at: datetime
    last_seen_at: datetime


class IssuePage(BaseModel):
    items: list[IssueOut]
    meta: PageMeta


class PipelineRunRequest(BaseModel):
    steps: list[Literal["ingest", "validate", "train", "predict", "actions"]] = Field(
        default=["validate", "train", "predict", "actions"])
    scale: Literal["starter", "full"] | None = None
    replace: bool = Field(default=False, description="Reload data (truncates non-reference tables) when ingesting")
    wait: bool = Field(default=False, description="Run synchronously and return the final summary")


class PipelineRunOut(BaseModel):
    run_id: str
    run_type: str
    status: str
    triggered_by: str
    parameters: dict[str, Any]
    summary: dict[str, Any]
    sentiment_model_version: str | None
    theme_model_version: str | None
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None

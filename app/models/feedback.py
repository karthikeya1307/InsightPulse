"""Unified FeedbackItem, training annotations, predictions and theme bridge."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY, BigInteger, Boolean, DateTime, ForeignKey, Identity, Numeric, SmallInteger, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class FeedbackItem(Base):
    __tablename__ = "feedback_items"
    feedback_id: Mapped[str] = mapped_column(String(30), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(16))
    source_record_id: Mapped[str] = mapped_column(String(25), unique=True)
    survey_response_id: Mapped[str | None] = mapped_column(
        ForeignKey("survey_responses.response_id"), unique=True
    )
    review_id: Mapped[str | None] = mapped_column(ForeignKey("reviews.review_id"), unique=True)
    support_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("support_messages.message_id"), unique=True
    )
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.customer_id"))
    product_id: Mapped[str] = mapped_column(ForeignKey("products.product_id"))
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.channel_id"))
    feedback_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rating: Mapped[int | None] = mapped_column(SmallInteger)
    feedback_text: Mapped[str] = mapped_column(Text)
    redacted_text: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(2))
    text_hash: Mapped[str] = mapped_column(String(64))
    dataset_split: Mapped[str] = mapped_column(String(10))
    processing_status: Mapped[str] = mapped_column(String(12))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    predictions: Mapped[list["SentimentPrediction"]] = relationship(back_populates="feedback")
    themes: Mapped[list["FeedbackTheme"]] = relationship(back_populates="feedback")

    @property
    def display_text(self) -> str:
        """Text safe to expose: redacted when a privacy issue was detected."""
        return self.redacted_text if self.redacted_text is not None else self.feedback_text


class FeedbackAnnotation(Base):
    __tablename__ = "feedback_annotations"
    annotation_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    feedback_id: Mapped[str] = mapped_column(ForeignKey("feedback_items.feedback_id"))
    sentiment_label: Mapped[str] = mapped_column(ForeignKey("sentiment_labels.label_code"))
    theme_labels: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    annotator_id: Mapped[str] = mapped_column(String(20))
    annotation_note: Mapped[str | None] = mapped_column(Text)
    annotated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    declared_split: Mapped[str] = mapped_column(String(10))
    training_status: Mapped[str] = mapped_column(String(10), default="PENDING")
    exclusion_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    run_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    run_type: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(10))
    triggered_by: Mapped[str] = mapped_column(String(60))
    parameters: Mapped[dict] = mapped_column(JSONB, default=dict)
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)
    sentiment_model_version: Mapped[str | None] = mapped_column(String(80))
    theme_model_version: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SentimentPrediction(Base):
    __tablename__ = "sentiment_predictions"
    __table_args__ = (UniqueConstraint("feedback_id", "run_id"),)
    prediction_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    feedback_id: Mapped[str] = mapped_column(ForeignKey("feedback_items.feedback_id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.run_id"))
    sentiment_label: Mapped[str] = mapped_column(ForeignKey("sentiment_labels.label_code"))
    confidence: Mapped[Decimal] = mapped_column(Numeric(6, 5))
    prob_positive: Mapped[Decimal] = mapped_column(Numeric(6, 5))
    prob_negative: Mapped[Decimal] = mapped_column(Numeric(6, 5))
    prob_neutral: Mapped[Decimal] = mapped_column(Numeric(6, 5))
    prob_mixed: Mapped[Decimal] = mapped_column(Numeric(6, 5))
    model_name: Mapped[str] = mapped_column(String(60))
    model_version: Mapped[str] = mapped_column(String(80))
    uncertainty_threshold: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    needs_human_review: Mapped[bool] = mapped_column(Boolean)
    review_reason: Mapped[str | None] = mapped_column(String(120))
    detected_language: Mapped[str] = mapped_column(String(2))
    prediction_method: Mapped[str] = mapped_column(String(40))
    predicted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    feedback: Mapped[FeedbackItem] = relationship(back_populates="predictions")


class FeedbackTheme(Base):
    """Bridge table FeedbackItem N:M Theme (one row per run, history preserved)."""

    __tablename__ = "feedback_themes"
    __table_args__ = (UniqueConstraint("feedback_id", "theme_id", "run_id"),)
    feedback_theme_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    feedback_id: Mapped[str] = mapped_column(ForeignKey("feedback_items.feedback_id"))
    theme_id: Mapped[str] = mapped_column(ForeignKey("themes.theme_id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.run_id"))
    confidence: Mapped[Decimal] = mapped_column(Numeric(6, 5))
    model_version: Mapped[str] = mapped_column(String(80))
    assignment_source: Mapped[str] = mapped_column(String(10))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    feedback: Mapped[FeedbackItem] = relationship(back_populates="themes")

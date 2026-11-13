"""Action items (with evidence bridge and status events), data quality, state changes."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Identity, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ActionItem(Base):
    __tablename__ = "action_items"
    action_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    product_id: Mapped[str | None] = mapped_column(ForeignKey("products.product_id"))
    theme_id: Mapped[str] = mapped_column(ForeignKey("themes.theme_id"))
    rule_code: Mapped[str] = mapped_column(String(40))
    priority: Mapped[str] = mapped_column(String(8))
    evidence_count: Mapped[int] = mapped_column(Integer)
    negative_count: Mapped[int] = mapped_column(Integer, default=0)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    negative_share: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    recommended_action: Mapped[str] = mapped_column(Text)
    owner: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(12))
    origin: Mapped[str] = mapped_column(String(8))
    duplicate_reason: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("pipeline_runs.run_id"))
    window_start: Mapped[date | None] = mapped_column(Date)
    window_end: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    evidence: Mapped[list["ActionEvidence"]] = relationship(back_populates="action")
    events: Mapped[list["ActionStatusEvent"]] = relationship(
        back_populates="action", order_by="ActionStatusEvent.event_id"
    )


class ActionEvidence(Base):
    """Bridge table ActionItem N:M FeedbackItem (traceability)."""

    __tablename__ = "action_evidence"
    action_id: Mapped[str] = mapped_column(ForeignKey("action_items.action_id"), primary_key=True)
    feedback_id: Mapped[str] = mapped_column(ForeignKey("feedback_items.feedback_id"), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("pipeline_runs.run_id"))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    action: Mapped[ActionItem] = relationship(back_populates="evidence")


class ActionStatusEvent(Base):
    __tablename__ = "action_status_events"
    event_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    action_id: Mapped[str] = mapped_column(ForeignKey("action_items.action_id"))
    from_status: Mapped[str | None] = mapped_column(String(12))
    to_status: Mapped[str] = mapped_column(String(12))
    changed_by: Mapped[str] = mapped_column(String(80))
    note: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    action: Mapped[ActionItem] = relationship(back_populates="events")


class DataQualityIssue(Base):
    __tablename__ = "data_quality_issues"
    issue_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("pipeline_runs.run_id"))
    rule_code: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(8))
    entity_type: Mapped[str] = mapped_column(String(30))
    record_id: Mapped[str] = mapped_column(String(40))
    field_name: Mapped[str | None] = mapped_column(String(60))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    resolution_action: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(12), default="OPEN")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StateChangeEvent(Base):
    __tablename__ = "state_change_events"
    event_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[str] = mapped_column(String(40))
    from_state: Mapped[str | None] = mapped_column(String(20))
    to_state: Mapped[str] = mapped_column(String(20))
    changed_by: Mapped[str] = mapped_column(String(80))
    reason: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("pipeline_runs.run_id"))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

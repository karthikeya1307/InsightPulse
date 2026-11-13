"""Reference data: sentiment labels and the theme taxonomy."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import ARRAY, Boolean, Date, DateTime, SmallInteger, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SentimentLabel(Base):
    __tablename__ = "sentiment_labels"
    label_code: Mapped[str] = mapped_column(String(10), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(30))
    polarity: Mapped[int] = mapped_column(SmallInteger)
    description: Mapped[str] = mapped_column(Text)
    display_order: Mapped[int] = mapped_column(SmallInteger, unique=True)


class Theme(Base):
    __tablename__ = "themes"
    theme_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    theme_code: Mapped[str] = mapped_column(String(40), unique=True)
    theme_name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(Text)
    keywords: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

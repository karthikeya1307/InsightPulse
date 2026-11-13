"""Immutable source records: SurveyResponse, Review, SupportTicket, SupportMessage."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class SurveyResponse(Base):
    __tablename__ = "survey_responses"
    response_id: Mapped[str] = mapped_column(String(25), primary_key=True)
    survey_id: Mapped[str] = mapped_column(ForeignKey("surveys.survey_id"))
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.customer_id"))
    product_id: Mapped[str] = mapped_column(ForeignKey("products.product_id"))
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.channel_id"))
    response_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rating: Mapped[int] = mapped_column(SmallInteger)
    nps_score: Mapped[int | None] = mapped_column(SmallInteger)
    response_text: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    survey: Mapped["Survey"] = relationship(back_populates="responses")  # noqa: F821
    customer: Mapped["Customer"] = relationship(back_populates="survey_responses")  # noqa: F821


class Review(Base):
    __tablename__ = "reviews"
    review_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.customer_id"))
    product_id: Mapped[str] = mapped_column(ForeignKey("products.product_id"))
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.channel_id"))
    rating: Mapped[int] = mapped_column(SmallInteger)
    review_title: Mapped[str] = mapped_column(String(160))
    review_text: Mapped[str] = mapped_column(Text)
    review_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    language: Mapped[str] = mapped_column(String(2))
    verified_purchase: Mapped[bool] = mapped_column(Boolean)
    helpful_votes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    customer: Mapped["Customer"] = relationship(back_populates="reviews")  # noqa: F821
    product: Mapped["Product"] = relationship(back_populates="reviews")  # noqa: F821


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    ticket_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.customer_id"))
    product_id: Mapped[str] = mapped_column(ForeignKey("products.product_id"))
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.channel_id"))
    category_theme_id: Mapped[str] = mapped_column(ForeignKey("themes.theme_id"))
    subject: Mapped[str] = mapped_column(String(200))
    priority: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    customer: Mapped["Customer"] = relationship(back_populates="tickets")  # noqa: F821
    messages: Mapped[list["SupportMessage"]] = relationship(
        back_populates="ticket", order_by="SupportMessage.message_seq"
    )


class SupportMessage(Base):
    __tablename__ = "support_messages"
    __table_args__ = (UniqueConstraint("ticket_id", "message_seq"),)
    message_id: Mapped[str] = mapped_column(String(25), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("support_tickets.ticket_id"))
    message_seq: Mapped[int] = mapped_column(SmallInteger)
    sender_type: Mapped[str] = mapped_column(String(8))
    message_text: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    language: Mapped[str] = mapped_column(String(2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    ticket: Mapped[SupportTicket] = relationship(back_populates="messages")

"""Master data entities: Customer, Product, Channel, Survey."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Customer(Base):
    __tablename__ = "customers"
    customer_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    first_name: Mapped[str] = mapped_column(String(60))
    last_name: Mapped[str] = mapped_column(String(60))
    email: Mapped[str] = mapped_column(String(120), unique=True)
    phone: Mapped[str] = mapped_column(String(20))
    city: Mapped[str] = mapped_column(String(60))
    state: Mapped[str] = mapped_column(String(60))
    country: Mapped[str] = mapped_column(String(60), default="India")
    preferred_language: Mapped[str] = mapped_column(String(2))
    segment: Mapped[str] = mapped_column(String(12))
    signup_date: Mapped[date] = mapped_column(Date)
    lifecycle_status: Mapped[str] = mapped_column(String(10))
    effective_from: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    reviews: Mapped[list["Review"]] = relationship(back_populates="customer")  # noqa: F821
    survey_responses: Mapped[list["SurveyResponse"]] = relationship(back_populates="customer")  # noqa: F821
    tickets: Mapped[list["SupportTicket"]] = relationship(back_populates="customer")  # noqa: F821


class Product(Base):
    __tablename__ = "products"
    product_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    sku: Mapped[str] = mapped_column(String(30), unique=True)
    product_name: Mapped[str] = mapped_column(String(120), unique=True)
    category: Mapped[str] = mapped_column(String(40))
    brand: Mapped[str] = mapped_column(String(60))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    launch_date: Mapped[date] = mapped_column(Date)
    discontinued_date: Mapped[date | None] = mapped_column(Date)
    lifecycle_status: Mapped[str] = mapped_column(String(12))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    reviews: Mapped[list["Review"]] = relationship(back_populates="product")  # noqa: F821


class Channel(Base):
    __tablename__ = "channels"
    channel_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    channel_code: Mapped[str] = mapped_column(String(30), unique=True)
    channel_name: Mapped[str] = mapped_column(String(60), unique=True)
    channel_type: Mapped[str] = mapped_column(String(12))
    supports_reviews: Mapped[bool] = mapped_column(Boolean)
    supports_surveys: Mapped[bool] = mapped_column(Boolean)
    supports_support: Mapped[bool] = mapped_column(Boolean)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Survey(Base):
    __tablename__ = "surveys"
    survey_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    survey_type: Mapped[str] = mapped_column(String(20))
    product_id: Mapped[str | None] = mapped_column(ForeignKey("products.product_id"))
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.channel_id"))
    question_text: Mapped[str] = mapped_column(Text)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    responses: Mapped[list["SurveyResponse"]] = relationship(back_populates="survey")  # noqa: F821

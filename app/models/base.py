"""Declarative base. DDL authority is database/schema.sql; these models mirror it."""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all ORM models."""

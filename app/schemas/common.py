"""Shared response schemas."""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    database: str
    database_detail: str
    schema_tables: int
    feedback_items: int
    models: dict[str, str | None]


class PageMeta(BaseModel):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)
    pages: int = Field(ge=0)


class Page(BaseModel, Generic[T]):
    items: list[T]
    meta: PageMeta


class ErrorResponse(BaseModel):
    detail: str


def page_meta(page: int, page_size: int, total: int) -> PageMeta:
    return PageMeta(page=page, page_size=page_size, total=total, pages=(total + page_size - 1) // page_size)

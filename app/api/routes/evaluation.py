"""Protected evaluation endpoint (ground truth never flows through inference APIs)."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException

from app.core.database import get_engine
from app.services import evaluation_service as ev

router = APIRouter(prefix="/api/evaluation", tags=["evaluation (restricted)"])


@router.get("/metrics", summary="Model, data-quality and BI metrics vs. human-reviewed ground truth",
            responses={401: {"description": "Missing token"}, 403: {"description": "Invalid token"},
                       409: {"description": "Ground-truth scale does not match the loaded dataset"}})
def metrics(x_evaluation_token: str | None = Header(default=None, alias="X-Evaluation-Token"),
            scale: Literal["starter", "full"] | None = None) -> dict[str, Any]:
    if not x_evaluation_token:
        raise HTTPException(401, "X-Evaluation-Token header required", headers={"WWW-Authenticate": "Token"})
    try:
        return ev.compute_metrics(get_engine(), x_evaluation_token, scale)
    except ev.EvaluationAccessError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ev.EvaluationNotConfiguredError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ev.EvaluationScaleMismatchError as exc:
        raise HTTPException(409, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc

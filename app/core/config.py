"""Central application configuration.

All important thresholds are configurable through environment variables (or a
local ``.env`` file). Nothing here is a secret by default; real credentials must
only live in ``.env``, which is git-ignored.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Typed settings loaded from the environment."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "InsightPulse"
    app_version: str = "1.0.0"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://insightpulse:insightpulse@localhost:5432/insightpulse"
    test_database_url: str = (
        "postgresql+psycopg://insightpulse:insightpulse@localhost:5432/insightpulse_test"
    )

    data_scale: str = Field(default="starter", pattern="^(starter|full)$")
    generator_seed: int = 20261022
    data_dir: Path = PROJECT_ROOT / "data"
    model_dir: Path = PROJECT_ROOT / "ml" / "saved_models"

    # Ground truth access control
    evaluation_access_token: str = ""

    # Model behaviour
    sentiment_uncertainty_threshold: float = Field(default=0.55, ge=0, le=1)
    multilingual_review_threshold: float = Field(default=0.70, ge=0, le=1)
    theme_default_threshold: float = Field(default=0.5, ge=0, le=1)
    prediction_batch_size: int = Field(default=5000, gt=0)

    # Trend detection
    trend_z_threshold: float = 2.0
    trend_min_increase: float = 0.10
    trend_min_volume: int = 8
    trend_baseline_periods: int = 6

    # Action generation
    action_lookback_days: int = 90
    action_min_negative_count: int = 5
    action_min_negative_share: float = 0.45
    action_min_lift: float = 1.3
    action_due_days_critical: int = 7
    action_due_days_high: int = 14
    action_due_days_medium: int = 30
    action_due_days_low: int = 45

    # Dashboard
    api_base_url: str = "http://localhost:8000"

    @property
    def ground_truth_dir(self) -> Path:
        return self.data_dir / "ground_truth"


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()

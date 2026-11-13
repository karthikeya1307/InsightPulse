"""Synthetic data: determinism, volumes, parent-before-child integrity, label rules, GT separation."""
import csv
import json
from pathlib import Path

import pytest

from generator.config import SCALES
from generator.feedback_generator import derive_sentiment
from generator.generate_data import generate


def _rows(root: Path, group: str, name: str) -> list[dict]:
    with (root / group / "starter" / f"{name}.csv").open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def two_runs(tmp_path_factory):
    a, b = tmp_path_factory.mktemp("gen_a"), tmp_path_factory.mktemp("gen_b")
    return a, generate("starter", 20261022, a), b, generate("starter", 20261022, b)


def test_same_seed_same_bytes(two_runs):
    _, ma, _, mb = two_runs
    assert ma["file_checksums_sha256"] == mb["file_checksums_sha256"]
    assert ma["row_counts"] == mb["row_counts"]
    assert len(ma["file_checksums_sha256"]) == 32


def test_same_ids_labels_and_relationships(two_runs):
    a, _, b, _ = two_runs
    for group, name, key in [("raw", "reviews", "review_id"), ("raw", "survey_responses", "response_id"),
                             ("raw", "support_messages", "message_id"), ("raw", "annotations", "annotation_id"),
                             ("ground_truth", "evaluation_set", "feedback_id")]:
        ra, rb = _rows(a, group, name), _rows(b, group, name)
        assert [r[key] for r in ra] == [r[key] for r in rb]
        assert ra == rb


def test_different_seed_changes_data(tmp_path, two_runs):
    _, ma, _, _ = two_runs
    other = generate("starter", 7, tmp_path)
    assert other["file_checksums_sha256"]["raw/starter/reviews.csv"] != ma["file_checksums_sha256"]["raw/starter/reviews.csv"]


def test_configured_volumes(two_runs):
    a, m, _, _ = two_runs
    cfg = SCALES["starter"]
    assert m["row_counts"]["raw/reviews"] == cfg.n_reviews == 500
    dups = m["derived_counts"]["anomalies_by_type"]["DUPLICATE_RESPONSE"]
    assert m["row_counts"]["raw/survey_responses"] == cfg.n_survey_responses + dups
    assert m["row_counts"]["raw/support_messages"] == cfg.n_support_messages == 200
    assert SCALES["full"].n_reviews == 50_000 and SCALES["full"].n_survey_responses == 50_000
    assert SCALES["full"].n_support_messages == 20_000


def test_children_reference_existing_parents(two_runs):
    from app.services.ingestion_service import load_dataset_files
    from app.services.validation_service import validate_files

    a, _, _, _ = two_runs
    ds = load_dataset_files("starter", a)
    report = validate_files(ds.tables, ds.annotations, ds.splits, ds.master_themes, ds.ticket_events)
    assert report.rejected == {} or all(not v for v in report.rejected.values())
    assert [i for i in report.issues if i.severity != "LOW"] == []


def test_derived_values_reconcile(two_runs):
    a, _, _, _ = two_runs
    msgs = _rows(a, "raw", "support_messages")
    for t in _rows(a, "raw", "support_tickets"):
        assert int(t["message_count"]) == sum(1 for m in msgs if m["ticket_id"] == t["ticket_id"])


def test_label_rule():
    assert derive_sentiment(["pos", "neg"]) == "MIXED"
    assert derive_sentiment(["pos", "neu"]) == "POSITIVE"
    assert derive_sentiment(["neg"]) == "NEGATIVE"
    assert derive_sentiment(["neu"]) == derive_sentiment([]) == "NEUTRAL"


def test_label_distribution_balanced_and_multilingual(two_runs):
    a, _, _, _ = two_runs
    ev = _rows(a, "ground_truth", "evaluation_set")
    labels = {r["sentiment_label"] for r in ev}
    assert labels == {"POSITIVE", "NEGATIVE", "NEUTRAL", "MIXED"}
    langs = {r["language"] for r in _rows(a, "raw", "reviews")}
    assert {"en", "hi", "te"} <= langs
    assert any(r["is_sarcasm"] == "true" for r in ev) and any(r["is_ambiguous"] == "true" for r in ev)


def test_ground_truth_kept_out_of_raw_inputs(two_runs):
    a, _, _, _ = two_runs
    forbidden = {"true_sentiment", "true_theme_ids", "is_sarcasm", "scenario_event", "ground_truth_label"}
    for f in (a / "raw" / "starter").glob("*.csv"):
        header = set(f.read_text(encoding="utf-8").splitlines()[0].split(","))
        assert not header & forbidden, f.name
    splits = {r["source_record_id"]: r["dataset_split"] for r in _rows(a, "raw", "dataset_splits")}
    ev_ids = {r["source_record_id"] for r in _rows(a, "ground_truth", "evaluation_set")}
    assert all(splits[s] == "TEST" for s in ev_ids)


def test_anomaly_registry_complete(two_runs):
    a, m, _, _ = two_runs
    reg = json.loads((a / "ground_truth" / "starter" / "ground_truth.json").read_text(encoding="utf-8"))
    assert {r["issue_type"] for r in reg} == {"LABEL_CONTRADICTION", "DUPLICATE_RESPONSE", "PRIVACY_LEAK",
                                              "INVALID_THEME", "TARGET_LEAKAGE"}
    for r in reg:
        assert r["issue_id"].startswith("ANOM-") and r["record_id"] and r["expected_detection"] and r["severity"]
        assert r["ground_truth_label"]

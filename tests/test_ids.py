"""ID generation: formats, zero padding, parent scoping, determinism, validation."""
import pytest

from app.utils import id_generator as ids


@pytest.mark.parametrize("fn,n,expected", [
    (ids.customer_id, 1, "CUS-000001"), (ids.product_id, 42, "PRD-000042"), (ids.channel_id, 7, "CHN-000007"),
    (ids.survey_id, 1, "SUR-2026-000001"), (ids.review_id, 123456, "REV-2026-123456"),
    (ids.ticket_id, 9, "TKT-2026-000009"), (ids.theme_id, 10, "THEME-000010"), (ids.action_id, 3, "ACT-2026-000003"),
])
def test_formats(fn, n, expected):
    assert fn(n) == expected


def test_child_ids_embed_parent():
    assert ids.response_id("SUR-2026-000001", 1) == "RESP-SUR-2026-000001-001"
    assert ids.message_id("TKT-2026-000001", 12) == "MSG-TKT-2026-000001-012"
    assert ids.parent_of("RESP-SUR-2026-000001-001") == "SUR-2026-000001"
    assert ids.parent_of("MSG-TKT-2026-000123-002") == "TKT-2026-000123"


@pytest.mark.parametrize("bad", [0, -1, 1.5, "1", True])
def test_invalid_counters_rejected(bad):
    with pytest.raises(ids.IdError):
        ids.customer_id(bad)


def test_overflow_rejected():
    with pytest.raises(ids.IdError):
        ids.review_id(1_000_000)
    with pytest.raises(ids.IdError):
        ids.response_id("SUR-2026-000001", 1000)


def test_invalid_parent_rejected():
    with pytest.raises(ids.IdError):
        ids.response_id("SURVEY-1", 1)
    with pytest.raises(ids.IdError):
        ids.message_id("TKT-26-1", 1)


def test_feedback_id_roundtrip_and_source_type():
    for sid, st in [("REV-2026-000001", "REVIEW"), ("RESP-SUR-2026-000002-003", "SURVEY_RESPONSE"),
                    ("MSG-TKT-2026-000004-001", "SUPPORT_MESSAGE")]:
        fid = ids.feedback_id(sid)
        assert fid == f"FB-{sid}" and ids.source_id_from_feedback_id(fid) == sid and ids.source_type_of(sid) == st
    with pytest.raises(ids.IdError):
        ids.feedback_id("CUS-000001")


def test_sequences_are_deterministic_and_scoped():
    a, b = ids.IdSequence("review"), ids.IdSequence("review")
    assert [a.next() for _ in range(3)] == [b.next() for _ in range(3)] == [
        "REV-2026-000001", "REV-2026-000002", "REV-2026-000003"]
    child = ids.ChildIdSequence("response")
    assert child.next("SUR-2026-000001") == "RESP-SUR-2026-000001-001"
    assert child.next("SUR-2026-000002") == "RESP-SUR-2026-000002-001"
    assert child.next("SUR-2026-000001") == "RESP-SUR-2026-000001-002"


def test_validation_patterns():
    assert ids.is_valid_id("survey_response", "RESP-SUR-2026-000001-001")
    assert not ids.is_valid_id("survey_response", "RESP-SUR-2026-1-1")
    assert not ids.is_valid_id("customer", "CUS-1")
    assert ids.counter_of("ACT-2026-000017") == 17

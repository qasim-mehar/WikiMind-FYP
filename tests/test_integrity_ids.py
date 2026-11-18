"""Pure determinism tests for the integrity content addresses."""

from __future__ import annotations

from uuid import UUID, uuid4

from wikimind.integrity.ids import (
    ZERO_UUID,
    batch_digest,
    batch_fingerprint,
    cost_event_uuid,
    deterministic_uuid,
    ledger_block_uuid,
)


def test_deterministic_uuid_is_stable_and_valid_v8() -> None:
    first = deterministic_uuid("wikimind-test-v1", "abc", "def")
    assert first == deterministic_uuid("wikimind-test-v1", "abc", "def")
    assert first != deterministic_uuid("wikimind-test-v1", "abd", "def")
    assert first.version == 8
    assert isinstance(first, UUID)


def test_domain_separation_and_separator_are_load_bearing() -> None:
    assert deterministic_uuid("one", "a", "b") != deterministic_uuid("two", "a", "b")
    # The \x1f separator prevents ('a\x1fb',) from colliding with ('a','b').
    assert deterministic_uuid("one", "a\x1fb") != deterministic_uuid("one", "a", "b")


def test_cost_event_uuid_is_a_function_of_the_line() -> None:
    line = '{"call_site":"compile.analyze","model":"m"}'
    assert cost_event_uuid(line) == cost_event_uuid(line)
    assert cost_event_uuid(line) != cost_event_uuid(line + " ")


def test_ledger_block_uuid_commits_to_every_field() -> None:
    kwargs = {
        "project_id": uuid4(),
        "prev_id": ZERO_UUID,
        "position": 0,
        "node_id": uuid4(),
        "claim_id": uuid4(),
        "block_type": "claim",
    }
    baseline = ledger_block_uuid(**kwargs)
    assert baseline == ledger_block_uuid(**kwargs)
    for field in ("project_id", "prev_id", "position", "node_id", "claim_id", "block_type"):
        changed = dict(kwargs)
        changed[field] = (
            uuid4()
            if field in {"project_id", "node_id", "claim_id"}
            else ("other" if field == "prev_id" else (99 if field == "position" else "derived"))
        )
        assert ledger_block_uuid(**changed) != baseline


def test_batch_digest_depends_on_order_and_membership() -> None:
    a, b = str(uuid4()), str(uuid4())
    assert batch_digest([(a, b)]) == batch_digest([(a, b)])
    assert batch_digest([(a, b)]) != batch_digest([(b, a)])
    assert batch_digest([(a, b)]) != batch_digest([(a, b), (a, str(uuid4()))])
    assert batch_digest([]) != batch_digest([(a, b)])


def test_batch_fingerprint_binds_project_and_anchor() -> None:
    project, anchor, claim = uuid4(), uuid4(), uuid4()
    baseline = batch_fingerprint(project, anchor, [(anchor, claim)])
    assert baseline == batch_fingerprint(project, anchor, [(anchor, claim)])
    assert baseline != batch_fingerprint(uuid4(), anchor, [(anchor, claim)])
    assert baseline != batch_fingerprint(project, uuid4(), [(anchor, claim)])
    assert baseline != batch_fingerprint(project, anchor, [])

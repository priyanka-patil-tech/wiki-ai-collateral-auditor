import pandas as pd
import duckdb
import pytest
from datetime import datetime, timezone

from src.extraction import minimal_pipeline
from src.extraction.minimal_pipeline import (
    append_reverted_claim_rows,
    ensure_reverted_claims_table,
    load_manifest_usernames,
    load_cohort_events_from_parquet_directory,
    load_usernames_from_parquet_directory,
)


def test_manifest_users_and_duckdb_write(tmp_path):
    manifest = tmp_path / "cohort_manifest.parquet"
    pd.DataFrame({"username": ["alice", "bob", "alice"]}).to_parquet(manifest, index=False)

    users = load_manifest_usernames(manifest)
    assert users == ["alice", "bob"]

    db_path = tmp_path / "reverted_claims.duckdb"
    ensure_reverted_claims_table(db_path)

    row = {
        "claim_id": "c1",
        "user_id": "alice",
        "page_id": 123,
        "page_title": "Example page",
        "claim_text": "Example claim",
        "doi": None,
        "isbn": None,
        "citation_title": "Example citation",
        "timestamp": "2026-09-23T12:00:00Z",
        "revert_revision_id": 999,
    }
    append_reverted_claim_rows([row], db_path)

    con = duckdb.connect(str(db_path))
    result = con.execute("SELECT COUNT(*) FROM fct_reverted_claims").fetchone()
    assert result[0] == 1


def test_load_usernames_from_all_parquets_recursively(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    pd.DataFrame({"username": ["alice", "bob", None]}).to_parquet(
        tmp_path / "cohort_a.parquet", index=False
    )
    pd.DataFrame({"username": ["bob", "carol", " "]}).to_parquet(
        nested / "cohort_b.parquet", index=False
    )
    pd.DataFrame({"page_title": ["Article"]}).to_parquet(
        nested / "unrelated.parquet", index=False
    )

    assert load_usernames_from_parquet_directory(tmp_path) == ["alice", "bob", "carol"]


def test_load_cohort_events_preserves_t0_and_deduplicates(tmp_path):
    pd.DataFrame(
        {
            "username": ["alice", "alice", "missing-time"],
            "t0_timestamp": ["2026-05-01T12:00:00Z", "2026-05-01T12:00:00Z", None],
        }
    ).to_parquet(tmp_path / "events.parquet", index=False)

    events = load_cohort_events_from_parquet_directory(tmp_path)

    assert len(events) == 1
    assert events[0]["username"] == "alice"
    assert events[0]["t0_timestamp"] == datetime(2026, 5, 1, 12, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_fetch_recent_user_revisions_fetches_full_t0_window(monkeypatch):
    requests = []
    responses = [
        {
            "query": {"usercontribs": [{"revid": 1}]},
            "continue": {"continue": "||", "uccontinue": "2026-10-31T00:00:00Z|1"},
        },
        {"query": {"usercontribs": [{"revid": 2}]}},
    ]

    class FakeWikiClient:
        def __init__(self, config, log_dir=None):
            pass

        async def get(self, params):
            requests.append(params)
            return responses.pop(0)

    monkeypatch.setattr(minimal_pipeline, "WikiClient", FakeWikiClient)

    revisions = await minimal_pipeline.fetch_recent_user_revisions(
        "alice",
        limit=500,
        t0_timestamp=datetime(2026, 10, 31, tzinfo=timezone.utc),
    )

    assert [revision["revid"] for revision in revisions] == [1, 2]
    assert requests[0]["ucstart"] == "2027-04-30T00:00:00Z"
    assert requests[0]["ucend"] == "2026-04-30T00:00:00Z"
    assert requests[0]["ucdir"] == "older"
    assert requests[0]["uclimit"] == 500
    assert requests[1]["uccontinue"] == "2026-10-31T00:00:00Z|1"
    assert requests[1]["continue"] == "||"


@pytest.mark.asyncio
async def test_directory_pipeline_batches_users_and_persists_rows(monkeypatch, tmp_path):
    pd.DataFrame(
        {
            "username": ["alice", "bob", "carol"],
            "t0_timestamp": ["2026-05-01T00:00:00Z"] * 3,
        }
    ).to_parquet(
        tmp_path / "cohort.parquet", index=False
    )
    batches = []
    persisted = []

    async def fake_run_cohort_event_batch(events, limit, concurrency, enable_logging):
        batches.append([event["username"] for event in events])
        return [{"user_id": event["username"], "claim_text": "claim"} for event in events]

    monkeypatch.setattr(minimal_pipeline, "run_cohort_event_batch", fake_run_cohort_event_batch)
    monkeypatch.setattr(
        minimal_pipeline,
        "append_reverted_claim_rows",
        lambda rows, db_path: persisted.extend(rows) or len(rows),
    )

    rows = await minimal_pipeline.run_parquet_directory_pipeline(
        parquet_dir=tmp_path,
        db_path=tmp_path / "audit.duckdb",
        batch_size=2,
        concurrency=1,
    )

    assert batches == [["alice", "bob"], ["carol"]]
    assert rows == persisted
    assert len(rows) == 3

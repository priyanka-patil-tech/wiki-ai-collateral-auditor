from __future__ import annotations

import argparse
import asyncio
import calendar
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pandas as pd

if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from src.extraction.revert_miner import detect_rollback_revision, extract_reverted_claim_rows, write_json_log
from src.extraction.med_stream_extractor import MedicalAuditExtractor
from src.ingestion.wiki_client import WikiClient, WikiClientConfig

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST_PATH = PROJECT_ROOT / "data" / "processed" / "cohort_manifest.parquet"
DEFAULT_DUCKDB_PATH = PROJECT_ROOT / "data" / "processed" / "pr_auditor.duckdb"
DEFAULT_JSON_LOG_PATH = PROJECT_ROOT / "logs" / "reverted_claims_minimal.json"
DEFAULT_POC_CHECKPOINT_PATH = PROJECT_ROOT / "data" / "processed" / "poc_100_treated_100_control_checkpoint.json"


def configure_logging(enabled: bool) -> None:
    """Enable pipeline logs on the console and in logs/revision_audit.log."""
    if not enabled:
        return

    log_path = PROJECT_ROOT / "logs" / "revision_audit.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
        force=True,
    )


def load_manifest_usernames(manifest_path: str | Path = DEFAULT_MANIFEST_PATH) -> list[str]:
    """Return the unique usernames from the cohort manifest parquet file."""
    manifest = Path(manifest_path)
    if not manifest.exists():
        raise FileNotFoundError(f"Cohort manifest not found: {manifest}")

    df = pd.read_parquet(manifest)
    usernames = [str(value).strip() for value in df["username"].dropna().tolist()]
    seen: set[str] = set()
    unique_users: list[str] = []
    for username in usernames:
        if username and username not in seen:
            seen.add(username)
            unique_users.append(username)
    return unique_users


def load_usernames_from_parquet_directory(parquet_dir: str | Path) -> list[str]:
    """Load and deduplicate usernames from every readable Parquet with a username column."""
    directory = Path(parquet_dir)
    if not directory.is_dir():
        raise FileNotFoundError(f"Parquet directory not found: {directory}")

    parquet_files = sorted(directory.rglob("*.parquet"))
    if not parquet_files:
        raise FileNotFoundError(f"No Parquet files found under: {directory}")

    usernames: list[str] = []
    seen: set[str] = set()
    for parquet_file in parquet_files:
        try:
            df = pd.read_parquet(parquet_file)
        except Exception:
            logger.exception("Could not read Parquet file %s; skipping", parquet_file)
            continue

        if "username" not in df.columns:
            logger.info("Skipping Parquet without username column: %s", parquet_file)
            continue

        for value in df["username"].dropna().tolist():
            username = str(value).strip()
            if username and username not in seen:
                seen.add(username)
                usernames.append(username)

    logger.info("Loaded %d unique usernames from %d Parquet files", len(usernames), len(parquet_files))
    return usernames


def _cohort_events_from_frame(df: pd.DataFrame, cohort_type: str | None = None) -> list[dict[str, Any]]:
    """Convert cohort rows with usernames and T0 values into unique user-event records."""
    if "username" not in df.columns or "t0_timestamp" not in df.columns:
        return []
    if cohort_type is not None:
        if "cohort_type" not in df.columns:
            return []
        df = df[df["cohort_type"].astype(str).str.strip() == cohort_type]

    events: list[dict[str, Any]] = []
    seen: set[tuple[str, pd.Timestamp]] = set()
    for row in df[["username", "t0_timestamp"]].dropna().itertuples(index=False, name=None):
        username = str(row[0]).strip()
        timestamp = pd.to_datetime(row[1], utc=True, errors="coerce")
        if not username or pd.isna(timestamp):
            continue
        key = (username, timestamp)
        if key not in seen:
            seen.add(key)
            events.append({"username": username, "t0_timestamp": timestamp.to_pydatetime()})
    return events


def load_cohort_events(
    manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
    cohort_type: str | None = None,
) -> list[dict[str, Any]]:
    """Load unique username/T0 events from one cohort manifest."""
    manifest = Path(manifest_path)
    if not manifest.exists():
        raise FileNotFoundError(f"Cohort manifest not found: {manifest}")
    return _cohort_events_from_frame(pd.read_parquet(manifest), cohort_type=cohort_type)


def load_cohort_events_from_parquet_directory(parquet_dir: str | Path) -> list[dict[str, Any]]:
    """Load unique username/T0 events from all Parquets under a directory."""
    directory = Path(parquet_dir)
    if not directory.is_dir():
        raise FileNotFoundError(f"Parquet directory not found: {directory}")

    parquet_files = sorted(directory.rglob("*.parquet"))
    if not parquet_files:
        raise FileNotFoundError(f"No Parquet files found under: {directory}")

    events: list[dict[str, Any]] = []
    seen: set[tuple[str, pd.Timestamp]] = set()
    for parquet_file in parquet_files:
        try:
            frame = pd.read_parquet(parquet_file)
        except Exception:
            logger.exception("Could not read Parquet file %s; skipping", parquet_file)
            continue
        for event in _cohort_events_from_frame(frame):
            key = (event["username"], pd.Timestamp(event["t0_timestamp"]))
            if key not in seen:
                seen.add(key)
                events.append(event)

    logger.info("Loaded %d unique username/T0 events from %d Parquet files", len(events), len(parquet_files))
    return events


def _shift_months(value: datetime, months: int) -> datetime:
    """Shift a datetime by calendar months, clamping to the target month's last day."""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def ensure_reverted_claims_table(db_path: str | Path = DEFAULT_DUCKDB_PATH) -> duckdb.DuckDBPyConnection:
    """Create the fct_reverted_claims table if it is not present."""
    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_file))
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS fct_reverted_claims (
            claim_id VARCHAR,
            user_id VARCHAR,
            page_id BIGINT,
            page_title VARCHAR,
            claim_text VARCHAR,
            doi VARCHAR,
            isbn VARCHAR,
            citation_title VARCHAR,
            timestamp TIMESTAMP,
            revert_revision_id BIGINT
        )
        """
    )
    return con


def append_reverted_claim_rows(rows: list[dict[str, Any]], db_path: str | Path = DEFAULT_DUCKDB_PATH) -> int:
    """Append extracted claim rows to the DuckDB fact table."""
    if not rows:
        return 0

    con = ensure_reverted_claims_table(db_path)
    records = []
    for row in rows:
        records.append(
            (
                row.get("claim_id") or f"{row.get('user_id','unknown')}::{row.get('revert_revision_id','0')}::{row.get('page_id','0')}::{row.get('timestamp','')}",
                row.get("user_id"),
                row.get("page_id"),
                row.get("page_title"),
                row.get("claim_text"),
                row.get("doi"),
                row.get("isbn"),
                row.get("citation_title"),
                row.get("timestamp"),
                row.get("revert_revision_id"),
            )
        )

    con.executemany(
        """
        INSERT INTO fct_reverted_claims (
            claim_id, user_id, page_id, page_title, claim_text, doi, isbn,
            citation_title, timestamp, revert_revision_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        records,
    )
    con.close()
    return len(records)


async def fetch_recent_user_revisions(
    username: str,
    limit: int = 500,
    enable_logging: bool = False,
    t0_timestamp: datetime | None = None,
) -> list[dict[str, Any]]:
    """Fetch recent revisions, or all contributions in the T0 +/- six-month window."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    api_page_size = min(limit, 500)
    config = WikiClientConfig(
        api_endpoint="https://en.wikipedia.org/w/api.php",
        user_agent="WikiAICitationAuditor/1.0 (mailto:pnp1609@uw.edu)",
        rate_limit_per_second=2,
        api_call_interval_seconds=0.0,
        enable_logging=enable_logging,
    )
    client = WikiClient(config=config, log_dir=Path("logs") if enable_logging else None)
    params = {
        "action": "query",
        "list": "usercontribs",
        "ucuser": username,
        "uclimit": api_page_size,
        "ucprop": "ids|title|timestamp|comment|tags",
        "format": "json",
    }
    if t0_timestamp is not None:
        if t0_timestamp.tzinfo is None:
            t0_timestamp = t0_timestamp.replace(tzinfo=timezone.utc)
        else:
            t0_timestamp = t0_timestamp.astimezone(timezone.utc)
        window_start = _shift_months(t0_timestamp, -6)
        window_end = _shift_months(t0_timestamp, 6)
        params.update(
            {
                "ucstart": window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ucend": window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ucdir": "older",
            }
        )
        logger.info(
            "Fetching contributions for %s from %s through %s",
            username,
            params["ucend"],
            params["ucstart"],
        )
    else:
        logger.info("Fetching recent revisions for %s (limit=%s)", username, api_page_size)

    rows: list[dict[str, Any]] = []
    continuation: dict[str, Any] = {}
    while True:
        data = await client.get({**params, **continuation})
        rows.extend(data.get("query", {}).get("usercontribs", []))
        continuation = data.get("continue", {}) if t0_timestamp is not None else {}
        if not continuation:
            break
    logger.info("Fetched %s revisions for %s", len(rows), username)
    return rows


async def fetch_compare_diff(from_rev: int, to_rev: int, enable_logging: bool = False) -> dict[str, Any]:
    """Fetch the compare payload for a revision pair."""
    config = WikiClientConfig(
        api_endpoint="https://en.wikipedia.org/w/api.php",
        user_agent="WikiAICitationAuditor/1.0 (mailto:pnp1609@uw.edu)",
        rate_limit_per_second=2,
        api_call_interval_seconds=0.0,
        enable_logging=enable_logging,
    )
    client = WikiClient(config=config, log_dir=Path("logs") if enable_logging else None)
    logger.info("Comparing revisions %s -> %s", from_rev, to_rev)
    data = await client.compare_revisions(from_rev, to_rev)
    return data


async def run_minimal_pipeline(
    username: str,
    limit: int = 500,
    enable_logging: bool = False,
    medical_extractor: MedicalAuditExtractor | None = None,
    t0_timestamp: datetime | None = None,
) -> list[dict[str, Any]]:
    """Minimal end-to-end pipeline for one user: fetch recent revisions, detect rollbacks, extract claims, and log JSON."""
    logger.info("Starting minimal pipeline for user=%s limit=%s", username, limit)
    medical_extractor = medical_extractor or MedicalAuditExtractor()
    recent = await fetch_recent_user_revisions(
        username=username,
        limit=limit,
        enable_logging=enable_logging,
        t0_timestamp=t0_timestamp,
    )
    all_rows: list[dict[str, Any]] = []
    medical_rows: list[dict[str, Any]] = []

    titles = list(dict.fromkeys(str(rev.get("title") or "") for rev in recent if rev.get("title")))
    medical_titles: set[str] = set()
    if titles:
        try:
            async with httpx.AsyncClient() as medical_client:
                medical_titles.update(await medical_extractor.filter_medical_pages(titles, medical_client))
            logger.info("Identified %d medical pages for %s", len(medical_titles), username)
            logger.debug("Medical page titles for %s: %s", username, sorted(medical_titles))
        except Exception:
            logger.exception("Medical page filtering failed for %s; continuing with generic extraction", username)

    for rev in recent:
        comment = rev.get("comment") or ""
        tags = rev.get("tags") or []
        if not detect_rollback_revision(comment, tags=tags):
            continue

        from_rev = rev.get("revid")
        parent_id = rev.get("parentid")
        if from_rev is None or parent_id is None:
            continue

        logger.info("Detected revert candidate for %s at revision %s", username, from_rev)
        compare_data = await fetch_compare_diff(parent_id, from_rev, enable_logging=enable_logging)
        diff_text = compare_data.get("compare", {}).get("*", "")
        if not diff_text:
            logger.warning("No diff text returned for %s compare %s -> %s", username, parent_id, from_rev)
            continue

        extracted = extract_reverted_claim_rows(diff_text)
        logger.info("Extracted %s claim rows for %s from revision %s", len(extracted), username, from_rev)
        for row in extracted:
            row["user_id"] = username
            row["page_id"] = rev.get("pageid")
            row["page_title"] = rev.get("title")
            row["timestamp"] = rev.get("timestamp")
            row["revert_revision_id"] = from_rev
            all_rows.append(row)

            if rev.get("title") not in medical_titles or not row.get("raw_citation"):
                continue

            medical_citations = medical_extractor.parse_med_wikitext(str(row["raw_citation"]))
            for citation_index, citation in enumerate(medical_citations):
                medical_rows.append(
                    {
                        "claim_id": (
                            f"{username}::{rev.get('title')}::{from_rev}::"
                            f"{citation.get('pmid') or citation.get('doi') or citation_index}"
                        ),
                        "user_id": None,
                        "username": username,
                        "page_title": rev.get("title"),
                        "rev_id": from_rev,
                        "rev_timestamp": rev.get("timestamp"),
                        "claim_text": row.get("claim_text"),
                        "pmid": citation.get("pmid"),
                        "doi": citation.get("doi"),
                        "journal_title": citation.get("journal"),
                    }
                )

    if medical_rows:
        saved_count = medical_extractor.save_rows(medical_rows)
        logger.info("Saved %d medical reverted claims for %s", saved_count, username)

    if enable_logging:
        write_json_log(all_rows, DEFAULT_JSON_LOG_PATH)
    logger.info("Finished minimal pipeline for %s; rows=%s", username, len(all_rows))
    return all_rows


def load_manifest_users_by_cohort(
    manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
    cohort_type: str | None = None,
    limit: int | None = None,
) -> list[str]:
    """Return deduplicated usernames for a given cohort type, optionally capped by limit."""
    df = pd.read_parquet(Path(manifest_path))
    if cohort_type is not None:
        df = df[df["cohort_type"].astype(str).str.strip() == cohort_type]

    usernames = [str(value).strip() for value in df["username"].dropna().tolist()]
    unique_users: list[str] = []
    seen: set[str] = set()
    for username in usernames:
        if username and username not in seen:
            seen.add(username)
            unique_users.append(username)

    if limit is not None:
        return unique_users[:limit]
    return unique_users


def load_checkpoint(checkpoint_path: str | Path = DEFAULT_POC_CHECKPOINT_PATH) -> set[str]:
    """Load previously processed usernames from a JSON checkpoint file."""
    path = Path(checkpoint_path)
    if not path.exists():
        return set()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        processed = payload.get("processed", [])
        return {str(item) for item in processed}
    except Exception:
        return set()


def save_checkpoint(processed: set[str], checkpoint_path: str | Path = DEFAULT_POC_CHECKPOINT_PATH) -> None:
    """Persist processed usernames to a JSON checkpoint file."""
    path = Path(checkpoint_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "processed": sorted(processed),
        "count": len(processed),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def run_user_batch(
    usernames: list[str],
    limit: int = 20,
    concurrency: int = 5,
    enable_logging: bool = False,
) -> list[dict[str, Any]]:
    """Process a batch of usernames in parallel, with bounded concurrency."""
    if not usernames:
        return []

    semaphore = asyncio.Semaphore(concurrency)
    medical_extractor = MedicalAuditExtractor()

    async def process_one(username: str) -> list[dict[str, Any]]:
        async with semaphore:
            try:
                return await run_minimal_pipeline(
                    username=username,
                    limit=limit,
                    enable_logging=enable_logging,
                    medical_extractor=medical_extractor,
                )
            except Exception as exc:
                print(f"ERROR {username}: {exc}", file=sys.stderr)
                return []

    tasks = [asyncio.create_task(process_one(username)) for username in usernames]
    batch_results = await asyncio.gather(*tasks)
    output: list[dict[str, Any]] = []
    for rows in batch_results:
        output.extend(rows)
    return output


async def run_cohort_event_batch(
    events: list[dict[str, Any]],
    limit: int = 500,
    concurrency: int = 5,
    enable_logging: bool = False,
) -> list[dict[str, Any]]:
    """Process username/T0 events concurrently using their cohort-specific windows."""
    if not events:
        return []

    semaphore = asyncio.Semaphore(concurrency)
    medical_extractor = MedicalAuditExtractor()

    async def process_event(event: dict[str, Any]) -> list[dict[str, Any]]:
        async with semaphore:
            username = event["username"]
            try:
                return await run_minimal_pipeline(
                    username=username,
                    limit=limit,
                    enable_logging=enable_logging,
                    medical_extractor=medical_extractor,
                    t0_timestamp=event["t0_timestamp"],
                )
            except Exception as exc:
                print(f"ERROR {username}: {exc}", file=sys.stderr)
                return []

    tasks = [asyncio.create_task(process_event(event)) for event in events]
    batch_results = await asyncio.gather(*tasks)
    return [row for rows in batch_results for row in rows]


async def run_poc_treated_control_pipeline(
    manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
    db_path: str | Path = DEFAULT_DUCKDB_PATH,
    limit: int = 500,
    treated_limit: int = 100,
    control_limit: int = 100,
    batch_size: int = 50,
    concurrency: int = 5,
    checkpoint_path: str | Path = DEFAULT_POC_CHECKPOINT_PATH,
    enable_logging: bool = False,
) -> list[dict[str, Any]]:
    """Run a POC across 100 treated + 100 control users in async batches, with checkpoint resume support."""
    treated_events = load_cohort_events(manifest_path, cohort_type="TREATED_AI")
    control_events = load_cohort_events(manifest_path, cohort_type="CONTROL_BEHAVIORAL")
    treated_users = list(dict.fromkeys(event["username"] for event in treated_events))[:treated_limit]
    control_users = list(dict.fromkeys(event["username"] for event in control_events))[:control_limit]
    selected_users = set(treated_users + control_users)
    selected_events = [
        event
        for event in treated_events + control_events
        if event["username"] in selected_users
    ]
    checkpoint = load_checkpoint(checkpoint_path)

    ordered_events = [event for event in selected_events if event["username"] not in checkpoint]

    all_rows: list[dict[str, Any]] = []
    processed: set[str] = set(checkpoint)

    for start in range(0, len(ordered_events), batch_size):
        batch = ordered_events[start : start + batch_size]
        batch_rows = await run_cohort_event_batch(
            batch,
            limit=limit,
            concurrency=concurrency,
            enable_logging=enable_logging,
        )
        all_rows.extend(batch_rows)
        processed.update(event["username"] for event in batch)
        save_checkpoint(processed, checkpoint_path)

        if batch_rows:
            append_reverted_claim_rows(batch_rows, db_path)

    if enable_logging:
        write_json_log(all_rows, DEFAULT_JSON_LOG_PATH)
    return all_rows


async def run_cohort_pipeline(
    manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
    db_path: str | Path = DEFAULT_DUCKDB_PATH,
    limit: int = 500,
    max_users: int | None = None,
    batch_size: int = 50,
    concurrency: int = 5,
    enable_logging: bool = False,
) -> list[dict[str, Any]]:
    """Run the minimal pipeline across usernames in the cohort manifest in chunks of 50 until all records are covered."""
    events = load_cohort_events(manifest_path)
    if max_users is not None:
        selected_usernames = set(dict.fromkeys(event["username"] for event in events).__iter__().__next__() for _ in [])
        selected_usernames = set(list(dict.fromkeys(event["username"] for event in events))[:max_users])
        events = [event for event in events if event["username"] in selected_usernames]

    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    all_rows: list[dict[str, Any]] = []
    for start in range(0, len(events), batch_size):
        batch = events[start : start + batch_size]
        batch_rows = await run_cohort_event_batch(
            batch,
            limit=limit,
            concurrency=concurrency,
            enable_logging=enable_logging,
        )
        all_rows.extend(batch_rows)
        if batch_rows:
            append_reverted_claim_rows(batch_rows, db_path)

    if enable_logging:
        write_json_log(all_rows, DEFAULT_JSON_LOG_PATH)
    return all_rows


async def run_parquet_directory_pipeline(
    parquet_dir: str | Path,
    db_path: str | Path = DEFAULT_DUCKDB_PATH,
    limit: int = 20,
    batch_size: int = 50,
    concurrency: int = 5,
    enable_logging: bool = False,
) -> list[dict[str, Any]]:
    """Audit unique users from all Parquets under a directory and persist rows to DuckDB."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if concurrency <= 0:
        raise ValueError("concurrency must be positive")

    usernames = load_usernames_from_parquet_directory(parquet_dir)
    events = load_cohort_events_from_parquet_directory(parquet_dir)
    logger.info("Starting directory-wide audit for %d username/T0 events", len(events))
    all_rows: list[dict[str, Any]] = []

    for start in range(0, len(events), batch_size):
        batch = events[start : start + batch_size]
        logger.info("Processing event batch %d-%d of %d", start + 1, start + len(batch), len(events))
        batch_rows = await run_cohort_event_batch(
            batch,
            limit=limit,
            concurrency=concurrency,
            enable_logging=enable_logging,
        )
        all_rows.extend(batch_rows)
        if batch_rows:
            append_reverted_claim_rows(batch_rows, db_path)

    if enable_logging:
        write_json_log(all_rows, DEFAULT_JSON_LOG_PATH)
    logger.info("Finished directory-wide audit; events=%d rows=%d", len(events), len(all_rows))
    return all_rows


def build_arg_parser() -> argparse.ArgumentParser:
    """Create a CLI parser for single-user, all-users, and POC cohort runs."""
    parser = argparse.ArgumentParser(description="Minimal Wikipedia revert mining pipeline")
    parser.add_argument("user", nargs="?", default="Re000searchist", help="Single username to process")
    parser.add_argument("--all", action="store_true", help="Run full cohort pipeline across the parquet manifest")
    parser.add_argument("--all-parquet", action="store_true", help="Run all unique users found in Parquet files under --parquet-dir")
    parser.add_argument("--poc", action="store_true", help="Run 100 treated + 100 control POC pipeline")
    parser.add_argument("--debug-logs", action="store_true", help="Enable verbose request and payload logging")
    parser.add_argument("--limit", type=int, default=500, help="usercontribs page size; T0-window runs paginate through the full window")
    parser.add_argument("--batch-size", type=int, default=50, help="Users processed per batch")
    parser.add_argument("--concurrency", type=int, default=5, help="Concurrent users within a batch")
    parser.add_argument("--treated-limit", type=int, default=100, help="Number of treated users to include in POC")
    parser.add_argument("--control-limit", type=int, default=100, help="Number of control users to include in POC")
    parser.add_argument("--manifest", type=str, default=str(DEFAULT_MANIFEST_PATH), help="Path to the cohort parquet manifest")
    parser.add_argument("--parquet-dir", type=str, default=str(PROJECT_ROOT / "data" / "processed"), help="Directory to recursively scan for Parquet files in --all-parquet mode")
    parser.add_argument("--db-path", type=str, default=str(DEFAULT_DUCKDB_PATH), help="DuckDB output path")
    parser.add_argument("--checkpoint", type=str, default=str(DEFAULT_POC_CHECKPOINT_PATH), help="Checkpoint path for resumable POC runs")
    return parser


if __name__ == "__main__":
    parser = build_arg_parser()
    args = parser.parse_args()
    configure_logging(args.debug_logs)

    if args.all_parquet:
        rows = asyncio.run(
            run_parquet_directory_pipeline(
                parquet_dir=args.parquet_dir,
                db_path=args.db_path,
                limit=args.limit,
                batch_size=args.batch_size,
                concurrency=args.concurrency,
                enable_logging=args.debug_logs,
            )
        )
        print(json.dumps(rows[:5], ensure_ascii=False, indent=2))
        print(f"TOTAL_ROWS={len(rows)}")
    elif args.all:
        rows = asyncio.run(
            run_cohort_pipeline(
                manifest_path=args.manifest,
                db_path=args.db_path,
                limit=args.limit,
                batch_size=args.batch_size,
                concurrency=args.concurrency,
                enable_logging=args.debug_logs,
            )
        )
        print(json.dumps(rows[:5], ensure_ascii=False, indent=2))
        print(f"TOTAL_ROWS={len(rows)}")
    elif args.poc:
        rows = asyncio.run(
            run_poc_treated_control_pipeline(
                manifest_path=args.manifest,
                db_path=args.db_path,
                limit=args.limit,
                treated_limit=args.treated_limit,
                control_limit=args.control_limit,
                batch_size=args.batch_size,
                concurrency=args.concurrency,
                checkpoint_path=args.checkpoint,
                enable_logging=args.debug_logs,
            )
        )
        print(json.dumps(rows[:5], ensure_ascii=False, indent=2))
        print(f"TOTAL_ROWS={len(rows)}")
    else:
        rows = asyncio.run(run_minimal_pipeline(args.user, limit=args.limit, enable_logging=args.debug_logs))
        print(json.dumps(rows, ensure_ascii=False, indent=2))

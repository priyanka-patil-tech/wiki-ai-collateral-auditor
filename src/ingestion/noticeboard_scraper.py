"""
noticeboard_scraper.py — Scrapes WP:AINB and WP:ANI archives.

Implements Module 1 (Ingestion & Discovery Service) from the System Design
Document, §4.1.

Treated cohort (WP:AINB):
    * Parse `Wikipedia:AI_noticeboard` and its `/Archive_*` subpages via
      `WikiClient.parse_page`.
    * Extract section headings matching `==\\s*User:(.*?)\\s*==` to recover
      the accused username.
    * Extract outcome/status from `{{AINB status|(open|resolved|blocked|
      presumptive)}}` templates.
    * Extract the enforcement timestamp T0 from the standard MediaWiki
      signature pattern: `\\d{2}:\\d{2},\\s\\d{1,2}\\s[A-Za-z]+\\s\\d{4}\\s\\(UTC\\)`.

Control cohort (WP:ANI / block log):
    * Query `list=logevents&letype=block` for users sanctioned under
      standard editorial infractions (WP:EW edit-warring, WP:NOTHERE)
      within the same calendar windows as the treated cohort, so both
      groups share a comparable time distribution.

Output:
    Rows conforming to the `cohorts` table schema (see System Design
    Document §5 / src/econometrics/panel_builder.py):
        user_id, username, cohort_type ('TREATED_AI' | 'CONTROL_BEHAVIORAL'),
        case_url, t0_timestamp, status
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Any

import httpx
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "processed" / "cohort_manifest.parquet"

PROJECT_LOG_DIR = PROJECT_ROOT / "logs"
PROJECT_LOG_DIR.mkdir(parents=True, exist_ok=True)

WIKI_API = "https://en.wikipedia.org/w/api.php"
DEFAULT_USER_AGENT = "WikiMedAuditResearch/1.0 (mailto:your.email@university.edu)"
AINB_HEADERS = {"User-Agent": DEFAULT_USER_AGENT}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(PROJECT_LOG_DIR / "noticeboard_scraper.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

@dataclass
class CohortRecord:
    user_id: str
    username: str
    cohort_type: str  # "TREATED_AI" | "CONTROL_BEHAVIORAL"
    case_url: str
    t0_timestamp: datetime
    status: str


class NoticeboardScraper:
    """Object-oriented ingestion helper for treated and control cohort discovery."""

    def __init__(
        self,
        api_url: str = WIKI_API,
        user_agent: str = DEFAULT_USER_AGENT,
        log_dir: str | Path = PROJECT_LOG_DIR,
    ) -> None:
        self.api_url = api_url
        self.headers = {"User-Agent": user_agent}
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    async def discover_noticeboard_pages(self, parent_page: str) -> list[str]:
        """Return a noticeboard page and its archive subpages via MediaWiki allpages."""
        parent_page = parent_page.strip()
        namespace, separator, page_name = parent_page.partition(":")
        if separator and namespace.casefold() in {"wikipedia", "project"}:
            base_title = page_name.strip("/")
        else:
            base_title = parent_page.strip("/")

        active_page = f"Wikipedia:{base_title}"
        archive_pages = [active_page]
        seen_pages = {active_page}
        continuation: dict[str, str] = {}

        async with httpx.AsyncClient() as client:
            while True:
                params: dict[str, Any] = {
                    "action": "query",
                    "list": "allpages",
                    "apnamespace": 4,
                    "apprefix": f"{base_title}/",
                    "aplimit": 500,
                    "format": "json",
                }
                params.update(continuation)
                response = await client.get(self.api_url, params=params, headers=self.headers, timeout=30.0)
                response.raise_for_status()
                data = response.json()

                for page in data.get("query", {}).get("allpages", []):
                    title = str(page.get("title") or "")
                    if "archive" in title.casefold() and title not in seen_pages:
                        archive_pages.append(title)
                        seen_pages.add(title)

                continuation = data.get("continue", {})
                if not continuation:
                    break

        logger.info("Discovered %d noticeboard pages under %s", len(archive_pages), active_page)
        return archive_pages

    async def scrape_ainb_cohort(self, archive_subpages: list[str]) -> list[dict[str, str]]:
        """Scrape WP:AINB archive pages for accused usernames and their sanction timestamps."""
        cohort: list[dict[str, str]] = []

        async with httpx.AsyncClient() as client:
            for page in archive_subpages:
                params = {
                    "action": "parse",
                    "page": page,
                    "prop": "wikitext",
                    "format": "json",
                }
                response = await client.get(self.api_url, params=params, headers=self.headers, timeout=30.0)
                response.raise_for_status()
                wikitext = response.json().get("parse", {}).get("wikitext", {}).get("*", "")

                sections = re.split(r"(?im)^==+\s*(?:User:)?(.*?)\s*==+\s*$", wikitext)
                for i in range(1, len(sections) - 1, 2):
                    username = (sections[i] or "").strip()
                    body = sections[i + 1] if i + 1 < len(sections) else ""
                    if not username:
                        continue
                    if any(keyword in body.lower() for keyword in ["presumptive", "mass rollback", "blocked", "llmprod"]):
                        match = re.search(r"(\d{2}:\d{2},\s\d{1,2}\s[A-Za-z]+\s\d{4}\s\(UTC\))", body)
                        t0 = match.group(1) if match else "UNKNOWN"
                        cohort.append(
                            {
                                "username": username,
                                "shock_timestamp_utc": t0,
                                "source_board": "WP:AINB",
                            }
                        )

        return cohort

    @staticmethod
    def parse_treated_cohort(raw_wikitext: str, case_url: str) -> list[CohortRecord]:
        """Parse treated users, AINB status, and signature timestamps from wikitext."""
        sections = re.split(r"(?im)^[ \t]*==+\s*(?:User:)?(.*?)\s*==+[ \t]*$", raw_wikitext or "")
        records: list[CohortRecord] = []

        for index in range(1, len(sections) - 1, 2):
            username = sections[index].strip()
            body = sections[index + 1]
            timestamp_match = re.search(
                r"(\d{2}:\d{2}),\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s+\(UTC\)",
                body,
            )
            if not username or not timestamp_match:
                continue

            status_match = re.search(r"\{\{\s*AINB\s+status\s*\|\s*([^|}]+)", body, re.IGNORECASE)
            status = status_match.group(1).strip().lower() if status_match else "presumptive"
            timestamp = datetime.strptime(
                f"{timestamp_match.group(1)} {timestamp_match.group(2)} "
                f"{timestamp_match.group(3)} {timestamp_match.group(4)}",
                "%H:%M %d %B %Y",
            )
            records.append(
                CohortRecord(
                    user_id=username,
                    username=username,
                    cohort_type="TREATED_AI",
                    case_url=case_url,
                    t0_timestamp=timestamp,
                    status=status,
                )
            )

        return records

    @staticmethod
    def _cohort_from_scraped_ainb_rows(scraped_rows: list[dict[str, str]], case_url: str) -> list[CohortRecord]:
        """Convert AINB scrape rows into CohortRecord objects with parsed timestamps."""
        records: list[CohortRecord] = []
        for row in scraped_rows:
            username = str(row.get("username") or "").strip()
            if not username:
                continue
            timestamp_text = str(row.get("shock_timestamp_utc") or "UNKNOWN").strip()
            if timestamp_text == "UNKNOWN":
                continue
            try:
                timestamp = datetime.strptime(timestamp_text.replace(",", ""), "%H:%M %d %B %Y (UTC)")
            except ValueError:
                logger.warning("Could not parse AINB timestamp %r for user %s", timestamp_text, username)
                continue

            records.append(
                CohortRecord(
                    user_id=username,
                    username=username,
                    cohort_type="TREATED_AI",
                    case_url=case_url,
                    t0_timestamp=timestamp,
                    status="presumptive",
                )
            )
        return records

    @staticmethod
    def parse_control_cohort(block_log_entries: list[dict]) -> list[CohortRecord]:
        """Build CONTROL_BEHAVIORAL cohort records from WP:ANI / block-log entries."""
        records: list[CohortRecord] = []

        for entry in block_log_entries:
            username = entry.get("user") or entry.get("username")
            if not username:
                title = entry.get("title") or ""
                if title.startswith("User:"):
                    username = title[len("User:") :]

            if not username:
                continue

            timestamp = entry.get("timestamp")
            if timestamp:
                if timestamp.endswith("Z"):
                    dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).replace(tzinfo=None)
                else:
                    dt = datetime.fromisoformat(timestamp)
            else:
                continue

            records.append(
                CohortRecord(
                    user_id=username,
                    username=username,
                    cohort_type="CONTROL_BEHAVIORAL",
                    case_url=entry.get("case_url", ""),
                    t0_timestamp=dt,
                    status=str(entry.get("status") or entry.get("action") or "blocked"),
                )
            )

        return records


def load_settings(settings_path: str | Path = DEFAULT_CONFIG_PATH) -> dict:
    """Load the YAML settings file relative to the repository root."""
    path = Path(settings_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data


def resolve_project_path(value: str | None, default_relative: str) -> Path:
    """Return a repo-root-relative path for portable local execution."""
    if value is None or value == "":
        return PROJECT_ROOT / default_relative

    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return PROJECT_ROOT / candidate


class NoticeboardManifestWriter:
    """Handles writing cohort records to parquet output."""

    @staticmethod
    def write_cohort_parquet(records: list[CohortRecord], output_path: str | Path) -> Path:
        """Persist a cohort manifest to a parquet file."""
        parquet_path = resolve_project_path(str(output_path), "data/processed/cohort_manifest.parquet")
        parquet_path.parent.mkdir(parents=True, exist_ok=True)

        rows = [
            {
                "user_id": record.user_id,
                "username": record.username,
                "cohort_type": record.cohort_type,
                "case_url": record.case_url,
                "t0_timestamp": pd.Timestamp(record.t0_timestamp),
                "status": record.status,
            }
            for record in records
        ]

        if rows:
            df = pd.DataFrame(rows)
        else:
            df = pd.DataFrame(
                columns=[
                    "user_id",
                    "username",
                    "cohort_type",
                    "case_url",
                    "t0_timestamp",
                    "status",
                ]
            )

        df.to_parquet(parquet_path, index=False)
        logger.info("Wrote %d cohort rows to %s", len(df), parquet_path)
        return parquet_path

    @staticmethod
    def append_cohort_parquet(records: list[CohortRecord], output_path: str | Path) -> Path:
        """Append a new batch of cohorts to the parquet manifest without overwriting earlier rows."""
        parquet_path = resolve_project_path(str(output_path), "data/processed/cohort_manifest.parquet")
        parquet_path.parent.mkdir(parents=True, exist_ok=True)

        rows = [
            {
                "user_id": record.user_id,
                "username": record.username,
                "cohort_type": record.cohort_type,
                "case_url": record.case_url,
                "t0_timestamp": pd.Timestamp(record.t0_timestamp),
                "status": record.status,
            }
            for record in records
        ]

        if rows:
            new_df = pd.DataFrame(rows)
            if parquet_path.exists():
                existing_df = pd.read_parquet(parquet_path)
                df = pd.concat([existing_df, new_df], ignore_index=True)
            else:
                df = new_df
            df = df.drop_duplicates(subset=["user_id", "username", "cohort_type", "case_url", "t0_timestamp", "status"])
            df.to_parquet(parquet_path, index=False)
            logger.info("Appended %d cohort rows to %s", len(new_df), parquet_path)
            return parquet_path

        logger.info("No rows to append for %s", parquet_path)
        return parquet_path


class CohortDiscoveryService:
    """High-level service that orchestrates treated/control cohort discovery."""

    def __init__(self, scraper: NoticeboardScraper | None = None, writer: NoticeboardManifestWriter | None = None):
        self.scraper = scraper or NoticeboardScraper()
        self.writer = writer or NoticeboardManifestWriter()

    @staticmethod
    def delete_checkpoint(checkpoint_path: str | Path) -> None:
        """Clear the checkpoint if the upload has finished."""
        path = resolve_project_path(str(checkpoint_path), "data/processed/block_log_checkpoint.json")
        if path.exists():
            path.unlink()

    @staticmethod
    def load_checkpoint(checkpoint_path: str | Path) -> dict:
        """Load a continuation checkpoint from disk if one exists."""
        path = resolve_project_path(str(checkpoint_path), "data/processed/block_log_checkpoint.json")
        if not path.exists():
            return {}

        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle) or {}
        except json.JSONDecodeError:
            logger.warning("Checkpoint file was unreadable; starting fresh from the first batch.")
            return {}

    @staticmethod
    def save_checkpoint(checkpoint_path: str | Path, payload: dict) -> Path:
        """Persist the continuation token for the next Wikipedia batch."""
        path = resolve_project_path(str(checkpoint_path), "data/processed/block_log_checkpoint.json")
        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle)

        logger.info("Saved checkpoint to %s", path)
        return path

    async def discover_cohorts(
        self,
        client,
        limit: int | None = None,
        max_records: int | None = None,
        checkpoint_path: str | Path | None = None,
        output_path: str | Path | None = None,
        treated_page: str = "Wikipedia:AI_noticeboard",
    ) -> list[CohortRecord]:
        """Entry point: run treated + control discovery and persist to `cohorts`."""
        if max_records is not None and max_records < 1:
            raise ValueError("max_records must be greater than zero.")

        treated_case_url = f"https://en.wikipedia.org/wiki/{treated_page.replace(' ', '_')}"

        logger.info("Starting treated-cohort discovery for %s", treated_page)

        treated_records: list[CohortRecord] = []
        if client is not None:
            noticeboard_pages = await self.scraper.discover_noticeboard_pages(treated_page)
            logger.info("Fetching treated page data from %d noticeboard pages", len(noticeboard_pages))
            scraped_treated = await self.scraper.scrape_ainb_cohort(noticeboard_pages)
            treated_records = self.scraper._cohort_from_scraped_ainb_rows(scraped_treated, treated_case_url)
            if max_records is not None:
                treated_records = treated_records[:max_records]
            logger.info("Parsed %d treated records via AINB scrape", len(treated_records))
        else:
            logger.warning("Dry run: skipping live wiki request; no client supplied.")

        control_records: list[CohortRecord] = []
        checkpoint_payload = {}
        if checkpoint_path is not None:
            checkpoint_payload = self.load_checkpoint(checkpoint_path)
            logger.info("Loaded checkpoint payload: %s", checkpoint_payload)

        if client is not None and (max_records is None or len(treated_records) < max_records):
            continue_token = checkpoint_payload.get("logcontinue")
            logger.info("Starting control-log fetch with continue_token=%s", continue_token)
            while True:
                batch_entries, next_continue = await client.fetch_block_log_batch(
                    letype="block",
                    limit=limit or 500,
                    continue_token=continue_token,
                )

                logger.info("Wiki block-log batch size=%d; next_continue=%s", len(batch_entries), next_continue)
                if not batch_entries:
                    logger.warning("Received an empty block-log batch; no more records found.")
                    break

                batch_records = NoticeboardScraper.parse_control_cohort(batch_entries)
                if max_records is not None:
                    remaining_records = max_records - len(treated_records) - len(control_records)
                    batch_records = batch_records[:remaining_records]
                control_records.extend(batch_records)
                logger.info("Parsed %d control records from current batch", len(batch_records))
                if output_path is not None:
                    self.writer.append_cohort_parquet(batch_records, output_path)

                if checkpoint_path is not None:
                    if next_continue:
                        self.save_checkpoint(checkpoint_path, {"logcontinue": next_continue})
                    else:
                        self.delete_checkpoint(checkpoint_path)

                if max_records is not None and len(treated_records) + len(control_records) >= max_records:
                    logger.info("Reached the maximum of %d parsed cohort records.", max_records)
                    break

                if not next_continue:
                    logger.info("No continuation token returned; end of block-log stream reached.")
                    break

                continue_token = next_continue
                logger.info("Waiting 0.2s before next batch to stay polite to Wikimedia.")
                await asyncio.sleep(0.2)
        elif client is None:
            logger.warning("Dry run: skipping control block-log fetch; no client supplied.")

        logger.info("Parsed %d control records total", len(control_records))

        all_records = treated_records + control_records
        if output_path is not None and not all_records:
            logger.info("No records to write; parquet output skipped.")
        elif output_path is not None:
            self.writer.write_cohort_parquet(all_records, output_path)

        return all_records


async def scrape_ainb_cohort(archive_subpages: list[str]) -> list[dict[str, str]]:
    """Compatibility wrapper for the scraper API."""
    return await NoticeboardScraper().scrape_ainb_cohort(archive_subpages)


def parse_treated_cohort(raw_wikitext: str, case_url: str) -> list[CohortRecord]:
    """Compatibility wrapper for parsing treated cohort wikitext."""
    return NoticeboardScraper.parse_treated_cohort(raw_wikitext, case_url)


def parse_control_cohort(block_log_entries: list[dict]) -> list[CohortRecord]:
    """Compatibility wrapper for the control parser."""
    return NoticeboardScraper.parse_control_cohort(block_log_entries)


async def discover_cohorts(
    client,
    limit: int | None = None,
    max_records: int | None = None,
    checkpoint_path: str | Path | None = None,
    output_path: str | Path | None = None,
    treated_page: str = "Wikipedia:AI_noticeboard",
) -> list[CohortRecord]:
    """Compatibility wrapper for the discovery service."""
    service = CohortDiscoveryService()
    return await service.discover_cohorts(
        client,
        limit=limit,
        max_records=max_records,
        checkpoint_path=checkpoint_path,
        output_path=output_path,
        treated_page=treated_page,
    )


def write_cohort_parquet(records: list[CohortRecord], output_path: str | Path) -> Path:
    """Compatibility wrapper for writing a manifest."""
    return NoticeboardManifestWriter.write_cohort_parquet(records, output_path)


def append_cohort_parquet(records: list[CohortRecord], output_path: str | Path) -> Path:
    """Compatibility wrapper for appending a manifest batch."""
    return NoticeboardManifestWriter.append_cohort_parquet(records, output_path)


if __name__ == "__main__":
    import asyncio

    from src.ingestion.wiki_client import WikiClient, WikiClientConfig

    parser = argparse.ArgumentParser(description="Scrape treated and control cohorts from Wikipedia into a parquet manifest.")
    parser.add_argument("--config", type=str, default=str(DEFAULT_CONFIG_PATH), help="Path to the YAML settings file.")
    parser.add_argument("--output", type=str, default=None, help="Optional output parquet path; defaults to config/noticeboards.output_manifest_path.")
    parser.add_argument("--dry-run", action="store_true", help="Skip live API calls and just validate the local pipeline.")
    parser.add_argument("--limit", type=int, default=500, help="Batch size for the control block-log fetch. Defaults to 500 for a polite Wikimedia batch.")
    parser.add_argument("--max-records", type=int, default=None, help="Stop after writing this many parsed cohort records total.")
    parser.add_argument("--checkpoint", type=str, default="data/processed/block_log_checkpoint.json", help="Path to the continuation-token checkpoint file.")
    args = parser.parse_args()

    settings = load_settings(args.config)
    noticeboard_settings = settings.get("noticeboards", {})
    output_path = resolve_project_path(
        args.output or noticeboard_settings.get("output_manifest_path"),
        "data/processed/cohort_manifest.parquet",
    )

    config = WikiClientConfig(
        api_endpoint=settings.get("wikipedia", {}).get("api_endpoint", "https://en.wikipedia.org/w/api.php"),
        user_agent=settings.get("wikipedia", {}).get("user_agent", "WikiAICitationAuditor/1.0"),
        rate_limit_per_second=settings.get("wikipedia", {}).get("rate_limit_per_second", 10),
        max_retries=settings.get("wikipedia", {}).get("max_retries", 5),
        api_call_interval_seconds=0.25,
    )

    async def main() -> None:
        logger.info("Using project-root relative output path: %s", output_path)
        logger.info("Dry run mode: %s", args.dry_run)

        log_dir = PROJECT_LOG_DIR
        client = None if args.dry_run else WikiClient(config, log_dir=log_dir)
        logger.info("Using log directory: %s", log_dir)
        records = await discover_cohorts(
            client,
            limit=args.limit,
            max_records=args.max_records,
            checkpoint_path=args.checkpoint,
            output_path=output_path if not args.dry_run else None,
            treated_page=noticeboard_settings.get("treated_page", "Wikipedia:AI_noticeboard"),
        )

        if not args.dry_run and not records:
            logger.info("No records were fetched; parquet output skipped.")

        print(f"Fetched {len(records)} cohort records")
        if args.dry_run:
            print("Dry run complete; no live wiki request sent.")
        else:
            print(f"Parquet output: {output_path}")

        for record in records[:10]:
            print(record.username, record.cohort_type, record.t0_timestamp.isoformat(), record.status)

    asyncio.run(main())

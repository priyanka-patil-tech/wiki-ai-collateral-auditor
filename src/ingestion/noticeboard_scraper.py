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

import re
from dataclasses import dataclass
from datetime import datetime

USER_HEADING_RE = re.compile(r"==\s*User:(.*?)\s*==")
STATUS_TEMPLATE_RE = re.compile(r"\{\{AINB status\|(open|resolved|blocked|presumptive)\}\}")
TIMESTAMP_RE = re.compile(r"\d{2}:\d{2},\s\d{1,2}\s[A-Za-z]+\s\d{4}\s\(UTC\)")


@dataclass
class CohortRecord:
    user_id: str
    username: str
    cohort_type: str  # "TREATED_AI" | "CONTROL_BEHAVIORAL"
    case_url: str
    t0_timestamp: datetime
    status: str


def parse_treated_cohort(wikitext: str, case_url: str) -> list[CohortRecord]:
    """Extract TREATED_AI cohort records from a WP:AINB (archive) page."""
    raise NotImplementedError


def parse_control_cohort(block_log_entries: list[dict]) -> list[CohortRecord]:
    """Build CONTROL_BEHAVIORAL cohort records from WP:ANI / block-log entries."""
    raise NotImplementedError


async def discover_cohorts(client) -> list[CohortRecord]:
    """Entry point: run treated + control discovery and persist to `cohorts`."""
    raise NotImplementedError

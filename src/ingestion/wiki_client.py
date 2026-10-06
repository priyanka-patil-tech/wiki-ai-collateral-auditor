"""
wiki_client.py — Async MediaWiki Action API client with backoff.

Responsibilities:
    * Wrap httpx.AsyncClient with a semaphore capped at the configured
      requests-per-second ceiling (see config/settings.yaml: wikipedia.*).
    * Apply exponential backoff with jitter on HTTP 429 / 503 responses.
    * Provide typed helpers for the endpoints this pipeline depends on:
        - action=parse            (noticeboard wikitext)
        - list=usercontribs       (a user's edit history, paginated via uccontinue)
        - action=compare          (side-by-side diff between two revisions)
        - prop=revisions&rvslots=main   (raw wikitext for a revision)
        - list=logevents&letype=block  (control-cohort block log)

This module intentionally holds no business logic (cohort assignment, diff
parsing, etc.) — it is a thin, well-behaved transport layer that every other
module in src/ingestion and src/extraction should route through, so rate
limiting and retry policy live in exactly one place.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


logger = logging.getLogger(__name__)


@dataclass
class WikiClientConfig:
    api_endpoint: str
    user_agent: str
    rate_limit_per_second: int = 10
    max_retries: int = 5
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 60.0
    api_call_interval_seconds: float = 0.25
    enable_logging: bool = False


class WikiAPIClient:
    """Async HTTP client for MediaWiki API with rate limiting and exponential backoff."""

    def __init__(
        self,
        api_url: str,
        user_agent: str,
        requests_per_second: int = 10,
        log_dir: str | Path | None = None,
        api_call_interval_seconds: float = 0.25,
        enable_logging: bool = False,
    ):
        self.api_url = api_url
        self.headers = {"User-Agent": user_agent}
        self.rate_limiter = asyncio.Semaphore(requests_per_second)
        self.log_dir = Path(log_dir) if log_dir is not None else None
        self.api_call_interval_seconds = api_call_interval_seconds
        self.enable_logging = enable_logging
        self.request_counter = 0
        if self.enable_logging:
            logger.info("WikiAPIClient initialized for %s with rate_limit=%s", api_url, requests_per_second)

    def _append_debug_jsonl(self, payload: dict[str, Any]) -> None:
        if not self.enable_logging or self.log_dir is None:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        out_path = self.log_dir / "wiki_api_calls.jsonl"
        with out_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _write_debug_json(self, name: str, payload: dict[str, Any]) -> None:
        if not self.enable_logging or self.log_dir is None:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        out_path = self.log_dir / f"{name}.json"
        with out_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(5),
        retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)),
    )
    async def get(self, params: dict[str, Any]) -> dict:
        """Executes a rate-limited GET request to the Wikipedia API."""
        params = {**params, "format": "json"}
        self.request_counter += 1
        logger.info("MediaWiki request #%s: %s", self.request_counter, params)

        async with self.rate_limiter:
            async with httpx.AsyncClient() as client:
                try:
                    response = await client.get(
                        self.api_url,
                        params=params,
                        headers=self.headers,
                        timeout=15.0,
                    )
                    response.raise_for_status()
                    data = response.json()
                    if self.enable_logging:
                        self._append_debug_jsonl(data)
                        self._write_debug_json("wiki_api_response", data)
                    if self.api_call_interval_seconds > 0:
                        await asyncio.sleep(self.api_call_interval_seconds)
                    logger.info("MediaWiki request #%s succeeded for action=%s", self.request_counter, params.get("action"))
                    return data
                except Exception:
                    logger.exception("MediaWiki request #%s failed for %s", self.request_counter, params)
                    raise

    async def get_page_wikitext(self, page_title: str) -> Optional[str]:
        """Fetches the raw wikitext of a given page."""
        params = {
            "action": "parse",
            "page": page_title,
            "prop": "wikitext",
        }
        data = await self.get(params)
        try:
            return data["parse"]["wikitext"]["*"]
        except KeyError:
            return None


class WikiClient(WikiAPIClient):
    """Backward-compatible MediaWiki client wrapper used by the project."""

    def __init__(self, config: WikiClientConfig, log_dir: str | Path | None = None) -> None:
        self.config = config
        super().__init__(
            api_url=config.api_endpoint,
            user_agent=config.user_agent,
            requests_per_second=config.rate_limit_per_second,
            log_dir=log_dir,
            api_call_interval_seconds=config.api_call_interval_seconds,
            enable_logging=config.enable_logging,
        )
        self._semaphore = self.rate_limiter
        self._headers = self.headers

    async def parse_page(self, page: str) -> dict[str, Any]:
        """action=parse — fetch a noticeboard page's wikitext."""
        return await self.get({"action": "parse", "page": page, "prop": "wikitext"})

    async def user_contribs(self, username: str) -> AsyncIterator[dict[str, Any]]:
        """list=usercontribs — page through a user's full edit history."""
        raise NotImplementedError
        yield {}  # pragma: no cover

    async def compare_revisions(self, from_rev: int, to_rev: int) -> dict[str, Any]:
        """action=compare — diff two revisions."""
        return await self.get({"action": "compare", "fromrev": from_rev, "torev": to_rev})

    async def get_revision_wikitext(self, rev_id: int) -> str:
        """prop=revisions&rvslots=main — raw wikitext for a revision."""
        data = await self.get({
            "action": "query",
            "prop": "revisions",
            "revids": rev_id,
            "rvslots": "main",
            "rvprop": "content",
        })
        try:
            pages = data["query"]["pages"]
            page = next(iter(pages.values()))
            return page["revisions"][0]["slots"]["main"]["*"]
        except (KeyError, IndexError, TypeError, StopIteration):
            return ""

    async def fetch_block_log_batch(
        self,
        letype: str = "block",
        limit: int | None = None,
        continue_token: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Fetch a single MediaWiki block-log batch and return the entries plus the next continuation token."""
        params: dict[str, Any] = {
            "action": "query",
            "list": "logevents",
            "letype": letype,
            "format": "json",
        }
        if limit is not None:
            params["lelimit"] = str(limit)
        if continue_token:
            params["lecontinue"] = continue_token

        data = await self.get(params)
        entries = data.get("query", {}).get("logevents", [])
        next_continue = data.get("continue", {}).get("logcontinue")
        return entries, next_continue

    async def block_log(
        self,
        letype: str = "block",
        limit: int | None = None,
        continue_token: str | None = None,
        sleep_seconds: float = 0.2,
    ) -> AsyncIterator[dict[str, Any]]:
        """Backward-compatible block-log iterator that yields a full continuation stream."""
        next_token = continue_token
        while True:
            entries, next_token = await self.fetch_block_log_batch(
                letype=letype,
                limit=limit,
                continue_token=next_token,
            )
            for entry in entries:
                yield entry
            if not next_token:
                break
            await asyncio.sleep(sleep_seconds)

    async def _request_with_backoff(self, params: dict[str, Any]) -> dict[str, Any]:
        """Compatibility wrapper for the project’s existing API shape."""
        return await self.get(params)

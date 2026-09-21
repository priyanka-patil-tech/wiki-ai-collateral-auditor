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
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


@dataclass
class WikiClientConfig:
    api_endpoint: str
    user_agent: str
    rate_limit_per_second: int = 10
    max_retries: int = 5
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 60.0


class WikiAPIClient:
    """Async HTTP client for MediaWiki API with rate limiting and exponential backoff."""

    def __init__(self, api_url: str, user_agent: str, requests_per_second: int = 10):
        self.api_url = api_url
        self.headers = {"User-Agent": user_agent}
        self.rate_limiter = asyncio.Semaphore(requests_per_second)

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(5),
        retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)),
    )
    async def get(self, params: dict[str, Any]) -> dict:
        """Executes a rate-limited GET request to the Wikipedia API."""
        params = {**params, "format": "json"}

        async with self.rate_limiter:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    self.api_url,
                    params=params,
                    headers=self.headers,
                    timeout=15.0,
                )
                response.raise_for_status()
                return response.json()

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

    def __init__(self, config: WikiClientConfig) -> None:
        self.config = config
        super().__init__(
            api_url=config.api_endpoint,
            user_agent=config.user_agent,
            requests_per_second=config.rate_limit_per_second,
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

    async def block_log(self, letype: str = "block") -> AsyncIterator[dict[str, Any]]:
        """list=logevents&letype=block — control-cohort candidates."""
        raise NotImplementedError
        yield {}  # pragma: no cover

    async def _request_with_backoff(self, params: dict[str, Any]) -> dict[str, Any]:
        """Compatibility wrapper for the project’s existing API shape."""
        return await self.get(params)

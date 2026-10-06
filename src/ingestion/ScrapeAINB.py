import asyncio
import re
from typing import Dict, List
import httpx

WIKI_API = "https://en.wikipedia.org/w/api.php"
HEADERS = {
    "User-Agent": (
        "WikiMedAuditResearch/1.0 (mailto:your.pnp1609@uw.edu)"
    )
}


async def scrape_ainb_cohort(
    archive_subpages: List[str],
) -> List[Dict[str, str]]:
    """Scrapes WP:AINB and archives for usernames and sanction timestamps (T0)."""
    cohort = []

    async with httpx.AsyncClient() as client:
        for page in archive_subpages:
            params = {
                "action": "parse",
                "page": page,
                "prop": "wikitext",
                "format": "json",
            }
            res = await client.get(WIKI_API, params=params, headers=HEADERS)
            wikitext = res.json().get("parse", {}).get("wikitext", {}).get(
                "*", ""
            )

            # Match user sections: == User:<Username> == or === <Username> ===
            sections = re.split(r"\n==+\s*(?:User:)?(.*?)\s*==+\n", wikitext)
            for i in range(1, len(sections), 2):
                username = sections[i].strip()
                body = sections[i + 1]

                # Identify cases resulting in presumptive rollbacks or blocks
                if any(
                    k in body.lower()
                    for k in [
                        "presumptive",
                        "mass rollback",
                        "blocked",
                        "llmprod",
                    ]
                ):
                    # Extract timestamp (e.g., 14:22, 10 March 2024 (UTC))
                    match = re.search(
                        r"(\d{2}:\d{2},\s\d{1,2}\s[A-Za-z]+\s\d{4}\s\(UTC\))",
                        body,
                    )
                    t0 = match.group(1) if match else "UNKNOWN"

                    cohort.append({
                        "username": username,
                        "shock_timestamp_utc": t0,
                        "source_board": "WP:AINB",
                    })

    return cohort
from datetime import datetime

import pytest

from src.ingestion.noticeboard_scraper import (
    CohortDiscoveryService,
    CohortRecord,
    NoticeboardScraper,
    parse_control_cohort,
    parse_treated_cohort,
    write_cohort_parquet,
)


def test_parse_treated_cohort_extracts_user_status_and_timestamp():
    sample = """
    == User:ExampleUser ==
    {{AINB status|resolved}}
    This user was found to have undisclosed AI-generated edits.
    12:35, 5 March 2024 (UTC)

    == User:AnotherUser ==
    {{AINB status|blocked}}
    04:10, 14 April 2024 (UTC)
    """

    records = parse_treated_cohort(sample, "https://example.test")

    assert len(records) == 2
    assert records[0].username == "ExampleUser"
    assert records[0].status == "resolved"
    assert records[0].cohort_type == "TREATED_AI"
    assert records[0].t0_timestamp == datetime(2024, 3, 5, 12, 35)


def test_parse_control_cohort_builds_behavioral_records():
    entries = [
        {
            "user": "ControlUser",
            "title": "User:ControlUser",
            "timestamp": "2024-03-06T12:30:00Z",
            "action": "block",
            "status": "blocked",
        }
    ]

    records = parse_control_cohort(entries)

    assert len(records) == 1
    assert records[0].username == "ControlUser"
    assert records[0].cohort_type == "CONTROL_BEHAVIORAL"
    assert records[0].status == "blocked"
    assert records[0].t0_timestamp.isoformat() == "2024-03-06T12:30:00"
    assert isinstance(records[0], CohortRecord)


def test_write_cohort_parquet_creates_file(tmp_path):
    records = [
        CohortRecord(
            user_id="ExampleUser",
            username="ExampleUser",
            cohort_type="TREATED_AI",
            case_url="https://example.test",
            t0_timestamp=datetime(2024, 3, 5, 12, 35),
            status="resolved",
        )
    ]

    output_path = tmp_path / "cohort_manifest.parquet"
    returned = write_cohort_parquet(records, output_path)

    assert output_path.exists()
    assert returned == output_path
    assert output_path.stat().st_size > 0


@pytest.mark.asyncio
async def test_discover_noticeboard_pages_follows_allpages_continuation(monkeypatch, tmp_path):
    responses = [
        {
            "query": {
                "allpages": [
                    {"title": "Wikipedia:AI_noticeboard/Archive 1"},
                    {"title": "Wikipedia:AI_noticeboard/Template notes"},
                ]
            },
            "continue": {"apcontinue": "AI_noticeboard/Archive 2", "continue": "||"},
        },
        {"query": {"allpages": [{"title": "Wikipedia:AI_noticeboard/Archive 2"}]}},
    ]
    request_params = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def get(self, url, params, headers, timeout):
            request_params.append(params.copy())
            return FakeResponse(responses.pop(0))

    monkeypatch.setattr("src.ingestion.noticeboard_scraper.httpx.AsyncClient", FakeAsyncClient)
    scraper = NoticeboardScraper(log_dir=tmp_path)

    pages = await scraper.discover_noticeboard_pages("Project:AI_noticeboard")

    assert pages == [
        "Wikipedia:AI_noticeboard",
        "Wikipedia:AI_noticeboard/Archive 1",
        "Wikipedia:AI_noticeboard/Archive 2",
    ]
    assert request_params[0]["apnamespace"] == 4
    assert request_params[0]["apprefix"] == "AI_noticeboard/"
    assert request_params[0]["aplimit"] == 500
    assert request_params[1]["apcontinue"] == "AI_noticeboard/Archive 2"
    assert request_params[1]["continue"] == "||"


@pytest.mark.asyncio
async def test_discover_cohorts_stops_at_maximum_parsed_records(tmp_path):
    class FakeScraper:
        _cohort_from_scraped_ainb_rows = staticmethod(NoticeboardScraper._cohort_from_scraped_ainb_rows)

        async def discover_noticeboard_pages(self, parent_page):
            return []

        async def scrape_ainb_cohort(self, archive_subpages):
            return []

    class FakeClient:
        def __init__(self):
            self.calls = 0

        async def fetch_block_log_batch(self, **kwargs):
            self.calls += 1
            entries = [
                {
                    "user": f"ControlUser{index}",
                    "timestamp": f"2024-03-{index + 1:02d}T12:30:00Z",
                    "action": "block",
                }
                for index in range((self.calls - 1) * 3, self.calls * 3)
            ]
            return entries, f"continue-{self.calls}"

    client = FakeClient()
    service = CohortDiscoveryService(scraper=FakeScraper())

    records = await service.discover_cohorts(client, limit=3, max_records=4)

    assert len(records) == 4
    assert [record.username for record in records] == [
        "ControlUser0",
        "ControlUser1",
        "ControlUser2",
        "ControlUser3",
    ]
    assert client.calls == 2

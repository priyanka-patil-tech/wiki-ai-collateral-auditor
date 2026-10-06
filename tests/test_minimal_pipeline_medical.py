import pytest

from src.extraction import minimal_pipeline
from src.extraction.med_stream_extractor import MedicalAuditExtractor


class FakeMedicalExtractor:
    def __init__(self):
        self.saved_rows = []

    async def filter_medical_pages(self, titles, client):
        return ["Medical article"]

    def parse_med_wikitext(self, raw_wikitext):
        assert "pmid=12345" in raw_wikitext
        return [{"claim_text": "Citation content", "pmid": "12345", "doi": "10.1000/test", "journal": "Test Journal"}]

    def save_rows(self, rows):
        self.saved_rows.extend(rows)
        return len(rows)


@pytest.mark.parametrize(
    "raw_wikitext",
    [
        "{{cite journal|pmid=12345|doi=10.1000/test|journal=Test Journal}}",
        "<ref>{{cite journal|pmid=12345|doi=10.1000/test|journal=Test Journal}}</ref>",
    ],
)
def test_medical_parser_extracts_standalone_and_ref_citations(raw_wikitext):
    extractor = object.__new__(MedicalAuditExtractor)

    rows = extractor.parse_med_wikitext(raw_wikitext)

    assert rows == [
        {
            "claim_text": "",
            "pmid": "12345",
            "doi": "10.1000/test",
            "journal": "Test Journal",
        }
    ]


@pytest.mark.asyncio
async def test_minimal_pipeline_persists_reverted_medical_citation(monkeypatch):
    async def fake_fetch_recent_user_revisions(username, limit, enable_logging, t0_timestamp=None):
        return [
            {
                "comment": "Reverted edit",
                "tags": [],
                "revid": 200,
                "parentid": 199,
                "pageid": 10,
                "title": "Medical article",
                "timestamp": "2026-09-01T10:00:00Z",
            }
        ]

    async def fake_fetch_compare_diff(from_rev, to_rev, enable_logging):
        return {"compare": {"*": "removed wikitext"}}

    monkeypatch.setattr(minimal_pipeline, "fetch_recent_user_revisions", fake_fetch_recent_user_revisions)
    monkeypatch.setattr(minimal_pipeline, "fetch_compare_diff", fake_fetch_compare_diff)
    monkeypatch.setattr(
        minimal_pipeline,
        "extract_reverted_claim_rows",
        lambda diff: [{"claim_text": "A medical claim.", "raw_citation": "<ref>{{cite journal|pmid=12345}}</ref>"}],
    )
    extractor = FakeMedicalExtractor()

    rows = await minimal_pipeline.run_minimal_pipeline(
        "TestUser",
        medical_extractor=extractor,
    )

    assert len(rows) == 1
    assert len(extractor.saved_rows) == 1
    assert extractor.saved_rows[0]["claim_text"] == "A medical claim."
    assert extractor.saved_rows[0]["rev_id"] == 200
    assert extractor.saved_rows[0]["pmid"] == "12345"
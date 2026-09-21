from datetime import datetime

from src.ingestion.noticeboard_scraper import (
    CohortRecord,
    parse_control_cohort,
    parse_treated_cohort,
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

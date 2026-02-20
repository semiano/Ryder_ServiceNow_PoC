from services.graph_client import extract_meeting_reference


def test_extract_meeting_reference_finds_join_url() -> None:
    ticket = {
        "close_notes": "Please review call notes https://teams.microsoft.com/l/meetup-join/19%3ameeting_xxx",
        "description": "",
        "short_description": "",
        "work_notes": [],
        "comments": [],
    }
    reference = extract_meeting_reference(ticket)
    assert reference["meetingJoinUrl"].startswith("https://teams.microsoft.com/l/meetup-join/")
    assert reference["foundInField"] == "close_notes"


def test_extract_meeting_reference_finds_thread_token() -> None:
    ticket = {
        "close_notes": "",
        "description": "Conference context 19:abcDEF123_x.y-z@thread.v2",
        "short_description": "",
        "work_notes": [],
        "comments": [],
    }
    reference = extract_meeting_reference(ticket)
    assert reference["meetingIdCandidate"] == "19:abcDEF123_x.y-z@thread.v2"
    assert reference["foundInField"] == "description"


def test_extract_meeting_reference_no_match() -> None:
    ticket = {
        "close_notes": "No meeting references here.",
        "description": "General details only.",
        "short_description": "Printer issue",
        "work_notes": [],
        "comments": [],
    }
    reference = extract_meeting_reference(ticket)
    assert reference["meetingJoinUrl"] is None
    assert reference["meetingIdCandidate"] is None

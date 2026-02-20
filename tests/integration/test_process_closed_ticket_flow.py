from function_app import ProcessingDependencies, process_payload


def _valid_rca() -> dict:
    return {
        "schemaVersion": "1.0",
        "ticket": {
            "number": "INC0012345",
            "sys_id": "123e4567-e89b-42d3-a456-426614174000",
            "priority": "1",
            "closedAt": "2026-02-19T15:00:00Z",
        },
        "summary": {
            "title": "Title",
            "executiveSummary": "Exec",
            "customerImpact": "Impact",
            "severity": "High",
        },
        "timeline": [
            {"timestamp": "2026-02-19T14:00:00Z", "event": "Event", "source": "ticket"}
        ],
        "rootCause": {
            "statement": "Root",
            "category": "configuration",
            "confidence": 0.8,
        },
        "contributingFactors": ["Factor"],
        "detection": {"howDetected": "Monitoring", "whyNotDetectedSooner": "Noise"},
        "resolution": {"fixApplied": "Fix", "verification": "Verified"},
        "correctiveActions": [
            {"action": "Action", "owner": None, "dueDate": None, "priority": "P1"}
        ],
        "evidence": {
            "serviceNowFieldsUsed": ["description"],
            "transcriptUsed": False,
            "notes": "N/A",
        },
        "risks": ["Risk"],
        "similarIncidents": {
            "referenced": False,
            "summary": "No similar incidents provided.",
        },
        "appendix": {
            "rawTranscriptIncluded": False,
            "truncation": {"applied": False, "maxChars": 120000},
        },
    }


class StubServiceNow:
    def fetch_incident(self, ticket_id: str, ticket_key_type: str):
        assert ticket_id == "INC0012345"
        assert ticket_key_type == "number"
        return {
            "sys_id": "123e4567-e89b-42d3-a456-426614174000",
            "number": "INC0012345",
            "short_description": "Service impact",
            "description": "Root analysis needed",
            "close_notes": "No transcript link",
            "state": "Closed",
            "priority": "1",
            "severity": "1",
            "assignment_group": "Platform",
            "opened_at": "2026-02-19T12:00:00Z",
            "closed_at": "2026-02-19T15:00:00Z",
            "caller_id": "Operations",
            "cmdb_ci": "App-01",
            "work_notes": ["Investigated error spikes"],
            "comments": ["Customer notified"],
        }


class StubGraph:
    def extract_meeting_reference(self, ticket):
        return {
            "meetingJoinUrl": None,
            "meetingIdCandidate": None,
            "meetingSubjectCandidate": None,
            "foundInField": None,
        }

    def fetch_transcript_best_effort(self, ticket, meeting_reference, correlation_id):
        return {
            "attempted": True,
            "found": False,
            "source": "graph",
            "details": {
                "matchStrategy": "no_reference",
                "graphTranscriptId": None,
                "graphMeetingId": None,
            },
            "transcriptText": "",
            "transcriptChars": 0,
        }


class StubFoundry:
    def __init__(self, payload):
        self.payload = payload
        self.call_count = 0

    def generate_rca(self, correlation_id, service_now_ticket, ticket_body_text, transcript_text, transcript_metadata):
        self.call_count += 1
        assert correlation_id
        assert "Ticket Number" in ticket_body_text
        return self.payload, "unknown"


class StubCosmos:
    def __init__(self):
        self.entities = []

    def upsert_entity(self, entity):
        self.entities.append(entity)


def test_closed_happy_path_with_mocks() -> None:
    foundry = StubFoundry(_valid_rca())
    cosmos = StubCosmos()
    deps = ProcessingDependencies(
        service_now=StubServiceNow(),
        graph=StubGraph(),
        foundry=foundry,
        cosmos=cosmos,
    )

    status_code, body = process_payload(
        payload={"ticketId": "INC0012345", "status": "closed"},
        dependencies=deps,
        correlation_id="11111111-1111-1111-1111-111111111111",
    )

    assert status_code == 200
    assert body["processed"] is True
    assert foundry.call_count == 1
    assert len(cosmos.entities) == 1


def test_invalid_foundry_schema_returns_500_and_no_cosmos_write() -> None:
    foundry = StubFoundry({"schemaVersion": "1.0"})
    cosmos = StubCosmos()
    deps = ProcessingDependencies(
        service_now=StubServiceNow(),
        graph=StubGraph(),
        foundry=foundry,
        cosmos=cosmos,
    )

    status_code, body = process_payload(
        payload={"ticketId": "INC0012345", "status": "closed"},
        dependencies=deps,
        correlation_id="22222222-2222-2222-2222-222222222222",
    )

    assert status_code == 500
    assert body["error"]["code"] == "FOUNDRY_SCHEMA_INVALID"
    assert len(cosmos.entities) == 0


class FailIfCalledGraph:
    def extract_meeting_reference(self, ticket):
        return {
            "meetingJoinUrl": None,
            "meetingIdCandidate": None,
            "meetingSubjectCandidate": None,
            "foundInField": None,
        }

    def fetch_transcript_best_effort(self, ticket, meeting_reference, correlation_id):
        raise AssertionError("Graph fetch should be bypassed when SIMULATE_CALL_TRANSCRIPT_LOOKUP=true")


def test_simulated_transcript_bypass_uses_static_file(monkeypatch) -> None:
    monkeypatch.setenv("SIMULATE_CALL_TRANSCRIPT_LOOKUP", "true")
    foundry = StubFoundry(_valid_rca())
    cosmos = StubCosmos()
    deps = ProcessingDependencies(
        service_now=StubServiceNow(),
        graph=FailIfCalledGraph(),
        foundry=foundry,
        cosmos=cosmos,
    )

    status_code, body = process_payload(
        payload={"ticketId": "INC0012345", "status": "closed"},
        dependencies=deps,
        correlation_id="33333333-3333-3333-3333-333333333333",
    )

    assert status_code == 200
    assert body["processed"] is True
    assert body["transcript"]["found"] is True
    assert body["transcript"]["details"]["matchStrategy"] == "simulated_transcript"
    assert cosmos.entities[0]["TranscriptFound"] is True


from function_app import ProcessingDependencies, process_payload
from services.servicenow_client import ServiceNowClientError


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

    def fetch_similar_records(self, ticket: dict, record_types=None, max_results: int | None = None):
        return [
            {
                "record_type": "incident",
                "sys_id": "123e4567-e89b-42d3-a456-426614174111",
                "number": "INC0012000",
                "short_description": "Prior service impact",
                "description": "Previous similar failure",
                "close_notes": "Mitigated by restart",
                "state": "Closed",
                "priority": "1",
                "severity": "1",
                "assignment_group": "Platform",
                "opened_at": "2026-01-10T11:00:00Z",
                "closed_at": "2026-01-10T12:00:00Z",
                "caller_id": "Operations",
                "cmdb_ci": "App-01",
            }
        ]

    def create_child_incident(self, parent_ticket, short_description, description, correlation_id):
        assert parent_ticket["number"] == "INC0012345"
        assert short_description.startswith("RCA Report:")
        assert "Full RCA JSON" in description
        assert correlation_id
        return {
            "sys_id": "123e4567-e89b-42d3-a456-426614174222",
            "number": "INC0099999",
            "short_description": short_description,
            "state": "New",
            "assignment_group": "Platform",
            "opened_at": "2026-02-19T15:01:00Z",
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

    def generate_rca(
        self,
        correlation_id,
        service_now_ticket,
        ticket_body_text,
        transcript_text,
        transcript_metadata,
        similar_tickets,
    ):
        self.call_count += 1
        assert correlation_id
        assert "Ticket Number" in ticket_body_text
        assert isinstance(similar_tickets, list)
        assert len(similar_tickets) == 1
        return self.payload, "unknown"


class StubCosmos:
    def __init__(self):
        self.entities = []

    def upsert_entity(self, entity):
        self.entities.append(entity)


class ChildCreateFailServiceNow(StubServiceNow):
    def create_child_incident(self, parent_ticket, short_description, description, correlation_id):
        raise ServiceNowClientError("ServiceNow child incident create failed with status 401")


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
    assert body["similarIncidents"]["recordTypes"] == ["incident"]
    assert body["similarIncidents"]["count"] == 1
    assert body["rcaChildTicket"]["created"] is True
    assert body["rcaChildTicket"]["number"] == "INC0099999"
    assert foundry.call_count == 1
    assert len(cosmos.entities) == 2
    assert cosmos.entities[-1]["RCAChildTicketNumber"] == "INC0099999"
    assert "INC0012000" in cosmos.entities[-1]["SimilarTicketsJson"]


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
    assert any(entity["TranscriptFound"] is True for entity in cosmos.entities)


def test_child_ticket_create_failure_is_best_effort_and_process_still_succeeds() -> None:
    foundry = StubFoundry(_valid_rca())
    cosmos = StubCosmos()
    deps = ProcessingDependencies(
        service_now=ChildCreateFailServiceNow(),
        graph=StubGraph(),
        foundry=foundry,
        cosmos=cosmos,
    )

    status_code, body = process_payload(
        payload={"ticketId": "INC0012345", "status": "closed"},
        dependencies=deps,
        correlation_id="44444444-4444-4444-4444-444444444444",
    )

    assert status_code == 200
    assert body["processed"] is True
    assert body["rcaChildTicket"]["created"] is False
    assert body["rcaChildTicket"]["number"] == ""
    assert body["rcaChildTicket"]["error"] == "ServiceNow child incident create failed with status 401"
    assert len(cosmos.entities) == 2
    assert cosmos.entities[-1]["RCAChildTicketCreated"] is False
    assert cosmos.entities[-1]["RCAChildTicketError"] == "ServiceNow child incident create failed with status 401"


import json
import logging

from function_app import ProcessingDependencies, process_payload


class FakeServiceNow:
    def __init__(self) -> None:
        self.called = False

    def fetch_incident(self, ticket_id: str, ticket_key_type: str):
        self.called = True
        raise AssertionError("ServiceNow should not be called for non-closed status")


class FakeGraph:
    def __init__(self) -> None:
        self.extract_called = False
        self.fetch_called = False

    def extract_meeting_reference(self, ticket):
        self.extract_called = True
        raise AssertionError("Graph extract should not be called for non-closed status")

    def fetch_transcript_best_effort(self, ticket, meeting_reference, correlation_id):
        self.fetch_called = True
        raise AssertionError("Graph fetch should not be called for non-closed status")


class FakeFoundry:
    def __init__(self) -> None:
        self.called = False

    def generate_rca(
        self,
        correlation_id,
        service_now_ticket,
        ticket_body_text,
        transcript_text,
        transcript_metadata,
        similar_tickets,
    ):
        self.called = True
        raise AssertionError("Foundry should not be called for non-closed status")


class FakeCosmos:
    def __init__(self) -> None:
        self.called = False

    def upsert_entity(self, entity):
        self.called = True
        raise AssertionError("Cosmos should not be called for non-closed status")


def test_status_not_closed_returns_processed_false_and_no_downstream_calls() -> None:
    deps = ProcessingDependencies(
        service_now=FakeServiceNow(),
        graph=FakeGraph(),
        foundry=FakeFoundry(),
        cosmos=FakeCosmos(),
    )

    status_code, body = process_payload(
        payload={"ticketId": "INC0012345", "status": " In Progress "},
        dependencies=deps,
        correlation_id="00000000-0000-0000-0000-000000000001",
    )

    assert status_code == 200
    assert body["processed"] is False
    assert body["reason"] == "status_not_closed"
    assert body["normalizedStatus"] == "in progress"

    assert deps.service_now.called is False
    assert deps.graph.extract_called is False
    assert deps.graph.fetch_called is False
    assert deps.foundry.called is False
    assert deps.cosmos.called is False

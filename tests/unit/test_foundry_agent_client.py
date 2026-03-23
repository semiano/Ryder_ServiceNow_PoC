import os

import pytest

from services.foundry_client import FoundryClient, FoundryClientError


class _StubCredential:
    def get_token(self, scope: str):
        assert scope == "https://ai.azure.com/.default"
        return type("Token", (), {"token": "test-token"})()


class _StubResponse:
    def __init__(self, status_code: int, payload: dict, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> dict:
        return self._payload


def _valid_rca_payload() -> dict:
    return {
        "model": "gpt-4.1",
        "output_text": "{\"schemaVersion\":\"1.0\",\"rootCause\":{\"statement\":\"x\"},\"summary\":{\"title\":\"ok\"}}",
    }


def test_generate_rca_posts_to_primary_agent_endpoint(monkeypatch) -> None:
    monkeypatch.delenv("FOUNDRY_TOKEN_SCOPE", raising=False)
    monkeypatch.setattr("services.foundry_client.DefaultAzureCredential", lambda: _StubCredential())

    calls = []

    def _post(url, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _StubResponse(200, _valid_rca_payload())

    monkeypatch.setattr("services.foundry_client.requests.post", _post)

    client = FoundryClient(endpoint_url="https://example.services.ai.azure.com/api/projects/p/applications/a/protocols/openai/responses?api-version=2025-11-15-preview")

    rca, model = client.generate_rca(
        correlation_id="corr-1",
        service_now_ticket={"number": "INC001"},
        ticket_body_text="Ticket Number: INC001",
        transcript_text="",
        transcript_metadata={"found": False},
        similar_tickets=[],
    )

    assert model == "gpt-4.1"
    assert rca["schemaVersion"] == "1.0"
    assert len(calls) == 1
    assert calls[0]["url"].startswith("https://example.services.ai.azure.com/")
    assert calls[0]["headers"]["Authorization"] == "Bearer test-token"
    assert calls[0]["json"]["metadata"]["correlationId"] == "corr-1"


def test_generate_rca_falls_back_to_secondary_agent_endpoint(monkeypatch) -> None:
    monkeypatch.setattr("services.foundry_client.DefaultAzureCredential", lambda: _StubCredential())

    calls = []

    def _post(url, headers, json, timeout):
        calls.append(url)
        if "agent-one" in url:
            return _StubResponse(500, {}, text="primary unavailable")
        return _StubResponse(200, _valid_rca_payload())

    monkeypatch.setattr("services.foundry_client.requests.post", _post)

    client = FoundryClient(
        endpoint_url="https://agent-one.services.ai.azure.com/api/projects/p/applications/a/protocols/openai/responses?api-version=2025-11-15-preview",
        fallback_endpoint_urls=[
            "https://agent-two.services.ai.azure.com/api/projects/p/applications/a/protocols/openai/responses?api-version=2025-11-15-preview"
        ],
    )

    rca, _ = client.generate_rca(
        correlation_id="corr-2",
        service_now_ticket={"number": "INC001"},
        ticket_body_text="Ticket Number: INC001",
        transcript_text="",
        transcript_metadata={"found": False},
        similar_tickets=[],
    )

    assert rca["rootCause"]["statement"] == "x"
    assert calls == [
        "https://agent-one.services.ai.azure.com/api/projects/p/applications/a/protocols/openai/responses?api-version=2025-11-15-preview",
        "https://agent-two.services.ai.azure.com/api/projects/p/applications/a/protocols/openai/responses?api-version=2025-11-15-preview",
    ]


def test_generate_rca_raises_after_all_agent_endpoints_fail(monkeypatch) -> None:
    monkeypatch.setattr("services.foundry_client.DefaultAzureCredential", lambda: _StubCredential())

    def _post(url, headers, json, timeout):
        return _StubResponse(503, {}, text="service unavailable")

    monkeypatch.setattr("services.foundry_client.requests.post", _post)

    client = FoundryClient(
        endpoint_url="https://agent-one.services.ai.azure.com/api/projects/p/applications/a/protocols/openai/responses?api-version=2025-11-15-preview",
        fallback_endpoint_urls=[
            "https://agent-two.services.ai.azure.com/api/projects/p/applications/a/protocols/openai/responses?api-version=2025-11-15-preview"
        ],
    )

    with pytest.raises(FoundryClientError) as exc:
        client.generate_rca(
            correlation_id="corr-3",
            service_now_ticket={"number": "INC001"},
            ticket_body_text="Ticket Number: INC001",
            transcript_text="",
            transcript_metadata={"found": False},
            similar_tickets=[],
        )

    assert "All Foundry agent endpoints failed" in str(exc.value)


def test_check_connectivity_returns_success_for_first_reachable_agent(monkeypatch) -> None:
    monkeypatch.setattr("services.foundry_client.DefaultAzureCredential", lambda: _StubCredential())

    def _post(url, headers, json, timeout):
        if "agent-a" in url:
            return _StubResponse(500, {}, text="boom")
        return _StubResponse(200, {"id": "resp_123", "model": "gpt-4.1"})

    monkeypatch.setattr("services.foundry_client.requests.post", _post)

    client = FoundryClient(
        endpoint_url="https://agent-a.services.ai.azure.com/api/projects/p/applications/a/protocols/openai/responses?api-version=2025-11-15-preview",
        fallback_endpoint_urls=[
            "https://agent-b.services.ai.azure.com/api/projects/p/applications/a/protocols/openai/responses?api-version=2025-11-15-preview"
        ],
    )

    result = client.check_connectivity(correlation_id="health-check")

    assert result["ok"] is True
    assert result["statusCode"] == 200
    assert "agent-b.services.ai.azure.com" in result["endpointUrl"]
    assert result["responseId"] == "resp_123"

from services.servicenow_client import ServiceNowClient


def test_build_incident_request_for_sys_id() -> None:
    client = ServiceNowClient(
        instance_url="https://example.service-now.com",
        api_token="token",
    )
    url, params = client.build_incident_request(
        ticket_id="123e4567-e89b-42d3-a456-426614174000",
        ticket_key_type="sys_id",
    )

    assert url == "https://example.service-now.com/api/now/table/incident/123e4567-e89b-42d3-a456-426614174000"
    assert "sysparm_fields" in params
    assert params["sysparm_display_value"] == "true"


def test_build_incident_request_for_number() -> None:
    client = ServiceNowClient(
        instance_url="https://example.service-now.com",
        api_token="token",
    )
    url, params = client.build_incident_request(
        ticket_id="INC0012345",
        ticket_key_type="number",
    )

    assert url == "https://example.service-now.com/api/now/table/incident"
    assert params["sysparm_query"] == "number=INC0012345"
    assert params["sysparm_limit"] == "1"
    assert params["sysparm_display_value"] == "true"


def test_build_headers_for_api_key_mode() -> None:
    client = ServiceNowClient(
        instance_url="https://example.service-now.com",
        api_token="token-value",
        auth_scheme="x-sn-apikey",
    )
    headers = client._build_headers()
    assert headers["x-sn-apikey"] == "token-value"
    assert "Authorization" not in headers


def test_build_headers_for_bearer_mode() -> None:
    client = ServiceNowClient(
        instance_url="https://example.service-now.com",
        api_token="token-value",
        auth_scheme="Bearer",
    )
    headers = client._build_headers()
    assert headers["Authorization"] == "Bearer token-value"


def test_build_headers_with_basic_auth_uses_child_username_password() -> None:
    client = ServiceNowClient(
        instance_url="https://example.service-now.com",
        api_token="token-value",
        auth_scheme="Bearer",
        child_auth_scheme="Basic",
        child_username="api.user",
        child_password="secret",
    )
    headers = client._build_headers_with_auth("Basic", None)
    assert headers["Authorization"].startswith("Basic ")


def test_build_similar_incidents_request_uses_incident_table_and_related_fields() -> None:
    client = ServiceNowClient(
        instance_url="https://example.service-now.com",
        api_token="token",
    )
    url, params = client.build_similar_incidents_request(
        ticket={
            "cmdb_ci": "App-01",
            "assignment_group": "Platform Ops",
            "caller_id": "Operations",
        },
        max_results=5,
    )

    assert url == "https://example.service-now.com/api/now/table/incident"
    assert "cmdb_ci=App-01" in params["sysparm_query"]
    assert "assignment_group=Platform%20Ops" in params["sysparm_query"]
    assert "caller_id=Operations" in params["sysparm_query"]
    assert params["sysparm_limit"] == "6"
    assert params["sysparm_display_value"] == "true"


def test_build_similar_incidents_request_without_similarity_keys_returns_empty_query() -> None:
    client = ServiceNowClient(
        instance_url="https://example.service-now.com",
        api_token="token",
    )
    _, params = client.build_similar_incidents_request(
        ticket={
            "cmdb_ci": None,
            "assignment_group": None,
            "caller_id": None,
        },
        max_results=5,
    )

    assert params["sysparm_query"] == "sys_idISEMPTY"
    assert params["sysparm_limit"] == "0"


def test_build_similar_records_request_uses_configured_table_name() -> None:
    client = ServiceNowClient(
        instance_url="https://example.service-now.com",
        api_token="token",
    )
    url, _ = client.build_similar_records_request(
        record_type="problem",
        ticket={
            "cmdb_ci": "App-01",
        },
        max_results=3,
    )

    assert url == "https://example.service-now.com/api/now/table/problem"

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

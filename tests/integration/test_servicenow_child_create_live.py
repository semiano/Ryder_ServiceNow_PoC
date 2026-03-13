import json
import os
import time
import uuid
from pathlib import Path

import pytest

from services.servicenow_client import ServiceNowClient


def _load_local_settings_values() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    local_settings = root / "local.settings.json"
    if not local_settings.exists():
        return {}
    payload = json.loads(local_settings.read_text(encoding="utf-8"))
    values = payload.get("Values", {})
    if not isinstance(values, dict):
        return {}
    return {str(key): str(value) for key, value in values.items()}


def _setting(name: str, local_values: dict[str, str], default: str = "") -> str:
    value = os.getenv(name)
    if value is not None and str(value).strip():
        return str(value).strip()
    local_value = local_values.get(name)
    if local_value is not None and str(local_value).strip():
        return str(local_value).strip()
    return default


def test_live_create_child_record() -> None:
    if os.getenv("RUN_LIVE_SERVICENOW_TESTS", "false").strip().lower() != "true":
        pytest.skip("Set RUN_LIVE_SERVICENOW_TESTS=true to run live ServiceNow create test")

    local_values = _load_local_settings_values()

    instance_url = _setting("SERVICENOW_INSTANCE_URL", local_values)
    auth_scheme = _setting("SERVICENOW_AUTH_SCHEME", local_values, "x-sn-apikey")
    api_token = _setting("SERVICENOW_API_TOKEN", local_values)
    child_auth_scheme = _setting("SERVICENOW_CHILD_AUTH_SCHEME", local_values, auth_scheme)
    child_api_token = _setting("SERVICENOW_CHILD_API_TOKEN", local_values, api_token)
    child_username = _setting("SERVICENOW_CHILD_USERNAME", local_values)
    child_password = _setting("SERVICENOW_CHILD_PASSWORD", local_values)
    parent_ticket_number = _setting("SERVICENOW_TEST_PARENT_TICKET", local_values, "INC0010002")

    missing = [
        name
        for name, value in {
            "SERVICENOW_INSTANCE_URL": instance_url,
            "SERVICENOW_API_TOKEN": api_token,
            "SERVICENOW_TEST_PARENT_TICKET": parent_ticket_number,
        }.items()
        if not value
    ]
    if missing:
        pytest.skip(f"Missing required settings for live test: {', '.join(missing)}")

    client = ServiceNowClient(
        instance_url=instance_url,
        api_token=api_token,
        auth_scheme=auth_scheme,
        child_auth_scheme=child_auth_scheme,
        child_api_token=child_api_token,
        child_username=child_username,
        child_password=child_password,
        child_record_table=_setting("SERVICENOW_CHILD_RECORD_TABLE", local_values, "incident"),
    )

    parent = client.fetch_incident(parent_ticket_number, "number")
    assert parent is not None, f"Parent ticket not found: {parent_ticket_number}"

    correlation_id = str(uuid.uuid4())
    short_description = f"RCA Report Test {int(time.time())}"
    description = (
        "Automated live integration test for child record creation. "
        f"Correlation ID: {correlation_id}. Parent: {parent_ticket_number}."
    )

    created = client.create_child_incident(
        parent_ticket=parent,
        short_description=short_description,
        description=description,
        correlation_id=correlation_id,
    )

    assert created["sys_id"]
    assert created["number"]
    assert created["short_description"]
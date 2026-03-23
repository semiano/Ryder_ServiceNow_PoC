import json
import os
from pathlib import Path

import pytest

from services.foundry_client import FoundryClient


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


def _split_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item and item.strip()]


def test_live_foundry_agent_connectivity() -> None:
    if os.getenv("RUN_LIVE_FOUNDRY_TESTS", "false").strip().lower() != "true":
        pytest.skip("Set RUN_LIVE_FOUNDRY_TESTS=true to run live Foundry connectivity test")

    local_values = _load_local_settings_values()

    primary_endpoint = _setting("FOUNDRY_AGENT_ENDPOINT_URL", local_values)
    fallback_endpoints_csv = _setting("FOUNDRY_AGENT_ENDPOINT_URLS", local_values)
    fallback_endpoints = _split_csv(fallback_endpoints_csv)

    if not primary_endpoint:
        pytest.skip("Missing required setting FOUNDRY_AGENT_ENDPOINT_URL")

    client = FoundryClient(
        endpoint_url=primary_endpoint,
        fallback_endpoint_urls=fallback_endpoints,
    )

    result = client.check_connectivity(correlation_id="live-foundry-comms-test")
    assert result["ok"] is True
    assert result["statusCode"] == 200
    assert result["endpointUrl"].startswith("https://")

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from services.servicenow_client import ServiceNowClient, ServiceNowClientError


def _load_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        value = raw_value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _setting(name: str, dotenv_values: dict[str, str], default: str = "") -> str:
    env_value = os.getenv(name)
    if env_value is not None and str(env_value).strip():
        return str(env_value).strip()
    dotenv_value = dotenv_values.get(name)
    if dotenv_value is not None and str(dotenv_value).strip():
        return str(dotenv_value).strip()
    return default


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--auth-mode", choices=["basic", "apikey"], default="basic")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    dotenv_values = _load_dotenv(root / ".env")

    instance_url = _setting("SERVICENOW_INSTANCE_URL", dotenv_values)
    base_auth_scheme = _setting("SERVICENOW_AUTH_SCHEME", dotenv_values, "x-sn-apikey")
    base_api_token = _setting("SERVICENOW_API_TOKEN", dotenv_values)

    parent_ticket_number = _setting("SERVICENOW_TEST_PARENT_TICKET", dotenv_values, "INC0010002")
    child_table = _setting("SERVICENOW_CHILD_RECORD_TABLE", dotenv_values, "incident")

    child_username = _setting("SERVICENOW_CHILD_USERNAME", dotenv_values)
    child_password = _setting("SERVICENOW_CHILD_PASSWORD", dotenv_values)
    child_api_token = _setting("SERVICENOW_CHILD_API_TOKEN", dotenv_values, base_api_token)

    required = {
        "SERVICENOW_INSTANCE_URL": instance_url,
        "SERVICENOW_API_TOKEN": base_api_token,
    }
    if args.auth_mode == "basic":
        required["SERVICENOW_CHILD_USERNAME"] = child_username
        required["SERVICENOW_CHILD_PASSWORD"] = child_password
    else:
        required["SERVICENOW_CHILD_API_TOKEN_OR_SERVICENOW_API_TOKEN"] = child_api_token

    missing = [key for key, value in required.items() if not value]
    if missing:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "Missing required settings",
                    "missing": missing,
                    "hint": "Populate these keys in .env or process environment.",
                },
                indent=2,
            )
        )
        return 2

    child_auth_scheme = "Basic" if args.auth_mode == "basic" else "x-sn-apikey"
    client = ServiceNowClient(
        instance_url=instance_url,
        api_token=base_api_token,
        auth_scheme=base_auth_scheme,
        child_record_table=child_table,
        child_auth_scheme=child_auth_scheme,
        child_api_token=child_api_token,
        child_username=child_username,
        child_password=child_password,
    )

    report: dict[str, object] = {
        "timestampUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "instanceHost": instance_url.replace("https://", "").split("/")[0],
        "authAttempt": {
            "enabled": True,
            "authMode": args.auth_mode,
            "hasChildUsername": bool(child_username),
            "hasChildPassword": bool(child_password),
            "hasChildApiToken": bool(child_api_token),
            "childRecordTable": child_table,
            "parentTicketNumber": parent_ticket_number,
        },
    }

    try:
        parent = client.fetch_incident(parent_ticket_number, "number")
        report["fetchParent"] = {
            "found": bool(parent),
            "parentSysIdPresent": bool((parent or {}).get("sys_id")),
        }
        if not parent:
            report["ok"] = False
            report["error"] = f"Parent ticket not found: {parent_ticket_number}"
            _write_report(root, report, args.auth_mode)
            print(json.dumps(report, indent=2))
            return 3

        correlation_id = str(uuid.uuid4())
        created = client.create_child_incident(
            parent_ticket=parent,
            short_description=f"RCA AUTH DIAG {args.auth_mode.upper()} {int(time.time())}",
            description=f"Automated diagnostic create probe. Correlation ID: {correlation_id}",
            correlation_id=correlation_id,
        )
        report["createChild"] = {
            "created": True,
            "number": created.get("number") or "",
            "sysIdPresent": bool(created.get("sys_id")),
        }
        report["ok"] = True
    except ServiceNowClientError as exc:
        report["ok"] = False
        report["createChild"] = {
            "created": False,
            "error": str(exc),
        }
    except Exception as exc:
        report["ok"] = False
        report["error"] = f"Unhandled exception: {exc}"

    report_path = _write_report(root, report, args.auth_mode)
    report["reportFile"] = str(report_path)
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


def _write_report(root: Path, report: dict[str, object], auth_mode: str) -> Path:
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    output = artifacts / f"servicenow_{auth_mode}_auth_create_report_{time.strftime('%Y%m%d_%H%M%S', time.localtime())}.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return output


if __name__ == "__main__":
    raise SystemExit(main())
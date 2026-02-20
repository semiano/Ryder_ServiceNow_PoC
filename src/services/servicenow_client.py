from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests


INCIDENT_FIELDS = (
    "sys_id,number,short_description,description,close_notes,state,priority,severity,"
    "assignment_group,opened_at,closed_at,caller_id,cmdb_ci"
)


@dataclass
class ServiceNowClientError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


class ServiceNowClient:
    def __init__(
        self,
        instance_url: str,
        api_token: str,
        auth_scheme: str = "Bearer",
        timeout_seconds: int = 30,
    ) -> None:
        self.instance_url = instance_url.rstrip("/")
        self.api_token = api_token
        self.auth_scheme = auth_scheme
        self.timeout_seconds = timeout_seconds

    def _build_headers(self) -> dict[str, str]:
        scheme = (self.auth_scheme or "Bearer").strip().lower()
        headers = {
            "Accept": "application/json",
        }

        if scheme in {"x-sn-apikey", "sn_apikey", "apikey", "api_key"}:
            headers["x-sn-apikey"] = self.api_token
            return headers

        headers["Authorization"] = f"{self.auth_scheme} {self.api_token}"
        return headers

    def build_incident_request(
        self,
        ticket_id: str,
        ticket_key_type: str,
    ) -> tuple[str, dict[str, str]]:
        if ticket_key_type == "sys_id":
            url = f"{self.instance_url}/api/now/table/incident/{ticket_id}"
            params = {
                "sysparm_fields": INCIDENT_FIELDS,
                "sysparm_display_value": "true",
            }
            return url, params

        url = f"{self.instance_url}/api/now/table/incident"
        params = {
            "sysparm_query": f"number={quote(ticket_id, safe='')}",
            "sysparm_limit": "1",
            "sysparm_fields": INCIDENT_FIELDS,
            "sysparm_display_value": "true",
        }
        return url, params

    def fetch_incident(self, ticket_id: str, ticket_key_type: str) -> dict[str, Any] | None:
        url, params = self.build_incident_request(ticket_id, ticket_key_type)
        headers = self._build_headers()
        response = requests.get(url, headers=headers, params=params, timeout=self.timeout_seconds)
        if response.status_code >= 400:
            raise ServiceNowClientError(
                f"ServiceNow incident fetch failed with status {response.status_code}"
            )

        payload = response.json()
        result = payload.get("result")
        if ticket_key_type == "number":
            if not isinstance(result, list) or not result:
                return None
            record = result[0]
        else:
            if not isinstance(result, dict) or not result:
                return None
            record = result

        normalized = {
            "sys_id": self._extract_value(record.get("sys_id")),
            "number": self._extract_value(record.get("number")),
            "short_description": self._extract_value(record.get("short_description")),
            "description": self._extract_value(record.get("description")),
            "close_notes": self._extract_value(record.get("close_notes")),
            "state": self._extract_value(record.get("state")),
            "priority": self._extract_value(record.get("priority")),
            "severity": self._extract_value(record.get("severity")),
            "assignment_group": self._extract_value(record.get("assignment_group")),
            "opened_at": self._extract_value(record.get("opened_at")),
            "closed_at": self._extract_value(record.get("closed_at")),
            "caller_id": self._extract_value(record.get("caller_id")),
            "cmdb_ci": self._extract_value(record.get("cmdb_ci")),
            "work_notes": [],
            "comments": [],
        }

        sys_id = normalized.get("sys_id")
        if sys_id:
            work_notes, comments = self._fetch_journal_notes(sys_id)
            normalized["work_notes"] = work_notes
            normalized["comments"] = comments

        return normalized

    def _fetch_journal_notes(self, sys_id: str) -> tuple[list[str], list[str]]:
        url = f"{self.instance_url}/api/now/table/sys_journal_field"
        params = {
            "sysparm_query": f"element_id={sys_id}^elementINwork_notes,comments",
            "sysparm_fields": "element,value,sys_created_on,sys_created_by",
            "sysparm_orderbyDESC": "sys_created_on",
            "sysparm_limit": "50",
            "sysparm_display_value": "true",
        }
        headers = self._build_headers()

        response = requests.get(url, headers=headers, params=params, timeout=self.timeout_seconds)
        if response.status_code >= 400:
            return [], []

        payload = response.json()
        result = payload.get("result")
        if not isinstance(result, list):
            return [], []

        work_notes: list[str] = []
        comments: list[str] = []
        for item in result:
            element = self._extract_value(item.get("element"))
            value = self._extract_value(item.get("value"))
            if not value:
                continue
            if element == "work_notes":
                work_notes.append(value)
            elif element == "comments":
                comments.append(value)
        return work_notes, comments

    @staticmethod
    def _extract_value(raw: Any) -> str | None:
        if raw is None:
            return None
        if isinstance(raw, dict):
            if "display_value" in raw and raw["display_value"] is not None:
                return str(raw["display_value"])
            if "value" in raw and raw["value"] is not None:
                return str(raw["value"])
            return None
        return str(raw)

from __future__ import annotations

import base64
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
        similar_record_types: list[str] | None = None,
        similar_max_results: int = 5,
        child_record_table: str = "incident",
        child_auth_scheme: str | None = None,
        child_api_token: str | None = None,
        child_username: str | None = None,
        child_password: str | None = None,
    ) -> None:
        self.instance_url = instance_url.rstrip("/")
        self.api_token = api_token
        self.auth_scheme = auth_scheme
        self.timeout_seconds = timeout_seconds
        self.similar_record_types = self._normalize_record_types(similar_record_types or ["incident"])
        self.similar_max_results = max(1, int(similar_max_results))
        self.child_record_table = (child_record_table or "incident").strip().lower()
        self.child_auth_scheme = (child_auth_scheme or auth_scheme).strip()
        self.child_api_token = child_api_token or api_token
        self.child_username = child_username or ""
        self.child_password = child_password or ""

    def _build_headers(self) -> dict[str, str]:
        return self._build_headers_with_auth(self.auth_scheme, self.api_token)

    def _build_headers_with_auth(self, auth_scheme: str, token: str | None) -> dict[str, str]:
        scheme = (auth_scheme or "Bearer").strip().lower()
        headers = {
            "Accept": "application/json",
        }

        if scheme in {"x-sn-apikey", "sn_apikey", "apikey", "api_key"}:
            headers["x-sn-apikey"] = token or ""
            return headers

        if scheme == "basic":
            if self.child_username and self.child_password:
                credentials = f"{self.child_username}:{self.child_password}".encode("utf-8")
                encoded = base64.b64encode(credentials).decode("ascii")
                headers["Authorization"] = f"Basic {encoded}"
                return headers
            if token:
                headers["Authorization"] = f"Basic {token}"
                return headers

        headers["Authorization"] = f"{auth_scheme} {token or ''}".strip()
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

    def create_child_incident(
        self,
        parent_ticket: dict[str, Any],
        short_description: str,
        description: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        url = f"{self.instance_url}/api/now/table/{self.child_record_table}"
        headers = self._build_headers_with_auth(self.child_auth_scheme, self.child_api_token)
        headers["Content-Type"] = "application/json"

        payload = {
            "short_description": short_description,
            "description": description,
            "parent_incident": parent_ticket.get("sys_id") or "",
            "assignment_group": parent_ticket.get("assignment_group") or "",
            "caller_id": parent_ticket.get("caller_id") or "",
            "cmdb_ci": parent_ticket.get("cmdb_ci") or "",
        }

        response = requests.post(url, headers=headers, json=payload, timeout=self.timeout_seconds)
        if response.status_code == 401 and self.child_auth_scheme.strip().lower() != "basic":
            basic_headers = self._build_headers_with_auth("Basic", self.child_api_token)
            basic_headers["Content-Type"] = "application/json"
            response = requests.post(
                url,
                headers=basic_headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
        if response.status_code >= 400:
            snippet = (response.text or "").strip().replace("\n", " ")[:500]
            raise ServiceNowClientError(
                f"ServiceNow child incident create failed with status {response.status_code}. Response: {snippet}"
            )

        response_payload = response.json()
        result = response_payload.get("result")
        if not isinstance(result, dict) or not result:
            raise ServiceNowClientError("ServiceNow child incident create returned empty result")

        return {
            "sys_id": self._extract_value(result.get("sys_id")),
            "number": self._extract_value(result.get("number")),
            "short_description": self._extract_value(result.get("short_description")),
            "state": self._extract_value(result.get("state")),
            "assignment_group": self._extract_value(result.get("assignment_group")),
            "opened_at": self._extract_value(result.get("opened_at")),
        }

    def build_similar_records_request(
        self,
        record_type: str,
        ticket: dict[str, Any],
        max_results: int,
    ) -> tuple[str, dict[str, str]]:
        normalized_record_type = (record_type or "incident").strip().lower()
        url = f"{self.instance_url}/api/now/table/{normalized_record_type}"

        query_parts: list[str] = []
        cmdb_ci = ticket.get("cmdb_ci")
        assignment_group = ticket.get("assignment_group")
        caller_id = ticket.get("caller_id")

        if cmdb_ci:
            query_parts.append(f"cmdb_ci={quote(str(cmdb_ci), safe='')}")
        if assignment_group:
            query_parts.append(f"assignment_group={quote(str(assignment_group), safe='')}")
        if caller_id:
            query_parts.append(f"caller_id={quote(str(caller_id), safe='')}")

        if not query_parts:
            return url, {
                "sysparm_query": "sys_idISEMPTY",
                "sysparm_limit": "0",
                "sysparm_fields": INCIDENT_FIELDS,
                "sysparm_display_value": "true",
            }

        encoded_query = "^OR".join(query_parts)
        params = {
            "sysparm_query": encoded_query,
            "sysparm_limit": str(max(1, max_results + 1)),
            "sysparm_fields": INCIDENT_FIELDS,
            "sysparm_display_value": "true",
            "sysparm_orderbyDESC": "opened_at",
        }
        return url, params

    def build_similar_incidents_request(
        self,
        ticket: dict[str, Any],
        max_results: int,
    ) -> tuple[str, dict[str, str]]:
        return self.build_similar_records_request("incident", ticket, max_results)

    def fetch_similar_records(
        self,
        ticket: dict[str, Any],
        record_types: list[str] | None = None,
        max_results: int | None = None,
    ) -> list[dict[str, Any]]:
        resolved_max_results = max(1, int(max_results or self.similar_max_results))
        resolved_record_types = self._normalize_record_types(record_types or self.similar_record_types)
        headers = self._build_headers()

        current_sys_id = ticket.get("sys_id")
        current_number = ticket.get("number")
        normalized_items: list[dict[str, Any]] = []

        for record_type in resolved_record_types:
            url, params = self.build_similar_records_request(record_type, ticket, resolved_max_results)
            if params.get("sysparm_limit") == "0":
                continue

            response = requests.get(url, headers=headers, params=params, timeout=self.timeout_seconds)
            if response.status_code >= 400:
                continue

            payload = response.json()
            result = payload.get("result")
            if not isinstance(result, list):
                continue

            for record in result:
                if not isinstance(record, dict):
                    continue
                normalized = {
                    "record_type": record_type,
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
                }

                if normalized.get("sys_id") and normalized.get("sys_id") == current_sys_id:
                    continue
                if normalized.get("number") and normalized.get("number") == current_number:
                    continue

                normalized_items.append(normalized)
                if len(normalized_items) >= resolved_max_results:
                    return normalized_items

        return normalized_items

    def fetch_similar_incidents(
        self,
        ticket: dict[str, Any],
        max_results: int = 5,
    ) -> list[dict[str, Any]]:
        return self.fetch_similar_records(
            ticket=ticket,
            record_types=["incident"],
            max_results=max_results,
        )

    @staticmethod
    def _normalize_record_types(record_types: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in record_types:
            value = (item or "").strip().lower()
            if not value or value in normalized:
                continue
            normalized.append(value)
        return normalized or ["incident"]

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

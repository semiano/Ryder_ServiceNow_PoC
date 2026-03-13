from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
import os
from pathlib import Path

import requests
from azure.identity import DefaultAzureCredential


@dataclass
class FoundryClientError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


class FoundryClient:
    def __init__(
        self,
        endpoint_url: str,
        timeout_seconds: int = 60,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.timeout_seconds = timeout_seconds
        self.credential = DefaultAzureCredential()
        self.token_scope = os.getenv("FOUNDRY_TOKEN_SCOPE", "https://ai.azure.com/.default")
        self.prompt_path = os.getenv(
            "FOUNDRY_AGENT_PROMPT_PATH",
            "system_documentation/foundry-agent-system-prompt.md",
        )

    def _build_headers(self) -> dict[str, str]:
        token = self.credential.get_token(self.token_scope)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token.token}",
        }
        return headers

    def generate_rca(
        self,
        correlation_id: str,
        service_now_ticket: dict[str, Any],
        ticket_body_text: str,
        transcript_text: str,
        transcript_metadata: dict[str, Any],
        similar_tickets: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], str]:
        model_input = {
            "correlationId": correlation_id,
            "serviceNowTicket": {
                "record": service_now_ticket,
                "ticketBodyText": ticket_body_text,
            },
            "transcriptText": transcript_text,
            "transcriptMetadata": transcript_metadata,
            "similarTickets": similar_tickets or [],
            "responseRequirements": {
                "strictJson": True,
                "schemaVersion": "1.0",
            },
        }

        payload = {
            "input": [
                {
                    "role": "user",
                    "content": (
                        "SYSTEM_PROMPT:\n"
                        f"{self._load_system_prompt()}\n\n"
                        "INPUT_JSON:\n"
                        f"{json.dumps(model_input)}"
                    ),
                }
            ],
        }

        headers = self._build_headers()
        response = requests.post(
            self.endpoint_url,
            headers=headers,
            json=payload,
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            response_snippet = (response.text or "").strip().replace("\n", " ")[:500]
            raise FoundryClientError(
                f"Foundry request failed with status {response.status_code}. Response: {response_snippet}"
            )
        payload_json = response.json()
        rca = self._extract_rca(payload_json)
        model = str(payload_json.get("model") or payload_json.get("modelName") or "unknown")
        return rca, model

    def _load_system_prompt(self) -> str:
        prompt_file = Path(self.prompt_path)
        if prompt_file.exists() and prompt_file.is_file():
            return prompt_file.read_text(encoding="utf-8")
        return (
            "You are an enterprise reliability analysis assistant for closed critical incidents. "
            "Return only valid JSON and no markdown. Output must strictly follow schemaVersion 1.0 RCA format "
            "with keys: ticket, summary, timeline, rootCause, contributingFactors, detection, resolution, "
            "correctiveActions, evidence, risks, similarIncidents, appendix."
        )

    def _extract_rca(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._looks_like_rca(payload):
            return payload

        output_text = payload.get("output_text")
        if isinstance(output_text, str):
            parsed = self._try_parse_json(output_text)
            if isinstance(parsed, dict) and self._looks_like_rca(parsed):
                return parsed

        output_items = payload.get("output")
        if isinstance(output_items, list):
            for item in output_items:
                if not isinstance(item, dict):
                    continue
                content_items = item.get("content")
                if not isinstance(content_items, list):
                    continue
                for content in content_items:
                    if not isinstance(content, dict):
                        continue
                    text_candidate = (
                        content.get("text")
                        or content.get("output_text")
                        or content.get("value")
                    )
                    if not isinstance(text_candidate, str):
                        continue
                    parsed = self._try_parse_json(text_candidate)
                    if isinstance(parsed, dict) and self._looks_like_rca(parsed):
                        return parsed

        for key in ("rca", "result", "output", "data"):
            candidate = payload.get(key)
            if isinstance(candidate, dict) and self._looks_like_rca(candidate):
                return candidate
            if isinstance(candidate, str):
                parsed = self._try_parse_json(candidate)
                if isinstance(parsed, dict) and self._looks_like_rca(parsed):
                    return parsed

        content = payload.get("content")
        if isinstance(content, str):
            parsed = self._try_parse_json(content)
            if isinstance(parsed, dict) and self._looks_like_rca(parsed):
                return parsed

        payload_keys = list(payload.keys())[:20]
        raise FoundryClientError(
            f"Unable to extract RCA JSON from Foundry response. Payload keys: {payload_keys}"
        )

    @staticmethod
    def _looks_like_rca(candidate: dict[str, Any]) -> bool:
        return "schemaVersion" in candidate and "rootCause" in candidate and "summary" in candidate

    @staticmethod
    def _try_parse_json(raw: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
        return None

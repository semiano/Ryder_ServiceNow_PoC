from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any

import requests


JOIN_URL_REGEX = re.compile(r"(?i)https://teams\.microsoft\.com/l/meetup-join/[\w%\-\.\/:?=&+]+")
THREAD_TOKEN_REGEX = re.compile(r"(?i)19:[A-Za-z0-9_\-\.]+@thread\.v2")
GUID_TOKEN_REGEX = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
SUBJECT_REGEX = re.compile(r"(?im)^(?:subject|meeting)\s*:\s*(.+)$")


@dataclass
class GraphClientError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


def extract_meeting_reference(ticket: dict[str, Any]) -> dict[str, Any]:
    scan_fields: list[tuple[str, str]] = [
        ("close_notes", ticket.get("close_notes") or ""),
        ("description", ticket.get("description") or ""),
        ("short_description", ticket.get("short_description") or ""),
        ("work_notes", "\n".join(ticket.get("work_notes") or [])),
        ("comments", "\n".join(ticket.get("comments") or [])),
    ]

    reference: dict[str, Any] = {
        "meetingJoinUrl": None,
        "meetingIdCandidate": None,
        "meetingSubjectCandidate": None,
        "foundInField": None,
    }

    for field_name, text in scan_fields:
        if not text:
            continue

        join_match = JOIN_URL_REGEX.search(text)
        thread_match = THREAD_TOKEN_REGEX.search(text)
        guid_match = GUID_TOKEN_REGEX.search(text)
        subject_match = SUBJECT_REGEX.search(text)

        if join_match:
            reference["meetingJoinUrl"] = join_match.group(0).strip()
            reference["foundInField"] = field_name
        if thread_match and reference["meetingIdCandidate"] is None:
            reference["meetingIdCandidate"] = thread_match.group(0).strip()
            reference["foundInField"] = reference["foundInField"] or field_name
        if guid_match and reference["meetingIdCandidate"] is None:
            reference["meetingIdCandidate"] = guid_match.group(0).strip()
            reference["foundInField"] = reference["foundInField"] or field_name
        if subject_match and reference["meetingSubjectCandidate"] is None:
            reference["meetingSubjectCandidate"] = subject_match.group(1).strip()
            reference["foundInField"] = reference["foundInField"] or field_name

        if reference["meetingJoinUrl"] and reference["meetingIdCandidate"]:
            break

    return reference


class GraphClient:
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        transcript_lookback_days: int = 30,
        transcript_max_chars: int = 120000,
        fallback_user_id: str | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.transcript_lookback_days = transcript_lookback_days
        self.transcript_max_chars = transcript_max_chars
        self.fallback_user_id = fallback_user_id
        self.timeout_seconds = timeout_seconds

    def extract_meeting_reference(self, ticket: dict[str, Any]) -> dict[str, Any]:
        return extract_meeting_reference(ticket)

    def fetch_transcript_best_effort(
        self,
        ticket: dict[str, Any],
        meeting_reference: dict[str, Any],
        correlation_id: str,
    ) -> dict[str, Any]:
        del ticket
        del correlation_id

        result = {
            "attempted": True,
            "found": False,
            "source": "graph",
            "details": {
                "matchStrategy": "none",
                "graphTranscriptId": None,
                "graphMeetingId": None,
            },
            "transcriptText": "",
            "transcriptChars": 0,
        }

        if not meeting_reference.get("meetingJoinUrl") and not meeting_reference.get("meetingIdCandidate"):
            result["details"]["matchStrategy"] = "no_reference"
            return result

        access_token = self._acquire_token()
        user_id = self.fallback_user_id
        join_url = meeting_reference.get("meetingJoinUrl")
        meeting_id = None
        strategy = "no_user_context"

        if join_url and user_id:
            strategy = "join_url_user_lookup"
            meeting_id = self._resolve_meeting_id_by_join_url(access_token, user_id, join_url)

        if meeting_id is None and meeting_reference.get("meetingIdCandidate") and user_id:
            strategy = "meeting_id_candidate"
            meeting_id = str(meeting_reference["meetingIdCandidate"])

        if meeting_id is None:
            result["details"]["matchStrategy"] = strategy
            return result

        transcript = self._fetch_latest_transcript(access_token, user_id, meeting_id)
        if transcript is None:
            result["details"]["matchStrategy"] = f"{strategy}:no_transcript"
            result["details"]["graphMeetingId"] = meeting_id
            return result

        transcript_text = self._fetch_transcript_content(
            access_token,
            user_id,
            meeting_id,
            transcript["id"],
        )

        truncated = transcript_text[: self.transcript_max_chars]
        result["found"] = True
        result["details"]["matchStrategy"] = strategy
        result["details"]["graphMeetingId"] = meeting_id
        result["details"]["graphTranscriptId"] = transcript.get("id")
        result["transcriptText"] = truncated
        result["transcriptChars"] = len(truncated)
        return result

    def _acquire_token(self) -> str:
        url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://graph.microsoft.com/.default",
        }
        response = requests.post(url, data=data, timeout=self.timeout_seconds)
        if response.status_code >= 400:
            raise GraphClientError(f"Graph token request failed with status {response.status_code}")
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise GraphClientError("Graph token response missing access_token")
        return str(token)

    def _resolve_meeting_id_by_join_url(
        self,
        access_token: str,
        user_id: str,
        join_url: str,
    ) -> str | None:
        encoded_join_url = join_url.replace("'", "''")
        lookback_start = datetime.now(tz=timezone.utc) - timedelta(days=self.transcript_lookback_days)
        endpoint = (
            f"https://graph.microsoft.com/v1.0/users/{user_id}/onlineMeetings"
            f"?$filter=JoinWebUrl eq '{encoded_join_url}' and creationDateTime ge {lookback_start.isoformat()}"
        )
        headers = {"Authorization": f"Bearer {access_token}"}
        response = requests.get(endpoint, headers=headers, timeout=self.timeout_seconds)
        if response.status_code >= 400:
            return None
        payload = response.json()
        meetings = payload.get("value")
        if not isinstance(meetings, list) or not meetings:
            return None
        return meetings[0].get("id")

    def _fetch_latest_transcript(
        self,
        access_token: str,
        user_id: str | None,
        meeting_id: str,
    ) -> dict[str, Any] | None:
        if not user_id:
            return None
        endpoint = (
            f"https://graph.microsoft.com/v1.0/users/{user_id}/onlineMeetings/{meeting_id}/transcripts"
        )
        headers = {"Authorization": f"Bearer {access_token}"}
        response = requests.get(endpoint, headers=headers, timeout=self.timeout_seconds)
        if response.status_code >= 400:
            return None
        payload = response.json()
        transcripts = payload.get("value")
        if not isinstance(transcripts, list) or not transcripts:
            return None
        transcripts.sort(key=lambda t: t.get("createdDateTime") or "", reverse=True)
        return transcripts[0]

    def _fetch_transcript_content(
        self,
        access_token: str,
        user_id: str | None,
        meeting_id: str,
        transcript_id: str,
    ) -> str:
        if not user_id:
            return ""
        endpoint = (
            f"https://graph.microsoft.com/v1.0/users/{user_id}/onlineMeetings/{meeting_id}"
            f"/transcripts/{transcript_id}/content?$format=text/vtt"
        )
        headers = {"Authorization": f"Bearer {access_token}"}
        response = requests.get(endpoint, headers=headers, timeout=self.timeout_seconds)
        if response.status_code >= 400:
            raise GraphClientError(
                f"Graph transcript content request failed with status {response.status_code}"
            )
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            payload = response.json()
            return str(payload.get("transcriptContent") or "")
        return response.text

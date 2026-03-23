from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from time import perf_counter
import traceback
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4
import re

import azure.functions as func

from models.rca_schema import RcaValidationError, validate_rca_schema
from services.cosmos_table_repo import CosmosTableRepoError, CosmosTableRepository
from services.foundry_client import FoundryClient, FoundryClientError
from services.graph_client import GraphClient, GraphClientError
from services.servicenow_client import ServiceNowClient, ServiceNowClientError
from utils.logging import get_logger, log_event


GUID_REGEX = re.compile(
    r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


@dataclass
class ProcessingError(Exception):
    code: str
    message: str
    http_status: int = 500


@dataclass
class ProcessingDependencies:
    service_now: ServiceNowClient
    graph: GraphClient
    foundry: FoundryClient
    cosmos: CosmosTableRepository


def is_guid_ticket_id(ticket_id: str) -> bool:
    return bool(GUID_REGEX.match(ticket_id.strip()))


def resolve_ticket_key_type(ticket_id: str) -> str:
    return "sys_id" if is_guid_ticket_id(ticket_id) else "number"


def normalize_status(status: str) -> str:
    return status.strip().lower()


def current_utc_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_simulated_transcript_text() -> str:
    project_root = Path(__file__).resolve().parents[1]
    transcript_path = project_root / "simulated_call_transcript.txt"
    if transcript_path.exists():
        return transcript_path.read_text(encoding="utf-8").strip()
    return "simulated call transcript"


def validate_request_payload(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        raise ProcessingError("BAD_REQUEST_VALIDATION", "Request body must be a JSON object", 400)
    ticket_id = payload.get("ticketId")
    status = payload.get("status")
    if not isinstance(ticket_id, str) or not ticket_id.strip():
        raise ProcessingError("BAD_REQUEST_VALIDATION", "ticketId is required", 400)
    if not isinstance(status, str) or not status.strip():
        raise ProcessingError("BAD_REQUEST_VALIDATION", "status is required", 400)
    return ticket_id.strip(), status


def _safe_url_host(value: str | None) -> str:
    if not value:
        return ""
    try:
        parsed = urlparse(value)
        return parsed.netloc or ""
    except Exception:
        return ""


def _runtime_config_snapshot() -> dict[str, Any]:
    required_env_keys = [
        "SERVICENOW_INSTANCE_URL",
        "SERVICENOW_API_TOKEN",
        "GRAPH_TENANT_ID",
        "GRAPH_CLIENT_ID",
        "GRAPH_CLIENT_SECRET",
        "FOUNDRY_AGENT_ENDPOINT_URL",
    ]
    return {
        "serviceNowHost": _safe_url_host(os.getenv("SERVICENOW_INSTANCE_URL")),
        "foundryHost": _safe_url_host(os.getenv("FOUNDRY_AGENT_ENDPOINT_URL")),
        "cosmosEndpointHost": _safe_url_host(os.getenv("COSMOS_TABLE_ENDPOINT")),
        "cosmosAuthMode": os.getenv("COSMOS_TABLE_AUTH_MODE", "auto"),
        "graphFallbackUserIdConfigured": bool(os.getenv("GRAPH_FALLBACK_USER_ID")),
        "simulateCallTranscriptLookup": env_flag("SIMULATE_CALL_TRANSCRIPT_LOOKUP", default=False),
        "serviceNowSimilarRecordTypes": os.getenv("SERVICENOW_SIMILAR_RECORD_TYPES", "incident"),
        "serviceNowSimilarMaxResults": os.getenv("SERVICENOW_SIMILAR_MAX_RESULTS", "5"),
        "serviceNowChildRecordTable": os.getenv("SERVICENOW_CHILD_RECORD_TABLE", "incident"),
        "serviceNowChildAuthScheme": os.getenv("SERVICENOW_CHILD_AUTH_SCHEME", os.getenv("SERVICENOW_AUTH_SCHEME", "Bearer")),
        "serviceNowChildHasDedicatedToken": bool(os.getenv("SERVICENOW_CHILD_API_TOKEN")),
        "serviceNowChildBasicUserConfigured": bool(os.getenv("SERVICENOW_CHILD_USERNAME")),
        "missingRequiredEnvKeys": [key for key in required_env_keys if not os.getenv(key)],
    }


def _mask_identifier(value: str | None, visible_suffix: int = 8) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) <= visible_suffix:
        return "*" * len(normalized)
    return f"***{normalized[-visible_suffix:]}"


def compose_ticket_body_text(ticket: dict[str, Any]) -> str:
    work_notes = ticket.get("work_notes") or []
    comments = ticket.get("comments") or []

    sections = [
        ("Ticket Number", ticket.get("number")),
        ("Ticket SysId", ticket.get("sys_id")),
        ("Short Description", ticket.get("short_description")),
        ("Description", ticket.get("description")),
        ("Close Notes", ticket.get("close_notes")),
        ("State", ticket.get("state")),
        ("Priority", ticket.get("priority")),
        ("Severity", ticket.get("severity")),
        ("Assignment Group", ticket.get("assignment_group")),
        ("Opened At", ticket.get("opened_at")),
        ("Closed At", ticket.get("closed_at")),
        ("Caller", ticket.get("caller_id")),
        ("CI", ticket.get("cmdb_ci")),
        ("Latest Work Notes", "\n".join(work_notes[:10])),
        ("Latest Comments", "\n".join(comments[:10])),
    ]

    rendered: list[str] = []
    for label, value in sections:
        rendered.append(f"{label}: {value if value is not None else ''}")
    return "\n".join(rendered)


def _stringify_json(value: Any, indent: int | None = None) -> str:
    return json.dumps(value, indent=indent, ensure_ascii=False, default=str)


def _truncate_text(value: str, max_chars: int = 30000) -> str:
    if len(value) <= max_chars:
        return value
    return f"{value[:max_chars]}\n\n[truncated at {max_chars} chars]"


def build_similar_ticket_references(similar_incidents: list[dict[str, Any]]) -> list[dict[str, str]]:
    references: list[dict[str, str]] = []
    for item in similar_incidents:
        references.append(
            {
                "recordType": str(item.get("record_type") or ""),
                "ticketId": str(item.get("number") or item.get("sys_id") or ""),
                "shortDescription": str(item.get("short_description") or ""),
                "sysId": str(item.get("sys_id") or ""),
            }
        )
    return references


def compose_rca_child_ticket_short_description(parent_ticket: dict[str, Any]) -> str:
    parent_title = (parent_ticket.get("short_description") or parent_ticket.get("number") or "Parent Incident").strip()
    return f"RCA Report: {parent_title}"[:160]


def compose_rca_child_ticket_description(
    parent_ticket: dict[str, Any],
    rca_json: dict[str, Any],
    similar_incidents: list[dict[str, Any]],
) -> str:
    summary = rca_json.get("summary") or {}
    root_cause = rca_json.get("rootCause") or {}
    corrective_actions = rca_json.get("correctiveActions") or []

    similar_lines = [
        f"- [{item.get('record_type')}] {item.get('number') or item.get('sys_id')}: {item.get('short_description') or ''}"
        for item in similar_incidents
    ]
    corrective_lines = [
        f"- {action.get('action')} (owner={action.get('owner')}, priority={action.get('priority')})"
        for action in corrective_actions
        if isinstance(action, dict)
    ]

    rendered = [
        f"Parent Incident Number: {parent_ticket.get('number')}",
        f"Parent Incident SysId: {parent_ticket.get('sys_id')}",
        f"Parent Short Description: {parent_ticket.get('short_description')}",
        "",
        "RCA Executive Summary:",
        str(summary.get("executiveSummary") or ""),
        "",
        "Root Cause:",
        f"- Statement: {root_cause.get('statement')}",
        f"- Category: {root_cause.get('category')}",
        f"- Confidence: {root_cause.get('confidence')}",
        "",
        "Corrective Actions:",
        *(corrective_lines or ["- None provided"]),
        "",
        "Similar Referenced Tickets:",
        *(similar_lines or ["- None"]),
        "",
        "Full RCA JSON:",
        _stringify_json(rca_json, indent=2),
    ]
    return _truncate_text("\n".join(rendered))


def _build_error_response(
    ticket_id: str | None,
    correlation_id: str,
    code: str,
    message: str,
) -> dict[str, Any]:
    return {
        "processed": False,
        "reason": "error",
        "ticketId": ticket_id,
        "correlationId": correlation_id,
        "error": {
            "code": code,
            "message": message,
        },
    }


def process_payload(
    payload: Any,
    dependencies: ProcessingDependencies | None,
    correlation_id: str,
    logger_name: str = "process_closed_ticket",
) -> tuple[int, dict[str, Any]]:
    logger = get_logger(logger_name)
    processing_start_iso = current_utc_iso()
    total_start = perf_counter()

    service_now_ms = 0
    graph_ms = 0
    foundry_ms = 0
    cosmos_ms = 0
    similar_incidents_ms = 0
    child_ticket_ms = 0

    ticket_id = None
    try:
        ticket_id, status = validate_request_payload(payload)
        normalized_status = normalize_status(status)
        log_event(
            logger,
            "info",
            "request received",
            correlation_id,
            ticket_id,
            operationName="ProcessClosedTicket",
            normalizedStatus=normalized_status,
        )

        if normalized_status != "closed":
            log_event(
                logger,
                "info",
                "status not closed early exit",
                correlation_id,
                ticket_id,
                operationName="ProcessClosedTicket",
            )
            return (
                200,
                {
                    "processed": False,
                    "reason": "status_not_closed",
                    "ticketId": ticket_id,
                    "normalizedStatus": normalized_status,
                    "correlationId": correlation_id,
                },
            )

        if dependencies is None:
            log_event(
                logger,
                "info",
                "runtime config snapshot",
                correlation_id,
                ticket_id,
                **_runtime_config_snapshot(),
            )
            try:
                dependencies = build_dependencies_from_environment()
            except CosmosTableRepoError as exc:
                raise ProcessingError("COSMOS_WRITE_FAILED", str(exc)) from exc
            except KeyError as exc:
                missing_key = str(exc).strip("'")
                raise ProcessingError(
                    "CONFIG_MISSING",
                    f"Required configuration missing: {missing_key}",
                ) from exc
            except ValueError as exc:
                raise ProcessingError("CONFIG_INVALID", str(exc)) from exc

        ticket_key_type = resolve_ticket_key_type(ticket_id)
        log_event(
            logger,
            "info",
            "ticket key type resolved",
            correlation_id,
            ticket_id,
            ticketKeyType=ticket_key_type,
        )

        sn_start = perf_counter()
        log_event(logger, "info", "ServiceNow fetch start", correlation_id, ticket_id)
        ticket = dependencies.service_now.fetch_incident(ticket_id, ticket_key_type)
        service_now_ms = int((perf_counter() - sn_start) * 1000)
        log_event(
            logger,
            "info",
            "ServiceNow fetch end",
            correlation_id,
            ticket_id,
            serviceNowFetchMs=service_now_ms,
        )
        if not ticket:
            raise ProcessingError("SERVICENOW_NOT_FOUND", "Incident not found")

        ticket_body_text = compose_ticket_body_text(ticket)
        meeting_reference = dependencies.graph.extract_meeting_reference(ticket)
        log_event(
            logger,
            "info",
            "transcript extraction result",
            correlation_id,
            ticket_id,
            meetingReferenceFound=bool(
                meeting_reference.get("meetingJoinUrl") or meeting_reference.get("meetingIdCandidate")
            ),
            meetingJoinUrlFound=bool(meeting_reference.get("meetingJoinUrl")),
            meetingIdCandidateMasked=_mask_identifier(meeting_reference.get("meetingIdCandidate")),
            meetingReferenceField=meeting_reference.get("foundInField"),
        )

        transcript_result = {
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
        graph_start = perf_counter()
        if env_flag("SIMULATE_CALL_TRANSCRIPT_LOOKUP", default=False):
            simulated_text = load_simulated_transcript_text()
            transcript_result = {
                "attempted": True,
                "found": True,
                "source": "graph",
                "details": {
                    "matchStrategy": "simulated_transcript",
                    "graphTranscriptId": None,
                    "graphMeetingId": None,
                },
                "transcriptText": simulated_text,
                "transcriptChars": len(simulated_text),
            }
            log_event(
                logger,
                "info",
                "Graph fetch bypassed via simulation flag",
                correlation_id,
                ticket_id,
                simulationEnabled=True,
            )
        else:
            try:
                transcript_result = dependencies.graph.fetch_transcript_best_effort(
                    ticket,
                    meeting_reference,
                    correlation_id,
                )
            except GraphClientError as graph_exc:
                log_event(
                    logger,
                    "warning",
                    "Graph fetch attempt failed",
                    correlation_id,
                    ticket_id,
                    warningCode="GRAPH_TRANSCRIPT_FAILED",
                    warningMessage=str(graph_exc),
                )
        graph_ms = int((perf_counter() - graph_start) * 1000)
        log_event(
            logger,
            "info",
            "Graph fetch attempt result",
            correlation_id,
            ticket_id,
            graphFetchMs=graph_ms,
            transcriptFound=transcript_result.get("found", False),
            transcriptMatchStrategy=transcript_result["details"].get("matchStrategy"),
            graphMeetingIdMasked=_mask_identifier(transcript_result["details"].get("graphMeetingId")),
            graphTranscriptIdMasked=_mask_identifier(transcript_result["details"].get("graphTranscriptId")),
        )

        similar_incidents: list[dict[str, Any]] = []
        similar_start = perf_counter()
        try:
            similar_incidents = dependencies.service_now.fetch_similar_records(ticket)
        except ServiceNowClientError as sn_similar_exc:
            log_event(
                logger,
                "warning",
                "ServiceNow similar incident lookup failed",
                correlation_id,
                ticket_id,
                warningCode="SERVICENOW_SIMILAR_FETCH_FAILED",
                warningMessage=str(sn_similar_exc),
            )
        similar_incidents_ms = int((perf_counter() - similar_start) * 1000)
        log_event(
            logger,
            "info",
            "ServiceNow similar incident lookup result",
            correlation_id,
            ticket_id,
            similarIncidentsCount=len(similar_incidents),
            similarIncidentsFetchMs=similar_incidents_ms,
            similarRecordTypes=list({item.get("record_type") for item in similar_incidents if item.get("record_type")}),
            similarIncidentNumbers=[item.get("number") for item in similar_incidents if item.get("number")],
        )

        foundry_start = perf_counter()
        log_event(logger, "info", "Foundry call start", correlation_id, ticket_id)
        try:
            rca_json, foundry_model = dependencies.foundry.generate_rca(
                correlation_id,
                ticket,
                ticket_body_text,
                transcript_result.get("transcriptText", ""),
                {
                    "meetingReference": meeting_reference,
                    "matchStrategy": transcript_result["details"].get("matchStrategy"),
                    "graphMeetingId": transcript_result["details"].get("graphMeetingId"),
                    "graphTranscriptId": transcript_result["details"].get("graphTranscriptId"),
                    "found": transcript_result.get("found", False),
                },
                similar_incidents,
            )
        except FoundryClientError as exc:
            raise ProcessingError("FOUNDRY_CALL_FAILED", str(exc)) from exc
        foundry_ms = int((perf_counter() - foundry_start) * 1000)
        log_event(
            logger,
            "info",
            "Foundry call end",
            correlation_id,
            ticket_id,
            foundryCallMs=foundry_ms,
        )

        try:
            validate_rca_schema(rca_json)
        except RcaValidationError as exc:
            raise ProcessingError("FOUNDRY_SCHEMA_INVALID", str(exc)) from exc

        cosmos_start = perf_counter()
        log_event(logger, "info", "Cosmos write start", correlation_id, ticket_id)
        total_ms = int((perf_counter() - total_start) * 1000)
        entity = {
            "PartitionKey": ticket_id,
            "RowKey": processing_start_iso,
            "CorrelationId": correlation_id,
            "TicketIdOriginal": ticket_id,
            "TicketKeyType": ticket_key_type,
            "ServiceNowSysId": ticket.get("sys_id"),
            "ServiceNowNumber": ticket.get("number"),
            "StatusNormalized": normalized_status,
            "ProcessedAtUtc": current_utc_iso(),
            "TranscriptFound": bool(transcript_result.get("found", False)),
            "TranscriptMatchStrategy": transcript_result["details"].get("matchStrategy"),
            "GraphMeetingId": transcript_result["details"].get("graphMeetingId"),
            "GraphTranscriptId": transcript_result["details"].get("graphTranscriptId"),
            "TranscriptChars": int(transcript_result.get("transcriptChars", 0)),
            "SimilarIncidentCount": len(similar_incidents),
            "SimilarTicketsJson": _stringify_json(build_similar_ticket_references(similar_incidents)),
            "FoundryModel": foundry_model or "unknown",
            "RCAJson": json.dumps(rca_json, separators=(",", ":")),
            "RCASummaryTitle": (rca_json.get("summary") or {}).get("title", ""),
            "RCARootCauseCategory": (rca_json.get("rootCause") or {}).get("category", ""),
            "RCARootCauseConfidence": float((rca_json.get("rootCause") or {}).get("confidence", 0.0)),
            "RCAChildTicketNumber": "",
            "RCAChildTicketSysId": "",
            "RCAChildTicketTitle": "",
            "RCAChildTicketCreated": False,
            "RCAChildTicketError": "",
            "TimingTotalMs": total_ms,
            "ErrorCode": None,
            "ErrorMessage": None,
        }

        try:
            dependencies.cosmos.upsert_entity(entity)
        except CosmosTableRepoError as exc:
            raise ProcessingError("COSMOS_WRITE_FAILED", str(exc)) from exc
        cosmos_ms = int((perf_counter() - cosmos_start) * 1000)
        log_event(
            logger,
            "info",
            "Cosmos write end",
            correlation_id,
            ticket_id,
            cosmosWriteMs=cosmos_ms,
        )

        child_ticket_start = perf_counter()
        child_short_description = compose_rca_child_ticket_short_description(ticket)
        child_description = compose_rca_child_ticket_description(
            ticket,
            rca_json,
            similar_incidents,
        )
        child_ticket: dict[str, Any] | None = None
        child_ticket_error = ""
        log_event(
            logger,
            "info",
            "ServiceNow RCA child ticket create start",
            correlation_id,
            ticket_id,
            childTicketTitle=child_short_description,
        )
        try:
            child_ticket = dependencies.service_now.create_child_incident(
                parent_ticket=ticket,
                short_description=child_short_description,
                description=child_description,
                correlation_id=correlation_id,
            )
        except ServiceNowClientError as exc:
            child_ticket_error = str(exc)
            log_event(
                logger,
                "warning",
                "ServiceNow RCA child ticket create failed",
                correlation_id,
                ticket_id,
                warningCode="SERVICENOW_CHILD_CREATE_FAILED",
                warningMessage=child_ticket_error,
            )
        child_ticket_ms = int((perf_counter() - child_ticket_start) * 1000)

        if child_ticket:
            entity["RCAChildTicketNumber"] = child_ticket.get("number") or ""
            entity["RCAChildTicketSysId"] = child_ticket.get("sys_id") or ""
            entity["RCAChildTicketTitle"] = child_ticket.get("short_description") or child_short_description
            entity["RCAChildTicketCreated"] = True
            entity["RCAChildTicketError"] = ""
        else:
            entity["RCAChildTicketNumber"] = ""
            entity["RCAChildTicketSysId"] = ""
            entity["RCAChildTicketTitle"] = child_short_description
            entity["RCAChildTicketCreated"] = False
            entity["RCAChildTicketError"] = child_ticket_error

        try:
            dependencies.cosmos.upsert_entity(entity)
        except CosmosTableRepoError as exc:
            raise ProcessingError("COSMOS_WRITE_FAILED", str(exc)) from exc

        log_event(
            logger,
            "info",
            "ServiceNow RCA child ticket create end",
            correlation_id,
            ticket_id,
            childTicketCreated=bool(child_ticket),
            childTicketNumber=(child_ticket or {}).get("number"),
            childTicketSysIdMasked=_mask_identifier((child_ticket or {}).get("sys_id")),
            childTicketError=child_ticket_error,
            childTicketCreateMs=child_ticket_ms,
        )

        total_ms = int((perf_counter() - total_start) * 1000)
        return (
            200,
            {
                "processed": True,
                "ticketId": ticket_id,
                "ticketKeyType": ticket_key_type,
                "serviceNow": {
                    "fetched": True,
                    "sys_id": ticket.get("sys_id"),
                    "number": ticket.get("number"),
                },
                "transcript": {
                    "attempted": True,
                    "found": transcript_result.get("found", False),
                    "source": "graph",
                    "details": {
                        "matchStrategy": transcript_result["details"].get("matchStrategy"),
                        "graphTranscriptId": transcript_result["details"].get("graphTranscriptId"),
                        "graphMeetingId": transcript_result["details"].get("graphMeetingId"),
                    },
                },
                "similarIncidents": {
                    "attempted": True,
                    "count": len(similar_incidents),
                    "recordTypes": list(
                        {item.get("record_type") for item in similar_incidents if item.get("record_type")}
                    ),
                    "numbers": [item.get("number") for item in similar_incidents if item.get("number")],
                },
                "rcaChildTicket": {
                    "created": entity["RCAChildTicketCreated"],
                    "number": entity["RCAChildTicketNumber"],
                    "sys_id": entity["RCAChildTicketSysId"],
                    "short_description": entity["RCAChildTicketTitle"],
                    "error": entity["RCAChildTicketError"] or None,
                },
                "rca": {
                    "generated": True,
                    "schemaVersion": os.getenv("RCA_SCHEMA_VERSION", "1.0"),
                    "tableKeys": {
                        "partitionKey": ticket_id,
                        "rowKey": processing_start_iso,
                    },
                },
                "cosmosTable": {
                    "written": True,
                },
                "correlationId": correlation_id,
                "timingMs": {
                    "total": total_ms,
                    "serviceNowFetch": service_now_ms,
                    "graphFetch": graph_ms,
                    "similarIncidentsFetch": similar_incidents_ms,
                    "foundryCall": foundry_ms,
                    "childTicketCreate": child_ticket_ms,
                    "cosmosWrite": cosmos_ms,
                },
            },
        )
    except ProcessingError as exc:
        log_event(
            logger,
            "error",
            "terminal processing error",
            correlation_id,
            ticket_id,
            errorCode=exc.code,
            errorMessage=exc.message,
        )
        return exc.http_status, _build_error_response(ticket_id, correlation_id, exc.code, exc.message)
    except ServiceNowClientError as exc:
        log_event(
            logger,
            "error",
            "ServiceNow fetch failure",
            correlation_id,
            ticket_id,
            errorCode="SERVICENOW_FETCH_FAILED",
            errorMessage=str(exc),
        )
        return 500, _build_error_response(
            ticket_id,
            correlation_id,
            "SERVICENOW_FETCH_FAILED",
            str(exc),
        )
    except Exception as exc:  # pragma: no cover
        log_event(
            logger,
            "error",
            "unhandled exception",
            correlation_id,
            ticket_id,
            errorCode="UNHANDLED_EXCEPTION",
            errorMessage=str(exc),
            traceback=traceback.format_exc(),
        )
        return 500, _build_error_response(
            ticket_id,
            correlation_id,
            "UNHANDLED_EXCEPTION",
            "Unexpected error occurred",
        )


def build_dependencies_from_environment() -> ProcessingDependencies:
    service_now = ServiceNowClient(
        instance_url=os.environ["SERVICENOW_INSTANCE_URL"],
        api_token=os.environ["SERVICENOW_API_TOKEN"],
        auth_scheme=os.getenv("SERVICENOW_AUTH_SCHEME", "Bearer"),
        similar_record_types=[
            item.strip()
            for item in os.getenv("SERVICENOW_SIMILAR_RECORD_TYPES", "incident").split(",")
            if item.strip()
        ],
        similar_max_results=int(os.getenv("SERVICENOW_SIMILAR_MAX_RESULTS", "5")),
        child_record_table=os.getenv("SERVICENOW_CHILD_RECORD_TABLE", "incident"),
        child_auth_scheme=os.getenv("SERVICENOW_CHILD_AUTH_SCHEME", os.getenv("SERVICENOW_AUTH_SCHEME", "Bearer")),
        child_api_token=os.getenv("SERVICENOW_CHILD_API_TOKEN") or os.environ["SERVICENOW_API_TOKEN"],
        child_username=os.getenv("SERVICENOW_CHILD_USERNAME") or None,
        child_password=os.getenv("SERVICENOW_CHILD_PASSWORD") or None,
    )
    graph = GraphClient(
        tenant_id=os.environ["GRAPH_TENANT_ID"],
        client_id=os.environ["GRAPH_CLIENT_ID"],
        client_secret=os.environ["GRAPH_CLIENT_SECRET"],
        transcript_lookback_days=int(os.getenv("TRANSCRIPT_LOOKBACK_DAYS", "30")),
        transcript_max_chars=int(os.getenv("TRANSCRIPT_MAX_CHARS", "120000")),
        fallback_user_id=os.getenv("GRAPH_FALLBACK_USER_ID") or None,
    )
    foundry = FoundryClient(
        endpoint_url=os.environ["FOUNDRY_AGENT_ENDPOINT_URL"],
        fallback_endpoint_urls=[
            endpoint.strip()
            for endpoint in os.getenv("FOUNDRY_AGENT_ENDPOINT_URLS", "").split(",")
            if endpoint.strip()
        ],
    )
    cosmos_auth_mode = os.getenv("COSMOS_TABLE_AUTH_MODE", "auto")
    cosmos = CosmosTableRepository(
        table_name=os.getenv("COSMOS_TABLE_NAME", "RcaReports"),
        auth_mode=cosmos_auth_mode,
        connection_string=os.getenv("COSMOS_TABLE_CONNECTION_STRING"),
        endpoint=os.getenv("COSMOS_TABLE_ENDPOINT"),
        ensure_table_exists=env_flag("COSMOS_TABLE_ENSURE_EXISTS", default=False),
    )
    return ProcessingDependencies(
        service_now=service_now,
        graph=graph,
        foundry=foundry,
        cosmos=cosmos,
    )


@app.route(route="process-closed-ticket", methods=["POST"])
def process_closed_ticket(req: func.HttpRequest) -> func.HttpResponse:
    correlation_id = str(uuid4())
    logger = get_logger("process_closed_ticket")
    body_size = 0
    try:
        raw_body = req.get_body() or b""
        body_size = len(raw_body)
    except Exception:
        body_size = 0

    log_event(
        logger,
        "info",
        "http request received",
        correlation_id,
        ticket_id=None,
        method=req.method,
        url=req.url,
        userAgent=req.headers.get("User-Agent"),
        contentType=req.headers.get("Content-Type"),
        bodyBytes=body_size,
    )
    try:
        payload = req.get_json()
    except ValueError:
        log_event(
            logger,
            "warning",
            "invalid json payload",
            correlation_id,
            ticket_id=None,
            contentType=req.headers.get("Content-Type"),
            bodyBytes=body_size,
        )
        body = _build_error_response(
            ticket_id=None,
            correlation_id=correlation_id,
            code="BAD_REQUEST_INVALID_JSON",
            message="Request body must be valid JSON",
        )
        return func.HttpResponse(
            json.dumps(body),
            status_code=400,
            mimetype="application/json",
        )

    status_code, body = process_payload(payload, None, correlation_id)
    return func.HttpResponse(
        json.dumps(body),
        status_code=status_code,
        mimetype="application/json",
    )

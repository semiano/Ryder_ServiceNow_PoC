from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from time import perf_counter
import traceback
from typing import Any
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
            try:
                dependencies = build_dependencies_from_environment()
            except CosmosTableRepoError as exc:
                raise ProcessingError("COSMOS_WRITE_FAILED", str(exc)) from exc

        ticket_key_type = resolve_ticket_key_type(ticket_id)

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
            "FoundryModel": foundry_model or "unknown",
            "RCAJson": json.dumps(rca_json, separators=(",", ":")),
            "RCASummaryTitle": (rca_json.get("summary") or {}).get("title", ""),
            "RCARootCauseCategory": (rca_json.get("rootCause") or {}).get("category", ""),
            "RCARootCauseConfidence": float((rca_json.get("rootCause") or {}).get("confidence", 0.0)),
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
                    "foundryCall": foundry_ms,
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
    try:
        payload = req.get_json()
    except ValueError:
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

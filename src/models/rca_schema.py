from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator


RCA_SCHEMA_V1: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schemaVersion",
        "ticket",
        "summary",
        "timeline",
        "rootCause",
        "contributingFactors",
        "detection",
        "resolution",
        "correctiveActions",
        "evidence",
        "risks",
        "similarIncidents",
        "appendix",
    ],
    "properties": {
        "schemaVersion": {"type": "string", "const": "1.0"},
        "ticket": {
            "type": "object",
            "additionalProperties": False,
            "required": ["number", "sys_id", "priority", "closedAt"],
            "properties": {
                "number": {"type": ["string", "null"]},
                "sys_id": {"type": ["string", "null"]},
                "priority": {"type": ["string", "null"]},
                "closedAt": {"type": ["string", "null"]},
            },
        },
        "summary": {
            "type": "object",
            "additionalProperties": False,
            "required": ["title", "executiveSummary", "customerImpact", "severity"],
            "properties": {
                "title": {"type": "string", "minLength": 1},
                "executiveSummary": {"type": "string", "minLength": 1},
                "customerImpact": {"type": "string", "minLength": 1},
                "severity": {"type": "string", "minLength": 1},
            },
        },
        "timeline": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["timestamp", "event", "source"],
                "properties": {
                    "timestamp": {"type": ["string", "null"]},
                    "event": {"type": "string", "minLength": 1},
                    "source": {
                        "type": "string",
                        "enum": ["ticket", "transcript", "inferred"],
                    },
                },
            },
        },
        "rootCause": {
            "type": "object",
            "additionalProperties": False,
            "required": ["statement", "category", "confidence"],
            "properties": {
                "statement": {"type": "string", "minLength": 1},
                "category": {
                    "type": "string",
                    "enum": [
                        "configuration",
                        "code",
                        "infrastructure",
                        "network",
                        "process",
                        "human_error",
                        "third_party",
                        "unknown",
                    ],
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
        },
        "contributingFactors": {
            "type": "array",
            "items": {"type": "string"},
        },
        "detection": {
            "type": "object",
            "additionalProperties": False,
            "required": ["howDetected", "whyNotDetectedSooner"],
            "properties": {
                "howDetected": {"type": "string"},
                "whyNotDetectedSooner": {"type": "string"},
            },
        },
        "resolution": {
            "type": "object",
            "additionalProperties": False,
            "required": ["fixApplied", "verification"],
            "properties": {
                "fixApplied": {"type": "string"},
                "verification": {"type": "string"},
            },
        },
        "correctiveActions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action", "owner", "dueDate", "priority"],
                "properties": {
                    "action": {"type": "string", "minLength": 1},
                    "owner": {"type": ["string", "null"]},
                    "dueDate": {"type": ["string", "null"]},
                    "priority": {"type": "string", "enum": ["P0", "P1", "P2"]},
                },
            },
        },
        "evidence": {
            "type": "object",
            "additionalProperties": False,
            "required": ["serviceNowFieldsUsed", "transcriptUsed", "notes"],
            "properties": {
                "serviceNowFieldsUsed": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "transcriptUsed": {"type": "boolean"},
                "notes": {"type": "string"},
            },
        },
        "risks": {
            "type": "array",
            "items": {"type": "string"},
        },
        "similarIncidents": {
            "type": "object",
            "additionalProperties": False,
            "required": ["referenced", "summary"],
            "properties": {
                "referenced": {"type": "boolean"},
                "summary": {"type": "string"},
            },
        },
        "appendix": {
            "type": "object",
            "additionalProperties": False,
            "required": ["rawTranscriptIncluded", "truncation"],
            "properties": {
                "rawTranscriptIncluded": {"type": "boolean", "const": False},
                "truncation": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["applied", "maxChars"],
                    "properties": {
                        "applied": {"type": "boolean"},
                        "maxChars": {"type": "integer", "minimum": 0},
                    },
                },
            },
        },
    },
}


@dataclass
class RcaValidationError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


def validate_rca_schema(rca_json: dict[str, Any]) -> None:
    validator = Draft202012Validator(RCA_SCHEMA_V1)
    errors = sorted(validator.iter_errors(rca_json), key=lambda e: e.path)
    if errors:
        first = errors[0]
        path = ".".join(str(p) for p in first.path) or "<root>"
        raise RcaValidationError(f"RCA schema validation failed at {path}: {first.message}")

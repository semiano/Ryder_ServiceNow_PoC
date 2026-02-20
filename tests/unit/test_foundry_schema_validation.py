import pytest

from models.rca_schema import RcaValidationError, validate_rca_schema


def _valid_rca() -> dict:
    return {
        "schemaVersion": "1.0",
        "ticket": {
            "number": "INC0012345",
            "sys_id": "123e4567-e89b-42d3-a456-426614174000",
            "priority": "1",
            "closedAt": "2026-02-19T15:00:00Z",
        },
        "summary": {
            "title": "Database connection saturation",
            "executiveSummary": "Burst traffic exhausted pool connections.",
            "customerImpact": "Intermittent login failures.",
            "severity": "High",
        },
        "timeline": [
            {"timestamp": "2026-02-19T14:00:00Z", "event": "Alert fired", "source": "ticket"}
        ],
        "rootCause": {
            "statement": "Pool configuration too low",
            "category": "configuration",
            "confidence": 0.92,
        },
        "contributingFactors": ["Unexpected traffic spike"],
        "detection": {
            "howDetected": "Synthetic checks",
            "whyNotDetectedSooner": "Thresholds too high",
        },
        "resolution": {
            "fixApplied": "Increased pool size",
            "verification": "Error rate returned to baseline",
        },
        "correctiveActions": [
            {"action": "Tune autoscale", "owner": "Platform", "dueDate": None, "priority": "P1"}
        ],
        "evidence": {
            "serviceNowFieldsUsed": ["description", "close_notes"],
            "transcriptUsed": False,
            "notes": "Transcript unavailable",
        },
        "risks": ["Recurrence during promotions"],
        "similarIncidents": {
            "referenced": False,
            "summary": "No similar incidents were provided in input.",
        },
        "appendix": {
            "rawTranscriptIncluded": False,
            "truncation": {"applied": False, "maxChars": 120000},
        },
    }


def test_validate_rca_schema_accepts_valid_payload() -> None:
    validate_rca_schema(_valid_rca())


def test_validate_rca_schema_rejects_invalid_payload() -> None:
    payload = _valid_rca()
    del payload["rootCause"]
    with pytest.raises(RcaValidationError):
        validate_rca_schema(payload)

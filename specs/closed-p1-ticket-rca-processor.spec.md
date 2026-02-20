# Spec: Closed P1 Ticket RCA Processor

## 1) Document Control
- **Spec ID**: `rca-closed-p1-v1`
- **Version**: `1.0.0`
- **Date**: `2026-02-19`
- **Status**: `Approved for implementation`
- **Implementation Target**: Azure Functions (Python, Programming Model v2)
- **Single Source of Truth**: This document is normative. If code diverges, this spec wins.

## 2) Objective
Implement a single synchronous HTTP-triggered Azure Function that:
1. Accepts `{ ticketId, status }`.
2. Returns early (HTTP 200, `processed=false`) when status is not closed.
3. For closed status:
   - Fetches ServiceNow incident data.
   - Attempts best-effort Teams transcript retrieval via Microsoft Graph.
   - Calls Azure Foundry Agent endpoint to produce strict RCA JSON.
   - Persists results/metadata to Cosmos DB Table API.
   - Returns deterministic JSON with timings and correlation ID.

## 3) Scope and Non-Goals
### In scope
- One Azure Function App with one HTTP function endpoint.
- Observability via Application Insights.
- Secrets in Key Vault with Function Managed Identity access.
- Cosmos DB Table API persistence.
- Bicep deployment for all required Azure resources.

### Out of scope
- Durable Functions, queues, retries/orchestration workflows.
- VNET/private endpoints.
- UI/Flow Designer.
- ServiceNow KB retrieval implementation (design placeholders only).

## 4) Runtime and Architecture
### Runtime
- Azure Functions Python v2.
- Python version: `3.12`.
- Function host route: explicit `route="process-closed-ticket"`.
- Effective endpoint path: `POST /api/process-closed-ticket`.

### High-level flow
1. Generate `correlationId` (UUID v4) at request start.
2. Validate JSON body and fields.
3. Normalize status via: `normalizedStatus = status.strip().lower()`.
4. If `normalizedStatus != "closed"`: return deterministic non-processed response and stop.
5. Resolve `ticketKeyType` (`sys_id` or `number`) from `ticketId`.
6. Fetch incident from ServiceNow (terminal if fail).
7. Build canonical `ticketBodyText` from ordered fields.
8. Extract meeting reference heuristics from ticket text.
9. Attempt Graph transcript retrieval (best-effort; warning on failure).
10. Invoke Foundry endpoint once (terminal if fail).
11. Strictly validate Foundry RCA output schema v1.0 (terminal if invalid).
12. Persist entity to Cosmos Table (terminal if fail).
13. Return HTTP 200 `processed=true` envelope.

## 5) API Contract (Inbound)
### Request
- Method: `POST`
- Path: `/api/process-closed-ticket`
- Header: `Content-Type: application/json`
- Body schema:

```json
{
  "ticketId": "<string>",
  "status": "<string>"
}
```

### Validation rules
- If body is invalid JSON => HTTP 400.
- If `ticketId` missing/non-string/empty-after-trim => HTTP 400.
- If `status` missing/non-string/empty-after-trim => HTTP 400.
- Otherwise continue.

### Ticket key detection rule (MUST)
Treat as `sys_id` only if `ticketId` matches canonical GUID with hyphens, case-insensitive:

```regex
(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$
```

- Match => `ticketKeyType="sys_id"`
- No match => `ticketKeyType="number"`

### Non-closed response (MUST)
If `normalizedStatus != "closed"`, return HTTP 200:

```json
{
  "processed": false,
  "reason": "status_not_closed",
  "ticketId": "<original ticketId>",
  "normalizedStatus": "<normalized>",
  "correlationId": "<generated-guid>"
}
```

And MUST NOT call ServiceNow, Graph, Foundry, or Cosmos.

### Closed success response (MUST)
HTTP 200:

```json
{
  "processed": true,
  "ticketId": "<original>",
  "ticketKeyType": "number",
  "serviceNow": {
    "fetched": true,
    "sys_id": "<if known>",
    "number": "<if known>"
  },
  "transcript": {
    "attempted": true,
    "found": false,
    "source": "graph",
    "details": {
      "matchStrategy": "<string>",
      "graphTranscriptId": null,
      "graphMeetingId": null
    }
  },
  "rca": {
    "generated": true,
    "schemaVersion": "1.0",
    "tableKeys": {
      "partitionKey": "<ticketId>",
      "rowKey": "<iso-utc>"
    }
  },
  "cosmosTable": {
    "written": true
  },
  "correlationId": "<guid>",
  "timingMs": {
    "total": 0,
    "serviceNowFetch": 0,
    "graphFetch": 0,
    "foundryCall": 0,
    "cosmosWrite": 0
  }
}
```

### Error response envelope (MUST)
For processing failures after closed-path entry, return HTTP 500:

```json
{
  "processed": false,
  "reason": "error",
  "ticketId": "<ticketId>",
  "correlationId": "<guid>",
  "error": {
    "code": "<stable_error_code>",
    "message": "<safe message>"
  }
}
```

No secret values may appear in `message`.

## 6) Terminal vs Best-Effort Rules
### Terminal failures (must stop and return 500)
- ServiceNow fetch failed / record not found.
- Foundry endpoint call failed.
- Foundry response invalid JSON or schema-invalid.
- Cosmos Table write failed.

### Best-effort failures (must continue)
- Transcript extraction yields no meeting reference.
- Graph token acquisition/transcript retrieval errors.
- Missing transcript in Graph.

When transcript fails, continue with empty transcript and log warning.

## 7) Configuration and Secrets
## 7.1 Required app settings (env vars)
MUST define:
- `SERVICENOW_INSTANCE_URL` (e.g., `https://<instance>.service-now.com`)
- `SERVICENOW_AUTH_SCHEME` (default `Bearer`)
- `FOUNDRY_AGENT_ENDPOINT_URL`
- `COSMOS_TABLE_ENDPOINT`
- `COSMOS_TABLE_NAME` (default `RcaReports`)
- `LOG_LEVEL` (default `INFO`)
- `TRANSCRIPT_LOOKBACK_DAYS` (default `30`)
- `TRANSCRIPT_MAX_CHARS` (default `120000`)
- `RCA_SCHEMA_VERSION` (default `1.0`)
- `SERVICENOW_KB_ENABLED` (default `false`, placeholder)
- `SERVICENOW_KB_TABLE` (default `kb_knowledge`, placeholder)
- `GRAPH_FALLBACK_USER_ID` (optional; no hard requirement)

Additional required for selected Cosmos auth path:
- `COSMOS_TABLE_CONNECTION_STRING` (Key Vault reference).

### 7.2 Key Vault secret names (fixed)
MUST exist with exact names:
- `SERVICENOW_API_TOKEN`
- `GRAPH_TENANT_ID`
- `GRAPH_CLIENT_ID`
- `GRAPH_CLIENT_SECRET`
- `FOUNDRY_AGENT_API_KEY`

Additional required by selected Cosmos auth path:
- `COSMOS_TABLE_CONNECTION_STRING`

### 7.3 Key Vault reference usage
Function app settings MUST reference secrets via Key Vault references where supported:

```text
@Microsoft.KeyVault(SecretUri=https://<kv-name>.vault.azure.net/secrets/<secret-name>/)
```

## 8) Authentication and Authorization
### Function -> Key Vault
- Function App uses **System Assigned Managed Identity**.
- MI granted Key Vault RBAC role: `Key Vault Secrets User` at vault scope.

### Graph auth
- OAuth2 client credentials flow using tenant/client ID/client secret from Key Vault.
- Do not assume one fixed user owns all transcripts.

### Cosmos auth path (explicit choice)
Use **Cosmos Table connection string from Key Vault** (not MI RBAC) due inconsistent Table API MI/RBAC support across SDK/runtime scenarios.

## 9) ServiceNow Integration
### 9.1 Auth header format
Default request header:

```text
Authorization: <SERVICENOW_AUTH_SCHEME> <SERVICENOW_API_TOKEN>
```

Default scheme value is `Bearer`.

### 9.2 Endpoints and query patterns
Base: `{SERVICENOW_INSTANCE_URL}`

Common fields (`incidentFields`):
`sys_id,number,short_description,description,close_notes,state,priority,severity,assignment_group,opened_at,closed_at,caller_id,cmdb_ci`

When `ticketKeyType=sys_id`:

```text
GET {base}/api/now/table/incident/{sys_id}?sysparm_fields={incidentFields}&sysparm_display_value=true
```

When `ticketKeyType=number`:

```text
GET {base}/api/now/table/incident?sysparm_query=number={urlencodedTicketNumber}&sysparm_limit=1&sysparm_fields={incidentFields}&sysparm_display_value=true
```

Journal notes retrieval (best attempt; if unauthorized/unavailable continue with empty notes):

```text
GET {base}/api/now/table/sys_journal_field?sysparm_query=element_id={sys_id}^elementINwork_notes,comments&sysparm_fields=element,value,sys_created_on,sys_created_by&sysparm_orderbyDESCsys_created_on&sysparm_limit=50
```

### 9.3 Required ServiceNow extraction output
Normalized object MUST contain at least:
- `sys_id`
- `number`
- `short_description`
- `description`
- `close_notes`
- `state`
- `priority`
- `severity` (nullable)
- `assignment_group` (stringified display or value)
- `opened_at`
- `closed_at`
- `caller_id` (nullable)
- `cmdb_ci` (nullable)
- `work_notes` (list, latest-first when available)
- `comments` (list, latest-first when available)

### 9.4 ticketBodyText construction (MUST deterministic)
Construct `ticketBodyText` exactly in this order, each section prefixed with label and newline-delimited:
1. `Ticket Number`
2. `Ticket SysId`
3. `Short Description`
4. `Description`
5. `Close Notes`
6. `State`
7. `Priority`
8. `Severity`
9. `Assignment Group`
10. `Opened At`
11. `Closed At`
12. `Caller`
13. `CI`
14. `Latest Work Notes` (up to 10 entries, newest to oldest)
15. `Latest Comments` (up to 10 entries, newest to oldest)

Nulls render as empty string. Preserve note order deterministically.

## 10) Transcript Discovery and Graph Retrieval (Best-Effort)
### 10.1 Source text scan fields
Search in order:
1. `close_notes`
2. `description`
3. `short_description`
4. concatenated `work_notes`
5. concatenated `comments`

### 10.2 Required regex heuristics
Implement at least these regexes:

1) Teams join URL:
```regex
(?i)https://teams\.microsoft\.com/l/meetup-join/[\w%\-\.\/:?=&+]+ 
```

2) Meeting thread token candidate:
```regex
(?i)19:[A-Za-z0-9_\-\.]+@thread\.v2
```

3) Generic meeting id-like GUID token:
```regex
(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b
```

### 10.3 Normalized meetingReference object

```json
{
  "meetingJoinUrl": "<string|null>",
  "meetingIdCandidate": "<string|null>",
  "meetingSubjectCandidate": "<string|null>",
  "foundInField": "close_notes|description|short_description|work_notes|comments|null"
}
```

`meetingSubjectCandidate` may be extracted from patterns like `Subject: ...` or `Meeting: ...` if present.

### 10.4 Graph lookup strategy
1. Acquire app token:
   - `POST https://login.microsoftonline.com/{tenantId}/oauth2/v2.0/token`
   - `scope=https://graph.microsoft.com/.default`
   - grant_type `client_credentials`
2. If `meetingJoinUrl` found:
   - Attempt meeting resolution using available Graph meeting lookup patterns.
   - If user-scoped endpoint required, discover user as:
     1) parse organizer/user hints from join URL/text,
     2) if unresolved, use `GRAPH_FALLBACK_USER_ID` when configured,
     3) else abort transcript lookup as not found.
3. If `meetingIdCandidate` found but no join URL:
   - Attempt direct meeting/transcript resolution where API supports ID.
4. If transcript list available, pick latest transcript by created timestamp.
5. Retrieve transcript content.
6. Truncate deterministically to `TRANSCRIPT_MAX_CHARS`:
   - keep first N chars only.
   - set `appendix.truncation.applied=true` when truncated.

### 10.5 Graph permissions section (implementation minimum)
App registration permissions (application permissions) should include transcript/meeting read scopes required by chosen Graph endpoints, commonly including:
- `OnlineMeetings.Read.All`
- `OnlineMeetingTranscript.Read.All`
- `User.Read.All` (if user discovery is implemented)

Actual required set depends on final endpoints used; implementation must document exact final permissions.

## 11) Foundry RCA Generation
### 11.1 Call requirements
- Single HTTP call to `FOUNDRY_AGENT_ENDPOINT_URL`.
- Auth header uses `FOUNDRY_AGENT_API_KEY` secret unless AAD auth explicitly implemented.
- Determinism: `temperature=0`, `top_p=1` (or equivalent).
- Request strict JSON output only (no prose wrapper).

### 11.2 Foundry input payload
MUST include:

```json
{
  "correlationId": "<guid>",
  "serviceNowTicket": {
    "record": {"...": "..."},
    "ticketBodyText": "<string>"
  },
  "transcriptText": "<string possibly empty>",
  "transcriptMetadata": {
    "meetingReference": {"...": "..."},
    "matchStrategy": "<string>",
    "graphMeetingId": "<string|null>",
    "graphTranscriptId": "<string|null>",
    "found": true
  },
  "similarTickets": []
}
```

### 11.3 RCA schema v1.0 (normative)

```json
{
  "type": "object",
  "additionalProperties": false,
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
    "appendix"
  ],
  "properties": {
    "schemaVersion": {"type": "string", "const": "1.0"},
    "ticket": {
      "type": "object",
      "additionalProperties": false,
      "required": ["number", "sys_id", "priority", "closedAt"],
      "properties": {
        "number": {"type": ["string", "null"]},
        "sys_id": {"type": ["string", "null"]},
        "priority": {"type": ["string", "null"]},
        "closedAt": {"type": ["string", "null"]}
      }
    },
    "summary": {
      "type": "object",
      "additionalProperties": false,
      "required": ["title", "executiveSummary", "customerImpact", "severity"],
      "properties": {
        "title": {"type": "string", "minLength": 1},
        "executiveSummary": {"type": "string", "minLength": 1},
        "customerImpact": {"type": "string", "minLength": 1},
        "severity": {"type": "string", "minLength": 1}
      }
    },
    "timeline": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["timestamp", "event", "source"],
        "properties": {
          "timestamp": {"type": ["string", "null"]},
          "event": {"type": "string", "minLength": 1},
          "source": {"type": "string", "enum": ["ticket", "transcript", "inferred"]}
        }
      }
    },
    "rootCause": {
      "type": "object",
      "additionalProperties": false,
      "required": ["statement", "category", "confidence"],
      "properties": {
        "statement": {"type": "string", "minLength": 1},
        "category": {"type": "string", "minLength": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1}
      }
    },
    "contributingFactors": {
      "type": "array",
      "items": {"type": "string"}
    },
    "detection": {
      "type": "object",
      "additionalProperties": false,
      "required": ["howDetected", "whyNotDetectedSooner"],
      "properties": {
        "howDetected": {"type": "string"},
        "whyNotDetectedSooner": {"type": "string"}
      }
    },
    "resolution": {
      "type": "object",
      "additionalProperties": false,
      "required": ["fixApplied", "verification"],
      "properties": {
        "fixApplied": {"type": "string"},
        "verification": {"type": "string"}
      }
    },
    "correctiveActions": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["action", "owner", "dueDate", "priority"],
        "properties": {
          "action": {"type": "string", "minLength": 1},
          "owner": {"type": ["string", "null"]},
          "dueDate": {"type": ["string", "null"]},
          "priority": {"type": "string", "enum": ["P0", "P1", "P2"]}
        }
      }
    },
    "evidence": {
      "type": "object",
      "additionalProperties": false,
      "required": ["serviceNowFieldsUsed", "transcriptUsed", "notes"],
      "properties": {
        "serviceNowFieldsUsed": {
          "type": "array",
          "items": {"type": "string"}
        },
        "transcriptUsed": {"type": "boolean"},
        "notes": {"type": "string"}
      }
    },
    "risks": {
      "type": "array",
      "items": {"type": "string"}
    },
    "appendix": {
      "type": "object",
      "additionalProperties": false,
      "required": ["rawTranscriptIncluded", "truncation"],
      "properties": {
        "rawTranscriptIncluded": {"type": "boolean", "const": false},
        "truncation": {
          "type": "object",
          "additionalProperties": false,
          "required": ["applied", "maxChars"],
          "properties": {
            "applied": {"type": "boolean"},
            "maxChars": {"type": "integer", "minimum": 1}
          }
        }
      }
    }
  }
}
```

If Foundry output fails parse or schema validation => terminal error `FOUNDRY_SCHEMA_INVALID`.

## 12) Persistence: Cosmos DB Table API
### 12.1 Table
- Name from `COSMOS_TABLE_NAME`, default `RcaReports`.

### 12.2 Keys
- `PartitionKey = ticketId` (original inbound string, unmodified).
- `RowKey = processingStartUtc` formatted ISO8601 UTC with milliseconds, e.g. `2026-02-19T16:33:21.123Z`.

### 12.3 Entity schema (required)
- `PartitionKey` (string)
- `RowKey` (string)
- `CorrelationId` (string)
- `TicketIdOriginal` (string)
- `TicketKeyType` (string: `number|sys_id`)
- `ServiceNowSysId` (string nullable)
- `ServiceNowNumber` (string nullable)
- `StatusNormalized` (string)
- `ProcessedAtUtc` (string ISO)
- `TranscriptFound` (bool)
- `TranscriptMatchStrategy` (string nullable)
- `GraphMeetingId` (string nullable)
- `GraphTranscriptId` (string nullable)
- `TranscriptChars` (int)
- `FoundryModel` (string nullable, default `unknown`)
- `RCAJson` (string serialized JSON)
- `RCASummaryTitle` (string)
- `RCARootCauseCategory` (string)
- `RCARootCauseConfidence` (double)
- `TimingTotalMs` (int)
- `ErrorCode` (string nullable)
- `ErrorMessage` (string nullable)

TTL is not implemented by default.

## 13) Observability Requirements
### 13.1 Logging
- Every log line includes: `correlationId`, `ticketId`.
- Levels:
  - `INFO`: normal milestones
  - `WARNING`: transcript best-effort failures
  - `ERROR`: terminal failures

### 13.2 Required milestone logs with timing
- request received
- status not closed early exit
- ServiceNow fetch start/end
- transcript extraction result
- Graph fetch attempt result
- Foundry call start/end
- Cosmos write start/end

### 13.3 App Insights expectations
- HTTP outbound calls appear as dependencies (ServiceNow, Graph, Foundry, Cosmos when applicable).
- Operation name standardized to `ProcessClosedTicket`.
- Custom dimensions on relevant traces/requests/dependencies:
  - `ticketId`
  - `serviceNowSysId`
  - `meetingReferenceFound` (bool)
  - `transcriptFound` (bool)

## 14) Security Requirements
- Never log secret values or full auth headers.
- HTTPS only enabled on Function App.
- Minimum TLS set to `1.2`.
- Input validation behavior:
  - malformed/invalid request => HTTP 400
  - status not closed => HTTP 200
  - closed-path processing errors => HTTP 500

## 15) Bicep Infrastructure Specification
### 15.1 Deployment scope
- Resource Group scope only.

### 15.2 Required resources
1. Storage Account (Function storage)
2. App Service Plan (`Y1` consumption, Linux)
3. Function App (Linux, Python 3.12, System Assigned MI)
4. Log Analytics Workspace
5. Application Insights (workspace-based)
6. Key Vault (RBAC-enabled)
7. Cosmos DB account (Table API)
8. Cosmos Table resource creation (`RcaReports` by default)
9. Role assignment: Function MI -> Key Vault `Key Vault Secrets User`

### 15.3 Parameters (required)
- `location`
- `appNamePrefix`
- `environment` (`dev|prod`)
- `tags` (object)
- Optional: defaults for settings values above

### 15.4 Outputs (required)
- Function default hostname
- Key Vault name
- Cosmos endpoint
- Application Insights connection string (or reference)

### 15.5 Function app settings in Bicep
Must set all required env vars in section 7.
Secrets referenced via Key Vault references where possible:
- Graph secrets
- ServiceNow token
- Foundry API key
- Cosmos connection string

### 15.6 Cosmos access decision
Use connection string from Key Vault (`COSMOS_TABLE_CONNECTION_STRING`), not MI RBAC.

## 16) Repository and Code Structure
Implement with this module layout:

```text
src/
  function_app.py
  services/
    servicenow_client.py
    graph_client.py
    foundry_client.py
    cosmos_table_repo.py
  models/
    rca_schema.py
  utils/
    logging.py
tests/
  unit/
    test_ticket_id_detection.py
    test_status_early_exit.py
    test_servicenow_url_building.py
    test_transcript_extraction.py
    test_foundry_schema_validation.py
  integration/
    test_process_closed_ticket_flow.py
```

### Implementation notes
- Use typed models (`pydantic` or `jsonschema` validation wrapper) for RCA schema validation.
- Keep external clients isolated for easy mocking.
- Add integration hooks using env flags, e.g. `ENABLE_LIVE_EXTERNALS=false` by default.

## 17) Deterministic Error Codes
Use stable `error.code` values:
- `BAD_REQUEST_INVALID_JSON`
- `BAD_REQUEST_VALIDATION`
- `SERVICENOW_FETCH_FAILED`
- `SERVICENOW_NOT_FOUND`
- `GRAPH_TOKEN_FAILED` (warning path only unless no other error)
- `GRAPH_TRANSCRIPT_FAILED` (warning path only unless no other error)
- `FOUNDRY_CALL_FAILED`
- `FOUNDRY_SCHEMA_INVALID`
- `COSMOS_WRITE_FAILED`
- `UNHANDLED_EXCEPTION`

Only terminal errors return HTTP 500 envelope.

## 18) Test Requirements
### Unit tests (mandatory)
1. GUID detection:
   - valid canonical GUID => `sys_id`
   - non-GUID (`INC0012345`) => `number`
2. status early exit:
   - any non-closed casing/spacing => processed false and no downstream calls
3. ServiceNow URL building:
   - number path vs sys_id path exact query behavior
4. transcript extraction heuristics:
   - at least 3 cases: join URL found, thread token found, none found
5. Foundry schema validation:
   - valid JSON accepted
   - invalid/missing required fields rejected

### Integration-test hooks
- Must run with mocks/stubs; no live ServiceNow/Graph required.
- Optional live mode behind env flags.

## 19) Acceptance Criteria (explicit, testable)
1. **Early exit correctness**: `POST` with `status != closed` returns HTTP 200 with `processed=false` and no calls to ServiceNow, Graph, Foundry, or Cosmos (assert via mock call counts = 0).
2. **Closed happy path**: closed request fetches ServiceNow incident, builds `ticketBodyText`, attempts transcript lookup, calls Foundry exactly once, writes exactly one Cosmos row, returns `processed=true` with table keys and timings.
3. **Transcript best-effort**: Graph/transcript failures do not fail processing; response remains `processed=true`, Cosmos row has `TranscriptFound=false`, warning logs present.
4. **Foundry strictness**: invalid Foundry output (non-JSON or schema-invalid) returns HTTP 500 and performs no Cosmos write.
5. **Correlation logging**: all logs include both `correlationId` and `ticketId`.

## 20) Local Development Runbook
### Prerequisites
- Python `3.12`
- Azure Functions Core Tools v4
- Azure CLI

### Local setup
1. Create virtual environment and install dependencies.
2. Set local settings (`local.settings.json`) with required env vars.
3. For local secret testing, either:
   - use direct non-secret test values in local settings, or
   - use Key Vault and developer identity retrieval logic.
4. Run:

```bash
func start
```

### Sample request

```bash
curl -X POST http://localhost:7071/api/process-closed-ticket \
  -H "Content-Type: application/json" \
  -d '{"ticketId":"INC0012345","status":"closed"}'
```

## 21) Deployment Runbook (Bicep)
### Build/validate

```bash
az bicep build --file infra/main.bicep
```

### Deploy to RG

```bash
az deployment group create \
  --resource-group <rg-name> \
  --template-file infra/main.bicep \
  --parameters location=<location> appNamePrefix=<prefix> environment=<dev|prod>
```

### Post-deploy
1. Populate required Key Vault secrets.
2. Confirm Function app settings resolve Key Vault references.
3. Publish Function code.
4. Smoke test non-closed and closed paths.

## 22) Permissions Needed
### Azure
- Permission to deploy RG resources listed above.
- Permission to create role assignment on Key Vault scope.

### Microsoft Graph app registration
- Admin-consented application permissions needed for chosen transcript/meeting endpoints.
- Typical minimum set:
  - `OnlineMeetings.Read.All`
  - `OnlineMeetingTranscript.Read.All`
  - `User.Read.All` (if user discovery is used)

### ServiceNow
- API token with incident table read access.
- Access to journal data (`sys_journal_field`) if work notes/comments required.

## 23) Future Extension Placeholder (Not Implemented)
Design code interfaces now for future KB retrieval:
- `KnowledgeBaseProvider` interface with method `get_related_kb_articles(ticket) -> list`.
- Config gates:
  - `SERVICENOW_KB_ENABLED`
  - `SERVICENOW_KB_TABLE`
- Current behavior: feature disabled by default and returns empty list.

## 24) Done Definition
Implementation is complete only when:
- All acceptance criteria pass.
- Unit tests listed in section 18 exist and pass.
- Endpoint behavior and response shapes match this spec.
- Bicep deploys all required resources and outputs.
- Security and observability requirements are met.

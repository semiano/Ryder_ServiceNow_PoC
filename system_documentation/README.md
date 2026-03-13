# Ryder ServiceNow RCA Processor — Project README

## 1) Purpose and Business Context
This project implements a production-oriented **Closed Ticket RCA Processor** for critical incidents (P1/high-severity) to accelerate post-incident review.

When an incident closes, the function assembles operational evidence (ServiceNow record + optional bridge transcript), asks a Foundry agent to generate a structured RCA, validates strict schema compliance, and stores a durable artifact for engineering and leadership review.

### Why this matters to the business
- Reduces time-to-first-RCA for major incidents.
- Improves consistency and quality of incident review prep.
- Preserves an auditable, structured RCA history in Cosmos Table API.
- Supports faster corrective-action planning and repeat-incident detection.

## 2) Scope and Product Boundaries
### In scope
- One synchronous HTTP Azure Function endpoint: `POST /api/process-closed-ticket`.
- Closed-ticket processing pipeline with strict RCA schema validation.
- Best-effort transcript enrichment via Microsoft Graph.
- Cosmos Table API persistence of RCA + processing metadata.
- Azure-native observability and managed identity patterns.

### Out of scope (current phase)
- UI, workflow/orchestration, queues, and durable retries.
- Final human approval workflow for RCA publication.
- Autonomous remediation execution.

## 3) Architecture Overview
![Ryder RCA Architecture](Ryder%20RCA%20system.png)

### End-to-end flow
1. Request arrives with `{ ticketId, status }`.
2. If status is not closed, return deterministic `processed=false` early exit.
3. Retrieve incident + notes from ServiceNow.
4. Attempt transcript discovery/retrieval via Graph (best effort).
5. Send structured evidence to Foundry agent endpoint.
6. Validate returned RCA against strict JSON schema.
7. Persist RCA and metadata to Cosmos Table.
8. Return deterministic response envelope with timings and correlation ID.

## 4) AI Agent Contract and Behavior
The Foundry agent is intentionally configured as an **enterprise reliability analysis assistant** and constrained to:
- evidence-based reasoning,
- conservative inference,
- neutral, non-blaming language,
- strict JSON-only output.

This aligns with business usage: the output is a **first-pass RCA for review meetings**, not final adjudication.

### Design implications from prompt policy
- Transcript evidence is treated as high-confidence timeline input when present.
- Missing or conflicting evidence must lower confidence and be explicitly acknowledged.
- Corrective actions must be specific and prioritized (`P0|P1|P2`).
- Similar-incident framing is supported via `similarIncidents` in the output schema.

Prompt source of truth:
- [foundry-agent-system-prompt.md](foundry-agent-system-prompt.md)

## 5) Core Technical Components
- **Azure Function App (Python v2)**: request handling and orchestration.
- **ServiceNow client**: incident and journal field retrieval.
- **Graph client**: meeting reference extraction + transcript best-effort retrieval.
- **Foundry client**: RBAC-authenticated call to agent endpoint.
- **Schema validator**: strict RCA v1.0 contract enforcement.
- **Cosmos Table repository**: durable storage for RCA artifacts and processing metadata.
- **Application Insights**: traces, dependencies, and operational diagnostics.

## 6) Security and Identity Model
- Function uses Managed Identity.
- Foundry uses RBAC (`DefaultAzureCredential`, token scope `https://ai.azure.com/.default`).
- Cosmos Table supports AAD mode or connection string mode (environment-driven).
- Sensitive values are provided through settings/secrets; do not log tokens/secrets.

Foundry RBAC operations guide:
- [foundry-rbac-setup.md](foundry-rbac-setup.md)

## 7) Data Contracts and Validation
### Inbound contract
```json
{ "ticketId": "INC0000015", "status": "closed" }
```

### Key output behavior
- Non-closed status: `processed=false`, no downstream calls.
- Closed + success: `processed=true`, includes serviceNow/transcript/rca/cosmos/timing fields.
- Closed + terminal failure: `processed=false`, `reason=error`, stable error code.

### Strict schema enforcement
The RCA output is validated server-side and must include required sections such as:
- `summary`
- `timeline`
- `rootCause`
- `correctiveActions`
- `evidence`
- `similarIncidents`
- `appendix`

## 8) Operations, Reliability, and Failure Policy
### Terminal failures (return error)
- ServiceNow fetch failure/not found
- Foundry call failure
- Foundry schema invalid output
- Cosmos write failure

### Best-effort failures (continue)
- Transcript discovery/retrieval not found or failed

This policy preserves throughput while maintaining strictness on RCA quality and persistence.

## 9) Observability and Governance
- Correlation-first logging (`correlationId`, `ticketId`).
- Dependency timing visibility (ServiceNow/Graph/Foundry/Cosmos).
- Deterministic error codes for triage dashboards and alerting.

### Governance posture
- Human-in-the-loop remains mandatory for final RCA acceptance.
- Output is machine-generated draft analysis bounded by evidence.
- Schema strictness acts as a quality gate before persistence.

## 10) Deployment and Environments
Infrastructure is defined in Bicep and includes Function hosting, App Insights, Key Vault, and Cosmos Table API resources.

Operational references:
- Project implementation spec: [../specs/closed-p1-ticket-rca-processor.spec.md](../specs/closed-p1-ticket-rca-processor.spec.md)
- Foundry prompt policy: [foundry-agent-system-prompt.md](foundry-agent-system-prompt.md)
- Foundry RBAC setup: [foundry-rbac-setup.md](foundry-rbac-setup.md)

## 11) How this fits Incident Management Lifecycle
This solution sits between **incident closure** and **formal PIR/RCA review**:
- Input event: ticket closure in ServiceNow.
- Processing: evidence aggregation + AI-structured analysis.
- Output artifact: structured RCA JSON for engineers/managers.
- Business outcome: faster, more consistent post-incident readiness.

## 12) Future Growth Path
- Similar-ticket clustering and richer cross-record relevance scoring.
- Action ownership integration with engineering planning systems.
- RCA quality scoring and drift monitoring across incident portfolios.
- Optional workflow trigger at ticket close event instead of manual API invocation.

---

## Quick Links
- Project quickstart: [../README.md](../README.md)
- Architecture diagram: [Ryder RCA system.png](Ryder%20RCA%20system.png)
- Full implementation spec: [../specs/closed-p1-ticket-rca-processor.spec.md](../specs/closed-p1-ticket-rca-processor.spec.md)
- Foundry system prompt: [foundry-agent-system-prompt.md](foundry-agent-system-prompt.md)
- Foundry + Cosmos RBAC guide: [foundry-rbac-setup.md](foundry-rbac-setup.md)
- Container deployment runbook: [containerized-function-runbook.md](containerized-function-runbook.md)

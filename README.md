# Ryder ServiceNow RCA Processor

Azure Function that processes closed ServiceNow incidents, generates a structured RCA with Azure Foundry, validates schema, and persists results to Cosmos Table API.

## System Diagram
![Ryder RCA System Diagram](system_documentation/Ryder%20RCA%20system.png)

## Business Overview
This system is designed to accelerate post-incident learning for closed P1/critical incidents by producing a consistent, structured RCA artifact within minutes of ticket closure.

From the approved spec, the function provides a deterministic closed-ticket workflow: it accepts `{ ticketId, status }`, exits early when not closed, and for closed incidents it gathers ServiceNow evidence, attempts transcript enrichment, generates RCA JSON, validates schema, and stores a durable record in Cosmos Table API.

From the Foundry agent prompt, the RCA is intentionally a **first-pass analysis for human review** (not final adjudication). The model is constrained to be evidence-based, neutral, and non-speculative, with explicit confidence scoring and clear separation of fact vs inference.

### Why this matters
- Reduces time-to-first-RCA for engineering and operations teams.
- Improves consistency of incident review artifacts across teams.
- Creates an auditable incident knowledge trail for leadership and reliability programs.
- Supports higher-quality PIR meetings by front-loading timeline, root-cause hypotheses, and prioritized corrective actions.

### Operating boundaries
- **Terminal failures** (ServiceNow fetch, Foundry failure/schema invalid, Cosmos write) return deterministic error envelopes.
- **Best-effort failures** (transcript retrieval) do not block RCA generation.
- Output is strict JSON for downstream reliability workflows and governance controls.

## Current Status
- Closed-ticket flow validated in cloud on `ryder-rca-dev-func`.
- Cosmos auth finalized for cloud using Managed Identity + AAD.
- Container deployment path is operational and documented.

## Repository Structure
- `src/` application code and service clients.
- `tests/` unit and integration tests.
- `infra/` Bicep template and parameter file.
- `system_documentation/` architecture, runbooks, and RBAC setup.
- `scripts/` deployment scripts.

## Local Setup
1. Create and activate venv, then install dependencies:
   - `python -m venv .venv`
   - `.\.venv\Scripts\Activate.ps1`
   - `pip install -r requirements.txt`
2. Copy sample settings:
   - `Copy-Item local.settings.sample.json local.settings.json`
3. Update local `Values` in `local.settings.json`:
   - ServiceNow values (`SERVICENOW_*`)
   - Foundry endpoint (`FOUNDRY_AGENT_ENDPOINT_URL`)
   - Cosmos (`COSMOS_TABLE_ENDPOINT`, `COSMOS_TABLE_AUTH_MODE`, optional connection string)
   - Graph credentials (`GRAPH_*`)

Recommended auth mode defaults:
- Local + Azure user identity: `COSMOS_TABLE_AUTH_MODE=aad`
- Cloud (Function App MI): `COSMOS_TABLE_AUTH_MODE=aad`
- Local fallback with key: `COSMOS_TABLE_AUTH_MODE=connection_string`

## Run and Test
- Start function host: `func start --port 7071`
- Run tests: `python -m pytest -q`
- Local invoke example:
  - `Invoke-RestMethod -Uri 'http://localhost:7071/api/process-closed-ticket' -Method Post -ContentType 'application/json' -Body '{"ticketId":"INC0000015","status":"closed"}'`

## Cloud Deployment
- Container runbook: `system_documentation/containerized-function-runbook.md`
- Foundry + Cosmos RBAC: `system_documentation/foundry-rbac-setup.md`

## GitHub Publish Checklist
1. Confirm ignored files are not staged (`local.settings.json`, `.venv/`, `.deploy/`, Azurite files).
2. Ensure no real secrets remain in tracked files.
3. Initialize and push:
   - `git init`
   - `git add .`
   - `git commit -m "Initial commit: Ryder ServiceNow RCA processor"`
   - `git branch -M main`
   - `git remote add origin https://github.com/semiano/Ryder_ServiceNow_PoC.git`
   - `git push -u origin main`

If a remote already exists:
- `git remote set-url origin https://github.com/semiano/Ryder_ServiceNow_PoC.git`

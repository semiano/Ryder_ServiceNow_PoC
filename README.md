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
- Deployment baseline validated in `westus3` on Elastic Premium plan `ryder-rca-dev-ep1-plan`.

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

### Verified cloud baseline (March 2026)
- Resource group: `ryder-rca-dev-rg-westus3`
- Function App: `ryder-rca-dev-func`
- Function plan: `ryder-rca-dev-ep1-plan` (EP1 / Elastic Premium)
- Cosmos account: `ryder-rca-dev-cosmos` with `disableLocalAuth=true`
- Runtime auth mode: `COSMOS_TABLE_AUTH_MODE=aad`

### RBAC and access checks from local machine
Use these checks after deployment to verify permissions:

```powershell
$rg = 'ryder-rca-dev-rg-westus3'
$app = 'ryder-rca-dev-func'
$kv = 'ryderrcadevkve7e4nj4vs5k'
$foundryName = 'ryder-multi-agent-demo-resource'

$userObjectId = az ad signed-in-user show --query id -o tsv
$funcPrincipalId = az functionapp identity show -g $rg -n $app --query principalId -o tsv
$foundryId = az resource list --name $foundryName --query "[0].id" -o tsv
$kvId = az keyvault show -g $rg -n $kv --query id -o tsv

az role assignment list --scope $foundryId --query "[?roleDefinitionName=='Cognitive Services User' && (principalId=='$userObjectId' || principalId=='$funcPrincipalId')].{principalId:principalId,principalType:principalType}" -o table
az role assignment list --scope $kvId --query "[?roleDefinitionName=='Key Vault Secrets User' && (principalId=='$userObjectId' || principalId=='$funcPrincipalId')].{principalId:principalId,principalType:principalType}" -o table
az cosmosdb sql role assignment list --account-name ryder-rca-dev-cosmos --resource-group $rg --query "[].{principalId:principalId,roleDefinitionId:roleDefinitionId}" -o table
```

Foundry auth probe:

```powershell
$endpoint = az functionapp config appsettings list -g $rg -n $app --query "[?name=='FOUNDRY_AGENT_ENDPOINT_URL'].value | [0]" -o tsv
$token = az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv
Invoke-WebRequest -Method Post -Uri $endpoint -Headers @{ Authorization = "Bearer $token"; 'Content-Type' = 'application/json' } -Body '{"input":[{"role":"user","content":"ping"}]}'
```

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

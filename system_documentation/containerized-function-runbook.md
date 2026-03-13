# Containerized Function App Runbook

This runbook deploys this Azure Function as a **custom Linux container** to bypass zip/run-from-package deployment issues.

## Why use this path
- Avoids package deployment dependencies that rely on host storage key access.
- Provides deterministic runtime image content.
- Enables tighter release control via ACR image tags.

## Prerequisites
- Azure CLI logged in (`az login`).
- Permission to create/update resources in target resource group.
- Quota availability for **Elastic Premium (EP1)** in the target region.
- Permission to assign RBAC on Key Vault, Cosmos DB account, and Foundry account.

## Files added for this path
- `Dockerfile`
- `.dockerignore`
- `scripts/deploy-container-function.ps1`

## What the script does
`scripts/deploy-container-function.ps1` performs:
1. Ensures ACR exists.
2. Builds image in ACR (`az acr build`).
3. Ensures EP1 Linux plan exists.
4. Creates/updates a separate containerized Function App when allowed by storage policy.
5. Falls back to deploying container image on source Function App when shared-key storage is disallowed.
5. Assigns managed identity + `AcrPull`.
6. Configures identity-based `AzureWebJobsStorage__*` settings.
7. Enables managed identity pulls from ACR (`acrUseManagedIdentityCreds=true`).
8. Copies non-storage app settings from source app when deploying to a separate app.
9. Restarts app and syncs triggers.

## Run command
From repo root:

```powershell
.\scripts\deploy-container-function.ps1 \
  -ResourceGroup ryder-rca-dev-rg-swedencentral \
  -Location swedencentral \
  -SourceFunctionAppName ryder-rca-dev-func \
  -ContainerFunctionAppName ryder-rca-dev-func-cnt \
  -ContainerPlanName ryder-rca-dev-ep1-plan \
  -ImageName rca-processor
```

If your subscription enforces `allowSharedKeyAccess=false` on Storage Accounts, the script automatically uses `-SourceFunctionAppName` as the deployment target for the container image.

## Post-deploy validation
1. List functions:
```powershell
az functionapp function list --name ryder-rca-dev-func --resource-group ryder-rca-dev-rg-swedencentral --query "[].name" -o tsv
```

2. Get function key and test non-closed request:
```powershell
$funcName = az functionapp function list --name ryder-rca-dev-func --resource-group ryder-rca-dev-rg-swedencentral --query "[0].name" -o tsv
$shortFuncName = ($funcName -split '/')[1]
$key = az functionapp function keys list --name ryder-rca-dev-func --resource-group ryder-rca-dev-rg-swedencentral --function-name $shortFuncName --query default -o tsv
$body = '{"ticketId":"INC0000015","status":"in_progress"}'
Invoke-RestMethod -Uri "https://ryder-rca-dev-func.azurewebsites.net/api/process-closed-ticket?code=$key" -Method Post -ContentType 'application/json' -Body $body
```

## Required bootstrap after fresh redeploy (important)
After deleting/recreating the resource group, run these steps before testing `status=closed`.

1. Reseed ServiceNow token secret in Key Vault:
```powershell
$rg = 'ryder-rca-dev-rg-swedencentral'
$kv = az resource list -g $rg --resource-type Microsoft.KeyVault/vaults --query "[0].name" -o tsv
$snToken = (Get-Content local.settings.json -Raw | ConvertFrom-Json).Values.SERVICENOW_API_TOKEN
az keyvault secret set --vault-name $kv --name SERVICENOW-API-TOKEN --value $snToken -o none
```

2. Ensure Cosmos Table exists:
```powershell
az cosmosdb table create --account-name ryder-rca-dev-cosmos --resource-group ryder-rca-dev-rg-swedencentral --name RcaReports
```

3. Ensure Function MI has Cosmos data role:
```powershell
$rg = 'ryder-rca-dev-rg-swedencentral'
$app = 'ryder-rca-dev-func'
$principalId = az functionapp identity show -g $rg -n $app --query principalId -o tsv
az cosmosdb sql role assignment create --account-name ryder-rca-dev-cosmos --resource-group $rg --scope '/' --principal-id $principalId --role-definition-id 00000000-0000-0000-0000-000000000002
```

4. Ensure Function MI can pull ACR image:
```powershell
$rg = 'ryder-rca-dev-rg-swedencentral'
$app = 'ryder-rca-dev-func'
$acr = 'ryderrcadevfunccntacr'
$principalId = az functionapp identity show -g $rg -n $app --query principalId -o tsv
$acrId = az acr show -g $rg -n $acr --query id -o tsv
az role assignment create --assignee-object-id $principalId --assignee-principal-type ServicePrincipal --role AcrPull --scope $acrId
$appId = az functionapp show -g $rg -n $app --query id -o tsv
az resource update --ids "$appId/config/web" --set properties.acrUseManagedIdentityCreds=true
```

5. Ensure app points to Cosmos Table endpoint (not documents endpoint):
```powershell
az functionapp config appsettings set --name ryder-rca-dev-func --resource-group ryder-rca-dev-rg-swedencentral --settings COSMOS_TABLE_ENDPOINT=https://ryder-rca-dev-cosmos.table.cosmos.azure.com:443/
```

6. Optional: grant Function MI Foundry access if endpoint is in a different resource group/subscription:
```powershell
$rg = 'ryder-rca-dev-rg-swedencentral'
$app = 'ryder-rca-dev-func'
$principalId = az functionapp identity show -g $rg -n $app --query principalId -o tsv
$foundryId = az resource list --name 'ryder-multi-agent-demo-resource' --query "[0].id" -o tsv
az role assignment create --assignee-object-id $principalId --assignee-principal-type ServicePrincipal --role 'Cognitive Services User' --scope $foundryId
```

## Local machine validation (same backend resources)
1. In `local.settings.json`, use:
  - `COSMOS_TABLE_AUTH_MODE=aad`
  - `COSMOS_TABLE_ENDPOINT=https://ryder-rca-dev-cosmos.table.cosmos.azure.com:443/`
2. Grant your user Cosmos data role (one time):
```powershell
$userObjectId = az ad signed-in-user show --query id -o tsv
az cosmosdb sql role assignment create --account-name ryder-rca-dev-cosmos --resource-group ryder-rca-dev-rg-swedencentral --scope '/' --principal-id $userObjectId --role-definition-id 00000000-0000-0000-0000-000000000002
```
3. Start host and test:
```powershell
func host start --port 7071
Invoke-RestMethod -Uri 'http://localhost:7071/api/process-closed-ticket' -Method Post -ContentType 'application/json' -Body '{"ticketId":"INC0010002","status":"closed"}'
```

## Expected healthy responses
- Non-closed payload: `processed=false`, `reason=status_not_closed`.
- Closed payload: `processed=true`, `cosmosTable.written=true`, and `rca.generated=true`.

## Required Cosmos settings and RBAC (important)
For Cosmos Table accounts with local-key auth disabled, the Function App must use AAD and have Cosmos native RBAC.

1. Set app setting:
```powershell
az functionapp config appsettings set --name <function-app-name> --resource-group <function-app-rg> --settings COSMOS_TABLE_AUTH_MODE=aad
```

2. Assign Cosmos DB Built-in Data Contributor to Function MI:
```powershell
$principalId = az functionapp identity show --name <function-app-name> --resource-group <function-app-rg> --query principalId -o tsv
az cosmosdb sql role assignment create --account-name <cosmos-account-name> --resource-group <cosmos-rg> --scope '/' --principal-id $principalId --role-definition-id 00000000-0000-0000-0000-000000000002
```

Optionally assign the same role to your local user principal for local AAD verification:

```powershell
$userObjectId = az ad signed-in-user show --query id -o tsv
az cosmosdb sql role assignment create --account-name <cosmos-account-name> --resource-group <cosmos-rg> --scope '/' --principal-id $userObjectId --role-definition-id 00000000-0000-0000-0000-000000000002
```

3. Restart Function App:
```powershell
az functionapp restart --name <function-app-name> --resource-group <function-app-rg>
```

## Notes
- If EP1 creation fails with quota errors, request quota increase or deploy in a region with available EP capacity.
- If function indexing is delayed, wait 1–3 minutes and run trigger sync again.

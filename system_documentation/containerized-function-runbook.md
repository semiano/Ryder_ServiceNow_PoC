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

## Files added for this path
- `Dockerfile`
- `.dockerignore`
- `scripts/deploy-container-function.ps1`

## What the script does
`scripts/deploy-container-function.ps1` performs:
1. Ensures ACR exists.
2. Builds image in ACR (`az acr build`).
3. Ensures EP1 Linux plan exists.
4. Creates/updates a separate containerized Function App.
5. Assigns managed identity + `AcrPull`.
6. Configures identity-based `AzureWebJobsStorage__*` settings.
7. Copies non-storage app settings from source app.
8. Restarts app and syncs triggers.

## Run command
From repo root:

```powershell
.\scripts\deploy-container-function.ps1 \
  -ResourceGroup ryder-rca-dev-rg-westus3 \
  -Location westus3 \
  -SourceFunctionAppName ryder-rca-dev-func \
  -ContainerFunctionAppName ryder-rca-dev-func-cnt \
  -ContainerPlanName ryder-rca-dev-ep1-plan \
  -ImageName rca-processor
```

## Post-deploy validation
1. List functions:
```powershell
az functionapp function list --name ryder-rca-dev-func-cnt --resource-group ryder-rca-dev-rg-westus3 --query "[].name" -o tsv
```

2. Get function key and test non-closed request:
```powershell
$funcName = az functionapp function list --name ryder-rca-dev-func-cnt --resource-group ryder-rca-dev-rg-westus3 --query "[0].name" -o tsv
$key = az functionapp function keys list --name ryder-rca-dev-func-cnt --resource-group ryder-rca-dev-rg-westus3 --function-name $funcName --query default -o tsv
$body = '{"ticketId":"INC0000015","status":"in_progress"}'
Invoke-RestMethod -Uri "https://ryder-rca-dev-func-cnt.azurewebsites.net/api/process-closed-ticket?code=$key" -Method Post -ContentType 'application/json' -Body $body
```

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

3. Restart Function App:
```powershell
az functionapp restart --name <function-app-name> --resource-group <function-app-rg>
```

## Notes
- If EP1 creation fails with quota errors, request quota increase or deploy in a region with available EP capacity.
- If function indexing is delayed, wait 1–3 minutes and run trigger sync again.

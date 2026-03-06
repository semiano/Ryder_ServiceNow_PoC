# Foundry + Cosmos RBAC Setup (Azure CLI)

This project uses **RBAC authentication** for the Foundry endpoint in `src/services/foundry_client.py` via `DefaultAzureCredential`.

If Foundry or Cosmos calls fail with `401`/`403`, run the steps below.

## Why CLI steps are needed

- Bicep can assign RBAC only when scope constraints match deployment scope.
- In this repo, Foundry is often in a **different resource group** than the Function App.
- For cross-RG/subscription scenarios, Azure CLI role assignment is the most reliable operational step.
- Cosmos SQL role assignments are currently more reliable operationally via CLI than ARM/Bicep in this deployment path.

## Required roles

- Foundry role: **Cognitive Services User**
  - Role definition ID: `a97b65f3-24c7-4388-baec-2e87135dc908`
- Cosmos data-plane role: **Cosmos DB Built-in Data Contributor**
  - Role definition ID: `00000000-0000-0000-0000-000000000002`

Assign this role to:
1. Your local user principal (for local `func start` + `az login` flow)
2. Function App managed identity (for deployed runtime)

---

## 1) Sign in and select subscription

```powershell
az login
az account set --subscription <subscription-id-or-name>
```

## 2) Resolve Foundry resource scope

From your `FOUNDRY_AGENT_ENDPOINT_URL`, identify the Foundry resource name.

Example resource name:
- `ryder-multi-agent-demo-resource`

Get resource ID:

```powershell
$foundryId = az resource list --name <foundry-resource-name> --query "[0].id" -o tsv
$foundryId
```

## 3) Assign RBAC to local user

```powershell
$userObjectId = az ad signed-in-user show --query id -o tsv
az role assignment create \
  --assignee-object-id $userObjectId \
  --assignee-principal-type User \
  --role "Cognitive Services User" \
  --scope $foundryId
```

## 4) Assign RBAC to Function managed identity

```powershell
$funcPrincipalId = az functionapp identity show \
  --name <function-app-name> \
  --resource-group <function-app-rg> \
  --query principalId -o tsv

az role assignment create \
  --assignee-object-id $funcPrincipalId \
  --assignee-principal-type ServicePrincipal \
  --role "Cognitive Services User" \
  --scope $foundryId
```

## 5) Verify assignments

```powershell
az role assignment list \
  --scope $foundryId \
  --query "[?roleDefinitionName=='Cognitive Services User'].{principalId:principalId,principalType:principalType,role:roleDefinitionName}" \
  -o table
```

## 6) Cosmos native RBAC for Table AAD access

Set app setting to force AAD mode:

```powershell
az functionapp config appsettings set --name <function-app-name> --resource-group <function-app-rg> --settings COSMOS_TABLE_AUTH_MODE=aad
```

Assign Cosmos data-plane role to Function MI:

```powershell
$funcPrincipalId = az functionapp identity show --name <function-app-name> --resource-group <function-app-rg> --query principalId -o tsv
az cosmosdb sql role assignment create \
  --account-name <cosmos-account-name> \
  --resource-group <cosmos-rg> \
  --scope '/' \
  --principal-id $funcPrincipalId \
  --role-definition-id 00000000-0000-0000-0000-000000000002
```

Validate Cosmos role assignments:

```powershell
az cosmosdb sql role assignment list --account-name <cosmos-account-name> --resource-group <cosmos-rg> --query "[].{principalId:principalId,roleDefinitionId:roleDefinitionId,scope:scope}" -o table
```

Assign the same Cosmos role to your local user principal (for local AAD testing):

```powershell
$userObjectId = az ad signed-in-user show --query id -o tsv
az cosmosdb sql role assignment create \
  --account-name <cosmos-account-name> \
  --resource-group <cosmos-rg> \
  --scope '/' \
  --principal-id $userObjectId \
  --role-definition-id 00000000-0000-0000-0000-000000000002
```

If duplicate assignments were created during retries, remove extras:

```powershell
az cosmosdb sql role assignment list --account-name <cosmos-account-name> --resource-group <cosmos-rg> -o table
az cosmosdb sql role assignment delete --account-name <cosmos-account-name> --resource-group <cosmos-rg> --role-assignment-id <assignment-guid> --yes
```

## 7) Local runtime notes

- Ensure `az login` is done in the same account expected for local auth.
- Restart Function host after role assignment:

```powershell
func start --port 7071
```

- RBAC propagation can take a short time. If needed, wait 1–5 minutes and retry.

## Current project settings expectations

`local.settings.json` should include:
- `FOUNDRY_AGENT_ENDPOINT_URL=<foundry responses endpoint>`
- `COSMOS_TABLE_AUTH_MODE=aad`

and should **not require**:
- `FOUNDRY_AGENT_API_KEY`

because Foundry auth is now RBAC-based.

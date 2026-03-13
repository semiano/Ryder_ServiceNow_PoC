param(
    [Parameter(Mandatory = $false)]
    [string]$ResourceGroup = "ryder-rca-dev-rg-swedencentral",

    [Parameter(Mandatory = $false)]
    [string]$Location = "swedencentral",

    [Parameter(Mandatory = $false)]
    [string]$SourceFunctionAppName = "ryder-rca-dev-func",

    [Parameter(Mandatory = $false)]
    [string]$ContainerFunctionAppName = "ryder-rca-dev-func-cnt",

    [Parameter(Mandatory = $false)]
    [string]$ContainerPlanName = "ryder-rca-dev-ep1-plan",

    [Parameter(Mandatory = $false)]
    [string]$ImageName = "rca-processor"
)

$ErrorActionPreference = "Stop"
$imageTag = "${ImageName}:latest"
$deploymentAppName = $ContainerFunctionAppName

Write-Host "[1/9] Resolving subscription and storage account..."
$subId = az account show --query id -o tsv
$storageAccount = az storage account list --resource-group $ResourceGroup --query "[0].name" -o tsv

Write-Host "[2/9] Ensuring Azure Container Registry exists..."
$acrName = ("{0}acr" -f ($ContainerFunctionAppName -replace '-', '')).ToLower()
if ($acrName.Length -gt 50) { $acrName = $acrName.Substring(0, 50) }
$existingAcr = az acr list --resource-group $ResourceGroup --query "[?name=='$acrName'].name | [0]" -o tsv
if (-not $existingAcr) {
    az acr create --name $acrName --resource-group $ResourceGroup --location $Location --sku Basic | Out-Null
}
$acrLoginServer = az acr show --name $acrName --resource-group $ResourceGroup --query loginServer -o tsv

Write-Host "[3/9] Building container image in ACR..."
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $repoRoot
try {
    az acr build --registry $acrName --image $imageTag --file Dockerfile "$repoRoot" --no-logs | Out-Null
}
finally {
    Pop-Location
}

Write-Host "[4/9] Ensuring Elastic Premium Linux plan exists..."
$existingPlan = az functionapp plan list --resource-group $ResourceGroup --query "[?name=='$ContainerPlanName'].name | [0]" -o tsv
if (-not $existingPlan) {
    az functionapp plan create --name $ContainerPlanName --resource-group $ResourceGroup --location $Location --sku EP1 --is-linux | Out-Null
}

Write-Host "[4.5/9] Ensuring dedicated storage account for container app exists..."
$containerStorageAccount = ("st" + (($ContainerFunctionAppName -replace '-', '') + (Get-Random -Maximum 99999))).ToLower()
if ($containerStorageAccount.Length -gt 24) { $containerStorageAccount = $containerStorageAccount.Substring(0, 24) }
$existingContainerStorage = az storage account list --resource-group $ResourceGroup --query "[?starts_with(name, 'st') && contains(name, '$($ContainerFunctionAppName -replace '-', '')')].name | [0]" -o tsv
if ($existingContainerStorage) {
    $containerStorageAccount = $existingContainerStorage
}
else {
    az storage account create --name $containerStorageAccount --resource-group $ResourceGroup --location $Location --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 --allow-blob-public-access false | Out-Null
}

Write-Host "[5/9] Creating or updating containerized Function App..."
$existingContainerApp = az functionapp list --resource-group $ResourceGroup --query "[?name=='$ContainerFunctionAppName'].name | [0]" -o tsv
if (-not $existingContainerApp) {
    $allowSharedKey = az storage account show --name $containerStorageAccount --resource-group $ResourceGroup --query allowSharedKeyAccess -o tsv
    if ($allowSharedKey -ne "true") {
        Write-Warning "Storage account '$containerStorageAccount' has allowSharedKeyAccess=$allowSharedKey. Falling back to existing source app '$SourceFunctionAppName' for container deployment."
        $deploymentAppName = $SourceFunctionAppName
    }
    else {
        az functionapp create `
            --name $ContainerFunctionAppName `
            --resource-group $ResourceGroup `
            --plan $ContainerPlanName `
            --storage-account $containerStorageAccount `
            --deployment-container-image-name "$acrLoginServer/$imageTag" `
            --functions-version 4 | Out-Null
    }
}
else {
    $deploymentAppName = $ContainerFunctionAppName
}

az functionapp config container set `
    --name $deploymentAppName `
    --resource-group $ResourceGroup `
    --image "$acrLoginServer/$imageTag" | Out-Null

$appId = az functionapp show --name $deploymentAppName --resource-group $ResourceGroup --query id -o tsv
az resource update --ids "$appId/config/web" --set properties.acrUseManagedIdentityCreds=true | Out-Null

Write-Host "[6/9] Assigning managed identity and ACR pull role..."
$principalId = az functionapp identity assign --name $deploymentAppName --resource-group $ResourceGroup --query principalId -o tsv
$acrId = az acr show --name $acrName --resource-group $ResourceGroup --query id -o tsv
az role assignment create --assignee-object-id $principalId --assignee-principal-type ServicePrincipal --role AcrPull --scope $acrId 2>$null | Out-Null

Write-Host "[7/9] Configuring identity-based host storage access..."
$storageId = az storage account show --name $containerStorageAccount --resource-group $ResourceGroup --query id -o tsv
az role assignment create --assignee-object-id $principalId --assignee-principal-type ServicePrincipal --role "Storage Blob Data Owner" --scope $storageId 2>$null | Out-Null
az role assignment create --assignee-object-id $principalId --assignee-principal-type ServicePrincipal --role "Storage Queue Data Contributor" --scope $storageId 2>$null | Out-Null
az role assignment create --assignee-object-id $principalId --assignee-principal-type ServicePrincipal --role "Storage Table Data Contributor" --scope $storageId 2>$null | Out-Null

az functionapp config appsettings set `
    --name $deploymentAppName `
    --resource-group $ResourceGroup `
    --settings `
        "AzureWebJobsStorage__accountName=$containerStorageAccount" `
        "AzureWebJobsStorage__credential=managedidentity" `
        "AzureWebJobsStorage__blobServiceUri=https://$containerStorageAccount.blob.core.windows.net" `
        "AzureWebJobsStorage__queueServiceUri=https://$containerStorageAccount.queue.core.windows.net" `
        "AzureWebJobsStorage__tableServiceUri=https://$containerStorageAccount.table.core.windows.net" `
        "WEBSITES_ENABLE_APP_SERVICE_STORAGE=false" `
    "DOCKER_ENABLE_CI=true" | Out-Null

az functionapp config appsettings delete --name $deploymentAppName --resource-group $ResourceGroup --setting-names AzureWebJobsStorage 2>$null | Out-Null

Write-Host "[8/9] Cloning non-storage app settings from source app..."
$sourceSettings = az functionapp config appsettings list --name $SourceFunctionAppName --resource-group $ResourceGroup -o json | ConvertFrom-Json
$skipNames = @(
    "AzureWebJobsStorage",
    "AzureWebJobsStorage__accountName",
    "AzureWebJobsStorage__credential",
    "AzureWebJobsStorage__blobServiceUri",
    "AzureWebJobsStorage__queueServiceUri",
    "AzureWebJobsStorage__tableServiceUri",
    "WEBSITE_RUN_FROM_PACKAGE",
    "SCM_DO_BUILD_DURING_DEPLOYMENT",
    "ENABLE_ORYX_BUILD"
)

$forward = @()
foreach ($setting in $sourceSettings) {
    if ($skipNames -contains $setting.name) { continue }
    if ([string]::IsNullOrWhiteSpace($setting.name)) { continue }
    $forward += ("{0}={1}" -f $setting.name, $setting.value)
}

if ($forward.Count -gt 0 -and $deploymentAppName -ne $SourceFunctionAppName) {
    az functionapp config appsettings set --name $deploymentAppName --resource-group $ResourceGroup --settings $forward | Out-Null
}

Write-Host "[9/9] Restarting app and syncing triggers..."
az functionapp restart --name $deploymentAppName --resource-group $ResourceGroup | Out-Null
$syncUri = "https://management.azure.com/subscriptions/$subId/resourceGroups/$ResourceGroup/providers/Microsoft.Web/sites/$deploymentAppName/syncfunctiontriggers?api-version=2023-12-01"

$syncSucceeded = $false
for ($attempt = 1; $attempt -le 6; $attempt++) {
    try {
        az rest --method POST --uri $syncUri | Out-Null
        $syncSucceeded = $true
        break
    }
    catch {
        if ($attempt -lt 6) {
            Write-Warning "Sync triggers attempt $attempt failed. Retrying in 10 seconds..."
            Start-Sleep -Seconds 10
        }
    }
}
if (-not $syncSucceeded) {
    Write-Warning "Sync triggers failed after retries. App may still come online once container startup completes."
}

$hostName = az functionapp show --name $deploymentAppName --resource-group $ResourceGroup --query defaultHostName -o tsv
Write-Host "Containerized function deployment complete."
Write-Host "Function App: $deploymentAppName"
Write-Host "Host: https://$hostName"
Write-Host "ACR Image: $acrLoginServer/$imageTag"

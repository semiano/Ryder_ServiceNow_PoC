param(
    [string]$ResourceGroup = "ryder-rca-dev-rg-swedencentral",
    [string]$AppName = "ryder-rca-dev-func",
    [string]$TicketId = "INC0010002",
    [string]$Status = "closed"
)

$ErrorActionPreference = "Stop"

$funcName = az functionapp function list --name $AppName --resource-group $ResourceGroup --query "[0].name" -o tsv
if ([string]::IsNullOrWhiteSpace($funcName)) {
    throw "No functions found for app '$AppName' in resource group '$ResourceGroup'."
}

$shortFuncName = ($funcName -split "/")[1]
$key = az functionapp function keys list --name $AppName --resource-group $ResourceGroup --function-name $shortFuncName --query default -o tsv
if ([string]::IsNullOrWhiteSpace($key)) {
    throw "Unable to retrieve function key for '$shortFuncName'."
}

$uri = "https://$AppName.azurewebsites.net/api/process-closed-ticket?code=$key"
$payload = @{ ticketId = $TicketId; status = $Status } | ConvertTo-Json -Compress

Write-Host "POST $uri"
Write-Host "Payload: $payload"

curl.exe -sS -X POST "$uri" -H "Content-Type: application/json" -d "$payload"
Write-Host ""

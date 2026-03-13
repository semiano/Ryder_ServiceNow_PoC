param(
    [string]$RootPath = (Get-Location).Path
)

$ErrorActionPreference = "Stop"

$settingsPath = Join-Path $RootPath "local.settings.json"
if (-not (Test-Path $settingsPath)) {
    throw "local.settings.json not found at $settingsPath"
}

$settings = Get-Content $settingsPath -Raw | ConvertFrom-Json
$values = $settings.Values

$instance = [string]$values.SERVICENOW_INSTANCE_URL
$authScheme = if ([string]::IsNullOrWhiteSpace($values.SERVICENOW_AUTH_SCHEME)) { "Bearer" } else { [string]$values.SERVICENOW_AUTH_SCHEME }
$apiToken = [string]$values.SERVICENOW_API_TOKEN

$childAuthScheme = if ([string]::IsNullOrWhiteSpace($values.SERVICENOW_CHILD_AUTH_SCHEME)) { $authScheme } else { [string]$values.SERVICENOW_CHILD_AUTH_SCHEME }
$childApiToken = if ([string]::IsNullOrWhiteSpace($values.SERVICENOW_CHILD_API_TOKEN)) { $apiToken } else { [string]$values.SERVICENOW_CHILD_API_TOKEN }
$childUsername = [string]$values.SERVICENOW_CHILD_USERNAME
$childPassword = [string]$values.SERVICENOW_CHILD_PASSWORD
$childRecordTable = if ([string]::IsNullOrWhiteSpace($values.SERVICENOW_CHILD_RECORD_TABLE)) { "incident" } else { [string]$values.SERVICENOW_CHILD_RECORD_TABLE }
$parentTicketNumber = if ([string]::IsNullOrWhiteSpace($values.SERVICENOW_TEST_PARENT_TICKET)) { "INC0010002" } else { [string]$values.SERVICENOW_TEST_PARENT_TICKET }

function New-AuthHeaders {
    param(
        [string]$Scheme,
        [string]$Token,
        [string]$Username,
        [string]$Password
    )

    $headers = @{ Accept = "application/json" }
    $normalized = $Scheme.Trim().ToLowerInvariant()

    if ($normalized -in @("x-sn-apikey", "sn_apikey", "apikey", "api_key")) {
        $headers["x-sn-apikey"] = $Token
        return $headers
    }

    if ($normalized -eq "basic") {
        if (-not [string]::IsNullOrWhiteSpace($Username) -and -not [string]::IsNullOrWhiteSpace($Password)) {
            $bytes = [Text.Encoding]::UTF8.GetBytes("$Username`:$Password")
            $headers["Authorization"] = "Basic $([Convert]::ToBase64String($bytes))"
            return $headers
        }
        $headers["Authorization"] = "Basic $Token"
        return $headers
    }

    $headers["Authorization"] = "$Scheme $Token".Trim()
    return $headers
}

function Get-ResponseSnippet {
    param(
        [string]$Text,
        [int]$Max = 500
    )

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return ""
    }

    $clean = ($Text -replace "`r|`n", " ").Trim()
    if ($clean.Length -le $Max) {
        return $clean
    }

    return $clean.Substring(0, $Max)
}

$report = [ordered]@{}
$report.timestampUtc = (Get-Date).ToUniversalTime().ToString("o")
$report.instanceHost = try { ([Uri]$instance).Host } catch { $null }
$report.settings = [ordered]@{
    hasInstanceUrl = -not [string]::IsNullOrWhiteSpace($instance)
    authScheme = $authScheme
    hasApiToken = -not [string]::IsNullOrWhiteSpace($apiToken)
    childAuthScheme = $childAuthScheme
    hasChildApiToken = -not [string]::IsNullOrWhiteSpace([string]$values.SERVICENOW_CHILD_API_TOKEN)
    hasChildUsername = -not [string]::IsNullOrWhiteSpace($childUsername)
    hasChildPassword = -not [string]::IsNullOrWhiteSpace($childPassword)
    childRecordTable = $childRecordTable
    parentTicketNumber = $parentTicketNumber
}

$baseHeaders = New-AuthHeaders -Scheme $authScheme -Token $apiToken -Username "" -Password ""
$childHeaders = New-AuthHeaders -Scheme $childAuthScheme -Token $childApiToken -Username $childUsername -Password $childPassword
$childHeaders["Content-Type"] = "application/json"

$report.probes = [ordered]@{}

$parentSysId = ""
$assignmentGroup = ""
$callerId = ""
$cmdbCi = ""

$getUri = "$instance/api/now/table/incident?sysparm_query=number=$parentTicketNumber&sysparm_limit=1&sysparm_fields=sys_id,number,assignment_group,caller_id,cmdb_ci&sysparm_display_value=true"
try {
    $getResp = Invoke-WebRequest -Uri $getUri -Headers $baseHeaders -Method Get -TimeoutSec 30 -UseBasicParsing
    $getJson = $getResp.Content | ConvertFrom-Json
    $record = $null
    if ($getJson.result -is [System.Array] -and $getJson.result.Count -gt 0) {
        $record = $getJson.result[0]
    }

    if ($record) {
        $parentSysId = [string]$record.sys_id.value
        $assignmentGroup = [string]$record.assignment_group.value
        $callerId = [string]$record.caller_id.value
        $cmdbCi = [string]$record.cmdb_ci.value
    }

    $report.probes.fetchParent = [ordered]@{
        status = [int]$getResp.StatusCode
        found = $null -ne $record
        parentSysIdPresent = -not [string]::IsNullOrWhiteSpace($parentSysId)
    }
}
catch {
    $statusCode = try { [int]$_.Exception.Response.StatusCode.value__ } catch { -1 }
    $respBody = ""
    try {
        $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
        $respBody = $reader.ReadToEnd()
    }
    catch {
        $respBody = $_.Exception.Message
    }

    $report.probes.fetchParent = [ordered]@{
        status = $statusCode
        found = $false
        parentSysIdPresent = $false
        responseSnippet = Get-ResponseSnippet -Text $respBody -Max 300
    }
}

$diagSuffix = Get-Date -Format "yyyyMMddHHmmss"
$postUri = "$instance/api/now/table/$childRecordTable"
$payload = @{
    short_description = "RCA DIAG POST Test $diagSuffix"
    description = "ServiceNow configuration diagnostic POST probe."
    parent_incident = $parentSysId
    assignment_group = $assignmentGroup
    caller_id = $callerId
    cmdb_ci = $cmdbCi
} | ConvertTo-Json -Depth 5

try {
    $postResp = Invoke-WebRequest -Uri $postUri -Headers $childHeaders -Method Post -Body $payload -TimeoutSec 30 -UseBasicParsing
    $postJson = $postResp.Content | ConvertFrom-Json
    $result = $postJson.result

    $report.probes.createChild = [ordered]@{
        status = [int]$postResp.StatusCode
        created = $true
        number = [string]$result.number
        sysIdPresent = -not [string]::IsNullOrWhiteSpace([string]$result.sys_id)
    }
}
catch {
    $statusCode = try { [int]$_.Exception.Response.StatusCode.value__ } catch { -1 }
    $respBody = ""
    try {
        $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
        $respBody = $reader.ReadToEnd()
    }
    catch {
        $respBody = $_.Exception.Message
    }

    $report.probes.createChild = [ordered]@{
        status = $statusCode
        created = $false
        responseSnippet = Get-ResponseSnippet -Text $respBody -Max 500
    }
}

$artifactsDir = Join-Path $RootPath "artifacts"
New-Item -Path $artifactsDir -ItemType Directory -Force | Out-Null

$reportFile = Join-Path $artifactsDir ("servicenow_diagnostic_report_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".json")
$report | ConvertTo-Json -Depth 10 | Set-Content -Path $reportFile -Encoding UTF8

$report | ConvertTo-Json -Depth 10
Write-Host "REPORT_FILE=$reportFile"
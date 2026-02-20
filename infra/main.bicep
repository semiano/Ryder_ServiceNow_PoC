targetScope = 'resourceGroup'

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Application name prefix used for resource naming')
@minLength(2)
param appNamePrefix string

@allowed([
  'dev'
  'prod'
])
@description('Deployment environment')
param environmentName string

@description('Resource tags')
param tags object = {}

@description('Cosmos table name')
param cosmosTableName string = 'RcaReports'

@description('Log level for application')
param logLevel string = 'INFO'

@description('Transcript lookback window in days')
param transcriptLookbackDays int = 30

@description('Maximum transcript character count before truncation')
param transcriptMaxChars int = 120000

@description('RCA schema version')
param rcaSchemaVersion string = '1.0'

@description('ServiceNow KB placeholder enabled flag')
param serviceNowKbEnabled string = 'false'

@description('ServiceNow KB table placeholder')
param serviceNowKbTable string = 'kb_knowledge'

@description('Optional Graph fallback user id')
param graphFallbackUserId string = ''

@description('Set true to bypass Graph transcript lookup and use simulated transcript file content')
param simulateCallTranscriptLookup string = 'false'

@description('Optional Azure AI Foundry/Cognitive Services account name in this resource group for Function MI RBAC assignment')
param foundryAccountName string = ''

@description('ServiceNow instance URL')
param serviceNowInstanceUrl string = 'https://your-instance.service-now.com'

@description('ServiceNow auth scheme (for API key use x-sn-apikey)')
param serviceNowAuthScheme string = 'x-sn-apikey'

@description('Foundry Responses endpoint URL')
param foundryAgentEndpointUrl string = 'https://<resource>.services.ai.azure.com/api/projects/<project>/applications/<app>/protocols/openai/responses?api-version=2025-11-15-preview'

@secure()
@description('ServiceNow API token value to seed into Key Vault secret SERVICENOW-API-TOKEN')
param serviceNowApiToken string = ''

var baseName = toLower('${appNamePrefix}-${environmentName}')
var storageName = toLower('st${uniqueString(resourceGroup().id, appNamePrefix, environmentName)}')
var planName = '${baseName}-plan'
var functionName = '${baseName}-func'
var workspaceName = '${baseName}-law'
var appInsightsName = '${baseName}-appi'
var keyVaultName = toLower(take(replace('${appNamePrefix}-${environmentName}-kv-${uniqueString(resourceGroup().id)}', '-', ''), 24))
var cosmosName = '${baseName}-cosmos'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  tags: tags
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  kind: 'functionapp'
  properties: {
    reserved: true
  }
}

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enabledForTemplateDeployment: true
    enablePurgeProtection: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
  }
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-08-15' = {
  name: cosmosName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [
      {
        locationName: location
        failoverPriority: 0
      }
    ]
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    capabilities: [
      {
        name: 'EnableTable'
      }
    ]
    publicNetworkAccess: 'Enabled'
    enableAutomaticFailover: false
  }
}

var storageKey = storage.listKeys().keys[0].value
var azureWebJobsStorage = 'DefaultEndpointsProtocol=https;AccountName=${storage.name};EndpointSuffix=core.windows.net;AccountKey=${storageKey}'
var cosmosConnectionString = cosmos.listConnectionStrings().connectionStrings[0].connectionString

resource secretServiceNowToken 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'SERVICENOW-API-TOKEN'
  properties: {
    value: serviceNowApiToken
  }
}

resource secretGraphTenantId 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'GRAPH-TENANT-ID'
  properties: {
    value: 'set-me'
  }
}

resource secretGraphClientId 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'GRAPH-CLIENT-ID'
  properties: {
    value: 'set-me'
  }
}

resource secretGraphClientSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'GRAPH-CLIENT-SECRET'
  properties: {
    value: 'set-me'
  }
}

resource secretCosmosConnection 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'COSMOS-TABLE-CONNECTION-STRING'
  properties: {
    value: cosmosConnectionString
  }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    keyVaultReferenceIdentity: 'SystemAssigned'
    siteConfig: {
      minTlsVersion: '1.2'
      linuxFxVersion: 'Python|3.12'
      ftpsState: 'Disabled'
      appSettings: [
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'AzureWebJobsStorage'
          value: azureWebJobsStorage
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
        {
          name: 'SERVICENOW_INSTANCE_URL'
          value: serviceNowInstanceUrl
        }
        {
          name: 'SERVICENOW_AUTH_SCHEME'
          value: serviceNowAuthScheme
        }
        {
          name: 'FOUNDRY_AGENT_ENDPOINT_URL'
          value: foundryAgentEndpointUrl
        }
        {
          name: 'COSMOS_TABLE_ENDPOINT'
          value: cosmos.properties.documentEndpoint
        }
        {
          name: 'COSMOS_TABLE_NAME'
          value: cosmosTableName
        }
        {
          name: 'LOG_LEVEL'
          value: logLevel
        }
        {
          name: 'TRANSCRIPT_LOOKBACK_DAYS'
          value: string(transcriptLookbackDays)
        }
        {
          name: 'TRANSCRIPT_MAX_CHARS'
          value: string(transcriptMaxChars)
        }
        {
          name: 'SIMULATE_CALL_TRANSCRIPT_LOOKUP'
          value: simulateCallTranscriptLookup
        }
        {
          name: 'RCA_SCHEMA_VERSION'
          value: rcaSchemaVersion
        }
        {
          name: 'SERVICENOW_KB_ENABLED'
          value: serviceNowKbEnabled
        }
        {
          name: 'SERVICENOW_KB_TABLE'
          value: serviceNowKbTable
        }
        {
          name: 'GRAPH_FALLBACK_USER_ID'
          value: graphFallbackUserId
        }
        {
          name: 'SERVICENOW_API_TOKEN'
          value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/SERVICENOW-API-TOKEN/)'
        }
        {
          name: 'GRAPH_TENANT_ID'
          value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/GRAPH-TENANT-ID/)'
        }
        {
          name: 'GRAPH_CLIENT_ID'
          value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/GRAPH-CLIENT-ID/)'
        }
        {
          name: 'GRAPH_CLIENT_SECRET'
          value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/GRAPH-CLIENT-SECRET/)'
        }
        {
          name: 'COSMOS_TABLE_CONNECTION_STRING'
          value: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/COSMOS-TABLE-CONNECTION-STRING/)'
        }
      ]
    }
  }
  dependsOn: [
    secretServiceNowToken
    secretGraphTenantId
    secretGraphClientId
    secretGraphClientSecret
    secretCosmosConnection
  ]
}

resource foundryResource 'Microsoft.CognitiveServices/accounts@2023-10-01-preview' existing = if (!empty(foundryAccountName)) {
  name: foundryAccountName
}

resource foundryCognitiveUserAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(foundryAccountName)) {
  scope: foundryResource
  name: guid(foundryResource.id, functionApp.name, 'foundry-cognitive-user')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource keyVaultSecretsUserAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: keyVault
  name: guid(keyVault.id, functionApp.name, 'kv-secrets-user')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output functionDefaultHostname string = functionApp.properties.defaultHostName
output keyVaultName string = keyVault.name
output cosmosEndpoint string = cosmos.properties.documentEndpoint
output appInsightsConnectionString string = appInsights.properties.ConnectionString

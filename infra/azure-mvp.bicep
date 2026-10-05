targetScope = 'resourceGroup'

param location string = resourceGroup().location
@description('Public Linux amd64 image, pinned to a version or digest. No paid Azure registry is provisioned.')
param containerImage string
@secure()
@minLength(16)
param sqlAdminPassword string
@secure()
@minLength(16)
param bootstrapAdminPassword string
param bootstrapAdminEmail string
param sqlAdminLogin string = 'admissionsadmin'

var suffix = uniqueString(resourceGroup().id)
var sqlName = 'admissions-${suffix}'
var databaseName = 'admissions'
var storageName = 'adm${suffix}'
var databaseUrl = 'mssql+pyodbc://${uriComponent(sqlAdminLogin)}:${uriComponent(sqlAdminPassword)}@${sqlName}.database.windows.net:1433/${databaseName}?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=no'

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'admissions-identity'
  location: location
}

resource sql 'Microsoft.Sql/servers@2023-08-01' = {
  name: sqlName
  location: location
  properties: {
    administratorLogin: sqlAdminLogin
    administratorLoginPassword: sqlAdminPassword
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
  }
}

// MVP permits Azure service network connections; SQL authentication is still required.
// Restrict this to controlled egress/private networking before using real applicant data.
resource azureFirewall 'Microsoft.Sql/servers/firewallRules@2023-08-01' = {
  parent: sql
  name: 'AllowAzureServices'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

resource database 'Microsoft.Sql/servers/databases@2023-08-01' = {
  parent: sql
  name: databaseName
  location: location
  sku: { name: 'GP_S_Gen5', tier: 'GeneralPurpose', family: 'Gen5', capacity: 2 }
  properties: {
    minCapacity: json('0.5')
    autoPauseDelay: 60
    maxSizeBytes: 34359738368
    requestedBackupStorageRedundancy: 'Local'
    useFreeLimit: true
    freeLimitExhaustionBehavior: 'AutoPause'
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
  }
}
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}
resource documents 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'admissions-raw'
  properties: { publicAccess: 'None' }
}
resource blobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(documents.id, identity.id, 'blob-contributor')
  scope: documents
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'admissions-environment'
  location: location
  properties: {
    appLogsConfiguration: { destination: 'none' }
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'admissions-mvp'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  properties: {
    managedEnvironmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 8000, allowInsecure: false, transport: 'auto' }
      secrets: [
        { name: 'database-url', value: databaseUrl }
        { name: 'bootstrap-password', value: bootstrapAdminPassword }
      ]
    }
    template: {
      containers: [{
        name: 'admissions'
        image: containerImage
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
        env: [
          { name: 'APP_ENV', value: 'azure-mvp' }
          { name: 'DATABASE_URL', secretRef: 'database-url' }
          { name: 'BOOTSTRAP_ADMIN_EMAIL', value: bootstrapAdminEmail }
          { name: 'BOOTSTRAP_ADMIN_PASSWORD', secretRef: 'bootstrap-password' }
          { name: 'AUTO_CREATE_DB_SCHEMA', value: 'true' }
          { name: 'DEV_AUTH_ENABLED', value: 'false' }
          { name: 'EXPOSE_LEGACY_API', value: 'false' }
          { name: 'BACKEND_CORS_ORIGINS', value: '' }
          { name: 'STORAGE_BACKEND', value: 'azure' }
          { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
          { name: 'AZURE_STORAGE_ACCOUNT_NAME', value: storage.name }
          { name: 'AZURE_STORAGE_CONTAINER_DOCUMENTS', value: documents.name }
          { name: 'PARSER_MODE', value: 'mock' }
          { name: 'DOCUMENT_EXTRACTION_BACKEND', value: 'mock' }
          { name: 'DOCUMENT_CLASSIFICATION_BACKEND', value: 'mock' }
          { name: 'STRUCTURED_EXTRACTION_BACKEND', value: 'mock' }
          { name: 'DOCUMENT_SUMMARIZATION_BACKEND', value: 'mock' }
          { name: 'RUBRIC_SCORING_BACKEND', value: 'mock' }
        ]
        probes: [{
          type: 'Startup'
          httpGet: { path: '/health', port: 8000 }
          initialDelaySeconds: 10
          periodSeconds: 10
          failureThreshold: 30
        }, {
          type: 'Readiness'
          httpGet: { path: '/health', port: 8000 }
          periodSeconds: 10
        }]
      }]
      // Poll job status while processing. The next request restarts interrupted jobs.
      scale: { minReplicas: 0, maxReplicas: 1, rules: [{ name: 'http', http: { metadata: { concurrentRequests: '10' } } }] }
    }
  }
  dependsOn: [database, azureFirewall, blobRole]
}

output appUrl string = 'https://${app.properties.configuration.ingress.fqdn}'
output sqlServerName string = sql.name
output storageAccountName string = storage.name

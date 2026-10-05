targetScope = 'resourceGroup'
param location string = resourceGroup().location
// Static Web Apps has its own supported regions, independent of the backend.
param frontendLocation string = 'eastasia'
param publicAppUrl string = 'https://admissions.everydayai.work'
param bootstrapAdminEmail string
param supabaseUrl string
param supabaseBucket string = 'admissions-raw'
@secure()
param supabaseServiceKey string
@secure()
@minLength(16)
param sqlAdminPassword string
@secure()
@minLength(16)
param bootstrapAdminPassword string
@secure()
param llamaCloudApiKey string = ''
@allowed(['azure', 'llamaparse', 'mock'])
param ocrProvider string = 'llamaparse'
@allowed(['ODBC Driver 17 for SQL Server', 'ODBC Driver 18 for SQL Server'])
param sqlOdbcDriver string = 'ODBC Driver 18 for SQL Server'
var suffix = uniqueString(resourceGroup().id)
var sqlName = 'admissions-${suffix}'
var databaseUrl = 'mssql+pyodbc://admissionsadmin:${uriComponent(sqlAdminPassword)}@${sqlName}.database.windows.net:1433/admissions?driver=${uriComponent(sqlOdbcDriver)}&Encrypt=yes&TrustServerCertificate=no'

resource sql 'Microsoft.Sql/servers@2023-08-01' = {
  name: sqlName
  location: location
  properties: {
    administratorLogin: 'admissionsadmin'
    administratorLoginPassword: sqlAdminPassword
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
  }
}
resource firewall 'Microsoft.Sql/servers/firewallRules@2023-08-01' = {
  parent: sql
  name: 'AllowAzureServices'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}
resource database 'Microsoft.Sql/servers/databases@2023-08-01' = {
  parent: sql
  name: 'admissions'
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
resource ocr 'Microsoft.CognitiveServices/accounts@2023-05-01' = if (ocrProvider == 'azure') {
  name: 'admissions-ocr-${suffix}'
  location: location
  kind: 'FormRecognizer'
  sku: { name: 'F0' }
  properties: { customSubDomainName: 'admissions-ocr-${suffix}', publicNetworkAccess: 'Enabled' }
}
resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: 'admissions-free-plan'
  location: location
  kind: 'linux'
  sku: { name: 'F1', tier: 'Free', capacity: 1 }
  properties: { reserved: true }
}
resource frontend 'Microsoft.Web/staticSites@2023-12-01' = {
  name: 'admissions-ui-${suffix}'
  location: frontendLocation
  sku: { name: 'Free', tier: 'Free' }
  properties: {
    provider: 'Custom'
    publicNetworkAccess: 'Enabled'
    enterpriseGradeCdnStatus: 'Disabled'
    buildProperties: { skipGithubActionWorkflowGeneration: true }
  }
}
resource web 'Microsoft.Web/sites@2023-12-01' = {
  name: 'admissions-free-${suffix}'
  location: location
  kind: 'app,linux'
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      appCommandLine: 'python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1'
      alwaysOn: false
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: [
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
        { name: 'APP_ENV', value: 'azure-mvp' }
        { name: 'DATABASE_URL', value: databaseUrl }
        { name: 'BOOTSTRAP_ADMIN_EMAIL', value: bootstrapAdminEmail }
        { name: 'BOOTSTRAP_ADMIN_PASSWORD', value: bootstrapAdminPassword }
        { name: 'AUTO_CREATE_DB_SCHEMA', value: 'true' }
        { name: 'DEV_AUTH_ENABLED', value: 'false' }
        { name: 'EXPOSE_LEGACY_API', value: 'false' }
        { name: 'PUBLIC_APP_URL', value: publicAppUrl }
        { name: 'FRONTEND_DIST_PATH', value: '' }
        { name: 'BACKEND_CORS_ORIGINS', value: '${publicAppUrl},https://${frontend.properties.defaultHostname}' }
        { name: 'INTAKE_QUEUE_WATCHDOG_SECONDS', value: '0' }
        { name: 'MAX_UPLOAD_BYTES', value: '4194304' }
        { name: 'STORAGE_BACKEND', value: 'supabase' }
        { name: 'SUPABASE_URL', value: supabaseUrl }
        { name: 'SUPABASE_STORAGE_BUCKET', value: supabaseBucket }
        { name: 'SUPABASE_SERVICE_KEY', value: supabaseServiceKey }
        { name: 'LLAMA_CLOUD_API_KEY', value: llamaCloudApiKey }
        { name: 'AZURE_DOCUMENT_INTELLIGENCE_FREE_TIER', value: ocrProvider == 'azure' ? 'true' : 'false' }
        { name: 'AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT', value: ocrProvider == 'azure' ? ocr!.properties.endpoint : '' }
        { name: 'AZURE_DOCUMENT_INTELLIGENCE_KEY', value: ocrProvider == 'azure' ? ocr!.listKeys().key1 : '' }
        { name: 'DOCUMENT_EXTRACTION_BACKEND', value: ocrProvider }
        { name: 'PARSER_MODE', value: ocrProvider == 'mock' ? 'mock' : 'azure' }
        { name: 'DOCUMENT_CLASSIFICATION_BACKEND', value: 'mock' }
        { name: 'STRUCTURED_EXTRACTION_BACKEND', value: 'mock' }
        { name: 'DOCUMENT_SUMMARIZATION_BACKEND', value: 'mock' }
        { name: 'RUBRIC_SCORING_BACKEND', value: 'mock' }
      ]
    }
  }
  dependsOn: [database, firewall]
}
output appUrl string = 'https://${web.properties.defaultHostName}'
output appName string = web.name
output frontendUrl string = 'https://${frontend.properties.defaultHostname}'
output frontendName string = frontend.name
output frontendHostname string = frontend.properties.defaultHostname
output customDomain string = replace(publicAppUrl, 'https://', '')

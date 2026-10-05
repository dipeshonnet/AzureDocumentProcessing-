param([string]$TenantId = '')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'azure-tools.ps1')
$loginArguments = @('login','--use-device-code','--output','none')
if ($TenantId) { $loginArguments += @('--tenant',$TenantId) }
Invoke-Azure $loginArguments
Invoke-Azure @('account','list','--query','[].{name:name,id:id,isDefault:isDefault}','--output','table')

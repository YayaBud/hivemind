param(
    [string]$Workspace = "new-game",
    [int]$Port = 5199
)

Set-StrictMode -Version Latest
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot ".." )).Path

function Test-TcpEndpoint([string]$HostName, [int]$EndpointPort) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $operation = $client.ConnectAsync($HostName, $EndpointPort)
        if (-not $operation.Wait(750)) {
            return $false
        }
        return $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Invoke-LocalApp {
    if ($Workspace -notmatch '^(?!.*__)[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?$') {
        Write-Error "Workspace must be 1-64 characters using letters, digits, or hyphens."
        return 1
    }
    if ($Port -lt 1 -or $Port -gt 65535) {
        Write-Error "Port must be between 1 and 65535."
        return 1
    }
    if (-not (Test-TcpEndpoint "127.0.0.1" 6379)) {
        Write-Error "Local FalkorDB is unavailable at 127.0.0.1:6379. Start scripts/run_local_falkordb.py in WSL first."
        return 1
    }
    if (Test-TcpEndpoint "127.0.0.1" $Port) {
        Write-Error "Application port 127.0.0.1:$Port is already in use."
        return 1
    }

    $names = @(
        "FALKORDB_HOST", "FALKORDB_PORT", "FALKORDB_USERNAME", "FALKORDB_PASSWORD",
        "FALKORDB_SSL", "HIVEMIND_WORKSPACE"
    )
    $previous = @{}
    foreach ($name in $names) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
    }

    try {
        $env:FALKORDB_HOST = "127.0.0.1"
        $env:FALKORDB_PORT = "6379"
        $env:FALKORDB_USERNAME = ""
        $env:FALKORDB_PASSWORD = ""
        $env:FALKORDB_SSL = "false"
        $env:HIVEMIND_WORKSPACE = $Workspace
        & python -m uvicorn hivemind.server:app --app-dir $projectRoot --host 127.0.0.1 --port $Port
        return $LASTEXITCODE
    }
    finally {
        foreach ($name in $names) {
            [Environment]::SetEnvironmentVariable($name, $previous[$name], "Process")
        }
    }
}

$exitCode = Invoke-LocalApp
if ($MyInvocation.InvocationName -eq ".") {
    $global:LASTEXITCODE = $exitCode
    return
}
exit $exitCode


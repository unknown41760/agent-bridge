param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('status', 'delegate', 'resume')]
    [string]$Mode,
    [Parameter(Position = 1)]
    [string]$Task,
    [string]$Session,
    [string]$Correction
)

$ErrorActionPreference = 'Stop'
if ($Mode -ne 'status' -and [string]::IsNullOrWhiteSpace($Task)) {
    throw 'delegate and resume require a task JSON path'
}
if ($Mode -eq 'resume' -and ([string]::IsNullOrWhiteSpace($Session) -or [string]::IsNullOrWhiteSpace($Correction))) {
    throw 'resume requires -Session and -Correction'
}

$proxy = $env:AGENT_BRIDGE_SYNTHETIC_PROXY
if ([string]::IsNullOrWhiteSpace($proxy)) {
    $proxyFile = Join-Path $PSScriptRoot 'proxy.local.txt'
    if (Test-Path -LiteralPath $proxyFile) {
        $proxy = (Get-Content -LiteralPath $proxyFile -Raw).Trim()
    }
}
if ([string]::IsNullOrWhiteSpace($proxy)) {
    throw 'Set AGENT_BRIDGE_SYNTHETIC_PROXY or scripts/proxy.local.txt before using the Synthetic worker'
}

$names = @('PYTHONPATH', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'PYTHONDONTWRITEBYTECODE')
$previous = @{}
foreach ($name in $names) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

try {
    $source = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\src'))
    $env:PYTHONPATH = if ($previous['PYTHONPATH']) {
        $source + [IO.Path]::PathSeparator + $previous['PYTHONPATH']
    } else {
        $source
    }
    $env:HTTP_PROXY = $proxy
    $env:HTTPS_PROXY = $proxy
    $env:NO_PROXY = 'localhost,127.0.0.1,::1'
    $env:PYTHONDONTWRITEBYTECODE = '1'

    $pythonArgs = @('-m', 'agent_bridge.cli', $Mode)
    if ($Mode -ne 'status') { $pythonArgs += $Task }
    if ($Mode -eq 'resume') {
        $pythonArgs += @('--session', $Session, '--correction', $Correction)
    }
    $pythonArgs += '--json'
    & python @pythonArgs
    $code = $LASTEXITCODE
} finally {
    foreach ($name in $names) {
        if ($null -eq $previous[$name]) {
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        } else {
            [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
        }
    }
}
exit $code

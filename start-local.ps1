$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root "services\api\.venv\Scripts\python.exe"
$webRoot = Join-Path $root "apps\web"
$distIndex = Join-Path $webRoot "dist\index.html"
$url = "http://127.0.0.1:8000"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found: $python"
}

$sourceFiles = Get-ChildItem -Path (Join-Path $webRoot "src") -Recurse -File
$latestSource = ($sourceFiles | Measure-Object -Property LastWriteTimeUtc -Maximum).Maximum
$needsBuild = -not (Test-Path -LiteralPath $distIndex)
if (-not $needsBuild) {
    $needsBuild = $latestSource -gt (Get-Item -LiteralPath $distIndex).LastWriteTimeUtc
}
if ($needsBuild) {
    & npm.cmd --prefix $webRoot run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
}

# Reuse a compatible backend, or select a free port without stopping another service.
for ($taskPort = 8000; $taskPort -le 8010; $taskPort++) {
    $url = "http://127.0.0.1:$taskPort"
    $listener = Get-NetTCPConnection -LocalPort $taskPort -State Listen -ErrorAction SilentlyContinue
    if (-not $listener) { break }
    try {
        $health = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 2
        $media = Invoke-RestMethod -Uri "$url/api/media" -TimeoutSec 5
        if ($health.status -eq "ok" -and $health.api_version -eq 20 -and $null -ne $media.templates) {
            Start-Process $url
            exit 0
        }
    } catch {}
}
if ($taskPort -gt 8010) { throw "No free VideoGen port in 8000-8010." }

$browserJob = Start-Job -ArgumentList $url -ScriptBlock {
    param($targetUrl)
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $health = Invoke-RestMethod -Uri "$targetUrl/api/health" -TimeoutSec 2
            if ($health.status -eq "ok") {
                Start-Process $targetUrl
                break
            }
        } catch {}
    }
}

Write-Host "VideoGen backend is running at $url"
Write-Host "Close this window or press Ctrl+C to stop the backend."
try {
    Push-Location (Join-Path $root "services\api")
    & $python -m uvicorn main:app --host 127.0.0.1 --port $taskPort
    if ($LASTEXITCODE -ne 0) { throw "VideoGen backend exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
    Stop-Job -Job $browserJob -ErrorAction SilentlyContinue
    Remove-Job -Job $browserJob -Force -ErrorAction SilentlyContinue
}

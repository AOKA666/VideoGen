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

$running = $false
try {
    $health = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 2
    $running = $health.status -eq "ok"
} catch {}

if ($running) {
    throw "VideoGen backend is already running on port 8000. Close the existing backend window first."
}

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
    & $python -m uvicorn main:app --host 127.0.0.1 --port 8000
    if ($LASTEXITCODE -ne 0) { throw "VideoGen backend exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
    Stop-Job -Job $browserJob -ErrorAction SilentlyContinue
    Remove-Job -Job $browserJob -Force -ErrorAction SilentlyContinue
}

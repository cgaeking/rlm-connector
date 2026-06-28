# RLM Knowledge Base - REST API Windows Service
# Installs the REST API (http://127.0.0.1:8000) as a background service. This is
# the backend the RootMind desktop app talks to. By default it also runs the
# background indexing scheduler. If you ALREADY run the MCP service (which does
# its own indexing), install this with -NoIndex to avoid indexing twice.
#
# Requires: Run as Administrator. Uses NSSM (auto-downloaded to tools/).

param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Start,
    [switch]$Stop,
    [switch]$Status,
    [switch]$NoIndex
)

$ErrorActionPreference = "Stop"

$ServiceName = "RLM-API"
$DisplayName = "RLM Knowledge Base REST API"
$Description = "REST API (127.0.0.1:8000) backend for the RootMind desktop app, with background indexing."
$Port = 8000
# This script lives in deploy/windows/ -> repo root is two levels up.
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$NssmPath = "$ProjectDir\tools\nssm.exe"
$NssmUrl = "https://nssm.cc/release/nssm-2.24.zip"

function Install-Nssm {
    if (Test-Path $NssmPath) { Write-Host "[OK] NSSM present" -ForegroundColor Green; return }
    Write-Host "[*] Downloading NSSM..." -ForegroundColor Cyan
    $toolsDir = "$ProjectDir\tools"; $zipPath = "$toolsDir\nssm.zip"
    New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null
    try {
        Invoke-WebRequest -Uri $NssmUrl -OutFile $zipPath
        Expand-Archive -Path $zipPath -DestinationPath $toolsDir -Force
        $arch = if ([Environment]::Is64BitOperatingSystem) { "win64" } else { "win32" }
        $nssmExe = Get-ChildItem -Path $toolsDir -Recurse -Filter "nssm.exe" |
                   Where-Object { $_.Directory.Name -eq $arch } | Select-Object -First 1
        Copy-Item -Path $nssmExe.FullName -Destination $NssmPath -Force
        Remove-Item $zipPath -Force
        Write-Host "[OK] NSSM installed" -ForegroundColor Green
    } catch {
        Write-Host "[ERROR] NSSM download failed: $_" -ForegroundColor Red
        Write-Host "Download nssm.exe from https://nssm.cc/ into $toolsDir manually." -ForegroundColor Yellow
        exit 1
    }
}

function Test-Administrator {
    $u = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($u)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Microsoft Store Python (WindowsApps) is user-scoped and fails under LocalSystem.
function Test-ServiceSafeVenv {
    param([string]$VenvPath)
    if (-not (Test-Path $VenvPath)) { return $false }
    $cfgFile = Join-Path (Split-Path -Parent (Split-Path -Parent $VenvPath)) "pyvenv.cfg"
    if (-not (Test-Path $cfgFile)) { return $true }
    $cfg = Get-Content $cfgFile -Raw
    if ($cfg -match "WindowsApps" -or $cfg -match "PythonSoftwareFoundation\.Python") {
        Write-Host "[!] Project venv uses Microsoft Store Python - not service-safe." -ForegroundColor Yellow
        return $false
    }
    return $true
}

function Test-PythonRuntime {
    param([string]$PythonPath)
    if (-not (Test-Path $PythonPath)) { return $false }
    & $PythonPath -c "import fastapi, uvicorn, pydantic_settings, apscheduler" 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Resolve-Python {
    $venvPython = "$ProjectDir\.venv\Scripts\python.exe"
    if ((Test-Path $venvPython) -and (Test-ServiceSafeVenv -VenvPath $venvPython)) {
        Write-Host "[i] Using project venv Python" -ForegroundColor Cyan
        return $venvPython
    }
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "C:\Python312\python.exe", "C:\Python311\python.exe"
    )
    foreach ($p in $candidates) { if ((Test-Path $p) -and (Test-PythonRuntime -PythonPath $p)) { return $p } }
    $fallback = (Get-Command python -ErrorAction SilentlyContinue).Source
    if ($fallback -and (Test-PythonRuntime -PythonPath $fallback)) { return $fallback }
    return $null
}

function Install-Service {
    if (-not (Test-Administrator)) { Write-Host "[ERROR] Run as Administrator." -ForegroundColor Red; exit 1 }
    Install-Nssm
    if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
        Write-Host "[!] Service '$ServiceName' exists. Uninstall first with -Uninstall." -ForegroundColor Yellow; exit 1
    }
    $pythonPath = Resolve-Python
    if (-not $pythonPath) {
        Write-Host "[ERROR] No service-safe Python with required modules found." -ForegroundColor Red
        Write-Host "  Install Python from python.org, recreate .venv, then: .venv\Scripts\python.exe -m pip install -e ." -ForegroundColor Gray
        exit 1
    }
    Write-Host "[*] Installing '$ServiceName' (port $Port, indexing: $(if ($NoIndex) {'OFF'} else {'ON'}))" -ForegroundColor Cyan
    Write-Host "    Python:  $pythonPath" -ForegroundColor Gray
    Write-Host "    WorkDir: $ProjectDir" -ForegroundColor Gray

    & $NssmPath install $ServiceName $pythonPath "-m" "src.main" "api" "--host" "127.0.0.1" "--port" "$Port"
    & $NssmPath set $ServiceName AppDirectory $ProjectDir
    & $NssmPath set $ServiceName DisplayName $DisplayName
    & $NssmPath set $ServiceName Description $Description
    & $NssmPath set $ServiceName Start SERVICE_AUTO_START
    if ($NoIndex) { & $NssmPath set $ServiceName AppEnvironmentExtra "RLM_DISABLE_SCHEDULER=1" }
    New-Item -ItemType Directory -Path "$ProjectDir\logs" -Force | Out-Null
    & $NssmPath set $ServiceName AppStdout "$ProjectDir\logs\api-service.log"
    & $NssmPath set $ServiceName AppStderr "$ProjectDir\logs\api-service.log"
    & $NssmPath set $ServiceName AppRotateFiles 1
    & $NssmPath set $ServiceName AppRotateBytes 10485760

    Write-Host "[OK] Service installed." -ForegroundColor Green
    Write-Host "  Start:  .\_service-api.ps1 -Start"
    Write-Host "  Config: $ProjectDir\config.yaml (edit folders/LLM here or via the desktop app)"
}

function Uninstall-Service {
    if (-not (Test-Administrator)) { Write-Host "[ERROR] Run as Administrator." -ForegroundColor Red; exit 1 }
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $svc) { Write-Host "[!] '$ServiceName' not found." -ForegroundColor Yellow; return }
    if ($svc.Status -eq 'Running') { & $NssmPath stop $ServiceName }
    & $NssmPath remove $ServiceName confirm
    Write-Host "[OK] Service removed." -ForegroundColor Green
}

function Start-RlmService {
    if (-not (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) {
        Write-Host "[ERROR] Not installed. Run -Install first." -ForegroundColor Red; exit 1
    }
    Start-Service -Name $ServiceName
    Start-Sleep -Seconds 2
    Show-Status
}

function Stop-RlmService {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $svc) { Write-Host "[ERROR] Not found." -ForegroundColor Red; exit 1 }
    if ($svc.Status -ne 'Stopped') { Stop-Service -Name $ServiceName }
    Write-Host "[OK] Service stopped." -ForegroundColor Green
}

function Show-Status {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $svc) { Write-Host "Service '$ServiceName': NOT INSTALLED" -ForegroundColor Yellow; return }
    $color = switch ($svc.Status) { 'Running' { 'Green' } 'Stopped' { 'Red' } default { 'Yellow' } }
    Write-Host "Service '$ServiceName': $($svc.Status)" -ForegroundColor $color
    try {
        $null = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2 -ErrorAction Stop
        Write-Host "REST API: RESPONDING (http://127.0.0.1:$Port)" -ForegroundColor Green
    } catch { Write-Host "REST API: NOT RESPONDING" -ForegroundColor Red }
}

if ($Install) { Install-Service }
elseif ($Uninstall) { Uninstall-Service }
elseif ($Start) { Start-RlmService }
elseif ($Stop) { Stop-RlmService }
elseif ($Status) { Show-Status }
else {
    Write-Host "RLM REST API - Windows Service Manager" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Usage: .\_service-api.ps1 [-Install [-NoIndex]|-Uninstall|-Start|-Stop|-Status]"
    Write-Host ""
    Write-Host "  -Install    Install the REST API service (Admin). Add -NoIndex if the"
    Write-Host "              MCP service already handles indexing (avoids double indexing)."
    Write-Host "  -Uninstall  Remove the service (Admin)"
    Write-Host "  -Start / -Stop / -Status"
    Write-Host ""
    Show-Status
}

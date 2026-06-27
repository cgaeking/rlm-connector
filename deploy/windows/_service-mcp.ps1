# RLM Knowledge Base - Windows Service Installation
# Requires: Run as Administrator
# Uses NSSM (Non-Sucking Service Manager) for robust Windows service management

param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Start,
    [switch]$Stop,
    [switch]$Status
)

$ErrorActionPreference = "Stop"

# Configuration
$ServiceName = "RLM-MCP-Server"
$DisplayName = "RLM Knowledge Base MCP Server"
$Description = "MCP HTTP Server for RLM Knowledge Base with ngrok tunnel"
# This script lives in deploy/windows/ -> repo root is two levels up.
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$NssmPath = "$ProjectDir\tools\nssm.exe"
$NssmUrl = "https://nssm.cc/release/nssm-2.24.zip"

# Helper function to download and extract NSSM
function Install-Nssm {
    if (Test-Path $NssmPath) {
        Write-Host "[OK] NSSM already installed at $NssmPath" -ForegroundColor Green
        return
    }
    
    Write-Host "[*] Downloading NSSM..." -ForegroundColor Cyan
    $toolsDir = "$ProjectDir\tools"
    $zipPath = "$toolsDir\nssm.zip"
    
    New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null
    
    try {
        Invoke-WebRequest -Uri $NssmUrl -OutFile $zipPath
        Expand-Archive -Path $zipPath -DestinationPath $toolsDir -Force
        
        # Find the correct nssm.exe (64-bit or 32-bit)
        $arch = if ([Environment]::Is64BitOperatingSystem) { "win64" } else { "win32" }
        $nssmExe = Get-ChildItem -Path $toolsDir -Recurse -Filter "nssm.exe" | 
                   Where-Object { $_.Directory.Name -eq $arch } | 
                   Select-Object -First 1
        
        Copy-Item -Path $nssmExe.FullName -Destination $NssmPath -Force
        Remove-Item $zipPath -Force
        
        Write-Host "[OK] NSSM installed successfully" -ForegroundColor Green
    }
    catch {
        Write-Host "[ERROR] Failed to download NSSM: $_" -ForegroundColor Red
        Write-Host "Please download manually from https://nssm.cc/ and place nssm.exe in $toolsDir" -ForegroundColor Yellow
        exit 1
    }
}

# Check if running as Administrator
function Test-Administrator {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# A venv created from Microsoft Store Python (WindowsApps) is not suitable for
# LocalSystem services. Those interpreters are user-scoped and fail in services.
function Test-ServiceSafeVenv {
    param(
        [string]$VenvPath
    )

    if (-not (Test-Path $VenvPath)) {
        return $false
    }

    $venvDir = Split-Path -Parent (Split-Path -Parent $VenvPath)
    $pyVenvCfg = Join-Path $venvDir "pyvenv.cfg"
    if (-not (Test-Path $pyVenvCfg)) {
        return $true
    }

    $cfg = Get-Content $pyVenvCfg -Raw
    if ($cfg -match "WindowsApps" -or $cfg -match "PythonSoftwareFoundation\.Python") {
        Write-Host "[!] Project venv uses Microsoft Store Python (WindowsApps)." -ForegroundColor Yellow
        Write-Host "    This is not service-safe with LocalSystem account." -ForegroundColor Yellow
        return $false
    }

    return $true
}

# Validate that a Python executable has the runtime dependencies needed by the MCP HTTP server.
function Test-PythonRuntime {
    param(
        [string]$PythonPath
    )

    if (-not (Test-Path $PythonPath)) {
        return $false
    }

    & $PythonPath -c "import fastapi, starlette, pydantic_settings, uvicorn" 2>$null
    return ($LASTEXITCODE -eq 0)
}

# Install the service
function Install-Service {
    if (-not (Test-Administrator)) {
        Write-Host "[ERROR] This script must be run as Administrator!" -ForegroundColor Red
        exit 1
    }
    
    Install-Nssm
    
    # Check if service already exists
    $existingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($existingService) {
        Write-Host "[!] Service '$ServiceName' already exists. Uninstall first with -Uninstall" -ForegroundColor Yellow
        exit 1
    }
    
    # Find Python executable - prefer the one with packages installed
    $pythonPath = $null
    
    # First try: Check if there's a service-safe venv in the project
    $venvPython = "$ProjectDir\.venv\Scripts\python.exe"
    if ((Test-Path $venvPython) -and (Test-ServiceSafeVenv -VenvPath $venvPython)) {
        $pythonPath = $venvPython
        Write-Host "[i] Using project venv Python" -ForegroundColor Cyan
    }
    
    # Second try: Common Python installation locations (user installs)
    if (-not $pythonPath) {
        $commonPaths = @(
            "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
            "$env:APPDATA\Local\Programs\Python\Python312\python.exe",
            "C:\Python312\python.exe",
            "C:\Python311\python.exe"
        )
        foreach ($path in $commonPaths) {
            if (Test-Path $path) {
                # Verify core runtime dependencies
                if (Test-PythonRuntime -PythonPath $path) {
                    $pythonPath = $path
                    Write-Host "[i] Found Python with required runtime modules at: $pythonPath" -ForegroundColor Cyan
                    break
                }
            }
        }
    }
    
    # Third try: Find Python with required runtime modules from all pythons in PATH
    if (-not $pythonPath) {
        $allPythons = Get-Command python* -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "^python(\.exe)?$" }
        foreach ($py in $allPythons) {
            if (Test-PythonRuntime -PythonPath $py.Source) {
                $pythonPath = $py.Source
                Write-Host "[i] Found Python with required runtime modules in PATH: $pythonPath" -ForegroundColor Cyan
                break
            }
        }
    }
    
    # Final fallback: Get-Command python (best effort)
    if (-not $pythonPath) {
        $fallbackPython = (Get-Command python -ErrorAction SilentlyContinue).Source
        if ($fallbackPython -and (Test-PythonRuntime -PythonPath $fallbackPython)) {
            $pythonPath = $fallbackPython
            Write-Host "[!] Warning: Using fallback Python from PATH" -ForegroundColor Yellow
        }
    }
    
    if (-not $pythonPath) {
        Write-Host "[ERROR] No service-safe Python with required modules found." -ForegroundColor Red
        Write-Host "" 
        Write-Host "Fix recommendation:" -ForegroundColor Yellow
        Write-Host "  1. Install Python from python.org (not Microsoft Store)" -ForegroundColor Gray
        Write-Host "  2. Recreate .venv using that interpreter" -ForegroundColor Gray
        Write-Host "  3. Install deps: .venv\Scripts\python.exe -m pip install -e ." -ForegroundColor Gray
        exit 1
    }
    
    Write-Host "[*] Installing service '$ServiceName'..." -ForegroundColor Cyan
    Write-Host "    Python: $pythonPath" -ForegroundColor Gray
    Write-Host "    WorkDir: $ProjectDir" -ForegroundColor Gray
    
    # Install service
    & $NssmPath install $ServiceName $pythonPath "-m" "src.mcp_http_server" "--port" "3000"
    
    # Configure service
    & $NssmPath set $ServiceName AppDirectory $ProjectDir
    & $NssmPath set $ServiceName DisplayName $DisplayName
    & $NssmPath set $ServiceName Description $Description
    & $NssmPath set $ServiceName Start SERVICE_AUTO_START
    & $NssmPath set $ServiceName AppStdout "$ProjectDir\logs\service.log"
    & $NssmPath set $ServiceName AppStderr "$ProjectDir\logs\service.log"
    & $NssmPath set $ServiceName AppRotateFiles 1
    & $NssmPath set $ServiceName AppRotateBytes 10485760
    
    # Create logs directory
    New-Item -ItemType Directory -Path "$ProjectDir\logs" -Force | Out-Null
    
    Write-Host "[OK] Service installed successfully!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "  1. Start with: .\_service-mcp.ps1 -Start"
    Write-Host "  2. Or start manually: services.msc -> $DisplayName"
    Write-Host ""
    Write-Host "NOTE: This installs the MCP HTTP Server only." -ForegroundColor Yellow
    Write-Host "      For ngrok tunnel, see _service-ngrok.ps1" -ForegroundColor Yellow
}

# Uninstall the service
function Uninstall-Service {
    if (-not (Test-Administrator)) {
        Write-Host "[ERROR] This script must be run as Administrator!" -ForegroundColor Red
        exit 1
    }
    
    $existingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $existingService) {
        Write-Host "[!] Service '$ServiceName' not found." -ForegroundColor Yellow
        return
    }
    
    # Stop service if running
    if ($existingService.Status -eq 'Running') {
        Write-Host "[*] Stopping service..." -ForegroundColor Cyan
        & $NssmPath stop $ServiceName
    }
    
    Write-Host "[*] Removing service..." -ForegroundColor Cyan
    & $NssmPath remove $ServiceName confirm
    
    Write-Host "[OK] Service removed successfully!" -ForegroundColor Green
}

# Start the service
function Start-RlmService {
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $service) {
        Write-Host "[ERROR] Service '$ServiceName' not installed. Run with -Install first." -ForegroundColor Red
        exit 1
    }
    
    if ($service.Status -eq 'Running') {
        Write-Host "[!] Service is already running." -ForegroundColor Yellow
        return
    }
    
    Write-Host "[*] Starting service..." -ForegroundColor Cyan
    Start-Service -Name $ServiceName
    
    Start-Sleep -Seconds 2
    $service = Get-Service -Name $ServiceName
    if ($service.Status -eq 'Running') {
        Write-Host "[OK] Service started successfully!" -ForegroundColor Green
        
        # Show token info
        $tokenFile = "$ProjectDir\data\api_token.txt"
        if (Test-Path $tokenFile) {
            $token = Get-Content $tokenFile -Raw
            Write-Host ""
            Write-Host "API Token (for n8n):" -ForegroundColor Cyan
            Write-Host "  $token" -ForegroundColor White
        }
    }
}

# Stop the service
function Stop-RlmService {
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $service) {
        Write-Host "[ERROR] Service '$ServiceName' not found." -ForegroundColor Red
        exit 1
    }
    
    if ($service.Status -eq 'Stopped') {
        Write-Host "[!] Service is already stopped." -ForegroundColor Yellow
        return
    }
    
    Write-Host "[*] Stopping service..." -ForegroundColor Cyan
    Stop-Service -Name $ServiceName
    Write-Host "[OK] Service stopped." -ForegroundColor Green
}

# Show service status
function Show-Status {
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $service) {
        Write-Host "Service '$ServiceName': NOT INSTALLED" -ForegroundColor Yellow
        return
    }
    
    $statusColor = switch ($service.Status) {
        'Running' { 'Green' }
        'Stopped' { 'Red' }
        default { 'Yellow' }
    }
    
    Write-Host "Service '$ServiceName': $($service.Status)" -ForegroundColor $statusColor
    
    # Show token if exists
    $tokenFile = "$ProjectDir\data\api_token.txt"
    if (Test-Path $tokenFile) {
        $token = Get-Content $tokenFile -Raw
        Write-Host "API Token: $($token.Trim())" -ForegroundColor Cyan
    }
    
    # Check if server is responding
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:3000/health" -TimeoutSec 2 -ErrorAction SilentlyContinue
        Write-Host "HTTP Server: RESPONDING (http://localhost:3000)" -ForegroundColor Green
    }
    catch {
        Write-Host "HTTP Server: NOT RESPONDING" -ForegroundColor Red
    }
}

# Main logic
if ($Install) { Install-Service }
elseif ($Uninstall) { Uninstall-Service }
elseif ($Start) { Start-RlmService }
elseif ($Stop) { Stop-RlmService }
elseif ($Status) { Show-Status }
else {
    Write-Host "RLM Knowledge Base - Windows Service Manager" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Usage: .\_service-mcp.ps1 [-Install|-Uninstall|-Start|-Stop|-Status]"
    Write-Host ""
    Write-Host "Options:"
    Write-Host "  -Install    Install the service (requires Admin)"
    Write-Host "  -Uninstall  Remove the service (requires Admin)"
    Write-Host "  -Start      Start the service"
    Write-Host "  -Stop       Stop the service"
    Write-Host "  -Status     Show service status and API token"
    Write-Host ""
    Show-Status
}

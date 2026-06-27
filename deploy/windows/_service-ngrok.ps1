# RLM Knowledge Base - ngrok Tunnel Service Installation
# Requires: Run as Administrator
# Requires: ngrok.yml configured with static domain

param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Start,
    [switch]$Stop,
    [switch]$Status
)

$ErrorActionPreference = "Stop"

# Configuration
$ServiceName = "RLM-ngrok-Tunnel"
$DisplayName = "RLM ngrok Tunnel"
$Description = "ngrok tunnel for RLM Knowledge Base MCP Server"
# This script lives in deploy/windows/ -> repo root is two levels up.
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$NssmPath = "$ProjectDir\tools\nssm.exe"
$NgrokConfig = "$ProjectDir\ngrok.yml"

# Check if running as Administrator
function Test-Administrator {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Install the service
function Install-Service {
    if (-not (Test-Administrator)) {
        Write-Host "[ERROR] This script must be run as Administrator!" -ForegroundColor Red
        exit 1
    }
    
    # Check prerequisites
    if (-not (Test-Path $NssmPath)) {
        Write-Host "[ERROR] NSSM not found. Run _service-mcp.ps1 -Install first!" -ForegroundColor Red
        exit 1
    }
    
    if (-not (Test-Path $NgrokConfig)) {
        Write-Host "[ERROR] ngrok.yml not found at $NgrokConfig" -ForegroundColor Red
        exit 1
    }
    
    # Check if domain is configured (only check non-comment lines)
    $domainConfigured = $false
    Get-Content $NgrokConfig | ForEach-Object {
        if ($_ -match "^\s*domain:" -and $_ -notmatch "DEINE_NGROK_DOMAIN") {
            $domainConfigured = $true
        }
    }
    if (-not $domainConfigured) {
        Write-Host "[ERROR] ngrok.yml not configured!" -ForegroundColor Red
        Write-Host "Please edit ngrok.yml and set your ngrok domain." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "Get a free static domain at: https://dashboard.ngrok.com/cloud-edge/domains" -ForegroundColor Cyan
        exit 1
    }
    
    # Find ngrok executable
    $ngrokPath = (Get-Command ngrok -ErrorAction SilentlyContinue).Source
    if (-not $ngrokPath) {
        Write-Host "[ERROR] ngrok not found in PATH!" -ForegroundColor Red
        Write-Host "Install ngrok: https://ngrok.com/download" -ForegroundColor Yellow
        exit 1
    }
    
    # Check if service already exists
    $existingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($existingService) {
        Write-Host "[!] Service '$ServiceName' already exists. Uninstall first with -Uninstall" -ForegroundColor Yellow
        exit 1
    }
    
    Write-Host "[*] Installing ngrok tunnel service..." -ForegroundColor Cyan
    Write-Host "    ngrok: $ngrokPath" -ForegroundColor Gray
    Write-Host "    Config: $NgrokConfig" -ForegroundColor Gray
    
    # Install service
    & $NssmPath install $ServiceName $ngrokPath "start" "--config" $NgrokConfig "rlm-mcp"
    
    # Configure service
    & $NssmPath set $ServiceName AppDirectory $ProjectDir
    & $NssmPath set $ServiceName DisplayName $DisplayName
    & $NssmPath set $ServiceName Description $Description
    & $NssmPath set $ServiceName Start SERVICE_AUTO_START
    & $NssmPath set $ServiceName DependOnService "RLM-MCP-Server"
    & $NssmPath set $ServiceName AppStdout "$ProjectDir\logs\ngrok.log"
    & $NssmPath set $ServiceName AppStderr "$ProjectDir\logs\ngrok.log"
    
    # Create logs directory
    New-Item -ItemType Directory -Path "$ProjectDir\logs" -Force | Out-Null
    
    Write-Host "[OK] ngrok tunnel service installed!" -ForegroundColor Green
    Write-Host ""
    Write-Host "The ngrok service depends on RLM-MCP-Server and will start automatically." -ForegroundColor Cyan
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
    
    if ($existingService.Status -eq 'Running') {
        Write-Host "[*] Stopping service..." -ForegroundColor Cyan
        & $NssmPath stop $ServiceName
    }
    
    Write-Host "[*] Removing service..." -ForegroundColor Cyan
    & $NssmPath remove $ServiceName confirm
    
    Write-Host "[OK] ngrok tunnel service removed!" -ForegroundColor Green
}

# Start the service
function Start-NgrokService {
    # First ensure MCP server is running
    $mcpService = Get-Service -Name "RLM-MCP-Server" -ErrorAction SilentlyContinue
    if ($mcpService -and $mcpService.Status -ne 'Running') {
        Write-Host "[*] Starting MCP Server first..." -ForegroundColor Cyan
        Start-Service -Name "RLM-MCP-Server"
        Start-Sleep -Seconds 2
    }
    
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $service) {
        Write-Host "[ERROR] Service '$ServiceName' not installed." -ForegroundColor Red
        exit 1
    }
    
    if ($service.Status -eq 'Running') {
        Write-Host "[!] Service is already running." -ForegroundColor Yellow
        return
    }
    
    Write-Host "[*] Starting ngrok tunnel..." -ForegroundColor Cyan
    Start-Service -Name $ServiceName
    
    Start-Sleep -Seconds 3
    $service = Get-Service -Name $ServiceName
    if ($service.Status -eq 'Running') {
        Write-Host "[OK] ngrok tunnel started!" -ForegroundColor Green
        
        # Try to get tunnel URL from ngrok API
        try {
            $tunnels = Invoke-RestMethod -Uri "http://localhost:4040/api/tunnels" -ErrorAction SilentlyContinue
            if ($tunnels.tunnels) {
                Write-Host ""
                Write-Host "Tunnel URL:" -ForegroundColor Cyan
                foreach ($tunnel in $tunnels.tunnels) {
                    Write-Host "  $($tunnel.public_url)" -ForegroundColor White
                }
            }
        }
        catch {
            Write-Host "Tunnel URL: Check ngrok.yml for your static domain" -ForegroundColor Yellow
        }
    }
}

# Stop the service
function Stop-NgrokService {
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $service) {
        Write-Host "[ERROR] Service '$ServiceName' not found." -ForegroundColor Red
        exit 1
    }
    
    if ($service.Status -eq 'Stopped') {
        Write-Host "[!] Service is already stopped." -ForegroundColor Yellow
        return
    }
    
    Write-Host "[*] Stopping ngrok tunnel..." -ForegroundColor Cyan
    Stop-Service -Name $ServiceName
    Write-Host "[OK] Tunnel stopped." -ForegroundColor Green
}

# Show status
function Show-Status {
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $service) {
        Write-Host "ngrok Tunnel Service: NOT INSTALLED" -ForegroundColor Yellow
        return
    }
    
    $statusColor = switch ($service.Status) {
        'Running' { 'Green' }
        'Stopped' { 'Red' }
        default { 'Yellow' }
    }
    
    Write-Host "ngrok Tunnel Service: $($service.Status)" -ForegroundColor $statusColor
    
    if ($service.Status -eq 'Running') {
        try {
            $tunnels = Invoke-RestMethod -Uri "http://localhost:4040/api/tunnels" -ErrorAction SilentlyContinue
            if ($tunnels.tunnels) {
                foreach ($tunnel in $tunnels.tunnels) {
                    Write-Host "Tunnel URL: $($tunnel.public_url)" -ForegroundColor Cyan
                }
            }
        }
        catch {
            Write-Host "Tunnel URL: Check ngrok.yml" -ForegroundColor Yellow
        }
    }
}

# Main logic
if ($Install) { Install-Service }
elseif ($Uninstall) { Uninstall-Service }
elseif ($Start) { Start-NgrokService }
elseif ($Stop) { Stop-NgrokService }
elseif ($Status) { Show-Status }
else {
    Write-Host "RLM Knowledge Base - ngrok Tunnel Service Manager" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Usage: .\_service-ngrok.ps1 [-Install|-Uninstall|-Start|-Stop|-Status]"
    Write-Host ""
    Write-Host "Prerequisites:"
    Write-Host "  1. _service-mcp.ps1 -Install (installs NSSM and MCP server)"
    Write-Host "  2. ngrok.yml configured with your static domain"
    Write-Host ""
    Show-Status
}

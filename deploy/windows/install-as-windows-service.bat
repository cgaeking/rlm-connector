@echo off
:: RLM Knowledge Base - one-click auto-start setup (Windows services).
:: Installs the MCP server (and, if ngrok is configured, the ngrok tunnel)
:: as Windows services so they start automatically on boot.
::
:: Right-click this file -> "Run as administrator".

echo ============================================
echo RLM Knowledge Base - Service Installation
echo ============================================
echo.

:: Check for admin rights
net session >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Please run this script as Administrator!
    echo         Right-click and select "Run as administrator"
    pause
    exit /b 1
)

cd /d "%~dp0"

echo Installing MCP server service...
echo.

powershell -ExecutionPolicy Bypass -File ".\_service-mcp.ps1" -Install
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to install MCP server service
    pause
    exit /b 1
)

echo.
echo ============================================
echo ngrok Configuration Check
echo ============================================
echo.

findstr /R /C:"^[^#]*domain:.*YOUR-NGROK-DOMAIN" "..\..\ngrok.yml" >nul 2>&1
if %ERRORLEVEL%==0 (
    echo [!] ngrok.yml is not configured yet - skipping the tunnel service.
    echo.
    echo To enable a permanent public URL:
    echo   1. Copy ngrok.example.yml to ngrok.yml ^(in the repo root^)
    echo   2. Get a free static domain: https://dashboard.ngrok.com/cloud-edge/domains
    echo   3. Set your domain + authtoken in ngrok.yml
    echo   4. Run: powershell -File _service-ngrok.ps1 -Install -Start
    echo.
) else (
    echo [OK] ngrok.yml appears configured - installing tunnel service.
    powershell -ExecutionPolicy Bypass -File ".\_service-ngrok.ps1" -Install
)

echo.
echo ============================================
echo Starting services...
echo ============================================
echo.

powershell -ExecutionPolicy Bypass -File ".\_service-mcp.ps1" -Start

echo.
echo ============================================
echo Setup complete!
echo ============================================
echo.
echo Your API token is in:  data\api_token.txt  (repo root)
echo Manage the service:    powershell -File _service-mcp.ps1 -Status^|-Start^|-Stop
echo Details:               see README.md in this folder
echo.

powershell -ExecutionPolicy Bypass -File ".\_service-mcp.ps1" -Status

pause

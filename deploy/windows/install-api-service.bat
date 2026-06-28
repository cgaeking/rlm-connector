@echo off
:: RLM Knowledge Base - one-click install of the REST API service.
:: This is the backend the RootMind desktop app talks to (http://127.0.0.1:8000).
:: Installs it as an auto-start Windows service. Just double-click this file
:: (it requests Administrator rights automatically).

:: --- self-elevate to Administrator ---
net session >nul 2>&1
if %errorlevel% NEQ 0 (
    echo Requesting administrator rights...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"

echo ============================================
echo RLM REST API - Service Installation
echo ============================================
echo.

:: If the MCP service is already installed, it does the indexing -> install the
:: API for serving only, so files are not indexed twice. Otherwise the API
:: service also runs the indexer (full standalone).
set "NOIDX="
sc query RLM-MCP-Server >nul 2>&1
if %errorlevel%==0 (
    echo [i] MCP service detected - installing REST API WITHOUT its own indexer
    echo     (the MCP service keeps doing the indexing^).
    set "NOIDX=-NoIndex"
) else (
    echo [i] No MCP service - the REST API service will also run the indexer.
)
echo.

powershell -ExecutionPolicy Bypass -File ".\_service-api.ps1" -Install %NOIDX%
if %errorlevel% NEQ 0 (
    echo [ERROR] Failed to install the REST API service.
    pause
    exit /b 1
)

echo.
echo Starting service...
powershell -ExecutionPolicy Bypass -File ".\_service-api.ps1" -Start

echo.
echo ============================================
echo Done. The RootMind desktop app can now connect.
echo ============================================
echo   Config (folders / LLM): ..\..\config.yaml
echo   Manage: powershell -File _service-api.ps1 -Status^|-Start^|-Stop
echo.
pause

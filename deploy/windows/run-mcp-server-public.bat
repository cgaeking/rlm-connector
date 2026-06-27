@echo off
REM Runs the RLM MCP server AND exposes it through a public ngrok URL,
REM so a remote MCP client (e.g. a cloud n8n) can reach it.
REM Needs ngrok installed and ngrok.yml configured in the repo root.
echo ============================================
echo RLM Knowledge Base - MCP Server + ngrok (public URL)
echo ============================================
echo.

cd /d "%~dp0..\.."

:: Check if a static domain is configured in ngrok.yml
if exist "ngrok.yml" (
    findstr /R /C:"^[^#]*domain:.*YOUR-NGROK-DOMAIN" ngrok.yml >nul
    if %ERRORLEVEL%==0 (
        echo [!] ngrok.yml found but domain is not configured yet.
        echo     Edit ngrok.yml and replace YOUR-NGROK-DOMAIN.
        echo.
        set USE_STATIC=0
    ) else (
        echo [i] Using static ngrok domain from ngrok.yml
        set USE_STATIC=1
    )
) else (
    echo [i] No ngrok.yml found - using a random ngrok URL.
    set USE_STATIC=0
)

:: Start MCP server in the background
echo [1/2] Starting MCP server on port 3000...
start "RLM MCP Server" cmd /c "python -m src.mcp_http_server --port 3000"

:: Give the server a moment to come up
timeout /t 3 /nobreak > nul

:: Start ngrok
echo [2/2] Starting ngrok tunnel...
echo.

if "%USE_STATIC%"=="1" (
    echo ========================================
    echo   Static domain in use - the URL stays
    echo   the same across restarts, so you set
    echo   it in your MCP client only once.
    echo ========================================
    echo.
    ngrok start --config ngrok.yml rlm-mcp
) else (
    echo ========================================
    echo   IMPORTANT: copy the HTTPS URL below and
    echo   paste it into your MCP client (e.g. n8n).
    echo ========================================
    echo.
    ngrok http 3000
)

echo.
echo Tunnel stopped. Closing MCP server...
taskkill /FI "WINDOWTITLE eq RLM MCP Server" > nul 2>&1
echo Done.

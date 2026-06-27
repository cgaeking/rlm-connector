@echo off
REM Runs the RLM MCP server in the FOREGROUND on http://localhost:3000
REM so other apps (n8n, Claude, ...) can search the knowledge base.
REM For a permanent setup that starts automatically on boot,
REM use install-as-windows-service.bat instead.
echo Starting RLM Knowledge Base MCP Server (local: http://localhost:3000) ...
cd /d "%~dp0..\.."
python -m src.mcp_http_server %*

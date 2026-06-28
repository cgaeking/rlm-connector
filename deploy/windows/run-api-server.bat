@echo off
REM Runs the RLM REST API in the FOREGROUND on http://127.0.0.1:8000 so the
REM RootMind desktop app can connect. For a permanent auto-start setup, use
REM install-api-service.bat instead.
echo Starting RLM REST API (http://127.0.0.1:8000) ...
cd /d "%~dp0..\.."
set "PYEXE=python"
if exist ".venv\Scripts\python.exe" set "PYEXE=.venv\Scripts\python.exe"
"%PYEXE%" -m src.main api --host 127.0.0.1 --port 8000 %*

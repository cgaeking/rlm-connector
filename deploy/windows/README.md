# Windows Deployment

Scripts to run the **RLM MCP server** on Windows — either quickly in a window
for testing, or as a background **Windows service** that starts on boot.

> The RLM core itself is cross‑platform. These helpers are Windows‑specific.
> Linux/macOS deployment recipes live next to this folder under `deploy/`.

## What's in here

| File | What it does |
|------|--------------|
| `run-mcp-server.bat` | Runs the MCP server in a window on `http://localhost:3000`. Good for a quick test. |
| `run-mcp-server-public.bat` | Same, plus an **ngrok** tunnel so a remote client (e.g. cloud n8n) can reach it. Needs `ngrok.yml`. |
| `install-as-windows-service.bat` | **One‑click setup** (run as admin): installs the server (and ngrok, if configured) as auto‑start services. |
| `_service-mcp.ps1` | Internal: manages the MCP‑server Windows service (`-Install/-Uninstall/-Start/-Stop/-Status`). |
| `_service-ngrok.ps1` | Internal: manages the ngrok tunnel Windows service. |

Files starting with `_` are called by the installer — you normally don't run them directly.

## Prerequisites (once)

1. Install **Python** from [python.org](https://www.python.org/downloads/) — *not* the Microsoft Store version (Store Python can't run as a service).
2. From the **repo root**:
   ```bat
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -e .
   ```
3. (Optional, for a public URL) Install [ngrok](https://ngrok.com/download), then copy `ngrok.example.yml` → `ngrok.yml` and fill in your authtoken + static domain.

## Quickstart

**Just try it (foreground):**
```bat
deploy\windows\run-mcp-server.bat
```
The server prints your **API token** and listens on `http://localhost:3000`.
Endpoint for MCP clients: `http://localhost:3000/mcp` (Streamable HTTP, Bearer auth).

**Run it permanently (auto‑start on boot):**
1. Right‑click `install-as-windows-service.bat` → **Run as administrator**.
2. Done. Your token is in `data\api_token.txt`.

**Manage the service:**
```powershell
powershell -File deploy\windows\_service-mcp.ps1 -Status
powershell -File deploy\windows\_service-mcp.ps1 -Stop
powershell -File deploy\windows\_service-mcp.ps1 -Uninstall
```

## Run on any OS (no Windows needed)

After `pip install -e .`, the cross‑platform entry points work everywhere:
```bash
rlm-mcp-http        # HTTP MCP server (this is what the .bat files call)
rlm-mcp             # stdio MCP server (e.g. for Claude Desktop)
rlm-kb              # local app / indexer
```

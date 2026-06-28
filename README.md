# RLM Knowledge Base

[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![MCP](https://img.shields.io/badge/MCP-Compatible-purple.svg)](https://modelcontextprotocol.io/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)]()

[🇬🇧 English Version](README.en.md)

> **Recursive Language Model (RLM)** - Eine intelligente Dokumenten-Wissensdatenbank, die LLM-Agents direkten Zugriff auf Unternehmensdokumente gibt.

## Was ist das?

Ein lokales System, das:

1. **Dokumente indexiert** (PDF, DOCX, XLSX, TXT, MD) - ohne LLM-Kosten beim Indexieren
2. **Volltext-Suche** via SQLite FTS5 ermöglicht
3. **LLM-Agent** (Claude) intelligent durch die Dokumente navigieren lässt

### Der RLM-Ansatz vs. klassisches RAG

**Klassisches RAG:**

```
Dokumente → Chunks → Embeddings → Vector-DB → "Hoffentlich relevanter Chunk"
```

**RLM (dieses Projekt):**

```
Dokumente → Volltext in DB → LLM entscheidet selbst was es liest
```

Der LLM-Agent hat Tools zur Verfügung und entscheidet autonom:

- `search_documents` - FTS5-Volltextsuche mit Snippets
- `list_documents` - Übersicht aller Dokumente
- `read_document` - Liest Dokument (komplett oder Bereichsweise)
- `get_statistics` - DB-Statistiken

**Vorteile:**

- Kein Informationsverlust durch Chunking
- Agent kann gezielt nachfragen/nachlesen
- Transparentes Reasoning (zeigt welche Tools genutzt wurden)
- Indexierung ist blitzschnell (kein LLM/Embedding nötig)

## Quick Start

### 1. Installation

```bash
cd C:\Users\chris\Documents\MyProjects\rlm-connector

# Virtual Environment (optional aber empfohlen)
python -m venv venv
venv\Scripts\activate  # Windows

# Dependencies installieren
pip install -r requirements.txt
```

### 2. Konfiguration

`.env` Datei mit API-Key:

```env
ANTHROPIC_API_KEY=sk-ant-api03-...
```

`config.yaml` - Dokumentenpfade anpassen:

```yaml
llm:
  provider: anthropic
  model: claude-sonnet-4-6

connectors:
  - name: meine_dokumente
    type: local
    path: D:/Pfad/zu/Dokumenten
    include:
      - "*.pdf"
      - "*.docx"
      - "*.xlsx"
      - "*.txt"
      - "*.md"
    exclude:
      - ".*"
      - "~$*"
      - "node_modules"
```

### 3. Starten

```bash
python -m src.main
```

Öffnet:

- **API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs
- **Chat UI**: http://localhost:7860

### 4. Indexieren

In der UI auf "Index aktualisieren" klicken, oder:

```bash
curl -X POST http://localhost:8000/index/refresh
```

## API Endpoints

| Endpoint                    | Methode | Beschreibung                               |
| --------------------------- | ------- | ------------------------------------------ |
| `/`                       | GET     | Health Check                               |
| `/statistics`             | GET     | DB-Statistiken (Dokumente, Größe, Typen) |
| `/documents`              | GET     | Liste aller Dokumente                      |
| `/documents/{id}`         | GET     | Dokument-Metadaten                         |
| `/documents/{id}/content` | GET     | Dokument-Inhalt (mit Range-Support)        |
| `/search?q=...`           | GET     | FTS5 Volltextsuche                         |
| `/query`                  | POST    | RLM-Query (LLM-gestützte Frage)           |
| `/index/refresh`          | POST    | Index aktualisieren                        |
| `/index/progress`         | GET     | Indexier-Fortschritt                       |
| `/connectors`             | GET     | Konfigurierte Connectors                   |

### Beispiel: Frage stellen

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Erstelle mir eine Liste meiner Projekte"}'
```

## Projektstruktur

```
rlm-connector/
├── src/
│   ├── api/              # FastAPI REST-API
│   │   ├── app.py        # App-Factory, Lifespan, AppState
│   │   └── routes.py     # API-Endpoints
│   ├── connectors/       # Datenquellen
│   │   ├── base.py       # BaseConnector Interface
│   │   └── local.py      # LocalConnector (Dateisystem)
│   ├── database/         # SQLite + FTS5
│   │   ├── models.py     # Document, SyncStatus Models
│   │   └── repository.py # DocumentRepository (CRUD, FTS5-Suche)
│   ├── indexer/          # Dokument-Indexierung
│   │   ├── indexer.py    # Haupt-Indexer (kein LLM!)
│   │   ├── parser.py     # PDF/DOCX/XLSX/TXT Parser
│   │   └── sync.py       # SyncManager (Scheduling)
│   ├── rlm_engine/       # LLM-Agent
│   │   └── engine.py     # KnowledgeBaseEngine (Tool-Use)
│   ├── ui/               # Gradio Chat-UI
│   │   └── chat.py
│   ├── config.py         # AppConfig, load_config()
│   ├── mcp_server.py     # MCP Server für Claude Desktop
│   └── main.py           # Entry Point
├── data/
│   └── index.db          # SQLite Datenbank + FTS5
├── config.yaml           # Konfiguration
├── .env                  # API Keys (nicht committen!)
└── requirements.txt
```

## Technologie-Stack

| Komponente | Technologie                          |
| ---------- | ------------------------------------ |
| Backend    | Python 3.12, FastAPI, Uvicorn        |
| Datenbank  | SQLite + FTS5 (Volltext-Index)       |
| LLM        | Claude Sonnet 4 (Anthropic API)      |
| UI         | Gradio                               |
| Parser     | PyMuPDF (PDF), python-docx, openpyxl |

## Datenbank-Schema

### documents (Haupttabelle)

```sql
- id: STRING (SHA256 Hash aus connector:path)
- connector_name, file_path, file_name, file_type
- size_bytes, page_count, content_length
- content_text: TEXT (voller Dokumentinhalt!)
- content_hash: STRING (für Change Detection)
- status: pending|indexed|error|skipped
- indexed_at, created_at, modified_at
```

### documents_fts (FTS5 Virtual Table)

```sql
- doc_id, file_name, content
- Tokenizer: unicode61 (Umlaute-Support)
```

## RLM Engine - Wie funktioniert's?

1. **User stellt Frage** → Chat UI oder `/query` API
2. **Engine startet Agentic Loop** mit Claude + Tools
3. **Claude entscheidet** welche Tools es braucht:
   - Meist zuerst `search_documents` mit Suchbegriffen
   - Dann `read_document` für Details
   - Iteriert bis Antwort vollständig
4. **Antwort mit Quellen** wird zurückgegeben

**Max 10 Iterationen**, danach Abbruch mit Teilergebnis.

## Performance & Kosten

**Getestet mit ~600k Dateien:**

- Scan + Indexierung: ~20 Minuten
- Davon indexiert: Nur lesbare Typen (PDF, DOCX, etc.)
- FTS5-Suche: <100ms
- RLM-Query: 3-15 Sekunden (je nach Komplexität)

**Kosten:**

| Vorgang                            | Kosten        |
| ---------------------------------- | ------------- |
| Indexierung                        | $0 (kein LLM) |
| Einfache Frage                     | ~$0.02        |
| Komplexe Frage (viele Iterationen) | ~$0.05-0.10   |

## CLI-Befehle

```bash
# Alles starten (API + UI)
python -m src.main

# Nur API
python -m src.main api --port 8000

# Nur UI
python -m src.main ui --port 7860

# Index manuell triggern
python -m src.main index
python -m src.main index --full  # Force Re-Index
```

## Als Hintergrund-Dienst betreiben (Windows)

Der Connector ist das **eigenständige Backend** und läuft typischerweise als
Windows-Dienst, der **dauerhaft im Hintergrund indiziert** – unabhängig davon,
ob eine App geöffnet ist. Konfiguriert wird weiterhin über die `config.yaml` im
Projektverzeichnis (oder bequem über die Desktop-App, die per REST-API in
dieselbe Datei schreibt).

`deploy/windows/` enthält Ein-Klick-Installer (Doppelklick genügt, fragt
automatisch nach Admin-Rechten):

| Datei                              | Zweck                                                                                                                                                                                |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `install-api-service.bat`          | Installiert die **REST-API** (`127.0.0.1:8000`) als Dienst `RLM-API` – das Backend für die RootMind Desktop-App. Erkennt den MCP-Dienst und installiert dann ohne eigenen Indexer (kein Doppel-Indizieren). |
| `install-as-windows-service.bat`   | Installiert den **MCP-Server** (Port 3000) als Dienst (für n8n / Claude / Sam), inkl. optionalem ngrok-Tunnel.                                                                       |

Verwalten: `powershell -File deploy/windows/_service-api.ps1 -Status` (bzw.
`-Start` / `-Stop` / `-Uninstall`). Vordergrund zum Testen:
`deploy/windows/run-api-server.bat`.

> Indizierung soll nur **einmal** laufen: Wer den MCP-Dienst nutzt (der bereits
> indiziert), installiert die REST-API mit `-NoIndex` (macht die `.bat`
> automatisch). Andernfalls indiziert der REST-API-Dienst selbst.

## RootMind Desktop-App

[**RootMind**](https://github.com/cgaeking/rlm-desktop) ist die optionale
Windows-Desktop-App (Tauri) als komfortables Front-End: Ordner konfigurieren,
Index-Status sehen, mit den Dokumenten chatten (inkl. anklickbarer Quellen) und
Chat-Verlauf. Sie ist ein **reiner Client** der REST-API – sie startet oder
verwaltet das Backend **nicht**. Voraussetzung ist ein laufender Connector
(REST-API-Dienst auf `127.0.0.1:8000`).

## Aktuelle Features

- [X] Lokales Dateisystem indexieren
- [X] PDF, DOCX, XLSX, TXT, MD Parser
- [X] SQLite FTS5 Volltextsuche
- [X] Fuzzy-Suche mit Trigram-Matching
- [X] RLM Tool-Use Agent (Claude)
- [X] REST API mit OpenAPI Docs
- [X] Gradio Chat UI
- [X] **MCP Server** für Claude Desktop Integration
- [X] Inkrementelles Indexieren (Hash-basiert)
- [X] WAL-Mode für bessere DB-Performance
- [X] Hintergrund-Indizierung via Windows-Dienst (auto-start)
- [X] **RootMind** Desktop-App (Tauri) als REST-Client

## MCP Server (Claude Desktop Integration)

Der RLM Knowledge Base kann als MCP Server betrieben werden, um die Dokumentensuche direkt in Claude Desktop oder anderen MCP-kompatiblen Clients zu nutzen.

### Installation MCP Dependency

```bash
pip install mcp>=1.0.0
# oder komplettes Projekt neu installieren:
pip install -e .
```

### Claude Desktop Konfiguration

Füge in `%APPDATA%\Claude\claude_desktop_config.json` hinzu:

```json
{
  "mcpServers": {
    "rlm-knowledge-base": {
      "command": "python",
      "args": ["-m", "src.mcp_server"],
      "cwd": "C:\\Users\\chris\\Documents\\MyProjects\\rlm-connector"
    }
  }
}
```

**Wichtig:** Der MCP Server verwendet die gleiche `config.yaml` und Datenbank wie die Hauptanwendung.

### MCP Tools

Claude Desktop hat dann Zugriff auf:

| Tool                 | Beschreibung                     |
| -------------------- | -------------------------------- |
| `search_documents` | FTS5 Volltextsuche mit Snippets  |
| `search_fuzzy`     | Tippfehler-tolerante Fuzzy-Suche |
| `list_documents`   | Übersicht aller Dokumente       |
| `read_document`    | Dokument-Inhalt lesen            |
| `get_statistics`   | Datenbank-Statistiken            |

### Beispiel-Nutzung in Claude Desktop

Nach der Konfiguration kannst du in Claude Desktop fragen:

- *"Suche in meinen Dokumenten nach Urlaubsregelungen"*
- *"Was steht in der Projektdokumentation?"*
- *"Liste alle PDF-Dateien"*

Claude nutzt automatisch die MCP-Tools um in deiner lokalen Dokumentenbasis zu suchen.

### MCP Server manuell starten (zum Testen)

```bash
python -m src.mcp_server
```

## Geplante Features

- [ ] n8n Custom Node
- [ ] OCR für gescannte PDFs
- [ ] Cleanup-Endpoint für gelöschte Dateien
- [ ] Ollama-Support für lokale LLMs

## Bekannte Einschränkungen

- **Gescannte PDFs** ohne OCR-Layer können nicht gelesen werden
- **Sehr große Dokumente** (>50k Zeichen) werden in Teilen gelesen
- **Rate Limits** bei Anthropic API (automatisches Retry)

---

*Projekt-Pfad: `C:\Users\chris\Documents\MyProjects\rlm-connector`*
*Stand: Juni 2026*

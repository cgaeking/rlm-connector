# RLM Knowledge Base - Entwickler-Guide

> Anleitung für Weiterentwicklung und Debugging

## Schnellstart für neue Session

```bash
# 1. Projekt öffnen
cd C:\Users\chris\Documents\MyProjects\rlm-connector

# 2. Kontext laden (für LLM)
# Lies: README.md, ARCHITECTURE.md, diese Datei

# 3. App starten
python -m src.main
```

## Aktueller Stand (Januar 2025)

### Implementiert

- [X] LocalConnector für Dateisystem
- [X] Parser: PDF (PyMuPDF), DOCX, XLSX, TXT, MD
- [X] SQLite + FTS5 Volltext-Index
- [X] RLM Engine mit Tool-Use (Claude)
- [X] REST API (FastAPI)
- [X] MCP Server
- [X] Gradio Chat UI
- [X] Inkrementelles Indexieren (Hash-basiert)

### Nicht implementiert

- [ ] n8n Custom Node
- [ ] OCR für gescannte PDFs
- [ ] Cleanup für gelöschte Dateien
- [ ] Ollama-Support

## Debugging

### Logs aktivieren

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Wichtige Log-Ausgaben

```
src.indexer.indexer - INFO - Indexed: datei.pdf (1234 chars)
src.indexer.indexer - WARNING - Could not extract text from: scan.pdf
src.rlm_engine.engine - INFO - RLM iteration 1
src.rlm_engine.engine - INFO - Tool call: search_documents({"query": "..."})
```

### DB direkt inspizieren

```bash
sqlite3 data/index.db

# Dokumente zählen
SELECT COUNT(*) FROM documents WHERE status = 'indexed';

# FTS5 testen
SELECT doc_id, snippet(documents_fts, 2, '>>>', '<<<', '...', 30)
FROM documents_fts
WHERE documents_fts MATCH 'Suchbegriff';

# Große Dokumente finden
SELECT file_name, content_length FROM documents
ORDER BY content_length DESC LIMIT 10;
```

### API testen

```bash
# Health Check
curl http://localhost:8000/

# Statistiken
curl http://localhost:8000/statistics

# Suche
curl "http://localhost:8000/search?q=Vertrag"

# Query
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Was sind meine Projekte?"}'
```

## Code-Änderungen

### Neues RLM-Tool hinzufügen

1. **Tool-Definition** in `src/rlm_engine/engine.py`:

```python
TOOLS = [
    # ... bestehende Tools ...
    {
        "name": "mein_neues_tool",
        "description": "Beschreibung für Claude",
        "input_schema": {
            "type": "object",
            "properties": {
                "param1": {"type": "string", "description": "..."}
            },
            "required": ["param1"]
        }
    }
]
```

2. **Implementierung**:

```python
def _tool_mein_neues_tool(self, param1: str) -> str:
    # Logik hier
    return "Ergebnis als String"
```

3. **Registrierung** in `_execute_tool()`:

```python
elif tool_name == "mein_neues_tool":
    return self._tool_mein_neues_tool(tool_input["param1"])
```

### Neuen API-Endpoint hinzufügen

In `src/api/routes.py`:

```python
@router.get("/mein-endpoint", tags=["Custom"])
async def mein_endpoint():
    return {"data": app_state.db.some_method()}
```

### Neuen Connector hinzufügen

1. **Datei erstellen** `src/connectors/myconnector.py`:

```python
from .base import BaseConnector, FileInfo, FileMetadata

class MyConnector(BaseConnector):
    def __init__(self, name: str, ...):
        self._name = name
        # ...

    @property
    def name(self) -> str:
        return self._name

    def list_files_recursive(self) -> Iterator[FileInfo]:
        # Dateien auflisten
        yield FileInfo(path=..., name=..., size=..., ...)

    def read_file(self, path: str) -> bytes:
        # Datei lesen
        return content_bytes

    def get_metadata(self, path: str) -> FileMetadata:
        # Metadaten holen
        return FileMetadata(...)

    def file_exists(self, path: str) -> bool:
        return ...
```

2. **Registrieren** in `src/api/app.py`:

```python
def create_connectors(config: AppConfig) -> dict:
    # ...
    elif connector_config.type == "mytype":
        connector = MyConnector(...)
        connectors[connector_config.name] = connector
```

3. **Config** in `src/config.py` erweitern

## Typische Probleme

### "Could not extract text from: ..."

- PDF ist gescannt (nur Bilder, kein Text-Layer)
- Lösung: OCR integrieren (Tesseract) oder ignorieren

### FTS5-Suche findet nichts

- Prüfen ob Dokument in `documents_fts` ist:
  ```sql
  SELECT * FROM documents_fts WHERE doc_id = 'xxx';
  ```
- FTS5-Index wird bei `upsert_document()` aktualisiert

### Rate Limit (429)

- Anthropic API hat Limits
- Automatisches Retry ist eingebaut
- Bei häufigen Limits: Weniger parallele Requests

### Große Dokumente

- `read_document` limitiert auf 50k Zeichen pro Aufruf
- Agent kann mit `start_char`/`end_char` navigieren
- System-Prompt weist darauf hin

## Konfiguration

### config.yaml Struktur

```yaml
llm:
  provider: anthropic
  model: claude-sonnet-4-20250514
  # api_key: aus .env

database:
  type: sqlite
  path: ./data/index.db

connectors:
  - name: meine_docs
    type: local
    path: D:/Dokumente
    include: ["*.pdf", "*.docx"]
    exclude: [".*", "~$*"]

indexer:
  sync_schedule: "0 3 * * *"  # Cron: täglich 3 Uhr
  max_file_size_mb: 50
  max_concurrent: 5

api:
  host: 0.0.0.0
  port: 8000

ui:
  enabled: true
  port: 7860
```

### Umgebungsvariablen (.env)

```env
ANTHROPIC_API_KEY=sk-ant-api03-...
```

---

## Kontext-Wiederherstellung für LLMs

Wenn du (Claude oder anderes LLM) dieses Projekt weiterentwickeln sollst:

1. **Lies diese Dateien:**

   - `README.md` - Übersicht, Features, Setup
   - `ARCHITECTURE.md` - Technische Details, Komponenten
   - `DEVELOPMENT.md` - Diese Datei
2. **Wichtige Code-Dateien:**

   - `src/rlm_engine/engine.py` - RLM Agent
   - `src/database/repository.py` - DB-Operationen
   - `src/indexer/indexer.py` - Indexierung
   - `src/api/routes.py` - API Endpoints
3. **Aktueller Zustand:**

   - Projekt ist funktionsfähig
   - ~600k Dateien wurden getestet
   - FTS5 + RLM Tool-Use funktioniert
   - Kein LLM-Kosten beim Indexieren
4. **Projekt-Pfad:**

   ```
   C:\Users\chris\Documents\MyProjects\rlm-connector
   ```

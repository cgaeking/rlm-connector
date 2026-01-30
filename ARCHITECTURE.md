# RLM Knowledge Base - Architektur

> Technische Dokumentation für LLM-Context-Wiederherstellung

## Systemübersicht

```
┌─────────────────────────────────────────────────────────────────┐
│                         User Interface                          │
│  ┌─────────────────┐              ┌─────────────────────────┐   │
│  │  Gradio Chat UI │              │   REST API (FastAPI)    │   │
│  │   :7860         │              │   :8000                 │   │
│  └────────┬────────┘              └───────────┬─────────────┘   │
│           │                                   │                 │
│           └───────────────┬───────────────────┘                 │
│                           ▼                                     │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                   RLM Engine                             │   │
│  │   - Tool-Use Agentic Loop                               │   │
│  │   - Claude API (Anthropic)                              │   │
│  │   - Tools: search_documents, read_document, etc.        │   │
│  └────────────────────────┬────────────────────────────────┘   │
│                           ▼                                     │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                 DocumentRepository                       │   │
│  │   - SQLite Database                                     │   │
│  │   - FTS5 Volltext-Index                                 │   │
│  │   - WAL-Mode für Performance                            │   │
│  └────────────────────────┬────────────────────────────────┘   │
│                           ▼                                     │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                     Indexer                              │   │
│  │   - Parser (PDF, DOCX, XLSX, TXT)                       │   │
│  │   - SyncManager (Scheduling)                            │   │
│  │   - Change Detection (Hash-basiert)                     │   │
│  └────────────────────────┬────────────────────────────────┘   │
│                           ▼                                     │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    Connectors                            │   │
│  │   - LocalConnector (Dateisystem)                        │   │
│  │   - (geplant: OneDriveConnector)                        │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## Kernkomponenten

### 1. RLM Engine (`src/rlm_engine/engine.py`)

**Zweck:** LLM-gestützte Dokumentenabfrage mit Tool-Use

**Klasse:** `KnowledgeBaseEngine`

**Tools für Claude:**
```python
TOOLS = [
    "search_documents"  # FTS5 Volltextsuche, gibt Snippets zurück
    "list_documents"    # Dokumentenliste mit Metadaten
    "read_document"     # Dokument lesen (mit start_char/end_char für große Docs)
    "get_statistics"    # DB-Statistiken
]
```

**Agentic Loop:**
```python
async def query(question: str, max_iterations: int = 10):
    # 1. Sende Frage + Tools an Claude
    # 2. Wenn Claude Tool nutzen will → ausführen
    # 3. Ergebnis zurück an Claude
    # 4. Wiederholen bis stop_reason == "end_turn"
    # 5. Antwort + Sources + Token-Count zurückgeben
```

**System Prompt:**
- Deutsche Antworten
- Immer Quellen angeben
- Transparentes Vorgehen
- Bei großen Dokumenten in Teilen lesen

### 2. DocumentRepository (`src/database/repository.py`)

**Zweck:** Datenbank-Operationen + FTS5-Suche

**Wichtige Methoden:**
```python
# CRUD
upsert_document(...)     # Insert/Update + FTS5-Index aktualisieren
get_document(doc_id)     # Nach ID
delete_document(doc_id)  # Löscht auch aus FTS5

# Suche
search_fulltext(query, limit, file_type)  # FTS5 mit Snippets
list_documents(file_type, search_filename, limit)  # Metadaten-Liste
get_document_content(doc_id, start_char, end_char)  # Inhalt (Bereich)

# Stats
get_statistics()         # Zähler, Größen, Dateitypen
count_documents(...)     # Anzahl mit Filtern
```

**FTS5-Setup:**
```sql
CREATE VIRTUAL TABLE documents_fts USING fts5(
    doc_id UNINDEXED,
    file_name,
    content,
    tokenize='unicode61 remove_diacritics 0'
);
```

**SQLite Optimierungen:**
```sql
PRAGMA journal_mode=WAL;      -- Write-Ahead Logging
PRAGMA synchronous=NORMAL;    -- Schneller
PRAGMA cache_size=-64000;     -- 64MB Cache
```

### 3. Indexer (`src/indexer/indexer.py`)

**Zweck:** Dokumente parsen und in DB speichern (OHNE LLM!)

**Workflow:**
```python
async def index_file(connector, file_path, force=False):
    # 1. Metadaten holen
    # 2. Hash prüfen → Skip wenn unverändert
    # 3. Dateigröße prüfen → Skip wenn zu groß
    # 4. Parser aufrufen → Text extrahieren
    # 5. In DB speichern (inkl. FTS5-Update)
```

**Unterstützte Formate:**
| Format | Parser |
|--------|--------|
| PDF | PyMuPDF (fitz) |
| DOCX | python-docx |
| XLSX | openpyxl |
| TXT/MD | UTF-8 decode |

**Concurrency:** `asyncio.Semaphore` für parallele Verarbeitung

### 4. Connectors (`src/connectors/`)

**Interface:** `BaseConnector`
```python
class BaseConnector(ABC):
    name: str

    def list_files_recursive() -> Iterator[FileInfo]
    def read_file(path: str) -> bytes
    def get_metadata(path: str) -> FileMetadata
    def file_exists(path: str) -> bool
    def status() -> dict
```

**LocalConnector:**
- Nutzt `pathlib` + `fnmatch`
- Include/Exclude Patterns
- Hash-Berechnung für Change Detection

### 5. API (`src/api/`)

**app.py - Lifespan:**
```python
@asynccontextmanager
async def lifespan(app):
    # Startup: DB, Connectors, Indexer, RLM Engine initialisieren
    # Yield
    # Shutdown: Scheduler stoppen
```

**AppState:**
```python
class AppState:
    db: DocumentRepository
    connectors: dict
    indexer: Indexer
    sync_manager: SyncManager
    rlm_engine: KnowledgeBaseEngine
    config: AppConfig
```

**routes.py - Wichtige Endpoints:**
- `POST /query` → `rlm_engine.query()`
- `GET /search` → `db.search_fulltext()`
- `POST /index/refresh` → `sync_manager.incremental_sync()`

### 6. Config (`src/config.py`)

**Struktur:**
```python
@dataclass
class LLMConfig:
    provider: str      # "anthropic"
    model: str         # "claude-sonnet-4-20250514"
    api_key: str       # aus .env oder config

@dataclass
class ConnectorConfig:
    name: str
    type: str          # "local"
    path: str
    include: list[str]
    exclude: list[str]

@dataclass
class AppConfig:
    llm: LLMConfig
    database: DatabaseConfig
    connectors: list[ConnectorConfig]
    indexer: IndexerConfig
    api: APIConfig
    ui: UIConfig
```

## Datenfluss

### Indexierung
```
Dateisystem
    ↓ LocalConnector.list_files_recursive()
FileInfo (path, name, size, hash)
    ↓ Indexer.index_file()
bytes (Dateiinhalt)
    ↓ DocumentParser.parse()
str (Textinhalt)
    ↓ DocumentRepository.upsert_document()
SQLite (documents + documents_fts)
```

### Query
```
User-Frage
    ↓ /query API oder Chat UI
KnowledgeBaseEngine.query()
    ↓ Claude API + Tools
    ↓ Iteration 1: search_documents("...")
    ↓ Iteration 2: read_document("...")
    ↓ ... (bis max 10)
Antwort + Sources + Tokens
```

## Wichtige Dateien

| Datei | Zweck |
|-------|-------|
| `src/rlm_engine/engine.py` | RLM Tool-Use Agent |
| `src/database/repository.py` | DB + FTS5 Operationen |
| `src/database/models.py` | SQLAlchemy Models |
| `src/indexer/indexer.py` | Dokument-Indexierung |
| `src/indexer/parser.py` | PDF/DOCX/XLSX Parser |
| `src/api/app.py` | FastAPI App Factory |
| `src/api/routes.py` | API Endpoints |
| `src/config.py` | Konfiguration laden |
| `src/main.py` | Entry Point |

## Erweiterungspunkte

### Neuen Connector hinzufügen
1. `src/connectors/myconnector.py` erstellen
2. Von `BaseConnector` erben
3. In `src/api/app.py` `create_connectors()` registrieren
4. Config-Typ in `src/config.py` hinzufügen

### Neues Tool für RLM hinzufügen
1. In `src/rlm_engine/engine.py`:
   - Tool-Definition zu `TOOLS` Liste hinzufügen
   - `_tool_xxx()` Methode implementieren
   - In `_execute_tool()` registrieren

### Neuen Parser hinzufügen
1. In `src/indexer/parser.py`:
   - `SUPPORTED_EXTENSIONS` erweitern
   - Parse-Logik in `parse()` hinzufügen

---

*Für schnellen Context-Reset: Lies diese Datei + README.md*

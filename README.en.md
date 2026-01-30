# RLM Knowledge Base

[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![MCP](https://img.shields.io/badge/MCP-Compatible-purple.svg)](https://modelcontextprotocol.io/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)]()

[🇩🇪 Deutsche Version](README.md)

> **Recursive Language Model (RLM)** - An intelligent document knowledge base that gives LLM agents direct access to enterprise documents.

## What is this?

A local system that:

1. **Indexes documents** (PDF, DOCX, XLSX, TXT, MD) - without LLM costs during indexing
2. **Full-text search** via SQLite FTS5
3. **LLM Agent** (Claude) intelligently navigates through documents

### The RLM Approach vs. Classic RAG

**Classic RAG:**

```
Documents → Chunks → Embeddings → Vector-DB → "Hopefully relevant chunk"
```

**RLM (this project):**

```
Documents → Full-text in DB → LLM decides itself what to read
```

The LLM agent has tools available and decides autonomously:

- `search_documents` - FTS5 full-text search with snippets
- `list_documents` - Overview of all documents
- `read_document` - Reads document (complete or by range)
- `get_statistics` - DB statistics

**Advantages:**

- No information loss through chunking
- Agent can specifically query/re-read
- Transparent reasoning (shows which tools were used)
- Indexing is lightning fast (no LLM/embedding required)

## Quick Start

### 1. Installation

```bash
cd C:\Users\chris\Documents\MyProjects\rlm-connector

# Virtual Environment (optional but recommended)
python -m venv venv
venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt
```

### 2. Configuration

`.env` file with API key:

```env
ANTHROPIC_API_KEY=sk-ant-api03-...
```

`config.yaml` - Adjust document paths:

```yaml
llm:
  provider: anthropic
  model: claude-sonnet-4-20250514

connectors:
  - name: my_documents
    type: local
    path: D:/Path/to/Documents
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

### 3. Start

```bash
python -m src.main
```

Opens:

- **API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs
- **Chat UI**: http://localhost:7860

### 4. Index

Click "Refresh Index" in the UI, or:

```bash
curl -X POST http://localhost:8000/index/refresh
```

## API Endpoints

| Endpoint                    | Method | Description                                |
| --------------------------- | ------ | ------------------------------------------ |
| `/`                         | GET    | Health Check                               |
| `/statistics`               | GET    | DB statistics (documents, size, types)     |
| `/documents`                | GET    | List all documents                         |
| `/documents/{id}`           | GET    | Document metadata                          |
| `/documents/{id}/content`   | GET    | Document content (with range support)      |
| `/search?q=...`             | GET    | FTS5 full-text search                      |
| `/query`                    | POST   | RLM query (LLM-powered question)           |
| `/index/refresh`            | POST   | Refresh index                              |
| `/index/progress`           | GET    | Indexing progress                          |
| `/connectors`               | GET    | Configured connectors                      |

### Example: Ask a Question

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Create a list of my projects"}'
```

## Project Structure

```
rlm-connector/
├── src/
│   ├── api/              # FastAPI REST API
│   │   ├── app.py        # App factory, lifespan, AppState
│   │   └── routes.py     # API endpoints
│   ├── connectors/       # Data sources
│   │   ├── base.py       # BaseConnector interface
│   │   └── local.py      # LocalConnector (filesystem)
│   ├── database/         # SQLite + FTS5
│   │   ├── models.py     # Document, SyncStatus models
│   │   └── repository.py # DocumentRepository (CRUD, FTS5 search)
│   ├── indexer/          # Document indexing
│   │   ├── indexer.py    # Main indexer (no LLM!)
│   │   ├── parser.py     # PDF/DOCX/XLSX/TXT parser
│   │   └── sync.py       # SyncManager (scheduling)
│   ├── rlm_engine/       # LLM Agent
│   │   └── engine.py     # KnowledgeBaseEngine (tool use)
│   ├── ui/               # Gradio Chat UI
│   │   └── chat.py
│   ├── config.py         # AppConfig, load_config()
│   ├── mcp_server.py     # MCP Server for Claude Desktop
│   └── main.py           # Entry point
├── data/
│   └── index.db          # SQLite database + FTS5
├── config.yaml           # Configuration
├── .env                  # API keys (don't commit!)
└── requirements.txt
```

## Technology Stack

| Component | Technology                           |
| --------- | ------------------------------------ |
| Backend   | Python 3.12, FastAPI, Uvicorn        |
| Database  | SQLite + FTS5 (full-text index)      |
| LLM       | Claude Sonnet 4 (Anthropic API)      |
| UI        | Gradio                               |
| Parser    | PyMuPDF (PDF), python-docx, openpyxl |

## Database Schema

### documents (Main Table)

```sql
- id: STRING (SHA256 hash from connector:path)
- connector_name, file_path, file_name, file_type
- size_bytes, page_count, content_length
- content_text: TEXT (full document content!)
- content_hash: STRING (for change detection)
- status: pending|indexed|error|skipped
- indexed_at, created_at, modified_at
```

### documents_fts (FTS5 Virtual Table)

```sql
- doc_id, file_name, content
- Tokenizer: unicode61 (umlaut support)
```

## RLM Engine - How Does It Work?

1. **User asks question** → Chat UI or `/query` API
2. **Engine starts agentic loop** with Claude + tools
3. **Claude decides** which tools it needs:
   - Usually first `search_documents` with search terms
   - Then `read_document` for details
   - Iterates until answer is complete
4. **Answer with sources** is returned

**Max 10 iterations**, then abort with partial result.

## Performance & Costs

**Tested with ~600k files:**

- Scan + indexing: ~20 minutes
- Of which indexed: Only readable types (PDF, DOCX, etc.)
- FTS5 search: <100ms
- RLM query: 3-15 seconds (depending on complexity)

**Costs:**

| Operation                            | Cost          |
| ------------------------------------ | ------------- |
| Indexing                             | $0 (no LLM)   |
| Simple question                      | ~$0.02        |
| Complex question (many iterations)   | ~$0.05-0.10   |

## CLI Commands

```bash
# Start everything (API + UI)
python -m src.main

# API only
python -m src.main api --port 8000

# UI only
python -m src.main ui --port 7860

# Trigger index manually
python -m src.main index
python -m src.main index --full  # Force re-index
```

## Current Features

- [X] Index local filesystem
- [X] PDF, DOCX, XLSX, TXT, MD parser
- [X] SQLite FTS5 full-text search
- [X] Fuzzy search with trigram matching
- [X] RLM tool-use agent (Claude)
- [X] REST API with OpenAPI docs
- [X] Gradio Chat UI
- [X] **MCP Server** for Claude Desktop integration
- [X] Incremental indexing (hash-based)
- [X] WAL mode for better DB performance

## MCP Server (Claude Desktop Integration)

The RLM Knowledge Base can be run as an MCP server to use document search directly in Claude Desktop or other MCP-compatible clients.

### Install MCP Dependency

```bash
pip install mcp>=1.0.0
# or reinstall complete project:
pip install -e .
```

### Claude Desktop Configuration

Add to `%APPDATA%\Claude\claude_desktop_config.json`:

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

**Important:** The MCP server uses the same `config.yaml` and database as the main application.

### MCP Tools

Claude Desktop then has access to:

| Tool               | Description                      |
| ------------------ | -------------------------------- |
| `search_documents` | FTS5 full-text search with snippets |
| `search_fuzzy`     | Typo-tolerant fuzzy search       |
| `list_documents`   | Overview of all documents        |
| `read_document`    | Read document content            |
| `get_statistics`   | Database statistics              |

### Example Usage in Claude Desktop

After configuration, you can ask in Claude Desktop:

- *"Search my documents for vacation policies"*
- *"What does the project documentation say?"*
- *"List all PDF files"*

Claude automatically uses the MCP tools to search in your local document base.

### Start MCP Server Manually (for testing)

```bash
python -m src.mcp_server
```

## Planned Features

- [ ] n8n Custom Node
- [ ] OCR for scanned PDFs
- [ ] Cleanup endpoint for deleted files
- [ ] Ollama support for local LLMs

## Known Limitations

- **Scanned PDFs** without OCR layer cannot be read
- **Very large documents** (>50k characters) are read in parts
- **Rate limits** at Anthropic API (automatic retry)

---

*Project path: `C:\Users\chris\Documents\MyProjects\rlm-connector`*
*Last updated: January 2025*

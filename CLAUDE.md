# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

TMB Lost & Found — an AI-powered lost item tracker for public transport. Staff photograph found items, a VLM auto-describes them, passengers find items via a privacy-preserving LLM chatbot. Currently Phase 1: Staff App running locally.

## Running the App

```bash
# Requires Ollama with two models
ollama pull llava        # vision model for item detection
ollama pull llama3.2     # text model for search chatbot

# Terminal 1
ollama serve

# Terminal 2
python3 app.py           # serves on http://localhost:8080
```

**Zero Python dependencies** — uses only stdlib (`http.server`, `sqlite3`, `urllib`). No pip, no venv.

If port 8080 is busy: `lsof -ti:8080 | xargs kill`

## Architecture

**Two files, one page:**

- `app.py` — Python HTTP server + SQLite + Ollama integration. All API logic lives here.
- `index.html` — Single-page app with two tabs (Catalog / Find). All frontend logic is inline JS.

**Data flow:**

```
Camera/Upload → /api/detect → Ollama llava → auto-fill form fields
Form submit   → /api/entries (POST) → SQLite
Find chat     → /api/find → Ollama llama3.2 (receives full DB + conversation history)
```

**API endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/entries` | List all entries |
| POST | `/api/entries` | Create entry (optional `link_to` for connected items) |
| POST | `/api/entries/link` | Bidirectionally link two entries by ID |
| POST | `/api/detect` | Send base64 image to llava, get item description JSON |
| POST | `/api/find` | Send conversation array, get LLM match response |
| DELETE | `/api/entries/{id}` | Delete entry, clean up all connection references |

## Key Design Decisions

**Connected items** are stored as comma-separated entry IDs in the `connected_items` column. Links are always bidirectional — `add_connection()` updates both sides. The Find LLM sees resolved descriptions of connected items (not just IDs) via `resolve_connections()`.

**Privacy model:** The Find chatbot receives the entire database in its prompt but has strict instructions not to leak contents. Matching requires item type + color + (station OR date). The LLM never hints at what's stored.

**Colors are text-based** (e.g. "black", "light grey"), not hex codes. Both form inputs and VLM output use plain language.

**Debug:** Type `xyzzy` in the Find chat to dump the full database — bypasses the LLM entirely.

## DB Schema

Single table `entries` in SQLite (`entries.db`, gitignored):

```
id, station, item_type, connected_items, main_color, secondary_colors, perks, time, created_at
```

Schema is created automatically by `get_db()`. Delete `entries.db` to reset after schema changes.

## LLM Prompts

Two prompts defined as module-level strings in `app.py`:

- **`DETECT_PROMPT`** — Tells llava to describe items against a white background. Returns JSON with item_type, main_color, secondary_colors, perks.
- **`FIND_SYSTEM_PROMPT`** — Rules for the Find chatbot: fuzzy matching, privacy constraints, connected item awareness, match confirmation behavior. The full DB and conversation history are appended at call time.

Both prompts expect the LLM to return **only valid JSON**. Response parsing extracts the first `{...}` block.

## Roadmap

See `README.md` for the full 4-phase plan:
- Phase 1: Staff App local (current — SQLite + Ollama, add manual search + packaging)
- Phase 2: Cloud DB + Cloud LLM (PostgreSQL, S3, FastAPI, Claude/GPT API, multi-user)
- Phase 3: Passenger Web App (public-facing, privacy-preserving chatbot + claim form + info page)
- Phase 4: QR code labels with thermal printer integration

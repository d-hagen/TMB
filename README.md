# TMB Lost & Found — AI-Powered Lost Item Tracker

A lost-and-found system for public transport companies. Staff photograph found items, a local VLM (vision-language model) auto-describes them, and passengers retrieve items via an LLM chatbot that verifies ownership without leaking database contents.

**Current state:** Web-based prototype running on localhost with SQLite + Ollama (llava for vision, llama3.2 for text).

---

## Roadmap

### Phase 1 — Local Demo (Standalone App)

Single-user desktop/mobile app. No server, no internet required. SQLite on device, Ollama running locally.

#### 1.1 Package as Desktop App

- Wrap the existing Python server + HTML frontend using **Tauri** (Rust-based, lightweight) or **Electron**
- Tauri preferred: smaller binary (~5 MB vs ~150 MB), native webview, no bundled Chromium
- App starts the Python backend as a child process, opens the UI in a native window
- Alternative: **PyWebView** — pure Python, opens a native webview window, minimal packaging overhead
- Bundle Ollama or require it as a system dependency

#### 1.2 Package as Mobile App

- Use **Capacitor** (from Ionic) to wrap the existing HTML/JS frontend into a native iOS/Android shell
- Capacitor gives native access to camera, file system, notifications
- Backend options for mobile:
  - **On-device:** Bundle a lightweight ONNX/TFLite vision model for item detection (no Ollama needed). Text LLM via on-device inference (e.g. llama.cpp built for ARM)
  - **Hybrid:** Item detection on-device, LLM search calls a local network server or cloud endpoint
- SQLite works natively on both iOS and Android

#### 1.3 Deliverables

- [ ] Standalone `.dmg` / `.exe` / `.AppImage` for desktop
- [ ] iOS TestFlight / Android APK for mobile testing
- [ ] Local SQLite database, no network required
- [ ] Camera capture + photo upload for item detection
- [ ] Connected items workflow (scan multiple items, auto-link by ID)
- [ ] LLM chatbot for item search (runs locally)

---

### Phase 2 — Online Database (Multi-User)

Shared cloud database accessible from multiple devices. Multiple staff can add items, multiple passengers can search.

#### 2.1 Backend Architecture

```
                  ┌─────────────┐
                  │   Cloud DB   │
                  │  PostgreSQL  │
                  └──────┬──────┘
                         │
              ┌──────────┼──────────┐
              │          │          │
         ┌────┴────┐ ┌──┴───┐ ┌───┴────┐
         │ REST API │ │  S3  │ │ Ollama │
         │ FastAPI  │ │Images│ │ or API │
         └────┬────┘ └──────┘ └────────┘
              │
     ┌────────┼────────┐
     │        │        │
  Staff    Staff    Passenger
  App #1   App #2    App
```

- **Database:** PostgreSQL on AWS RDS / Supabase / Railway
- **Image storage:** S3 bucket (or Supabase Storage)
- **API server:** FastAPI (Python) — replaces the current `http.server`
  - Auth via API keys (staff) and anonymous/session-based (passengers)
  - Rate limiting on LLM endpoints
- **LLM inference:**
  - Option A: Self-hosted Ollama on a GPU instance (g4dn.xlarge)
  - Option B: Cloud API (Claude / GPT) — simpler, pay-per-use
  - Option C: Hybrid — vision model self-hosted, text LLM via cloud API

#### 2.2 Data Model (PostgreSQL)

```sql
entries (
  id            SERIAL PRIMARY KEY,
  station       TEXT NOT NULL,
  item_type     TEXT NOT NULL,
  main_color    TEXT,
  secondary_colors TEXT,
  perks         TEXT,
  image_url     TEXT,          -- S3 path
  time_found    TIMESTAMPTZ NOT NULL,
  created_by    INTEGER REFERENCES staff(id),
  status        TEXT DEFAULT 'available',  -- available / claimed / expired
  created_at    TIMESTAMPTZ DEFAULT NOW()
)

connections (
  id_a  INTEGER REFERENCES entries(id) ON DELETE CASCADE,
  id_b  INTEGER REFERENCES entries(id) ON DELETE CASCADE,
  PRIMARY KEY (id_a, id_b)
)

staff (
  id        SERIAL PRIMARY KEY,
  name      TEXT,
  station   TEXT,
  api_key   TEXT UNIQUE
)

claims (
  id          SERIAL PRIMARY KEY,
  entry_id    INTEGER REFERENCES entries(id),
  description TEXT,       -- what the passenger described
  contact     TEXT,       -- email or phone
  status      TEXT DEFAULT 'pending',  -- pending / approved / rejected
  created_at  TIMESTAMPTZ DEFAULT NOW()
)
```

#### 2.3 Deliverables

- [ ] FastAPI backend with auth, deployed to cloud
- [ ] PostgreSQL database with migrations
- [ ] S3 image storage
- [ ] Multi-user access — multiple staff adding items simultaneously
- [ ] Claim tracking — passengers submit claims, staff approve/reject
- [ ] Admin dashboard for managing staff accounts

---

### Phase 3 — Two Separate Applications

Split into **Staff App** (for transport workers) and **Passenger App** (for the public).

---

#### 3A — Staff App

For public transport lost-and-found workers. Full access to the database.

##### Screens

**1. Add Item (existing catalog screen)**
- Camera detect / photo upload with VLM auto-fill
- Connected items workflow (scan bag, then contents, auto-link)
- Manual field editing before save
- Station auto-filled from staff profile

**2. LLM Search (unredacted)**
- Same chat interface but with NO privacy restrictions
- Returns ALL matching or near-matching entries with full details
- Helps staff narrow down: "Show me all black cameras from this week"
- Shows confidence scores and explains why items match or don't
- Can search across connected item groups

**3. Manual Search**
- Filter/search table with one input per column:
  - Station (dropdown or text)
  - Item type (text, fuzzy)
  - Main color (text)
  - Secondary colors (text)
  - Perks/features (text)
  - Date range (from/to date pickers)
  - Status (available / claimed / expired)
  - Connected items (search within groups)
- Columns are combinable — e.g. station "L4" + color "black" + date range
- Results table with sorting, pagination
- Click an entry to see full details + connected items + claim history

**4. Claims Queue**
- List of passenger claims awaiting review
- Side-by-side: passenger's description vs. matched DB entry
- Approve / reject / request more info

##### Staff LLM Prompt Behavior
- Full database visibility, no redaction
- Returns multiple candidates ranked by match quality
- Explains reasoning: "This matches because: same item type, similar color, same station, time within 2 hours"
- Can handle staff queries like "show me everything from L4 today" or "any cameras this week?"

---

#### 3B — Passenger App

For the public. Privacy-preserving, no database browsing.

##### Screens

**1. Find My Item (LLM Chatbot)**
- Current chat interface with privacy-preserving LLM
- Strict matching rules — must describe item accurately enough
- Never reveals database contents
- On match: shows the item entry + station to collect from
- On no match: offers to submit a claim/alert

**2. Submit a Request (Claim Form)**
- Structured form as alternative to chatbot:
  - Item type
  - Main color / secondary colors
  - When lost (date + rough time)
  - Where lost (station/line)
  - Distinguishing features
  - Contact info (email / phone)
- Submitted as a claim — staff reviews and responds
- Passenger gets a claim reference number

**3. Info Page**
- How the system works (brief explanation)
- Which stations participate
- What to do if your item isn't found
- Average turnaround time
- Contact info for lost-and-found offices
- FAQ (how long are items kept, what items are accepted, etc.)
- Privacy policy — what data is stored, how long, who can see it

---

## Tech Stack Summary

| Component | Phase 1 (Local) | Phase 2 (Online) | Phase 3 (Split Apps) |
|-----------|-----------------|-------------------|----------------------|
| Frontend | HTML/JS | HTML/JS | Staff: Tauri/Capacitor, Passenger: Capacitor/PWA |
| Backend | Python http.server | FastAPI | FastAPI + WebSockets |
| Database | SQLite | PostgreSQL | PostgreSQL |
| Images | On disk | S3 | S3 |
| Vision LLM | Ollama (llava) | Ollama or Cloud API | Cloud API |
| Text LLM | Ollama (llama3.2) | Ollama or Cloud API | Cloud API |
| Auth | None | API keys | API keys (staff) + anonymous (passenger) |
| Deployment | Local | AWS / Railway / Fly.io | Same + app stores |

---

## Running the Current Prototype

```bash
# Prerequisites
# 1. Install Ollama: https://ollama.com
# 2. Pull models:
ollama pull llava
ollama pull llama3.2

# Start
cd TMB
ollama serve          # in one terminal
python3 app.py        # in another terminal

# Open http://localhost:8080
```

### Features (current)

- **Catalog tab:** Add lost items via camera detection or photo upload. VLM auto-fills item type, colors, and features. Connected items workflow for grouping (e.g. bag + contents).
- **Find tab:** Chat with an LLM that checks your description against the database. Privacy-preserving — never leaks stored item details. Matches bidirectionally through connected items.
- **Debug:** Type `xyzzy` in the Find chat to dump the full database.

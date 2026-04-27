# TMB Lost & Found — AI-Powered Lost Item Tracker

A lost-and-found system for public transport companies. Staff photograph found items, a VLM (vision-language model) auto-describes them, and passengers retrieve items via an LLM chatbot that verifies ownership without leaking database contents.

**Current state:** Phase 1 prototype — Staff App running on localhost with SQLite + Ollama.

---

## Roadmap

### Phase 1 — Staff App (Local)

Desktop/mobile app for lost-and-found workers. Runs fully offline — local database, local LLM. This is the foundation everything else builds on.

#### 1.1 Screens

**Add Item (Catalog)**
- Camera detect / photo upload — VLM (Ollama llava) auto-fills item type, colors, features
- Connected items workflow: scan a bag, then its contents — items auto-link by ID bidirectionally
- Manual field editing before save
- Fields: station, item type, main color, secondary colors, perks, time

**LLM Search (Unredacted)**
- Chat interface with full database visibility — no privacy restrictions
- Returns ALL matching or near-matching entries with full details
- Helps staff narrow down: "Show me all black cameras from this week"
- Explains reasoning: "This matches because: same item type, similar color, same station"
- Searches across connected item groups

**Manual Search**
- Filter/search table with one input per column:
  - Station (dropdown or text)
  - Item type (text, fuzzy)
  - Main color / secondary colors (text)
  - Perks/features (text)
  - Date range (from/to pickers)
  - Status (available / claimed / expired)
  - Connected items (search within groups)
- Columns are combinable — e.g. station "L4" + color "black" + date range
- Results table with sorting, pagination
- Click an entry to see full details + connected items

**Entries Table**
- All items listed with ID, station, type, colors, connections, perks, time
- Inline delete, edit on click

#### 1.2 Tech Stack

| Component | Choice |
|-----------|--------|
| Frontend | HTML/JS (single-page app) |
| Backend | Python `http.server` (stdlib, zero dependencies) |
| Database | SQLite (local file) |
| Vision LLM | Ollama llava (local) |
| Text LLM | Ollama llama3.2 (local) |
| Auth | None (single user) |
| Packaging | Tauri (desktop) or Capacitor (mobile) |

#### 1.3 Deliverables

- [x] Camera capture + photo upload with VLM auto-detection
- [x] Connected items workflow (scan multiple items, auto-link by ID)
- [x] LLM chatbot search (currently privacy-preserving — will become unredacted staff version)
- [ ] Manual search / filter page
- [ ] Entry detail view with edit capability
- [ ] Package as standalone desktop app (Tauri / PyWebView)
- [ ] Package as mobile app (Capacitor)

---

### Phase 2 — Cloud Database & Cloud LLM (Multi-User)

Move from local SQLite + Ollama to shared cloud infrastructure. Multiple staff at different stations can access and modify the same database simultaneously.

#### 2.1 Architecture

```
                  ┌─────────────┐
                  │   Cloud DB   │
                  │  PostgreSQL  │
                  └──────┬──────┘
                         │
              ┌──────────┼──────────┐
              │          │          │
         ┌────┴────┐ ┌──┴───┐ ┌───┴─────┐
         │ REST API │ │  S3  │ │Cloud LLM│
         │ FastAPI  │ │Images│ │Claude/  │
         └────┬────┘ └──────┘ │GPT API  │
              │               └─────────┘
     ┌────────┼────────┐
     │        │        │
  Staff    Staff    Staff
  App #1   App #2   App #3
```

#### 2.2 Backend Migration

- **Database:** PostgreSQL on AWS RDS / Supabase / Railway
- **Image storage:** S3 bucket (photos saved alongside entries)
- **API server:** FastAPI (Python) — replaces `http.server`
  - Auth via API keys per staff member
  - Rate limiting on LLM endpoints
- **Conflict handling:** Optimistic locking for simultaneous edits

#### 2.3 Data Model (PostgreSQL)

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

#### 2.4 Replace Local LLM/VLM with Cloud API

Remove the Ollama dependency entirely. Staff apps no longer need local GPU hardware.

**Vision (item detection):**
- Replace Ollama llava with **Claude API** (claude-sonnet-4-5 supports vision) or **OpenAI GPT-4o**
- Send base64 image + detection prompt via REST API
- Faster, more accurate, no local model download
- Cost: ~$0.01–0.03 per image analysis

**Text LLM (staff search + passenger chatbot):**
- Replace Ollama llama3.2 with **Claude API** (claude-sonnet-4-5 or claude-haiku-4-5) or **OpenAI GPT-4o-mini**
- Same prompt structure, swap the HTTP endpoint and payload format
- Sonnet/GPT-4o for staff search (smarter), Haiku/GPT-4o-mini for passenger chatbot (cheaper)
- Cost: ~$0.002–0.01 per conversation turn

**Implementation:**
- Abstract LLM calls behind a provider interface (swap Ollama / Claude / OpenAI via config)
- API keys stored as environment variables
- Retry logic and timeouts for cloud calls
- Optional: keep Ollama as offline fallback

**Cost estimate at scale (80k entries/year, ~200 lookups/day):**
- Vision (50 new items/day): ~$30–45/month
- Text LLM (200 lookups/day, ~3 turns each): ~$20–40/month
- **Total LLM API cost: ~$50–85/month** — far cheaper than a GPU instance ($350–500/month)

#### 2.5 Deliverables

- [ ] FastAPI backend with staff auth, deployed to cloud
- [ ] PostgreSQL database with migrations
- [ ] S3 image storage
- [ ] Cloud LLM provider integration (Claude or OpenAI)
- [ ] Multi-user access — multiple staff adding/editing items simultaneously
- [ ] Admin dashboard for managing staff accounts

---

### Phase 3 — Passenger Web App

Public-facing **web application** (not a native app — accessible from any browser, no install required). Passengers use this to find their lost items.

#### 3.1 Screens

**Find My Item (LLM Chatbot)**
- Chat interface with privacy-preserving LLM
- Strict matching rules — must describe item accurately enough
- Never reveals database contents
- On match: shows item type, station to collect from, and status
- On no match: offers to submit a claim/alert

**Submit a Request (Claim Form)**
- Structured form as alternative to chatbot:
  - Item type
  - Main color / secondary colors
  - When lost (date + rough time)
  - Where lost (station/line)
  - Distinguishing features
  - Contact info (email / phone)
- Submitted as a claim — staff reviews via Claims Queue in Staff App
- Passenger gets a claim reference number
- Can check claim status by reference number

**Info Page**
- How the system works (brief explanation)
- Which stations participate
- What to do if your item isn't found
- Average turnaround time
- Contact info for lost-and-found offices
- FAQ (how long are items kept, what items are accepted, etc.)
- Privacy policy — what data is stored, how long, who can see it

#### 3.2 Staff App Additions (Claims Queue)

When the passenger app goes live, the Staff App gets a new screen:

**Claims Queue**
- List of passenger claims awaiting review
- Side-by-side: passenger's description vs. matched DB entry
- Approve / reject / request more info
- Notify passenger of outcome (email / SMS)

#### 3.3 Tech Stack

| Component | Choice |
|-----------|--------|
| Frontend | Responsive web app (HTML/JS or lightweight framework) |
| Hosting | Static site on Vercel / Netlify / S3+CloudFront |
| Backend | Same FastAPI from Phase 2 |
| Auth | Anonymous / session-based (no login required) |
| LLM | Cloud API (Haiku/GPT-4o-mini for cost efficiency) |

#### 3.4 Deliverables

- [ ] Passenger web app — responsive, mobile-friendly, no install
- [ ] LLM chatbot with privacy-preserving matching
- [ ] Claim submission form with reference number tracking
- [ ] Info / FAQ page
- [ ] Claims Queue screen added to Staff App
- [ ] Notification system for claim updates (email / SMS)

---

### Phase 4 — QR Code Physical Labeling

Every cataloged item gets a printed QR code label. Bridges physical items to digital records. Enables instant lookup and status updates.

#### 4.1 Workflow

```
Staff scans item → VLM detects → staff confirms & saves
        │
        ▼
  Entry created (ID #247)
        │
        ▼
  QR code auto-generated (encodes URL: app.example.com/i/247)
        │
        ▼
  Sent to connected QR label printer (Bluetooth/USB thermal printer)
        │
        ▼
  Label attached to item or storage bag
```

#### 4.2 QR Code Behavior

**Staff scans QR code:**
- Opens the full entry in Staff App — editable
- Can update status (available / claimed / expired), add notes, modify fields
- See connected items, claim history, and photo

**Passenger scans QR code:**
- Opens a read-only view on the Passenger Web App
- Shows limited info: item type, station, date found, status
- "This is mine" button → opens claim form pre-filled with entry ID
- No sensitive details exposed

**Staff re-encounters item later:**
- Scan QR instead of searching the database
- Quickly mark as claimed, transfer to another station, or flag as expired

#### 4.3 QR Code Generation

- Generate QR as SVG/PNG on the backend when an entry is created
- Library: `qrcode` (Python) or `qrcodejs` (frontend)
- Encode a short URL: `https://app.example.com/i/{short_hash}`
- Short hash avoids exposing sequential IDs

#### 4.4 Printer Integration

- **Thermal label printers** (e.g. Brother QL-series, DYMO, Zebra)
- Connect via:
  - **Bluetooth** — mobile staff app sends print job directly
  - **USB** — desktop staff app prints via system print dialog
  - **Network** — shared printer at the station, API sends print job
- Print format: QR code + human-readable text (item type, ID, date, station)
- Label size: standard 62mm or 29mm thermal labels

#### 4.5 Deliverables

- [ ] QR code auto-generated on entry creation
- [ ] Print button in Staff App — sends to connected label printer
- [ ] Staff scan: opens editable entry view
- [ ] Public scan: opens limited read-only view + claim button
- [ ] Printer pairing settings in Staff App (Bluetooth/USB/network)
- [ ] Batch print option for labeling a backlog of items

---

## Tech Stack Summary

| Component | Phase 1 (Staff Local) | Phase 2 (Cloud Multi-User) | Phase 3 (Passenger Web) | Phase 4 (QR Labels) |
|-----------|----------------------|---------------------------|------------------------|---------------------|
| Staff Frontend | HTML/JS | HTML/JS | + Claims Queue | + QR scan/print |
| Passenger Frontend | — | — | Responsive web app | + QR scan view |
| Backend | Python http.server | FastAPI | Same | + QR generation |
| Database | SQLite | PostgreSQL | Same | + short URL hashes |
| Images | On disk | S3 | Same | Same |
| Vision LLM | Ollama (llava) | Cloud API | — | — |
| Text LLM | Ollama (llama3.2) | Cloud API | Cloud API | — |
| Auth | None | Staff API keys | Anonymous | + public scan tokens |
| Hardware | — | — | — | Thermal label printer |
| Deployment | Local | AWS / Railway | Vercel / Netlify | Same |

---

## Running the Current Prototype (Phase 1)

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

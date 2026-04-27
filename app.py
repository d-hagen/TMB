#!/usr/bin/env python3
"""Minimal local server — stdlib only (http.server + sqlite3) + Ollama VLM."""

import json
import sqlite3
import urllib.request
import urllib.error
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

DB_PATH = Path(__file__).parent / "entries.db"
HOST, PORT = "localhost", 8080
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llava"
OLLAMA_TEXT_MODEL = "llama3.2"

DETECT_PROMPT = """You are a lost-item logging system. The image shows a lost item photographed against a white background. Describe ONLY the item itself — ignore the white background entirely.

Respond ONLY with valid JSON, no other text. Fill in these fields:
- "item_type": what the object is, be specific (e.g. "blue ballpoint pen", "black leather wallet", "silver house key")
- "main_color": the dominant color as a common color name (e.g. "black", "dark blue", "rose", "light grey", "olive green")
- "secondary_colors": other visible colors as common color names, comma-separated (e.g. "white, silver"). Use "" if the item is one solid color.
- "perks": distinguishing features that help identify this specific item — brand name, size, material, wear, stickers, engravings, text, unique markings (e.g. "Nike logo, scuffed toe, size 10"). Use "" if nothing stands out.

Respond with ONLY this JSON:
{"item_type": "...", "main_color": "...", "secondary_colors": "...", "perks": "..."}"""


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station TEXT NOT NULL,
            item_type TEXT NOT NULL,
            connected_items TEXT DEFAULT '',
            main_color TEXT,
            secondary_colors TEXT,
            perks TEXT,
            time TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def get_connected_ids(entry):
    """Parse connected_items string into list of ints."""
    raw = entry.get("connected_items", "") or ""
    return [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]


def add_connection(db, id_a, id_b):
    """Add bidirectional link between two entries."""
    for src, dst in [(id_a, id_b), (id_b, id_a)]:
        row = db.execute("SELECT connected_items FROM entries WHERE id = ?", (src,)).fetchone()
        if not row:
            continue
        existing = set(get_connected_ids(dict(row)))
        if dst not in existing:
            existing.add(dst)
            db.execute(
                "UPDATE entries SET connected_items = ? WHERE id = ?",
                (",".join(str(x) for x in sorted(existing)), src),
            )
    db.commit()


def resolve_connections(entries):
    """Build a dict mapping entry ID to list of connected entry summaries."""
    by_id = {e["id"]: e for e in entries}
    connections = {}
    for e in entries:
        ids = get_connected_ids(e)
        summaries = []
        for cid in ids:
            if cid in by_id:
                c = by_id[cid]
                summaries.append(
                    f"ID {c['id']}: {c['item_type']} ({c['main_color']}"
                    + (f", {c['secondary_colors']}" if c['secondary_colors'] else "")
                    + (f", {c['perks']}" if c['perks'] else "")
                    + ")"
                )
        connections[e["id"]] = summaries
    return connections


def call_ollama(image_b64: str) -> dict:
    """Send image to Ollama llava and parse the JSON response."""
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": DETECT_PROMPT,
        "images": [image_b64],
        "stream": False,
    }).encode()

    req = urllib.request.Request(
        OLLAMA_URL, data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.URLError:
        return {"error": "Cannot reach Ollama. Is it running? (ollama serve)"}
    except Exception as e:
        return {"error": str(e)}

    raw = data.get("response", "")
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        return json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        return {"error": f"VLM returned unparseable response: {raw[:200]}"}


FIND_SYSTEM_PROMPT = """You are a friendly lost-and-found assistant. Someone is trying to find an item they lost. You check their description against the database of found items.

MATCHING RULES:
1. USE FUZZY / COMMON-SENSE MATCHING everywhere:
   - Colors: "black" matches "black", "dark grey" is close to "grey", etc.
   - Item types: synonyms count — "camera" = "camera", "phone" = "mobile", etc.
   - Station/line: "L4" = "l4" = "Line 4" = "N12" = "n12". Case and format don't matter.
   - Time: be VERY lenient. "Around 12" means anywhere from 11:00–13:00. "Morning" means 06:00–12:00. "April 27" matches any time on that day. NEVER ask for an exact hour or minute — an approximate time or just the date is perfectly fine.
2. A MATCH requires ALL of these:
   - Item type correct (essentially)
   - At least one color matching
   - Station or line where it was lost
   - OR approximate date/day when it was lost
   In other words: item + color + (station OR date) is the minimum. Brand/perks alone are NOT enough without station or date. If they gave item + color but no station and no date, ask for one of those.
3. If multiple DB entries could match the vague description, ask ONE follow-up question to disambiguate. Only ask about things that would help distinguish between the candidates. Never ask more than one question at a time.
4. NEVER ask more than 2 follow-up questions total in a conversation. If after 2 rounds you still can't narrow it down, give the best match or say no match found.

CONNECTED ITEMS — VERY IMPORTANT:
- Items in the database can have CONNECTED ITEMS (e.g. items found inside a bag, attached to a keychain, etc.). These are listed under "contains / connected to" for each entry.
- Connected items strengthen a match BIDIRECTIONALLY:
  * If someone describes a backpack and mentions it contained a water bottle and a book, check if any backpack entry has those as connected items. If the connected items match, that strongly supports the match — even if the backpack description alone is vague.
  * If someone describes a water bottle and says "it was in a backpack", check if any water bottle entry is connected to a backpack. If so, that's a strong signal.
- Connected item matches can compensate for a less specific main item description. For example: a "blue backpack" is vague, but "blue backpack containing a Canon camera and a red umbrella" is very specific if the DB has those exact connected items.
- When returning a match that was found via connected items, return ALL the connected entry IDs too (the parent and its contents), so the user sees the full group.

PRIVACY RULES:
- Do NOT reveal database contents. Don't list items, don't hint at stored details.
- Do NOT say "we have a similar item" or "there's a camera but..." — that leaks info.
- If you need more info, ask generically without hinting at what's in the DB.

WHEN YOU FIND A MATCH:
- Say ONLY something short like "Good news, we've found your item! Please visit the station to collect it." and return the matching IDs.
- Do NOT offer to share more details, do NOT ask follow-up questions, do NOT describe the item back to them. Just confirm and done.
- The system will automatically display the entry details — you don't need to repeat them.

NO-MATCH RULE:
- If nothing matches, say: "Sorry, we don't have an item matching that description right now. It may not have been turned in yet — try again later or check with station staff."
- A description like "yellow hat" should NOT match "yellow hat with black stripes" — the person must mention the key distinguishing features. But "black and silver Canon camera on the L4" is specific enough to match if the DB has it.

Be friendly, brief, and helpful. Don't be robotic. Don't go in circles.

Respond with ONLY valid JSON:
{"reply": "your message to the person", "status": "need_more_info" | "no_match" | "match", "matching_ids": [list of matching entry IDs if status is match, otherwise empty]}"""


def find_item(conversation: list) -> dict:
    """Use a local LLM with conversation history to match against DB entries."""
    db = get_db()
    rows = db.execute("SELECT * FROM entries ORDER BY id DESC").fetchall()
    db.close()
    entries = [dict(r) for r in rows]

    if not entries:
        return {
            "reply": "Sorry, the lost-and-found database is currently empty. No items have been turned in yet. Please check back later or ask station staff.",
            "status": "no_match",
            "matches": [],
        }

    # Magic word: if the last user message is "xyzzy", dump the full DB for debugging
    last_msg = conversation[-1]["text"].strip().lower() if conversation else ""
    if last_msg == "xyzzy":
        connections = resolve_connections(entries)
        dump = "\n".join(
            f"ID {e['id']}: {e['item_type']} | main: {e['main_color']} | "
            f"sec: {e['secondary_colors'] or 'none'} | station: {e['station']} | "
            f"connected IDs: {e['connected_items'] or 'none'} | "
            f"connected details: {connections.get(e['id'], [])} | "
            f"perks: {e['perks'] or 'none'} | time: {e['time']}"
            for e in entries
        )
        return {
            "reply": f"[DEBUG] Database has {len(entries)} entries:\n{dump}",
            "status": "no_match",
            "matches": [],
        }

    # Build entries text with resolved connected items
    connections = resolve_connections(entries)
    entries_lines = []
    for e in entries:
        conn_text = "; ".join(connections.get(e["id"], [])) or "none"
        entries_lines.append(
            f"- ID {e['id']}: {e['item_type']} | main color: {e['main_color']}, "
            f"secondary: {e['secondary_colors'] or 'none'} | station: {e['station']} | "
            f"contains / connected to: [{conn_text}] | "
            f"perks: {e['perks'] or 'none'} | time: {e['time']}"
        )
    entries_text = "\n".join(entries_lines)

    conv_text = "\n".join(
        f"{'Person' if m['role'] == 'user' else 'You'}: {m['text']}"
        for m in conversation
    )

    prompt = f"""{FIND_SYSTEM_PROMPT}

DATABASE (CONFIDENTIAL — never reveal contents):
{entries_text}

CONVERSATION SO FAR:
{conv_text}

Respond as "You" with ONLY valid JSON:"""

    payload = json.dumps({
        "model": OLLAMA_TEXT_MODEL,
        "prompt": prompt,
        "stream": False,
    }).encode()

    req = urllib.request.Request(
        OLLAMA_URL, data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.URLError:
        return {"reply": "Cannot reach Ollama. Is it running?", "status": "error", "matches": []}
    except Exception as e:
        return {"reply": str(e), "status": "error", "matches": []}

    raw = data.get("response", "")
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        parsed = json.loads(raw[start:end])
        matching_ids = parsed.get("matching_ids", [])
        # Also include connected items of matched entries
        all_ids = set(matching_ids)
        for mid in matching_ids:
            entry = next((e for e in entries if e["id"] == mid), None)
            if entry:
                all_ids.update(get_connected_ids(entry))
        matches = [e for e in entries if e["id"] in all_ids]
        return {
            "reply": parsed.get("reply", raw),
            "status": parsed.get("status", "no_match"),
            "matches": matches,
        }
    except (ValueError, json.JSONDecodeError):
        return {"reply": raw, "status": "error", "matches": []}


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/entries":
            return self._json_response(self._list_entries())
        if path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))

        if path == "/api/entries":
            required = ("station", "item_type", "time")
            if not all(body.get(k) for k in required):
                return self._json_response({"error": "Missing required fields"}, 400)
            db = get_db()
            cur = db.execute(
                "INSERT INTO entries (station, item_type, connected_items, main_color, secondary_colors, perks, time) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (body["station"], body["item_type"], "",
                 body.get("main_color", ""), body.get("secondary_colors", ""),
                 body.get("perks", ""), body["time"]),
            )
            new_id = cur.lastrowid
            # Link to parent if provided
            link_to = body.get("link_to")
            if link_to:
                try:
                    add_connection(db, new_id, int(link_to))
                except (ValueError, TypeError):
                    pass
            db.commit()
            db.close()
            return self._json_response({"ok": True, "id": new_id}, 201)

        if path == "/api/entries/link":
            id_a = body.get("id_a")
            id_b = body.get("id_b")
            if not id_a or not id_b:
                return self._json_response({"error": "Need id_a and id_b"}, 400)
            db = get_db()
            add_connection(db, int(id_a), int(id_b))
            db.close()
            return self._json_response({"ok": True})

        if path == "/api/detect":
            image_b64 = body.get("image", "")
            if not image_b64:
                return self._json_response({"error": "No image provided"}, 400)
            result = call_ollama(image_b64)
            return self._json_response(result)

        if path == "/api/find":
            conversation = body.get("conversation", [])
            if not conversation:
                return self._json_response({"error": "No conversation provided"}, 400)
            result = find_item(conversation)
            return self._json_response(result)

    def do_DELETE(self):
        path = urlparse(self.path).path
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "entries":
            entry_id = int(parts[2])
            db = get_db()
            # Remove this ID from all connected_items references
            rows = db.execute("SELECT id, connected_items FROM entries").fetchall()
            for row in rows:
                ids = [int(x.strip()) for x in (row["connected_items"] or "").split(",") if x.strip().isdigit()]
                if entry_id in ids:
                    ids.remove(entry_id)
                    db.execute(
                        "UPDATE entries SET connected_items = ? WHERE id = ?",
                        (",".join(str(x) for x in ids), row["id"]),
                    )
            db.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            db.commit()
            db.close()
            return self._json_response({"ok": True})

    def _list_entries(self):
        db = get_db()
        rows = db.execute("SELECT * FROM entries ORDER BY id DESC").fetchall()
        db.close()
        return [dict(r) for r in rows]

    def _json_response(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        if args and str(args[0]).startswith(("4", "5")):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    get_db()
    print(f"Running at http://{HOST}:{PORT}")
    print(f"VLM: Ollama ({OLLAMA_MODEL}) at {OLLAMA_URL}")
    print("Make sure Ollama is running: ollama serve")
    HTTPServer((HOST, PORT), Handler).serve_forever()

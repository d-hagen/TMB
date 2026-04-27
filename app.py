#!/usr/bin/env python3
"""Minimal local server — stdlib only (http.server + sqlite3) + Ollama VLM."""

import base64
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
- "connected_items": any other distinct items visible alongside the main item, comma-separated (e.g. "keychain, lanyard"). Use "" if there is only one item.
- "perks": distinguishing features that help identify this specific item — brand name, size, material, wear, stickers, engravings, text, unique markings (e.g. "Nike logo, scuffed toe, size 10"). Use "" if nothing stands out.

Respond with ONLY this JSON:
{"item_type": "...", "main_color": "...", "secondary_colors": "...", "connected_items": "...", "perks": "..."}"""


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station TEXT NOT NULL,
            item_type TEXT NOT NULL,
            connected_items TEXT,
            main_color TEXT,
            secondary_colors TEXT,
            perks TEXT,
            time TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def call_ollama(image_b64: str) -> dict:
    """Send image to Ollama llava and parse the JSON response."""
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": DETECT_PROMPT,
        "images": [image_b64],
        "stream": False,
    }).encode()

    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
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

PRIVACY RULES:
- Do NOT reveal database contents. Don't list items, don't hint at stored details.
- Do NOT say "we have a similar item" or "there's a camera but..." — that leaks info.
- If you need more info, ask generically (e.g. "Could you tell me roughly when you lost it?") without hinting at what's in the DB.

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
        dump = "\n".join(
            f"ID {e['id']}: {e['item_type']} | main: {e['main_color']} | "
            f"sec: {e['secondary_colors'] or 'none'} | station: {e['station']} | "
            f"connected: {e['connected_items'] or 'none'} | perks: {e['perks'] or 'none'} | "
            f"time: {e['time']}"
            for e in entries
        )
        return {
            "reply": f"[DEBUG] Database has {len(entries)} entries:\n{dump}",
            "status": "no_match",
            "matches": [],
        }

    entries_text = "\n".join(
        f"- ID {e['id']}: {e['item_type']} | main color: {e['main_color']}, "
        f"secondary: {e['secondary_colors'] or 'none'} | station: {e['station']} | "
        f"connected items: {e['connected_items'] or 'none'} | perks: {e['perks'] or 'none'} | "
        f"time: {e['time']}"
        for e in entries
    )

    # Build conversation into a single prompt
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
        OLLAMA_URL,
        data=payload,
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
        matches = [e for e in entries if e["id"] in matching_ids]
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
            db.execute(
                "INSERT INTO entries (station, item_type, connected_items, main_color, secondary_colors, perks, time) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (body["station"], body["item_type"], body.get("connected_items", ""),
                 body.get("main_color", ""), body.get("secondary_colors", ""),
                 body.get("perks", ""), body["time"]),
            )
            db.commit()
            db.close()
            return self._json_response({"ok": True}, 201)

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

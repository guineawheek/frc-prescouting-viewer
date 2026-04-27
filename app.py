import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from flask import Flask, jsonify, render_template, request
import requests

app = Flask(__name__)

with open("tba_api.json") as f:
    TBA_KEY = json.load(f)["key"]

TBA_BASE = "https://www.thebluealliance.com/api/v3"
STATBOTICS_BASE = "https://api.statbotics.io"
DB_PATH = "epa_cache.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS epa_cache (
                event_code TEXT NOT NULL,
                team_number INTEGER NOT NULL,
                epa REAL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (event_code, team_number)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_name_cache (
                event_code TEXT PRIMARY KEY,
                name TEXT NOT NULL
            )
            """
        )


init_db()


def tba_get(path):
    r = requests.get(
        f"{TBA_BASE}{path}",
        headers={"X-TBA-Auth-Key": TBA_KEY},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _extract_epa(raw):
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, dict):
        # Prefer season-specific game-point EPA over cumulative/normalized values
        tp = raw.get("total_points")
        if isinstance(tp, dict):
            val = tp.get("mean")
            if isinstance(val, (int, float)):
                return float(val)
        for field in ("mean", "norm"):
            val = raw.get(field)
            if isinstance(val, (int, float)):
                return float(val)
    return None


def _fetch_and_cache_epa(event_code):
    try:
        r = requests.get(
            f"{STATBOTICS_BASE}/v3/team_events",
            params={"event": event_code, "limit": 500},
            timeout=15,
        )
        if not r.ok:
            return {}
        data = r.json()
    except Exception:
        return {}

    epa_map = {}
    rows = []
    now = datetime.utcnow().isoformat()
    for item in data:
        team = item.get("team")
        epa = _extract_epa(item.get("epa"))
        if team is not None and epa is not None:
            epa_map[int(team)] = epa
            rows.append((event_code, int(team), epa, now))

    if rows:
        with get_db() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO epa_cache (event_code, team_number, epa, updated_at) VALUES (?, ?, ?, ?)",
                rows,
            )
    return epa_map


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/event/<event_code>/teams")
def get_teams(event_code):
    try:
        teams = tba_get(f"/event/{event_code}/teams/simple")
    except Exception as e:
        return jsonify({"error": f"TBA error: {e}"}), 502

    with get_db() as conn:
        rows = conn.execute(
            "SELECT team_number, epa FROM epa_cache WHERE event_code = ?",
            (event_code,),
        ).fetchall()
    cached = {r["team_number"]: r["epa"] for r in rows}

    if not cached:
        cached = _fetch_and_cache_epa(event_code)

    result = []
    for t in teams:
        n = t["team_number"]
        parts = [t.get("city"), t.get("state_prov"), t.get("country")]
        location = ", ".join(p for p in parts if p)
        result.append(
            {
                "team_number": n,
                "nickname": t.get("nickname") or f"Team {n}",
                "location": location,
                "epa": cached.get(n),
            }
        )

    return jsonify(result)


@app.route("/api/event/<event_code>/refresh", methods=["POST"])
def refresh_epa(event_code):
    with get_db() as conn:
        conn.execute("DELETE FROM epa_cache WHERE event_code = ?", (event_code,))
    epa_map = _fetch_and_cache_epa(event_code)
    if not epa_map:
        return jsonify({"error": "Statbotics unavailable or returned no data"}), 502
    return jsonify({"updated": len(epa_map)})


@app.route("/api/team/<int:team_number>/matches/<int:year>")
def get_team_matches(team_number, year):
    try:
        matches = tba_get(f"/team/frc{team_number}/matches/{year}")
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    return jsonify(matches)


@app.route("/api/events/names")
def get_event_names():
    keys = [k.strip() for k in request.args.get("keys", "").split(",") if k.strip()][:20]
    if not keys:
        return jsonify({})

    with get_db() as conn:
        rows = conn.execute(
            f"SELECT event_code, name FROM event_name_cache WHERE event_code IN ({','.join('?'*len(keys))})",
            keys,
        ).fetchall()
    result = {r["event_code"]: r["name"] for r in rows}

    missing = [k for k in keys if k not in result]
    if not missing:
        return jsonify(result)

    def fetch_name(key):
        try:
            event = tba_get(f"/event/{key}/simple")
            return key, event.get("name", key)
        except Exception:
            return key, key

    with ThreadPoolExecutor(max_workers=min(len(missing), 6)) as ex:
        fetched = dict(ex.map(fetch_name, missing))

    with get_db() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO event_name_cache (event_code, name) VALUES (?, ?)",
            fetched.items(),
        )

    result.update(fetched)
    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, port=5000)

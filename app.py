import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
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
                name TEXT NOT NULL,
                week INTEGER,
                start_date TEXT
            )
            """
        )
        cols = {row[1] for row in conn.execute("PRAGMA table_info(event_name_cache)")}
        if "week" not in cols:
            conn.execute("ALTER TABLE event_name_cache ADD COLUMN week INTEGER")
            conn.execute("DELETE FROM event_name_cache")
        if "start_date" not in cols:
            conn.execute("ALTER TABLE event_name_cache ADD COLUMN start_date TEXT")
            conn.execute("DELETE FROM event_name_cache")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS team_name_cache (
                team_number INTEGER PRIMARY KEY,
                nickname TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_teams (
                event_code TEXT NOT NULL,
                team_number INTEGER NOT NULL,
                PRIMARY KEY (event_code, team_number)
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
    now = datetime.now(timezone.utc).isoformat()
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
            f"SELECT event_code, name, week, start_date FROM event_name_cache WHERE event_code IN ({','.join('?'*len(keys))})",
            keys,
        ).fetchall()
    result = {r["event_code"]: {"name": r["name"], "week": r["week"], "start_date": r["start_date"]} for r in rows}

    missing = [k for k in keys if k not in result]
    if not missing:
        return jsonify(result)

    def fetch_name(key):
        try:
            event = tba_get(f"/event/{key}/simple")
            return key, event.get("name", key), event.get("week"), event.get("start_date")
        except Exception:
            return key, key, None, None

    with ThreadPoolExecutor(max_workers=min(len(missing), 6)) as ex:
        fetched_rows = list(ex.map(fetch_name, missing))

    with get_db() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO event_name_cache (event_code, name, week, start_date) VALUES (?, ?, ?, ?)",
            fetched_rows,
        )

    for key, name, week, start_date in fetched_rows:
        result[key] = {"name": name, "week": week, "start_date": start_date}
    return jsonify(result)


@app.route("/api/teams/names")
def get_team_names():
    event_keys = [k.strip() for k in request.args.get("events", "").split(",") if k.strip()][:20]
    if not event_keys:
        return jsonify({})

    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    # An event is fresh if it has at least one team cached within the TTL window.
    # Events with no mapping or only stale entries need a bulk fetch.
    with get_db() as conn:
        fresh = set()
        for ek in event_keys:
            row = conn.execute(
                """SELECT 1 FROM event_teams et
                   JOIN team_name_cache tnc ON et.team_number = tnc.team_number
                   WHERE et.event_code = ? AND tnc.updated_at >= ? LIMIT 1""",
                (ek, cutoff),
            ).fetchone()
            if row:
                fresh.add(ek)

    stale = [ek for ek in event_keys if ek not in fresh]

    if stale:
        def fetch_event(ek):
            try:
                return ek, tba_get(f"/event/{ek}/teams/simple")
            except Exception:
                return ek, []

        with ThreadPoolExecutor(max_workers=min(len(stale), 6)) as ex:
            fetched = list(ex.map(fetch_event, stale))

        now = datetime.now(timezone.utc).isoformat()
        with get_db() as conn:
            for ek, teams in fetched:
                if not teams:
                    continue
                conn.executemany(
                    "INSERT OR REPLACE INTO team_name_cache (team_number, nickname, updated_at) VALUES (?, ?, ?)",
                    [(t["team_number"], t.get("nickname") or f"Team {t['team_number']}", now) for t in teams],
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO event_teams (event_code, team_number) VALUES (?, ?)",
                    [(ek, t["team_number"]) for t in teams],
                )

    placeholders = ",".join("?" * len(event_keys))
    with get_db() as conn:
        rows = conn.execute(
            f"""SELECT tnc.team_number, tnc.nickname
                FROM event_teams et
                JOIN team_name_cache tnc ON et.team_number = tnc.team_number
                WHERE et.event_code IN ({placeholders})""",
            event_keys,
        ).fetchall()

    return jsonify({r["team_number"]: r["nickname"] for r in rows})


if __name__ == "__main__":
    app.run(debug=True, port=5000)

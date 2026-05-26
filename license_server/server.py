"""
TrackTag License Server
=======================
FastAPI + SQLite.  Deploy on Railway.app (see README below).

Endpoints:
  POST /activate          { key, machine_id }  → activate a key on this machine
  POST /deactivate        { key, machine_id }  → free the key (can move to new machine)
  POST /verify            { key, machine_id }  → check if still active (called on app start)
  POST /admin/keys        { count, note }      → generate new license keys  [admin]
  GET  /admin/keys        list all keys                                       [admin]
  DELETE /admin/keys/{key}  revoke a key                                      [admin]

Auth: all /admin/* routes require header  X-Admin-Token: <ADMIN_TOKEN>

Env vars:
  ADMIN_TOKEN   secret token for admin routes (set in Railway dashboard)
  DATABASE_PATH path to SQLite file (default: ./tracktag_licenses.db)
               on Railway: mount a volume at /data and set DATABASE_PATH=/data/tracktag.db
"""

import os
import sqlite3
import secrets
import hashlib
import time
from contextlib import contextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── Config ─────────────────────────────────────────────────────────────────────
ADMIN_TOKEN   = os.environ.get("ADMIN_TOKEN", "change-me-in-railway-dashboard")
DATABASE_PATH = os.environ.get("DATABASE_PATH", "tracktag_licenses.db")

# Simple rate limiter: max N activation attempts per IP per minute
_rate: dict[str, list[float]] = {}
RATE_LIMIT = 10   # attempts per minute per IP

# ── Database ───────────────────────────────────────────────────────────────────

def _db():
    con = sqlite3.connect(DATABASE_PATH)
    con.row_factory = sqlite3.Row
    return con

def _init_db():
    with _db() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS licenses (
                key         TEXT PRIMARY KEY,
                note        TEXT DEFAULT '',
                created_at  TEXT NOT NULL,
                revoked     INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS activations (
                key         TEXT NOT NULL REFERENCES licenses(key),
                machine_id  TEXT NOT NULL,
                activated_at TEXT NOT NULL,
                PRIMARY KEY (key)      -- only ONE active machine per key
            );
            CREATE TABLE IF NOT EXISTS activation_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                key         TEXT NOT NULL,
                machine_id  TEXT NOT NULL,
                action      TEXT NOT NULL,   -- 'activate' | 'deactivate' | 'verify'
                ts          TEXT NOT NULL
            );
        """)

# ── FastAPI app ────────────────────────────────────────────────────────────────
app = FastAPI(title="TrackTag License Server", docs_url=None, redoc_url=None)

@app.on_event("startup")
def startup():
    _init_db()


# ── Rate limiting ──────────────────────────────────────────────────────────────
def _check_rate(ip: str):
    now = time.time()
    hits = [t for t in _rate.get(ip, []) if now - t < 60]
    if len(hits) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests — try again in a minute.")
    hits.append(now)
    _rate[ip] = hits


# ── Admin auth ─────────────────────────────────────────────────────────────────
def _require_admin(x_admin_token: str = Header(default="")):
    if not secrets.compare_digest(x_admin_token, ADMIN_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid admin token.")


# ── Pydantic models ────────────────────────────────────────────────────────────
class ActivateRequest(BaseModel):
    key:        str
    machine_id: str   # sha256 of hardware UUID, truncated

class GenerateRequest(BaseModel):
    count: int = 1
    note:  str = ""


# ── Helpers ────────────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"

def _log(con, key, machine_id, action):
    con.execute(
        "INSERT INTO activation_log (key, machine_id, action, ts) VALUES (?,?,?,?)",
        (key, machine_id, action, _now())
    )

def _generate_key() -> str:
    """TT-XXXX-XXXX-XXXX-XXXX format."""
    raw = secrets.token_hex(8).upper()
    return f"TT-{raw[0:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}"


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.post("/activate")
def activate(req: ActivateRequest, request: Request):
    _check_rate(request.client.host)
    key = req.key.strip().upper()
    mid = req.machine_id.strip()[:64]

    with _db() as con:
        lic = con.execute("SELECT * FROM licenses WHERE key=?", (key,)).fetchone()
        if not lic:
            return JSONResponse({"ok": False, "error": "invalid_key"}, status_code=200)
        if lic["revoked"]:
            return JSONResponse({"ok": False, "error": "key_revoked"}, status_code=200)

        act = con.execute("SELECT * FROM activations WHERE key=?", (key,)).fetchone()
        if act:
            if act["machine_id"] == mid:
                # Already active on this machine — idempotent OK
                _log(con, key, mid, "verify")
                return {"ok": True, "message": "already_active_here"}
            else:
                return JSONResponse(
                    {"ok": False, "error": "already_active_on_other_machine"},
                    status_code=200
                )

        # Activate
        con.execute(
            "INSERT INTO activations (key, machine_id, activated_at) VALUES (?,?,?)",
            (key, mid, _now())
        )
        _log(con, key, mid, "activate")
        return {"ok": True, "message": "activated"}


@app.post("/deactivate")
def deactivate(req: ActivateRequest, request: Request):
    _check_rate(request.client.host)
    key = req.key.strip().upper()
    mid = req.machine_id.strip()[:64]

    with _db() as con:
        act = con.execute("SELECT * FROM activations WHERE key=?", (key,)).fetchone()
        if not act:
            return {"ok": True, "message": "not_active"}   # idempotent
        if act["machine_id"] != mid:
            return JSONResponse(
                {"ok": False, "error": "not_your_activation"},
                status_code=200
            )
        con.execute("DELETE FROM activations WHERE key=?", (key,))
        _log(con, key, mid, "deactivate")
        return {"ok": True, "message": "deactivated"}


@app.post("/verify")
def verify(req: ActivateRequest, request: Request):
    """Called silently on app start to confirm license still valid."""
    _check_rate(request.client.host)
    key = req.key.strip().upper()
    mid = req.machine_id.strip()[:64]

    with _db() as con:
        lic = con.execute("SELECT * FROM licenses WHERE key=?", (key,)).fetchone()
        if not lic or lic["revoked"]:
            return {"ok": False, "active": False, "error": "invalid_or_revoked"}

        act = con.execute("SELECT * FROM activations WHERE key=?", (key,)).fetchone()
        if act and act["machine_id"] == mid:
            _log(con, key, mid, "verify")
            return {"ok": True, "active": True}
        return {"ok": False, "active": False, "error": "not_active_here"}


# ── Admin routes ───────────────────────────────────────────────────────────────

@app.post("/admin/keys", dependencies=[Depends(_require_admin)])
def admin_generate(req: GenerateRequest):
    if req.count < 1 or req.count > 100:
        raise HTTPException(400, "count must be 1–100")
    keys = []
    with _db() as con:
        for _ in range(req.count):
            k = _generate_key()
            con.execute(
                "INSERT INTO licenses (key, note, created_at) VALUES (?,?,?)",
                (k, req.note, _now())
            )
            keys.append(k)
    return {"ok": True, "keys": keys}


@app.get("/admin/keys", dependencies=[Depends(_require_admin)])
def admin_list():
    with _db() as con:
        rows = con.execute("""
            SELECT l.key, l.note, l.created_at, l.revoked,
                   a.machine_id, a.activated_at
            FROM licenses l
            LEFT JOIN activations a ON l.key = a.key
            ORDER BY l.created_at DESC
        """).fetchall()
    return {"ok": True, "keys": [dict(r) for r in rows]}


@app.delete("/admin/keys/{key}", dependencies=[Depends(_require_admin)])
def admin_revoke(key: str):
    key = key.strip().upper()
    with _db() as con:
        con.execute("UPDATE licenses SET revoked=1 WHERE key=?", (key,))
        con.execute("DELETE FROM activations WHERE key=?", (key,))
    return {"ok": True, "message": f"{key} revoked"}


@app.get("/health")
def health():
    return {"status": "ok"}

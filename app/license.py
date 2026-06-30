"""
TrackTag Pro License Client
============================
- Aktiviert / deaktiviert Keys gegen den eigenen License-Server
- Speichert den aktiven Key lokal in ~/Library/Application Support/TrackTag/license.json
- Verifiziert beim App-Start im Hintergrund (Offline-Toleranz: 7 Tage)
"""

import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── License server URL — trage hier deine Railway-URL ein ────────────────────
LICENSE_SERVER = os.environ.get(
    "TRACKTAG_LICENSE_SERVER",
    "https://tracktag-production-8950.up.railway.app"
)

# ── Local storage ─────────────────────────────────────────────────────────────
_CONFIG_DIR  = Path.home() / "Library" / "Application Support" / "TrackTag"
_LICENSE_FILE = _CONFIG_DIR / "license.json"

# Offline-Toleranz: wie viele Sekunden darf der Server unerreichbar sein
# bevor die lokale Lizenz als ungültig gilt
_OFFLINE_GRACE = 7 * 24 * 3600   # 7 Tage


# ── Machine fingerprint ───────────────────────────────────────────────────────

def get_machine_id() -> str:
    """Liefert einen stabilen, anonymen Hash der Hardware-UUID dieses Macs."""
    try:
        out = subprocess.check_output(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            text=True, stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            if "IOPlatformUUID" in line:
                uuid = line.split('"')[-2]
                return hashlib.sha256(uuid.encode()).hexdigest()[:32]
    except Exception:
        pass
    # Fallback: Hostname (weniger eindeutig, aber besser als nichts)
    import platform
    return hashlib.sha256(platform.node().encode()).hexdigest()[:32]


# ── Local license state ───────────────────────────────────────────────────────

def _load() -> dict:
    try:
        if _LICENSE_FILE.exists():
            return json.loads(_LICENSE_FILE.read_text())
    except Exception:
        pass
    return {}


def _save(data: dict):
    try:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _LICENSE_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        print(f"[license] save error: {e}")


def _clear():
    try:
        if _LICENSE_FILE.exists():
            _LICENSE_FILE.unlink()
    except Exception:
        pass


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _post(endpoint: str, body: dict, timeout: int = 8) -> dict | None:
    url  = LICENSE_SERVER.rstrip("/") + endpoint
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "User-Agent": "TrackTag/1.0"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"[license] HTTP {e.code}: {e.read().decode()}")
        return None
    except Exception as e:
        print(f"[license] network error: {e}")
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def activate(key: str) -> tuple[bool, str]:
    """
    Aktiviert einen Key auf diesem Mac.
    Gibt (True, "") oder (False, "Fehlermeldung") zurück.
    """
    key = key.strip().upper()
    mid = get_machine_id()

    resp = _post("/activate", {"key": key, "machine_id": mid})

    if resp is None:
        return False, "Server unreachable. Please check your internet connection."

    if resp.get("ok"):
        _save({
            "key":          key,
            "machine_id":   mid,
            "activated_at": time.time(),
            "last_verified": time.time(),
        })
        return True, ""

    error = resp.get("error", "unknown")
    messages = {
        "invalid_key":                    "Invalid license key.",
        "key_revoked":                    "This license key has been revoked.",
        "already_active_on_other_machine": (
            "This key is already active on another Mac.\n"
            "Deactivate it there first (Settings → License → Deactivate)."
        ),
    }
    return False, messages.get(error, f"Fehler: {error}")


def deactivate() -> tuple[bool, str]:
    """
    Deaktiviert die lokale Lizenz und gibt sie frei.
    """
    data = _load()
    if not data.get("key"):
        _clear()
        return True, ""

    key = data["key"]
    mid = get_machine_id()

    resp = _post("/deactivate", {"key": key, "machine_id": mid})
    _clear()   # lokal immer löschen, auch wenn Server nicht antwortet

    if resp is None:
        # Offline deaktiviert — lokal gelöscht, Server weiß es noch nicht.
        # Das ist ok: nächste Aktivierung auf neuem Mac schlägt fehl bis
        # der Server ihn sieht — aber da wir ihn lokal löschen ist das sicher.
        return True, "Lizenz lokal deaktiviert (Server offline)."

    return True, ""


def verify_background() -> bool:
    """
    Stille Hintergrundprüfung beim App-Start.
    Gibt True zurück wenn Pro aktiv ist (auch offline innerhalb der Gnadenfrist).
    Gibt False zurück und löscht lokal wenn ungültig.
    """
    data = _load()
    if not data.get("key"):
        return False

    key = data["key"]
    mid = get_machine_id()

    # Maschinen-ID muss passen (Schutz gegen kopierten license.json)
    if data.get("machine_id") != mid:
        _clear()
        return False

    resp = _post("/verify", {"key": key, "machine_id": mid})

    if resp is None:
        # Server unerreichbar — Offline-Toleranz prüfen
        last = data.get("last_verified", 0)
        if time.time() - last < _OFFLINE_GRACE:
            return True   # Noch innerhalb der Gnadenfrist
        _clear()
        return False

    if resp.get("ok") and resp.get("active"):
        # Letzten Verify-Zeitstempel aktualisieren
        data["last_verified"] = time.time()
        _save(data)
        return True

    # Server sagt: nicht mehr aktiv → lokal löschen
    _clear()
    return False


def is_pro_active() -> bool:
    """Fast local check without network (for UI state)."""
    data = _load()
    if not data.get("key"):
        return False
    if data.get("machine_id") != get_machine_id():
        return False
    # Grobe Prüfung: zuletzt verifiziert innerhalb der Gnadenfrist?
    last = data.get("last_verified", 0)
    return time.time() - last < _OFFLINE_GRACE


def get_active_key() -> str | None:
    """Returns the active key or None."""
    data = _load()
    return data.get("key") if is_pro_active() else None

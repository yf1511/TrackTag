"""
Auto-updater — checks GitHub Releases for a newer version of TrackTag.

Usage in main_window.py:
    from .updater import UpdateChecker
    checker = UpdateChecker("yourusername", "tracktag", parent_widget)
    checker.start()   # runs in background, shows banner if update found
"""
import os
import sys
import shutil
import subprocess
import tempfile
import urllib.request
import urllib.error
import json
import re

from PyQt6.QtCore import QThread, pyqtSignal, Qt, QTimer
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton, QApplication


# ── Colour tokens (duplicated here to avoid circular import) ───────────────────
_C_BG      = "#0c0d11"
_C_SURFACE = "#111217"
_C_BORDER  = "#2f3039"
_C_TEXT    = "#ededf1"
_C_TEXT2   = "#9b9dab"
_C_PRIMARY = "#8b5cf6"
_C_ACCENT  = "#ec4899"


def _parse_version(v: str):
    """Return (major, minor, patch) ints from a semver string like '1.2.3' or 'v1.2.3'."""
    v = v.lstrip("v")
    parts = re.findall(r"\d+", v)
    parts = (parts + ["0", "0", "0"])[:3]
    return tuple(int(x) for x in parts)


class _FetchThread(QThread):
    """Background thread that checks GitHub Releases API."""
    result = pyqtSignal(str, str)  # (latest_version, download_url)
    error  = pyqtSignal(str)

    GITHUB_OWNER = ""   # filled by UpdateChecker
    GITHUB_REPO  = ""

    def run(self):
        url = f"https://api.github.com/repos/{self.GITHUB_OWNER}/{self.GITHUB_REPO}/releases/latest"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TrackTag-Updater/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
            tag  = data.get("tag_name", "")
            html = data.get("html_url", "")
            # Prefer .dmg asset download if available
            for asset in data.get("assets", []):
                name = asset.get("name", "")
                if name.endswith(".dmg"):
                    html = asset.get("browser_download_url", html)
                    break
            self.result.emit(tag, html)
        except Exception as ex:
            self.error.emit(str(ex))


def _current_app_bundle():
    """Path of the running TrackTag.app, or None when not running as a bundle."""
    if not getattr(sys, "frozen", False):
        return None
    # sys.executable = …/TrackTag.app/Contents/MacOS/TrackTag
    bundle = os.path.abspath(os.path.join(os.path.dirname(sys.executable), "..", ".."))
    return bundle if bundle.endswith(".app") else None


def can_self_update():
    bundle = _current_app_bundle()
    return bool(bundle) and os.access(os.path.dirname(bundle), os.W_OK)


class _DownloadThread(QThread):
    """Downloads the DMG and extracts the new .app into a staging folder."""
    progress = pyqtSignal(int)      # percent, -1 if unknown
    finished_ok = pyqtSignal(str)   # path to staged .app
    failed = pyqtSignal(str)

    def __init__(self, url: str):
        super().__init__()
        self._url = url
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        work = tempfile.mkdtemp(prefix="tracktag_update_")
        dmg = os.path.join(work, "update.dmg")
        mnt = os.path.join(work, "mnt")
        try:
            req = urllib.request.Request(self._url, headers={"User-Agent": "TrackTag-Updater/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp, open(dmg, "wb") as out:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    if self._cancel:
                        return
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    self.progress.emit(int(done * 100 / total) if total else -1)

            os.makedirs(mnt)
            subprocess.run(["hdiutil", "attach", dmg, "-nobrowse", "-readonly",
                            "-mountpoint", mnt], check=True, capture_output=True)
            try:
                apps = [n for n in os.listdir(mnt) if n.endswith(".app")]
                if not apps:
                    raise RuntimeError("No .app found in the downloaded DMG")
                staged = os.path.join(work, apps[0])
                subprocess.run(["ditto", os.path.join(mnt, apps[0]), staged],
                               check=True, capture_output=True)
            finally:
                subprocess.run(["hdiutil", "detach", mnt, "-quiet", "-force"],
                               capture_output=True)
            os.remove(dmg)
            self.finished_ok.emit(staged)
        except Exception as ex:
            shutil.rmtree(work, ignore_errors=True)
            self.failed.emit(str(ex))


def _launch_swap_helper(staged_app: str, target_app: str):
    """
    Spawn a detached shell script that waits for this process to exit,
    replaces the installed bundle with the staged one and relaunches it.
    """
    import shlex
    script = f'''#!/bin/bash
PID={os.getpid()}
NEW={shlex.quote(staged_app)}
OLD={shlex.quote(target_app)}
BAK="$OLD.old"
while kill -0 $PID 2>/dev/null; do sleep 0.3; done
rm -rf "$BAK"
if mv "$OLD" "$BAK" && mv "$NEW" "$OLD"; then
    rm -rf "$BAK"
else
    [ -d "$BAK" ] && [ ! -d "$OLD" ] && mv "$BAK" "$OLD"
fi
xattr -cr "$OLD" 2>/dev/null
rm -rf "$(dirname "$NEW")"
open "$OLD"
rm -f "$0"
'''
    fd, path = tempfile.mkstemp(prefix="tracktag_swap_", suffix=".sh")
    with os.fdopen(fd, "w") as f:
        f.write(script)
    os.chmod(path, 0o755)
    subprocess.Popen(["/bin/bash", path], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


class UpdateBanner(QWidget):
    """Slim banner shown at the top of the window when an update is available."""

    def __init__(self, version: str, url: str, parent=None):
        super().__init__(parent)
        self._url = url
        self._dl_thread = None
        self._self_update = can_self_update() and url.endswith(".dmg")
        self.setFixedHeight(40)
        self.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 rgba(139,92,246,0.18), stop:1 rgba(236,72,153,0.10));
                border-bottom: 1px solid rgba(139,92,246,0.35);
            }
            QLabel  { background: transparent; border: none; }
            QPushButton { border: none; background: transparent; padding: 0; }
        """)

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 0, 12, 0)
        row.setSpacing(12)

        icon = QLabel("✦")
        icon.setStyleSheet(f"color:{_C_PRIMARY};font-size:12px;font-weight:700;")
        row.addWidget(icon)

        msg = QLabel(f"Update available: TrackTag {version.lstrip('v')}  –  "
                     + ("Install now for new features and improvements!" if self._self_update
                        else "Download now for new features and improvements!"))
        msg.setStyleSheet(f"color:{_C_TEXT};font-size:12px;")
        row.addWidget(msg, 1)
        self._msg = msg

        dl_btn = QPushButton("Install & Restart" if self._self_update else "Download →")
        dl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dl_btn.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {_C_PRIMARY}, stop:1 {_C_ACCENT});
                color: #fff; border: none; border-radius: 6px;
                font-size:11px; font-weight:700; padding: 5px 14px;
            }}
            QPushButton:hover {{ opacity: 0.9; }}
        """)
        dl_btn.clicked.connect(self._open_download)
        row.addWidget(dl_btn)
        self._dl_btn = dl_btn

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet(
            f"color:{_C_TEXT2};font-size:13px;font-weight:600;"
            f"QPushButton:hover{{color:{_C_TEXT};}}")
        close_btn.clicked.connect(self.hide)
        row.addWidget(close_btn)

    def _open_download(self):
        if not self._self_update:
            subprocess.Popen(["open", self._url])
            return
        self._dl_btn.setEnabled(False)
        self._msg.setText("Downloading update…")
        self._dl_thread = _DownloadThread(self._url)
        self._dl_thread.progress.connect(self._on_progress)
        self._dl_thread.finished_ok.connect(self._on_staged)
        self._dl_thread.failed.connect(self._on_failed)
        self._dl_thread.start()

    def _on_progress(self, pct: int):
        self._msg.setText(f"Downloading update… {pct}%" if pct >= 0 else "Downloading update…")

    def _on_failed(self, err: str):
        self._msg.setText("Update failed – opening download page instead.")
        self._dl_btn.setEnabled(True)
        self._self_update = False
        self._dl_btn.setText("Download →")
        print(f"[updater] self-update failed: {err}")

    def _on_staged(self, staged_app: str):
        self._msg.setText("Update ready – restarting TrackTag…")
        win = self.window()
        win.close()                 # runs closeEvent (unsaved-changes prompt)
        if win.isVisible():         # user cancelled the quit
            self._msg.setText("Update ready – will install when you restart via the button.")
            self._dl_btn.setText("Restart now")
            self._dl_btn.setEnabled(True)
            self._dl_btn.clicked.disconnect()
            self._dl_btn.clicked.connect(lambda: self._on_staged(staged_app))
            return
        _launch_swap_helper(staged_app, _current_app_bundle())
        QApplication.quit()

    def shutdown(self):
        """Cancel a running download so the app can quit promptly."""
        if self._dl_thread is not None and self._dl_thread.isRunning():
            self._dl_thread.cancel()
            self._dl_thread.wait()


class UpdateChecker:
    """
    One-shot update checker.  Call start() once from MainWindow.__init__
    (or showEvent) to kick off a background fetch.

    Required setup — set these two class-level values before calling start():
        UpdateChecker.GITHUB_OWNER = "yourusername"
        UpdateChecker.GITHUB_REPO  = "tracktag"
    """

    GITHUB_OWNER = "yf1511"
    GITHUB_REPO  = "TrackTag"

    def __init__(self, parent_window):
        self._win    = parent_window
        self._thread = None
        self._banner = None

    def start(self):
        """Start background check.  Safe to call even if owner/repo not set yet."""
        if not self.GITHUB_OWNER or not self.GITHUB_REPO:
            return

        self._thread = _FetchThread()
        self._thread.GITHUB_OWNER = self.GITHUB_OWNER
        self._thread.GITHUB_REPO  = self.GITHUB_REPO
        self._thread.result.connect(self._on_result)
        self._thread.error.connect(lambda e: None)   # silent on error
        # Delay slightly so window is fully shown first
        QTimer.singleShot(2000, self._thread.start)

    def _on_result(self, latest_tag: str, url: str):
        try:
            from version import __version__ as current
        except ImportError:
            try:
                import sys, os
                # In bundled app, version.py is at sys._MEIPASS or next to executable
                for base in (getattr(sys, '_MEIPASS', None),
                             os.path.dirname(sys.executable),
                             os.path.dirname(os.path.dirname(__file__))):
                    if base:
                        vpath = os.path.join(base, 'version.py')
                        if os.path.exists(vpath):
                            ns = {}; exec(open(vpath).read(), ns)
                            current = ns.get('__version__', '0.0.0')
                            break
                else:
                    current = "0.0.0"
            except Exception:
                current = "0.0.0"

        if _parse_version(latest_tag) > _parse_version(current):
            self._show_banner(latest_tag, url)

    def shutdown(self):
        if self._banner is not None:
            self._banner.shutdown()

    def _show_banner(self, version: str, url: str):
        """Insert the banner at the very top of the window's central widget."""
        central = self._win.centralWidget()
        if not central:
            return
        layout = central.layout()
        if not layout:
            return
        self._banner = UpdateBanner(version, url, central)
        layout.insertWidget(0, self._banner)

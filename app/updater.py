"""
Auto-updater — checks GitHub Releases for a newer version of TrackTag.

Usage in main_window.py:
    from .updater import UpdateChecker
    checker = UpdateChecker("yourusername", "tracktag", parent_widget)
    checker.start()   # runs in background, shows banner if update found
"""
import sys
import urllib.request
import urllib.error
import json
import re

from PyQt6.QtCore import QThread, pyqtSignal, Qt, QTimer
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton


# ── Colour tokens (duplicated here to avoid circular import) ───────────────────
_C_BG      = "#0b0d13"
_C_SURFACE = "#12141b"
_C_BORDER  = "#2a2d3a"
_C_TEXT    = "#f1f3f5"
_C_TEXT2   = "#a1a5b3"
_C_PRIMARY = "#7c3aed"
_C_ACCENT  = "#ff2d75"


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


class UpdateBanner(QWidget):
    """Slim banner shown at the top of the window when an update is available."""

    def __init__(self, version: str, url: str, parent=None):
        super().__init__(parent)
        self._url = url
        self.setFixedHeight(40)
        self.setStyleSheet(f"""
            QWidget {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 rgba(124,58,237,0.25), stop:1 rgba(255,45,117,0.15));
                border-bottom: 1px solid rgba(124,58,237,0.4);
            }}
            QLabel  {{ background: transparent; border: none; }}
            QPushButton {{ border: none; background: transparent; padding: 0; }}
        """)

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 0, 12, 0)
        row.setSpacing(12)

        icon = QLabel("✦")
        icon.setStyleSheet(f"color:{_C_PRIMARY};font-size:12px;font-weight:700;")
        row.addWidget(icon)

        msg = QLabel(f"Update available: TrackTag {version.lstrip('v')}  –  Download now for new features and improvements!")
        msg.setStyleSheet(f"color:{_C_TEXT};font-size:12px;")
        row.addWidget(msg, 1)

        dl_btn = QPushButton("Download →")
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

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet(
            f"color:{_C_TEXT2};font-size:13px;font-weight:600;"
            f"QPushButton:hover{{color:{_C_TEXT};}}")
        close_btn.clicked.connect(self.hide)
        row.addWidget(close_btn)

    def _open_download(self):
        import subprocess
        subprocess.Popen(["open", self._url])


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

import os
import re
import subprocess
from pathlib import Path
from typing import List, Optional

import qtawesome as qta

_ICON_PATH    = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "app-icon.png")

# ── Pro license (verified against remote server, no local key list) ───────────
_is_pro = False

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QSplitter, QTableWidget, QTableWidgetItem, QLabel, QLineEdit,
    QPushButton, QFileDialog, QScrollArea, QAbstractItemView,
    QHeaderView, QMessageBox, QMenu, QComboBox, QCompleter,
    QFrame, QStyledItemDelegate,
    QApplication, QDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal, QBuffer, QIODevice, QSize, QRect, QPoint, QTimer, QSettings
from PyQt6.QtGui import (
    QPixmap, QIcon, QAction, QKeySequence, QShortcut,
    QFont, QColor, QPen, QPainter,
)

_SETTINGS = lambda: QSettings("TrackTag", "TrackTag")

from .audio_handler import AudioFile, SUPPORTED_EXTENSIONS
from .cover_search import MetaSearchDialog
from .batch_tag import BatchTagDialog
from .player import PlayerBar
from .convert import wav_to_aiff, move_to_trash, ConvertError
from .updater import UpdateChecker
from . import license as _lic

try:
    from version import __version__ as _APP_VERSION
except Exception:
    _APP_VERSION = "1.0.4"


def _ico(name: str, color: str = "#a1a5b3", size: int = 16) -> QIcon:
    """Return a qtawesome icon with given color."""
    return qta.icon(name, color=color, scale_factor=1.0)


# ── macOS titlebar CI integration ─────────────────────────────────────────────

def _apply_mac_titlebar(win_id: int):
    """Make the macOS titlebar transparent + dark to match C_BG (#0c0d11)."""
    import sys
    if sys.platform != "darwin":
        return
    try:
        import ctypes
        lib = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")

        # ── helpers ──────────────────────────────────────────────────
        lib.sel_getUid.restype  = ctypes.c_void_p
        lib.sel_getUid.argtypes = [ctypes.c_char_p]

        lib.objc_getClass.restype  = ctypes.c_void_p
        lib.objc_getClass.argtypes = [ctypes.c_char_p]

        def sel(b: bytes) -> ctypes.c_void_p:
            return ctypes.c_void_p(lib.sel_getUid(b))

        # generic send – restype / argtypes set per call
        send = lib.objc_msgSend

        # ── NSView → NSWindow ─────────────────────────────────────────
        send.restype  = ctypes.c_void_p
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        ns_window = send(ctypes.c_void_p(win_id), sel(b"window"))
        if not ns_window:
            return

        # ── setTitlebarAppearsTransparent: YES ────────────────────────
        send.restype  = None
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
        send(ctypes.c_void_p(ns_window), sel(b"setTitlebarAppearsTransparent:"), True)

        # ── setTitleVisibility: NSWindowTitleHidden (1) ───────────────
        send.restype  = None
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        send(ctypes.c_void_p(ns_window), sel(b"setTitleVisibility:"), ctypes.c_long(1))

        # ── setMovableByWindowBackground: YES ────────────────────────
        send.restype  = None
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
        send(ctypes.c_void_p(ns_window), sel(b"setMovableByWindowBackground:"), True)

        # ── background colour = #0c0d11 ───────────────────────────────
        NSColor = lib.objc_getClass(b"NSColor")
        send.restype  = ctypes.c_void_p
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_double, ctypes.c_double,
                         ctypes.c_double, ctypes.c_double]
        color = send(ctypes.c_void_p(NSColor),
                     sel(b"colorWithSRGBRed:green:blue:alpha:"),
                     ctypes.c_double(0x0c / 255),
                     ctypes.c_double(0x0d / 255),
                     ctypes.c_double(0x11 / 255),
                     ctypes.c_double(1.0))

        send.restype  = None
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        send(ctypes.c_void_p(ns_window),
             sel(b"setBackgroundColor:"),
             ctypes.c_void_p(color))

    except Exception as exc:
        print(f"macOS titlebar: {exc}")

# ── Design tokens (shared) ────────────────────────────────────────────────────
from .theme import (
    C_BG, C_SURFACE, C_SURFACE2, C_SURFACE3, C_BORDER,
    C_BORDER2, C_TEXT, C_TEXT2, C_TEXT3, C_PRIMARY,
    C_PRIMARY_SOFT, C_ACCENT, C_ACCENT2, C_DANGER, C_SUCCESS,
    C_KEY_CLR, C_SEL_BG, C_SEL_LINE, _GRAD,
    _BTN_PRIMARY, _BTN_SECONDARY, _BTN_GHOST,
)


_CAMELOT_TO_RB = {
    "1A":"Abm", "1B":"B",
    "2A":"Ebm", "2B":"F#",
    "3A":"Bbm", "3B":"Db",
    "4A":"Fm",  "4B":"Ab",
    "5A":"Cm",  "5B":"Eb",
    "6A":"Gm",  "6B":"Bb",
    "7A":"Dm",  "7B":"F",
    "8A":"Am",  "8B":"C",
    "9A":"Em",  "9B":"G",
    "10A":"Bm", "10B":"D",
    "11A":"F#m","11B":"A",
    "12A":"Dbm","12B":"E",
}

_KEY_NORM = {
    # major (full + short)
    "cmaj":"C",   "c#maj":"C#",  "dbmaj":"Db",  "dmaj":"D",   "d#maj":"Eb",
    "ebmaj":"Eb", "emaj":"E",    "fmaj":"F",    "f#maj":"F#", "gbmaj":"F#",
    "gmaj":"G",   "g#maj":"Ab",  "abmaj":"Ab",  "amaj":"A",   "a#maj":"Bb",
    "bbmaj":"Bb", "bmaj":"B",
    # minor (full + short)
    "cmin":"Cm",   "c#min":"C#m",  "dbmin":"Dbm",  "dmin":"Dm",  "d#min":"Ebm",
    "ebmin":"Ebm", "emin":"Em",    "fmin":"Fm",    "f#min":"F#m","gbmin":"F#m",
    "gmin":"Gm",   "g#min":"Abm",  "abmin":"Abm",  "amin":"Am",  "a#min":"Bbm",
    "bbmin":"Bbm", "bmin":"Bm",
    # short forms already normalised
    "c":"C",   "c#":"C#",  "db":"Db",  "d":"D",   "eb":"Eb",
    "e":"E",   "f":"F",    "f#":"F#",  "gb":"F#", "g":"G",
    "ab":"Ab", "a":"A",    "bb":"Bb",  "b":"B",
    "cm":"Cm",   "c#m":"C#m",  "dbm":"Dbm",  "dm":"Dm",  "ebm":"Ebm",
    "em":"Em",   "fm":"Fm",    "f#m":"F#m",  "gbm":"F#m","gm":"Gm",
    "abm":"Abm", "am":"Am",    "bbm":"Bbm",  "bm":"Bm",
}

_RB_TO_CAMELOT = {v: k for k, v in _CAMELOT_TO_RB.items()}
_RB_TO_CAMELOT.update({   # enharmonic spellings produced by _KEY_NORM
    "C#m":"12A", "C#":"3B", "G#m":"1A", "G#":"4B", "D#m":"2A", "D#":"5B",
    "A#m":"3A", "A#":"6B", "Gbm":"11A", "Gb":"2B",
})

def _key_color(n: int, minor: bool) -> str:
    """One hue per Camelot number (like the wheel); minor deeper, major lighter."""
    import colorsys
    r, g, b = colorsys.hls_to_rgb(((n - 1) * 30 % 360) / 360,
                                  0.58 if minor else 0.70, 0.62)
    return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"

# Camelot-wheel hue order — keyed by musical name
KEY_COLORS: dict[str, str] = {
    rb: _key_color(int(cam[:-1]), cam.endswith("A")) for rb, cam in _RB_TO_CAMELOT.items()
}

KEY_FORMATS = {"musical": "Musical  (Am, F#)", "camelot": "Camelot  (8A, 2B)",
               "openkey": "Open Key  (1m, 7d)"}
_key_format = _SETTINGS().value("key_format", "musical", str) or "musical"


def set_key_format(fmt: str):
    global _key_format
    _key_format = fmt if fmt in KEY_FORMATS else "musical"
    _SETTINGS().setValue("key_format", _key_format)


def key_to_musical(s: str) -> str:
    """Any notation (Beatport, Camelot, Open Key, musical) → Rekordbox short form."""
    if not s: return ""
    s = s.strip()
    # Camelot wheel: "11A", "6B"
    m = re.match(r'^(\d{1,2})([AB])$', s, re.IGNORECASE)
    if m:
        return _CAMELOT_TO_RB.get(f"{m.group(1)}{m.group(2).upper()}", s)
    # Open Key: "1m" = Am (8A), "1d" = C (8B)
    m = re.match(r'^(\d{1,2})([md])$', s, re.IGNORECASE)
    if m and 1 <= int(m.group(1)) <= 12:
        cam = (int(m.group(1)) + 6) % 12 + 1
        return _CAMELOT_TO_RB.get(f"{cam}{'A' if m.group(2).lower() == 'm' else 'B'}", s)
    # Normalise lookup key: strip spaces, lowercase, expand maj/min words
    lk = re.sub(r'\s+', '', s.lower())
    lk = lk.replace("major", "maj").replace("minor", "min")
    lk = lk.replace("maj.", "maj").replace("min.", "min")
    lk = lk.replace("mj", "maj").replace("mn", "min")
    return _KEY_NORM.get(lk, s)


def normalize_key(s: str) -> str:
    """Convert any key notation to the format chosen in Settings."""
    mus = key_to_musical(s)
    if _key_format == "musical" or mus not in _RB_TO_CAMELOT:
        return mus
    cam = _RB_TO_CAMELOT[mus]
    if _key_format == "camelot":
        return cam
    n, letter = int(cam[:-1]), cam[-1]
    return f"{(n - 8) % 12 + 1}{'m' if letter == 'A' else 'd'}"


# ── Genre presets ─────────────────────────────────────────────────────────────

DJ_GENRES = [
    "Afro House","Afrobeats","Ambient","Bass House","Breakbeat","Breaks",
    "Deep House","Disco","Downtempo","Drum & Bass","Dub","Dubstep",
    "EBM","Electro House","Electronic","Funk","Future House","Hard Techno",
    "Hardcore","Hip Hop","House","Industrial","Jazz","Jungle","Latin",
    "Melodic House & Techno","Melodic Techno","Minimal Techno","Nu Disco",
    "Organic House","Pop","Progressive House","Progressive Trance",
    "Psy-Trance","R&B","Reggae","Riddim","Rock","Soul",
    "Tech House","Techno","Trance","Trip Hop",
]

# ── Columns ───────────────────────────────────────────────────────────────────

COLUMNS = [
    ("_num",        "#"),
    ("_cover",      ""),
    ("title",       "TITLE"),
    ("artist",      "ARTIST"),
    ("album",       "ALBUM"),
    ("genre",       "GENRE"),
    ("label",       "LABEL"),
    ("bpm",         "BPM"),
    ("key",         "KEY"),
    ("year",        "YEAR"),
    ("bitrate_str", "BITRATE"),
    ("duration_str","LENGTH"),
    ("filename",    "FILENAME"),
    ("album_artist","ALBUM ARTIST"),
    ("track",       "TRACK"),
    ("comment",     "COMMENT"),
    ("composer",    "COMPOSER"),
    ("sample_rate_str","SAMPLERATE"),
]
_DEFAULT_COL_W = {
    "title": 260, "artist": 190, "album": 160, "genre": 140, "label": 140,
    "bpm": 64, "key": 72, "year": 64, "bitrate_str": 84, "duration_str": 72,
    "filename": 260, "album_artist": 160, "track": 64, "comment": 180,
    "composer": 150, "sample_rate_str": 96,
}

_NUM_COL   = 0
_COVER_COL = 1
_TITLE_COL = 2
_GENRE_COL = 5
_BPM_COL   = 7
_KEY_COL   = 8

# ── Shared styles ─────────────────────────────────────────────────────────────

_INPUT = f"""
    QLineEdit {{
        background:{C_SURFACE2}; color:{C_TEXT};
        border:1px solid {C_BORDER}; border-radius:7px;
        padding:0 10px; font-size:12px;
        selection-background-color:{C_PRIMARY};
    }}
    QLineEdit:hover {{ border-color:{C_BORDER2}; }}
    QLineEdit:focus {{ border-color:{C_PRIMARY}; background:{C_BG}; }}
    QLineEdit:disabled {{ color:{C_TEXT3}; background:{C_SURFACE}; border-color:{C_SURFACE2}; }}
"""
_COMBO = f"""
    QComboBox {{
        background:{C_SURFACE2}; color:{C_TEXT};
        border:1px solid {C_BORDER}; border-radius:7px;
        padding:0 10px; font-size:12px;
    }}
    QComboBox:hover {{ border-color:{C_BORDER2}; }}
    QComboBox:focus {{ border-color:{C_PRIMARY}; }}
    QComboBox:disabled {{ color:{C_TEXT3}; }}
    QComboBox::drop-down {{ border:none; width:20px; }}
    QComboBox::down-arrow {{ image:none; }}
    QComboBox QAbstractItemView {{
        background:{C_SURFACE2}; color:{C_TEXT};
        selection-background-color:{C_PRIMARY};
        border:1px solid {C_BORDER};
    }}
"""

# ── Subtitle extractor ────────────────────────────────────────────────────────

_MIX_KW = ('mix','remix','edit','version','extended','original',
            'club','radio','instrumental','vip','dub','reprise')

def _subtitle(title: str) -> str:
    m = re.search(r'\(([^)]+)\)\s*$', title.strip())
    if m and any(k in m.group(1).lower() for k in _MIX_KW):
        return m.group(0)
    return ""

# ── Custom table delegate ─────────────────────────────────────────────────────

class NoScrollComboBox(QComboBox):
    """ComboBox that ignores scroll wheel — prevents accidental genre changes while scrolling."""
    def wheelEvent(self, e):
        e.ignore()


_SORT_ROLE = Qt.ItemDataRole.UserRole + 7

class SortItem(QTableWidgetItem):
    """Table item that sorts numerically when a sort key is set, else case-insensitive."""
    def __lt__(self, other):
        a = self.data(_SORT_ROLE); b = other.data(_SORT_ROLE)
        if a is not None and b is not None:
            try: return a < b
            except TypeError: pass
        return self.text().lower() < other.text().lower()


def _rounded_pixmap(pix: QPixmap, size: int, radius: float) -> QPixmap:
    """Scale-to-fill a square and clip it to a rounded rect (HiDPI-aware)."""
    from PyQt6.QtGui import QPainterPath
    dpr = 2.0
    px = int(size * dpr)
    src = pix.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                     Qt.TransformationMode.SmoothTransformation)
    out = QPixmap(px, px); out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath(); path.addRoundedRect(0, 0, px, px, radius*dpr, radius*dpr)
    p.setClipPath(path)
    p.drawPixmap((px-src.width())//2, (px-src.height())//2, src)
    p.end()
    out.setDevicePixelRatio(dpr)
    return out


class TrackDelegate(QStyledItemDelegate):
    SUBTITLE_ROLE = Qt.ItemDataRole.UserRole + 2
    ROW_H = 56
    ART   = 40

    _BG       = QColor(C_BG)
    _HOVER    = QColor(C_SURFACE)
    _SEL_BG   = QColor(C_SEL_BG)
    _SEL_LINE = QColor(C_SEL_LINE)
    _SEP      = QColor(C_SURFACE2)
    _ART_BG   = QColor(C_SURFACE2)
    _TEXT     = QColor(C_TEXT)
    _TEXT2    = QColor(C_TEXT2)
    _TEXT3    = QColor(C_TEXT3)

    def __init__(self, table, parent=None):
        super().__init__(parent)
        self._table = table
        self.playing_path: Optional[str] = None

    def paint(self, painter, option, index):
        from PyQt6.QtWidgets import QStyle
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = index.column()
        sel  = bool(option.state & QStyle.StateFlag.State_Selected)
        hov  = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = option.rect

        # Background + hairline separator
        painter.fillRect(rect, self._SEL_BG if sel else (self._HOVER if hov else self._BG))
        painter.fillRect(rect.left(), rect.bottom(), rect.width(), 1, self._SEP)
        # Accent bar on the left edge of the selected row
        if sel and col == 0:
            painter.fillRect(rect.left(), rect.top(), 2, rect.height()-1, self._SEL_LINE)

        r = rect.adjusted(10, 0, -10, 0)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        base = QFont(option.font); base.setPixelSize(12)

        if col == _NUM_COL:
            af = index.data(Qt.ItemDataRole.UserRole)
            if self.playing_path and af is not None and getattr(af, "path", None) == self.playing_path:
                try:
                    _ico("fa5s.volume-up", C_PRIMARY).paint(
                        painter, QRect(rect.center().x()-7, rect.center().y()-7, 14, 14))
                except Exception:
                    painter.setPen(self._SEL_LINE)
                    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "▶")
            else:
                painter.setFont(base)
                painter.setPen(self._SEL_LINE if sel else self._TEXT3)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

        elif col == _COVER_COL:
            ax = rect.x() + (rect.width()-self.ART)//2
            ay = rect.y() + (rect.height()-self.ART)//2
            pix = index.data(Qt.ItemDataRole.DecorationRole)
            if isinstance(pix, QPixmap) and not pix.isNull():
                painter.drawPixmap(ax, ay, pix)
            else:
                painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(self._ART_BG)
                painter.drawRoundedRect(ax, ay, self.ART, self.ART, 6, 6)
                try:
                    _ico("fa5s.music", C_TEXT3).paint(
                        painter, QRect(ax+13, ay+13, 14, 14))
                except Exception:
                    pass

        elif col == _TITLE_COL:
            sub = index.data(self.SUBTITLE_ROLE) or ""
            main = text[:-len(sub)].rstrip() if sub and text.endswith(sub) else text
            mid = rect.center().y()
            f1 = QFont(base); f1.setPixelSize(13); f1.setWeight(QFont.Weight.Medium)
            painter.setFont(f1); painter.setPen(self._TEXT)
            fm = painter.fontMetrics()
            if sub:
                painter.drawText(QRect(r.left(), mid-17, r.width(), 17),
                    Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,
                    fm.elidedText(main, Qt.TextElideMode.ElideRight, r.width()))
                f2 = QFont(base); f2.setPixelSize(11)
                painter.setFont(f2); painter.setPen(self._TEXT2)
                painter.drawText(QRect(r.left(), mid+2, r.width(), 15),
                    Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,
                    painter.fontMetrics().elidedText(sub.strip("() "),
                        Qt.TextElideMode.ElideRight, r.width()))
            else:
                painter.drawText(r, Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,
                    fm.elidedText(text, Qt.TextElideMode.ElideRight, r.width()))

        elif col == _KEY_COL:
            if text:
                kc = QColor(KEY_COLORS.get(key_to_musical(text), C_KEY_CLR))
                f = QFont(base); f.setPixelSize(11); f.setWeight(QFont.Weight.DemiBold)
                painter.setFont(f)
                w = painter.fontMetrics().horizontalAdvance(text) + 16
                chip = QRect(r.left(), rect.center().y()-10, w, 20)
                bg = QColor(kc); bg.setAlpha(38)
                painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(bg)
                painter.drawRoundedRect(chip, 10, 10)
                painter.setPen(kc)
                painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, text)

        else:
            painter.setFont(base)
            painter.setPen(self._TEXT if col == 3 else self._TEXT2)   # artist brighter
            elided = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, r.width())
            painter.drawText(r, Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter, elided)

        painter.restore()

    def sizeHint(self, option, index):
        return QSize(super().sizeHint(option, index).width(), self.ROW_H)


# ── Cover label ───────────────────────────────────────────────────────────────

class CoverLabel(QLabel):
    cover_changed = pyqtSignal(bytes, str)
    clicked = pyqtSignal()
    _SIZE = 256
    _RADIUS = 12
    _IDLE   = (f"QLabel{{border:1px dashed {C_BORDER2};border-radius:12px;"
               f"background:{C_SURFACE2};color:{C_TEXT3};font-size:12px;}}")
    _HOVER  = (f"QLabel{{border:1px dashed {C_PRIMARY};border-radius:12px;"
               f"background:{C_SEL_BG};color:#c4b5fd;font-size:12px;}}")
    _FILLED = ("QLabel{border:none;background:transparent;}")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(self._SIZE, self._SIZE)
        self.setWordWrap(True)
        self._has = False
        self._idle()

    def _idle(self):
        self._has = False; self.clear()
        self.setText("Drop artwork here\nor click to choose\n\n⌘V to paste")
        self.setStyleSheet(self._IDLE)

    def set_cover_data(self, data: bytes, mime: str = "image/jpeg"):
        pix = QPixmap(); pix.loadFromData(data)
        if pix.isNull(): return
        self.show_pixmap(pix)
        self.cover_changed.emit(data, mime)

    def show_pixmap(self, pix: QPixmap):
        self.setPixmap(_rounded_pixmap(pix, self._SIZE, self._RADIUS))
        self.setStyleSheet(self._FILLED); self._has = True

    def clear_cover(self): self._idle()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton: self.clicked.emit()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasImage():
            e.acceptProposedAction(); self.setStyleSheet(self._HOVER)

    def dragLeaveEvent(self, _):
        self.setStyleSheet(self._FILLED if self._has else self._IDLE)

    def dropEvent(self, e):
        md = e.mimeData()
        self.setStyleSheet(self._FILLED if self._has else self._IDLE)
        if md.hasUrls():
            for url in md.urls():
                p = url.toLocalFile()
                if p and Path(p).suffix.lower() in {".jpg",".jpeg",".png",".bmp",".webp"}:
                    with open(p,"rb") as fh: data=fh.read()
                    self.set_cover_data(data, "image/png" if p.endswith(".png") else "image/jpeg")
                    e.acceptProposedAction(); return
        if md.hasImage():
            img=md.imageData(); buf=QBuffer()
            buf.open(QIODevice.OpenModeFlag.WriteOnly); img.save(buf,"JPEG",95)
            self.set_cover_data(bytes(buf.data()),"image/jpeg"); e.acceptProposedAction()


# ── Pro Activate Dialog ───────────────────────────────────────────────────────

class ProActivateDialog(QDialog):
    activated = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Activate Pro License")
        self.setFixedSize(400, 260)
        self.setModal(True)
        self.setStyleSheet(f"""
            QDialog {{
                background:{C_SURFACE};
                border-radius:16px;
            }}
            QLabel {{
                background:transparent;
                border:none;
                color:{C_TEXT};
            }}
            QLineEdit {{
                background:{C_BG};
                color:{C_TEXT};
                border:1px solid {C_BORDER};
                border-radius:8px;
                padding:0 12px;
                font-size:13px;
                selection-background-color:{C_PRIMARY};
            }}
            QLineEdit:focus {{ border-color:{C_PRIMARY}; }}
        """)
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 28)
        root.setSpacing(0)

        # Icon + Title
        hdr = QHBoxLayout(); hdr.setSpacing(12)
        try:
            ic = QLabel()
            ic.setPixmap(_ico("fa5s.key", C_ACCENT2).pixmap(22, 22))
            ic.setFixedSize(22, 22)
            hdr.addWidget(ic)
        except Exception:
            pass
        title = QLabel("Activate Pro License")
        title.setStyleSheet(
            f"color:{C_TEXT};font-size:18px;font-weight:700;")
        hdr.addWidget(title, 1)
        root.addLayout(hdr)
        root.addSpacing(6)

        sub = QLabel("Enter your license key to unlock all Pro features.")
        sub.setStyleSheet(f"color:{C_TEXT2};font-size:12px;")
        sub.setWordWrap(True)
        root.addWidget(sub)
        root.addSpacing(20)

        # Key input
        key_lbl = QLabel("License Key")
        key_lbl.setStyleSheet(
            f"color:{C_TEXT2};font-size:11px;font-weight:600;")
        root.addWidget(key_lbl)
        root.addSpacing(6)

        self._field = QLineEdit()
        self._field.setFixedHeight(42)
        self._field.setPlaceholderText("TT-XXXX-XXXX-XXXX-XXXX")
        self._field.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._field.returnPressed.connect(self._try_activate)
        root.addWidget(self._field)
        root.addSpacing(8)

        self._status = QLabel("")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setFixedHeight(18)
        root.addWidget(self._status)
        root.addSpacing(16)

        # Buttons
        btn_row = QHBoxLayout(); btn_row.setSpacing(10)
        cancel = QPushButton("Cancel")
        cancel.setFixedHeight(40)
        cancel.setStyleSheet(f"""
            QPushButton{{background:{C_SURFACE2};color:{C_TEXT2};
                border:1px solid {C_BORDER};border-radius:10px;font-size:13px;font-weight:600;}}
            QPushButton:hover{{background:{C_BORDER};color:{C_TEXT};}}
        """)
        cancel.clicked.connect(self.reject)

        self._act_btn = QPushButton("Activate")
        self._act_btn.setFixedHeight(40)
        self._act_btn.setStyleSheet(f"""
            QPushButton{{
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {C_PRIMARY}, stop:1 {C_ACCENT});
                color:#fff;border:none;border-radius:10px;
                font-size:13px;font-weight:700;
            }}
            QPushButton:hover{{
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #9d74f8, stop:1 #f062ab);
            }}
            QPushButton:disabled{{background:{C_SURFACE2};color:{C_TEXT3};}}
        """)
        self._act_btn.clicked.connect(self._try_activate)
        btn_row.addWidget(cancel, 1)
        btn_row.addWidget(self._act_btn, 1)
        root.addLayout(btn_row)

    def _try_activate(self):
        global _is_pro
        key = self._field.text().strip().upper()
        if not key:
            return

        # Disable UI while contacting server
        self._act_btn.setEnabled(False)
        self._act_btn.setText("Checking…")
        self._status.setText("")
        QApplication.processEvents()

        ok, err = _lic.activate(key)

        if ok:
            _is_pro = True
            self._status.setText("✓  License activated successfully!")
            self._status.setStyleSheet(
                "color:#22c55e;font-size:11px;font-weight:600;border:none;background:transparent;")
            self._field.setEnabled(False)
            self.activated.emit()
            QTimer.singleShot(1200, self.accept)
        else:
            self._act_btn.setEnabled(True)
            self._act_btn.setText("Activate")
            self._status.setText(err or "Invalid license key.")
            self._status.setStyleSheet(
                f"color:{C_DANGER};font-size:11px;border:none;background:transparent;")
            self._field.setStyleSheet(
                self._field.styleSheet() + f"QLineEdit{{border-color:{C_DANGER};}}")


# ── Full-window drag overlay ──────────────────────────────────────────────────

class DragOverlay(QWidget):
    """Semi-transparent overlay shown when files are dragged over the window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.hide()

    def paintEvent(self, _e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Semi-transparent purple fill
        painter.fillRect(self.rect(), QColor(12, 13, 17, 200))

        # Dashed border (inset 16px)
        inset = 16
        r = self.rect().adjusted(inset, inset, -inset, -inset)
        pen = QPen(QColor(139, 92, 246, 200), 2, Qt.PenStyle.DashLine)
        pen.setDashPattern([8, 6])
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(r, 16, 16)

        # Centered icon + text
        painter.setPen(QColor(237, 237, 241))
        font = QFont(self.font()); font.setPointSize(15); font.setBold(True)
        painter.setFont(font)
        painter.drawText(self.rect().adjusted(0, 20, 0, 0),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                         "Drop audio files here")
        font2 = QFont(self.font()); font2.setPointSize(11)
        painter.setFont(font2)
        painter.setPen(QColor(155, 157, 171))
        painter.drawText(self.rect().adjusted(0, 60, 0, 0),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                         "MP3  ·  FLAC  ·  WAV  ·  AIFF  ·  M4A")


# ── Nav item widget (clickable) ───────────────────────────────────────────────

class NavItem(QWidget):
    clicked = pyqtSignal()

    def __init__(self, label: str, fa_icon: str, count: str = "0",
                 active: bool = False, parent=None):
        super().__init__(parent)
        self.setFixedHeight(34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._fa_icon = fa_icon

        row = QHBoxLayout(self); row.setContentsMargins(10,0,10,0); row.setSpacing(10)
        self._ico_lbl = QLabel()
        self._ico_lbl.setFixedSize(14, 14)
        self._ico_lbl.setStyleSheet("border:none;background:transparent;")
        self._txt = QLabel(label)
        self._badge = QLabel(count)
        self._badge.setAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
        self._badge.setMinimumWidth(20)
        row.addWidget(self._ico_lbl)
        row.addWidget(self._txt, 1)
        row.addWidget(self._badge)
        self.set_active(active)

    def set_count(self, n: int):
        self._badge.setText(str(n))

    def set_active(self, active: bool):
        self._active = active
        self.setStyleSheet(f"""
            NavItem{{background:{C_SURFACE2 if active else 'transparent'};border-radius:7px;}}
            NavItem:hover{{background:{C_SURFACE2};}}
        """)
        try:
            self._ico_lbl.setPixmap(
                _ico(self._fa_icon, C_PRIMARY if active else C_TEXT3).pixmap(13, 13))
        except Exception:
            pass
        self._txt.setStyleSheet(
            f"color:{C_TEXT if active else C_TEXT2};font-size:13px;"
            f"font-weight:{'600' if active else '400'};background:transparent;border:none;")
        self._badge.setStyleSheet(
            f"color:{C_TEXT2 if active else C_TEXT3};font-size:11px;"
            f"background:transparent;border:none;")

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


# ── Navigation sidebar ────────────────────────────────────────────────────────

class NavSidebar(QWidget):
    add_files_clicked    = pyqtSignal()
    nav_filter_changed   = pyqtSignal(str, str)  # (mode, value): ('all','') | ('genre','Tech House')
    pro_activated        = pyqtSignal()

    _PRO_BADGE_STYLE = (
        f"background:{_GRAD};"
        f"color:#fff;font-size:9px;font-weight:800;padding:0 6px;"
        f"border-radius:4px;border:none;letter-spacing:0.8px;")
    _VERSION_STYLE = (
        f"background:transparent;color:{C_TEXT3};font-size:11px;font-weight:500;"
        f"border:none;")
    _ACT_STYLE = _BTN_GHOST + "QPushButton{text-align:left;padding-left:8px;font-size:12px;}"
    _ACT_ON_STYLE = (
        f"QPushButton{{background:transparent;color:{C_SUCCESS};border:none;border-radius:8px;"
        f"font-size:12px;font-weight:500;text-align:left;padding-left:8px;}}"
        f"QPushButton:hover{{background:rgba(34,197,94,0.10);}}")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(232)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("navSidebar")
        self.setStyleSheet(
            f"QWidget#navSidebar{{background:{C_BG};border-right:1px solid {C_BORDER};}}")
        self._nav_all    = None
        self._beta_lbl   = None
        self._genre_navs: dict[str, NavItem] = {}
        self._genre_container = None
        self._genre_vbox = None
        self._manual: list[str] = self._load_genres()
        self._genres: list[str] = sorted(self._manual)
        self._sel = ("all", "")
        self._setup_ui()

    # ── Genre persistence ──────────────────────────────────────────────────────
    def _load_genres(self) -> list:
        """Genres added by hand (the rest comes from the loaded files)."""
        v = _SETTINGS().value("manual_genres", [])
        return [str(g) for g in v] if isinstance(v, list) else []

    def _save_genres(self):
        _SETTINGS().setValue("manual_genres", self._manual)

    def _setup_ui(self):
        root = QVBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        # ── Header ──────────────────────────────────────────────────────
        hdr = QFrame(); hdr.setFixedHeight(60)
        hdr.setStyleSheet(
            f"QFrame{{background:transparent;border:none;border-bottom:1px solid {C_BORDER};}}")
        hl = QHBoxLayout(hdr); hl.setContentsMargins(18,0,16,0); hl.setSpacing(10)
        if os.path.exists(_ICON_PATH):
            il = QLabel()
            il.setPixmap(_rounded_pixmap(QPixmap(_ICON_PATH), 26, 7))
            il.setFixedSize(26,26)
            il.setStyleSheet("border:none;background:transparent;")
            hl.addWidget(il)
        nl = QLabel("TrackTag")
        nl.setStyleSheet(
            f"color:{C_TEXT};font-size:15px;font-weight:700;"
            f"background:transparent;border:none;")
        hl.addWidget(nl)
        self._beta_lbl = QLabel(f"v{_APP_VERSION}")
        self._beta_lbl.setFixedHeight(18)
        self._beta_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._beta_lbl.setStyleSheet(self._VERSION_STYLE)
        hl.addWidget(self._beta_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        hl.addStretch()
        root.addWidget(hdr)

        # ── Content ──────────────────────────────────────────────────────
        content = QWidget()
        content.setStyleSheet("background:transparent;border:none;")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(12,16,12,14); cl.setSpacing(0)

        # Add Files button
        add = QPushButton("  Add Files")
        add.setFixedHeight(36)
        add.setToolTip("Add files (⌘O) — or drop them anywhere on the window")
        try:
            add.setIcon(_ico("fa5s.plus", C_TEXT))
            add.setIconSize(QSize(11, 11))
        except Exception:
            add.setText("+  Add Files")
        add.setStyleSheet(_BTN_SECONDARY + "QPushButton{font-size:13px;}")
        add.clicked.connect(self.add_files_clicked.emit)
        cl.addWidget(add)
        cl.addSpacing(24)

        # Library
        self._nav_all = NavItem("All Tracks", "fa5s.music", "0", active=True)
        self._nav_all.clicked.connect(lambda: self._select("all", ""))
        cl.addWidget(self._nav_all)
        cl.addSpacing(20)

        # ── Genres section ────────────────────────────────────────────────
        genres_hdr_row = QHBoxLayout()
        genres_hdr_row.setContentsMargins(10, 0, 4, 0); genres_hdr_row.setSpacing(0)
        g_lbl = QLabel("GENRES")
        g_lbl.setStyleSheet(
            f"color:{C_TEXT3};font-size:10px;font-weight:600;"
            f"letter-spacing:1.2px;background:transparent;border:none;")
        genres_hdr_row.addWidget(g_lbl)
        genres_hdr_row.addStretch()
        add_genre_btn = QPushButton()
        add_genre_btn.setFixedSize(22, 22)
        try:
            add_genre_btn.setIcon(_ico("fa5s.plus", C_TEXT3))
            add_genre_btn.setIconSize(QSize(9, 9))
        except Exception:
            add_genre_btn.setText("+")
        add_genre_btn.setStyleSheet(f"""
            QPushButton{{background:transparent;color:{C_TEXT3};border:none;
                border-radius:6px;padding:0;}}
            QPushButton:hover{{background:{C_SURFACE2};}}
        """)
        add_genre_btn.setToolTip("Add genre")
        add_genre_btn.clicked.connect(self._add_genre_prompt)
        genres_hdr_row.addWidget(add_genre_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        cl.addLayout(genres_hdr_row)
        cl.addSpacing(6)

        # Container for dynamic genre NavItems (scrolls when the list gets long)
        self._genre_container = QWidget()
        self._genre_container.setStyleSheet("background:transparent;border:none;")
        self._genre_vbox = QVBoxLayout(self._genre_container)
        self._genre_vbox.setContentsMargins(0, 0, 0, 0); self._genre_vbox.setSpacing(1)
        self._genre_vbox.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._rebuild_genre_navs()
        gscroll = QScrollArea(); gscroll.setWidgetResizable(True)
        gscroll.setFrameShape(QFrame.Shape.NoFrame)
        gscroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        gscroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        gscroll.setWidget(self._genre_container)
        cl.addWidget(gscroll, 1)
        cl.addSpacing(12)

        # ── Pro card: upgrade + license in one quiet block ───────────────
        card = QFrame(); card.setObjectName("upgradeCard")
        card.setStyleSheet(f"""
            QFrame#upgradeCard{{background:{C_SURFACE};border-radius:12px;
                border:1px solid {C_BORDER};}}
            QFrame#upgradeCard QLabel{{border:none;background:transparent;}}
        """)
        ccl = QVBoxLayout(card)
        ccl.setContentsMargins(14,14,14,12); ccl.setSpacing(3)
        ct1 = QLabel("TrackTag Pro")
        ct1.setStyleSheet(f"color:{C_TEXT};font-size:13px;font-weight:600;")
        ct2 = QLabel("Automatic tag & cover search")
        ct2.setStyleSheet(f"color:{C_TEXT2};font-size:11px;")
        upbtn = QPushButton("Get Pro"); upbtn.setFixedHeight(32)
        upbtn.setCursor(Qt.CursorShape.PointingHandCursor)
        upbtn.setStyleSheet(_BTN_PRIMARY + "QPushButton{font-size:12px;}")
        upbtn.clicked.connect(lambda: subprocess.Popen(
            ["open", "https://yf1511.github.io/tracktag/#pricing"]))
        ccl.addWidget(ct1); ccl.addWidget(ct2); ccl.addSpacing(10); ccl.addWidget(upbtn)
        self._upgrade_card = card
        cl.addWidget(card)
        cl.addSpacing(6)

        # ── License button ───────────────────────────────────────────────
        act_btn = QPushButton("  Activate License")
        act_btn.setFixedHeight(30)
        try:
            act_btn.setIcon(_ico("fa5s.key", C_TEXT3))
            act_btn.setIconSize(QSize(11, 11))
        except Exception:
            pass
        act_btn.setStyleSheet(self._ACT_STYLE)
        act_btn.clicked.connect(self._open_activate_dialog)
        self._act_btn_ref = act_btn
        cl.addWidget(act_btn)
        root.addWidget(content, 1)

    def _select(self, mode: str, value: str = ""):
        if mode == "genre" and self._sel == ("genre", value):
            mode, value = "all", ""
        self._sel = (mode, value)
        if self._nav_all: self._nav_all.set_active(mode == "all")
        for g, nav in self._genre_navs.items():
            nav.set_active(mode == "genre" and g == value)
        self.nav_filter_changed.emit(mode, value)

    def _add_genre_prompt(self):
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self, "Add Genre", "Genre name:", QLineEdit.EchoMode.Normal)
        if ok and text.strip():
            genre = text.strip()
            if genre not in self._manual:
                self._manual.append(genre)
                self._save_genres()
            if genre not in self._genres:
                self._genres = sorted(set(self._genres) | {genre})
                self._rebuild_genre_navs()

    def _remove_genre(self, genre: str):
        if genre in self._manual:
            self._manual.remove(genre)
            self._save_genres()
        if genre in self._genres:
            self._genres.remove(genre)
            if self._sel == ("genre", genre):
                self._select("all", "")
            self._rebuild_genre_navs()

    def _rebuild_genre_navs(self):
        # Clear old items
        for nav in self._genre_navs.values():
            nav.setParent(None)
        self._genre_navs.clear()

        if self._genre_vbox is None:
            return

        for genre in self._genres:
            nav = NavItem(genre, "fa5s.tag", "", active=self._sel == ("genre", genre))
            nav.clicked.connect(lambda checked=False, g=genre: self._select("genre", g))

            # Right-click context menu to remove
            nav.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            nav.customContextMenuRequested.connect(
                lambda pos, g=genre, n=nav: self._genre_context_menu(g, n, pos))

            self._genre_navs[genre] = nav
            self._genre_vbox.addWidget(nav)

    def _sync_genres_from_files(self, audio_files):
        """Keep sidebar genres in sync with the current file list."""
        seen = set()
        for af in audio_files:
            g = (getattr(af, 'genre', '') or '').strip()
            seen.add(g if g else 'No Genre')
        self._genres = sorted(seen | set(self._manual))
        self._rebuild_genre_navs()
        self.update_genre_counts(audio_files)

    def update_genre_counts(self, audio_files):
        """Refresh the per-genre track count badges."""
        counts: dict[str, int] = {}
        for af in audio_files:
            g = (getattr(af, 'genre', '') or '').strip() or 'No Genre'
            counts[g] = counts.get(g, 0) + 1
        for g, nav in self._genre_navs.items():
            nav.set_count(counts.get(g, 0))

    def _genre_context_menu(self, genre: str, nav: "NavItem", pos):
        menu = QMenu(self)
        rm = menu.addAction(f'Remove "{genre}"')
        action = menu.exec(nav.mapToGlobal(pos))
        if action == rm:
            self._remove_genre(genre)

    def _open_activate_dialog(self):
        if _is_pro:
            self._open_deactivate_dialog()
        else:
            dlg = ProActivateDialog(self.window())
            dlg.activated.connect(self._on_pro_activated)
            dlg.exec()

    def _open_deactivate_dialog(self):
        dlg = QDialog(self.window())
        dlg.setWindowTitle("Deactivate Pro")
        dlg.setFixedSize(360, 200)
        dlg.setModal(True)
        dlg.setStyleSheet(f"""
            QDialog{{background:{C_SURFACE};border-radius:12px;}}
            QLabel{{background:transparent;border:none;color:{C_TEXT};}}
        """)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(28, 24, 28, 24); lay.setSpacing(12)

        t = QLabel("Deactivate Pro License?")
        t.setStyleSheet(f"color:{C_TEXT};font-size:16px;font-weight:700;")
        sub = QLabel("Your Pro features will be disabled. You can re-activate anytime with your license key.")
        sub.setStyleSheet(f"color:{C_TEXT2};font-size:12px;")
        sub.setWordWrap(True)
        lay.addWidget(t); lay.addWidget(sub); lay.addStretch()

        row = QHBoxLayout(); row.setSpacing(10)
        keep = QPushButton("Keep Pro"); keep.setFixedHeight(38)
        keep.setStyleSheet(f"""
            QPushButton{{background:{C_SURFACE2};color:{C_TEXT};
                border:1px solid {C_BORDER};border-radius:9px;font-size:13px;font-weight:600;}}
            QPushButton:hover{{background:{C_BORDER};}}
        """)
        keep.clicked.connect(dlg.reject)

        deact = QPushButton("Deactivate"); deact.setFixedHeight(38)
        deact.setStyleSheet(f"""
            QPushButton{{background:rgba(244,63,94,0.15);color:{C_DANGER};
                border:1px solid rgba(244,63,94,0.3);border-radius:9px;
                font-size:13px;font-weight:700;}}
            QPushButton:hover{{background:rgba(244,63,94,0.25);}}
        """)
        deact.clicked.connect(dlg.accept)
        row.addWidget(keep, 1); row.addWidget(deact, 1)
        lay.addLayout(row)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            _lic.deactivate()
            self._on_pro_deactivated()

    def _on_pro_activated(self):
        global _is_pro
        _is_pro = True
        if self._beta_lbl:
            self._beta_lbl.setText("PRO")
            self._beta_lbl.setStyleSheet(self._PRO_BADGE_STYLE)
        if getattr(self, '_upgrade_card', None):
            self._upgrade_card.hide()
        if hasattr(self, '_act_btn_ref'):
            self._act_btn_ref.setText("  Pro active")
            self._act_btn_ref.setToolTip("Click to deactivate this license")
            self._act_btn_ref.setEnabled(True)
            try:
                self._act_btn_ref.setIcon(_ico("fa5s.check-circle", C_SUCCESS))
                self._act_btn_ref.setIconSize(QSize(11, 11))
            except Exception:
                pass
            self._act_btn_ref.setStyleSheet(self._ACT_ON_STYLE)
        # Unlock search buttons
        for btn in (getattr(self, '_btn_search_tags', None),
                    getattr(self, '_btn_search_cover', None)):
            if btn:
                btn.setToolTip("")
        self.pro_activated.emit()

    def _on_pro_deactivated(self):
        global _is_pro
        _is_pro = False
        if self._beta_lbl:
            self._beta_lbl.setText(f"v{_APP_VERSION}")
            self._beta_lbl.setStyleSheet(self._VERSION_STYLE)
        if getattr(self, '_upgrade_card', None):
            self._upgrade_card.show()
        if hasattr(self, '_act_btn_ref'):
            self._act_btn_ref.setText("  Activate License")
            self._act_btn_ref.setToolTip("")
            self._act_btn_ref.setEnabled(True)
            try:
                self._act_btn_ref.setIcon(_ico("fa5s.key", C_TEXT3))
                self._act_btn_ref.setIconSize(QSize(11, 11))
            except Exception:
                pass
            self._act_btn_ref.setStyleSheet(self._ACT_STYLE)

    def update_counts(self, total: int):
        if self._nav_all:
            self._nav_all.set_count(total)


# ── Tag panel (right sidebar, no tabs, all fields visible) ────────────────────

class TagPanel(QWidget):
    tags_changed   = pyqtSignal()
    save_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._files: List[AudioFile] = []
        self._loading = False
        self.fields: dict = {}
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(0)
        root.addWidget(self._build_scroll_area(), 1)
        root.addWidget(self._build_bottom_bar())
        self._set_enabled(False)

    def _build_scroll_area(self) -> QScrollArea:
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"QScrollArea{{background:{C_SURFACE};border:none;}}")
        content = QWidget(); content.setStyleSheet(f"background:{C_SURFACE};")
        c = QVBoxLayout(content); c.setContentsMargins(0,0,0,20); c.setSpacing(0)

        # ── Cover section ──────────────────────────────────────────────────
        cov_sec = QWidget(); cov_sec.setStyleSheet(f"background:{C_SURFACE};")
        csv = QVBoxLayout(cov_sec); csv.setContentsMargins(20,20,20,8); csv.setSpacing(10)

        cover_wrap = QFrame()
        cover_wrap.setFixedSize(CoverLabel._SIZE, CoverLabel._SIZE)
        cover_wrap.setStyleSheet("background:transparent;")
        self.cover = CoverLabel(cover_wrap)
        self.cover.setGeometry(0,0,CoverLabel._SIZE,CoverLabel._SIZE)
        self.cover.clicked.connect(self._pick_cover)
        self.cover.cover_changed.connect(self._on_cover)
        _overlay_ss = ("QPushButton{background:rgba(12,13,17,0.72);border:none;"
                       "border-radius:8px;padding:0;}"
                       "QPushButton:hover{background:rgba(12,13,17,0.92);}"
                       "QPushButton:disabled{background:transparent;}")
        edit_btn = QPushButton(cover_wrap)
        edit_btn.setFixedSize(30,30); edit_btn.move(CoverLabel._SIZE-38, 8); edit_btn.raise_()
        edit_btn.setToolTip("Choose artwork…")
        try:
            edit_btn.setIcon(_ico("fa5s.pen", "#ffffff"))
            edit_btn.setIconSize(QSize(11, 11))
        except Exception:
            edit_btn.setText("Edit")
        edit_btn.setStyleSheet(_overlay_ss)
        edit_btn.clicked.connect(self._pick_cover)

        self.del_btn = QPushButton(cover_wrap)
        self.del_btn.setFixedSize(30,30); self.del_btn.move(CoverLabel._SIZE-72, 8)
        self.del_btn.raise_()
        self.del_btn.setToolTip("Remove artwork")
        try:
            self.del_btn.setIcon(_ico("fa5s.trash-alt", "#ffffff"))
            self.del_btn.setIconSize(QSize(11, 11))
        except Exception:
            self.del_btn.setText("×")
        self.del_btn.setStyleSheet(_overlay_ss)
        self.del_btn.clicked.connect(self._del_cover)
        csv.addWidget(cover_wrap, alignment=Qt.AlignmentFlag.AlignHCenter)
        csv.addSpacing(4)

        # Search actions — two equal secondary buttons
        row1 = QHBoxLayout(); row1.setSpacing(8)
        bt = QPushButton("  Search Tags"); bt.setFixedHeight(34)
        bc = QPushButton("  Find Cover");  bc.setFixedHeight(34)
        for b_, icn in ((bt, "fa5s.search"), (bc, "fa5s.image")):
            try:
                b_.setIcon(_ico(icn, C_TEXT2)); b_.setIconSize(QSize(11, 11))
            except Exception:
                pass
            b_.setStyleSheet(_BTN_SECONDARY)
        bt.clicked.connect(lambda: self._search("tags_only"))
        bc.clicked.connect(lambda: self._search("cover_only"))
        row1.addWidget(bt,1); row1.addWidget(bc,1)
        csv.addLayout(row1)
        self._btn_search_tags = bt
        self._btn_search_cover = bc

        # WAV: Rekordbox ignores embedded artwork → offer AIFF
        self._wav_hint = QPushButton("  Convert to AIFF for Rekordbox")
        self._wav_hint.setToolTip(
            "Rekordbox never shows artwork embedded in WAV files.\n"
            "Converting to AIFF is lossless and keeps all tags.")
        from PyQt6.QtWidgets import QSizePolicy
        self._wav_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        try:
            self._wav_hint.setIcon(_ico("fa5s.exchange-alt", C_ACCENT2))
            self._wav_hint.setIconSize(QSize(11, 11))
        except Exception:
            pass
        self._wav_hint.setFixedHeight(32)
        self._wav_hint.setCursor(Qt.CursorShape.PointingHandCursor)
        self._wav_hint.setStyleSheet(
            f"QPushButton{{background:rgba(245,158,11,0.10);color:{C_ACCENT2};"
            f"border:1px solid rgba(245,158,11,0.28);border-radius:8px;"
            f"font-size:11px;font-weight:500;text-align:left;padding-left:10px;}}"
            f"QPushButton:hover{{background:rgba(245,158,11,0.18);}}")
        self._wav_hint.clicked.connect(
            lambda: getattr(self.window(), "_convert_to_aiff", lambda: None)())
        self._wav_hint.hide()
        csv.addWidget(self._wav_hint)
        if not _is_pro:
            bt.setToolTip("✦ Pro feature — upgrade to use")
            bc.setToolTip("✦ Pro feature — upgrade to use")
        c.addWidget(cov_sec)

        # ── TRACK INFO section ─────────────────────────────────────────────
        c.addWidget(self._sec_hdr("Track Info"))

        fi = QWidget(); fi.setStyleSheet(f"background:{C_SURFACE};")
        fv = QVBoxLayout(fi); fv.setContentsMargins(20,4,20,8); fv.setSpacing(8)

        for field, lbl in [("title","Title"), ("artist","Artist"),
                            ("album","Album"), ("genre","Genre"), ("label","Label")]:
            w = self._mk_combo(field) if field == "genre" else self._mk_line(field)
            fv.addLayout(self._frow(lbl, w))

        # Jahr + BPM side by side with equal weight
        jb = QHBoxLayout(); jb.setSpacing(10)
        yw = self._mk_line("year")
        bw = self._mk_line("bpm")
        jb.addLayout(self._frow("Year", yw))
        jb.addLayout(self._frow("BPM",  bw, lbl_w=30))
        fv.addLayout(jb)

        fv.addLayout(self._frow("Key",      self._mk_line("key")))
        fv.addLayout(self._frow("Comment",  self._mk_line("comment")))
        c.addWidget(fi)

        # ── ERWEITERT section ──────────────────────────────────────────────
        c.addWidget(self._sec_hdr("Advanced"))

        ei = QWidget(); ei.setStyleSheet(f"background:{C_SURFACE};")
        ev = QVBoxLayout(ei); ev.setContentsMargins(20,4,20,8); ev.setSpacing(8)
        ev.addLayout(self._frow("Album Artist", self._mk_line("album_artist")))
        ev.addLayout(self._frow("Composer",     self._mk_line("composer")))

        # Track # — half width
        trk_row = QHBoxLayout(); trk_row.setSpacing(0)
        trk_row.addLayout(self._frow("Track #", self._mk_line("track")))
        trk_row.addStretch(1)
        ev.addLayout(trk_row)
        c.addWidget(ei)
        c.addStretch()

        scroll.setWidget(content)
        return scroll

    # ── Section header helper ──────────────────────────────────────────────────
    def _sec_hdr(self, text: str) -> QWidget:
        f = QWidget(); f.setFixedHeight(44)
        f.setStyleSheet(f"background:{C_SURFACE};")
        hl = QHBoxLayout(f); hl.setContentsMargins(20,14,20,4); hl.setSpacing(10)
        lbl = QLabel(text.upper())
        lbl.setStyleSheet(f"color:{C_TEXT3};font-size:10px;font-weight:600;"
                          f"letter-spacing:1.2px;background:transparent;border:none;")
        line = QFrame(); line.setFixedHeight(1)
        line.setStyleSheet(f"background:{C_BORDER};border:none;")
        hl.addWidget(lbl); hl.addWidget(line, 1, Qt.AlignmentFlag.AlignVCenter)
        return f

    # ── Form row helper — label + widget, all fields same right edge ───────────
    def _frow(self, label: str, widget, lbl_w: int = 82) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(10); row.setContentsMargins(0,0,0,0)
        lbl = QLabel(label)
        lbl.setFixedWidth(lbl_w)
        lbl.setStyleSheet(f"color:{C_TEXT2};font-size:12px;"
                          f"background:transparent;border:none;")
        row.addWidget(lbl)
        row.addWidget(widget)
        return row

    def _build_bottom_bar(self) -> QWidget:
        bar = QWidget(); bar.setObjectName("tagBottomBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar.setStyleSheet(f"QWidget#tagBottomBar{{background:{C_SURFACE};"
                          f"border-top:1px solid {C_BORDER};}}")
        bl = QHBoxLayout(bar); bl.setContentsMargins(20,12,20,14); bl.setSpacing(8)

        self.save_btn = QPushButton("Save Changes")
        self.save_btn.setFixedHeight(38); self.save_btn.setShortcut("Ctrl+S")
        self.save_btn.setToolTip("Save (⌘S)")
        self.save_btn.setStyleSheet(_BTN_PRIMARY)
        self.save_btn.clicked.connect(self.save_requested.emit)
        bl.addWidget(self.save_btn, 1)

        self.rename_btn = QPushButton()
        self.rename_btn.setFixedSize(38,38)
        self.rename_btn.setToolTip("Rename files from tags")
        try:
            self.rename_btn.setIcon(_ico("fa5s.i-cursor", C_TEXT2))
            self.rename_btn.setIconSize(QSize(12, 12))
        except Exception:
            self.rename_btn.setText("Aa")
        self.rename_btn.setStyleSheet(_BTN_SECONDARY + "QPushButton{padding:0;}")
        self.rename_btn.clicked.connect(self._rename)
        bl.addWidget(self.rename_btn)

        more = QPushButton(); more.setFixedSize(38,38)
        more.setToolTip("More")
        try:
            more.setIcon(_ico("fa5s.ellipsis-h", C_TEXT2)); more.setIconSize(QSize(13, 13))
        except Exception:
            more.setText("···")
        more.setStyleSheet(_BTN_SECONDARY + "QPushButton{padding:0;}")
        more.clicked.connect(self._more_menu)
        self._more_btn = more
        bl.addWidget(more)
        return bar

    # ── Field helpers ─────────────────────────────────────────────────────────

    def _mk_line(self, field: str) -> QLineEdit:
        w = QLineEdit(); w.setFixedHeight(30); w.setStyleSheet(_INPUT)
        w.textEdited.connect(lambda t, f=field: self._edited(f, t))
        if field == "key":
            def _norm_key(widget=w):
                normed = normalize_key(widget.text())
                if normed != widget.text():
                    widget.blockSignals(True)
                    widget.setText(normed)
                    widget.blockSignals(False)
                    self._edited("key", normed)
            w.editingFinished.connect(_norm_key)
        self.fields[field] = w; return w

    def _mk_combo(self, field: str) -> NoScrollComboBox:
        w = NoScrollComboBox(); w.setEditable(True); w.setFixedHeight(30)
        w.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        w.addItems([""]+DJ_GENRES); w.setCurrentText("")
        cp=QCompleter(DJ_GENRES); cp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        cp.setFilterMode(Qt.MatchFlag.MatchContains); w.setCompleter(cp)
        w.setStyleSheet(_COMBO)
        w.currentTextChanged.connect(lambda t, f=field: self._edited(f, t))
        self.fields[field] = w; return w

    def _set_enabled(self, on: bool):
        for w in self.fields.values(): w.setEnabled(on)
        self.save_btn.setEnabled(on)
        self.rename_btn.setEnabled(on)
        self.cover.setEnabled(on)
        self.del_btn.setEnabled(on)

    # ── Data loading ──────────────────────────────────────────────────────────

    def load_files(self, files: List[AudioFile]):
        self._loading = True; self._files = files
        if not files:
            for w in self.fields.values():
                (w.setCurrentText if isinstance(w,QComboBox) else w.setText)("")
            self.cover.clear_cover(); self.del_btn.hide(); self._set_enabled(False)
            self._wav_hint.hide()
            self._loading = False; return
        self._set_enabled(True)
        self._wav_hint.setVisible(any(f.extension == ".wav" for f in files))
        n = len(files)
        self._btn_search_tags.setText(f"  Auto-Tag {n}" if n > 1 else "  Search Tags")
        self._btn_search_cover.setText(f"  Find {n} Covers" if n > 1 else "  Find Cover")
        if len(files) == 1:
            f = files[0]
            for field, w in self.fields.items():
                val = getattr(f, field, "")
                if field == "key": val = normalize_key(val)
                if isinstance(w,QComboBox): w.setCurrentText(val)
                else: w.setText(val); w.setPlaceholderText("")
            self._show_cover(f.cover_data)
        else:
            for field, w in self.fields.items():
                vals = {getattr(f, field, "") for f in files}
                common = vals.pop() if len(vals)==1 else None
                if isinstance(w,QComboBox): w.setCurrentText(common or "")
                else:
                    w.setText(common or "")
                    w.setPlaceholderText("" if common is not None else "← multiple values")
            self._show_cover(files[0].cover_data if files else None)
        self._loading = False

    def _show_cover(self, data: Optional[bytes]):
        self.del_btn.setVisible(bool(data))
        if data:
            pix = QPixmap(); pix.loadFromData(data)
            if not pix.isNull():
                self.cover.show_pixmap(pix); return
        self.cover.clear_cover()

    def _edited(self, field, value):
        if self._loading: return
        for f in self._files: f.set_field(field, value)
        self.tags_changed.emit()

    def _on_cover(self, data, mime):
        self.del_btn.show()
        if self._loading: return
        for f in self._files: f.set_cover(data, mime)
        self.tags_changed.emit()

    # ── Cover actions ─────────────────────────────────────────────────────────

    def _pick_cover(self):
        if not self._files: return
        path,_ = QFileDialog.getOpenFileName(self,"Select Cover","",
            "Images (*.jpg *.jpeg *.png *.bmp *.webp)")
        if not path: return
        with open(path,"rb") as fh: data=fh.read()
        self.cover.set_cover_data(data, "image/png" if path.endswith(".png") else "image/jpeg")

    def _paste(self):
        if not self._files: return
        cb=QApplication.clipboard(); img=cb.image()
        if not img.isNull():
            buf=QBuffer(); buf.open(QIODevice.OpenModeFlag.WriteOnly)
            img.save(buf,"JPEG",95); self.cover.set_cover_data(bytes(buf.data()),"image/jpeg"); return
        text=cb.text().strip()
        if text and Path(text).is_file():
            with open(text,"rb") as fh: data=fh.read()
            self.cover.set_cover_data(data,"image/jpeg")

    def _del_cover(self):
        if not self._files: return
        for f in self._files: f.clear_cover()
        self.cover.clear_cover(); self.del_btn.hide(); self.tags_changed.emit()

    def _search(self, preset="all"):
        if not _is_pro:
            self._show_pro_prompt()
            return
        if not self._files: return
        if len(self._files) > 1 and hasattr(self.window(), "_auto_tag"):
            self.window()._auto_tag(preset)
            return
        f=self._files[0]
        artist, title = f.artist.strip(), f.title.strip()
        if not title:   # untagged file: search by its name
            title = Path(f.filename).stem.replace("_", " ")
        current = {k: str(getattr(f, k, "") or "") for k in
                   ("artist","title","album","genre","label","year","bpm","key")}
        current["key"] = normalize_key(current["key"])
        dlg=MetaSearchDialog(artist=artist, title=title, album=f.album, preset=preset,
                             current=current, duration=float(getattr(f, "duration", 0) or 0),
                             has_cover=bool(f.cover_data), parent=self)
        dlg.result_selected.connect(self._apply); dlg.exec()

    def _show_pro_prompt(self):
        from PyQt6.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setWindowTitle("Pro Feature")
        msg.setText("✦  This feature requires TrackTag Pro.")
        msg.setInformativeText("Upgrade to Pro to unlock automatic tag & cover search.")
        msg.setStandardButtons(QMessageBox.StandardButton.Cancel)
        upgrade = msg.addButton("Upgrade to Pro →", QMessageBox.ButtonRole.AcceptRole)
        msg.setDefaultButton(upgrade)
        msg.exec()
        if msg.clickedButton() == upgrade:
            import subprocess
            subprocess.Popen(["open", "https://yf1511.github.io/tracktag/#pricing"])

    def _apply(self, payload: dict):
        if not self._files: return
        if "cover_data" in payload:
            self.cover.set_cover_data(payload["cover_data"],
                                       payload.get("cover_mime","image/jpeg"))
        for key in ("artist","title","album","genre","label","year","bpm","key"):
            if key in payload:
                val = payload[key]
                if key == "key": val = normalize_key(val)
                w = self.fields.get(key)
                if w: (w.setCurrentText if isinstance(w,QComboBox) else w.setText)(val)
                for f in self._files: f.set_field(key, val)
        self.tags_changed.emit()

    def _more_menu(self):
        menu=QMenu(self)
        menu.addAction("Rename Files from Tags").triggered.connect(self._rename)
        menu.addSeparator()
        menu.addAction("Cover from File…").triggered.connect(self._pick_cover)
        menu.addAction("Paste Cover  ⌘V").triggered.connect(self._paste)
        menu.addAction("Remove Cover").triggered.connect(self._del_cover)
        m = menu.sizeHint()
        menu.exec(self._more_btn.mapToGlobal(
            self._more_btn.rect().topRight() - QPoint(m.width(), m.height() + 6)))

    def _rename(self):
        if not self._files: return
        _INV=set('/\\:*?"<>|')
        def san(s): return "".join("_" if c in _INV else c for c in s).strip(" .")
        pattern = _SETTINGS().value("rename_pattern", "%artist% - %title%", str) or "%artist% - %title%"
        renamed,skipped,errors=0,0,[]
        for f in self._files:
            name = pattern
            for tok, val in (("%artist%",f.artist),("%title%",f.title),
                              ("%album%",f.album),("%year%",f.year),
                              ("%bpm%",f.bpm),("%key%",f.key)):
                name = name.replace(tok, san(val.strip()))
            name = name.strip(" -_.")
            if not name or "%" in name:
                skipped+=1; errors.append(f"'{f.filename}' — missing tag values for pattern"); continue
            nn=f"{name}{f.extension}"
            np=os.path.join(os.path.dirname(f.path),nn)
            if f.path==np: skipped+=1; continue
            if os.path.exists(np):
                errors.append(f"'{nn}' already exists"); skipped+=1; continue
            try:
                os.rename(f.path,np); f.path=np; f.filename=nn; renamed+=1
            except OSError as e: errors.append(f"'{f.filename}': {e}")
        self.tags_changed.emit()
        if errors:
            QMessageBox.warning(self.window(), "Rename",
                f"{renamed} renamed, {skipped} skipped.\n\n" + "\n".join(errors))

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.StandardKey.Paste): self._paste()
        else: super().keyPressEvent(e)


# ── Quick Look preview helper ─────────────────────────────────────────────────

def _quick_look(path: str):
    """Open macOS Quick Look for the given file path."""
    try:
        subprocess.Popen(
            ['qlmanage', '-p', path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as ex:
        print(f"Quick Look failed: {ex}")


# ── Settings dialog ───────────────────────────────────────────────────────────

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setFixedSize(520, 500)
        self.setModal(True)
        self.setStyleSheet(f"""
            QDialog {{background:{C_SURFACE};}}
            QLabel {{background:transparent;border:none;color:{C_TEXT};}}
            QTabBar::tab {{
                background:{C_SURFACE2};color:{C_TEXT2};
                border:1px solid {C_BORDER};border-bottom:none;
                border-radius:6px 6px 0 0;
                padding:6px 16px;font-size:12px;font-weight:600;
                margin-right:2px;
            }}
            QTabBar::tab:selected {{background:{C_SURFACE3};color:{C_TEXT};border-color:{C_BORDER2};}}
            QTabWidget::pane {{
                border:1px solid {C_BORDER};border-radius:0 8px 8px 8px;
                background:{C_SURFACE2};
            }}
        """)
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 24)
        root.setSpacing(16)

        # Header
        hdr = QHBoxLayout(); hdr.setSpacing(10)
        try:
            ic = QLabel()
            ic.setPixmap(_ico("fa5s.sliders-h", C_PRIMARY).pixmap(20, 20))
            ic.setFixedSize(20, 20)
            hdr.addWidget(ic)
        except Exception:
            pass
        title = QLabel("Settings")
        title.setStyleSheet(f"color:{C_TEXT};font-size:18px;font-weight:700;")
        hdr.addWidget(title, 1)
        root.addLayout(hdr)

        # Tabs
        from PyQt6.QtWidgets import QTabWidget
        tabs = QTabWidget()
        tabs.addTab(self._general_tab(), "General")
        tabs.addTab(self._metadata_tab(), "Metadata")
        tabs.addTab(self._about_tab(), "About")
        root.addWidget(tabs, 1)

        # Close button
        close = QPushButton("Done")
        close.setFixedHeight(38)
        close.setStyleSheet(_BTN_PRIMARY)
        close.clicked.connect(self.accept)
        root.addWidget(close)

    def _section(self, text: str) -> QLabel:
        l = QLabel(text)
        l.setStyleSheet(
            f"color:{C_TEXT3};font-size:9px;font-weight:700;"
            f"letter-spacing:1.4px;background:transparent;border:none;")
        return l

    def _field_row(self, label: str, widget) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(12)
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color:{C_TEXT2};font-size:12px;")
        lbl.setFixedWidth(160)
        row.addWidget(lbl)
        row.addWidget(widget, 1)
        return row

    def _general_tab(self) -> QWidget:
        w = QWidget(); w.setStyleSheet(f"background:{C_SURFACE2};")
        v = QVBoxLayout(w); v.setContentsMargins(20, 16, 20, 16); v.setSpacing(10)
        s = _SETTINGS()

        v.addWidget(self._section("FILE RENAMING"))
        v.addSpacing(2)
        pattern = QLineEdit(s.value("rename_pattern", "%artist% - %title%", str))
        pattern.setFixedHeight(34); pattern.setStyleSheet(_INPUT)
        pattern.textChanged.connect(
            lambda t: _SETTINGS().setValue("rename_pattern", t.strip() or "%artist% - %title%"))
        v.addLayout(self._field_row("Rename Pattern", pattern))
        hint = QLabel("Available tags: %artist%  %title%  %album%  %year%  %bpm%  %key%")
        hint.setStyleSheet(f"color:{C_TEXT3};font-size:10px;padding-left:172px;")
        v.addWidget(hint)
        v.addSpacing(6)

        v.addWidget(self._section("SORT & DISPLAY"))
        v.addSpacing(2)
        sort_combo = QComboBox()
        sort_combo.addItems(["None", "Title", "Artist", "Genre", "BPM", "Key"])
        sort_combo.setCurrentText(s.value("default_sort", "None", str))
        sort_combo.setFixedHeight(34); sort_combo.setStyleSheet(_COMBO)
        sort_combo.currentTextChanged.connect(
            lambda t: _SETTINGS().setValue("default_sort", t))
        v.addLayout(self._field_row("Default Sort", sort_combo))
        sort_hint = QLabel("Applied when files are added to the library.")
        sort_hint.setStyleSheet(f"color:{C_TEXT3};font-size:10px;padding-left:172px;")
        v.addWidget(sort_hint)
        v.addSpacing(6)

        v.addWidget(self._section("PREVIEW"))
        v.addSpacing(2)
        info = QLabel(
            'Space plays the selected track · ↑/↓ switches tracks while playing\n'
            '←/→ skips 10 s · ⌘Y opens macOS Quick Look.')
        info.setStyleSheet(f"color:{C_TEXT2};font-size:12px;background:transparent;border:none;")
        info.setWordWrap(True)
        v.addWidget(info)
        v.addStretch()
        return w

    def _metadata_tab(self) -> QWidget:
        w = QWidget(); w.setStyleSheet(f"background:{C_SURFACE2};")
        v = QVBoxLayout(w); v.setContentsMargins(20, 16, 20, 16); v.setSpacing(10)
        s = _SETTINGS()

        v.addWidget(self._section("COVER ART"))
        v.addSpacing(2)
        _size_opts = {"1000×1000 px": 1000, "1500×1500 px": 1500,
                      "3000×3000 px": 3000, "Original size": 0}
        cover_combo = QComboBox()
        cover_combo.addItems(list(_size_opts.keys()))
        cur = int(s.value("cover_max_size", 1000))
        for lbl, px in _size_opts.items():
            if px == cur:
                cover_combo.setCurrentText(lbl); break
        cover_combo.setFixedHeight(34); cover_combo.setStyleSheet(_COMBO)
        cover_combo.currentTextChanged.connect(
            lambda t: _SETTINGS().setValue("cover_max_size", _size_opts.get(t, 1000)))
        v.addLayout(self._field_row("Max Cover Size", cover_combo))
        cov_hint = QLabel("Covers are converted to JPEG on save (required by Rekordbox).")
        cov_hint.setStyleSheet(f"color:{C_TEXT3};font-size:10px;padding-left:172px;")
        v.addWidget(cov_hint)
        v.addSpacing(6)

        v.addWidget(self._section("KEY NOTATION"))
        v.addSpacing(2)
        key_combo = QComboBox()
        for code, label in KEY_FORMATS.items():
            key_combo.addItem(label, code)
        key_combo.setCurrentIndex(max(0, key_combo.findData(_key_format)))
        key_combo.setFixedHeight(34); key_combo.setStyleSheet(_COMBO)
        key_combo.currentIndexChanged.connect(
            lambda i: set_key_format(key_combo.itemData(i)))
        v.addLayout(self._field_row("Key Format", key_combo))
        key_hint = QLabel("Used in the track list and when keys are written to files.")
        key_hint.setStyleSheet(f"color:{C_TEXT3};font-size:10px;padding-left:172px;")
        v.addWidget(key_hint)
        v.addSpacing(6)

        v.addWidget(self._section("TAG FORMAT"))
        v.addSpacing(2)
        fmt_info = QLabel(
            "Tags are written as ID3v2.3 with maximum DJ software compatibility\n"
            "(Rekordbox, Serato, Traktor). WAV/AIFF use UTF-16 encoding as required\n"
            "by the strict ID3v2.3 spec; MP3 uses UTF-8.")
        fmt_info.setStyleSheet(f"color:{C_TEXT2};font-size:12px;background:transparent;border:none;")
        fmt_info.setWordWrap(True)
        v.addWidget(fmt_info)
        v.addStretch()
        return w

    def _about_tab(self) -> QWidget:
        w = QWidget(); w.setStyleSheet(f"background:{C_SURFACE2};")
        v = QVBoxLayout(w); v.setContentsMargins(20, 16, 20, 16); v.setSpacing(6)

        # App logo + name
        if os.path.exists(_ICON_PATH):
            logo = QLabel()
            logo.setPixmap(QPixmap(_ICON_PATH).scaled(52, 52,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.addWidget(logo, alignment=Qt.AlignmentFlag.AlignHCenter)
        app_name = QLabel("TrackTag")
        app_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        app_name.setStyleSheet(f"color:{C_TEXT};font-size:20px;font-weight:800;")
        v.addWidget(app_name)
        version = QLabel(f"Version {_APP_VERSION}")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version.setStyleSheet(f"color:{C_TEXT3};font-size:11px;")
        v.addWidget(version)
        v.addSpacing(10)

        v.addWidget(self._section("KEYBOARD SHORTCUTS"))
        v.addSpacing(4)

        shortcuts = [
            ("⌘O",          "Open files"),
            ("⌘⇧O",         "Open folder"),
            ("⌘S",          "Save selection"),
            ("⌘⇧S",         "Save all"),
            ("Space",        "Play / Pause"),
            ("← / →",        "Skip 10 s  (⇧ 30 s)"),
            ("↑ / ↓",        "Next / previous track"),
            ("⌘Y",          "Quick Look preview"),
            ("⌘K",          "Focus search"),
            ("⌘A",          "Select all"),
            ("⌘F",          "Search tags"),
            ("⌘V",          "Paste cover art"),
            ("Backspace",    "Remove from list"),
        ]
        for key, desc in shortcuts:
            row = QHBoxLayout(); row.setSpacing(10)
            k = QLabel(key); k.setFixedWidth(90)
            k.setAlignment(Qt.AlignmentFlag.AlignCenter)
            k.setStyleSheet(
                f"background:{C_SURFACE};color:{C_TEXT};font-size:11px;font-weight:600;"
                f"border:1px solid {C_BORDER};border-radius:6px;padding:3px 8px;")
            d = QLabel(desc)
            d.setStyleSheet(f"color:{C_TEXT2};font-size:12px;")
            row.addWidget(k); row.addWidget(d, 1)
            v.addLayout(row)
        v.addStretch()
        return w


# ── File table ────────────────────────────────────────────────────────────────

class FileTable(QTableWidget):
    files_dropped = pyqtSignal(list)
    space_pressed = pyqtSignal()
    seek_requested = pyqtSignal(int)       # seconds, ±
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
    def keyPressEvent(self, e):
        # The view would use Space for selection — TrackTag uses it for Quick Look
        if e.key() == Qt.Key.Key_Space and not e.modifiers():
            if not e.isAutoRepeat(): self.space_pressed.emit()
            e.accept(); return
        # ←/→ skip through the playing track (⇧ = bigger steps)
        if e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = 30 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 10
            self.seek_requested.emit(step if e.key() == Qt.Key.Key_Right else -step)
            e.accept(); return
        super().keyPressEvent(e)
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls(): e.acceptProposedAction()
    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls(): e.acceptProposedAction()
    def dropEvent(self, e):
        paths=_audio_paths(e.mimeData().urls())
        if paths: self.files_dropped.emit(paths)
        e.acceptProposedAction()

def _audio_paths_from_urls(urls) -> List[str]:
    return _audio_paths(urls)

def _is_audio(name: str) -> bool:
    # "._Track.mp3" are macOS metadata files on USB sticks / exFAT, not audio
    return not name.startswith(".") and Path(name).suffix.lower() in SUPPORTED_EXTENSIONS


def _audio_paths(urls) -> List[str]:
    paths=[]
    for url in urls:
        p=url.toLocalFile()
        if os.path.isfile(p) and _is_audio(os.path.basename(p)):
            paths.append(p)
        elif os.path.isdir(p):
            paths += _walk_audio(p)
    return paths


def _walk_audio(folder: str) -> List[str]:
    return [os.path.join(root, name)
            for root, dirs, files in os.walk(folder)
            if not os.path.basename(root).startswith(".")
            for name in sorted(files) if _is_audio(name)]


def _missing_fields(af) -> List[str]:
    """DJ essentials a track is missing (used by the Incomplete filter)."""
    out = [f for f in ("bpm", "key", "genre") if not str(getattr(af, f, "") or "").strip()]
    if not af.cover_data:
        out.append("cover")
    return out


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TrackTag")
        self.setMinimumSize(1000,640)
        self.resize(1440,900)
        self.audio_files: List[AudioFile] = []
        self._active_genre_filter: Optional[str] = None
        self._active_bpm_min: Optional[int] = None
        self._active_bpm_max: Optional[int] = None
        self.setAcceptDrops(True)
        if os.path.exists(_ICON_PATH): self.setWindowIcon(QIcon(_ICON_PATH))
        self._setup_ui()
        self._setup_menu()
        self._mac_tb_done = False

    def _setup_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        root = QVBoxLayout(central); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        # ── Main area (nav + center + tag panel) ──────────────────────
        main_area = QWidget(); main_area.setStyleSheet(f"background:{C_BG};")
        ml = QHBoxLayout(main_area); ml.setContentsMargins(0,0,0,0); ml.setSpacing(0)

        # Nav sidebar
        self.nav = NavSidebar()
        self.nav.add_files_clicked.connect(self._open_files)
        self.nav.nav_filter_changed.connect(self._nav_filter)
        self._nav_mode = "all"   # 'all' | 'genre'
        self._nav_value = ""
        ml.addWidget(self.nav)

        # Right area (search + splitter)
        right = QWidget(); right.setStyleSheet(f"background:{C_BG};")
        rl = QVBoxLayout(right); rl.setContentsMargins(0,0,0,0); rl.setSpacing(0)

        # Top search bar
        topbar = QFrame(); topbar.setFixedHeight(60)
        topbar.setStyleSheet(f"QFrame{{background:{C_BG};border-bottom:1px solid {C_BORDER};}}")
        tbl = QHBoxLayout(topbar); tbl.setContentsMargins(24,0,16,0); tbl.setSpacing(8)

        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("Search title, artist, label…      ⌘K")
        self.search_field.setFixedHeight(34)
        self.search_field.setMaximumWidth(520)
        self.search_field.setClearButtonEnabled(True)
        try:
            self.search_field.addAction(_ico("fa5s.search", C_TEXT3),
                                        QLineEdit.ActionPosition.LeadingPosition)
        except Exception:
            pass
        self.search_field.setStyleSheet(f"""
            QLineEdit{{background:{C_SURFACE2};color:{C_TEXT};border:1px solid {C_BORDER};
                       border-radius:9px;padding:0 8px;font-size:13px;}}
            QLineEdit:hover{{border-color:{C_BORDER2};}}
            QLineEdit:focus{{border-color:{C_PRIMARY};background:{C_BG};}}
        """)
        self.search_field.textChanged.connect(self._filter)
        tbl.addWidget(self.search_field, 1)
        tbl.addStretch()

        _sc_focus = QShortcut(QKeySequence("Ctrl+K"), self)
        _sc_focus.activated.connect(
            lambda: (self.search_field.setFocus(), self.search_field.selectAll()))

        # Settings button (opens SettingsDialog)
        settings_btn = QPushButton(); settings_btn.setFixedSize(34,34)
        settings_btn.setToolTip("Settings")
        settings_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;border-radius:8px;padding:0;}}"
            f"QPushButton:hover{{background:{C_SURFACE2};}}")
        try:
            settings_btn.setIcon(_ico("fa5s.sliders-h", C_TEXT2))
            settings_btn.setIconSize(QSize(15,15))
        except Exception:
            pass
        settings_btn.clicked.connect(self._open_settings)
        tbl.addWidget(settings_btn)
        rl.addWidget(topbar)

        # Splitter: center table | tag panel
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet(f"QSplitter::handle{{background:{C_BORDER};}}")

        # Center
        center = QWidget(); center.setStyleSheet(f"background:{C_BG};")
        cl = QVBoxLayout(center); cl.setContentsMargins(0,0,0,0); cl.setSpacing(0)

        # List header
        lh = QWidget(); lh.setFixedHeight(68); lh.setStyleSheet(f"background:{C_BG};")
        lhl = QHBoxLayout(lh); lhl.setContentsMargins(24,4,20,0); lhl.setSpacing(10)
        self._list_title = QLabel("All Tracks")
        self._list_title.setStyleSheet(
            f"color:{C_TEXT};font-size:20px;font-weight:700;background:transparent;")
        self.count_lbl = QLabel("0 files")
        self.count_lbl.setStyleSheet(
            f"color:{C_TEXT3};font-size:13px;background:transparent;padding-top:3px;")
        lhl.addWidget(self._list_title); lhl.addWidget(self.count_lbl); lhl.addStretch()
        cl.addWidget(lh)
        fbl = lhl   # filter pills sit on the right of the header row
        fbl.setSpacing(6)

        _pill_style = (f"QPushButton{{background:transparent;color:{C_TEXT2};"
                       f"border:1px solid {C_BORDER};border-radius:14px;"
                       f"font-size:12px;font-weight:500;padding:0 12px;}}"
                       f"QPushButton:hover{{background:{C_SURFACE2};color:{C_TEXT};}}"
                       f"QPushButton:checked{{background:{C_PRIMARY_SOFT};color:#c4b5fd;"
                       f"border-color:rgba(139,92,246,0.45);}}")

        self._all_btn = QPushButton("All"); self._all_btn.setFixedHeight(28)
        self._all_btn.setCheckable(True); self._all_btn.setChecked(True)
        self._all_btn.setStyleSheet(_pill_style)
        self._all_btn.clicked.connect(self._reset_filters)

        self._genre_btn = QPushButton("Genre  ▾"); self._genre_btn.setFixedHeight(28)
        self._genre_btn.setStyleSheet(_pill_style)
        self._genre_btn.clicked.connect(self._pick_genre_filter)

        self._bpm_btn = QPushButton("BPM  ▾"); self._bpm_btn.setFixedHeight(28)
        self._bpm_btn.setStyleSheet(_pill_style)
        self._bpm_btn.clicked.connect(self._pick_bpm_filter)

        self._incomplete_btn = QPushButton("Incomplete"); self._incomplete_btn.setFixedHeight(28)
        self._incomplete_btn.setCheckable(True)
        self._incomplete_btn.setToolTip("Tracks missing BPM, key, genre or artwork")
        self._incomplete_btn.setStyleSheet(_pill_style)
        self._incomplete_btn.toggled.connect(lambda _: (self._sync_all_pill(), self._apply_filters()))

        fbl.addWidget(self._all_btn)
        fbl.addWidget(self._genre_btn)
        fbl.addWidget(self._bpm_btn)
        fbl.addWidget(self._incomplete_btn)

        # Table
        self.table = FileTable()
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHorizontalHeaderLabels([c[1] for c in COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(TrackDelegate.ROW_H)
        self.table.setIconSize(QSize(TrackDelegate.ART, TrackDelegate.ART))
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.setWordWrap(False)
        self.table.viewport().setMouseTracking(True)
        self.table.setMouseTracking(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setDefaultSectionSize(130)
        hh.setMinimumSectionSize(36)
        hh.setSectionResizeMode(_NUM_COL, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(_COVER_COL, QHeaderView.ResizeMode.Fixed)
        hh.setSortIndicatorShown(True)
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter)
        hh.setHighlightSections(False)
        hh.setFixedHeight(34)
        # DJ-first visual order: BPM + Key right after Artist (logical indices unchanged)
        for to_pos, logical in ((4, _BPM_COL), (5, _KEY_COL)):
            hh.moveSection(hh.visualIndex(logical), to_pos)
        self._apply_default_col_widths()
        # Restore user-adjusted column widths from the last session
        try:
            saved = _SETTINGS().value("column_widths_v2")
            if isinstance(saved, list) and len(saved) == len(COLUMNS):
                for i, wdt in enumerate(saved):
                    if i not in (_NUM_COL, _COVER_COL):
                        self.table.setColumnWidth(i, max(36, int(wdt)))
                self._cols_restored = True
        except Exception:
            pass
        self.table.setStyleSheet(f"""
            QTableWidget{{background:{C_BG};gridline-color:transparent;border:none;
                          selection-background-color:transparent;outline:none;font-size:12px;}}
            QTableWidget::item{{border:none;padding:0;}}
            QHeaderView{{background:{C_BG};border:none;}}
            QHeaderView::section{{background:{C_BG};color:{C_TEXT3};border:none;
                border-bottom:1px solid {C_BORDER};
                padding:0 10px;font-weight:600;font-size:10px;letter-spacing:0.9px;}}
            QHeaderView::section:hover{{color:{C_TEXT2};}}
        """)
        self._delegate = TrackDelegate(self.table)
        self.table.setItemDelegate(self._delegate)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.itemSelectionChanged.connect(self._on_sel)
        self.table.files_dropped.connect(self._add_files)
        self.table.space_pressed.connect(self._toggle_play)
        cl.addWidget(self.table,1)
        self.player_bar = PlayerBar()
        self.player_bar.playing_changed.connect(self._on_playing_changed)
        self.table.seek_requested.connect(self.player_bar.seek_by)
        self.table.cellDoubleClicked.connect(
            lambda r, _c: self.player_bar.load(
                self.table.item(r, _NUM_COL).data(Qt.ItemDataRole.UserRole), autoplay=True))
        cl.addWidget(self.player_bar)
        _ql = QShortcut(QKeySequence("Ctrl+Y"), self)
        _ql.activated.connect(self._quick_look_sel)
        splitter.addWidget(center)

        # Tag panel
        self.tag_panel = TagPanel()
        self.tag_panel.setMinimumWidth(328)
        self.tag_panel.tags_changed.connect(self._refresh_sel)
        self.tag_panel.save_requested.connect(self._save_sel)
        splitter.addWidget(self.tag_panel)
        splitter.setSizes([1080,328]); splitter.setStretchFactor(0,1)

        rl.addWidget(splitter,1)
        ml.addWidget(right,1)
        root.addWidget(main_area,1)


        # ── Full-window drag overlay (shown on dragEnter) ─────────────
        self._drag_overlay = DragOverlay(central)
        self._drag_overlay.setGeometry(central.rect())

        self._update_status()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, '_drag_overlay') and self.centralWidget():
            self._drag_overlay.setGeometry(self.centralWidget().rect())

    def showEvent(self, e):
        super().showEvent(e)
        if not self._mac_tb_done:
            self._mac_tb_done = True
            QTimer.singleShot(0, lambda: _apply_mac_titlebar(int(self.winId())))
            # License verify + update check after window is fully shown
            QTimer.singleShot(1000, self._post_show_checks)

    def _post_show_checks(self):
        """Runs 1 s after window shows — license verify + update check."""
        # License: check in background thread using a proper signal
        try:
            from PyQt6.QtCore import QThread, pyqtSignal as _sig

            class _LicThread(QThread):
                is_pro = _sig(bool)
                def run(self):
                    try:
                        result = _lic.verify_background()
                    except Exception:
                        result = False
                    self.is_pro.emit(result)

            self._lic_thread = _LicThread()
            self._lic_thread.is_pro.connect(self._on_lic_verified)
            self._lic_thread.start()
        except Exception as ex:
            print(f"[license] startup check failed: {ex}")

        # Update check
        try:
            self._update_checker = UpdateChecker(self)
            self._update_checker.start()
        except Exception as ex:
            print(f"[updater] startup check failed: {ex}")

    def _on_lic_verified(self, active: bool):
        global _is_pro
        if active:
            _is_pro = True
            if hasattr(self, 'nav'):
                self.nav._on_pro_activated()

    def _setup_menu(self):
        mb=self.menuBar()
        fm=mb.addMenu("File")
        self._act(fm,"Open Files…",      self._open_files,  "Ctrl+O")
        self._act(fm,"Open Folder…",     self._open_folder, "Ctrl+Shift+O")
        fm.addSeparator()
        self._act(fm,"Save Selection",   self._save_sel,    "Ctrl+S")
        self._act(fm,"Save All",         self._save_all,    "Ctrl+Shift+S")
        fm.addSeparator()
        self._act(fm,"Close Window",     self.close,        "Ctrl+W")
        self._act(fm,"Quit",             self.close,        "Ctrl+Q")
        em=mb.addMenu("Edit")
        self._act(em,"Select All",       self.table.selectAll,"Ctrl+A")
        self._act(em,"Remove Selected",  self._remove_sel,  "Backspace")
        self._act(em,"Clear List",       self._clear_all)
        em.addSeparator()
        self._act(em,"Fit Columns",      self._fit_cols,    "Ctrl+Shift+R")
        em.addSeparator()
        self._act(em,"Convert WAV to AIFF…", self._convert_to_aiff)
        sm=mb.addMenu("Search")
        self._act(sm,"Search Tags…",
            lambda: self.tag_panel._search("tags_only"),  "Ctrl+F")
        self._act(sm,"Cover Only…",
            lambda: self.tag_panel._search("cover_only"), "Ctrl+Shift+F")
        sm.addSeparator()
        self._act(sm,"Auto-Tag Selection…", self._auto_tag, "Ctrl+Shift+T")

    @staticmethod
    def _act(menu, label, slot, sc=None):
        a=QAction(label); a.triggered.connect(slot)
        if sc: a.setShortcut(sc)
        menu.addAction(a)

    # ── Player controls ───────────────────────────────────────────────────────

    def _auto_tag(self, preset: str = "all"):
        if not _is_pro:
            self.tag_panel._show_pro_prompt(); return
        files = self._sel_files()
        if not files:
            files = [self.table.item(r, _NUM_COL).data(Qt.ItemDataRole.UserRole)
                     for r in range(self.table.rowCount())
                     if not self.table.isRowHidden(r) and self.table.item(r, _NUM_COL)]
        if not files: return
        dlg = BatchTagDialog(files, preset=preset, parent=self)
        dlg.applied.connect(self._on_auto_tagged)
        dlg.exec()

    def _convert_to_aiff(self):
        wavs = [f for f in self._sel_files() if f.extension == ".wav"]
        if not wavs: return
        n = len(wavs)
        r = QMessageBox.question(self, "Convert to AIFF",
            f"Convert {n} WAV file{'s' if n != 1 else ''} to AIFF?\n\n"
            "The audio stays bit-for-bit identical. Tags, artwork and cue data are "
            "carried over, and the WAV files are moved to the Trash.\n\n"
            "Tracks already in Rekordbox: remove the old WAV entry and import the AIFF.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok)
        if r != QMessageBox.StandardButton.Ok: return

        from PyQt6.QtWidgets import QProgressDialog
        dlg = QProgressDialog("Converting…", "Cancel", 0, n * 100, self)
        dlg.setWindowTitle("Convert to AIFF"); dlg.setMinimumDuration(0)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        done, errors, kept = 0, [], []
        for i, af in enumerate(wavs):
            if dlg.wasCanceled(): break
            dlg.setLabelText(f"Converting {i + 1} of {n}:  {af.filename}")
            state = self.player_bar.release([af])
            try:
                new_path = wav_to_aiff(af, progress=lambda x, i=i: (
                    dlg.setValue(i * 100 + int(x * 100)), QApplication.processEvents()))
            except (ConvertError, OSError) as ex:
                errors.append(f"{af.filename}: {ex}")
                self.player_bar.resume(state); continue
            if not move_to_trash(af.path):
                kept.append(af.filename)
            new = AudioFile(new_path)
            idx = self.audio_files.index(af)
            self.audio_files[idx] = new
            if state:
                self.player_bar.load(new)
            done += 1
        dlg.setValue(n * 100)
        self._rebuild_table()
        self._on_sel()          # the panel must point at the new AIFF files
        msg = f"✓  Converted {done} file(s) to AIFF."
        if kept: msg += f"  {len(kept)} WAV(s) could not be moved to the Trash."
        self.statusBar().showMessage(msg)
        if errors:
            QMessageBox.warning(self, "Convert to AIFF",
                f"{len(errors)} file(s) were not converted:\n\n" + "\n".join(errors))

    def _on_auto_tagged(self, files: list):
        for af in files: self._refresh_row(af)
        self._sync_genres()
        self._apply_filters()
        self._on_sel()
        if files:
            self.statusBar().showMessage(
                f"✓  Updated {len(files)} track(s) — press ⌘⇧S to save all.")

    def _open_settings(self):
        fmt = _key_format
        dlg = SettingsDialog(self)
        dlg.exec()
        if _key_format != fmt:      # re-render keys in the new notation
            self._rebuild_table()
            self._on_sel()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _apply_default_col_widths(self):
        for i, (field, _) in enumerate(COLUMNS):
            if field in _DEFAULT_COL_W:
                self.table.setColumnWidth(i, _DEFAULT_COL_W[field])
        self.table.setColumnWidth(_NUM_COL, 44)
        self.table.setColumnWidth(_COVER_COL, 56)

    def _fit_cols(self):
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(_NUM_COL, 44)
        self.table.setColumnWidth(_COVER_COL, 56)

    def _filter(self, text: str):
        self._apply_filters(search=text)

    def _reset_filters(self):
        if self._nav_mode != "all":
            self.nav._select("all", "")
        self._active_genre_filter = None
        self._active_bpm_min = None
        self._active_bpm_max = None
        self._genre_btn.setText("Genre  ▾")
        self._bpm_btn.setText("BPM  ▾")
        self._incomplete_btn.blockSignals(True)
        self._incomplete_btn.setChecked(False)
        self._incomplete_btn.blockSignals(False)
        self._all_btn.setChecked(True)
        self._apply_filters()

    def _sync_all_pill(self):
        self._all_btn.setChecked(
            self._active_genre_filter is None and self._active_bpm_min is None
            and not self._incomplete_btn.isChecked()
            and getattr(self, "_nav_mode", "all") == "all")

    def _genre_filter(self, genre: Optional[str], btn: QPushButton = None):
        self._active_genre_filter = genre
        self._genre_btn.setText("Genre  ▾" if not genre else f"Genre: {genre}")
        self._sync_all_pill()
        self._apply_filters()

    def _pick_genre_filter(self):
        # Build list of genres in library
        genres_in_lib = sorted({
            af.genre.strip() for af in self.audio_files if af.genre.strip()
        })
        if not genres_in_lib:
            return
        menu = QMenu(self)
        clear = menu.addAction("All Genres")
        clear.triggered.connect(lambda: self._genre_filter(None, self._genre_btn))
        menu.addSeparator()
        for g in genres_in_lib:
            a = menu.addAction(g)
            a.triggered.connect(lambda checked, gn=g: self._genre_filter(gn, self._genre_btn))
        menu.exec(self._genre_btn.mapToGlobal(self._genre_btn.rect().bottomLeft()))

    def _pick_bpm_filter(self):
        menu = QMenu(self)
        menu.addAction("All BPM").triggered.connect(lambda: self._set_bpm_filter(None, None))
        menu.addSeparator()
        for label, lo, hi in [("< 100 BPM",0,100),("100–125 BPM",100,125),
                               ("125–135 BPM",125,135),("135–150 BPM",135,150),
                               ("> 150 BPM",150,999)]:
            menu.addAction(label).triggered.connect(
                lambda checked, a=lo, b=hi: self._set_bpm_filter(a, b))
        menu.exec(self._bpm_btn.mapToGlobal(self._bpm_btn.rect().bottomLeft()))

    def _set_bpm_filter(self, lo, hi):
        self._active_bpm_min = lo; self._active_bpm_max = hi
        if lo is None:
            self._bpm_btn.setText("BPM  ▾")
        elif hi >= 999:
            self._bpm_btn.setText(f"BPM: > {lo}")
        elif lo == 0:
            self._bpm_btn.setText(f"BPM: < {hi}")
        else:
            self._bpm_btn.setText(f"BPM: {lo}–{hi}")
        self._sync_all_pill()
        self._apply_filters()

    def _apply_filters(self, search: str = None):
        if search is None:
            search = self.search_field.text()
        tl = search.lower().strip()
        genre_f = self._active_genre_filter
        bpm_min = self._active_bpm_min
        bpm_max = self._active_bpm_max
        nav_mode = getattr(self, '_nav_mode', 'all')
        nav_value = getattr(self, '_nav_value', '')

        for row in range(self.table.rowCount()):
            it0 = self.table.item(row, _NUM_COL)
            af = it0.data(Qt.ItemDataRole.UserRole) if it0 else None

            # Nav filter: genre sidebar ("No Genre" matches files without genre)
            if nav_mode == "genre" and nav_value and af:
                af_genre = (af.genre or "").strip()
                if nav_value == "No Genre":
                    if af_genre:
                        self.table.setRowHidden(row, True); continue
                elif af_genre.lower() != nav_value.lower():
                    self.table.setRowHidden(row, True); continue

            # Search text match
            if tl:
                match = any(
                    tl in (self.table.item(row, c).text()
                           if self.table.item(row, c) else "").lower()
                    for c in range(self.table.columnCount()))
            else:
                match = True

            # Genre filter
            if match and genre_f is not None:
                item = self.table.item(row, _GENRE_COL)
                cell = item.text().strip() if item else ""
                match = (cell.lower() == genre_f.lower())

            # BPM filter
            if match and bpm_min is not None:
                item = self.table.item(row, _BPM_COL)
                try:
                    bpm = int(float(item.text().strip())) if item else 0
                except ValueError:
                    bpm = 0
                match = (bpm_min <= bpm <= bpm_max)

            # Incomplete: missing any of the DJ essentials
            if match and self._incomplete_btn.isChecked() and af:
                match = _missing_fields(af) != []

            self.table.setRowHidden(row, not match)

        # Count label reflects the filtered view
        visible = sum(1 for r in range(self.table.rowCount())
                      if not self.table.isRowHidden(r))
        total = len(self.audio_files)
        if visible == total:
            self.count_lbl.setText(f"{total} {'file' if total==1 else 'files'}")
        else:
            self.count_lbl.setText(f"{visible} of {total} files")

    def _nav_filter(self, mode: str, value: str = ""):
        self._nav_mode = mode
        self._nav_value = value
        self._list_title.setText(value if mode == "genre" and value else "All Tracks")
        self._sync_all_pill()
        self._apply_filters()

    def _update_status(self):
        n = len(self.audio_files)
        self.count_lbl.setText(f"{n} {'file' if n==1 else 'files'}")
        self.nav.update_counts(n)
        self.nav.update_genre_counts(self.audio_files)
        if n == 0:
            self.statusBar().showMessage("Drop files here  ·  ⌘O open  ·  ⌘S save")
            return
        ts = sum(f.duration for f in self.audio_files)
        m, s = divmod(int(ts), 60); h, m = divmod(m, 60)
        dur = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        self.statusBar().showMessage(
            f"  {n} file{'s' if n!=1 else ''}  ·  Total {dur}"
            f"  ·  Space play  ·  ←/→ skip  ·  ⌘Y Quick Look  ·  ⌘S save")

    def closeEvent(self, e):
        unsaved=[f for f in self.audio_files if f._modified]
        if unsaved:
            r=QMessageBox.question(self,"Unsaved Changes",
                f"{len(unsaved)} file(s) have unsaved changes.\nSave them before quitting?",
                QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard
                |QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
            if r==QMessageBox.StandardButton.Cancel: e.ignore(); return
            if r==QMessageBox.StandardButton.Save:
                self._save_files(unsaved)
                if any(f._modified for f in unsaved): e.ignore(); return   # a save failed
        if getattr(self, "_update_checker", None) is not None:
            self._update_checker.shutdown()
        threads = (
            getattr(self, "_lic_thread", None),
            getattr(getattr(self, "_update_checker", None), "_thread", None),
        )
        for thread in threads:
            if thread is not None and thread.isRunning():
                thread.wait()
        try:
            _SETTINGS().setValue("column_widths_v2",
                [self.table.columnWidth(i) for i in range(len(COLUMNS))])
        except Exception:
            pass
        e.accept()

    # ── File management ───────────────────────────────────────────────────────

    def _open_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,"Open Audio Files","",
            "Audio (*.mp3 *.flac *.wav *.aiff *.aif *.m4a *.mp4)")
        if paths: self._add_files(paths)

    def _open_folder(self):
        folder=QFileDialog.getExistingDirectory(self,"Open Folder")
        if not folder: return
        paths=_walk_audio(folder)
        if paths: self._add_files(paths)

    def _sync_genres(self):
        self.nav._sync_genres_from_files(self.audio_files)
        if self._nav_mode == "genre" and self._nav_value not in self.nav._genres:
            self.nav._sel = ("genre", self._nav_value)   # force-reset, not toggle
            self.nav._select("all", "")

    def _add_files(self, paths: List[str]):
        existing={f.path for f in self.audio_files}
        new=[p for p in paths if p not in existing]
        if not new: return
        self.statusBar().showMessage(f"Loading {len(new)} file(s)…")
        for p in new:
            try: self.audio_files.append(AudioFile(p))
            except Exception as e: print(f"Error loading {p}: {e}")
        self._sync_genres()
        self._rebuild_table()

    def _rebuild_table(self):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.audio_files))
        for row,af in enumerate(self.audio_files): self._fill_row(row,af)
        self.table.setSortingEnabled(True)
        self.table.setColumnWidth(_NUM_COL,44); self.table.setColumnWidth(_COVER_COL,56)
        # Apply default sort from settings
        _ds = _SETTINGS().value("default_sort", "None", str)
        _ds_map = {"Title":_TITLE_COL, "Artist":3, "Genre":_GENRE_COL,
                   "BPM":_BPM_COL, "Key":_KEY_COL}
        if _ds in _ds_map:
            self.table.sortItems(_ds_map[_ds], Qt.SortOrder.AscendingOrder)
        self._update_status()
        self._apply_filters()

    _NUMERIC_FIELDS = {"bpm", "year", "track"}

    def _fill_row(self, row: int, af: AudioFile):
        for col,(field,_) in enumerate(COLUMNS):
            if field=="_num":
                item=SortItem(str(row+1))
                item.setData(_SORT_ROLE, row+1)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            elif field=="_cover":
                item=SortItem()
                if af.cover_data:
                    pix=QPixmap(); pix.loadFromData(af.cover_data)
                    if not pix.isNull():
                        item.setData(Qt.ItemDataRole.DecorationRole,
                            _rounded_pixmap(pix, TrackDelegate.ART, 6))
            elif field=="title":
                tv=str(getattr(af,"title",""))
                item=SortItem(tv)
                item.setData(TrackDelegate.SUBTITLE_ROLE, _subtitle(tv))
            elif field=="key":
                item=SortItem(normalize_key(str(getattr(af,"key",""))))
                cam=_RB_TO_CAMELOT.get(key_to_musical(str(getattr(af,"key",""))))
                if cam: item.setData(_SORT_ROLE, int(cam[:-1])*2 + (cam[-1]=="B"))
            elif field=="duration_str":
                item=SortItem(str(getattr(af,field,"")))
                item.setData(_SORT_ROLE, float(af.duration))
            elif field=="bitrate_str":
                item=SortItem(str(getattr(af,field,"")))
                item.setData(_SORT_ROLE, float(af.bitrate))
            elif field=="sample_rate_str":
                item=SortItem(str(getattr(af,field,"")))
                item.setData(_SORT_ROLE, float(af.sample_rate))
            elif field in self._NUMERIC_FIELDS:
                val=str(getattr(af,field,""))
                item=SortItem(val)
                try:    item.setData(_SORT_ROLE, float(val))
                except ValueError: item.setData(_SORT_ROLE, 0.0)
            else:
                item=SortItem(str(getattr(af,field,"")))
            item.setData(Qt.ItemDataRole.UserRole,af)
            self.table.setItem(row,col,item)

    def _find_row(self, af):
        for row in range(self.table.rowCount()):
            it=self.table.item(row,_NUM_COL)
            if it and it.data(Qt.ItemDataRole.UserRole) is af: return row
        return -1

    def _refresh_row(self, af):
        row=self._find_row(af)
        if row>=0:
            self.table.setSortingEnabled(False)
            self._fill_row(row,af); self.table.setSortingEnabled(True)

    def _refresh_sel(self):
        for af in self._sel_files(): self._refresh_row(af)
        self.player_bar.refresh_meta()

    def _sel_files(self) -> List[AudioFile]:
        seen,result=set(),[]
        for idx in self.table.selectedIndexes():
            it=self.table.item(idx.row(),_NUM_COL)
            if it:
                af=it.data(Qt.ItemDataRole.UserRole)
                if af and id(af) not in seen: seen.add(id(af)); result.append(af)
        return result

    def _on_sel(self):
        sel=self._sel_files()
        self.tag_panel.load_files(sel)
        if len(sel) == 1 and hasattr(self, "player_bar"):
            self.player_bar.load(sel[0], autoplay=self.player_bar.is_playing())

    # ── Save ──────────────────────────────────────────────────────────────────

    def _save_files(self, files):
        state = self.player_bar.release(files)
        errors=[af.filename for af in files if not af.save()]
        self.player_bar.resume(state)
        if errors:
            QMessageBox.warning(self,"Save Error",
                "Could not save:\n\n"+"\n".join(errors))
        else:
            self.statusBar().showMessage(f"✓  {len(files)} file(s) saved.")
            self._sync_genres()

    def _save_sel(self): self._save_files(self._sel_files() or self.audio_files)
    def _save_all(self): self._save_files(self.audio_files)

    # ── Remove ────────────────────────────────────────────────────────────────

    def _confirm_discard(self, files) -> bool:
        n = sum(1 for f in files if f._modified)
        if not n: return True
        r = QMessageBox.question(self, "Unsaved Changes",
            f"{n} file(s) have unsaved changes that will be lost.\nRemove anyway?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        return r == QMessageBox.StandardButton.Discard

    def _remove_sel(self):
        if not self._confirm_discard(self._sel_files()): return
        rows=sorted({idx.row() for idx in self.table.selectedIndexes()},reverse=True)
        for row in rows:
            it=self.table.item(row,_NUM_COL)
            if it:
                af=it.data(Qt.ItemDataRole.UserRole)
                if af in self.audio_files: self.audio_files.remove(af)
            self.table.removeRow(row)
        # Renumber the # column so it stays sequential
        for r in range(self.table.rowCount()):
            it = self.table.item(r, _NUM_COL)
            if it:
                it.setText(str(r+1)); it.setData(_SORT_ROLE, r+1)
        if self.player_bar.path and not any(f.path == self.player_bar.path for f in self.audio_files):
            self.player_bar.stop()
        self._sync_genres()
        self.tag_panel.load_files([]); self._update_status()
        self._apply_filters()

    def _clear_all(self):
        if not self._confirm_discard(self.audio_files): return
        self.player_bar.stop()
        self.audio_files.clear()
        self._sync_genres()
        self.table.setRowCount(0); self.tag_panel.load_files([]); self._update_status()

    # ── Context menu ──────────────────────────────────────────────────────────

    def _context_menu(self, pos):
        sel=self._sel_files(); menu=QMenu(self)
        menu.addAction("Play").triggered.connect(
            lambda: self.player_bar.load(sel[0], autoplay=True) if sel else None)
        menu.addAction("Quick Look  ⌘Y").triggered.connect(
            lambda: _quick_look(sel[0].path) if sel else None)
        menu.addAction(f"Save ({len(sel)} file(s))").triggered.connect(self._save_sel)
        menu.addSeparator()
        multi = len(sel) > 1
        act_tags = menu.addAction(f"Auto-Tag {len(sel)} Tracks…" if multi else "Search Tags…")
        act_tags.triggered.connect(lambda: self.tag_panel._search("tags_only"))
        act_cover = menu.addAction(f"Find {len(sel)} Covers…" if multi else "Find Cover…")
        act_cover.triggered.connect(lambda: self.tag_panel._search("cover_only"))
        if not _is_pro:
            act_tags.setText(act_tags.text().rstrip("…") + "  [Pro]")
            act_cover.setText(act_cover.text().rstrip("…") + "  [Pro]")
        menu.addSeparator()
        n_wav = sum(1 for f in sel if f.extension == ".wav")
        if n_wav:
            menu.addAction(f"Convert {n_wav} WAV to AIFF (for Rekordbox)…" if n_wav > 1
                           else "Convert WAV to AIFF (for Rekordbox)…").triggered.connect(
                self._convert_to_aiff)
        menu.addAction("Rename Files from Tags").triggered.connect(self.tag_panel._rename)
        menu.addSeparator()
        menu.addAction("Remove from List").triggered.connect(self._remove_sel)
        menu.exec(self.table.mapToGlobal(pos))

    # ── Drag & drop ───────────────────────────────────────────────────────────

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            if hasattr(self, '_drag_overlay'):
                self._drag_overlay.setGeometry(self.centralWidget().rect())
                self._drag_overlay.show()
                self._drag_overlay.raise_()

    def dragLeaveEvent(self, e):
        if hasattr(self, '_drag_overlay'):
            self._drag_overlay.hide()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        if hasattr(self, '_drag_overlay'):
            self._drag_overlay.hide()
        paths=_audio_paths(e.mimeData().urls())
        if paths: self._add_files(paths)

    def _quick_look_sel(self):
        sel = self._sel_files()
        if sel: _quick_look(sel[0].path)

    def _toggle_play(self):
        sel = self._sel_files()
        self.player_bar.toggle(sel[0] if sel else None)

    def _on_playing_changed(self, path):
        self._delegate.playing_path = path
        self.table.viewport().update()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._toggle_play()
            e.accept()
        elif e.matches(QKeySequence.StandardKey.Paste):
            self.tag_panel._paste()
        else:
            super().keyPressEvent(e)

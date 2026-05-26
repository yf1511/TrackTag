import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import List, Optional

import qtawesome as qta

_ICON_PATH    = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "app-icon.png")
_GENRES_FILE  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sidebar_genres.json")

# ── Pro license (verified against remote server, no local key list) ───────────
_is_pro = False

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QFormLayout,
    QSplitter, QTableWidget, QTableWidgetItem, QLabel, QLineEdit,
    QPushButton, QFileDialog, QScrollArea, QAbstractItemView,
    QHeaderView, QMessageBox, QMenu, QComboBox, QCompleter,
    QSizePolicy, QFrame, QSlider, QStyledItemDelegate,
    QStyleOptionViewItem, QApplication, QDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal, QBuffer, QIODevice, QSize, QRect, QTimer
from PyQt6.QtGui import (
    QPixmap, QIcon, QAction, QKeySequence, QDragEnterEvent,
    QDropEvent, QImage, QFont, QColor, QPen, QPainter,
)

from .audio_handler import AudioFile, SUPPORTED_EXTENSIONS
from .cover_search import MetaSearchDialog
from .updater import UpdateChecker
from . import license as _lic


def _ico(name: str, color: str = "#a1a5b3", size: int = 16) -> QIcon:
    """Return a qtawesome icon with given color."""
    return qta.icon(name, color=color, scale_factor=1.0)


# ── macOS titlebar CI integration ─────────────────────────────────────────────

def _apply_mac_titlebar(win_id: int):
    """Make the macOS titlebar transparent + dark to match CI (#0b0d13)."""
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

        # ── background colour = #0b0d13 ───────────────────────────────
        NSColor = lib.objc_getClass(b"NSColor")
        send.restype  = ctypes.c_void_p
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_double, ctypes.c_double,
                         ctypes.c_double, ctypes.c_double]
        color = send(ctypes.c_void_p(NSColor),
                     sel(b"colorWithSRGBRed:green:blue:alpha:"),
                     ctypes.c_double(0x0b / 255),
                     ctypes.c_double(0x0d / 255),
                     ctypes.c_double(0x13 / 255),
                     ctypes.c_double(1.0))

        send.restype  = None
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        send(ctypes.c_void_p(ns_window),
             sel(b"setBackgroundColor:"),
             ctypes.c_void_p(color))

    except Exception as exc:
        print(f"macOS titlebar: {exc}")

# ── Design tokens (matching spec) ─────────────────────────────────────────────

C_BG       = "#0b0d13"
C_SURFACE  = "#12141b"
C_SURFACE2 = "#171a22"
C_BORDER   = "#22262f"
C_TEXT     = "#f1f3f5"
C_TEXT2    = "#a1a5b3"
C_TEXT3    = "#52576b"
C_PRIMARY  = "#7c3aed"
C_ACCENT   = "#ff2d75"
C_ACCENT2  = "#ff8a00"
C_KEY_CLR  = "#ff2d75"
C_SEL_BG   = "#1d1230"
C_SEL_LINE = "#7c3aed"

# ── Key normalization (Rekordbox format) + per-key colors ─────────────────────

_CAMELOT_TO_RB = {
    "1A":"Am",  "1B":"A",
    "2A":"Em",  "2B":"E",
    "3A":"Bm",  "3B":"B",
    "4A":"F#m", "4B":"F#",
    "5A":"Dbm", "5B":"Db",
    "6A":"Abm", "6B":"Ab",
    "7A":"Ebm", "7B":"Eb",
    "8A":"Bbm", "8B":"Bb",
    "9A":"Fm",  "9B":"F",
    "10A":"Cm", "10B":"C",
    "11A":"Gm", "11B":"G",
    "12A":"Dm", "12B":"D",
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

# Camelot-wheel hue order — one color per note (minor = deep, major = lighter)
KEY_COLORS: dict[str, str] = {
    "Am":"#e05252", "A":"#ff8585",
    "Em":"#e07040", "E":"#ff9966",
    "Bm":"#e09930", "B":"#ffc055",
    "F#m":"#c8b800","F#":"#e8d840",
    "Dbm":"#8ec820","Db":"#aadd44",
    "Abm":"#3ab83a","Ab":"#5dd85d",
    "Ebm":"#30b87a","Eb":"#50d898",
    "Bbm":"#2898c0","Bb":"#44b8e0",
    "Fm":"#3060d8", "F":"#5585ff",
    "Cm":"#5550d0", "C":"#7878ff",
    "Gm":"#8840d0", "G":"#aa66ee",
    "Dm":"#c030a0", "D":"#e055cc",
    # enharmonic aliases
    "C#m":"#e07040","C#":"#ff9966",
    "D#m":"#c8b800","D#":"#e8d840",
    "G#m":"#3ab83a","G#":"#5dd85d",
    "A#m":"#2898c0","A#":"#44b8e0",
}

def normalize_key(s: str) -> str:
    """Convert any key notation (Beatport, Camelot, Open Key…) to Rekordbox short form."""
    if not s: return ""
    s = s.strip()
    # Camelot wheel: "11A", "6B"
    m = re.match(r'^(\d{1,2})([AB])$', s, re.IGNORECASE)
    if m:
        return _CAMELOT_TO_RB.get(f"{m.group(1)}{m.group(2).upper()}", s)
    # Normalise lookup key: strip spaces, lowercase, expand maj/min words
    lk = re.sub(r'\s+', '', s.lower())
    lk = lk.replace("major", "maj").replace("minor", "min")
    lk = lk.replace("maj.", "maj").replace("min.", "min")
    lk = lk.replace("mj", "maj").replace("mn", "min")
    return _KEY_NORM.get(lk, s)


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
_NUM_COL   = 0
_COVER_COL = 1
_TITLE_COL = 2
_GENRE_COL = 5
_BPM_COL   = 7
_KEY_COL   = 8

# ── Shared styles ─────────────────────────────────────────────────────────────

_INPUT = f"""
    QLineEdit {{
        background:{C_SURFACE}; color:{C_TEXT};
        border:1px solid {C_BORDER}; border-radius:8px;
        padding:0 10px; font-size:12px;
        selection-background-color:{C_PRIMARY};
    }}
    QLineEdit:focus {{ border-color:{C_PRIMARY}; background:{C_SURFACE2}; }}
    QLineEdit:disabled {{ color:{C_TEXT3}; border-color:{C_BG}; }}
"""
_COMBO = f"""
    QComboBox {{
        background:{C_SURFACE}; color:{C_TEXT};
        border:1px solid {C_BORDER}; border-radius:8px;
        padding:0 10px; font-size:12px;
    }}
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

def _fmt_time(secs: float) -> str:
    s = max(0, int(secs))
    return f"{s//60}:{s%60:02d}"


# ── Custom table delegate ─────────────────────────────────────────────────────

class ClickSlider(QSlider):
    """QSlider that jumps directly to clicked position + allows dragging.
    macOS default is page-step behaviour — we bypass it entirely."""

    def _pct_to_val(self, x: float) -> int:
        pct = max(0.0, min(1.0, x / max(self.width(), 1)))
        return int(pct * self.maximum())

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.setValue(self._pct_to_val(e.position().x()))
            self.sliderPressed.emit()
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self.setValue(self._pct_to_val(e.position().x()))
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.setValue(self._pct_to_val(e.position().x()))
            self.sliderReleased.emit()
            e.accept()
        else:
            super().mouseReleaseEvent(e)


class NoScrollComboBox(QComboBox):
    """ComboBox that ignores scroll wheel — prevents accidental genre changes while scrolling."""
    def wheelEvent(self, e):
        e.ignore()


class TrackDelegate(QStyledItemDelegate):
    SUBTITLE_ROLE = Qt.ItemDataRole.UserRole + 2

    _EVEN     = QColor(C_BG)
    _ODD      = QColor(C_SURFACE)
    _HOVER    = QColor("#161922")
    _SEL_BG   = QColor(C_SEL_BG)
    _SEL_LINE = QColor(C_SEL_LINE)
    _KEY      = QColor(C_KEY_CLR)
    _TEXT     = QColor(C_TEXT)
    _TEXT2    = QColor(C_TEXT2)
    _TEXT3    = QColor(C_TEXT3)

    def __init__(self, table, parent=None):
        super().__init__(parent)
        self._table = table

    def paint(self, painter, option, index):
        from PyQt6.QtWidgets import QStyle
        painter.save()
        row, col = index.row(), index.column()
        sel  = bool(option.state & QStyle.StateFlag.State_Selected)
        hov  = bool(option.state & QStyle.StateFlag.State_MouseOver)
        ncols = self._table.columnCount()

        # Background
        if sel:   painter.fillRect(option.rect, self._SEL_BG)
        elif hov: painter.fillRect(option.rect, self._HOVER)
        else:     painter.fillRect(option.rect, self._ODD if row % 2 else self._EVEN)

        # Selection border (top/bottom + edges)
        if sel:
            pen = QPen(self._SEL_LINE, 1.5)
            painter.setPen(pen)
            r = option.rect
            painter.drawLine(r.left(), r.top(), r.right(), r.top())
            painter.drawLine(r.left(), r.bottom()-1, r.right(), r.bottom()-1)
            if col == 0:
                painter.drawLine(r.left()+1, r.top()+1, r.left()+1, r.bottom()-2)
            if col == ncols - 1:
                painter.drawLine(r.right()-1, r.top()+1, r.right()-1, r.bottom()-2)

        r = option.rect.adjusted(8, 0, -8, 0)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")

        if col == _NUM_COL:
            painter.setPen(self._TEXT3)
            painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, text)

        elif col == _COVER_COL:
            icon = index.data(Qt.ItemDataRole.DecorationRole)
            if isinstance(icon, QIcon):
                pix = icon.pixmap(38, 38)
                if not pix.isNull():
                    px = option.rect.x() + (option.rect.width()-pix.width())//2
                    py = option.rect.y() + (option.rect.height()-pix.height())//2
                    painter.drawPixmap(px, py, pix)

        elif col == _TITLE_COL:
            sub = index.data(self.SUBTITLE_ROLE) or ""
            mid = option.rect.center().y()
            f1 = QFont(option.font); f1.setPixelSize(13)
            if sub:
                f2 = QFont(option.font); f2.setPixelSize(11)
                painter.setFont(f1); painter.setPen(self._TEXT)
                painter.drawText(QRect(r.left(), mid-13, r.width(), 16),
                    Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter, text)
                painter.setFont(f2); painter.setPen(self._TEXT2)
                painter.drawText(QRect(r.left(), mid+3, r.width(), 14),
                    Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter, sub)
            else:
                painter.setFont(f1); painter.setPen(self._TEXT)
                elided = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, r.width())
                painter.drawText(r, Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter, elided)

        elif col == _KEY_COL:
            key_color = QColor(KEY_COLORS.get(text, C_KEY_CLR))
            painter.setPen(key_color); painter.setFont(option.font)
            painter.drawText(r, Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter, text)

        else:
            painter.setPen(self._TEXT); painter.setFont(option.font)
            elided = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, r.width())
            painter.drawText(r, Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter, elided)

        painter.restore()

    def sizeHint(self, option, index):
        return QSize(super().sizeHint(option, index).width(), 50)


# ── Cover label ───────────────────────────────────────────────────────────────

class CoverLabel(QLabel):
    cover_changed = pyqtSignal(bytes, str)
    clicked = pyqtSignal()
    _SIZE = 268
    _IDLE   = (f"QLabel{{border:2px dashed {C_BORDER};border-radius:12px;"
               f"background:{C_SURFACE};color:{C_TEXT3};font-size:11px;}}")
    _HOVER  = (f"QLabel{{border:2px dashed {C_PRIMARY};border-radius:12px;"
               f"background:{C_SEL_BG};color:{C_PRIMARY};font-size:11px;}}")
    _FILLED = (f"QLabel{{border:none;border-radius:12px;background:{C_SURFACE};}}")

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
        self.setText("Drop cover here\nor click\n\n⌘V to paste")
        self.setStyleSheet(self._IDLE)

    def set_cover_data(self, data: bytes, mime: str = "image/jpeg"):
        pix = QPixmap(); pix.loadFromData(data)
        if pix.isNull(): return
        inner = self._SIZE - 4
        self.setPixmap(pix.scaled(inner, inner,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        self.setStyleSheet(self._FILLED); self._has = True
        self.cover_changed.emit(data, mime)

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
        self._field.setPlaceholderText("TRACKTAG-BETA-2024-PRO")
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
                    stop:0 #8b46ff, stop:1 #ff4d88);
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
                f"color:{C_ACCENT};font-size:11px;border:none;background:transparent;")
            self._field.setStyleSheet(
                self._field.styleSheet() + f"QLineEdit{{border-color:{C_ACCENT};}}")


# ── Sidebar drag-drop zone ────────────────────────────────────────────────────

class SidebarDropZone(QFrame):
    files_dropped = pyqtSignal(list)

    _IDLE  = (f"QFrame{{border:2px dashed {C_BORDER};border-radius:12px;"
              f"background:transparent;}}")
    _HOVER = (f"QFrame{{border:2px dashed {C_PRIMARY};border-radius:12px;"
              f"background:rgba(124,58,237,0.10);}}")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFixedHeight(106)
        self.setStyleSheet(self._IDLE)
        lay = QVBoxLayout(self); lay.setContentsMargins(10,10,10,10); lay.setSpacing(5)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._ico_lbl = QLabel(); self._ico_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ico_lbl.setStyleSheet("border:none;background:transparent;")
        self._ico_hover = None
        try:
            self._ico_lbl.setPixmap(
                _ico("fa5s.cloud-upload-alt", C_TEXT3).pixmap(24, 24))
            self._ico_hover = _ico("fa5s.cloud-upload-alt", C_PRIMARY).pixmap(24, 24)
        except Exception:
            pass

        lbl = QLabel("Drop audio files here")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            f"color:{C_TEXT2};font-size:12px;font-weight:600;"
            f"border:none;background:transparent;")
        sub = QLabel("MP3  ·  FLAC  ·  WAV  ·  AIFF  ·  M4A")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet(
            f"color:{C_TEXT3};font-size:10px;letter-spacing:0.3px;"
            f"border:none;background:transparent;")
        lay.addWidget(self._ico_lbl); lay.addWidget(lbl); lay.addWidget(sub)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.setStyleSheet(self._HOVER)
            if self._ico_hover:
                self._ico_lbl.setPixmap(self._ico_hover)

    def dragLeaveEvent(self, _):
        self.setStyleSheet(self._IDLE)
        try:
            self._ico_lbl.setPixmap(_ico("fa5s.cloud-upload-alt", C_TEXT3).pixmap(24, 24))
        except Exception:
            pass

    def dropEvent(self, e):
        self.setStyleSheet(self._IDLE)
        try:
            self._ico_lbl.setPixmap(_ico("fa5s.cloud-upload-alt", C_TEXT3).pixmap(24, 24))
        except Exception:
            pass
        paths = _audio_paths_from_urls(e.mimeData().urls())
        if paths: self.files_dropped.emit(paths)
        e.acceptProposedAction()


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
        painter.fillRect(self.rect(), QColor(124, 58, 237, 28))

        # Dashed border (inset 16px)
        inset = 16
        r = self.rect().adjusted(inset, inset, -inset, -inset)
        pen = QPen(QColor(124, 58, 237, 180), 2, Qt.PenStyle.DashLine)
        pen.setDashPattern([8, 6])
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(r, 16, 16)

        # Centered icon + text
        cx, cy = self.rect().center().x(), self.rect().center().y()
        painter.setPen(QColor(198, 168, 255, 220))
        font = QFont("-apple-system", 15, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(self.rect().adjusted(0, 20, 0, 0),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                         "Drop audio files here")
        font2 = QFont("-apple-system", 11)
        painter.setFont(font2)
        painter.setPen(QColor(167, 139, 250, 160))
        painter.drawText(self.rect().adjusted(0, 60, 0, 0),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                         "MP3  ·  FLAC  ·  WAV  ·  AIFF  ·  M4A")


# ── Nav item widget (clickable) ───────────────────────────────────────────────

class NavItem(QWidget):
    clicked = pyqtSignal()

    def __init__(self, label: str, fa_icon: str, count: str = "0",
                 active: bool = False, parent=None):
        super().__init__(parent)
        self.setFixedHeight(38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._active = active
        self._apply_style(active)

        row = QHBoxLayout(self); row.setContentsMargins(12,0,10,0); row.setSpacing(8)

        # Icon using qtawesome
        self._ico_lbl = QLabel()
        self._ico_lbl.setFixedSize(16, 16)
        self._ico_lbl.setStyleSheet("border:none;background:transparent;")
        try:
            color = C_PRIMARY if active else C_TEXT3
            self._ico_lbl.setPixmap(_ico(fa_icon, color).pixmap(14, 14))
        except Exception:
            pass

        self._txt = QLabel(label)
        self._txt.setStyleSheet(
            f"color:{'#f1f3f5' if active else '#a1a5b3'};"
            f"font-size:13px;font-weight:{'600' if active else '400'};"
            f"background:transparent;border:none;")

        self._badge = QLabel(count)
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge.setFixedHeight(20); self._badge.setMinimumWidth(26)
        self._badge.setStyleSheet(
            f"background:{'rgba(124,58,237,0.3)' if active else C_SURFACE2};"
            f"color:{'#c4b5fd' if active else C_TEXT3};"
            f"font-size:10px;font-weight:700;padding:0 6px;"
            f"border-radius:10px;border:none;")

        row.addWidget(self._ico_lbl)
        row.addWidget(self._txt, 1)
        row.addWidget(self._badge)

    def _apply_style(self, active: bool):
        self.setStyleSheet(f"""
            NavItem{{
                background:{'rgba(124,58,237,0.15)' if active else 'transparent'};
                border-radius:8px;
                border-left:3px solid {'#7c3aed' if active else 'transparent'};
                border-top:none;border-right:none;border-bottom:none;
            }}
            NavItem:hover{{background:rgba(124,58,237,0.10);}}
        """)

    def set_count(self, n: int):
        self._badge.setText(str(n))

    def set_active(self, active: bool):
        self._active = active
        self._apply_style(active)
        color = C_PRIMARY if active else C_TEXT3
        try:
            self._ico_lbl.setPixmap(
                _ico(self._fa_icon if hasattr(self,'_fa_icon') else "fa5s.music",
                     color).pixmap(14, 14))
        except Exception:
            pass
        self._txt.setStyleSheet(
            f"color:{'#f1f3f5' if active else '#a1a5b3'};"
            f"font-size:13px;font-weight:{'600' if active else '400'};"
            f"background:transparent;border:none;")
        self._badge.setStyleSheet(
            f"background:{'rgba(124,58,237,0.3)' if active else C_SURFACE2};"
            f"color:{'#c4b5fd' if active else C_TEXT3};"
            f"font-size:10px;font-weight:700;padding:0 6px;"
            f"border-radius:10px;border:none;")

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


# ── Navigation sidebar ────────────────────────────────────────────────────────

class NavSidebar(QWidget):
    add_files_clicked    = pyqtSignal()
    files_dropped        = pyqtSignal(list)
    nav_filter_changed   = pyqtSignal(str, str)  # (mode, value): ('all','') | ('genre','Tech House')
    pro_activated        = pyqtSignal()

    _PRO_BADGE_STYLE = (
        f"background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
        f"stop:0 #7c3aed,stop:1 #ff2d75);"
        f"color:#fff;font-size:8px;font-weight:800;"
        f"border-radius:4px;border:none;letter-spacing:0.8px;")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(228)
        self.setStyleSheet(f"background:{C_SURFACE};")
        self._nav_all    = None
        self._beta_lbl   = None
        self._genre_navs: dict[str, NavItem] = {}
        self._genre_container = None
        self._genre_vbox = None
        self._genres: list[str] = self._load_genres()
        self._setup_ui()

    # ── Genre persistence ──────────────────────────────────────────────────────
    def _load_genres(self) -> list:
        try:
            if os.path.exists(_GENRES_FILE):
                with open(_GENRES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list): return data
        except Exception: pass
        return []

    def _save_genres(self):
        try:
            with open(_GENRES_FILE, "w", encoding="utf-8") as f:
                json.dump(self._genres, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"save genres: {e}")

    def _setup_ui(self):
        root = QVBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        # ── Header ──────────────────────────────────────────────────────
        hdr = QFrame(); hdr.setFixedHeight(64)
        hdr.setStyleSheet(
            f"QFrame{{background:{C_BG};border-bottom:1px solid {C_BORDER};}}")
        hl = QHBoxLayout(hdr); hl.setContentsMargins(16,0,16,0); hl.setSpacing(8)
        if os.path.exists(_ICON_PATH):
            il = QLabel()
            il.setPixmap(QPixmap(_ICON_PATH).scaled(28,28,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            il.setFixedSize(28,28)
            il.setStyleSheet("border:none;background:transparent;")
            hl.addWidget(il)
        nl = QLabel("TrackTag")
        nl.setStyleSheet(
            f"color:{C_TEXT};font-size:16px;font-weight:700;"
            f"background:transparent;border:none;")
        hl.addWidget(nl)

        self._beta_lbl = QLabel("BETA")
        self._beta_lbl.setFixedHeight(18)
        self._beta_lbl.setContentsMargins(6,0,6,0)
        self._beta_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._beta_lbl.setStyleSheet(
            f"background:#0ea5e9;color:#fff;font-size:8px;font-weight:800;"
            f"border-radius:4px;border:none;letter-spacing:0.8px;")
        hl.addWidget(self._beta_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        hl.addStretch()
        root.addWidget(hdr)

        # ── Content ──────────────────────────────────────────────────────
        content = QWidget()
        content.setStyleSheet(f"background:{C_SURFACE};border:none;")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(12,14,12,14); cl.setSpacing(0)

        # ── Drop zone (TOP — above Add Files) ────────────────────────────
        drop = SidebarDropZone()
        drop.files_dropped.connect(self.files_dropped.emit)
        cl.addWidget(drop)
        cl.addSpacing(10)

        # Add Files button
        add = QPushButton()
        add.setFixedHeight(40)
        add.setText("  Add Files")
        try:
            add.setIcon(_ico("fa5s.plus", "#ffffff"))
            add.setIconSize(QSize(13, 13))
        except Exception:
            add.setText("+ Add Files")
        add.setStyleSheet(f"""
            QPushButton{{
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {C_PRIMARY}, stop:1 {C_ACCENT});
                color:#fff;border:none;border-radius:10px;
                font-weight:700;font-size:13px;text-align:center;
            }}
            QPushButton:hover{{
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #8b46ff, stop:1 #ff4d88);
            }}
            QPushButton:pressed{{
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #6d28d9, stop:1 #e01060);
            }}
        """)
        add.clicked.connect(self.add_files_clicked.emit)
        cl.addWidget(add)
        cl.addSpacing(20)

        # ── Library section label ─────────────────────────────────────────
        nav_lbl = QLabel("LIBRARY")
        nav_lbl.setStyleSheet(
            f"color:{C_TEXT3};font-size:9px;font-weight:700;"
            f"letter-spacing:1.4px;background:transparent;border:none;")
        cl.addWidget(nav_lbl)
        cl.addSpacing(6)

        # Nav items
        self._nav_all = NavItem("All Tracks", "fa5s.music", "0", active=True)
        self._nav_all.clicked.connect(lambda: self._select("all", ""))
        cl.addWidget(self._nav_all)
        cl.addSpacing(16)

        # ── Genres section ────────────────────────────────────────────────
        genres_hdr_row = QHBoxLayout()
        genres_hdr_row.setContentsMargins(0, 0, 0, 0); genres_hdr_row.setSpacing(0)
        g_lbl = QLabel("GENRES")
        g_lbl.setStyleSheet(
            f"color:{C_TEXT3};font-size:9px;font-weight:700;"
            f"letter-spacing:1.4px;background:transparent;border:none;")
        genres_hdr_row.addWidget(g_lbl)
        genres_hdr_row.addStretch()
        add_genre_btn = QPushButton("+")
        add_genre_btn.setFixedSize(22, 22)
        add_genre_btn.setStyleSheet(f"""
            QPushButton{{background:{C_SURFACE2};color:{C_TEXT2};border:1px solid {C_BORDER};
                border-radius:6px;font-size:14px;font-weight:600;padding:0;}}
            QPushButton:hover{{background:{C_BORDER};color:{C_TEXT};}}
            QPushButton:pressed{{background:{C_PRIMARY};color:#fff;border-color:{C_PRIMARY};}}
        """)
        add_genre_btn.setToolTip("Add genre")
        add_genre_btn.clicked.connect(self._add_genre_prompt)
        genres_hdr_row.addWidget(add_genre_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        cl.addLayout(genres_hdr_row)
        cl.addSpacing(6)

        # Container for dynamic genre NavItems
        self._genre_container = QWidget()
        self._genre_container.setStyleSheet("background:transparent;border:none;")
        self._genre_vbox = QVBoxLayout(self._genre_container)
        self._genre_vbox.setContentsMargins(0, 0, 0, 0); self._genre_vbox.setSpacing(2)
        self._rebuild_genre_navs()
        cl.addWidget(self._genre_container)
        cl.addStretch()

        # ── Activate Pro button (opens popup) ────────────────────────────
        act_btn = QPushButton()
        act_btn.setFixedHeight(34)
        act_btn.setText("  Activate License")
        try:
            act_btn.setIcon(_ico("fa5s.key", C_ACCENT2))
            act_btn.setIconSize(QSize(12, 12))
        except Exception:
            act_btn.setText("Activate License")
        act_btn.setStyleSheet(f"""
            QPushButton{{
                background:{C_SURFACE2};color:{C_TEXT2};
                border:1px solid {C_BORDER};border-radius:8px;
                font-size:11px;font-weight:600;text-align:left;padding-left:10px;
            }}
            QPushButton:hover{{
                background:{C_BORDER};color:{C_TEXT};
                border-color:{C_ACCENT2};
            }}
        """)
        act_btn.clicked.connect(self._open_activate_dialog)
        self._act_btn_ref = act_btn
        cl.addWidget(act_btn)
        cl.addSpacing(8)

        # ── Upgrade to Pro card ──────────────────────────────────────────
        card = QFrame(); card.setObjectName("upgradeCard")
        card.setStyleSheet(f"""
            QFrame#upgradeCard{{
                background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 rgba(124,58,237,0.18), stop:1 rgba(255,45,117,0.12));
                border-radius:12px;
                border:1px solid rgba(124,58,237,0.35);
            }}
            QFrame#upgradeCard QLabel{{border:none;background:transparent;}}
        """)
        ccl = QVBoxLayout(card)
        ccl.setContentsMargins(14,10,14,10); ccl.setSpacing(4)
        ct1 = QLabel("Upgrade to Pro")
        ct1.setStyleSheet(f"color:{C_TEXT};font-size:12px;font-weight:700;")
        ct2 = QLabel("Unlock all powerful features")
        ct2.setStyleSheet(f"color:{C_TEXT2};font-size:10px;")
        upbtn = QPushButton("Get Pro  →"); upbtn.setFixedHeight(26)
        upbtn.setStyleSheet(f"""
            QPushButton{{
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {C_PRIMARY},stop:1 {C_ACCENT});
                color:#fff;border:none;border-radius:7px;
                font-size:11px;font-weight:700;
            }}
            QPushButton:hover{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #8b46ff,stop:1 #ff4d88);}}
        """)
        ccl.addWidget(ct1); ccl.addWidget(ct2); ccl.addWidget(upbtn)
        cl.addWidget(card)
        root.addWidget(content, 1)

    def _select(self, mode: str, value: str = ""):
        self._nav_all.set_active(mode == "all")
        for g, nav in self._genre_navs.items():
            nav.set_active(mode == "genre" and g == value)
        self.nav_filter_changed.emit(mode, value)

    def _add_genre_prompt(self):
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self, "Add Genre", "Genre name:", QLineEdit.EchoMode.Normal)
        if ok and text.strip():
            genre = text.strip()
            if genre not in self._genres:
                self._genres.append(genre)
                self._save_genres()
                self._rebuild_genre_navs()

    def _remove_genre(self, genre: str):
        if genre in self._genres:
            self._genres.remove(genre)
            self._save_genres()
            # If currently selected, switch to all
            self.nav_filter_changed.emit("all", "")
            self._rebuild_genre_navs()
            self._nav_all.set_active(True)

    def _rebuild_genre_navs(self):
        # Clear old items
        for nav in self._genre_navs.values():
            nav.setParent(None)
        self._genre_navs.clear()

        if not self._genre_vbox:
            return

        for genre in self._genres:
            nav = NavItem(genre, "fa5s.tag", "", active=False)
            nav.clicked.connect(lambda checked=False, g=genre: self._select("genre", g))

            # Right-click context menu to remove
            nav.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            nav.customContextMenuRequested.connect(
                lambda pos, g=genre, n=nav: self._genre_context_menu(g, n, pos))

            self._genre_navs[genre] = nav
            self._genre_vbox.addWidget(nav)

    def _genre_context_menu(self, genre: str, nav: "NavItem", pos):
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu{{background:{C_SURFACE2};color:{C_TEXT};border:1px solid {C_BORDER};
                border-radius:8px;padding:4px 0;}}
            QMenu::item{{padding:6px 22px;}}
            QMenu::item:selected{{background:{C_PRIMARY};border-radius:4px;color:white;}}
        """)
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
            QPushButton{{background:rgba(255,45,117,0.15);color:{C_ACCENT};
                border:1px solid rgba(255,45,117,0.3);border-radius:9px;
                font-size:13px;font-weight:700;}}
            QPushButton:hover{{background:rgba(255,45,117,0.25);}}
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
        if hasattr(self, '_act_btn_ref'):
            self._act_btn_ref.setText("  Pro Active  —  click to deactivate")
            self._act_btn_ref.setEnabled(True)
            try:
                self._act_btn_ref.setIcon(_ico("fa5s.check-circle", "#22c55e"))
                self._act_btn_ref.setIconSize(QSize(12, 12))
            except Exception:
                pass
            self._act_btn_ref.setStyleSheet(
                f"QPushButton{{background:rgba(34,197,94,0.12);"
                f"color:#22c55e;border:1px solid rgba(34,197,94,0.28);"
                f"border-radius:8px;font-size:10px;font-weight:600;"
                f"text-align:left;padding-left:10px;}}"
                f"QPushButton:hover{{background:rgba(34,197,94,0.22);}}")
        self.pro_activated.emit()

    def _on_pro_deactivated(self):
        global _is_pro
        _is_pro = False
        if self._beta_lbl:
            self._beta_lbl.setText("BETA")
            self._beta_lbl.setStyleSheet(
                f"background:#0ea5e9;color:#fff;font-size:8px;font-weight:800;"
                f"border-radius:4px;border:none;letter-spacing:0.8px;")
        if hasattr(self, '_act_btn_ref'):
            self._act_btn_ref.setText("  Activate License")
            self._act_btn_ref.setEnabled(True)
            try:
                self._act_btn_ref.setIcon(_ico("fa5s.key", C_ACCENT2))
                self._act_btn_ref.setIconSize(QSize(12, 12))
            except Exception:
                pass
            self._act_btn_ref.setStyleSheet(f"""
                QPushButton{{
                    background:{C_SURFACE2};color:{C_TEXT2};
                    border:1px solid {C_BORDER};border-radius:8px;
                    font-size:11px;font-weight:600;text-align:left;padding-left:10px;
                }}
                QPushButton:hover{{
                    background:{C_BORDER};color:{C_TEXT};
                    border-color:{C_ACCENT2};
                }}
            """)

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
        scroll.setStyleSheet(f"""
            QScrollArea{{background:{C_SURFACE2};border:none;border-left:1px solid {C_BORDER};}}
            QScrollBar:vertical{{background:{C_SURFACE2};width:4px;border:none;}}
            QScrollBar::handle:vertical{{background:{C_BORDER};border-radius:2px;}}
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}
        """)
        content = QWidget(); content.setStyleSheet(f"background:{C_SURFACE2};")
        c = QVBoxLayout(content); c.setContentsMargins(0,0,0,24); c.setSpacing(0)

        # ── Cover section ──────────────────────────────────────────────────
        cov_sec = QWidget(); cov_sec.setStyleSheet(f"background:{C_SURFACE2};")
        csv = QVBoxLayout(cov_sec); csv.setContentsMargins(16,16,16,14); csv.setSpacing(10)

        cover_wrap = QFrame()
        cover_wrap.setFixedSize(CoverLabel._SIZE, CoverLabel._SIZE)
        cover_wrap.setStyleSheet("background:transparent;")
        self.cover = CoverLabel(cover_wrap)
        self.cover.setGeometry(0,0,CoverLabel._SIZE,CoverLabel._SIZE)
        self.cover.clicked.connect(self._pick_cover)
        self.cover.cover_changed.connect(self._on_cover)
        edit_btn = QPushButton("✏", cover_wrap)
        edit_btn.setFixedSize(30,30); edit_btn.move(CoverLabel._SIZE-36, 6); edit_btn.raise_()
        edit_btn.setStyleSheet(
            "QPushButton{background:rgba(0,0,0,0.70);color:#fff;border:none;"
            "border-radius:8px;font-size:13px;}"
            "QPushButton:hover{background:rgba(0,0,0,0.90);}")
        edit_btn.clicked.connect(self._pick_cover)
        csv.addWidget(cover_wrap, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Action buttons — two equal columns then full-width rows
        btn_ss_active = (f"QPushButton{{background:{C_SURFACE};color:{C_TEXT};"
                         f"border:1px solid {C_BORDER};border-radius:9px;"
                         f"font-size:11px;font-weight:600;}}"
                         f"QPushButton:hover{{background:{C_BORDER};border-color:{C_PRIMARY};}}"
                         f"QPushButton:disabled{{color:{C_TEXT3};border-color:{C_SURFACE};}}")

        row1 = QHBoxLayout(); row1.setSpacing(7)
        bt = QPushButton("Search Tags"); bt.setFixedHeight(34)
        bt.setStyleSheet(btn_ss_active); bt.clicked.connect(lambda: self._search("tags_only"))
        bc = QPushButton("Cover Only");  bc.setFixedHeight(34)
        bc.setStyleSheet(btn_ss_active); bc.clicked.connect(lambda: self._search("cover_only"))
        row1.addWidget(bt,1); row1.addWidget(bc,1)
        csv.addLayout(row1)

        bp = QPushButton("Paste  (⌘V)"); bp.setFixedHeight(34)
        bp.setStyleSheet(btn_ss_active); bp.clicked.connect(self._paste)
        csv.addWidget(bp)

        self.del_btn = QPushButton("Remove Cover"); self.del_btn.setFixedHeight(34)
        self.del_btn.setStyleSheet(f"""
            QPushButton{{background:rgba(255,45,117,0.10);color:{C_ACCENT};
                border:1px solid rgba(255,45,117,0.25);border-radius:9px;
                font-size:11px;font-weight:600;}}
            QPushButton:hover{{background:rgba(255,45,117,0.20);border-color:{C_ACCENT};}}
            QPushButton:disabled{{color:{C_TEXT3};border-color:{C_SURFACE};background:transparent;}}
        """)
        self.del_btn.clicked.connect(self._del_cover)
        csv.addWidget(self.del_btn)
        c.addWidget(cov_sec)

        # ── TRACK INFO section ─────────────────────────────────────────────
        c.addWidget(self._sec_hdr("TRACK INFO", C_PRIMARY))

        fi = QWidget(); fi.setStyleSheet(f"background:{C_SURFACE2};")
        fv = QVBoxLayout(fi); fv.setContentsMargins(16,12,16,14); fv.setSpacing(7)

        for field, lbl in [("title","Titel"), ("artist","Interpret"),
                            ("album","Album"), ("genre","Genre"), ("label","Label")]:
            w = self._mk_combo(field) if field == "genre" else self._mk_line(field)
            fv.addLayout(self._frow(lbl, w))

        # Jahr + BPM side by side with equal weight
        jb = QHBoxLayout(); jb.setSpacing(10)
        yw = self._mk_line("year")
        bw = self._mk_line("bpm")
        jb.addLayout(self._frow("Jahr", yw, lbl_w=46))
        jb.addLayout(self._frow("BPM",  bw, lbl_w=40))
        fv.addLayout(jb)

        fv.addLayout(self._frow("Key",      self._mk_line("key")))
        fv.addLayout(self._frow("Kommentar",self._mk_line("comment")))
        c.addWidget(fi)

        # ── ERWEITERT section ──────────────────────────────────────────────
        c.addWidget(self._sec_hdr("ERWEITERT", C_ACCENT2))

        ei = QWidget(); ei.setStyleSheet(f"background:{C_SURFACE2};")
        ev = QVBoxLayout(ei); ev.setContentsMargins(16,12,16,14); ev.setSpacing(7)
        ev.addLayout(self._frow("Album-Interpret", self._mk_line("album_artist")))
        ev.addLayout(self._frow("Komponist",        self._mk_line("composer")))

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
    def _sec_hdr(self, text: str, color: str) -> QFrame:
        f = QFrame(); f.setFixedHeight(38)
        f.setStyleSheet(f"QFrame{{background:{C_SURFACE};border-top:1px solid {C_BORDER};"
                        f"border-bottom:1px solid {C_BORDER};}}")
        hl = QHBoxLayout(f); hl.setContentsMargins(16,0,16,0)
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color:{color};font-size:9px;font-weight:800;"
                          f"letter-spacing:1.6px;background:transparent;border:none;")
        hl.addWidget(lbl); hl.addStretch()
        return f

    # ── Form row helper — label + widget, all fields same right edge ───────────
    def _frow(self, label: str, widget, lbl_w: int = 96) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(10); row.setContentsMargins(0,0,0,0)
        lbl = QLabel(label)
        lbl.setFixedWidth(lbl_w)
        lbl.setStyleSheet(f"color:{C_TEXT2};font-size:11px;font-weight:500;"
                          f"background:transparent;border:none;")
        row.addWidget(lbl)
        row.addWidget(widget)
        return row

    def _build_bottom_bar(self) -> QWidget:
        bar = QWidget()
        bar.setStyleSheet(f"background:{C_SURFACE};border-top:1px solid {C_BORDER};")
        bl = QVBoxLayout(bar); bl.setContentsMargins(16,10,16,14); bl.setSpacing(8)

        # Rename button — subtle, arrow-style
        self.rename_btn = QPushButton("— Rename  →  Artist – Title  ›")
        self.rename_btn.setFixedHeight(34)
        self.rename_btn.setStyleSheet(f"""
            QPushButton{{background:{C_SURFACE2};color:{C_TEXT2};
                         border:1px solid {C_BORDER};border-radius:9px;
                         font-size:11px;font-weight:500;text-align:left;padding-left:12px;}}
            QPushButton:hover{{background:{C_BORDER};color:{C_TEXT};border-color:{C_PRIMARY};}}
            QPushButton:disabled{{color:{C_TEXT3};border-color:{C_BG};background:{C_SURFACE};}}
        """)
        self.rename_btn.clicked.connect(self._rename)
        bl.addWidget(self.rename_btn)

        # Save + More row
        save_row = QHBoxLayout(); save_row.setSpacing(8)
        self.save_btn = QPushButton("  Save Changes")
        self.save_btn.setFixedHeight(42); self.save_btn.setShortcut("Ctrl+S")
        self.save_btn.setStyleSheet(f"""
            QPushButton{{
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {C_ACCENT}, stop:1 {C_ACCENT2});
                color:#fff;border:none;border-radius:10px;
                font-weight:700;font-size:13px;
            }}
            QPushButton:hover{{
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #e01060, stop:1 #e07800);
            }}
            QPushButton:disabled{{background:{C_SURFACE2};color:{C_TEXT3};}}
        """)
        self.save_btn.clicked.connect(self.save_requested.emit)
        save_row.addWidget(self.save_btn,1)

        more = QPushButton("···"); more.setFixedSize(42,42)
        more.setStyleSheet(f"QPushButton{{background:{C_SURFACE2};color:{C_TEXT2};"
                            f"border:none;border-radius:10px;font-size:18px;letter-spacing:1px;}}"
                            f"QPushButton:hover{{background:{C_BORDER};color:{C_TEXT};}}")
        more.clicked.connect(self._more_menu)
        save_row.addWidget(more)
        bl.addLayout(save_row)
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

    def _pair(self, f1, l1, f2, l2) -> QWidget:
        w = QWidget(); w.setStyleSheet("background:transparent;")
        row = QHBoxLayout(w); row.setContentsMargins(0,0,0,0); row.setSpacing(8)
        for field, label in [(f1,l1),(f2,l2)]:
            if not field:
                row.addStretch(); continue
            col = QVBoxLayout(); col.setSpacing(3)
            col.addWidget(self._lbl(label))
            col.addWidget(self._mk_line(field))
            row.addLayout(col)
        return w

    @staticmethod
    def _lbl(text: str) -> QLabel:
        l = QLabel(text)
        l.setStyleSheet(f"color:{C_TEXT2};font-size:11px;font-weight:500;background:transparent;")
        return l

    @staticmethod
    def _div() -> QFrame:
        f = QFrame(); f.setFixedHeight(1)
        f.setStyleSheet(f"background:{C_BORDER};margin:4px 0;"); return f

    @staticmethod
    def _pill(fa_icon: str, text: str, accent: str) -> QPushButton:
        btn = QPushButton(f"  {text}"); btn.setFixedHeight(32)
        try:
            btn.setIcon(_ico(fa_icon, C_TEXT2))
            btn.setIconSize(QSize(12, 12))
        except Exception:
            btn.setText(text)
        btn.setStyleSheet(
            f"QPushButton{{background:{C_SURFACE2};color:{C_TEXT};"
            f"border:1px solid {C_BORDER};border-radius:8px;"
            f"font-size:11px;font-weight:500;}}"
            f"QPushButton:hover{{background:{C_BORDER};border-color:{accent};color:#fff;}}"
            f"QPushButton:disabled{{color:{C_TEXT3};border-color:{C_BG};}}")
        return btn

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
            self.cover.clear_cover(); self._set_enabled(False)
            self._loading = False; return
        self._set_enabled(True)
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
                    w.setPlaceholderText("" if common is not None else "← verschiedene Werte")
            self._show_cover(files[0].cover_data if files else None)
        self._loading = False

    def _show_cover(self, data: Optional[bytes]):
        if data:
            pix = QPixmap(); pix.loadFromData(data)
            if not pix.isNull():
                inner = CoverLabel._SIZE - 4
                self.cover.setPixmap(pix.scaled(inner,inner,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
                self.cover.setStyleSheet(CoverLabel._FILLED); self.cover._has = True; return
        self.cover.clear_cover()

    def _edited(self, field, value):
        if self._loading: return
        for f in self._files: f.set_field(field, value)
        self.tags_changed.emit()

    def _on_cover(self, data, mime):
        if self._loading: return
        for f in self._files: f.set_cover(data, mime)
        self.tags_changed.emit()

    # ── Cover actions ─────────────────────────────────────────────────────────

    def _pick_cover(self):
        if not self._files: return
        path,_ = QFileDialog.getOpenFileName(self,"Select Cover","",
            "Bilder (*.jpg *.jpeg *.png *.bmp *.webp)")
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
        self.cover.clear_cover(); self.tags_changed.emit()

    def _search(self, preset="all"):
        if not self._files: return
        f=self._files[0]
        dlg=MetaSearchDialog(artist=f.artist,title=f.title,album=f.album,
                              preset=preset,parent=self)
        dlg.result_selected.connect(self._apply); dlg.exec()

    def _apply(self, payload: dict):
        if not self._files: return
        if "cover_data" in payload:
            self.cover.set_cover_data(payload["cover_data"],
                                       payload.get("cover_mime","image/jpeg"))
        for key in ("artist","title","genre","label","year","bpm","key"):
            if key in payload:
                val = payload[key]
                if key == "key": val = normalize_key(val)
                w = self.fields.get(key)
                if w: (w.setCurrentText if isinstance(w,QComboBox) else w.setText)(val)
                for f in self._files: f.set_field(key, val)
        self.tags_changed.emit()

    def _more_menu(self):
        menu=QMenu(self)
        menu.setStyleSheet(f"QMenu{{background:{C_SURFACE2};color:{C_TEXT};"
                            f"border:1px solid {C_BORDER};border-radius:10px;padding:4px;}}"
                            f"QMenu::item{{padding:7px 16px;border-radius:6px;font-size:12px;}}"
                            f"QMenu::item:selected{{background:{C_PRIMARY};}}"
                            f"QMenu::separator{{background:{C_BORDER};height:1px;margin:3px 8px;}}")
        menu.addAction("📂  Cover aus Datei…").triggered.connect(self._pick_cover)
        menu.addAction("📋  Paste Cover (⌘V)").triggered.connect(self._paste)
        menu.addSeparator()
        menu.addAction("🗑  Remove Cover").triggered.connect(self._del_cover)
        menu.exec(self.save_btn.mapToGlobal(self.save_btn.rect().topRight()))

    def _rename(self):
        if not self._files: return
        _INV=set('/\\:*?"<>|')
        def san(s): return "".join("_" if c in _INV else c for c in s).strip(" .")
        renamed,skipped,errors=0,0,[]
        for f in self._files:
            a,t=f.artist.strip(),f.title.strip()
            if not a or not t:
                skipped+=1; errors.append(f"⚠️  '{f.filename}' — fehlt Interpret/Titel"); continue
            nn=f"{san(a)} - {san(t)}{f.extension}"
            np=os.path.join(os.path.dirname(f.path),nn)
            if f.path==np: skipped+=1; continue
            if os.path.exists(np):
                errors.append(f"⚠️  '{nn}' existiert bereits"); skipped+=1; continue
            try:
                os.rename(f.path,np); f.path=np; f.filename=nn; renamed+=1
            except OSError as e: errors.append(f"❌  '{f.filename}': {e}")
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


# ── Audio player (bottom bar) ─────────────────────────────────────────────────

class AudioPlayer(QWidget):
    """Bottom preview player — uses macOS afplay."""

    request_prev = pyqtSignal()
    request_next = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._af: Optional[AudioFile] = None
        self._proc = None
        self._start = 0.0
        self._offset = 0.0   # paused position
        self._playing = False

        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._tick)
        self._setup_ui()

    def _setup_ui(self):
        self.setFixedHeight(86)
        self.setStyleSheet(f"background:{C_BG};border-top:1px solid {C_BORDER};")

        lay = QHBoxLayout(self); lay.setContentsMargins(20, 0, 20, 0); lay.setSpacing(0)

        # Left: cover + info (fixed width so center stays truly centered)
        left = QWidget(); left.setFixedWidth(280)
        left.setStyleSheet("background:transparent;border:none;")
        left_row = QHBoxLayout(left); left_row.setContentsMargins(0, 0, 0, 0); left_row.setSpacing(10)

        self.cover_lbl = QLabel()
        self.cover_lbl.setFixedSize(48, 48)
        self.cover_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_lbl.setStyleSheet(
            f"background:{C_SURFACE2};border-radius:8px;border:none;")
        left_row.addWidget(self.cover_lbl)

        info_col = QVBoxLayout(); info_col.setSpacing(2)
        info_col.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.title_lbl = QLabel("—")
        self.title_lbl.setStyleSheet(
            f"color:{C_TEXT};font-size:13px;font-weight:600;"
            f"background:transparent;border:none;")
        self.title_lbl.setWordWrap(False)
        self.artist_lbl = QLabel("—")
        self.artist_lbl.setStyleSheet(
            f"color:{C_TEXT2};font-size:11px;background:transparent;border:none;")
        info_col.addWidget(self.title_lbl); info_col.addWidget(self.artist_lbl)
        left_row.addLayout(info_col, 1)
        lay.addWidget(left)

        # Center: controls + progress bar
        center = QVBoxLayout()
        center.setSpacing(0)
        center.setContentsMargins(0, 0, 0, 0)
        center.addStretch(3)   # bigger top push → controls sit slightly above center

        ctrl = QHBoxLayout(); ctrl.setSpacing(0); ctrl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        _skip_ss = (f"QPushButton{{background:transparent;color:{C_TEXT3};border:none;"
                    f"font-size:12px;font-weight:700;padding:0;}}"
                    f"QPushButton:hover{{color:{C_TEXT};border:none;}}"
                    f"QPushButton:pressed{{color:{C_TEXT2};border:none;}}"
                    f"QPushButton:disabled{{color:{C_BORDER};border:none;}}")

        b_prev = QPushButton("◀◀"); b_prev.setFixedSize(32, 32)
        b_prev.setStyleSheet(_skip_ss)
        b_prev.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b_prev.clicked.connect(self.request_prev.emit)
        ctrl.addWidget(b_prev)
        ctrl.addSpacing(6)

        # Play/pause button — white circle, dark icon
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedSize(42, 42)
        self.play_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.play_btn.setStyleSheet(f"""
            QPushButton{{
                background:{C_TEXT};color:{C_BG};
                border:none;border-radius:21px;
                font-size:15px;font-weight:800;
                padding-left:2px;
            }}
            QPushButton:hover{{background:#e8e8e8;border:none;}}
            QPushButton:pressed{{background:#cccccc;border:none;}}
            QPushButton:disabled{{background:{C_BORDER};color:{C_TEXT3};border:none;}}
        """)
        self.play_btn.clicked.connect(self.toggle)
        ctrl.addWidget(self.play_btn)
        ctrl.addSpacing(6)

        b_next = QPushButton("▶▶"); b_next.setFixedSize(32, 32)
        b_next.setStyleSheet(_skip_ss)
        b_next.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b_next.clicked.connect(self.request_next.emit)
        ctrl.addWidget(b_next)

        center.addLayout(ctrl)
        center.addStretch(1)   # small gap between ctrl and progress
        center.addSpacing(2)

        # Progress row
        prog_row = QHBoxLayout(); prog_row.setSpacing(8); prog_row.setContentsMargins(0, 0, 0, 0)
        self.t_cur = QLabel("0:00")
        self.t_cur.setStyleSheet(
            f"color:{C_TEXT3};font-size:10px;font-weight:500;"
            f"background:transparent;border:none;min-width:32px;")
        self.t_tot = QLabel("0:00")
        self.t_tot.setStyleSheet(
            f"color:{C_TEXT3};font-size:10px;font-weight:500;"
            f"background:transparent;border:none;min-width:32px;")
        self.t_tot.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.progress = ClickSlider(Qt.Orientation.Horizontal)
        self.progress.setRange(0, 1000); self.progress.setValue(0)
        self.progress.setFixedHeight(16)
        self.progress.setStyleSheet(f"""
            QSlider{{background:transparent;border:none;}}
            QSlider::groove:horizontal{{
                background:{C_BORDER};height:3px;border-radius:2px;margin:0;border:none;
            }}
            QSlider::sub-page:horizontal{{
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {C_ACCENT}, stop:1 {C_ACCENT2});
                height:3px;border-radius:2px;border:none;
            }}
            QSlider::handle:horizontal{{
                background:{C_TEXT};width:10px;height:10px;
                border-radius:5px;margin:-4px 0;border:none;
            }}
            QSlider::handle:horizontal:hover{{
                background:#ffffff;border:none;
            }}
        """)
        self.progress.sliderPressed.connect(self._on_seek_start)
        self.progress.sliderReleased.connect(self._on_seek_end)

        prog_row.addWidget(self.t_cur)
        prog_row.addWidget(self.progress, 1)
        prog_row.addWidget(self.t_tot)
        center.addLayout(prog_row)
        center.addStretch(2)   # bottom padding

        lay.addLayout(center, 1)

        # Right: volume (fixed width mirrors left)
        right = QWidget(); right.setFixedWidth(160)
        right.setStyleSheet("background:transparent;border:none;")
        vol_row = QHBoxLayout(right); vol_row.setContentsMargins(0, 0, 0, 0); vol_row.setSpacing(8)

        vol_ic = QLabel()
        vol_ic.setFixedSize(18, 18)
        vol_ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vol_ic.setStyleSheet("background:transparent;border:none;")
        try:
            vol_ic.setPixmap(_ico("fa5s.volume-up", C_TEXT3).pixmap(14, 14))
        except Exception:
            vol_ic.setText("♪")
            vol_ic.setStyleSheet(f"color:{C_TEXT3};font-size:12px;background:transparent;border:none;")

        self.vol = ClickSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 100); self.vol.setValue(80)
        self.vol.setFixedHeight(16)
        self.vol.setStyleSheet(f"""
            QSlider{{background:transparent;border:none;}}
            QSlider::groove:horizontal{{
                background:{C_BORDER};height:3px;border-radius:2px;margin:0;border:none;
            }}
            QSlider::sub-page:horizontal{{
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {C_PRIMARY}, stop:1 #a855f7);
                height:3px;border-radius:2px;border:none;
            }}
            QSlider::handle:horizontal{{
                background:{C_TEXT};width:10px;height:10px;
                border-radius:5px;margin:-4px 0;border:none;
            }}
            QSlider::handle:horizontal:hover{{
                background:#ffffff;border:none;
            }}
        """)
        vol_row.addWidget(vol_ic); vol_row.addWidget(self.vol, 1)
        lay.addWidget(right)
        self._seeking = False

    def load(self, af: AudioFile):
        was_playing = self._playing
        self.stop()
        self._af = af
        self._offset = 0.0
        self.title_lbl.setText(af.title or af.filename)
        self.artist_lbl.setText(af.artist or "")
        dur = int(af.duration)
        self.t_tot.setText(_fmt_time(dur))
        self.t_cur.setText("0:00"); self.progress.setValue(0)
        if af.cover_data:
            pix = QPixmap(); pix.loadFromData(af.cover_data)
            if not pix.isNull():
                self.cover_lbl.setPixmap(pix.scaled(46,46,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation))
        else:
            self.cover_lbl.clear()
        if was_playing:
            self._do_play()

    def toggle(self):
        if self._playing: self._do_pause()
        elif self._af:    self._do_play()

    def stop(self):
        if self._proc:
            try: self._proc.terminate()
            except: pass
            self._proc = None
        self._playing = False; self._timer.stop()
        self._offset = 0.0; self.progress.setValue(0); self.t_cur.setText("0:00")
        self.play_btn.setText("▶")

    def _do_play(self):
        if not self._af: return
        if self._proc:
            try: self._proc.terminate()
            except: pass
        vol = self.vol.value() / 100.0
        cmd = ['afplay', '-v', str(vol)]
        if self._offset > 0:
            cmd += ['-t', str(max(0, self._af.duration - self._offset)),
                    '-s', str(self._offset)]
        cmd.append(self._af.path)
        self._proc = subprocess.Popen(cmd,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._start = time.time() - self._offset
        self._playing = True; self._timer.start()
        self.play_btn.setText("⏸")

    def _do_pause(self):
        self._offset = time.time() - self._start
        if self._proc:
            try: self._proc.terminate()
            except: pass
            self._proc = None
        self._playing = False; self._timer.stop()
        self.play_btn.setText("▶")

    def _on_seek_start(self):
        self._seeking = True
        if self._playing: self._do_pause()

    def _on_seek_end(self):
        self._seeking = False
        if self._af:
            pct = self.progress.value() / 1000.0
            self._offset = pct * self._af.duration
            self.t_cur.setText(_fmt_time(self._offset))
            self._do_play()

    def _tick(self):
        if not self._playing or self._seeking: return
        if self._proc and self._proc.poll() is not None:
            self.stop(); return
        elapsed = time.time() - self._start
        dur = self._af.duration if self._af else 1
        pct = min(elapsed / dur, 1.0)
        self.progress.setValue(int(pct * 1000))
        self.t_cur.setText(_fmt_time(elapsed))

    def closeEvent(self, e):
        self.stop(); super().closeEvent(e)


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
            QTabBar::tab:selected {{background:{C_PRIMARY};color:#fff;border-color:{C_PRIMARY};}}
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
        tabs.addTab(self._audio_tab(), "Audio")
        tabs.addTab(self._metadata_tab(), "Metadata")
        tabs.addTab(self._about_tab(), "About")
        root.addWidget(tabs, 1)

        # Close button
        close = QPushButton("Close")
        close.setFixedHeight(40)
        close.setStyleSheet(f"""
            QPushButton{{
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {C_PRIMARY},stop:1 {C_ACCENT});
                color:#fff;border:none;border-radius:10px;
                font-size:13px;font-weight:700;
            }}
            QPushButton:hover{{
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #8b46ff,stop:1 #ff4d88);
            }}
        """)
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

        v.addWidget(self._section("FILE RENAMING"))
        v.addSpacing(2)
        pattern = QLineEdit("%artist% - %title%")
        pattern.setFixedHeight(34); pattern.setStyleSheet(_INPUT)
        v.addLayout(self._field_row("Rename Pattern", pattern))
        hint = QLabel("Available tags: %artist%  %title%  %album%  %year%  %bpm%  %key%")
        hint.setStyleSheet(f"color:{C_TEXT3};font-size:10px;padding-left:172px;")
        v.addWidget(hint)
        v.addSpacing(6)

        v.addWidget(self._section("SORT & DISPLAY"))
        v.addSpacing(2)
        sort_combo = QComboBox()
        sort_combo.addItems(["Title", "Artist", "BPM", "Key", "Genre", "Date Added"])
        sort_combo.setFixedHeight(34); sort_combo.setStyleSheet(_COMBO)
        v.addLayout(self._field_row("Default Sort", sort_combo))
        v.addSpacing(6)

        v.addWidget(self._section("STARTUP"))
        v.addSpacing(2)
        restore_combo = QComboBox()
        restore_combo.addItems(["Empty library", "Restore last session", "Open dialog"])
        restore_combo.setFixedHeight(34); restore_combo.setStyleSheet(_COMBO)
        v.addLayout(self._field_row("On Launch", restore_combo))
        v.addStretch()
        return w

    def _audio_tab(self) -> QWidget:
        w = QWidget(); w.setStyleSheet(f"background:{C_SURFACE2};")
        v = QVBoxLayout(w); v.setContentsMargins(20, 16, 20, 16); v.setSpacing(10)

        v.addWidget(self._section("QUICK LOOK"))
        v.addSpacing(2)

        info = QLabel(
            'Press Space or choose "Quick Look" from the context menu\n'
            'to preview a track using macOS Quick Look.')
        info.setStyleSheet(f"color:{C_TEXT2};font-size:12px;background:transparent;border:none;")
        info.setWordWrap(True)
        v.addWidget(info)
        v.addSpacing(6)

        v.addWidget(self._section("WAVEFORM"))
        v.addSpacing(2)
        waveform_combo = QComboBox()
        waveform_combo.addItems(["Disabled", "Full waveform", "Mini waveform"])
        waveform_combo.setFixedHeight(34); waveform_combo.setStyleSheet(_COMBO)
        v.addLayout(self._field_row("Waveform Display", waveform_combo))
        v.addStretch()
        return w

    def _metadata_tab(self) -> QWidget:
        w = QWidget(); w.setStyleSheet(f"background:{C_SURFACE2};")
        v = QVBoxLayout(w); v.setContentsMargins(20, 16, 20, 16); v.setSpacing(10)

        v.addWidget(self._section("ID3 TAGS"))
        v.addSpacing(2)
        id3_combo = QComboBox()
        id3_combo.addItems(["ID3v2.3 (recommended)", "ID3v2.4", "ID3v1 + ID3v2.3"])
        id3_combo.setFixedHeight(34); id3_combo.setStyleSheet(_COMBO)
        v.addLayout(self._field_row("ID3 Version", id3_combo))
        v.addSpacing(6)

        v.addWidget(self._section("COVER ART"))
        v.addSpacing(2)
        cover_combo = QComboBox()
        cover_combo.addItems(["1000×1000 px", "1500×1500 px", "3000×3000 px", "Original size"])
        cover_combo.setFixedHeight(34); cover_combo.setStyleSheet(_COMBO)
        v.addLayout(self._field_row("Max Cover Size", cover_combo))

        fmt_combo = QComboBox()
        fmt_combo.addItems(["JPEG (recommended)", "PNG", "Keep original"])
        fmt_combo.setFixedHeight(34); fmt_combo.setStyleSheet(_COMBO)
        v.addLayout(self._field_row("Cover Format", fmt_combo))
        v.addSpacing(6)

        v.addWidget(self._section("TEXT ENCODING"))
        v.addSpacing(2)
        encoding_combo = QComboBox()
        encoding_combo.addItems(["UTF-8", "UTF-16", "Latin-1"])
        encoding_combo.setFixedHeight(34); encoding_combo.setStyleSheet(_COMBO)
        v.addLayout(self._field_row("Text Encoding", encoding_combo))
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
        version = QLabel("Version 1.0 Beta")
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
            ("Space",        "Play / Pause preview"),
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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
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

def _audio_paths(urls) -> List[str]:
    paths=[]
    for url in urls:
        p=url.toLocalFile()
        if os.path.isfile(p) and Path(p).suffix.lower() in SUPPORTED_EXTENSIONS:
            paths.append(p)
        elif os.path.isdir(p):
            for root,_,files in os.walk(p):
                for name in files:
                    if Path(name).suffix.lower() in SUPPORTED_EXTENSIONS:
                        paths.append(os.path.join(root,name))
    return paths


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
        self.nav.files_dropped.connect(self._add_files)
        self.nav.nav_filter_changed.connect(self._nav_filter)
        self._nav_mode = "all"   # 'all' | 'genre'
        self._nav_value = ""
        ml.addWidget(self.nav)

        # Right area (search + splitter)
        right = QWidget(); right.setStyleSheet(f"background:{C_BG};")
        rl = QVBoxLayout(right); rl.setContentsMargins(0,0,0,0); rl.setSpacing(0)

        # Top search bar
        topbar = QFrame(); topbar.setFixedHeight(64)
        topbar.setStyleSheet(f"QFrame{{background:{C_BG};border-bottom:1px solid {C_BORDER};}}")
        tbl = QHBoxLayout(topbar); tbl.setContentsMargins(20,0,20,0); tbl.setSpacing(12)

        # Search icon (qtawesome)
        srch_ic = QLabel()
        srch_ic.setStyleSheet("background:transparent;border:none;")
        try:
            srch_ic.setPixmap(_ico("fa5s.search", C_TEXT3).pixmap(15,15))
        except Exception:
            srch_ic.setText("S")
        tbl.addWidget(srch_ic)

        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("Search in your library…")
        self.search_field.setFixedHeight(36)
        self.search_field.setStyleSheet(f"""
            QLineEdit{{background:{C_SURFACE2};color:{C_TEXT};border:1px solid {C_BORDER};
                       border-radius:18px;padding:0 16px;font-size:13px;}}
            QLineEdit:focus{{border-color:{C_PRIMARY};}}
        """)
        self.search_field.textChanged.connect(self._filter)
        tbl.addWidget(self.search_field, 1)

        sc = QLabel("⌘K")
        sc.setStyleSheet(f"background:{C_SURFACE2};color:{C_TEXT2};"
                         f"font-size:10px;font-weight:600;padding:3px 8px;"
                         f"border-radius:6px;border:none;")
        tbl.addWidget(sc); tbl.addSpacing(4)

        # Settings button (opens SettingsDialog)
        settings_btn = QPushButton(); settings_btn.setFixedSize(32,32)
        settings_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;border-radius:8px;}}"
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
        lh = QWidget(); lh.setFixedHeight(52); lh.setStyleSheet(f"background:{C_BG};")
        lhl = QHBoxLayout(lh); lhl.setContentsMargins(20,0,20,0)
        tl = QLabel("All Tracks"); tl.setStyleSheet(f"color:{C_TEXT};font-size:18px;font-weight:700;background:transparent;")
        self.count_lbl = QLabel("  0 files")
        self.count_lbl.setStyleSheet(f"color:{C_TEXT2};font-size:13px;background:transparent;")
        lhl.addWidget(tl); lhl.addWidget(self.count_lbl); lhl.addStretch()
        cl.addWidget(lh)

        # Filter pills
        fb = QFrame(); fb.setFixedHeight(44)
        fb.setStyleSheet(f"QFrame{{background:{C_BG};border-bottom:1px solid {C_SURFACE};}}")
        fbl = QHBoxLayout(fb); fbl.setContentsMargins(16,0,16,0); fbl.setSpacing(6)

        _pill_style = (f"QPushButton{{background:{C_SURFACE2};color:{C_TEXT2};"
                       f"border:1px solid {C_BORDER};border-radius:14px;"
                       f"font-size:11px;padding:0 12px;}}"
                       f"QPushButton:hover{{border-color:{C_PRIMARY};color:{C_TEXT};}}"
                       f"QPushButton:checked{{background:{C_PRIMARY};color:#fff;border-color:{C_PRIMARY};}}")

        p_all = QPushButton("All"); p_all.setFixedHeight(28); p_all.setCheckable(True)
        p_all.setChecked(True); p_all.setStyleSheet(_pill_style)
        p_all.clicked.connect(lambda: self._genre_filter(None, p_all))

        self._genre_btn = QPushButton("Genre  ˅"); self._genre_btn.setFixedHeight(28)
        self._genre_btn.setStyleSheet(_pill_style)
        self._genre_btn.clicked.connect(self._pick_genre_filter)

        p_bpm = QPushButton("BPM  ˅"); p_bpm.setFixedHeight(28)
        p_bpm.setStyleSheet(_pill_style)
        p_bpm.clicked.connect(self._pick_bpm_filter)

        fbl.addWidget(p_all)
        fbl.addWidget(self._genre_btn)
        fbl.addWidget(p_bpm)
        fbl.addStretch()

        # View toggle
        for vico, act in [("☰", True), ("⊟", False)]:
            vb = QPushButton(vico); vb.setFixedSize(30,30)
            bg = C_SURFACE2 if act else "transparent"
            vb.setStyleSheet(f"QPushButton{{background:{bg};color:{C_TEXT if act else C_TEXT2};"
                              f"border:none;border-radius:7px;font-size:14px;}}"
                              f"QPushButton:hover{{background:{C_SURFACE2};color:{C_TEXT};}}")
            fbl.addWidget(vb)
        cl.addWidget(fb)

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
        self.table.verticalHeader().setDefaultSectionSize(50)
        self.table.setIconSize(QSize(38,38))
        self.table.setWordWrap(False)
        self.table.viewport().setMouseTracking(True)
        self.table.setMouseTracking(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setDefaultSectionSize(130)
        self.table.horizontalHeader().setMinimumSectionSize(36)
        self.table.horizontalHeader().setSectionResizeMode(_NUM_COL, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(_COVER_COL, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(_NUM_COL, 40)
        self.table.setColumnWidth(_COVER_COL, 54)
        self.table.setStyleSheet(f"""
            QTableWidget{{background:{C_BG};gridline-color:transparent;border:none;
                          selection-background-color:transparent;outline:none;font-size:12px;}}
            QTableWidget::item{{border:none;padding:0;}}
            QHeaderView{{background:{C_SURFACE};}}
            QHeaderView::section{{background:{C_SURFACE};color:{C_TEXT2};border:none;
                border-right:1px solid {C_BORDER};border-bottom:2px solid {C_BORDER};
                padding:0 8px;font-weight:700;font-size:10px;letter-spacing:0.8px;height:32px;}}
            QScrollBar:vertical{{background:{C_SURFACE};width:8px;border:none;margin:0;}}
            QScrollBar::handle:vertical{{background:{C_BORDER};border-radius:4px;min-height:30px;}}
            QScrollBar::handle:vertical:hover{{background:{C_PRIMARY};}}
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}
            QScrollBar:horizontal{{background:{C_SURFACE};height:8px;border:none;margin:0;}}
            QScrollBar::handle:horizontal{{background:{C_BORDER};border-radius:4px;}}
            QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{{width:0;}}
        """)
        self._delegate = TrackDelegate(self.table)
        self.table.setItemDelegate(self._delegate)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.itemSelectionChanged.connect(self._on_sel)
        self.table.files_dropped.connect(self._add_files)
        cl.addWidget(self.table,1)
        splitter.addWidget(center)

        # Tag panel
        self.tag_panel = TagPanel()
        self.tag_panel.setMinimumWidth(290)
        self.tag_panel.tags_changed.connect(self._refresh_sel)
        self.tag_panel.save_requested.connect(self._save_sel)
        splitter.addWidget(self.tag_panel)
        splitter.setSizes([1080,320]); splitter.setStretchFactor(0,1)

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
            _uc = UpdateChecker(self)
            _uc.start()
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
        sm=mb.addMenu("Search")
        self._act(sm,"Search Tags…",
            lambda: self.tag_panel._search("tags_only"),  "Ctrl+F")
        self._act(sm,"Cover Only…",
            lambda: self.tag_panel._search("cover_only"), "Ctrl+Shift+F")

    @staticmethod
    def _act(menu, label, slot, sc=None):
        a=QAction(label); a.triggered.connect(slot)
        if sc: a.setShortcut(sc)
        menu.addAction(a)

    # ── Player controls ───────────────────────────────────────────────────────

    def _open_settings(self):
        dlg = SettingsDialog(self)
        dlg.exec()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _fit_cols(self):
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(_NUM_COL, 40)
        self.table.setColumnWidth(_COVER_COL, 54)

    def _filter(self, text: str):
        self._apply_filters(search=text)

    def _genre_filter(self, genre: Optional[str], btn: QPushButton):
        self._active_genre_filter = genre
        self._genre_btn.setText("Genre  ˅" if not genre else f"Genre: {genre}  ✕")
        self._apply_filters()

    def _pick_genre_filter(self):
        # Build list of genres in library
        genres_in_lib = sorted({
            af.genre.strip() for af in self.audio_files if af.genre.strip()
        })
        if not genres_in_lib:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu{{background:{C_SURFACE2};color:{C_TEXT};"
            f"border:1px solid {C_BORDER};border-radius:10px;padding:4px;}}"
            f"QMenu::item{{padding:6px 16px;border-radius:6px;font-size:12px;}}"
            f"QMenu::item:selected{{background:{C_PRIMARY};}}")
        clear = menu.addAction("— Alle Genres —")
        clear.triggered.connect(lambda: self._genre_filter(None, self._genre_btn))
        menu.addSeparator()
        for g in genres_in_lib:
            a = menu.addAction(g)
            a.triggered.connect(lambda checked, gn=g: self._genre_filter(gn, self._genre_btn))
        menu.exec(self._genre_btn.mapToGlobal(self._genre_btn.rect().bottomLeft()))

    def _pick_bpm_filter(self):
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu{{background:{C_SURFACE2};color:{C_TEXT};"
            f"border:1px solid {C_BORDER};border-radius:10px;padding:4px;}}"
            f"QMenu::item{{padding:6px 16px;border-radius:6px;font-size:12px;}}"
            f"QMenu::item:selected{{background:{C_PRIMARY};}}")
        menu.addAction("— Alle BPM —").triggered.connect(lambda: self._set_bpm_filter(None, None))
        menu.addSeparator()
        for label, lo, hi in [("< 100 BPM",0,100),("100–125 BPM",100,125),
                               ("125–135 BPM",125,135),("135–150 BPM",135,150),
                               ("> 150 BPM",150,999)]:
            menu.addAction(label).triggered.connect(
                lambda checked, a=lo, b=hi: self._set_bpm_filter(a, b))
        menu.exec(self._genre_btn.mapToGlobal(self._genre_btn.rect().bottomLeft()))

    def _set_bpm_filter(self, lo, hi):
        self._active_bpm_min = lo; self._active_bpm_max = hi
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

            # Nav filter: genre sidebar
            if nav_mode == "genre" and nav_value and af:
                af_genre = (af.genre or "").strip()
                if af_genre.lower() != nav_value.lower():
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

            self.table.setRowHidden(row, not match)

    def _nav_filter(self, mode: str, value: str = ""):
        self._nav_mode = mode
        self._nav_value = value
        self._apply_filters()

    def _update_status(self):
        n = len(self.audio_files)
        self.count_lbl.setText(f"  {n} {'file' if n==1 else 'files'}")
        self.nav.update_counts(n)
        if n == 0:
            self.statusBar().showMessage("Drop files here  ·  ⌘O open  ·  ⌘S save")
            return
        ts = sum(f.duration for f in self.audio_files)
        m, s = divmod(int(ts), 60); h, m = divmod(m, 60)
        dur = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        self.statusBar().showMessage(
            f"  {n} file{'s' if n!=1 else ''}  ·  Total {dur}"
            f"  ·  ⌘S save  ·  ⌘A select all  ·  Space Quick Look")

    def closeEvent(self, e):
        unsaved=[f for f in self.audio_files if f._modified]
        if unsaved:
            r=QMessageBox.question(self,"Unsaved Changes",
                f"{len(unsaved)} file(s) have unsaved changes.\nQuit anyway?",
                QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel)
            if r==QMessageBox.StandardButton.Cancel: e.ignore(); return
        e.accept()

    # ── File management ───────────────────────────────────────────────────────

    def _open_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,"Open Audio Files","",
            "Audio (*.mp3 *.flac *.wav *.aiff *.aif *.m4a *.mp4)")
        if paths: self._add_files(paths)

    def _open_folder(self):
        folder=QFileDialog.getExistingDirectory(self,"Open Folder")
        if not folder: return
        paths=[]
        for root,_,files in os.walk(folder):
            for name in files:
                if Path(name).suffix.lower() in SUPPORTED_EXTENSIONS:
                    paths.append(os.path.join(root,name))
        if paths: self._add_files(paths)

    def _add_files(self, paths: List[str]):
        existing={f.path for f in self.audio_files}
        new=[p for p in paths if p not in existing]
        if not new: return
        self.statusBar().showMessage(f"Loading {len(new)} file(s)…")
        for p in new:
            try: self.audio_files.append(AudioFile(p))
            except Exception as e: print(f"Error loading {p}: {e}")
        self._rebuild_table()

    def _rebuild_table(self):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.audio_files))
        for row,af in enumerate(self.audio_files): self._fill_row(row,af)
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(_NUM_COL,40); self.table.setColumnWidth(_COVER_COL,54)
        self._update_status()

    def _fill_row(self, row: int, af: AudioFile):
        for col,(field,_) in enumerate(COLUMNS):
            if field=="_num":
                item=QTableWidgetItem(str(row+1))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            elif field=="_cover":
                item=QTableWidgetItem()
                if af.cover_data:
                    pix=QPixmap(); pix.loadFromData(af.cover_data)
                    if not pix.isNull():
                        item.setIcon(QIcon(pix.scaled(38,38,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)))
            elif field=="title":
                tv=str(getattr(af,"title",""))
                item=QTableWidgetItem(tv)
                item.setData(TrackDelegate.SUBTITLE_ROLE, _subtitle(tv))
            elif field=="key":
                item=QTableWidgetItem(normalize_key(str(getattr(af,"key",""))))
            else:
                item=QTableWidgetItem(str(getattr(af,field,"")))
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

    # ── Save ──────────────────────────────────────────────────────────────────

    def _save_files(self, files):
        errors=[af.filename for af in files if not af.save()]
        if errors:
            QMessageBox.warning(self,"Save Error",
                "Could not save:\n\n"+"\n".join(errors))
        else:
            self.statusBar().showMessage(f"✓  {len(files)} file(s) saved.")

    def _save_sel(self): self._save_files(self._sel_files() or self.audio_files)
    def _save_all(self): self._save_files(self.audio_files)

    # ── Remove ────────────────────────────────────────────────────────────────

    def _remove_sel(self):
        rows=sorted({idx.row() for idx in self.table.selectedIndexes()},reverse=True)
        for row in rows:
            it=self.table.item(row,_NUM_COL)
            if it:
                af=it.data(Qt.ItemDataRole.UserRole)
                if af in self.audio_files: self.audio_files.remove(af)
            self.table.removeRow(row)
        self.tag_panel.load_files([]); self._update_status()

    def _clear_all(self):
        self.audio_files.clear()
        self.table.setRowCount(0); self.tag_panel.load_files([]); self._update_status()

    # ── Context menu ──────────────────────────────────────────────────────────

    def _context_menu(self, pos):
        sel=self._sel_files(); menu=QMenu(self)
        menu.setStyleSheet(f"QMenu{{background:{C_SURFACE2};color:{C_TEXT};"
                            f"border:1px solid {C_BORDER};border-radius:10px;padding:4px;}}"
                            f"QMenu::item{{padding:7px 16px;border-radius:6px;font-size:12px;}}"
                            f"QMenu::item:selected{{background:{C_PRIMARY};}}"
                            f"QMenu::separator{{background:{C_BORDER};height:1px;margin:3px 8px;}}")
        menu.addAction(f"▶  Quick Look (Space)").triggered.connect(
            lambda: _quick_look(sel[0].path) if sel else None)
        menu.addAction(f"💾  Save ({len(sel)} file(s))").triggered.connect(self._save_sel)
        menu.addSeparator()
        menu.addAction("🔍  Search Tags…").triggered.connect(lambda: self.tag_panel._search("tags_only"))
        menu.addAction("🖼  Cover Only…").triggered.connect(lambda: self.tag_panel._search("cover_only"))
        menu.addSeparator()
        menu.addAction("✏️  Rename").triggered.connect(self.tag_panel._rename)
        menu.addSeparator()
        menu.addAction("✕  Remove from List").triggered.connect(self._remove_sel)
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

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            sel = self._sel_files()
            if sel:
                _quick_look(sel[0].path)
            e.accept()
        elif e.matches(QKeySequence.StandardKey.Paste):
            self.tag_panel._paste()
        else:
            super().keyPressEvent(e)

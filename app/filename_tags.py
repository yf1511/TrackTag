"""
Tags from filename: "01. Artist - Title (Extended Mix) [www.site.com].mp3"
→ artist "Artist", title "Title (Extended Mix)". Shows a preview first.
"""
import re
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor

from .theme import (
    C_BG, C_SURFACE, C_SURFACE2, C_BORDER, C_TEXT, C_TEXT2, C_TEXT3,
    C_PRIMARY_SOFT, _BTN_PRIMARY, _BTN_GHOST,
)

# Junk commonly found in downloaded filenames
_JUNK = re.compile(
    r"\s*[\(\[]\s*(?:www\.[^\)\]]*|[^\)\]]*\.(?:com|net|org|ru|to|me|info)|free\s*(?:dl|download)"
    r"|official\s*(?:music\s*)?(?:video|audio)|lyrics?(?:\s*video)?|audio|hq|hd|4k"
    r"|\d{3}\s*kbps|320|mp3|flac|wav|aiff|clean|dirty|explicit)\s*[\)\]]", re.I)
_TRACKNO = re.compile(r"^\s*(?:\d{1,3}|[A-D]\d)\s*(?:[-._)\]]\s*|\s+(?=\D))")
_SEP = re.compile(r"\s+[-–—]\s+")


def parse_filename(name: str) -> tuple:
    """Return (artist, title) — artist may be empty if there's no separator."""
    s = Path(name).stem.replace("_", " ")
    s = _JUNK.sub("", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" -–—.")
    parts = _SEP.split(s)
    # "03 - Artist - Title" / "2024 - Artist - Title": drop the number segment
    if len(parts) >= 3 and re.fullmatch(r"(?:\d{1,4}|[A-D]\d)", parts[0].strip()):
        parts = parts[1:]
    if len(parts) >= 2:
        # "Artist - Title - Label": the extra parts are dropped
        artist, title = _TRACKNO.sub("", parts[0]), parts[1]
    else:
        artist, title = "", _TRACKNO.sub("", s)
    return artist.strip(), title.strip()


class FilenameTagsDialog(QDialog):
    applied = pyqtSignal(list)

    def __init__(self, files: list, parent=None):
        super().__init__(parent)
        self._files = files
        self._overwrite = False
        self._parsed = [parse_filename(f.filename) for f in files]
        self.setWindowTitle("Tags from Filename")
        self.setMinimumSize(860, 480); self.resize(980, 600)
        self._build()
        self._render()

    def _build(self):
        self.setStyleSheet(f"QDialog{{background:{C_BG};}}")
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        head = QFrame(); head.setStyleSheet(
            f"QFrame{{background:{C_BG};border:none;border-bottom:1px solid {C_BORDER};}}")
        hl = QHBoxLayout(head); hl.setContentsMargins(24, 18, 20, 16)
        col = QVBoxLayout(); col.setSpacing(2)
        t = QLabel("Tags from Filename"); t.setStyleSheet(
            f"color:{C_TEXT};font-size:18px;font-weight:700;border:none;")
        sub = QLabel("Reads “Artist - Title (Mix)” from the file name. Track numbers and "
                     "download-site junk are removed.")
        sub.setStyleSheet(f"color:{C_TEXT2};font-size:12px;border:none;")
        col.addWidget(t); col.addWidget(sub)
        hl.addLayout(col, 1)
        self._ow = QPushButton("Overwrite existing tags"); self._ow.setCheckable(True)
        self._ow.setFixedHeight(28)
        self._ow.setStyleSheet(
            f"QPushButton{{background:transparent;color:{C_TEXT2};border:1px solid {C_BORDER};"
            f"border-radius:14px;font-size:12px;padding:0 12px;}}"
            f"QPushButton:checked{{background:{C_PRIMARY_SOFT};color:#c4b5fd;"
            f"border-color:rgba(139,92,246,0.45);}}")
        self._ow.setToolTip("Off: only empty Artist / Title fields are filled")
        self._ow.toggled.connect(self._toggle)
        hl.addWidget(self._ow, 0, Qt.AlignmentFlag.AlignTop)
        root.addWidget(head)

        tb = QTableWidget(len(self._files), 3)
        tb.setHorizontalHeaderLabels(["FILE", "ARTIST", "TITLE"])
        tb.verticalHeader().setVisible(False); tb.verticalHeader().setDefaultSectionSize(40)
        tb.setShowGrid(False); tb.setFrameShape(QFrame.Shape.NoFrame)
        tb.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                           | QAbstractItemView.EditTrigger.EditKeyPressed)
        tb.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        hh = tb.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hh.setFixedHeight(34)
        for c in range(3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        tb.setStyleSheet(f"""
            QTableWidget{{background:{C_BG};color:{C_TEXT};border:none;outline:none;font-size:12px;}}
            QTableWidget::item{{border-bottom:1px solid {C_SURFACE2};padding:0 10px;}}
            QHeaderView::section{{background:{C_BG};color:{C_TEXT3};border:none;
                border-bottom:1px solid {C_BORDER};padding:0 10px;font-size:10px;
                font-weight:600;letter-spacing:0.9px;}}
            QLineEdit{{background:{C_SURFACE2};color:{C_TEXT};border:1px solid #8b5cf6;}}
        """)
        self._tb = tb
        root.addWidget(tb, 1)

        bottom = QFrame(); bottom.setFixedHeight(64)
        bottom.setStyleSheet(
            f"QFrame{{background:{C_SURFACE};border:none;border-top:1px solid {C_BORDER};}}")
        bl = QHBoxLayout(bottom); bl.setContentsMargins(24, 0, 20, 0); bl.setSpacing(8)
        self._foot = QLabel("Double-click a value to correct it")
        self._foot.setStyleSheet(f"color:{C_TEXT3};font-size:11px;border:none;")
        bl.addWidget(self._foot); bl.addStretch()
        cancel = QPushButton("Cancel"); cancel.setFixedHeight(36)
        cancel.setStyleSheet(_BTN_GHOST + "QPushButton{padding:0 16px;font-size:13px;}")
        cancel.clicked.connect(self.reject); bl.addWidget(cancel)
        self._apply_btn = QPushButton("Apply"); self._apply_btn.setFixedHeight(36)
        self._apply_btn.setMinimumWidth(160); self._apply_btn.setStyleSheet(_BTN_PRIMARY)
        self._apply_btn.clicked.connect(self._apply)
        bl.addWidget(self._apply_btn)
        root.addWidget(bottom)

    def _toggle(self, on: bool):
        self._overwrite = on
        self._render()

    def _new_value(self, af, field: str, parsed: str) -> str:
        cur = (getattr(af, field, "") or "").strip()
        return parsed if parsed and (self._overwrite or not cur) else ""

    def _render(self):
        tb = self._tb
        n = 0
        for r, (af, (artist, title)) in enumerate(zip(self._files, self._parsed)):
            name = QTableWidgetItem(af.filename)
            name.setFlags(Qt.ItemFlag.ItemIsEnabled)
            name.setForeground(QColor(C_TEXT2))
            tb.setItem(r, 0, name)
            changed = False
            for c, field, parsed in ((1, "artist", artist), (2, "title", title)):
                new = self._new_value(af, field, parsed)
                it = QTableWidgetItem(new or (getattr(af, field, "") or "—"))
                it.setData(Qt.ItemDataRole.UserRole, bool(new))
                it.setForeground(QColor("#c4b5fd" if new else C_TEXT3))
                if not new:
                    it.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    it.setToolTip("Already tagged — turn on “Overwrite existing tags”")
                tb.setItem(r, c, it)
                changed |= bool(new)
            n += changed
        self._apply_btn.setEnabled(n > 0)
        self._apply_btn.setText(f"Apply to {n} file{'s' if n != 1 else ''}" if n else "Nothing to change")

    def _apply(self):
        changed = []
        for r, af in enumerate(self._files):
            touched = False
            for c, field in ((1, "artist"), (2, "title")):
                it = self._tb.item(r, c)
                if it.data(Qt.ItemDataRole.UserRole) and it.text().strip():
                    af.set_field(field, it.text().strip()); touched = True
            if touched:
                changed.append(af)
        self.applied.emit(changed)
        self.accept()

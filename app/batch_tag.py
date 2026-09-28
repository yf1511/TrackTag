"""
Auto-Tag: find tags & artwork for many tracks at once.

Each track is searched on Beatport + Apple Music in the background and
matched with the same logic as the single-track search. The table shows what
would change per track; confident matches are ticked, uncertain ones are not.
Double-click a row to pick the result by hand.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QFrame, QProgressBar,
    QStyledItemDelegate, QStyle,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QRect, QSize
from PyQt6.QtGui import QPixmap, QColor, QFont, QPainter

from .theme import (
    C_BG, C_SURFACE, C_SURFACE2, C_BORDER, C_BORDER2, C_TEXT, C_TEXT2, C_TEXT3,
    C_PRIMARY, C_PRIMARY_SOFT, C_SUCCESS, C_ACCENT2, C_SEL_BG,
    _BTN_PRIMARY, _BTN_GHOST,
)
from .cover_search import (
    search_beatport, search_itunes, rank_results, best_match, _fetch_image,
    _rounded, MetaSearchDialog,
)

# Fields Auto-Tag can fill, and which are on by default
FIELDS = [("cover", "Artwork", True), ("genre", "Genre", True), ("label", "Label", True),
          ("album", "Album", False), ("year", "Year", True), ("bpm", "BPM", True),
          ("key", "Key", True), ("artist", "Artist", False), ("title", "Title", False)]

MATCHED, UNSURE, NOT_FOUND, SEARCHING, DONE = "matched", "unsure", "none", "searching", "done"
_STATUS = {
    MATCHED:   ("Matched",   C_SUCCESS,  "rgba(34,197,94,0.14)"),
    UNSURE:    ("Check",     C_ACCENT2,  "rgba(245,158,11,0.14)"),
    NOT_FOUND: ("Not found", C_TEXT3,    C_SURFACE2),
    SEARCHING: ("Searching", C_TEXT3,    "transparent"),
    DONE:      ("Applied",   "#c4b5fd",  C_PRIMARY_SOFT),
}

COL_CHECK, COL_ART, COL_TRACK, COL_MATCH, COL_CHANGES, COL_STATUS = range(6)
_ROLE_SUB = Qt.ItemDataRole.UserRole + 1


def _query_for(af) -> str:
    title = (af.title or "").strip() or Path(af.filename).stem.replace("_", " ")
    return f"{(af.artist or '').strip()} {title}".strip()


# ── Background search ─────────────────────────────────────────────────────────

class _SearchAll(QThread):
    found = pyqtSignal(int, object, str)     # row, result dict | None, status

    def __init__(self, jobs):                # [(row, query, duration)]
        super().__init__(); self._jobs = jobs

    def run(self):
        def one(job):
            row, q, duration = job
            if self.isInterruptionRequested():
                return row, None, NOT_FOUND
            results = {}
            for r in search_beatport(q) + search_itunes(q):
                results[f"{r['source']}_{len(results)}"] = r
            ranked = rank_results(results, q, duration)
            best = best_match(results, ranked)
            if best:
                return row, best, MATCHED
            if ranked and results[ranked[0]]["_score"] >= 0.55:
                return row, results[ranked[0]], UNSURE
            return row, None, NOT_FOUND
        # 3 at a time keeps Beatport happy
        with ThreadPoolExecutor(max_workers=3) as ex:
            for row, res, status in ex.map(one, self._jobs):
                if self.isInterruptionRequested(): return
                self.found.emit(row, res, status)


class _Download(QThread):
    progress = pyqtSignal(int, int)
    loaded = pyqtSignal(int, bytes)          # row, data (thumbs or full artwork)

    def __init__(self, jobs):                # [(row, url, source)]
        super().__init__(); self._jobs = jobs

    def run(self):
        def one(job):
            row, url, src = job
            if self.isInterruptionRequested(): return row, b""
            try:    return row, _fetch_image(url, src, timeout=15)
            except Exception: return row, b""
        with ThreadPoolExecutor(max_workers=6) as ex:
            for i, (row, data) in enumerate(ex.map(one, self._jobs), 1):
                if self.isInterruptionRequested(): return
                if data: self.loaded.emit(row, data)
                self.progress.emit(i, len(self._jobs))


# ── Two-line cell painter ─────────────────────────────────────────────────────

class _Delegate(QStyledItemDelegate):
    def paint(self, p, opt, idx):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        p.fillRect(opt.rect, QColor(C_SEL_BG if sel else C_BG))
        p.fillRect(opt.rect.left(), opt.rect.bottom(), opt.rect.width(), 1, QColor(C_SURFACE2))
        r = opt.rect.adjusted(10, 0, -10, 0)
        col = idx.column()
        text = idx.data(Qt.ItemDataRole.DisplayRole) or ""
        sub = idx.data(_ROLE_SUB) or ""
        f = QFont(opt.font); f.setPixelSize(12)

        if col == COL_ART:
            pix = idx.data(Qt.ItemDataRole.DecorationRole)
            ax, ay = opt.rect.x() + (opt.rect.width()-36)//2, opt.rect.y() + (opt.rect.height()-36)//2
            if isinstance(pix, QPixmap) and not pix.isNull():
                p.drawPixmap(ax, ay, pix)
            else:
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(C_SURFACE2))
                p.drawRoundedRect(ax, ay, 36, 36, 6, 6)
        elif col == COL_STATUS:
            label, fg, bg = _STATUS.get(text, _STATUS[SEARCHING])
            f.setPixelSize(11); f.setWeight(QFont.Weight.DemiBold); p.setFont(f)
            w = p.fontMetrics().horizontalAdvance(label) + 18
            chip = QRect(r.left(), opt.rect.center().y()-10, w, 20)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(bg) if not bg.startswith("rgba") else _rgba(bg))
            p.drawRoundedRect(chip, 10, 10)
            p.setPen(QColor(fg)); p.drawText(chip, Qt.AlignmentFlag.AlignCenter, label)
        elif col == COL_CHECK:
            super().paint(p, opt, idx)
        else:
            mid = opt.rect.center().y()
            f1 = QFont(f); f1.setPixelSize(13 if col != COL_CHANGES else 12)
            if col == COL_TRACK: f1.setWeight(QFont.Weight.Medium)
            p.setFont(f1)
            color = C_TEXT if col != COL_CHANGES else ("#c4b5fd" if text and text[0] == "+" else C_TEXT3)
            p.setPen(QColor(color))
            fm = p.fontMetrics()
            if sub:
                p.drawText(QRect(r.left(), mid-17, r.width(), 17), Qt.AlignmentFlag.AlignVCenter,
                           fm.elidedText(text, Qt.TextElideMode.ElideRight, r.width()))
                f2 = QFont(f); f2.setPixelSize(11); p.setFont(f2); p.setPen(QColor(C_TEXT2))
                p.drawText(QRect(r.left(), mid+2, r.width(), 15), Qt.AlignmentFlag.AlignVCenter,
                           p.fontMetrics().elidedText(sub, Qt.TextElideMode.ElideRight, r.width()))
            else:
                p.drawText(r, Qt.AlignmentFlag.AlignVCenter,
                           fm.elidedText(text, Qt.TextElideMode.ElideRight, r.width()))
        p.restore()

    def sizeHint(self, opt, idx):
        return QSize(super().sizeHint(opt, idx).width(), 56)


def _rgba(s: str) -> QColor:
    r, g, b, a = [float(x) for x in s[s.index("(")+1:-1].split(",")]
    return QColor(int(r), int(g), int(b), int(a * 255))


# ── Dialog ────────────────────────────────────────────────────────────────────

class BatchTagDialog(QDialog):
    applied = pyqtSignal(list)               # AudioFiles that changed

    def __init__(self, files: list, preset: str = "all", parent=None):
        super().__init__(parent)
        self._files = files
        self._res: List[Optional[dict]] = [None] * len(files)
        self._status = [SEARCHING] * len(files)
        self._thumbs: dict[int, QPixmap] = {}
        self._done_n = 0
        self._touched: set = set()           # rows the user ticked/unticked by hand
        self._changed_by_hand: set = set()   # rows applied via the single search
        self._search = None; self._thumb_dl = None; self._cover_dl = None
        self._overwrite = False
        self._on = {k: d for k, _, d in FIELDS}
        if preset == "cover_only":
            self._on = {k: k == "cover" for k, _, _ in FIELDS}
        self.setWindowTitle("Auto-Tag")
        self.setMinimumSize(980, 620); self.resize(1080, 720)
        self._setup_ui()
        self._start()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setStyleSheet(f"QDialog{{background:{C_BG};}}")
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        head = QFrame(); head.setStyleSheet(
            f"QFrame{{background:{C_BG};border:none;border-bottom:1px solid {C_BORDER};}}")
        hl = QVBoxLayout(head); hl.setContentsMargins(24, 18, 24, 14); hl.setSpacing(12)
        row = QHBoxLayout(); row.setSpacing(12)
        col = QVBoxLayout(); col.setSpacing(2)
        t = QLabel(f"Auto-Tag {len(self._files)} track{'s' if len(self._files) != 1 else ''}")
        t.setStyleSheet(f"color:{C_TEXT};font-size:18px;font-weight:700;border:none;")
        self._sub = QLabel("Searching Beatport and Apple Music…")
        self._sub.setStyleSheet(f"color:{C_TEXT2};font-size:12px;border:none;")
        col.addWidget(t); col.addWidget(self._sub)
        row.addLayout(col, 1)
        hl.addLayout(row)
        self._bar = QProgressBar(); self._bar.setFixedHeight(3); self._bar.setTextVisible(False)
        self._bar.setRange(0, len(self._files)); self._bar.setValue(0)
        self._bar.setStyleSheet(
            f"QProgressBar{{background:{C_SURFACE2};border:none;border-radius:1px;}}"
            f"QProgressBar::chunk{{background:{C_PRIMARY};border-radius:1px;}}")
        hl.addWidget(self._bar)

        # Field toggles
        tog = QHBoxLayout(); tog.setSpacing(6)
        lbl = QLabel("FILL"); lbl.setStyleSheet(
            f"color:{C_TEXT3};font-size:10px;font-weight:600;letter-spacing:1.2px;border:none;")
        tog.addWidget(lbl); tog.addSpacing(6)
        pill = (f"QPushButton{{background:transparent;color:{C_TEXT2};border:1px solid {C_BORDER};"
                f"border-radius:13px;font-size:12px;padding:0 11px;}}"
                f"QPushButton:hover{{background:{C_SURFACE2};color:{C_TEXT};}}"
                f"QPushButton:checked{{background:{C_PRIMARY_SOFT};color:#c4b5fd;"
                f"border-color:rgba(139,92,246,0.45);}}")
        for key, name, _ in FIELDS:
            b = QPushButton(name); b.setCheckable(True); b.setChecked(self._on[key])
            b.setFixedHeight(26); b.setStyleSheet(pill)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.toggled.connect(lambda on, k=key: self._toggle_field(k, on))
            tog.addWidget(b)
        tog.addStretch()
        self._ow = QPushButton("Overwrite existing values"); self._ow.setCheckable(True)
        self._ow.setFixedHeight(26); self._ow.setStyleSheet(pill)
        self._ow.setToolTip("Off: only empty fields are filled")
        self._ow.toggled.connect(self._toggle_overwrite)
        tog.addWidget(self._ow)
        hl.addLayout(tog)
        root.addWidget(head)

        # Table
        tb = QTableWidget(len(self._files), 6)
        tb.setHorizontalHeaderLabels(["", "", "YOUR TRACK", "FOUND", "CHANGES", "STATUS"])
        tb.verticalHeader().setVisible(False)
        tb.verticalHeader().setDefaultSectionSize(56)
        tb.setShowGrid(False); tb.setFrameShape(QFrame.Shape.NoFrame)
        tb.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tb.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        tb.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tb.setItemDelegate(_Delegate(tb))
        hh = tb.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hh.setFixedHeight(34); hh.setHighlightSections(False)
        for c, w in ((COL_CHECK, 40), (COL_ART, 52), (COL_STATUS, 110)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed); tb.setColumnWidth(c, w)
        for c in (COL_TRACK, COL_MATCH):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(COL_CHANGES, QHeaderView.ResizeMode.Fixed)
        tb.setColumnWidth(COL_CHANGES, 220)
        tb.setStyleSheet(f"""
            QTableWidget{{background:{C_BG};border:none;outline:none;}}
            QHeaderView::section{{background:{C_BG};color:{C_TEXT3};border:none;
                border-bottom:1px solid {C_BORDER};padding:0 10px;font-size:10px;
                font-weight:600;letter-spacing:0.9px;}}
            QTableWidget::indicator{{width:16px;height:16px;border-radius:5px;
                border:1px solid {C_BORDER2};background:{C_SURFACE2};margin-left:12px;}}
            QTableWidget::indicator:checked{{background:{C_PRIMARY};border-color:{C_PRIMARY};
                image:url("{_check_png()}");}}
            QTableWidget::indicator:disabled{{background:transparent;border-color:{C_SURFACE2};}}
        """)
        for i, af in enumerate(self._files):
            ck = QTableWidgetItem(); ck.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            ck.setCheckState(Qt.CheckState.Unchecked)
            tb.setItem(i, COL_CHECK, ck)
            tb.setItem(i, COL_ART, QTableWidgetItem())
            it = QTableWidgetItem(af.title or Path(af.filename).stem)
            it.setData(_ROLE_SUB, af.artist or af.filename)
            tb.setItem(i, COL_TRACK, it)
            tb.setItem(i, COL_MATCH, QTableWidgetItem(""))
            tb.setItem(i, COL_CHANGES, QTableWidgetItem(""))
            tb.setItem(i, COL_STATUS, QTableWidgetItem(SEARCHING))
        tb.itemChanged.connect(self._on_item_changed)
        tb.cellDoubleClicked.connect(self._pick_manually)
        self._tb = tb
        root.addWidget(tb, 1)

        # Bottom bar
        bottom = QFrame(); bottom.setFixedHeight(64)
        bottom.setStyleSheet(
            f"QFrame{{background:{C_SURFACE};border:none;border-top:1px solid {C_BORDER};}}")
        bl = QHBoxLayout(bottom); bl.setContentsMargins(24, 0, 20, 0); bl.setSpacing(8)
        self._foot = QLabel("Double-click a track to choose its result by hand")
        self._foot.setStyleSheet(f"color:{C_TEXT3};font-size:11px;border:none;")
        bl.addWidget(self._foot); bl.addStretch()
        cancel = QPushButton("Cancel"); cancel.setFixedHeight(36)
        cancel.setStyleSheet(_BTN_GHOST + "QPushButton{padding:0 16px;font-size:13px;}")
        cancel.clicked.connect(self.reject); bl.addWidget(cancel)
        self._apply_btn = QPushButton("Apply"); self._apply_btn.setFixedHeight(36)
        self._apply_btn.setMinimumWidth(170); self._apply_btn.setStyleSheet(_BTN_PRIMARY)
        self._apply_btn.clicked.connect(self._apply); self._apply_btn.setEnabled(False)
        bl.addWidget(self._apply_btn)
        root.addWidget(bottom)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def _start(self):
        jobs = [(i, _query_for(af), float(getattr(af, "duration", 0) or 0))
                for i, af in enumerate(self._files)]
        self._search = _SearchAll(jobs)
        self._search.found.connect(self._on_found)
        self._search.finished.connect(self._on_search_done)
        self._search.start()

    def _stop_threads(self):
        ts = [t for t in (self._search, self._thumb_dl, self._cover_dl) if t]
        for t in ts:
            if t.isRunning(): t.requestInterruption()
        for t in ts:
            if t.isRunning(): t.wait()

    def reject(self):
        self._stop_threads(); super().reject()

    def accept(self):
        self._stop_threads(); super().accept()

    def closeEvent(self, e):
        self._stop_threads(); super().closeEvent(e)

    # ── results ───────────────────────────────────────────────────────────────

    def _on_found(self, row: int, res, status: str):
        self._res[row] = res
        self._status[row] = status
        self._done_n += 1
        self._bar.setValue(self._done_n)
        self._sub.setText(f"Searching… {self._done_n} of {len(self._files)}")
        self._render_row(row)

    def _on_search_done(self):
        n = {s: self._status.count(s) for s in (MATCHED, UNSURE, NOT_FOUND)}
        parts = [f"{n[MATCHED]} matched"]
        if n[UNSURE]: parts.append(f"{n[UNSURE]} to check")
        if n[NOT_FOUND]: parts.append(f"{n[NOT_FOUND]} not found")
        self._sub.setText(" · ".join(parts))
        self._bar.hide()
        jobs = [(r, self._res[r]["thumb"], self._res[r]["source"])
                for r in range(len(self._files)) if self._res[r] and self._res[r].get("thumb")
                and r not in self._thumbs]
        if jobs:
            self._thumb_dl = _Download(jobs)
            self._thumb_dl.loaded.connect(self._set_thumb)
            self._thumb_dl.start()

    def _set_thumb(self, row: int, data: bytes):
        pix = QPixmap(); pix.loadFromData(data)
        if pix.isNull(): return
        self._thumbs[row] = _rounded(pix, 36, 6)
        self._tb.item(row, COL_ART).setData(Qt.ItemDataRole.DecorationRole, self._thumbs[row])

    # ── what would change ─────────────────────────────────────────────────────

    def _changes(self, row: int) -> list:
        res, af = self._res[row], self._files[row]
        if not res or self._status[row] == DONE:
            return []
        out = []
        for key, name, _ in FIELDS:
            if not self._on[key]: continue
            if key == "cover":
                if res.get("cover_url") and (self._overwrite or not af.cover_data):
                    out.append((key, name))
                continue
            new = str(res.get(key) or "").strip()
            cur = str(getattr(af, key, "") or "").strip()
            if key == "key":
                new, cur = _norm_key(new), _norm_key(cur)
            if new and new.lower() != cur.lower() and (self._overwrite or not cur):
                out.append((key, name))
        return out

    def _render_row(self, row: int):
        tb = self._tb
        tb.blockSignals(True)
        res, status = self._res[row], self._status[row]
        m = tb.item(row, COL_MATCH)
        if res:
            m.setText(res.get("title", ""))
            bits = [res.get("artist", ""), res.get("label", "")]
            if res.get("bpm"): bits.append(str(res["bpm"]))
            if res.get("key"): bits.append(_norm_key(res["key"]))
            m.setData(_ROLE_SUB, " · ".join(b for b in bits if b))
        elif status == NOT_FOUND:
            m.setText("—"); m.setData(_ROLE_SUB, "")
        ch = self._changes(row)
        c = tb.item(row, COL_CHANGES)
        if status == DONE:
            c.setText("Applied by hand")
        elif res:
            c.setText("+ " + ", ".join(n for _, n in ch) if ch else "Nothing to change")
        else:
            c.setText("")
        tb.item(row, COL_STATUS).setText(status)
        ck = tb.item(row, COL_CHECK)
        can = bool(ch) and status in (MATCHED, UNSURE)
        ck.setFlags((Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled) if can
                    else Qt.ItemFlag.NoItemFlags)
        if not can:
            ck.setCheckState(Qt.CheckState.Unchecked)
        elif row not in self._touched:   # default: tick confident matches only
            ck.setCheckState(Qt.CheckState.Checked if status == MATCHED else Qt.CheckState.Unchecked)
        tb.blockSignals(False)
        self._update_apply()

    def _toggle_field(self, key: str, on: bool):
        self._on[key] = on
        self._rerender()

    def _toggle_overwrite(self, on: bool):
        self._overwrite = on
        self._rerender()

    def _rerender(self):
        for r in range(len(self._files)):
            if self._status[r] != SEARCHING:
                self._render_row(r)

    def _on_item_changed(self, item):
        if item.column() == COL_CHECK:
            self._touched.add(item.row())
            self._update_apply()

    def _checked_rows(self) -> list:
        return [r for r in range(len(self._files))
                if self._tb.item(r, COL_CHECK).checkState() == Qt.CheckState.Checked]

    def _update_apply(self):
        n = len(self._checked_rows())
        self._apply_btn.setEnabled(n > 0)
        self._apply_btn.setText(f"Apply to {n} track{'s' if n != 1 else ''}" if n else "Apply")

    # ── manual pick ───────────────────────────────────────────────────────────

    def _pick_manually(self, row: int, _col: int):
        af = self._files[row]
        cur = {k: str(getattr(af, k, "") or "") for k in
               ("artist", "title", "album", "genre", "label", "year", "bpm", "key")}
        cur["key"] = _norm_key(cur["key"])
        title = (af.title or "").strip() or Path(af.filename).stem.replace("_", " ")
        dlg = MetaSearchDialog(artist=af.artist, title=title, album=af.album, preset="all",
                               current=cur, duration=float(af.duration or 0),
                               has_cover=bool(af.cover_data), parent=self)
        def apply(payload):
            _apply_payload(af, payload)
            self._status[row] = DONE
            self._changed_by_hand.add(row)
            self._render_row(row)
        dlg.result_selected.connect(apply)
        dlg.exec()

    # ── apply ─────────────────────────────────────────────────────────────────

    def _apply(self):
        rows = self._checked_rows()
        plan = {r: self._changes(r) for r in rows}
        covers = [(r, self._res[r]["cover_url"], self._res[r]["source"])
                  for r in rows if any(k == "cover" for k, _ in plan[r])]
        self._plan = plan
        self._cover_data: dict[int, bytes] = {}
        if covers:
            self._apply_btn.setEnabled(False)
            self._foot.setText(f"Downloading artwork 0 of {len(covers)}…")
            self._cover_dl = _Download(covers)
            self._cover_dl.loaded.connect(lambda r, d: self._cover_data.__setitem__(r, d))
            self._cover_dl.progress.connect(
                lambda i, n: self._foot.setText(f"Downloading artwork {i} of {n}…"))
            self._cover_dl.finished.connect(self._finish_apply)
            self._cover_dl.start()
        else:
            self._finish_apply()

    def _finish_apply(self):
        changed = [self._files[r] for r in self._changed_by_hand]
        for r, fields in self._plan.items():
            af, res = self._files[r], self._res[r]
            payload = {k: res[k] for k, _ in fields if k != "cover" and res.get(k)}
            if r in self._cover_data:
                payload["cover_data"] = self._cover_data[r]
                payload["cover_mime"] = "image/jpeg"
            if payload:
                _apply_payload(af, payload)
                if af not in changed: changed.append(af)
        self.applied.emit(changed)
        self.accept()


def _norm_key(k: str) -> str:
    try:
        from .main_window import normalize_key
        return normalize_key(k)
    except Exception:
        return k


def _check_png() -> str:
    from .cover_search import _check_png as f
    return f()


def _apply_payload(af, payload: dict):
    if "cover_data" in payload:
        af.set_cover(payload["cover_data"], payload.get("cover_mime", "image/jpeg"))
    for key in ("artist", "title", "album", "genre", "label", "year", "bpm", "key"):
        if payload.get(key):
            af.set_field(key, _norm_key(payload[key]) if key == "key" else str(payload[key]))

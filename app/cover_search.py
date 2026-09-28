"""
Tag & cover search: Beatport + Apple Music run in parallel.

Every result is scored against the track (artist, title, mix version and
length). The best confident match is preselected and merged with the other
matching results (earliest release year, original release over compilations,
hi-res artwork). The preview shows current → new for every field; by default
only empty fields are filled.
"""
import json, re, time, unicodedata, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QWidget, QFrame, QCheckBox, QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QRect
from PyQt6.QtGui import QPixmap, QPainter, QPainterPath, QColor

from .theme import (
    C_BG, C_SURFACE, C_SURFACE2, C_SURFACE3, C_BORDER, C_BORDER2,
    C_TEXT, C_TEXT2, C_TEXT3, C_PRIMARY, C_SUCCESS, C_ACCENT2, C_SEL_BG,
    _BTN_PRIMARY, _BTN_SECONDARY, _BTN_GHOST,
)

# ── constants ─────────────────────────────────────────────────────────────────

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

COVER_PX = 1400   # artwork size requested from the sources

SOURCES = {
    "beatport": {"name": "Beatport",    "color": "#01ff95"},
    "itunes":   {"name": "Apple Music", "color": "#fc3c44"},
}

# Words that describe a version rather than identify a track
_GENERIC = {"original", "extended", "mix", "radio", "edit", "club", "version",
            "remix", "rmx", "dub", "vip", "feat", "ft", "featuring", "the",
            "and", "x", "vs", "with", "a", "remastered", "remaster"}
_COMPILATION = re.compile(
    r"\b(vol|volume|sounds|hits|compilation|various|best of|essentials|collection|"
    r"sampler|selection|anthems|top \d+|summer|winter|ibiza|miami|ade|20\d\d)\b", re.I)


# ── matching helpers ──────────────────────────────────────────────────────────

def _tokens(s: str) -> list:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z0-9]+", s)


def _core(tokens) -> set:
    return {t for t in tokens if t not in _GENERIC}


def _version(title: str) -> set:
    """Tokens inside the trailing (…) / […] — e.g. {'extended','mix'}."""
    parts = re.findall(r"[\(\[]([^\)\]]+)[\)\]]", title or "")
    return set(_tokens(" ".join(parts)))


def _vkey(title: str) -> set:
    """Comparable version: 'Original Mix' ≡ no version, 'Extended Mix' → {'extended'}."""
    return _version(title) - {"mix", "original", "version"}


def _base_title(title: str) -> str:
    return re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", title or "").strip()


def _score(r: dict, query: str, duration: float) -> float:
    """0‥1+ — how well a result matches the query (and the file's length)."""
    q_core = _core(_tokens(query))
    if not q_core:
        return 0.0
    r_core = _core(_tokens(f"{r['artist']} {r['title']}"))
    hit = q_core & r_core
    recall    = len(hit) / len(q_core)
    precision = len(hit) / max(1, len(r_core))
    score = 0.65 * recall + 0.35 * precision

    # Mix version: "extended" vs "radio" etc.
    qv, rv = _vkey(query), _vkey(r["title"])
    if qv:
        if qv == rv:            score += 0.10
        elif qv & rv:           score += 0.04
        elif rv:                score -= 0.10
    elif rv:                    score -= 0.04   # query has no version, result is a special one

    # Track length vs. the file
    length = r.get("length") or 0
    if duration and length:
        diff = abs(duration - length)
        if diff <= 2:    score += 0.15
        elif diff <= 8:  score += 0.05
        elif diff > 30:  score -= 0.15
    return score


def _is_match(r: dict, query: str) -> bool:
    q_core = _core(_tokens(query))
    r_core = _core(_tokens(f"{r['artist']} {r['title']}"))
    if not q_core or not r_core:
        return False
    # DJ-mixed compilation cuts ("[Mixed]") are not the track itself
    if "mixed" in _tokens(r["title"]) and "mixed" not in _tokens(query):
        return False
    # A remixer named in the query ("… (Youree Remix)") must be in the result
    qv_core = _core(_version(query))
    if qv_core and not qv_core <= r_core:
        return False
    hit = q_core & r_core
    return len(hit) / len(q_core) >= 0.85 and len(hit) / len(r_core) >= 0.6


def _clean_artists(names: list) -> str:
    """Drop combined entities ('A & B') when A and B are listed; keep their order."""
    names = [n.strip() for n in names if n and n.strip()]
    singles = set(names)
    order = names
    for n in names:
        parts = [p.strip() for p in re.split(r"\s*(?:&|,| and | x )\s*", n) if p.strip()]
        if len(parts) > 1 and all(p in singles for p in parts):
            order = parts + [m for m in names if m not in parts and m != n]
            break
    out, seen = [], set()
    for n in order:
        parts = [p.strip() for p in re.split(r"\s*(?:&|,| and | x )\s*", n) if p.strip()]
        if len(parts) > 1 and all(p in singles for p in parts):
            continue
        if n.lower() not in seen:
            seen.add(n.lower()); out.append(n)
    return ", ".join(out)


def _tidy_artist(artist: str, title: str, query: str) -> str:
    """Beatport lists remixers as artists and sorts alphabetically — undo both."""
    names = [n.strip() for n in artist.split(",") if n.strip()]
    ver = _core(_version(title))
    keep = [n for n in names if not (_core(_tokens(n)) and _core(_tokens(n)) <= ver)]
    if keep:
        names = keep
    ql = " ".join(_tokens(query))
    def pos(n):
        i = ql.find(" ".join(_tokens(n)))
        return i if i >= 0 else 10**6
    return ", ".join(sorted(names, key=pos))


# ── Ranking ───────────────────────────────────────────────────────────────────

def rank_results(results: dict, q: str, duration: float) -> list:
    """Score, tidy and sort results (uids); sets r['_score'] and r['_match']."""
    seen, ranked = set(), []
    for uid, r in results.items():
        k = (r["source"], r["artist"].lower(), r["title"].lower(),
             (r["label"] or r["album"]).lower(), r["year"])
        if k in seen: continue
        seen.add(k)
        if r["source"] == "beatport":
            r["artist"] = _tidy_artist(r["artist"], r["title"], q)
        r["_score"] = _score(r, q, duration) - (0.05 if r.get("compilation") else 0)
        r["_match"] = _is_match(r, q)
        ranked.append(uid)
    # Beatport first on ties (richer DJ data)
    ranked.sort(key=lambda u: (not results[u]["_match"],
                               -results[u]["_score"],
                               results[u]["source"] != "beatport"))
    return ranked


def best_match(results: dict, ranked: list) -> Optional[dict]:
    """Merge all confident matches of the same track into one result."""
    matches = [results[u] for u in ranked if results[u]["_match"]]
    if not matches:
        return None
    top = matches[0]
    # Same track = same base title and same version (Original ≡ none)
    same_base, top_v = _core(_tokens(_base_title(top["title"]))), _vkey(top["title"])
    same = [m for m in matches
            if _core(_tokens(_base_title(m["title"]))) == same_base
            and _vkey(m["title"]) == top_v]

    bp = [m for m in same if m["source"] == "beatport"]
    # Original release: not a compilation, then earliest year
    bp.sort(key=lambda m: (m.get("compilation", False), m["year"] or "9999"))
    it = [m for m in same if m["source"] == "itunes"]
    it.sort(key=lambda m: (m.get("compilation", False), m["year"] or "9999"))
    primary = bp[0] if bp else top

    best = dict(primary)
    years = [m["year"] for m in same if m["year"]]
    if years: best["year"] = min(years)
    for f in ("genre", "label", "album", "bpm", "key"):
        if not best.get(f):
            for m in bp + it:
                if m.get(f): best[f] = m[f]; break
    best["_sources"] = sorted({m["source"] for m in same}, key=lambda s: s != "beatport")
    if not best.get("cover_url") and it:
        best["cover_url"], best["thumb"] = it[0]["cover_url"], it[0]["thumb"]
    best["_primary"] = next((u for u, r in results.items() if r is primary), None)
    best["_best"] = True
    best["_match"] = True
    return best


# ── Workers ───────────────────────────────────────────────────────────────────

def search_itunes(q: str) -> list:
    try:
        enc = urllib.parse.quote(q)
        req = urllib.request.Request(
            f"https://itunes.apple.com/search?term={enc}&entity=song&limit=10",
            headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        out = []
        for it in data.get("results", []):
            t = it.get("artworkUrl100", "")
            out.append({
                "source": "itunes",
                "artist": it.get("artistName", ""),
                "title":  it.get("trackName", ""),
                "album":  it.get("collectionName", ""),
                "genre":  it.get("primaryGenreName", ""),
                "label":  "",
                "year":   it.get("releaseDate", "")[:4],
                "bpm": "", "key": "",
                "length": (it.get("trackTimeMillis") or 0) / 1000,
                "thumb":     t.replace("100x100bb", "200x200bb"),
                "cover_url": t.replace("100x100bb", f"{COVER_PX}x{COVER_PX}bb"),
                "compilation": it.get("collectionArtistName", "") == "Various Artists",
            })
        return out
    except Exception as e:
        print(f"iTunes: {e}"); return []


def search_beatport(q: str) -> list:
    try:
        return _Beatport(q).search()
    except Exception as e:
        print(f"Beatport: {e}"); return []


class _iTunesWorker(QThread):
    done = pyqtSignal(list)
    def __init__(self, q): super().__init__(); self.q = q
    def run(self): self.done.emit(search_itunes(self.q))


class _BeatportWorker(QThread):
    done = pyqtSignal(list)
    def __init__(self, q): super().__init__(); self.q = q
    def run(self): self.done.emit(search_beatport(self.q))


class _Beatport:
    """Beatport search (scrapes the public search page)."""
    def __init__(self, q): self.q = q

    def search(self) -> list:
        return self._scrape()

    @staticmethod
    def _art(uri: str, size: int) -> str:
        if "{w}x{h}" in uri:
            return uri.replace("{w}x{h}", f"{size}x{size}")
        return re.sub(r"/\d+x\d+(?=/|$)", f"/{size}x{size}", uri)

    def _parse_track(self, it: dict) -> dict:
        release = it.get("release") or {}
        name = it.get("track_name") or it.get("name") or ""
        mix  = it.get("mix_name") or ""
        title = f"{name} ({mix})" if mix and mix.lower() not in name.lower() else name

        names = [(a.get("artist_name") or a.get("name") or "")
                 for a in (it.get("artists") or []) if isinstance(a, dict)]

        art = release.get("release_image_dynamic_uri") or release.get("release_image_uri") or ""
        img = release.get("image") or {}
        if not art and isinstance(img, dict):
            art = img.get("dynamic_uri") or img.get("uri") or ""

        gens = it.get("genre") or []
        g0 = gens[0] if isinstance(gens, list) and gens else gens
        genre = (g0.get("genre_name") or g0.get("name") or "") if isinstance(g0, dict) else str(g0 or "")

        lb = it.get("label") or release.get("label") or {}
        label = (lb.get("label_name") or lb.get("name") or "") if isinstance(lb, dict) else str(lb or "")

        rel_name = release.get("release_name") or release.get("name") or ""
        return {
            "source":  "beatport",
            "artist":  _clean_artists(names),
            "title":   title,
            "album":   rel_name,
            "genre":   genre,
            "label":   label,
            "year":    (it.get("publish_date") or it.get("release_date") or "")[:4],
            "bpm":     str(it.get("bpm") or ""),
            "key":     it.get("key_name") or "",
            "length":  (it.get("length") or 0) / 1000,
            "thumb":     self._art(art, 200) if art else "",
            "cover_url": self._art(art, COVER_PX) if art else "",
            "compilation": bool(rel_name) and (
                bool(_COMPILATION.search(rel_name))
                and not (_core(_tokens(name)) <= _core(_tokens(rel_name)))),
        }

    def _scrape(self) -> list:
        enc = urllib.parse.quote(self.q)
        req = urllib.request.Request(
            f"https://www.beatport.com/search/tracks?q={enc}",
            headers={"User-Agent": _UA, "Accept": "text/html",
                     "Accept-Language": "en-US,en;q=0.9",
                     "Referer": "https://www.beatport.com/"})
        with urllib.request.urlopen(req, timeout=14) as r:
            html = r.read().decode("utf-8", errors="replace")
        m = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not m:
            return []
        tracks = self._dig_tracks(json.loads(m.group(1)))
        # Keep all results: the original release is often listed after compilations
        return [self._parse_track(it) for it in tracks[:40] if isinstance(it, dict)]

    def _dig_tracks(self, nd: dict) -> list:
        pp = nd.get("props", {}).get("pageProps", {})
        for q in pp.get("dehydratedState", {}).get("queries", []):
            data = q.get("state", {}).get("data", {})
            results = data.get("results") or data.get("tracks") or data.get("data")
            if isinstance(results, list) and results:
                return results
        def _find(obj, depth=0):
            if depth > 8: return []
            if isinstance(obj, list) and obj:
                if isinstance(obj[0], dict) and ("track_name" in obj[0] or "bpm" in obj[0]):
                    return obj
                for item in obj:
                    r = _find(item, depth+1)
                    if r: return r
            elif isinstance(obj, dict):
                for v in obj.values():
                    r = _find(v, depth+1)
                    if r: return r
            return []
        return _find(pp)


def _fetch_image(url: str, source: str, timeout=10) -> bytes:
    h = {"User-Agent": _UA, "Accept": "image/*"}
    if source == "beatport":
        h["Referer"] = "https://www.beatport.com/"
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        return r.read()


class _ThumbLoader(QThread):
    loaded = pyqtSignal(str, bytes)    # uid, image data (QPixmap is GUI-thread only)
    def __init__(self, items):         # [(uid, url, source)]
        super().__init__(); self._items = items
    def run(self):
        def one(job):
            uid, url, src = job
            if self.isInterruptionRequested() or not url: return uid, None
            try:    return uid, _fetch_image(url, src, timeout=8)
            except Exception: return uid, None
        with ThreadPoolExecutor(max_workers=6) as ex:
            for uid, data in ex.map(one, self._items):
                if self.isInterruptionRequested(): return
                if data: self.loaded.emit(uid, data)


# ── small painting helpers ────────────────────────────────────────────────────

def _rounded(pix: QPixmap, size: int, radius: float) -> QPixmap:
    dpr = 2.0; px = int(size * dpr)
    src = pix.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                     Qt.TransformationMode.SmoothTransformation)
    out = QPixmap(px, px); out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath(); path.addRoundedRect(0, 0, px, px, radius*dpr, radius*dpr)
    p.setClipPath(path)
    p.drawPixmap((px-src.width())//2, (px-src.height())//2, src); p.end()
    out.setDevicePixelRatio(dpr)
    return out


def _lbl(text="", color=C_TEXT, size=12, weight=400, wrap=False) -> QLabel:
    l = QLabel(text)
    l.setStyleSheet(f"color:{color};font-size:{size}px;font-weight:{weight};"
                    f"background:transparent;border:none;")
    l.setWordWrap(wrap)
    return l


def _chip(text: str, fg: str, bg: str) -> QLabel:
    c = QLabel(text)
    c.setStyleSheet(f"color:{fg};background:{bg};border:none;border-radius:9px;"
                    f"font-size:10px;font-weight:600;padding:0 8px;")
    c.setFixedHeight(18)
    c.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return c


def _art_placeholder(label: QLabel, size: int, radius: int):
    label.setStyleSheet(f"background:{C_SURFACE2};border-radius:{radius}px;border:none;")
    label.clear()


# ── Result row ────────────────────────────────────────────────────────────────

class _ResultRow(QFrame):
    selected = pyqtSignal(str)
    activated = pyqtSignal(str)
    ART = 44

    def __init__(self, uid: str, r: dict, best: bool = False, matched: bool = True):
        super().__init__()
        self.uid = uid
        self._sel = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(64)
        self.setObjectName("resultRow")
        self._style()

        row = QHBoxLayout(self); row.setContentsMargins(10, 0, 12, 0); row.setSpacing(12)
        self.art = QLabel(); self.art.setFixedSize(self.ART, self.ART)
        _art_placeholder(self.art, self.ART, 6)
        row.addWidget(self.art)

        col = QVBoxLayout(); col.setSpacing(2); col.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout(); top.setSpacing(6)
        t = _lbl(r.get("title", ""), C_TEXT if matched else C_TEXT2, 13, 500)
        t.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top.addWidget(t, 1)
        col.addLayout(top)
        sub = " · ".join(x for x in (r.get("artist", ""), r.get("label", "") or r.get("album", "")) if x)
        s = _lbl(sub, C_TEXT2 if matched else C_TEXT3, 11)
        s.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        col.addWidget(s)
        row.addLayout(col, 1)

        meta = []
        if r.get("bpm"): meta.append(r["bpm"])
        if r.get("key"): meta.append(_key_display(r["key"]))
        if meta:
            row.addWidget(_lbl("  ·  ".join(meta), C_TEXT2, 11))
        if best:
            row.addWidget(_chip("Best match", "#c4b5fd", "rgba(139,92,246,0.18)"),
                          0, Qt.AlignmentFlag.AlignVCenter)
        src = SOURCES.get(r["source"], {"name": r["source"], "color": C_TEXT3})
        dot = _lbl("●", src["color"], 8); dot.setToolTip(src["name"])
        row.addWidget(dot)

    def _style(self):
        bg = C_SEL_BG if self._sel else "transparent"
        border = "rgba(139,92,246,0.55)" if self._sel else "transparent"
        self.setStyleSheet(
            f"QFrame#resultRow{{background:{bg};border:1px solid {border};border-radius:10px;}}"
            f"QFrame#resultRow:hover{{background:{C_SEL_BG if self._sel else C_SURFACE2};}}")

    def set_thumb(self, pix: QPixmap):
        self.art.setStyleSheet("background:transparent;border:none;")
        self.art.setPixmap(_rounded(pix, self.ART, 6))

    def mark(self, sel: bool):
        self._sel = sel; self._style()

    def mousePressEvent(self, _): self.selected.emit(self.uid)
    def mouseDoubleClickEvent(self, _): self.activated.emit(self.uid)


_CHECK_PNG = None

def _check_png() -> str:
    """Render a white check mark once and return its path (for the QSS image)."""
    global _CHECK_PNG
    if _CHECK_PNG is None:
        _CHECK_PNG = ""
        try:
            import os, tempfile, qtawesome as qta
            path = os.path.join(tempfile.gettempdir(), "tracktag_check.png")
            qta.icon("fa5s.check", color="#ffffff").pixmap(20, 20).save(path)
            _CHECK_PNG = path
        except Exception:
            pass
    return _CHECK_PNG


def _key_display(k: str) -> str:
    try:
        from .main_window import normalize_key
        return normalize_key(k)
    except Exception:
        return k


# ── Main dialog ───────────────────────────────────────────────────────────────

_FIELDS = [("cover", "Cover"), ("artist", "Artist"), ("title", "Title"),
           ("album", "Album"), ("genre", "Genre"), ("label", "Label"),
           ("year", "Year"), ("bpm", "BPM"), ("key", "Key")]


class MetaSearchDialog(QDialog):
    result_selected = pyqtSignal(dict)

    def __init__(self, artist="", title="", album="", preset="all",
                 current: Optional[dict] = None, duration: float = 0.0,
                 has_cover: bool = False, parent=None):
        super().__init__(parent)
        self._preset = preset          # 'all' | 'cover_only' | 'tags_only'
        self._current = current or {"artist": artist, "title": title, "album": album}
        self._duration = duration or 0.0
        self._has_cover = has_cover
        self.setWindowTitle("Find Cover" if preset == "cover_only" else "Search Tags")
        self.setMinimumSize(920, 640)
        self.resize(1000, 680)
        self._results: dict[str, dict] = {}
        self._rows:    dict[str, _ResultRow] = {}
        self._pix:     dict[str, QPixmap] = {}
        self._sel_uid: Optional[str] = None
        self._pending  = 0
        self._workers: list = []
        self._loader = None
        self._setup_ui(f"{artist} {title}".strip() or album)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def _wait_for_workers(self):
        workers = list(self._workers) + ([self._loader] if self._loader else [])
        for w in workers:
            if w.isRunning(): w.requestInterruption()
        for w in workers:
            if w.isRunning(): w.wait()

    def accept(self):
        self._wait_for_workers(); super().accept()

    def reject(self):
        self._wait_for_workers(); super().reject()

    def closeEvent(self, event):
        self._wait_for_workers(); super().closeEvent(event)

    # ── layout ────────────────────────────────────────────────────────────────

    def _setup_ui(self, query: str):
        self.setStyleSheet(f"QDialog{{background:{C_BG};}}")
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        # Search bar
        top = QFrame(); top.setFixedHeight(64)
        top.setStyleSheet(f"QFrame{{background:{C_BG};border:none;border-bottom:1px solid {C_BORDER};}}")
        tl = QHBoxLayout(top); tl.setContentsMargins(20, 0, 20, 0); tl.setSpacing(8)
        self.q_edit = QLineEdit(query)
        self.q_edit.setFixedHeight(36)
        self.q_edit.setPlaceholderText("Artist and title…")
        self.q_edit.setClearButtonEnabled(True)
        try:
            import qtawesome as qta
            self.q_edit.addAction(qta.icon("fa5s.search", color=C_TEXT3),
                                  QLineEdit.ActionPosition.LeadingPosition)
        except Exception:
            pass
        self.q_edit.setStyleSheet(f"""
            QLineEdit{{background:{C_SURFACE2};color:{C_TEXT};border:1px solid {C_BORDER};
                       border-radius:9px;padding:0 8px;font-size:13px;}}
            QLineEdit:focus{{border-color:{C_PRIMARY};background:{C_BG};}}""")
        self.q_edit.returnPressed.connect(self._search)
        tl.addWidget(self.q_edit, 1)
        self.s_btn = QPushButton("Search"); self.s_btn.setFixedHeight(36)
        self.s_btn.setStyleSheet(_BTN_SECONDARY + "QPushButton{padding:0 18px;font-size:13px;}")
        self.s_btn.clicked.connect(self._search)
        tl.addWidget(self.s_btn)
        root.addWidget(top)

        body = QHBoxLayout(); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(0)

        # Left: results list
        left = QWidget(); left.setStyleSheet(f"background:{C_BG};")
        ll = QVBoxLayout(left); ll.setContentsMargins(14, 14, 8, 0); ll.setSpacing(8)
        self.status = _lbl("", C_TEXT3, 11)
        self.status.setContentsMargins(8, 0, 0, 0)
        ll.addWidget(self.status)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self._list_w = QWidget(); self._list_w.setStyleSheet("background:transparent;")
        self._list = QVBoxLayout(self._list_w)
        self._list.setContentsMargins(0, 0, 6, 12); self._list.setSpacing(2)
        self._list.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._list_w)
        ll.addWidget(scroll, 1)
        body.addWidget(left, 1)

        # Right: preview of the selected result
        right = QFrame(); right.setFixedWidth(380); right.setObjectName("preview")
        right.setStyleSheet(f"QFrame#preview{{background:{C_SURFACE};border:none;"
                            f"border-left:1px solid {C_BORDER};}}")
        rl = QVBoxLayout(right); rl.setContentsMargins(24, 20, 24, 12); rl.setSpacing(0)

        head = QHBoxLayout(); head.setSpacing(16)
        self.p_art = QLabel(); self.p_art.setFixedSize(112, 112)
        _art_placeholder(self.p_art, 112, 10)
        head.addWidget(self.p_art, 0, Qt.AlignmentFlag.AlignTop)
        hv = QVBoxLayout(); hv.setSpacing(4)
        self.p_badge = _chip("", "#c4b5fd", "rgba(139,92,246,0.18)")
        self.p_badge.hide()
        bl = QHBoxLayout(); bl.addWidget(self.p_badge); bl.addStretch()
        hv.addLayout(bl)
        self.p_title = _lbl("", C_TEXT, 15, 600, wrap=True)
        self.p_artist = _lbl("", C_TEXT2, 12, 400, wrap=True)
        self.p_src = _lbl("", C_TEXT3, 11)
        hv.addWidget(self.p_title); hv.addWidget(self.p_artist)
        hv.addStretch(); hv.addWidget(self.p_src)
        head.addLayout(hv, 1)
        rl.addLayout(head)
        rl.addSpacing(16)

        fh = QHBoxLayout()
        fh.addWidget(_lbl("APPLY", C_TEXT3, 10, 600))
        fh.addStretch()
        self.p_hint = _lbl("Only empty fields are ticked", C_TEXT3, 10)
        fh.addWidget(self.p_hint)
        rl.addLayout(fh)
        rl.addSpacing(6)

        self.chk: dict[str, QCheckBox] = {}
        self._new_lbl: dict[str, QLabel] = {}
        self._old_lbl: dict[str, QLabel] = {}
        self._field_rows: dict[str, QWidget] = {}
        chk_ss = f"""
            QCheckBox{{spacing:0;background:transparent;}}
            QCheckBox::indicator{{width:16px;height:16px;border-radius:5px;
                border:1px solid {C_BORDER2};background:{C_SURFACE2};}}
            QCheckBox::indicator:hover{{border-color:{C_PRIMARY};}}
            QCheckBox::indicator:checked{{background:{C_PRIMARY};border-color:{C_PRIMARY};
                image:url("{_check_png()}");}}
            QCheckBox::indicator:disabled{{background:transparent;border-color:{C_SURFACE2};}}"""
        for key, name in _FIELDS:
            w = QWidget(); w.setFixedHeight(36)
            w.setStyleSheet("background:transparent;")
            h = QHBoxLayout(w); h.setContentsMargins(0, 1, 0, 0); h.setSpacing(12)
            h.setAlignment(Qt.AlignmentFlag.AlignTop)
            c = QCheckBox(); c.setStyleSheet(chk_ss)
            c.setCursor(Qt.CursorShape.PointingHandCursor)
            self.chk[key] = c
            h.addWidget(c)
            h.addWidget(_lbl(name, C_TEXT2, 12), 0)
            h.itemAt(1).widget().setFixedWidth(52)
            v = QVBoxLayout(); v.setSpacing(0)
            nl = _lbl("", C_TEXT, 12, 500); nl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            ol = _lbl("", C_TEXT3, 10); ol.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            v.addWidget(nl); v.addWidget(ol)
            h.addLayout(v, 1)
            self._new_lbl[key] = nl; self._old_lbl[key] = ol
            self._field_rows[key] = w
            c.toggled.connect(self._update_apply_btn)
            rl.addWidget(w)
        rl.addStretch()
        body.addWidget(right)

        wrap = QWidget(); wrap.setLayout(body)
        root.addWidget(wrap, 1)

        # Bottom bar
        bottom = QFrame(); bottom.setFixedHeight(64)
        bottom.setStyleSheet(f"QFrame{{background:{C_SURFACE};border:none;border-top:1px solid {C_BORDER};}}")
        bl2 = QHBoxLayout(bottom); bl2.setContentsMargins(20, 0, 20, 0); bl2.setSpacing(8)
        self.foot = _lbl("Double-click a result to apply it", C_TEXT3, 11)
        bl2.addWidget(self.foot); bl2.addStretch()
        cb = QPushButton("Cancel"); cb.setFixedHeight(36)
        cb.setStyleSheet(_BTN_GHOST + "QPushButton{padding:0 16px;font-size:13px;}")
        cb.clicked.connect(self.reject); bl2.addWidget(cb)
        self.ok_btn = QPushButton("Apply"); self.ok_btn.setFixedHeight(36)
        self.ok_btn.setMinimumWidth(140)
        self.ok_btn.setStyleSheet(_BTN_PRIMARY)
        self.ok_btn.setEnabled(False); self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self._apply_selection)
        bl2.addWidget(self.ok_btn)
        root.addWidget(bottom)

        self._show_preview(None)
        if self.q_edit.text().strip():
            QTimer.singleShot(120, self._search)
        else:
            self.status.setText("Type artist and title, then press Enter.")

    # ── search ────────────────────────────────────────────────────────────────

    def _search(self):
        q = self.q_edit.text().strip()
        if not q or self._pending: return
        self._wait_for_workers()
        self._clear()
        self._query = q
        self.s_btn.setEnabled(False); self.ok_btn.setEnabled(False)
        self.status.setText("Searching Beatport and Apple Music…")
        self._workers = []
        self._pending = 2
        for Cls in (_BeatportWorker, _iTunesWorker):
            w = Cls(q); w.done.connect(self._on_results); w.start()
            self._workers.append(w)

    def _clear(self):
        while self._list.count():
            it = self._list.takeAt(0)
            if it.widget(): it.widget().deleteLater()
        self._results.clear(); self._rows.clear(); self._pix.clear()
        self._sel_uid = None
        self._show_preview(None)

    def _on_results(self, results: list):
        for r in results:
            self._results[f"{r['source']}_{len(self._results)}"] = r
        self._pending -= 1
        if self._pending == 0:
            self.s_btn.setEnabled(True)
            self._rebuild()

    # ── ranking ───────────────────────────────────────────────────────────────

    def _rank(self):
        return rank_results(self._results, self._query, self._duration)

    def _best_match(self, ranked: list) -> Optional[dict]:
        return best_match(self._results, ranked)

    def _rebuild(self):
        ranked = self._rank()
        if not ranked:
            self.status.setText("No results — try a shorter search (artist + title).")
            return
        best = self._best_match(ranked)
        order = []
        if best:
            self._results["__best__"] = best
            order.append("__best__")
        # Matches: one row per source + version (compilations collapse into the
        # original release); non-matches: the 10 closest
        seen = {(best["source"], best["artist"].lower(), best["title"].lower())} if best else set()
        matches = []
        for u in ranked:
            r = self._results[u]
            if not r["_match"]: continue
            k = (r["source"], r["artist"].lower(), r["title"].lower())
            if k in seen: continue
            seen.add(k); matches.append(u)
        others = [u for u in ranked if not self._results[u]["_match"]][:10]
        order += matches + others

        n_match = len(matches) + (1 if best else 0)
        self.status.setText(
            f"{n_match} matching result{'s' if n_match != 1 else ''}" if n_match
            else "No exact match — pick the right one manually")

        other_hdr_done = False
        jobs = []
        for uid in order:
            r = self._results[uid]
            if not r["_match"] and not other_hdr_done:
                other_hdr_done = True
                h = _lbl("OTHER RESULTS", C_TEXT3, 10, 600)
                h.setContentsMargins(10, 14, 0, 6)
                self._list.addWidget(h)
            row = _ResultRow(uid, r, best=(uid == "__best__"), matched=r["_match"])
            row.selected.connect(self._select)
            row.activated.connect(lambda u: (self._select(u), self._apply_selection()))
            self._rows[uid] = row
            self._list.addWidget(row)
            if r.get("thumb"):
                jobs.append((uid, r["thumb"], r["source"]))
        self._thumb_uids: dict[str, list] = {}
        for uid, url, _ in jobs:
            self._thumb_uids.setdefault(url, []).append(uid)
        jobs = [(url, url, src) for url, src in {u: s_ for _, u, s_ in jobs}.items()]

        self._select(order[0] if best else None)
        if jobs:
            self._loader = _ThumbLoader(jobs)
            self._loader.loaded.connect(
                lambda url, data: [self._set_thumb(u, data) for u in self._thumb_uids.get(url, [])])
            self._loader.start()

    def _set_thumb(self, uid: str, data: bytes):
        pix = QPixmap(); pix.loadFromData(data)
        if pix.isNull(): return
        self._pix[uid] = pix
        if uid in self._rows:
            self._rows[uid].set_thumb(pix)
        if uid == self._sel_uid:
            self.p_art.setStyleSheet("background:transparent;border:none;")
            self.p_art.setPixmap(_rounded(pix, 112, 10))

    # ── preview ───────────────────────────────────────────────────────────────

    def _select(self, uid: Optional[str]):
        if self._sel_uid in self._rows:
            self._rows[self._sel_uid].mark(False)
        self._sel_uid = uid
        if uid in self._rows:
            self._rows[uid].mark(True)
        self._show_preview(self._results.get(uid) if uid else None)

    def _show_preview(self, r: Optional[dict]):
        if not r:
            self.p_title.setText("No result selected")
            self.p_artist.setText("Pick a result on the left")
            self.p_src.setText(""); self.p_badge.hide()
            _art_placeholder(self.p_art, 112, 10)
            for key in self.chk:
                self._fill_field(key, "", "")
            self._update_apply_btn()
            return

        self.p_title.setText(r.get("title", ""))
        self.p_artist.setText(r.get("artist", ""))
        srcs = r.get("_sources") or [r["source"]]
        self.p_src.setText("From " + " + ".join(SOURCES[s]["name"] for s in srcs))
        if r.get("_best"):
            self.p_badge.setText("Best match"); self.p_badge.show()
        elif not r.get("_match"):
            self.p_badge.setText("Uncertain match"); self.p_badge.show()
        else:
            self.p_badge.hide()
        pix = self._pix.get(self._sel_uid)
        if pix:
            self.p_art.setStyleSheet("background:transparent;border:none;")
            self.p_art.setPixmap(_rounded(pix, 112, 10))
        else:
            _art_placeholder(self.p_art, 112, 10)

        for key, _ in _FIELDS:
            if key == "cover":
                new = "New artwork" if r.get("cover_url") else ""
                old = "Replaces current artwork" if self._has_cover else "No artwork yet"
                self._fill_field(key, new, old, empty_now=not self._has_cover)
            else:
                val = r.get(key, "") or ""
                if key == "key" and val: val = _key_display(val)
                cur = (self._current.get(key) or "").strip()
                self._fill_field(key, val, cur, empty_now=not cur)
        self._update_apply_btn()

    def _fill_field(self, key: str, new: str, cur: str, empty_now: bool = True):
        c = self.chk[key]; nl = self._new_lbl[key]; ol = self._old_lbl[key]
        c.blockSignals(True)
        if not new:
            nl.setText("—"); nl.setStyleSheet(f"color:{C_TEXT3};font-size:12px;background:transparent;")
            ol.setText(""); ol.hide()
            c.setChecked(False); c.setEnabled(False)
        else:
            nl.setText(new)
            nl.setStyleSheet(f"color:{C_TEXT};font-size:12px;font-weight:500;background:transparent;")
            same = key != "cover" and cur.lower() == new.lower()
            c.setEnabled(not same)
            if same:
                ol.setText("Unchanged"); ol.show()
                c.setChecked(False)
            else:
                if key == "cover":
                    ol.setText(cur)
                else:
                    ol.setText(f"Currently: {cur}" if cur else "Currently empty")
                ol.show()
                if self._preset == "cover_only":
                    c.setChecked(key == "cover")
                elif self._preset == "tags_only" and key == "cover":
                    c.setChecked(False)
                else:
                    c.setChecked(empty_now)
        c.blockSignals(False)

    def _update_apply_btn(self, *_):
        n = sum(1 for c in self.chk.values() if c.isChecked() and c.isEnabled())
        self.ok_btn.setEnabled(bool(self._sel_uid) and n > 0)
        self.ok_btn.setText(f"Apply {n} field{'s' if n != 1 else ''}" if n else "Apply")

    # ── apply ─────────────────────────────────────────────────────────────────

    def _apply_selection(self):
        if not self._sel_uid: return
        r = self._results[self._sel_uid]
        payload: dict = {}
        if self.chk["cover"].isChecked() and r.get("cover_url"):
            self.foot.setText("Downloading artwork…"); self.ok_btn.setEnabled(False)
            self.repaint()
            try:
                payload["cover_data"] = _fetch_image(r["cover_url"], r["source"], timeout=15)
                payload["cover_mime"] = "image/jpeg"
            except Exception as e:
                self.foot.setText(f"Artwork download failed: {e}")
                self._update_apply_btn()
                return
        for key, _ in _FIELDS:
            if key != "cover" and self.chk[key].isChecked() and r.get(key):
                payload[key] = r[key]
        if payload:
            self.result_selected.emit(payload)
            self.accept()

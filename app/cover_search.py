"""
Smart multi-source search: iTunes + Discogs + Beatport run in parallel.
A "Smart Match" banner auto-combines the best cover + metadata in one click.
"""
import json, re, time, urllib.request, urllib.parse
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QWidget, QFrame,
    QCheckBox, QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QPixmap

# ── constants ─────────────────────────────────────────────────────────────────

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_UA_DG = "TrackTag/1.0 +tracktag@local"

SOURCES = {
    "itunes":     {"name": "Apple Music", "color": "#fc3c44", "priority": 0},
    "beatport":   {"name": "Beatport",    "color": "#01ff95", "priority": 1},
    "soundcloud": {"name": "SoundCloud",  "color": "#ff5500", "priority": 2},
    "discogs":    {"name": "Discogs",     "color": "#5577ff", "priority": 3},
}


# ── Workers ───────────────────────────────────────────────────────────────────

class _iTunesWorker(QThread):
    done = pyqtSignal(list)
    def __init__(self, q): super().__init__(); self.q = q
    def run(self):
        try:
            enc = urllib.parse.quote(self.q)
            req = urllib.request.Request(
                f"https://itunes.apple.com/search?term={enc}&entity=song&limit=6",
                headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            out, seen = [], set()
            for it in data.get("results", []):
                t = it.get("artworkUrl100", "")
                if not t or t in seen: continue
                seen.add(t)
                out.append({
                    "source": "itunes",
                    "artist": it.get("artistName", ""),
                    "title":  it.get("trackName", ""),
                    "album":  it.get("collectionName", ""),
                    "genre":  it.get("primaryGenreName", ""),
                    "label":  "",
                    "year":   it.get("releaseDate", "")[:4],
                    "bpm": "", "key": "",
                    "thumb":     t,
                    "cover_url": t.replace("100x100bb","600x600bb").replace("100x100","600x600"),
                })
            self.done.emit(out)
        except Exception as e:
            print(f"iTunes: {e}"); self.done.emit([])


class _DiscogsWorker(QThread):
    done = pyqtSignal(list)
    def __init__(self, q): super().__init__(); self.q = q
    def run(self):
        try:
            enc = urllib.parse.quote(self.q)
            req = urllib.request.Request(
                f"https://api.discogs.com/database/search?q={enc}&type=release&per_page=8",
                headers={"User-Agent": _UA_DG, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=12) as r:
                data = json.loads(r.read())
            out = []
            for it in data.get("results", []):
                raw = it.get("title", "")
                parts = raw.split(" - ", 1)
                artist = parts[0].strip() if len(parts) > 1 else ""
                title  = parts[1].strip() if len(parts) > 1 else raw
                genre  = (it.get("style") or it.get("genre") or [""])[0]
                lbs    = it.get("label", [])
                label  = lbs[0].split("(")[0].strip() if lbs else ""
                thumb  = it.get("thumb", "")
                cover  = it.get("cover_image", thumb)
                for bad in ("spacer", "images/vinyl", "images/default"):
                    if bad in thumb:  thumb = ""
                    if bad in cover:  cover = ""
                out.append({
                    "source": "discogs",
                    "artist": artist, "title": title, "album": title,
                    "genre": genre,   "label": label,
                    "year":  str(it.get("year", "")),
                    "bpm": "", "key": "",
                    "thumb": thumb, "cover_url": cover or thumb,
                })
            self.done.emit(out)
        except Exception as e:
            print(f"Discogs: {e}"); self.done.emit([])


class _BeatportWorker(QThread):
    done = pyqtSignal(list)
    def __init__(self, q): super().__init__(); self.q = q
    def run(self):
        results = self._api() or self._scrape()
        self.done.emit(results)

    def _parse_track(self, it):
        """Parse a Beatport track dict — handles both API v4 and scraped __NEXT_DATA__ formats."""
        release = it.get("release") or {}

        # ── Title ────────────────────────────────────────────────────────────
        name = it.get("track_name") or it.get("name") or it.get("title") or ""
        mix  = it.get("mix_name") or ""
        title = f"{name} ({mix})" if mix and mix.lower() not in ("original mix", "") else name

        # ── Artists ───────────────────────────────────────────────────────────
        artists = it.get("artists") or it.get("artist") or []
        if isinstance(artists, list):
            artist_str = ", ".join(
                (a.get("artist_name") or a.get("name") or "")
                if isinstance(a, dict) else str(a)
                for a in artists)
        else:
            artist_str = str(artists)

        # ── Cover ─────────────────────────────────────────────────────────────
        # Helper: extract (thumb, hq) from an image dict — returns ("","") for waveforms
        def _extract_img(obj) -> tuple:
            if not isinstance(obj, dict): return "", ""
            # Prefer dynamic_uri with size placeholder
            dyn = obj.get("dynamic_uri") or obj.get("dynamicUri") or ""
            if dyn and "{w}x{h}" in dyn:
                th = dyn.replace("{w}x{h}", "150x150")
                hq = dyn.replace("{w}x{h}", "500x500")
                return th, hq
            # Fall back to static uri/url
            uri = obj.get("uri") or obj.get("url") or obj.get("src") or ""
            if not uri: return "", ""
            # Reject waveform images (landscape, width >> height in URL)
            m = re.search(r'/(\d+)x(\d+)/', uri)
            if m and int(m.group(1)) > int(m.group(2)) * 1.5: return "", ""
            hq = re.sub(r'/\d+x\d+/', '/500x500/', uri)
            th = re.sub(r'/\d+x\d+/', '/150x150/', uri)
            return th, hq

        # Helper: extract from a plain string URL (handles /WxH/ and /image_size/WxH/ patterns)
        def _extract_uri(uri: str) -> tuple:
            if not uri: return "", ""
            m = re.search(r'/(\d+)x(\d+)(?:/|$)', uri)
            if m and int(m.group(1)) > int(m.group(2)) * 1.5: return "", ""
            hq = re.sub(r'/\d+x\d+(?=/|$)', '/500x500', uri)
            th = re.sub(r'/\d+x\d+(?=/|$)', '/150x150', uri)
            return th, hq

        thu, cov = "", ""

        # Priority 1: release_image_dynamic_uri / release_image_uri
        # (Beatport scrape format — the actual square cover art)
        rel_dyn = release.get("release_image_dynamic_uri") or ""
        rel_uri = release.get("release_image_uri") or ""
        if rel_dyn and "{w}x{h}" in rel_dyn:
            thu = rel_dyn.replace("{w}x{h}", "150x150")
            cov = rel_dyn.replace("{w}x{h}", "500x500")
        elif rel_uri:
            thu, cov = _extract_uri(rel_uri)

        # Priority 2: release.image dict (API v4 format)
        if not thu:
            thu, cov = _extract_img(release.get("image") or {})

        # Priority 3: other release-level plain image URIs
        if not thu:
            for field in ("image_uri", "imageUri", "cover_uri", "art_uri"):
                thu, cov = _extract_uri(release.get(field) or "")
                if thu: break

        # Priority 4: release.images list
        if not thu:
            for img in (release.get("images") or []):
                thu, cov = _extract_img(img)
                if thu: break

        # Priority 5: track-level image dict
        if not thu:
            thu, cov = _extract_img(it.get("image") or {})

        # Priority 6: track_image_dynamic_uri — only if NOT a waveform
        if not thu:
            dyn  = it.get("track_image_dynamic_uri") or ""
            orig = it.get("track_image_uri") or ""
            m = re.search(r'/(\d+)x(\d+)/', orig or dyn)
            is_waveform = bool(m and int(m.group(1)) > int(m.group(2)) * 1.5)
            if not is_waveform:
                if dyn and "{w}x{h}" in dyn:
                    thu = dyn.replace("{w}x{h}", "150x150")
                    cov = dyn.replace("{w}x{h}", "500x500")
                elif orig:
                    thu, cov = _extract_uri(orig)

        # Priority 7: any geo-media URL anywhere in the release dict
        if not thu and isinstance(release, dict):
            for v in release.values():
                if isinstance(v, str) and "geo-media" in v and "image_size" in v:
                    thu, cov = _extract_uri(v)
                    if thu: break

        # ── Genre ─────────────────────────────────────────────────────────────
        gens = it.get("genre") or it.get("genres") or []
        genre = ""
        if gens:
            g0 = gens[0]
            genre = (g0.get("genre_name") or g0.get("name") or "") if isinstance(g0, dict) else str(g0)

        # ── Label ─────────────────────────────────────────────────────────────
        lb = it.get("label") or release.get("label") or {}
        label = (lb.get("label_name") or lb.get("name") or "") if isinstance(lb, dict) else str(lb or "")

        # ── Year ─────────────────────────────────────────────────────────────
        year = (it.get("publish_date") or it.get("release_date") or it.get("new_release_date") or
                release.get("date") or release.get("publish_date") or "")[:4]

        # ── BPM ──────────────────────────────────────────────────────────────
        bpm = str(it.get("bpm") or "")

        # ── Key ───────────────────────────────────────────────────────────────
        key = it.get("key_name") or ""
        if not key:
            ki = it.get("key") or {}
            if isinstance(ki, dict):
                num   = ki.get("camelot_number", "")
                ctype = ki.get("chord_type") or {}
                cname = ctype.get("name", "") if isinstance(ctype, dict) else str(ctype)
                key   = f"{num}{cname}" if num else cname
            elif isinstance(ki, str):
                key = ki

        print(f"Beatport cover: thumb={thu!r:.60} cov={cov!r:.60}")
        return {
            "source":    "beatport",
            "artist":    artist_str,
            "title":     title,
            "album":     release.get("name", "") or it.get("album", ""),
            "genre":     genre,
            "label":     label,
            "year":      year,
            "bpm":       bpm,
            "key":       key,
            "thumb":     thu,
            "cover_url": cov,
        }

    def _api(self):
        try:
            enc = urllib.parse.quote(self.q)
            req = urllib.request.Request(
                f"https://api.beatport.com/v4/catalog/tracks/?q={enc}&per_page=6",
                headers={"User-Agent": _UA, "Accept": "application/json",
                         "Referer": "https://www.beatport.com/"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            return [self._parse_track(it) for it in data.get("results", [])]
        except: return None

    def _scrape(self):
        try:
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
                print("Beatport: no __NEXT_DATA__ found"); return []
            nd = json.loads(m.group(1))
            tracks = self._dig_tracks(nd)
            print(f"Beatport: found {len(tracks)} tracks via scrape")
            if tracks:
                t0 = tracks[0]
                print(f"Beatport track[0] keys: {list(t0.keys())}")
                rel = t0.get("release") or {}
                print(f"Beatport release keys: {list(rel.keys()) if isinstance(rel, dict) else rel}")
                print(f"Beatport release.image: {rel.get('image') if isinstance(rel, dict) else '?'}")
                print(f"Beatport track.image: {t0.get('image')}")
                print(f"Beatport track_image_uri: {t0.get('track_image_uri','')[:80]}")
                print(f"Beatport track_image_dynamic_uri: {t0.get('track_image_dynamic_uri','')[:80]}")
            return [self._parse_track(it) for it in tracks[:6] if isinstance(it, dict)]
        except Exception as e:
            print(f"Beatport scrape: {e}"); return []

    def _dig_tracks(self, nd: dict) -> list:
        """Try every known __NEXT_DATA__ path to find a tracks list."""
        pp = nd.get("props", {}).get("pageProps", {})

        # Path 1: dehydratedState queries
        for q in pp.get("dehydratedState", {}).get("queries", []):
            data = q.get("state", {}).get("data", {})
            results = data.get("results") or data.get("tracks") or data.get("data")
            if results and isinstance(results, list) and results:
                return results

        # Path 2: direct pageProps.tracks
        for key in ("tracks", "search_tracks", "searchTracks"):
            v = pp.get(key)
            if isinstance(v, dict):
                r = v.get("data") or v.get("results") or []
                if r: return r
            elif isinstance(v, list) and v:
                return v

        # Path 3: any list of dicts with track-identifying keys anywhere in pageProps
        def _find(obj, depth=0):
            if depth > 8: return []
            if isinstance(obj, list) and obj:
                d0 = obj[0]
                if isinstance(d0, dict) and (
                    "track_name" in d0 or "label" in d0 or "bpm" in d0
                ):
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


class _SoundCloudWorker(QThread):
    done = pyqtSignal(list)
    def __init__(self, q): super().__init__(); self.q = q

    def run(self):
        try:
            results = self._fetch()
            self.done.emit(results)
        except Exception as e:
            print(f"SoundCloud: {e}"); self.done.emit([])

    def _get_client_id(self, html: str) -> str:
        """Extract client_id from SoundCloud page or its JS bundles."""
        m = re.search(r'client_id["\s:=]+(["\'])([a-zA-Z0-9]{32})\1', html)
        if m: return m.group(2)
        js_urls = re.findall(r'https://a-v2\.sndcdn\.com/assets/[^"\']+\.js', html)
        for url in js_urls[:4]:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": _UA})
                with urllib.request.urlopen(req, timeout=8) as r:
                    js = r.read().decode("utf-8", errors="replace")
                m2 = re.search(r'client_id:"([a-zA-Z0-9]{32})"', js)
                if m2: return m2.group(1)
            except: pass
        return ""

    def _artwork(self, url: str, size_thumb="t200x200", size_hq="t500x500"):
        if not url: return "", ""
        thumb = re.sub(r'-(large|t\d+x\d+)\b', f'-{size_thumb}', url)
        hq    = re.sub(r'-(large|t\d+x\d+)\b', f'-{size_hq}',    url)
        return thumb, hq

    def _parse_track(self, it: dict) -> dict:
        user   = it.get("user") or {}
        raw_aw = it.get("artwork_url") or user.get("avatar_url") or ""
        thumb, hq = self._artwork(raw_aw)
        year = str(it.get("created_at") or "")[:4]
        return {
            "source":    "soundcloud",
            "artist":    user.get("username") or user.get("full_name") or "",
            "title":     it.get("title") or "",
            "album":     "",
            "genre":     it.get("genre") or "",
            "label":     "",
            "year":      year,
            "bpm":       str(it.get("bpm") or ""),
            "key":       "",
            "thumb":     thumb,
            "cover_url": hq,
        }

    def _fetch(self) -> list:
        enc  = urllib.parse.quote(self.q)
        req0 = urllib.request.Request(
            f"https://soundcloud.com/search?q={enc}",
            headers={"User-Agent": _UA, "Accept": "text/html",
                     "Accept-Language": "en-US,en;q=0.9"})
        with urllib.request.urlopen(req0, timeout=12) as r:
            html = r.read().decode("utf-8", errors="replace")

        client_id = self._get_client_id(html)
        if client_id:
            try:
                api_url = (f"https://api-v2.soundcloud.com/search/tracks"
                           f"?q={enc}&client_id={client_id}&limit=6&offset=0")
                req1 = urllib.request.Request(
                    api_url, headers={"User-Agent": _UA,
                                      "Referer": "https://soundcloud.com/"})
                with urllib.request.urlopen(req1, timeout=10) as r2:
                    data = json.loads(r2.read())
                tracks = data.get("collection", [])
                if tracks:
                    print(f"SoundCloud API: {len(tracks)} tracks")
                    return [self._parse_track(t) for t in tracks[:6]]
            except Exception as e:
                print(f"SoundCloud API call failed: {e}")

        # Fallback: parse __sc_hydration__ from the HTML
        return self._parse_hydration(html)

    def _parse_hydration(self, html: str) -> list:
        m = re.search(r'window\.__sc_hydration__\s*=\s*(\[.+?\]);', html, re.DOTALL)
        if not m: return []
        try:
            items = json.loads(m.group(1))
            for item in items:
                if item.get("hydratable") == "sounds":
                    tracks = item.get("data", {}).get("collection", [])
                    return [self._parse_track(t) for t in tracks[:6]]
                if item.get("hydratable") == "sound":
                    return [self._parse_track(item.get("data", {}))]
        except Exception as e:
            print(f"SoundCloud hydration parse: {e}")
        return []


class _ThumbLoader(QThread):
    loaded = pyqtSignal(str, QPixmap)  # uid, pix
    def __init__(self, items):         # [(uid, url, source)]
        super().__init__(); self._items = items
    def run(self):
        for uid, url, src in self._items:
            if not url: continue
            try:
                h = {"User-Agent": _UA, "Accept": "image/*"}
                if src == "discogs":     h["Referer"] = "https://www.discogs.com/"
                if src == "beatport":    h["Referer"] = "https://www.beatport.com/"
                if src == "soundcloud":  h["Referer"] = "https://soundcloud.com/"
                req = urllib.request.Request(url, headers=h)
                with urllib.request.urlopen(req, timeout=8) as r:
                    d = r.read()
                pix = QPixmap(); pix.loadFromData(d)
                if not pix.isNull(): self.loaded.emit(uid, pix)
            except: pass
            time.sleep(0.07)


# ── Smart Match Banner ────────────────────────────────────────────────────────

class SmartBanner(QFrame):
    """Full-width 'best-of-all-sources' card shown above the grid."""
    quick_apply = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            SmartBanner {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #1c2d1c, stop:1 #1c1c2e);
                border: 1px solid #2a6a2a;
                border-radius: 10px;
            }
        """)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(16)

        # Cover thumbnail
        self.cover_lbl = QLabel()
        self.cover_lbl.setFixedSize(90, 90)
        self.cover_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_lbl.setStyleSheet(
            "background: #111; border-radius: 6px; color: #555; font-size: 20px;")
        self.cover_lbl.setText("⏳")
        row.addWidget(self.cover_lbl)

        # Metadata
        meta_col = QVBoxLayout()
        meta_col.setSpacing(2)

        badge = QLabel("⭐  Smart Match  —  beste Kombination aller Quellen")
        badge.setStyleSheet(
            "color: #4cde4c; font-size: 10px; font-weight: 700; letter-spacing: 0.5px;")
        meta_col.addWidget(badge)

        self.at_lbl = QLabel()
        self.at_lbl.setStyleSheet("color: #ffffff; font-size: 12px; font-weight: 700;")
        self.at_lbl.setWordWrap(True)
        self.at_lbl.hide()
        meta_col.addWidget(self.at_lbl)

        self.meta_lbl = QLabel("Suche läuft…")
        self.meta_lbl.setStyleSheet("color: #ebebf5; font-size: 12px;")
        self.meta_lbl.setWordWrap(True)
        meta_col.addWidget(self.meta_lbl)

        self.src_lbl = QLabel()
        self.src_lbl.setStyleSheet("color: #636366; font-size: 10px;")
        meta_col.addWidget(self.src_lbl)
        row.addLayout(meta_col, 1)

        # Apply button
        self.apply_btn = QPushButton("✓  Alles übernehmen")
        self.apply_btn.setFixedSize(160, 38)
        self.apply_btn.setEnabled(False)
        self.apply_btn.setStyleSheet("""
            QPushButton {
                background: #1e8c1e; color: white; border: none;
                border-radius: 8px; font-weight: 700; font-size: 12px;
            }
            QPushButton:hover   { background: #28a828; }
            QPushButton:pressed { background: #146014; }
            QPushButton:disabled { background: #2c2c2e; color: #555; }
        """)
        self.apply_btn.clicked.connect(self.quick_apply)
        row.addWidget(self.apply_btn)

    def update_meta(self, sm: dict):
        # Interpret – Titel
        artist = sm.get("artist","").strip()
        title  = sm.get("title","").strip()
        if artist or title:
            self.at_lbl.setText(f"{artist} – {title}" if artist and title
                                else artist or title)
            self.at_lbl.show()
        else:
            self.at_lbl.hide()

        parts = []
        if sm.get("genre"): parts.append(f"🎵  {sm['genre']}")
        if sm.get("label"): parts.append(f"🏷  {sm['label']}")
        if sm.get("year"):  parts.append(f"📅  {sm['year']}")
        if sm.get("bpm"):   parts.append(f"♩  {sm['bpm']} BPM")
        if sm.get("key"):   parts.append(f"🎹  {sm['key']}")
        self.meta_lbl.setText("   ·   ".join(parts) if parts else "(keine Metadaten gefunden)")

        srcs = []
        if sm.get("cover_src"):  srcs.append(f"Cover: {SOURCES[sm['cover_src']]['name']}")
        if sm.get("genre_src"):  srcs.append(f"Genre: {SOURCES[sm['genre_src']]['name']}")
        if sm.get("label_src"):  srcs.append(f"Label: {SOURCES[sm['label_src']]['name']}")
        self.src_lbl.setText("   ".join(srcs))
        self.apply_btn.setEnabled(bool(parts) or bool(sm.get("cover_url")))

    def set_thumb(self, pix: QPixmap):
        scaled = pix.scaled(90, 90,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.cover_lbl.setPixmap(scaled)


# ── Result card ───────────────────────────────────────────────────────────────

class _Card(QFrame):
    selected = pyqtSignal(str)

    def __init__(self, uid: str, r: dict, show_cover: bool = True):
        super().__init__()
        self.uid = uid
        self.setFixedWidth(175)           # height is dynamic
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sel = False
        self._show_cover = show_cover
        self._style(False)

        vl = QVBoxLayout(self)
        vl.setContentsMargins(6, 6, 6, 6)
        vl.setSpacing(4)

        # Cover thumbnail — only shown in cover/all mode, not in tags_only
        self.thumb = None
        if show_cover:
            self.thumb = QLabel()
            self.thumb.setFixedSize(163, 120)
            self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.thumb.setWordWrap(True)
            has_img = bool(r.get("thumb") or r.get("cover_url"))
            sm_col = SOURCES.get(r["source"], {"color": "#555"})["color"]
            if has_img:
                self.thumb.setStyleSheet("background:#111;border-radius:4px;color:#555;font-size:18px;")
                self.thumb.setText("⏳")
            else:
                t = (r.get("title") or r.get("album") or "")[:38]
                self.thumb.setStyleSheet(
                    f"background:{sm_col}14;border-radius:4px;"
                    f"color:{sm_col};font-size:10px;padding:6px;")
                self.thumb.setText(f"🎵\n{t}\n\n(Kein Cover)")
            vl.addWidget(self.thumb)

        # Source badge
        sm = SOURCES.get(r["source"], {"name": r["source"], "color": "#555"})
        b = QLabel(sm["name"])
        b.setFixedHeight(16)
        b.setStyleSheet(
            f"background:{sm['color']}30;color:{sm['color']};"
            "border-radius:3px;font-size:8px;font-weight:700;padding:0 5px;")
        vl.addWidget(b)

        # Artist – Title  (white, prominent)
        artist = r.get("artist", "").strip()
        title  = r.get("title", "").strip()
        if artist or title:
            text = f"{artist} – {title}" if (artist and title) else (artist or title)
            at_lbl = QLabel(text)
            at_lbl.setStyleSheet("color:#ffffff;font-size:9px;font-weight:700;")
            at_lbl.setWordWrap(True)
            vl.addWidget(at_lbl)

        # Metadata rows
        for icon, key, color in [("🎵", "genre", "#0a84ff"),
                                   ("🏷", "label", "#d4b0ff"),
                                   ("📅", "year",  "#8e8e93"),
                                   ("♩", "bpm",   "#ff9f0a"),
                                   ("🎹", "key",   "#30d158")]:
            val = r.get(key, "")
            if not val: continue
            suffix = " BPM" if key == "bpm" else ""
            lbl = QLabel(f"{icon} {val}{suffix}")
            lbl.setStyleSheet(f"color:{color};font-size:9px;font-weight:600;")
            lbl.setWordWrap(True)
            vl.addWidget(lbl)

        vl.addStretch(1)

    def _style(self, sel):
        if sel:
            self.setStyleSheet("QFrame{border-radius:9px;background:#1c3a5e;border:2px solid #0a84ff;}")
        else:
            self.setStyleSheet("QFrame{border-radius:9px;background:#2c2c2e;border:none;}")

    def set_thumb(self, pix: QPixmap):
        if self.thumb is None: return
        s = pix.scaled(160, 126, Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
        self.thumb.setPixmap(s)

    def mark(self, sel: bool):
        self._sel = sel; self._style(sel)

    def mousePressEvent(self, _): self.selected.emit(self.uid)


# ── Main dialog ───────────────────────────────────────────────────────────────

class MetaSearchDialog(QDialog):
    result_selected = pyqtSignal(dict)

    def __init__(self, artist="", title="", album="", cover_only=False, preset="all", parent=None):
        super().__init__(parent)
        self._cover_only = cover_only
        self._preset = preset   # 'all' | 'cover_only' | 'tags_only'
        self.setWindowTitle("Im Internet suchen — Cover & Metadaten")
        self.setMinimumSize(800, 660)
        self.resize(880, 720)
        self._results: dict[str, dict] = {}
        self._cards:   dict[str, _Card] = {}
        self._sel_uid: Optional[str] = None
        self._pending  = 0
        self._smart: dict = {}
        self._setup_ui(artist, title, album)

    # ── layout ────────────────────────────────────────────────────────────────

    def _setup_ui(self, artist, title, album):
        self.setStyleSheet("QDialog { background: #141416; }")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top bar (search + source chips) ──────────────────────────────
        top_bar = QWidget()
        top_bar.setStyleSheet("background: #1c1c1e; border-bottom: 1px solid #2c2c2e;")
        top_bar.setFixedHeight(72)
        tb = QVBoxLayout(top_bar)
        tb.setContentsMargins(20, 10, 20, 10)
        tb.setSpacing(6)

        # Search row
        sr = QHBoxLayout(); sr.setSpacing(8)
        self.q_edit = QLineEdit()
        self.q_edit.setFixedHeight(38)
        self.q_edit.setPlaceholderText("🔍   Interpret + Titel suchen…")
        self.q_edit.setText(f"{artist} {title}".strip() or album)
        self.q_edit.setStyleSheet("""
            QLineEdit {
                background: #2c2c2e; border: 1.5px solid #3a3a3c;
                border-radius: 10px; padding: 0 14px;
                color: #ebebf5; font-size: 13px;
                selection-background-color: #0a84ff;
            }
            QLineEdit:focus { border-color: #0a84ff; background: #1c2d40; }
        """)
        self.q_edit.returnPressed.connect(self._search)
        sr.addWidget(self.q_edit, 1)

        self.s_btn = QPushButton("Suchen")
        self.s_btn.setFixedSize(88, 38)
        self.s_btn.setStyleSheet("""
            QPushButton {
                background: #0a84ff; color: white; border: none;
                border-radius: 10px; font-weight: 700; font-size: 13px;
            }
            QPushButton:hover   { background: #2a94ff; }
            QPushButton:pressed { background: #006ee0; }
            QPushButton:disabled { background: #2c2c2e; color: #555; }
        """)
        self.s_btn.clicked.connect(self._search)
        sr.addWidget(self.s_btn)
        tb.addLayout(sr)
        root.addWidget(top_bar)

        # ── Content area ──────────────────────────────────────────────────
        inner = QWidget()
        inner.setStyleSheet("background: #141416;")
        i_layout = QVBoxLayout(inner)
        i_layout.setContentsMargins(20, 12, 20, 0)
        i_layout.setSpacing(8)
        root.addWidget(inner, 1)

        # Source chips
        chips_row = QHBoxLayout(); chips_row.setSpacing(6)
        ql = QLabel("Quellen:"); ql.setStyleSheet("color:#636366;font-size:10px;font-weight:600;")
        chips_row.addWidget(ql)
        for s in SOURCES.values():
            chip = QLabel(f"  {s['name']}  ")
            chip.setStyleSheet(
                f"background:{s['color']}22;color:{s['color']};"
                "border-radius:8px;font-size:9px;font-weight:700;padding:2px 0;")
            chip.setFixedHeight(18)
            chips_row.addWidget(chip)
        chips_row.addStretch()
        i_layout.addLayout(chips_row)

        # Status
        self.status = QLabel("Suchbegriff eingeben und Enter drücken.")
        self.status.setStyleSheet("color:#48484a;font-size:11px;")
        i_layout.addWidget(self.status)

        # Smart banner
        self.banner = SmartBanner()
        self.banner.quick_apply.connect(self._quick_apply)
        self.banner.hide()
        i_layout.addWidget(self.banner)

        # Divider
        self.divider = QFrame()
        self.divider.setFixedHeight(1)
        self.divider.setStyleSheet("background:#2c2c2e;")
        self.divider.hide()
        i_layout.addWidget(self.divider)

        # Results grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self._gw = QWidget()
        self._gw.setStyleSheet("background: transparent;")
        self._grid = QGridLayout(self._gw)
        self._grid.setSpacing(10)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        scroll.setWidget(self._gw)
        i_layout.addWidget(scroll, 1)

        # ── Bottom bar (checkboxes + buttons) ─────────────────────────────
        bottom = QWidget()
        bottom.setStyleSheet(
            "background: #1c1c1e; border-top: 1px solid #2c2c2e;")
        b_layout = QVBoxLayout(bottom)
        b_layout.setContentsMargins(20, 10, 20, 14)
        b_layout.setSpacing(8)

        # Checkboxes
        chk_row = QHBoxLayout(); chk_row.setSpacing(12)
        take_lbl = QLabel("Übernehmen:")
        take_lbl.setStyleSheet("color:#636366;font-size:10px;font-weight:600;")
        chk_row.addWidget(take_lbl)
        self.chk = {}
        # In tags_only mode: show artist+title+tags, no cover
        # In cover_only mode: show only cover
        # In all mode: show everything
        chk_fields = []
        if self._preset != "cover_only":
            chk_fields += [("artist","🎤 Artist"), ("title","📝 Titel")]
        if self._preset != "tags_only":
            chk_fields += [("cover","🖼 Cover")]
        chk_fields += [("genre","🎵 Genre"), ("label","🏷 Label"),
                       ("year","📅 Jahr"), ("bpm","♩ BPM"), ("key","🎹 Key")]

        _chk_style = """
            QCheckBox { color:#ebebf5; font-size:11px; spacing:4px; }
            QCheckBox::indicator { width:14px; height:14px; border-radius:4px;
                border:1.5px solid #3a3a3c; background:#2c2c2e; }
            QCheckBox::indicator:checked { background:#0a84ff; border-color:#0a84ff; }
        """
        for key, label in chk_fields:
            c = QCheckBox(label)
            c.setChecked(True)
            c.setStyleSheet(_chk_style)
            self.chk[key] = c
            chk_row.addWidget(c)
        chk_row.addStretch()
        b_layout.addLayout(chk_row)

        # Apply preset overrides
        if self._cover_only or self._preset == "cover_only":
            for k, c in self.chk.items():
                c.setChecked(k == "cover")
        elif self._preset == "tags_only":
            for k, c in self.chk.items():
                c.setChecked(k not in ("cover",))

        # Buttons row
        br = QHBoxLayout(); br.setSpacing(8); br.addStretch()
        cb = QPushButton("Abbrechen")
        cb.setFixedHeight(38)
        cb.setStyleSheet("""
            QPushButton { background:#2c2c2e; color:#ebebf5; border:none;
                border-radius:10px; font-size:13px; padding:0 18px; }
            QPushButton:hover { background:#3a3a3c; }
        """)
        cb.clicked.connect(self.reject); br.addWidget(cb)
        self.ok_btn = QPushButton("✓  Auswahl übernehmen")
        self.ok_btn.setFixedHeight(38)
        self.ok_btn.setEnabled(False)
        self.ok_btn.setDefault(True)
        self.ok_btn.setStyleSheet("""
            QPushButton { background:#0a84ff; color:white; border:none;
                border-radius:10px; padding:0 20px; font-weight:700; font-size:13px; }
            QPushButton:hover   { background:#2a94ff; }
            QPushButton:pressed { background:#0060c0; }
            QPushButton:disabled { background:#2c2c2e; color:#48484a; }
        """)
        self.ok_btn.clicked.connect(self._apply_selection)
        br.addWidget(self.ok_btn)
        b_layout.addLayout(br)
        root.addWidget(bottom)

        if self.q_edit.text().strip():
            QTimer.singleShot(150, self._search)

    # ── search ────────────────────────────────────────────────────────────────

    def _search(self):
        q = self.q_edit.text().strip()
        if not q: return
        self._clear()
        self._pending = 4
        self.s_btn.setEnabled(False)
        self.ok_btn.setEnabled(False)
        self.banner.hide(); self.divider.hide()
        self.status.setText("🔍  Suche auf Apple Music, Beatport, SoundCloud und Discogs…")
        for Cls in (_iTunesWorker, _DiscogsWorker, _BeatportWorker, _SoundCloudWorker):
            w = Cls(q); w.done.connect(self._on_results); w.start()
            setattr(self, f"_w{Cls.__name__}", w)

    def _clear(self):
        for i in reversed(range(self._grid.count())):
            widget = self._grid.itemAt(i).widget()
            if widget: widget.deleteLater()
        self._results.clear(); self._cards.clear()
        self._sel_uid = None; self._smart = {}

    # ── receive & display ─────────────────────────────────────────────────────

    def _on_results(self, results: list):
        for r in results:
            uid = f"{r['source']}_{len(self._results)}"
            self._results[uid] = r
        self._pending -= 1
        if self._pending == 0:
            self._rebuild(); self.s_btn.setEnabled(True)

    def _rebuild(self):
        # In tags_only mode: Beatport first (best BPM/Key/Genre data for DJs)
        # In cover/all mode: Apple Music first (best artwork)
        if self._preset == "tags_only":
            _order = {"beatport": 0, "soundcloud": 1, "discogs": 2, "itunes": 3}
        else:
            _order = {s: v["priority"] for s, v in SOURCES.items()}
        ordered = sorted(
            self._results.items(),
            key=lambda x: _order.get(x[1]["source"], 9)
        )
        COLS = 4
        show_cover = (self._preset != "tags_only")
        thumb_jobs = []
        for idx, (uid, r) in enumerate(ordered):
            card = _Card(uid, r, show_cover=show_cover)
            card.selected.connect(self._select)
            self._cards[uid] = card
            self._grid.addWidget(card, idx // COLS, idx % COLS)
            if show_cover:
                t = r.get("thumb") or ""
                if t and "spacer" not in t and "vinyl" not in t:
                    thumb_jobs.append((uid, t, r["source"]))

        n = len(self._cards)
        if n == 0:
            self.status.setText("Keine Ergebnisse gefunden."); return

        src_names = " + ".join(
            SOURCES[s]["name"] for s in ["itunes","beatport","soundcloud","discogs"]
            if any(r["source"] == s for r in self._results.values())
        )
        self.status.setText(
            f"{n} Ergebnis(se) von {src_names} — "
            "einzeln auswählen oder ⭐ Smart Match nutzen")

        # Compute smart match
        self._smart = self._compute_smart()
        self.banner.update_meta(self._smart)

        # In tags_only mode: hide cover thumbnail in banner, still show metadata
        if not show_cover:
            self.banner.cover_lbl.hide()
        else:
            self.banner.cover_lbl.show()
            self.banner.cover_lbl.setText("⏳")

        self.banner.show(); self.divider.show()

        # Load all thumbs (including smart banner) — only in cover/all mode
        if show_cover:
            sm_thumb = self._smart.get("cover_thumb","")
            if sm_thumb:
                thumb_jobs.insert(0, ("__smart__", sm_thumb, self._smart.get("cover_src","")))
            if thumb_jobs:
                self._loader = _ThumbLoader(thumb_jobs)
                self._loader.loaded.connect(self._set_thumb)
                self._loader.start()

    def _set_thumb(self, uid: str, pix: QPixmap):
        if uid == "__smart__":
            self.banner.set_thumb(pix)
        elif uid in self._cards:
            self._cards[uid].set_thumb(pix)

    # ── smart match logic ─────────────────────────────────────────────────────

    def _compute_smart(self) -> dict:
        rs = list(self._results.values())
        def best(field, order):
            for src in order:
                for r in rs:
                    if r["source"] == src and r.get(field):
                        return r[field], src
            return "", ""

        cover_url, cover_src = "", ""
        for src in ["itunes", "beatport", "soundcloud", "discogs"]:
            for r in rs:
                cu = r.get("cover_url","")
                if r["source"] == src and cu and "spacer" not in cu and "vinyl" not in cu:
                    cover_url = cu
                    cover_src = src
                    break
            if cover_url: break

        cover_thumb = ""
        cover_artist = ""
        cover_title  = ""
        for r in rs:
            if r["source"] == cover_src and r.get("thumb"):
                cover_thumb  = r["thumb"]
                cover_artist = r.get("artist", "")
                cover_title  = r.get("title", "")
                break

        genre,  genre_src  = best("genre", ["discogs","beatport","soundcloud","itunes"])
        label,  label_src  = best("label", ["discogs","beatport"])
        year,   year_src   = best("year",  ["beatport","itunes","soundcloud","discogs"])
        bpm,    _          = best("bpm",   ["beatport"])
        key,    _          = best("key",   ["beatport"])

        return {
            "cover_url": cover_url, "cover_thumb": cover_thumb, "cover_src": cover_src,
            "artist": cover_artist, "title": cover_title,
            "genre": genre, "genre_src": genre_src,
            "label": label, "label_src": label_src,
            "year":  year,  "year_src":  year_src,
            "bpm":   bpm,   "key":       key,
        }

    # ── apply ─────────────────────────────────────────────────────────────────

    def _quick_apply(self):
        """Smart Match button — apply best-of-all sources."""
        if not self._smart: return
        self._emit_payload(self._smart.get("cover_url",""),
                           self._smart.get("cover_src","itunes"),
                           self._smart)

    def _apply_selection(self):
        """Apply the manually selected card."""
        if not self._sel_uid: return
        r = self._results[self._sel_uid]
        self._emit_payload(r.get("cover_url",""), r["source"], r)

    def _emit_payload(self, cover_url: str, cover_src: str, sm: dict):
        payload: dict = {}

        if self.chk.get("cover") and self.chk["cover"].isChecked() and cover_url:
            self.status.setText("⬇️  Lade Cover…")
            self.banner.apply_btn.setEnabled(False)
            self.ok_btn.setEnabled(False)
            try:
                h = {"User-Agent": _UA, "Accept": "image/*"}
                if cover_src == "discogs":     h["Referer"] = "https://www.discogs.com/"
                if cover_src == "beatport":    h["Referer"] = "https://www.beatport.com/"
                if cover_src == "soundcloud":  h["Referer"] = "https://soundcloud.com/"
                req = urllib.request.Request(cover_url, headers=h)
                with urllib.request.urlopen(req, timeout=15) as r:
                    payload["cover_data"] = r.read()
                payload["cover_mime"] = "image/jpeg"
            except Exception as e:
                self.status.setText(f"Cover-Fehler: {e}")

        for key in ("artist","title","genre","label","year","bpm","key"):
            if key in self.chk and self.chk[key].isChecked() and sm.get(key):
                payload[key] = sm[key]

        if payload:
            self.result_selected.emit(payload)
            self.accept()
        else:
            self.status.setText("Nichts zum Übernehmen ausgewählt.")
            self.banner.apply_btn.setEnabled(True)
            self.ok_btn.setEnabled(True)

    def _select(self, uid: str):
        if self._sel_uid and self._sel_uid in self._cards:
            self._cards[self._sel_uid].mark(False)
        self._sel_uid = uid
        self._cards[uid].mark(True)
        self.ok_btn.setEnabled(True)

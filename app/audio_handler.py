import os
import shutil
import struct
import tempfile
from pathlib import Path
from typing import Optional

from mutagen.mp3 import MP3
from mutagen.id3 import (
    ID3, ID3NoHeaderError,
    TIT2, TPE1, TALB, TPE2, TDRC, TCON,
    TRCK, TBPM, TKEY, TPUB, COMM, TCOM, APIC,
)
from mutagen.flac import FLAC, Picture as FLACPicture
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from mutagen.wave import WAVE
from mutagen.aiff import AIFF

SUPPORTED_EXTENSIONS = {'.mp3', '.flac', '.wav', '.aiff', '.aif', '.m4a', '.mp4'}


def _id3_str(tags, key):
    if tags is None:
        return ''
    frame = tags.get(key)
    if frame is None:
        return ''
    if hasattr(frame, 'text') and frame.text:
        return str(frame.text[0])
    return ''


def _year_only(value: str) -> str:
    return value.split('T')[0].split('-')[0].strip()


# ── RIFF INFO (WAV) ───────────────────────────────────────────────────────────
# Rekordbox ignores ID3 inside WAV and only reads the RIFF LIST/INFO chunk, so
# WAV files get both: ID3 (Serato, Traktor, Finder) and INFO (Rekordbox).

_INFO_FIELDS = (('INAM', 'title'), ('IART', 'artist'), ('IPRD', 'album'),
                ('IGNR', 'genre'), ('ICRD', 'year'), ('ICMT', 'comment'),
                ('ITRK', 'track'))


def _riff_chunks(f):
    """Yield (id, data_offset, size) for every top-level chunk of a RIFF/WAVE file."""
    f.seek(0)
    hdr = f.read(12)
    if len(hdr) < 12 or hdr[:4] != b'RIFF' or hdr[8:12] != b'WAVE':
        return
    end = min(struct.unpack('<I', hdr[4:8])[0] + 8, os.fstat(f.fileno()).st_size)
    pos = 12
    while pos + 8 <= end:
        f.seek(pos)
        cid, size = struct.unpack('<4sI', f.read(8))
        yield cid, pos + 8, size
        pos += 8 + size + (size & 1)


def _parse_info(data: bytes) -> list:
    out, i = [], 4                       # skip b'INFO'
    while i + 8 <= len(data):
        sid, size = struct.unpack('<4sI', data[i:i+8])
        out.append((sid, data[i+8:i+8+size]))
        i += 8 + size + (size & 1)
    return out


def _decode(raw: bytes) -> str:
    raw = raw.split(b'\x00', 1)[0]
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('latin-1')


def read_riff_info(path: str) -> dict:
    try:
        with open(path, 'rb') as f:
            for cid, off, size in _riff_chunks(f):
                if cid == b'LIST':
                    f.seek(off); data = f.read(size)
                    if data[:4] == b'INFO':
                        names = dict((k.encode(), v) for k, v in _INFO_FIELDS)
                        return {names[sid]: _decode(v) for sid, v in _parse_info(data)
                                if sid in names and _decode(v).strip()}
    except OSError:
        pass
    return {}


def write_riff_info(path: str, values: dict):
    """Replace the managed INFO fields (others such as ISFT are kept)."""
    managed = {k.encode() for k, _ in _INFO_FIELDS}
    with open(path, 'rb') as src:
        chunks = list(_riff_chunks(src))
        if not chunks:
            return
        old = []
        for cid, off, size in chunks:
            if cid == b'LIST':
                src.seek(off)
                if src.read(4) == b'INFO':
                    src.seek(off); old = _parse_info(src.read(size))
        subs = [(sid, v) for sid, v in old if sid not in managed]
        for k, field in _INFO_FIELDS:
            v = str(values.get(field, '') or '').strip()
            if v:
                subs.append((k.encode(), v.encode('utf-8') + b'\x00'))
        body = b'INFO' + b''.join(
            struct.pack('<4sI', sid, len(v)) + v + (b'\x00' if len(v) & 1 else b'')
            for sid, v in subs)
        info = struct.pack('<4sI', b'LIST', len(body)) + body if len(subs) else b''

        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.tmp')
        try:
            with os.fdopen(fd, 'wb') as dst:
                dst.write(b'RIFF\x00\x00\x00\x00WAVE')
                placed = False
                for cid, off, size in chunks:
                    if cid == b'LIST':
                        src.seek(off)
                        if src.read(4) == b'INFO':
                            if not placed:          # new INFO where the old one was
                                dst.write(info); placed = True
                            continue
                    src.seek(off - 8)
                    _copy(src, dst, 8 + size + (size & 1))
                if not placed:
                    dst.write(info)
                riff_size = dst.tell() - 8
                dst.seek(4); dst.write(struct.pack('<I', riff_size))
            shutil.copymode(path, tmp)
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp): os.remove(tmp)
            raise


def _copy(src, dst, n: int):
    while n > 0:
        buf = src.read(min(n, 1 << 20))
        if not buf: break
        dst.write(buf); n -= len(buf)


class AudioFile:
    def __init__(self, path: str):
        self.path = path
        self.filename = os.path.basename(path)
        self.extension = Path(path).suffix.lower()

        self.title = ''
        self.artist = ''
        self.album = ''
        self.album_artist = ''
        self.year = ''
        self.genre = ''
        self.track = ''
        self.bpm = ''
        self.key = ''
        self.label = ''
        self.comment = ''
        self.composer = ''

        self.cover_data: Optional[bytes] = None
        self.cover_mime: str = 'image/jpeg'

        self.duration: float = 0.0
        self.bitrate: int = 0
        self.sample_rate: int = 0

        self._modified = False
        self._load()

    # ── loading ──────────────────────────────────────────────────────────────

    def _load(self):
        try:
            ext = self.extension
            if ext == '.mp3':
                self._load_mp3()
            elif ext == '.flac':
                self._load_flac()
            elif ext == '.wav':
                self._load_wav()
            elif ext in ('.aiff', '.aif'):
                self._load_aiff()
            elif ext in ('.m4a', '.mp4'):
                self._load_m4a()
        except Exception as e:
            print(f"Load error {self.path}: {e}")

    def _load_id3_tags(self, tags):
        self.title = _id3_str(tags, 'TIT2')
        self.artist = _id3_str(tags, 'TPE1')
        self.album = _id3_str(tags, 'TALB')
        self.album_artist = _id3_str(tags, 'TPE2')
        self.year = _year_only(_id3_str(tags, 'TDRC'))
        self.genre = _id3_str(tags, 'TCON')
        self.track = _id3_str(tags, 'TRCK').split('/')[0]
        self.bpm = _id3_str(tags, 'TBPM')
        self.key = _id3_str(tags, 'TKEY')
        self.label = _id3_str(tags, 'TPUB')
        self.composer = _id3_str(tags, 'TCOM')

        # Prefer the plain comment; skip iTunes housekeeping (iTunNORM, iTunSMPB…)
        comms = [tags[k] for k in tags.keys() if k.startswith('COMM')]
        comms = [c for c in comms if not str(getattr(c, 'desc', '')).startswith('iTun')]
        comms.sort(key=lambda c: bool(getattr(c, 'desc', '')))
        if comms and comms[0].text:
            self.comment = str(comms[0].text[0])

        for key in tags.keys():
            if key.startswith('APIC'):
                frame = tags[key]
                self.cover_data = frame.data
                self.cover_mime = frame.mime
                break

    def _load_mp3(self):
        audio = MP3(self.path)
        self.duration = audio.info.length
        self.bitrate = getattr(audio.info, 'bitrate', 0) // 1000
        self.sample_rate = audio.info.sample_rate
        try:
            self._load_id3_tags(ID3(self.path))
        except ID3NoHeaderError:
            pass

    def _load_flac(self):
        audio = FLAC(self.path)
        self.duration = audio.info.length
        self.bitrate = getattr(audio.info, 'bitrate', 0) // 1000
        self.sample_rate = audio.info.sample_rate

        def g(k):
            vals = audio.get(k.lower(), [])
            return vals[0] if vals else ''

        self.title = g('title')
        self.artist = g('artist')
        self.album = g('album')
        self.album_artist = g('albumartist')
        self.year = _year_only(g('date'))
        self.genre = g('genre')
        self.track = g('tracknumber').split('/')[0]
        self.bpm = g('bpm')
        self.key = g('initialkey')
        self.label = g('label') or g('organization')
        self.comment = g('comment')
        self.composer = g('composer')

        if audio.pictures:
            pic = audio.pictures[0]
            self.cover_data = pic.data
            self.cover_mime = pic.mime

    def _load_wav(self):
        audio = WAVE(self.path)
        self.duration = audio.info.length
        self.sample_rate = audio.info.sample_rate
        if audio.tags:
            self._load_id3_tags(audio.tags)
        # Fill gaps from RIFF INFO (what Rekordbox and many DAWs write)
        for field, value in read_riff_info(self.path).items():
            if not getattr(self, field):
                setattr(self, field, _year_only(value) if field == 'year' else value)

    def _load_aiff(self):
        audio = AIFF(self.path)
        self.duration = audio.info.length
        self.sample_rate = audio.info.sample_rate
        if audio.tags:
            self._load_id3_tags(audio.tags)

    def _load_m4a(self):
        audio = MP4(self.path)
        self.duration = audio.info.length
        self.bitrate = getattr(audio.info, 'bitrate', 0) // 1000
        self.sample_rate = audio.info.sample_rate

        tags = audio.tags
        if tags is None:
            return

        def g(k, default=''):
            vals = tags.get(k, [])
            if not vals:
                return default
            v = vals[0]
            if isinstance(v, bytes):
                return v.decode('utf-8', errors='replace')
            if hasattr(v, '__bytes__'):
                return bytes(v).decode('utf-8', errors='replace')
            if isinstance(v, tuple):
                return str(v[0])
            return str(v)

        self.title = g('\xa9nam')
        self.artist = g('\xa9ART')
        self.album = g('\xa9alb')
        self.album_artist = g('aART')
        self.year = _year_only(g('\xa9day'))
        self.genre = g('\xa9gen')
        self.comment = g('\xa9cmt')
        self.composer = g('\xa9wrt')

        trkn = tags.get('trkn', [])
        if trkn and isinstance(trkn[0], tuple):
            self.track = str(trkn[0][0])

        tmpo = tags.get('tmpo', [])
        if tmpo:
            self.bpm = str(tmpo[0])

        for k in ('----:com.apple.iTunes:initialkey', '----:com.apple.iTunes:KEY',
                  '----:com.apple.iTunes:INITIALKEY'):
            v = tags.get(k)
            if v:
                raw = v[0]
                self.key = (bytes(raw).decode('utf-8', errors='replace')
                            if hasattr(raw, '__bytes__') else str(raw))
                break

        for k in ('----:com.apple.iTunes:LABEL', '----:com.apple.iTunes:Label',
                  '\xa9pub', 'cprt'):
            v = tags.get(k)
            if v:
                raw = v[0]
                self.label = (bytes(raw).decode('utf-8', errors='replace')
                              if hasattr(raw, '__bytes__') else str(raw))
                break

        if 'covr' in tags:
            cover = tags['covr'][0]
            self.cover_data = bytes(cover)
            self.cover_mime = ('image/png'
                               if cover.imageformat == MP4Cover.FORMAT_PNG
                               else 'image/jpeg')

    # ── saving ───────────────────────────────────────────────────────────────

    def save(self) -> bool:
        try:
            ext = self.extension
            if ext == '.mp3':
                self._save_mp3()
            elif ext == '.flac':
                self._save_flac()
            elif ext == '.wav':
                self._save_wav()
            elif ext in ('.aiff', '.aif'):
                self._save_aiff()
            elif ext in ('.m4a', '.mp4'):
                self._save_m4a()
            self._modified = False
            return True
        except Exception as e:
            print(f"Save error {self.path}: {e}")
            return False

    # ── cover normalization (Rekordbox compatibility) ─────────────────────────

    def _normalize_cover(self):
        """Convert cover to JPEG and resize per user setting — required for Rekordbox."""
        if not self.cover_data:
            return
        try:
            from PIL import Image
            import io
            max_px = 1000
            try:
                from PyQt6.QtCore import QSettings
                max_px = int(QSettings("TrackTag", "TrackTag").value("cover_max_size", 1000))
            except Exception:
                pass
            img = Image.open(io.BytesIO(self.cover_data))
            # Already a baseline JPEG within the size limit → keep the original
            # bytes (re-encoding on every save would slowly degrade the artwork).
            # Progressive JPEGs are re-encoded: CDJs and Rekordbox can't show them.
            progressive = img.info.get('progressive') or img.info.get('progression')
            if (img.format == 'JPEG' and img.mode == 'RGB' and not progressive
                    and not (max_px and max(img.size) > max_px)):
                self.cover_mime = 'image/jpeg'
                return
            if img.mode not in ('RGB',):
                img = img.convert('RGB')
            if max_px and max(img.size) > max_px:
                img.thumbnail((max_px, max_px), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=92, optimize=True, progressive=False)
            self.cover_data = buf.getvalue()
            self.cover_mime = 'image/jpeg'
        except Exception as e:
            print(f"Cover normalization warning: {e}")

    def _apply_id3_tags(self, tags, encoding=3):
        """
        Write ID3 frames to `tags`.
        encoding=3 (UTF-8)  → for MP3/ID3v2.4-compatible files
        encoding=1 (UTF-16) → for WAV/AIFF which must be strict ID3v2.3

        Only the frames TrackTag manages are touched — cue points, beatgrids
        (Serato GEOB), ISRC and other frames are left as they are.
        """
        for fid, cls, value in (
            ('TIT2', TIT2, self.title),   ('TPE1', TPE1, self.artist),
            ('TALB', TALB, self.album),   ('TPE2', TPE2, self.album_artist),
            ('TDRC', TDRC, self.year),    ('TCON', TCON, self.genre),
            ('TRCK', TRCK, self.track),   ('TBPM', TBPM, self.bpm),
            ('TKEY', TKEY, self.key),     ('TPUB', TPUB, self.label),
            ('TCOM', TCOM, self.composer),
        ):
            tags.delall(fid)
            if str(value).strip():
                tags.add(cls(encoding=encoding, text=str(value).strip()))

        # Replace the plain comment only (keep iTunNORM etc.)
        for k in [k for k in tags.keys() if k.startswith('COMM')]:
            if not str(getattr(tags[k], 'desc', '')).startswith('iTun'):
                del tags[k]
        if self.comment.strip():
            tags.add(COMM(encoding=encoding, lang='eng', desc='', text=self.comment))

        # Always clear existing cover, then re-add if set
        tags.delall('APIC')
        if self.cover_data:
            tags['APIC:'] = APIC(
                encoding=0,           # Latin-1 for desc — max compatibility
                mime='image/jpeg',    # Rekordbox requires JPEG
                type=3,               # 3 = Cover (front)
                desc='',
                data=self.cover_data,
            )

    def _save_mp3(self):
        self._normalize_cover()       # JPEG + max 1000px
        try:
            tags = ID3(self.path)
        except ID3NoHeaderError:
            tags = ID3()
        self._apply_id3_tags(tags)
        tags.save(self.path, v2_version=3)   # ID3v2.3 — Rekordbox compatibility

    def _save_flac(self):
        self._normalize_cover()
        audio = FLAC(self.path)
        for k, v in (('title', self.title), ('artist', self.artist), ('album', self.album),
                     ('albumartist', self.album_artist), ('date', self.year),
                     ('genre', self.genre), ('tracknumber', self.track), ('bpm', self.bpm),
                     ('initialkey', self.key), ('label', self.label),
                     ('comment', self.comment), ('composer', self.composer)):
            if str(v).strip():
                audio[k] = str(v).strip()
            elif k in audio:
                del audio[k]

        audio.clear_pictures()
        if self.cover_data:
            pic = FLACPicture()
            pic.type = 3
            pic.mime = 'image/jpeg'
            pic.data = self.cover_data
            audio.add_picture(pic)
        audio.save()

    def _save_wav(self):
        self._normalize_cover()
        audio = WAVE(self.path)
        if audio.tags is None:
            audio.add_tags()
        # encoding=1 (UTF-16) is the only valid text encoding for strict ID3v2.3
        # Rekordbox enforces this for WAV files (more lenient for MP3)
        self._apply_id3_tags(audio.tags, encoding=1)
        audio.tags.update_to_v23()
        audio.save(v2_version=3)
        # Rekordbox only reads RIFF INFO in WAV files
        write_riff_info(self.path, {f: getattr(self, f) for _, f in _INFO_FIELDS})

    def _save_aiff(self):
        self._normalize_cover()
        audio = AIFF(self.path)
        if audio.tags is None:
            audio.add_tags()
        self._apply_id3_tags(audio.tags, encoding=1)
        audio.tags.update_to_v23()
        audio.save(v2_version=3)      # strict ID3v2.3 for Rekordbox

    def _save_m4a(self):
        self._normalize_cover()
        audio = MP4(self.path)
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags

        tags['\xa9nam'] = [self.title]
        tags['\xa9ART'] = [self.artist]
        tags['\xa9alb'] = [self.album]
        tags['aART'] = [self.album_artist]
        tags['\xa9day'] = [self.year]
        tags['\xa9gen'] = [self.genre]
        tags['\xa9cmt'] = [self.comment]
        tags['\xa9wrt'] = [self.composer]

        if self.track:
            try:
                tags['trkn'] = [(int(self.track), 0)]
            except ValueError:
                pass
        if self.bpm:
            try:
                tags['tmpo'] = [int(round(float(self.bpm)))]
            except ValueError:
                pass
        elif 'tmpo' in tags:
            del tags['tmpo']

        # Key + label live in iTunes freeform atoms
        for atom, value, aliases in (
            ('----:com.apple.iTunes:initialkey', self.key,
             ('----:com.apple.iTunes:KEY', '----:com.apple.iTunes:INITIALKEY')),
            ('----:com.apple.iTunes:LABEL', self.label, ('----:com.apple.iTunes:Label',)),
        ):
            for a in aliases:
                if a in tags: del tags[a]
            if value.strip():
                tags[atom] = [MP4FreeForm(value.strip().encode('utf-8'))]
            elif atom in tags:
                del tags[atom]

        # Always clear cover, re-add as JPEG if set
        if 'covr' in tags:
            del tags['covr']
        if self.cover_data:
            tags['covr'] = [MP4Cover(self.cover_data, imageformat=MP4Cover.FORMAT_JPEG)]

        audio.save()

    def clear_cover(self):
        """Explicitly remove cover art (will be deleted on next save)."""
        self.cover_data = None
        self.cover_mime = 'image/jpeg'
        self._modified = True

    # ── helpers ───────────────────────────────────────────────────────────────

    @property
    def duration_str(self) -> str:
        total = int(self.duration)
        return f"{total // 60}:{total % 60:02d}"

    @property
    def bitrate_str(self) -> str:
        return f"{self.bitrate} kbps" if self.bitrate else ''

    @property
    def sample_rate_str(self) -> str:
        return f"{self.sample_rate // 1000} kHz" if self.sample_rate else ''

    def set_field(self, field: str, value: str):
        setattr(self, field, value)
        self._modified = True

    def set_cover(self, data: bytes, mime: str):
        self.cover_data = data
        self.cover_mime = mime
        self._modified = True

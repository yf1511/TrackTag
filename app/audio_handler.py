import os
from pathlib import Path
from typing import Optional

from mutagen.mp3 import MP3
from mutagen.id3 import (
    ID3, ID3NoHeaderError,
    TIT2, TPE1, TALB, TPE2, TDRC, TCON,
    TRCK, TBPM, TKEY, TPUB, COMM, TCOM, APIC,
)
from mutagen.flac import FLAC, Picture as FLACPicture
from mutagen.mp4 import MP4, MP4Cover
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

        for key in tags.keys():
            if key.startswith('COMM'):
                frame = tags[key]
                if hasattr(frame, 'text') and frame.text:
                    self.comment = str(frame.text[0])
                break

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
        """Convert cover to JPEG and resize to max 1000px — required for Rekordbox."""
        if not self.cover_data:
            return
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(self.cover_data))
            if img.mode not in ('RGB',):
                img = img.convert('RGB')
            if max(img.size) > 1000:
                img.thumbnail((1000, 1000), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=92, optimize=True)
            self.cover_data = buf.getvalue()
            self.cover_mime = 'image/jpeg'
        except Exception as e:
            print(f"Cover normalization warning: {e}")

    def _apply_id3_tags(self, tags, encoding=3):
        """
        Write ID3 frames to `tags`.
        encoding=3 (UTF-8)  → for MP3/ID3v2.4-compatible files
        encoding=1 (UTF-16) → for WAV/AIFF which must be strict ID3v2.3
        """
        tags['TIT2'] = TIT2(encoding=encoding, text=self.title)
        tags['TPE1'] = TPE1(encoding=encoding, text=self.artist)
        tags['TALB'] = TALB(encoding=encoding, text=self.album)
        tags['TPE2'] = TPE2(encoding=encoding, text=self.album_artist)
        tags['TDRC'] = TDRC(encoding=encoding, text=self.year)
        tags['TCON'] = TCON(encoding=encoding, text=self.genre)
        tags['TRCK'] = TRCK(encoding=encoding, text=self.track)
        tags['TBPM'] = TBPM(encoding=encoding, text=self.bpm)
        tags['TKEY'] = TKEY(encoding=encoding, text=self.key)
        tags['TPUB'] = TPUB(encoding=encoding, text=self.label)
        tags['TCOM'] = TCOM(encoding=encoding, text=self.composer)
        tags['COMM::eng'] = COMM(encoding=encoding, lang='eng', desc='', text=self.comment)

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
        audio['title'] = self.title
        audio['artist'] = self.artist
        audio['album'] = self.album
        audio['albumartist'] = self.album_artist
        audio['date'] = self.year
        audio['genre'] = self.genre
        audio['tracknumber'] = self.track
        audio['bpm'] = self.bpm
        audio['initialkey'] = self.key
        audio['label'] = self.label
        audio['comment'] = self.comment
        audio['composer'] = self.composer

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
        else:
            audio.tags.clear()           # wipe stale frames before rewriting
        # encoding=1 (UTF-16) is the only valid text encoding for strict ID3v2.3
        # Rekordbox enforces this for WAV files (more lenient for MP3)
        self._apply_id3_tags(audio.tags, encoding=1)
        try:
            audio.tags.update_to_v23()
        except Exception:
            pass
        audio.save()

    def _save_aiff(self):
        self._normalize_cover()
        audio = AIFF(self.path)
        if audio.tags is None:
            audio.add_tags()
        else:
            audio.tags.clear()
        self._apply_id3_tags(audio.tags, encoding=1)
        try:
            audio.tags.update_to_v23()
        except Exception:
            pass
        audio.save()

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
                tags['tmpo'] = [int(self.bpm)]
            except ValueError:
                pass

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

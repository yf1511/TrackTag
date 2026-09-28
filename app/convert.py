"""
Lossless WAV → AIFF conversion.

Rekordbox never shows artwork embedded in WAV files, but it does for AIFF.
The PCM samples are copied bit-for-bit (only the byte order changes, as AIFF
is big-endian), every ID3 frame — including Serato cue points — is carried
over, and the WAV is moved to the Trash.
"""
import math
import os
import struct
from array import array


from mutagen.aiff import AIFF
from mutagen.wave import WAVE

from .audio_handler import AudioFile, _riff_chunks

_PCM = 1
_EXTENSIBLE = 0xFFFE


class ConvertError(Exception):
    pass


def _ext80(x: float) -> bytes:
    """IEEE 754 80-bit extended float (AIFF sample rate)."""
    if x <= 0:
        return b"\x00" * 10
    m, e = math.frexp(x)                        # x = m · 2^e, 0.5 ≤ m < 1
    return struct.pack(">HQ", e + 16382, int(m * (1 << 64)))


def _swap(block: bytes, width: int) -> bytes:
    """Little-endian PCM → big-endian PCM (8-bit: unsigned → signed)."""
    if width == 1:
        return bytes(b ^ 0x80 for b in block)
    if width == 2:
        a = array("h"); a.frombytes(block); a.byteswap(); return a.tobytes()
    if width == 4:
        a = array("i"); a.frombytes(block); a.byteswap(); return a.tobytes()
    if width == 3:
        out = bytearray(len(block))
        out[0::3], out[1::3], out[2::3] = block[2::3], block[1::3], block[0::3]
        return bytes(out)
    raise ConvertError(f"{width * 8}-bit samples are not supported")


def aiff_path_for(wav_path: str) -> str:
    base = os.path.splitext(wav_path)[0]
    out, n = base + ".aiff", 1
    while os.path.exists(out):
        out = f"{base} ({n}).aiff"; n += 1
    return out


def wav_to_aiff(af: AudioFile, progress=None) -> str:
    """Convert af (a WAV) to AIFF next to it; returns the new path.
    Tags come from the in-memory AudioFile, so unsaved edits are kept."""
    src_path = af.path
    with open(src_path, "rb") as src:
        chunks = {cid: (off, size) for cid, off, size in _riff_chunks(src)}
        if b"fmt " not in chunks or b"data" not in chunks:
            raise ConvertError("not a valid WAV file")
        off, size = chunks[b"fmt "]
        src.seek(off)
        fmt = src.read(size)
        tag, channels, rate, _, align, bits = struct.unpack("<HHIIHH", fmt[:16])
        if tag == _EXTENSIBLE and len(fmt) >= 26:
            tag = struct.unpack("<H", fmt[24:26])[0]
        if tag != _PCM:
            raise ConvertError("only integer PCM WAV files can be converted (this one is float/compressed)")
        width = (bits + 7) // 8
        if align != width * channels:
            raise ConvertError("unusual sample layout")

        data_off, data_size = chunks[b"data"]
        data_size -= data_size % align
        frames = data_size // align
        dst_path = aiff_path_for(src_path)
        try:
            with open(dst_path, "wb") as dst:
                comm = struct.pack(">hIh", channels, frames, bits) + _ext80(rate)
                ssnd_len = 8 + data_size
                form_len = 4 + (8 + len(comm)) + (8 + ssnd_len + (ssnd_len & 1))
                dst.write(b"FORM" + struct.pack(">I", form_len) + b"AIFF")
                dst.write(b"COMM" + struct.pack(">I", len(comm)) + comm)
                dst.write(b"SSND" + struct.pack(">III", ssnd_len, 0, 0))
                src.seek(data_off)
                step = align * 65536
                done = 0
                while done < data_size:
                    block = src.read(min(step, data_size - done))
                    if not block: break
                    dst.write(_swap(block, width))
                    done += len(block)
                    if progress: progress(done / data_size)
                if ssnd_len & 1:
                    dst.write(b"\x00")
        except Exception:
            if os.path.exists(dst_path): os.remove(dst_path)
            raise

    # Carry over every ID3 frame (Serato cues, ISRC, …), then TrackTag's fields
    try:
        wav = WAVE(src_path)
        aiff = AIFF(dst_path)
        aiff.add_tags()
        if wav.tags:
            for frame in wav.tags.values():
                aiff.tags.add(frame)
        aiff.save(v2_version=3)
        new = AudioFile(dst_path)
        for f in ("title", "artist", "album", "album_artist", "year", "genre", "track",
                  "bpm", "key", "label", "comment", "composer"):
            setattr(new, f, getattr(af, f))
        new.cover_data, new.cover_mime = af.cover_data, af.cover_mime
        if not new.save():
            raise ConvertError("could not write tags to the AIFF file")
    except Exception:
        if os.path.exists(dst_path): os.remove(dst_path)
        raise
    return dst_path


def move_to_trash(path: str) -> bool:
    """Finder-style trash (supports 'Put Back')."""
    from PyQt6.QtCore import QFile
    ok = QFile.moveToTrash(path)
    return ok[0] if isinstance(ok, tuple) else bool(ok)

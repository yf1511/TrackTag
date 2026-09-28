"""
Audio quality check — spots fake bitrates and lossy-sourced lossless files.

Lossy encoders cut everything above a frequency that depends on the bitrate
(128 kbps ≈ 16 kHz, 192 ≈ 19 kHz, 320 ≈ 20 kHz); genuine lossless audio
reaches ~22 kHz. The track is decoded with Qt's audio decoder, a windowed FFT
is averaged over loud passages, and the frequency where the spectrum falls
off a cliff is compared with what the file claims to be.
"""
import json
import os
from typing import Callable, Optional

import numpy as np
from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioDecoder, QAudioFormat

N_FFT = 4096
STEP_S = 0.35            # one analysis window every 0.35 s of audio

GOOD, OK, SUSPECT, FAKE, UNKNOWN = "good", "ok", "suspect", "fake", "unknown"

_LOSSLESS = {".wav", ".aiff", ".aif", ".flac"}


def _to_mono(buf) -> np.ndarray:
    fmt = buf.format()
    data = buf.data()                       # sip.voidptr / bytes
    raw = bytes(data) if not isinstance(data, (bytes, bytearray)) else data
    sf = fmt.sampleFormat()
    if sf == QAudioFormat.SampleFormat.Float:
        a = np.frombuffer(raw, dtype="<f4")
    elif sf == QAudioFormat.SampleFormat.Int16:
        a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
    elif sf == QAudioFormat.SampleFormat.Int32:
        a = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648
    elif sf == QAudioFormat.SampleFormat.UInt8:
        a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128
    else:
        return np.zeros(0, np.float32)
    ch = max(1, fmt.channelCount())
    if ch > 1:
        a = a[: len(a) - len(a) % ch].reshape(-1, ch).mean(axis=1)
    return a


class _Spectrum:
    """Streams samples in, keeps the average power spectrum of loud windows."""
    def __init__(self):
        self.rate = 0
        self.acc = np.zeros(0, np.float32)
        self.power: Optional[np.ndarray] = None
        self.n = 0
        self.skip = 0
        self.win = np.hanning(N_FFT).astype(np.float32)

    def feed(self, mono: np.ndarray, rate: int):
        self.rate = rate
        self.acc = np.concatenate([self.acc, mono])
        step = int(rate * STEP_S)
        while len(self.acc) >= N_FFT + self.skip:
            frame = self.acc[self.skip:self.skip + N_FFT]
            self.acc = self.acc[self.skip + N_FFT:]
            self.skip = max(0, step - N_FFT)
            if np.sqrt(np.mean(frame ** 2)) < 0.02:      # ignore quiet parts
                continue
            p = np.abs(np.fft.rfft(frame * self.win)) ** 2
            self.power = p if self.power is None else self.power + p
            self.n += 1


def analyse_spectrum(power: np.ndarray, rate: int) -> dict:
    """Find the frequency where the spectrum falls off a cliff."""
    freqs = np.fft.rfftfreq(N_FFT, 1 / rate)
    db = 10 * np.log10(power + 1e-20)
    k = 9
    db = np.convolve(db, np.ones(k) / k, mode="same")      # ~100 Hz smoothing
    band = (freqs > 1000) & (freqs < 6000)
    ref = float(np.median(db[band]))
    top = min(rate / 2, 22050) - 150
    # highest frequency that is still within 50 dB of the midrange
    above = np.where((db > ref - 50) & (freqs > 8000) & (freqs < top))[0]
    cutoff = float(freqs[above[-1]]) if len(above) else 8000.0
    # How far does the level fall across the cutoff? Lossy files hit a brick
    # wall (40+ dB), natural roll-off in real recordings is gradual.
    below = db[(freqs > cutoff - 1500) & (freqs < cutoff - 300)]
    above_c = db[(freqs > cutoff + 300) & (freqs < min(cutoff + 1500, rate / 2))]
    drop = float(np.mean(below) - np.mean(above_c)) if len(below) and len(above_c) else 0.0
    return {"cutoff": cutoff, "drop": drop, "ref": ref, "nyquist": rate / 2}


def _kbps_for(cutoff: float) -> int:
    # LAME low-pass per bitrate: 128 → 17.0, 160 → 17.5, 192 → 18.6, 256 → 19.7, 320 → 20.5 kHz
    for f, kb in ((17250, 128), (18000, 160), (19100, 192), (19950, 256)):
        if cutoff < f:
            return kb
    return 320


def verdict(ext: str, bitrate_kbps: int, s: dict) -> tuple:
    """(status, short label, explanation)"""
    cut, drop = s["cutoff"], s["drop"]
    khz = f"{cut / 1000:.1f} kHz"
    brick = drop > 25
    if ext in _LOSSLESS:
        if cut >= 20800 or not brick:
            return GOOD, "Lossless", f"Full spectrum up to {khz}"
        if cut >= 19500:
            return SUSPECT, "From MP3?", f"Hard cut at {khz} — likely made from a 320 kbps MP3"
        return FAKE, f"Fake · ~{_kbps_for(cut)} kbps", f"Hard cut at {khz} — made from a ~{_kbps_for(cut)} kbps file"
    # lossy — store encoders (FhG, iTunes) cut 320s anywhere from 19.3 kHz up,
    # so only clearly lower cutoffs are flagged
    claimed = bitrate_kbps or 0
    real = _kbps_for(cut) if brick else 320
    if claimed >= 256 and cut < 18000:
        return FAKE, f"Fake · ~{real} kbps", f"Claims {claimed} kbps but cuts at {khz} (≈{real} kbps)"
    if claimed >= 256 and cut < 19300:
        return SUSPECT, f"~{real} kbps?", f"Claims {claimed} kbps but cuts at {khz} (≈{real} kbps)"
    if claimed and claimed < 256:
        return OK, f"{claimed} kbps", f"Low bitrate for club use — cut at {khz}"
    return GOOD, f"{claimed or real} kbps", f"Spectrum up to {khz}"


# ── Result cache (per audio identity — tag edits don't invalidate it) ────────

_CACHE_FILE = os.path.join(os.path.expanduser("~/Library/Application Support/TrackTag"),
                           "quality.json")
_cache: Optional[dict] = None


def _key(af) -> str:
    return f"{af.path}|{round(af.duration, 1)}|{af.bitrate}|{af.sample_rate}"


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        try:
            with open(_CACHE_FILE, encoding="utf-8") as f:
                _cache = json.load(f)
        except (OSError, ValueError):
            _cache = {}
    return _cache


def cached(af) -> Optional[tuple]:
    v = _load_cache().get(_key(af))
    return tuple(v) if v else None


def remember(af, status: str, label: str, detail: str):
    c = _load_cache()
    c[_key(af)] = [status, label, detail]
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(c, f)
    except OSError:
        pass


class QualityChecker(QObject):
    """Checks files one after another (QAudioDecoder is asynchronous)."""
    result = pyqtSignal(object, str, str, str, dict)   # af, status, label, detail, stats
    progress = pyqtSignal(int, int)
    finished = pyqtSignal()

    def __init__(self, files: list, parent=None):
        super().__init__(parent)
        self._files = list(files)
        self._i = -1
        self._dec: Optional[QAudioDecoder] = None
        self._spec: Optional[_Spectrum] = None
        self._stopped = False

    def start(self):
        self._next()

    def stop(self):
        self._stopped = True
        if self._dec: self._dec.stop()

    def _next(self):
        self._i += 1
        self.progress.emit(self._i, len(self._files))
        if self._stopped or self._i >= len(self._files):
            self.finished.emit(); return
        af = self._files[self._i]
        self._spec = _Spectrum()
        self._dec = QAudioDecoder(self)
        self._dec.bufferReady.connect(self._on_buffer)
        self._dec.finished.connect(self._on_done)
        self._dec.error.connect(lambda *_: self._on_done(error=True))
        self._dec.setSource(QUrl.fromLocalFile(af.path))
        self._dec.start()

    def _on_buffer(self):
        dec = self._dec
        if not dec: return
        buf = dec.read()
        if buf.isValid():
            self._spec.feed(_to_mono(buf), buf.format().sampleRate())

    def _on_done(self, error: bool = False):
        dec, self._dec = self._dec, None
        if dec is None: return
        dec.stop(); dec.deleteLater()
        af = self._files[self._i]
        sp = self._spec
        if error or sp is None or sp.power is None or sp.n < 3:
            self.result.emit(af, UNKNOWN, "—", "Could not analyse this file", {})
        else:
            stats = analyse_spectrum(sp.power / sp.n, sp.rate)
            st, label, detail = verdict(af.extension, af.bitrate, stats)
            self.result.emit(af, st, label, detail, stats)
        self._next()


def check_files(files: list, on_result: Callable, on_done: Callable, parent=None) -> QualityChecker:
    qc = QualityChecker(files, parent)
    qc.result.connect(on_result)
    qc.finished.connect(on_done)
    qc.start()
    return qc

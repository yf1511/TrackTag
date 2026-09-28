"""
Built-in preview player — Space plays/pauses, ↑/↓ changes track while
playing, ←/→ skips. The selected track is preloaded so playback starts
instantly.
"""
from typing import Optional

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QSlider, QStyle
from PyQt6.QtCore import Qt, QUrl, QSize, QSettings, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

from .theme import C_SURFACE, C_SURFACE2, C_BORDER, C_BORDER2, C_TEXT, C_TEXT2, C_TEXT3, C_PRIMARY


def _fmt(ms: int) -> str:
    s = max(0, int(ms // 1000))
    return f"{s // 60}:{s % 60:02d}"


class _SeekSlider(QSlider):
    """Jumps straight to the clicked position (a plain QSlider pages)."""
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            val = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), int(e.position().x()), self.width())
            self.setValue(val)
            self.sliderMoved.emit(val)
        super().mousePressEvent(e)


def _slider_ss(groove_h: int, handle: int) -> str:
    r = handle // 2
    return f"""
        QSlider{{background:transparent;}}
        QSlider::groove:horizontal{{height:{groove_h}px;background:{C_BORDER2};border-radius:{groove_h//2}px;}}
        QSlider::sub-page:horizontal{{background:{C_PRIMARY};border-radius:{groove_h//2}px;}}
        QSlider::handle:horizontal{{width:{handle}px;height:{handle}px;margin:-{r - groove_h//2}px 0;
            border-radius:{r}px;background:#ffffff;}}
    """


class PlayerBar(QFrame):
    playing_changed = pyqtSignal(object)      # path of the playing track or None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("playerBar")
        self.setFixedHeight(60)
        self.setStyleSheet(f"QFrame#playerBar{{background:{C_SURFACE};border:none;"
                           f"border-top:1px solid {C_BORDER};}}")
        self._af = None
        self._dragging = False

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(float(QSettings("TrackTag", "TrackTag").value("volume", 0.8)))
        self.player.setAudioOutput(self.audio)
        self.player.positionChanged.connect(self._on_pos)
        self.player.durationChanged.connect(self._on_dur)
        self.player.playbackStateChanged.connect(self._on_state)

        row = QHBoxLayout(self); row.setContentsMargins(16, 0, 20, 0); row.setSpacing(12)

        self.btn = QPushButton(); self.btn.setFixedSize(34, 34)
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.setToolTip("Play / Pause (Space)")
        self.btn.setStyleSheet(
            "QPushButton{background:#ffffff;border:none;border-radius:17px;padding:0;}"
            "QPushButton:hover{background:#e6e6ea;}")
        self.btn.clicked.connect(lambda: self.toggle())   # clicked passes a bool
        row.addWidget(self.btn)

        self.art = QLabel(); self.art.setFixedSize(36, 36)
        self.art.setStyleSheet(f"background:{C_SURFACE2};border-radius:6px;")
        row.addWidget(self.art)

        info = QVBoxLayout(); info.setSpacing(1)
        self.t_lbl = QLabel("—"); self.a_lbl = QLabel("")
        self.t_lbl.setStyleSheet(f"color:{C_TEXT};font-size:12px;font-weight:600;background:transparent;")
        self.a_lbl.setStyleSheet(f"color:{C_TEXT2};font-size:11px;background:transparent;")
        for l in (self.t_lbl, self.a_lbl):
            l.setFixedWidth(210)
        info.addWidget(self.t_lbl); info.addWidget(self.a_lbl)
        row.addLayout(info)

        self.cur = QLabel("0:00"); self.tot = QLabel("0:00")
        for l in (self.cur, self.tot):
            l.setStyleSheet(f"color:{C_TEXT3};font-size:11px;background:transparent;")
            l.setFixedWidth(34)
        self.cur.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.seek = _SeekSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 0); self.seek.setStyleSheet(_slider_ss(4, 12))
        self.seek.setCursor(Qt.CursorShape.PointingHandCursor)
        self.seek.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.seek.sliderPressed.connect(lambda: setattr(self, "_dragging", True))
        self.seek.sliderReleased.connect(self._release)
        self.seek.sliderMoved.connect(self._scrub)
        row.addWidget(self.cur); row.addWidget(self.seek, 1); row.addWidget(self.tot)

        vol_ic = QLabel()
        try:
            import qtawesome as qta
            vol_ic.setPixmap(qta.icon("fa5s.volume-up", color=C_TEXT3).pixmap(13, 13))
        except Exception:
            pass
        vol_ic.setStyleSheet("background:transparent;")
        self.vol = QSlider(Qt.Orientation.Horizontal); self.vol.setFixedWidth(84)
        self.vol.setRange(0, 100); self.vol.setValue(int(self.audio.volume() * 100))
        self.vol.setStyleSheet(_slider_ss(4, 10)); self.vol.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.vol.valueChanged.connect(self._set_volume)
        row.addSpacing(8); row.addWidget(vol_ic); row.addWidget(self.vol)

        self._set_icon(False)

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def path(self) -> Optional[str]:
        return self._af.path if self._af else None

    def is_playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def load(self, af, autoplay: bool = False):
        """Preload a track (cheap). Keeps playing if it's already the current one."""
        if af is None: return
        if self._af is not af or self.player.source() != QUrl.fromLocalFile(af.path):
            self._af = af
            self.player.setSource(QUrl.fromLocalFile(af.path))
            self._show_meta(af)
        if autoplay:
            self.player.play()

    def toggle(self, af=None):
        if af is not None and af is not self._af:
            self.load(af, autoplay=True); return
        if self._af is None: return
        if self.is_playing(): self.player.pause()
        else: self.player.play()

    def seek_by(self, seconds: int):
        if self._af is None: return
        dur = self.player.duration() or 0
        self.player.setPosition(max(0, min(dur - 500, self.player.position() + seconds * 1000)))

    def stop(self):
        self.player.stop()
        self.player.setSource(QUrl())
        self._af = None
        self.t_lbl.setText("—"); self.a_lbl.setText(""); self.art.clear()
        self.cur.setText("0:00"); self.tot.setText("0:00"); self.seek.setRange(0, 0)

    def release(self, files) -> Optional[tuple]:
        """Let go of the file before it is rewritten; returns state for resume()."""
        if self._af is None or self._af not in files: return None
        state = (self._af, self.player.position(), self.is_playing())
        self.player.stop(); self.player.setSource(QUrl())
        return state

    def resume(self, state: Optional[tuple]):
        if not state: return
        af, pos, playing = state
        # Seeking only works once the media is loaded again
        def on_status(st):
            if st in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
                self.player.mediaStatusChanged.disconnect(on_status)
                self.player.setPosition(pos)
                if playing: self.player.play()
        self.player.mediaStatusChanged.connect(on_status)
        self.player.setSource(QUrl.fromLocalFile(af.path))

    def refresh_meta(self):
        if self._af: self._show_meta(self._af)

    # ── internals ─────────────────────────────────────────────────────────────

    def _show_meta(self, af):
        fm = self.t_lbl.fontMetrics()
        self.t_lbl.setText(fm.elidedText(af.title or af.filename, Qt.TextElideMode.ElideRight, 210))
        self.a_lbl.setText(self.a_lbl.fontMetrics().elidedText(
            af.artist or "", Qt.TextElideMode.ElideRight, 210))
        if af.cover_data:
            pix = QPixmap(); pix.loadFromData(af.cover_data)
            if not pix.isNull():
                from .main_window import _rounded_pixmap
                self.art.setPixmap(_rounded_pixmap(pix, 36, 6)); return
        self.art.clear()

    def _set_icon(self, playing: bool):
        try:
            import qtawesome as qta
            self.btn.setIcon(qta.icon("fa5s.pause" if playing else "fa5s.play", color="#0c0d11"))
            self.btn.setIconSize(QSize(12, 12))
        except Exception:
            self.btn.setText("❚❚" if playing else "▶")

    def _on_state(self, st):
        playing = st == QMediaPlayer.PlaybackState.PlayingState
        self._set_icon(playing)
        self.playing_changed.emit(self.path if playing else None)

    def _on_pos(self, ms: int):
        if not self._dragging:
            self.seek.setValue(ms)
        self.cur.setText(_fmt(ms))

    def _on_dur(self, ms: int):
        self.seek.setRange(0, ms); self.tot.setText(_fmt(ms))

    def _scrub(self, ms: int):
        self.cur.setText(_fmt(ms))
        if not self._dragging:          # plain click → jump now
            self.player.setPosition(ms)

    def _release(self):
        self._dragging = False
        self.player.setPosition(self.seek.value())

    def _set_volume(self, v: int):
        self.audio.setVolume(v / 100)
        QSettings("TrackTag", "TrackTag").setValue("volume", v / 100)

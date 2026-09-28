"""Shared design tokens and button styles for all TrackTag windows."""

# ── Design tokens ─────────────────────────────────────────────────────────────

C_BG       = "#0c0d11"   # content canvas
C_SURFACE  = "#111217"   # sidebars / panels
C_SURFACE2 = "#18191f"   # inputs, cards
C_SURFACE3 = "#202129"   # hover
C_BORDER   = "#23242c"   # hairlines
C_BORDER2  = "#2f3039"   # stronger hairline / hover border
C_TEXT     = "#ededf1"
C_TEXT2    = "#9b9dab"
C_TEXT3    = "#5d606e"
C_PRIMARY  = "#8b5cf6"
C_PRIMARY_SOFT = "rgba(139,92,246,0.14)"
C_ACCENT   = "#ec4899"
C_ACCENT2  = "#f59e0b"
C_DANGER   = "#f43f5e"
C_SUCCESS  = "#22c55e"
C_KEY_CLR  = "#ec4899"
C_SEL_BG   = "#1a1627"
C_SEL_LINE = "#8b5cf6"

# One brand gradient, used only for primary calls-to-action
_GRAD       = f"qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {C_PRIMARY},stop:1 {C_ACCENT})"
_GRAD_HOVER = "qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #9d74f8,stop:1 #f062ab)"
_GRAD_PRESS = "qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #7c4ae8,stop:1 #d93d8a)"

_BTN_PRIMARY = f"""
    QPushButton{{background:{_GRAD};color:#fff;border:none;border-radius:8px;
                 font-size:13px;font-weight:600;}}
    QPushButton:hover{{background:{_GRAD_HOVER};}}
    QPushButton:pressed{{background:{_GRAD_PRESS};}}
    QPushButton:disabled{{background:{C_SURFACE2};color:{C_TEXT3};}}
"""
_BTN_SECONDARY = f"""
    QPushButton{{background:{C_SURFACE2};color:{C_TEXT};border:1px solid {C_BORDER};
                 border-radius:8px;font-size:12px;font-weight:500;padding:0 12px;}}
    QPushButton:hover{{background:{C_SURFACE3};border-color:{C_BORDER2};}}
    QPushButton:pressed{{background:{C_SURFACE};}}
    QPushButton:disabled{{color:{C_TEXT3};background:{C_SURFACE};border-color:{C_SURFACE2};}}
"""
_BTN_GHOST = f"""
    QPushButton{{background:transparent;color:{C_TEXT2};border:none;border-radius:8px;
                 font-size:12px;font-weight:500;padding:0 10px;}}
    QPushButton:hover{{background:{C_SURFACE2};color:{C_TEXT};}}
    QPushButton:disabled{{color:{C_TEXT3};}}
"""

# ── Key normalization (Rekordbox format) + per-key colors ─────────────────────

import sys
import os
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPalette, QColor, QFont, QIcon
from app.main_window import MainWindow
try:
    from version import __version__
except ImportError:
    __version__ = "1.0.0"

_ICON_PATH = os.path.join(os.path.dirname(__file__), "assets", "app-icon.png")


def _dark_palette() -> QPalette:
    p = QPalette()
    # Backgrounds
    p.setColor(QPalette.ColorRole.Window,          QColor(12, 13, 17))    # #0c0d11
    p.setColor(QPalette.ColorRole.Base,            QColor(24, 25, 31))    # #18191f
    p.setColor(QPalette.ColorRole.AlternateBase,   QColor(17, 18, 23))
    # Text
    p.setColor(QPalette.ColorRole.WindowText,      QColor(237, 237, 241))
    p.setColor(QPalette.ColorRole.Text,            QColor(237, 237, 241))
    p.setColor(QPalette.ColorRole.BrightText,      QColor(255, 255, 255))
    # Buttons
    p.setColor(QPalette.ColorRole.Button,          QColor(24, 25, 31))
    p.setColor(QPalette.ColorRole.ButtonText,      QColor(237, 237, 241))
    # Highlights
    p.setColor(QPalette.ColorRole.Highlight,       QColor(139, 92, 246))   # #8b5cf6
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    # Links
    p.setColor(QPalette.ColorRole.Link,            QColor(139, 92, 246))
    # Tooltips
    p.setColor(QPalette.ColorRole.ToolTipBase,     QColor(32, 33, 41))
    p.setColor(QPalette.ColorRole.ToolTipText,     QColor(237, 237, 241))
    # Disabled
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       QColor(93, 96, 110))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(93, 96, 110))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(93, 96, 110))
    return p


STYLESHEET = """
/* ── Global ───────────────────────────────────────────────────────── */
* { font-size: 12px; }

QMainWindow, QDialog { background: #0c0d11; }
QToolTip { background: #202129; color: #ededf1; border: 1px solid #2f3039;
           border-radius: 6px; padding: 5px 8px; }

/* ── Scroll bars: thin, quiet, no arrows ───────────────────────────── */
QScrollArea          { border: none; background: transparent; }
QScrollBar:vertical  { background: transparent; width: 10px; border: none; margin: 2px; }
QScrollBar::handle:vertical { background: #2f3039; border-radius: 3px; min-height: 32px; }
QScrollBar::handle:vertical:hover { background: #454652; }
QScrollBar:horizontal { background: transparent; height: 10px; border: none; margin: 2px; }
QScrollBar::handle:horizontal { background: #2f3039; border-radius: 3px; min-width: 32px; }
QScrollBar::handle:horizontal:hover { background: #454652; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ── Splitter handle ───────────────────────────────────────────────── */
QSplitter::handle { background: #23242c; }

/* ── Menus ─────────────────────────────────────────────────────────── */
QMenuBar             { background: #0c0d11; color: #ededf1; padding: 2px 0; }
QMenuBar::item       { padding: 4px 10px; border-radius: 4px; }
QMenuBar::item:selected { background: #18191f; }
QMenu                { background: #18191f; color: #ededf1; border: 1px solid #2f3039;
                        border-radius: 10px; padding: 5px; }
QMenu::item          { padding: 7px 18px; border-radius: 6px; }
QMenu::item:selected { background: #8b5cf6; color: white; }
QMenu::item:disabled { color: #5d606e; }
QMenu::separator     { height: 1px; background: #2f3039; margin: 4px 8px; }

/* ── Status bar ────────────────────────────────────────────────────── */
QStatusBar           { background: #0c0d11; color: #5d606e; font-size: 11px;
                        border-top: 1px solid #23242c; padding-left: 8px; }
QStatusBar::item     { border: none; }
QSizeGrip            { width: 0; height: 0; }

/* ── Line edits ────────────────────────────────────────────────────── */
QLineEdit {
    background: #18191f;
    border: 1px solid #23242c;
    border-radius: 7px;
    padding: 4px 10px;
    color: #ededf1;
    selection-background-color: #8b5cf6;
}
QLineEdit:hover  { border-color: #2f3039; }
QLineEdit:focus  { border-color: #8b5cf6; background: #0c0d11; }
QLineEdit:disabled { background: #111217; color: #5d606e; border-color: #18191f; }

/* ── ComboBox ──────────────────────────────────────────────────────── */
QComboBox {
    background: #18191f;
    border: 1px solid #23242c;
    border-radius: 7px;
    padding: 4px 10px;
    color: #ededf1;
}
QComboBox:focus  { border-color: #8b5cf6; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox::down-arrow { image: none; width: 0; }
QComboBox QAbstractItemView {
    background: #18191f;
    border: 1px solid #2f3039;
    selection-background-color: #8b5cf6;
    color: #ededf1;
    border-radius: 8px;
    padding: 4px;
    outline: none;
}

/* ── Buttons (default) ─────────────────────────────────────────────── */
QPushButton {
    background: #18191f;
    color: #ededf1;
    border: 1px solid #23242c;
    border-radius: 7px;
    padding: 6px 14px;
}
QPushButton:hover   { background: #202129; border-color: #2f3039; }
QPushButton:pressed { background: #111217; }
QPushButton:disabled { color: #5d606e; }
QPushButton:default { border-color: #8b5cf6; }

/* ── Tables (dialogs) ──────────────────────────────────────────────── */
QTableWidget {
    background: #0c0d11;
    gridline-color: #18191f;
    border: none;
    color: #ededf1;
    selection-background-color: #1a1627;
    selection-color: #ededf1;
}
QTableWidget::item { padding: 2px 6px; }

QHeaderView::section {
    background: #0c0d11;
    color: #5d606e;
    padding: 5px 8px;
    border: none;
    border-bottom: 1px solid #23242c;
    font-weight: 600;
    font-size: 10px;
}

QMessageBox QLabel { color: #ededf1; }
QCheckBox { color: #ededf1; spacing: 8px; }
"""


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("TrackTag")
    app.setApplicationDisplayName("TrackTag")
    app.setOrganizationName("TrackTag")
    app.setStyle("Fusion")
    app.setPalette(_dark_palette())
    app.setStyleSheet(STYLESHEET)

    # macOS: set process/dock name via NSBundle
    try:
        from Foundation import NSBundle
        NSBundle.mainBundle().infoDictionary()["CFBundleName"] = "TrackTag"
    except Exception:
        pass

    if os.path.exists(_ICON_PATH):
        # Add macOS-standard padding (~10% each side) so icon matches other Dock icons
        try:
            from PIL import Image
            import io as _io
            _img = Image.open(_ICON_PATH).convert("RGBA")
            _s = _img.size[0]
            _pad = int(_s * 0.10)
            _canvas = Image.new("RGBA", (_s + 2 * _pad, _s + 2 * _pad), (0, 0, 0, 0))
            _canvas.paste(_img, (_pad, _pad))
            _buf = _io.BytesIO()
            _canvas.save(_buf, "PNG")
            _pix = __import__('PyQt6.QtGui', fromlist=['QPixmap']).QPixmap()
            _pix.loadFromData(_buf.getvalue())
            app.setWindowIcon(QIcon(_pix))
        except Exception:
            app.setWindowIcon(QIcon(_ICON_PATH))

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

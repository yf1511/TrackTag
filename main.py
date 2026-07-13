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
    p.setColor(QPalette.ColorRole.Window,          QColor(28, 28, 30))    # #1c1c1e
    p.setColor(QPalette.ColorRole.Base,            QColor(18, 18, 18))
    p.setColor(QPalette.ColorRole.AlternateBase,   QColor(28, 28, 30))
    # Text
    p.setColor(QPalette.ColorRole.WindowText,      QColor(235, 235, 245))
    p.setColor(QPalette.ColorRole.Text,            QColor(235, 235, 245))
    p.setColor(QPalette.ColorRole.BrightText,      QColor(255, 255, 255))
    # Buttons
    p.setColor(QPalette.ColorRole.Button,          QColor(44, 44, 46))
    p.setColor(QPalette.ColorRole.ButtonText,      QColor(235, 235, 245))
    # Highlights
    p.setColor(QPalette.ColorRole.Highlight,       QColor(124, 58, 237))   # #7c3aed
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    # Links
    p.setColor(QPalette.ColorRole.Link,            QColor(124, 58, 237))
    # Tooltips
    p.setColor(QPalette.ColorRole.ToolTipBase,     QColor(44, 44, 46))
    p.setColor(QPalette.ColorRole.ToolTipText,     QColor(235, 235, 245))
    # Disabled
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       QColor(99, 99, 102))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(99, 99, 102))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(99, 99, 102))
    return p


STYLESHEET = """
/* ── Global ───────────────────────────────────────────────────────── */
* { font-family: -apple-system, "SF Pro Text", "Helvetica Neue", Arial, sans-serif;
    font-size: 12px; }

QMainWindow, QDialog { background: #1c1c1e; }

/* ── Scroll areas ──────────────────────────────────────────────────── */
QScrollArea          { border: none; background: transparent; }
QScrollBar:vertical  { background: #1c1c1e; width: 8px; border: none; }
QScrollBar::handle:vertical { background: #3a3a3c; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

/* ── Sidebar splitter handle ───────────────────────────────────────── */
QSplitter::handle { background: #2c2c2e; }

/* ── Menu bar ──────────────────────────────────────────────────────── */
QMenuBar             { background: #1c1c1e; color: #ebebf5; padding: 2px 0; }
QMenuBar::item       { padding: 4px 10px; border-radius: 4px; }
QMenuBar::item:selected { background: #2c2c2e; }
QMenu                { background: #2c2c2e; color: #ebebf5; border: 1px solid #3a3a3c;
                        border-radius: 8px; padding: 4px 0; }
QMenu::item          { padding: 6px 22px; }
QMenu::item:selected { background: #7c3aed; border-radius: 4px; color: white; }
QMenu::separator     { height: 1px; background: #3a3a3c; margin: 4px 10px; }

/* ── Status bar ────────────────────────────────────────────────────── */
QStatusBar           { background: #1c1c1e; color: #636366; font-size: 11px;
                        border-top: 1px solid #2c2c2e; }

/* ── Line edits ────────────────────────────────────────────────────── */
QLineEdit {
    background: #2c2c2e;
    border: 1px solid #3a3a3c;
    border-radius: 6px;
    padding: 4px 8px;
    color: #ebebf5;
    selection-background-color: #7c3aed;
}
QLineEdit:focus  { border-color: #7c3aed; background: #1d1230; }
QLineEdit:disabled { background: #1c1c1e; color: #636366; border-color: #2c2c2e; }

/* ── ComboBox (genre picker) ───────────────────────────────────────── */
QComboBox {
    background: #2c2c2e;
    border: 1px solid #3a3a3c;
    border-radius: 6px;
    padding: 4px 8px;
    color: #ebebf5;
}
QComboBox:focus  { border-color: #7c3aed; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox::down-arrow { image: none; width: 0; }
QComboBox QAbstractItemView {
    background: #2c2c2e;
    border: 1px solid #3a3a3c;
    selection-background-color: #7c3aed;
    color: #ebebf5;
    border-radius: 6px;
    padding: 2px;
}

/* ── Buttons (default style) ───────────────────────────────────────── */
QPushButton {
    background: #2c2c2e;
    color: #ebebf5;
    border: 1px solid #3a3a3c;
    border-radius: 6px;
    padding: 5px 14px;
}
QPushButton:hover   { background: #3a3a3c; }
QPushButton:pressed { background: #1c1c1e; }
QPushButton:disabled { color: #636366; }

/* ── File table ────────────────────────────────────────────────────── */
QTableWidget {
    background: #111111;
    alternate-background-color: #1a1a1a;
    gridline-color: #242424;
    border: none;
    color: #e5e5ea;
    selection-background-color: #7c3aed;
    selection-color: white;
}
QTableWidget::item { padding: 2px 6px; }
QTableWidget::item:selected { background: #7c3aed; color: white; }

QHeaderView::section {
    background: #1c1c1e;
    color: #8e8e93;
    padding: 5px 8px;
    border: none;
    border-right: 1px solid #2c2c2e;
    border-bottom: 1px solid #2c2c2e;
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
QHeaderView::section:first { border-left: none; }

/* ── Cover search dialog ───────────────────────────────────────────── */
QDialog { background: #1c1c1e; }
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

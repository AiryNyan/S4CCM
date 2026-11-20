from __future__ import annotations

import os
import subprocess

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QComboBox, QMessageBox, QWidget

from .. import categories as cats
from ..config import app_dir

GROUP_COLORS = {
    "overrides": "#c49a52", "cas": "#c97a93", "bb": "#6fa483", "tuning": "#7d8fc4",
    "animations": "#9b86bd", cats.SCRIPT_MOD: "#5e9dbc", cats.UNSORTED: "#8f8b82",
}
STATUS_LABEL = {"active": "사용 중", "disabled": "비활성", "quarantine": "충돌 테스트로 잠시 뺌"}

THEMES = {
    "dark": dict(
        window="#1b1a18", sidebar="#171614", surface="#232220", base="#1f1e1c", alt="#232220",
        input="#161514", border="#302e2b", border_soft="#282725", border_strong="#4a4743",
        hover="#2b2a27", pressed="#33312e", button="#262523",
        text="#ebe9e4", muted="#9b978e", faint="#66635d",
        accent="#5bb974", accent_hover="#6cc684", on_accent="#0e1a11", sel="#26382c",
        danger="#e0736a", danger_soft="#3a2523", warn="#e0ac4b",
        scroll="#3a3835", scroll_hover="#4d4a46", track="#2b2a27",
    ),
    "light": dict(
        window="#f5f4f0", sidebar="#eeede8", surface="#fbfaf7", base="#ffffff", alt="#faf9f6",
        input="#ffffff", border="#dfddd6", border_soft="#ebe9e3", border_strong="#b9b6ad",
        hover="#ebe9e3", pressed="#e1dfd8", button="#ffffff",
        text="#1e1d1a", muted="#6c6961", faint="#a5a299",
        accent="#2e8b4f", accent_hover="#277a45", on_accent="#ffffff", sel="#dcefe2",
        danger="#c2453c", danger_soft="#f8e4e1", warn="#a46c12",
        scroll="#cfccc4", scroll_hover="#b9b6ad", track="#e6e4de",
    ),
}
_theme = "dark"

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")
_FALLBACK_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Malgun Gothic"]
_families: list[str] | None = None


def ui_families() -> list[str]:
    global _families
    if _families is None:
        loaded: list[str] = []
        if os.path.isdir(FONT_DIR):
            for name in sorted(os.listdir(FONT_DIR)):
                if name.lower().endswith((".ttf", ".otf")):
                    fid = QFontDatabase.addApplicationFont(os.path.join(FONT_DIR, name))
                    for fam in QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []:
                        if fam not in loaded:
                            loaded.append(fam)
        nanum = [f for f in loaded if "nanum" in f.lower().replace(" ", "")]
        _families = nanum[:1] + _FALLBACK_FAMILIES
    return _families


def uses_nanum() -> bool:
    return "nanum" in ui_families()[0].lower().replace(" ", "")

_icon_cache: dict[str, QIcon] = {}
_pixmap_cache: dict[tuple[str, int], QPixmap] = {}
_placeholder_cache: dict[str, QPixmap] = {}


def tok(name: str) -> str:
    return THEMES[_theme][name]


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def category_path_label(key: str) -> str:
    g = cats.group_of(key)
    glabel = dict(cats.GROUPS).get(g, "")
    lbl = cats.label(key)
    return lbl if g in (key, cats.SCRIPT_MOD, cats.UNSORTED) or lbl == glabel else f"{glabel.split(' ')[0]} › {lbl}"


def _blend(fg: str, bg: str, amount: float) -> QColor:
    a, b = QColor(fg), QColor(bg)
    return QColor(round(a.red() * amount + b.red() * (1 - amount)),
                  round(a.green() * amount + b.green() * (1 - amount)),
                  round(a.blue() * amount + b.blue() * (1 - amount)))


def placeholder(group: str, size: int = 160) -> QPixmap:
    key = f"{_theme}:{group}:{size}"
    if key in _placeholder_cache:
        return _placeholder_cache[key]
    color = GROUP_COLORS.get(group, tok("border_strong"))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    m = max(2, size // 16)
    p.setBrush(_blend(color, tok("surface"), 0.16))
    p.setPen(_blend(color, tok("surface"), 0.35))
    p.drawRoundedRect(m, m, size - 2 * m - 1, size - 2 * m - 1, size / 12, size / 12)
    if not group:
        pass
    elif size >= 48:
        p.setPen(_blend(color, tok("text"), 0.8))
        p.setFont(QFont(ui_families(), max(7, size // 14), QFont.DemiBold))
        text = {"cas": "CAS", "bb": "건축/구매", "tuning": "게임 모드", "animations": "포즈",
                cats.SCRIPT_MOD: "스크립트", "overrides": "오버레이", cats.UNSORTED: "미분류"}.get(group, "미분류")
        p.drawText(pm.rect(), Qt.AlignCenter, text)
    else:
        p.setBrush(QColor(color))
        p.setPen(Qt.NoPen)
        d = size / 4
        p.drawEllipse(size / 2 - d / 2, size / 2 - d / 2, d, d)
    p.end()
    _placeholder_cache[key] = pm
    return pm


def item_icon(item) -> QIcon:
    path = item.display_thumb
    if path and os.path.exists(path):
        ic = _icon_cache.get(path)
        if ic is None:
            ic = QIcon(QPixmap(path))
            _icon_cache[path] = ic
        return ic
    return QIcon(placeholder(cats.group_of(item.category)))


def item_pixmap(item, size: int) -> QPixmap:
    path = item.display_thumb
    if path and os.path.exists(path):
        pm = _pixmap_cache.get((path, size))
        if pm is None:
            pm = QPixmap(path)
            if not pm.isNull():
                pm = pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                _pixmap_cache[(path, size)] = pm
        if not pm.isNull():
            return pm
    return placeholder(cats.group_of(item.category), size)


def forget_icon(path: str | None) -> None:
    if path:
        _icon_cache.pop(path, None)
        for k in [k for k in _pixmap_cache if k[0] == path]:
            del _pixmap_cache[k]


class CategoryCombo(QComboBox):
    def __init__(self, parent: QWidget | None = None, include_script: bool = False):
        super().__init__(parent)
        self.setMaxVisibleItems(25)
        if include_script:
            self.addItem("스크립트 모드", cats.SCRIPT_MOD)
        for c in cats.CATEGORIES:
            self.addItem(category_path_label(c.key), c.key)

    def set_key(self, key: str) -> None:
        i = self.findData(key)
        if i >= 0:
            self.setCurrentIndex(i)

    def key(self) -> str:
        return self.currentData()


def reveal(path: str) -> None:
    if os.path.isdir(path):
        os.startfile(path)
    elif os.path.exists(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])


def confirm(parent: QWidget, title: str, text: str) -> bool:
    return QMessageBox.question(parent, title, text, QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes


def _write_assets(t: dict) -> str:
    d = os.path.join(app_dir(), "ui", _theme)
    os.makedirs(d, exist_ok=True)
    check = f'<polyline points="4,8.5 7,11.5 12.5,5" fill="none" stroke="{t["on_accent"]}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
    svgs = {
        "check_off": f'<rect x="1" y="1" width="14" height="14" rx="4" fill="{t["input"]}" stroke="{t["border_strong"]}" stroke-width="1.2"/>',
        "check_on": f'<rect x="1" y="1" width="14" height="14" rx="4" fill="{t["accent"]}" stroke="{t["accent"]}" stroke-width="1.2"/>{check}',
        "check_off_dis": f'<rect x="1" y="1" width="14" height="14" rx="4" fill="{t["hover"]}" stroke="{t["border"]}" stroke-width="1.2"/>',
        "check_on_dis": f'<rect x="1" y="1" width="14" height="14" rx="4" fill="{t["faint"]}" stroke="{t["faint"]}" stroke-width="1.2"/>{check}',
        "radio_off": f'<circle cx="8" cy="8" r="6.5" fill="{t["input"]}" stroke="{t["border_strong"]}" stroke-width="1.2"/>',
        "radio_on": f'<circle cx="8" cy="8" r="6.5" fill="{t["input"]}" stroke="{t["accent"]}" stroke-width="1.2"/><circle cx="8" cy="8" r="3.5" fill="{t["accent"]}"/>',
        "radio_dis": f'<circle cx="8" cy="8" r="6.5" fill="{t["hover"]}" stroke="{t["border"]}" stroke-width="1.2"/>',
        "down": f'<polyline points="4,6 8,10 12,6" fill="none" stroke="{t["muted"]}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
        "up": f'<polyline points="4,10 8,6 12,10" fill="none" stroke="{t["muted"]}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
        "right": f'<polyline points="6,4 10,8 6,12" fill="none" stroke="{t["muted"]}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    }
    for name, body in svgs.items():
        with open(os.path.join(d, name + ".svg"), "w", encoding="utf-8") as f:
            f.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">{body}</svg>')
    return d.replace("\\", "/")


_QSS = """
* {{ outline: 0; }}
QMainWindow, QDialog, QMessageBox {{ background: {window}; }}
QWidget {{ color: {text}; }}
QLabel#h1 {{ font-size: 15pt; font-weight: bold; }}
QLabel#h2 {{ font-size: 12pt; font-weight: bold; }}
QLabel#percent {{ color: {text}; font-weight: bold; }}
QProgressBar#gauge {{ max-height: 8px; min-height: 8px; border-radius: 4px; }}
QProgressBar#gauge::chunk {{ border-radius: 4px; }}
QLabel#muted {{ color: {muted}; }}
QLabel#warn {{ color: {warn}; }}
QLabel#section {{ color: {muted}; font-weight: bold; }}
QFrame#card {{ background: {surface}; border: 1px solid {border}; border-radius: 10px; }}

QToolBar {{ background: {window}; border: none; border-bottom: 1px solid {border}; padding: 6px 10px; spacing: 2px; }}
QToolBar::separator {{ width: 1px; background: {border}; margin: 6px 8px; }}
QToolBar QToolButton {{ padding: 6px 12px; border-radius: 6px; border: 1px solid transparent; }}
QToolBar QToolButton:hover {{ background: {hover}; }}
QToolBar QToolButton:pressed {{ background: {pressed}; }}
QToolButton#accent {{ background: {accent}; color: {on_accent}; font-weight: bold; }}
QToolButton#accent:hover {{ background: {accent_hover}; }}

QTreeWidget#sidebar {{ background: {sidebar}; border: none; border-right: 1px solid {border}; padding: 8px 6px; }}
QTreeWidget#sidebar::item {{ height: 30px; border: none; padding-left: 2px; }}
QTreeWidget#sidebar::item:hover {{ background: {hover}; }}
QTreeWidget#sidebar::item:selected {{ background: {sel}; color: {text}; }}

QPushButton {{ background: {button}; border: 1px solid {border}; border-radius: 6px; padding: 6px 14px; min-height: 18px; }}
QPushButton:hover {{ background: {hover}; border-color: {border_strong}; }}
QPushButton:pressed {{ background: {pressed}; }}
QPushButton:disabled {{ color: {faint}; background: transparent; border-color: {border_soft}; }}
QPushButton#primary {{ background: {accent}; border-color: {accent}; color: {on_accent}; font-weight: bold; }}
QPushButton#primary:hover {{ background: {accent_hover}; border-color: {accent_hover}; }}
QPushButton#primary:disabled {{ background: {hover}; border-color: {hover}; color: {faint}; }}
QPushButton#danger {{ background: transparent; border-color: {danger}; color: {danger}; }}
QPushButton#danger:hover {{ background: {danger_soft}; }}
QPushButton#danger:disabled {{ border-color: {border_soft}; color: {faint}; }}

QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{
    background: {input}; border: 1px solid {border}; border-radius: 6px; padding: 5px 8px;
    selection-background-color: {accent}; selection-color: {on_accent};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{ border-color: {accent}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QPlainTextEdit:disabled {{ color: {faint}; background: {window}; }}
QLineEdit[readOnly="true"] {{ color: {muted}; }}
QComboBox {{ padding-right: 24px; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url({assets}/down.svg); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
    background: {surface}; border: 1px solid {border}; border-radius: 6px; padding: 4px;
    selection-background-color: {sel}; selection-color: {text};
}}
QSpinBox {{ padding-right: 20px; }}
QSpinBox::up-button, QSpinBox::down-button {{ border: none; width: 18px; background: transparent; }}
QSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-arrow {{ image: url({assets}/up.svg); width: 10px; height: 10px; }}
QSpinBox::down-arrow {{ image: url({assets}/down.svg); width: 10px; height: 10px; }}

QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QAbstractItemView::indicator {{ width: 16px; height: 16px; }}
QCheckBox::indicator:unchecked, QAbstractItemView::indicator:unchecked {{ image: url({assets}/check_off.svg); }}
QCheckBox::indicator:checked, QAbstractItemView::indicator:checked {{ image: url({assets}/check_on.svg); }}
QCheckBox::indicator:unchecked:disabled, QAbstractItemView::indicator:unchecked:disabled {{ image: url({assets}/check_off_dis.svg); }}
QCheckBox::indicator:checked:disabled, QAbstractItemView::indicator:checked:disabled {{ image: url({assets}/check_on_dis.svg); }}
QRadioButton::indicator {{ width: 16px; height: 16px; }}
QRadioButton::indicator:unchecked {{ image: url({assets}/radio_off.svg); }}
QRadioButton::indicator:checked {{ image: url({assets}/radio_on.svg); }}
QRadioButton::indicator:disabled {{ image: url({assets}/radio_dis.svg); }}

QAbstractItemView {{
    background: {base}; alternate-background-color: {alt}; border: 1px solid {border}; border-radius: 8px;
    selection-background-color: {sel}; selection-color: {text}; gridline-color: {border_soft};
}}
QTreeWidget#sidebar, QListView#grid {{ border-radius: 0; }}
QAbstractItemView::item {{ padding: 2px 4px; }}
QAbstractItemView::item:hover {{ background: {hover}; }}
QAbstractItemView::item:selected {{ background: {sel}; color: {text}; }}
QListView#grid {{ border: none; background: {window}; }}
QListView#grid::item {{ border-radius: 8px; padding: 6px; }}
QTreeView::branch {{ background: transparent; }}
QTreeView::branch:has-children:closed {{ image: url({assets}/right.svg); }}
QTreeView::branch:has-children:open {{ image: url({assets}/down.svg); }}
QHeaderView {{ background: {base}; border: none; }}
QHeaderView::section {{
    background: {base}; color: {muted}; border: none; border-bottom: 1px solid {border};
    border-right: 1px solid {border_soft}; padding: 6px 8px; font-weight: bold;
}}
QHeaderView::section:last {{ border-right: none; }}
QHeaderView::down-arrow {{ image: url({assets}/down.svg); width: 10px; height: 10px; }}
QHeaderView::up-arrow {{ image: url({assets}/up.svg); width: 10px; height: 10px; }}
QTableCornerButton::section {{ background: {base}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {scroll}; border-radius: 3px; min-height: 30px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: {scroll_hover}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QGroupBox {{
    background: {surface}; border: 1px solid {border}; border-radius: 10px;
    margin-top: 22px; padding: 14px 12px 12px 12px;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 4px; top: 0; padding: 0; color: {muted}; font-weight: bold; }}
QGroupBox:disabled {{ color: {faint}; }}

QTabWidget::pane {{ border: 1px solid {border}; border-radius: 8px; background: {base}; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {muted}; padding: 7px 14px; border: none; border-bottom: 2px solid transparent; margin-right: 4px; }}
QTabBar::tab:selected {{ color: {text}; border-bottom-color: {accent}; }}
QTabBar::tab:hover {{ color: {text}; }}

QProgressBar {{ background: {track}; border: none; border-radius: 3px; max-height: 6px; min-height: 6px; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}

QStatusBar {{ background: {window}; border-top: 1px solid {border}; color: {muted}; }}
QStatusBar QLabel {{ color: {muted}; padding: 2px 4px; }}
QStatusBar::item {{ border: none; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 8px; }}

QToolTip {{ background: {surface}; color: {text}; border: 1px solid {border}; padding: 6px 8px; }}
QMenu {{ background: {surface}; border: 1px solid {border}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 28px 6px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {sel}; color: {text}; }}
QMenu::item:disabled {{ color: {faint}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 4px 6px; }}
QMenu::right-arrow {{ image: url({assets}/right.svg); width: 12px; height: 12px; }}
"""


def resolve_theme(theme: str) -> str:
    if theme in THEMES:
        return theme
    scheme = QApplication.styleHints().colorScheme()
    return "light" if scheme == Qt.ColorScheme.Light else "dark"


def apply_theme(app: QApplication, theme: str) -> None:
    global _theme
    _theme = resolve_theme(theme)
    t = THEMES[_theme]
    app.setStyle("Fusion")
    font = QFont()
    font.setFamilies(ui_families())
    font.setPointSizeF(9.5)
    app.setFont(font)
    pal = QPalette()
    for role, key in ((QPalette.Window, "window"), (QPalette.WindowText, "text"), (QPalette.Base, "base"),
                      (QPalette.AlternateBase, "alt"), (QPalette.ToolTipBase, "surface"),
                      (QPalette.ToolTipText, "text"), (QPalette.Text, "text"), (QPalette.Button, "button"),
                      (QPalette.ButtonText, "text"), (QPalette.Highlight, "accent"),
                      (QPalette.HighlightedText, "on_accent"), (QPalette.PlaceholderText, "faint"),
                      (QPalette.Link, "accent"), (QPalette.Mid, "border"), (QPalette.Dark, "border_strong")):
        pal.setColor(role, QColor(t[key]))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        pal.setColor(QPalette.Disabled, role, QColor(t["faint"]))
    app.setPalette(pal)
    app.setStyleSheet(_QSS.format(assets=_write_assets(t), **t))


ICON_SIZE = QSize(128, 128)

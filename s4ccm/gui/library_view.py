from __future__ import annotations

import datetime as dt
import os

from PySide6.QtCore import (QAbstractTableModel, QEvent, QItemSelectionModel, QModelIndex, QPointF,
                            QRect, QRectF, QSize, QSortFilterProxyModel, Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListView, QMenu, QPlainTextEdit,
                               QPushButton, QSplitter, QStackedWidget, QStyle, QStyledItemDelegate,
                               QTableView, QVBoxLayout, QWidget)

from .. import categories as cats
from ..db import Item
from ..library import Library
from .common import (STATUS_LABEL, CategoryCombo, category_path_label, confirm,
                     forget_icon, human_size, item_icon, item_pixmap, placeholder, reveal, tok)

COLUMNS = ["이름", "분류", "상태", "크기", "추가일", "위치"]
ItemRole = Qt.UserRole + 1
INSTALLED = "installed"


class ItemModel(QAbstractTableModel):
    check_toggled = Signal(object, bool)

    def __init__(self):
        super().__init__()
        self.items: list[Item] = []

    def set_items(self, items: list[Item]) -> None:
        self.beginResetModel()
        self.items = items
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        it = self.items[index.row()]
        col = index.column()
        if role == ItemRole:
            return it
        if role == Qt.DisplayRole:
            return [it.name, category_path_label(it.category), STATUS_LABEL.get(it.status, it.status),
                    human_size(it.size), dt.datetime.fromtimestamp(it.added_at).strftime("%Y-%m-%d"),
                    it.rel_path][col]
        if role == Qt.UserRole:
            return [it.name.lower(), category_path_label(it.category), it.status, it.size, it.added_at,
                    it.rel_path][col]
        if role == Qt.DecorationRole and col == 0:
            return item_icon(it)
        if role == Qt.CheckStateRole and col == 0:
            return Qt.Checked if it.status == "active" else Qt.Unchecked
        if role == Qt.ToolTipRole:
            tip = f"{it.name}\n{category_path_label(it.category)}\n{it.reason}"
            if it.memo:
                tip += f"\n\n메모: {it.memo[:200]}"
            return tip
        if role == Qt.ForegroundRole and it.status != "active":

            return QColor(tok("faint"))
        return None

    def flags(self, index):
        f = super().flags(index)
        if index.column() == 0 and self.items[index.row()].status != "quarantine":
            f |= Qt.ItemIsUserCheckable
        return f

    def setData(self, index, value, role=Qt.EditRole):
        if role == Qt.CheckStateRole and index.column() == 0:
            self.check_toggled.emit(self.items[index.row()], Qt.CheckState(value) == Qt.Checked)
        return False


class ItemFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.setSortRole(Qt.UserRole)
        self.group: str | None = None
        self.category: str | None = None
        self.status: str | None = INSTALLED
        self.text = ""

    def set_scope(self, group=None, category=None, status=INSTALLED):
        self.group, self.category, self.status = group, category, status
        self.invalidateFilter()

    def set_text(self, text: str):
        self.text = text.strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        it: Item = self.sourceModel().items[row]
        if self.status == INSTALLED:
            if it.status not in ("active", "disabled"):
                return False
        elif self.status and it.status != self.status:
            return False
        if self.category and it.category != self.category:
            return False
        if self.group and cats.group_of(it.category) != self.group:
            return False
        if self.text:
            hay = " ".join([it.name, it.memo, it.rel_path, " ".join(it.meta.get("casp_names", []))]).lower()
            return all(t in hay for t in self.text.split())
        return True


THUMB_SIZES = (64, 80, 96, 112, 128, 160, 192, 224, 256)


class GridDelegate(QStyledItemDelegate):
    PAD = 8

    def __init__(self, parent=None, size: int = 128):
        super().__init__(parent)
        self.icon = size

    @property
    def cell(self) -> QSize:
        return QSize(self.icon + 28, self.icon + 48)

    def sizeHint(self, option, index) -> QSize:
        return self.cell

    def _image_rect(self, item, rect: QRect) -> QRect:
        box = QRect(rect.x() + (rect.width() - self.icon) // 2, rect.y() + self.PAD, self.icon, self.icon)
        pm = item_pixmap(item, self.icon)
        w, h = pm.width() / pm.devicePixelRatio(), pm.height() / pm.devicePixelRatio()
        return QRect(box.x() + (self.icon - int(w)) // 2, box.y() + (self.icon - int(h)) // 2, int(w), int(h))

    def _badge_rect(self, item, rect: QRect) -> QRect:
        img = self._image_rect(item, rect)
        size = max(14, min(24, self.icon // 6))
        inset = max(3, self.icon // 22)
        return QRect(img.x() + inset, img.y() + inset, size, size)

    def paint(self, p: QPainter, option, index) -> None:
        item = index.data(ItemRole)
        if item is None:
            return
        rect = option.rect.adjusted(2, 2, -2, -2)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if option.state & QStyle.State_Selected:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(tok("sel")))
            p.drawRoundedRect(QRectF(rect), 8, 8)
        elif option.state & QStyle.State_MouseOver:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(tok("hover")))
            p.drawRoundedRect(QRectF(rect), 8, 8)

        active = item.status == "active"
        img = self._image_rect(item, rect)
        p.setOpacity(1.0 if active else 0.35)
        p.drawPixmap(img, item_pixmap(item, self.icon))
        p.setOpacity(1.0)

        if not active:
            text = "비활성" if item.status == "disabled" else "테스트 중"
            f = QFont(option.font)
            f.setPointSizeF(8)
            p.setFont(f)
            fm = p.fontMetrics()
            w, h = fm.horizontalAdvance(text) + 12, fm.height() + 4
            chip = QRectF(img.left() + 4, img.bottom() - h - 4, w, h)
            p.setPen(QPen(QColor(tok("border_strong")), 1))
            p.setBrush(QColor(tok("surface")))
            p.drawRoundedRect(chip, h / 2, h / 2)
            p.setPen(QColor(tok("muted")))
            p.drawText(chip, Qt.AlignCenter, text)

        if item.meta.get("hq"):
            f = QFont(option.font)
            f.setPointSizeF(max(6.5, min(9.0, self.icon / 18)))
            f.setBold(True)
            p.setFont(f)
            fm = p.fontMetrics()
            w, h = fm.horizontalAdvance("HQ") + 8, fm.height() + 1
            inset = max(3, self.icon // 22)
            chip = QRectF(img.right() - w - inset + 1, img.bottom() - h - inset + 1, w, h)
            p.setPen(Qt.NoPen)
            bg = QColor(20, 20, 20)
            bg.setAlpha(190)
            p.setBrush(bg)
            p.drawRoundedRect(chip, 4, 4)
            p.setPen(QColor("#ffffff"))
            p.drawText(chip, Qt.AlignCenter, "HQ")

        if item.status != "quarantine":
            b = QRectF(self._badge_rect(item, rect))
            if active:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(tok("accent")))
                p.drawEllipse(b)
                path = QPainterPath(QPointF(b.x() + b.width() * 0.28, b.y() + b.height() * 0.53))
                path.lineTo(b.x() + b.width() * 0.44, b.y() + b.height() * 0.69)
                path.lineTo(b.x() + b.width() * 0.73, b.y() + b.height() * 0.36)
                pen = QPen(QColor(tok("on_accent")), 2.2)
                pen.setCapStyle(Qt.RoundCap)
                pen.setJoinStyle(Qt.RoundJoin)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
            else:
                bg = QColor(tok("window"))
                bg.setAlpha(215)
                p.setBrush(bg)
                p.setPen(QPen(QColor(tok("muted")), 1.6))
                p.drawEllipse(b.adjusted(0.8, 0.8, -0.8, -0.8))

        name_rect = QRect(rect.x() + 6, rect.y() + self.PAD + self.icon + 6, rect.width() - 12, 20)
        p.setFont(option.font)
        p.setPen(QColor(tok("text") if active else tok("faint")))
        p.drawText(name_rect, Qt.AlignHCenter | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(item.name, Qt.ElideRight, name_rect.width()))
        p.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        if event.type() not in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick):
            return False
        item = index.data(ItemRole)
        if item is None or item.status == "quarantine" or event.button() != Qt.LeftButton:
            return False
        rect = option.rect.adjusted(2, 2, -2, -2)
        if not self._badge_rect(item, rect).adjusted(-3, -3, 3, 3).contains(event.position().toPoint()):
            return False
        if event.type() == QEvent.MouseButtonRelease:
            model.setData(index, Qt.Unchecked if item.status == "active" else Qt.Checked, Qt.CheckStateRole)
        return True


class ElideLabel(QLabel):
    def __init__(self, mode=Qt.ElideMiddle):
        super().__init__()
        self._full = ""
        self._mode = mode
        self.setMinimumWidth(40)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)

    def set_full(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._elide()

    def _elide(self) -> None:
        super().setText(self.fontMetrics().elidedText(self._full, self._mode, max(10, self.width())))

    def minimumSizeHint(self) -> QSize:
        return QSize(40, super().minimumSizeHint().height())


class ThumbLabel(QLabel):
    dropped = Signal(str)

    def __init__(self):
        super().__init__()
        self.setFixedSize(220, 220)
        self.setAlignment(Qt.AlignCenter)
        self.setAcceptDrops(True)
        self.setToolTip("이미지를 끌어다 놓으면 미리보기를 바꿀 수 있습니다.")

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls:
            self.dropped.emit(urls[0].toLocalFile())


class DetailPanel(QFrame):
    changed = Signal()
    request = Signal(str)

    def __init__(self, lib: Library):
        super().__init__()
        self.lib = lib
        self.items: list[Item] = []
        self.setObjectName("card")
        self.setMinimumWidth(300)
        self.setMaximumWidth(380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        self.thumb = ThumbLabel()
        self.thumb.dropped.connect(self._set_thumb_file)
        lay.addWidget(self.thumb, 0, Qt.AlignHCenter)
        row = QHBoxLayout()
        b_img = QPushButton("이미지 변경…")
        b_img.clicked.connect(self._pick_thumb)
        b_paste = QPushButton("붙여넣기")
        b_paste.setToolTip("클립보드의 이미지로 미리보기 지정")
        b_paste.clicked.connect(self._paste_thumb)
        row.addWidget(b_img)
        row.addWidget(b_paste)
        lay.addLayout(row)

        self.title = QLabel()
        self.title.setObjectName("h1")
        self.title.setWordWrap(True)
        lay.addWidget(self.title)
        self.warn = QLabel()
        self.warn.setObjectName("warn")
        self.warn.setWordWrap(True)
        lay.addWidget(self.warn)

        form = QFormLayout()
        self.cat = CategoryCombo()
        self.cat.activated.connect(self._category_changed)
        self.reason = QLabel()
        self.reason.setObjectName("muted")
        self.reason.setWordWrap(True)
        self.info = QLabel()
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)
        self.memo = QPlainTextEdit()
        self.memo.setPlaceholderText("메모를 입력하세요")
        form.addRow("분류", self.cat)
        form.addRow("", self.reason)
        lay.addLayout(form)
        lay.addWidget(self.info)
        self.facts = QWidget()
        grid = QGridLayout(self.facts)
        grid.setContentsMargins(0, 4, 0, 4)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(5)
        grid.setColumnStretch(1, 1)
        self.fact_rows: dict[str, tuple[QLabel, ElideLabel]] = {}
        for i, (key, label) in enumerate((("status", "상태"), ("size", "크기"), ("quality", "화질"),
                                          ("folder", "위치"), ("inner", "내부 이름"), ("files", "구성 파일"))):
            k = QLabel(label)
            k.setObjectName("muted")
            v = ElideLabel()
            grid.addWidget(k, i, 0, Qt.AlignLeft | Qt.AlignTop)
            grid.addWidget(v, i, 1)
            self.fact_rows[key] = (k, v)
        lay.addWidget(self.facts)
        lay.addWidget(QLabel("메모"))
        lay.addWidget(self.memo, 1)

        self._save_timer = QTimer(self, singleShot=True, interval=600)
        self._save_timer.timeout.connect(self._save_note)
        self.memo.textChanged.connect(lambda: self._save_timer.start() if self.memo.hasFocus() else None)

        btns = QHBoxLayout()
        self.b_open = QPushButton("폴더 열기")
        self.b_open.clicked.connect(lambda: self.request.emit("reveal"))
        self.b_on = QPushButton("활성화")
        self.b_on.setToolTip("Mods 폴더로 되돌려 게임에서 쓰이게 합니다.")
        self.b_on.clicked.connect(lambda: self.request.emit("enable"))
        self.b_off = QPushButton("비활성화")
        self.b_off.setToolTip("Mods 밖(S4CCM_Disabled)으로 옮겨 게임에서 빠지게 합니다. 파일은 지워지지 않습니다.")
        self.b_off.clicked.connect(lambda: self.request.emit("disable"))
        self.b_del = QPushButton("휴지통으로")
        self.b_del.setObjectName("danger")
        self.b_del.clicked.connect(lambda: self.request.emit("trash"))
        for b in (self.b_open, self.b_on, self.b_off, self.b_del):
            btns.addWidget(b)
        lay.addLayout(btns)
        self.show_items([])

    def show_items(self, items: list[Item]) -> None:
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save_note()
        self.items = items
        one = items[0] if len(items) == 1 else None
        for w in (self.cat, self.memo, self.b_open, self.b_on, self.b_off, self.b_del):
            w.setEnabled(bool(items))
        for w in (self.memo, self.b_open):
            w.setEnabled(one is not None)
        self.memo.blockSignals(True)
        if not items:
            self.thumb.setPixmap(placeholder("", 200))
            self.title.setText("CC를 선택하세요")
            self.reason.clear()
            self.info.setText("목록에서 CC를 선택하면 자세한 정보가 표시됩니다.\n이름 앞 체크를 끄면 비활성화, 켜면 활성화됩니다.\n파일이나 압축파일을 창에 끌어다 놓으면 추가됩니다.")
            self._show_facts({})
            self.warn.hide()
            self.memo.clear()
            self.cat.setCurrentIndex(-1)
        elif one:
            pm = QPixmap(one.display_thumb) if one.display_thumb and os.path.exists(one.display_thumb) else \
                placeholder(cats.group_of(one.category), 200)
            self.thumb.setPixmap(pm.scaled(210, 210, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.title.setText(one.name)
            self.cat.setEnabled(one.kind == "package")
            self.cat.set_key(one.category) if one.kind == "package" else self.cat.setCurrentIndex(-1)
            src = "직접 지정" if one.user_set else f"자동 판정 (신뢰도 {one.confidence:.0%})"
            self.reason.setText(f"{src} · {one.reason}" if one.kind == "package" else one.reason)
            names = [n.strip() for n in one.meta.get("casp_names", []) if n.strip()]
            tex = one.meta.get("tex")
            self.info.clear()
            self._show_facts({
                "status": STATUS_LABEL.get(one.status, one.status),
                "size": human_size(one.size),
                "quality": (("HQ 고화질" if one.meta.get("hq") else "일반") + f" · {tex[0]}×{tex[1]}") if tex else "",
                "folder": (one.rel_path.rsplit("/", 1)[0] if one.kind == "package" or "." in one.rel_path.rsplit("/", 1)[-1]
                           else one.rel_path),
                "inner": names[0] if names else "",
                "files": f"{len(one.members)}개" if one.kind == "script" else "",
            }, tooltip={"folder": one.rel_path})
            warn = one.meta.get("warning")
            self.warn.setVisible(bool(warn))
            self.warn.setText(warn or "")
            self.memo.setPlainText(one.memo)
        else:
            self.thumb.setPixmap(placeholder("", 200))
            self.title.setText(f"{len(items)}개 선택됨")
            self.cat.setEnabled(all(i.kind == "package" for i in items))
            self.cat.setCurrentIndex(-1)
            self.reason.setText("분류를 고르면 선택한 CC가 모두 해당 폴더로 이동합니다.")
            self.info.clear()
            self._show_facts({"size": human_size(sum(i.size for i in items))})
            self.warn.hide()
            self.memo.clear()
        self.memo.blockSignals(False)
        if items:
            self.b_on.setEnabled(any(i.status == "disabled" for i in items))
            self.b_off.setEnabled(any(i.status == "active" for i in items))

    def _show_facts(self, values: dict, tooltip: dict | None = None) -> None:
        self.facts.setVisible(any(values.values()))
        self.info.setVisible(bool(self.info.text()))
        for key, (k, v) in self.fact_rows.items():
            text = values.get(key, "")
            k.setVisible(bool(text))
            v.setVisible(bool(text))
            v.set_full(text)
            if tooltip and key in tooltip:
                v.setToolTip(tooltip[key])

    def _save_note(self) -> None:
        if len(self.items) != 1:
            return
        it = self.items[0]
        it.memo = self.memo.toPlainText()
        self.lib.save_note(it, it.memo)

    def _category_changed(self) -> None:
        key = self.cat.key()
        if not key or not self.items:
            return
        for it in self.items:
            if it.kind == "package" and it.category != key:
                self.lib.set_category(it, key)
        self.changed.emit()

    def _set_thumb_file(self, path: str) -> None:
        if len(self.items) == 1 and path:
            forget_icon(self.items[0].user_thumb)
            res = self.lib.set_user_thumb(self.items[0], path)
            if res:
                self.items[0].user_thumb = res
                forget_icon(res)
                self.show_items(self.items)
                self.changed.emit()

    def _pick_thumb(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "미리보기 이미지 선택", "", "이미지 (*.png *.jpg *.jpeg *.webp *.bmp)")
        self._set_thumb_file(path)

    def _paste_thumb(self) -> None:
        img = QGuiApplication.clipboard().image()
        if img.isNull() or len(self.items) != 1:
            return
        from PySide6.QtCore import QBuffer, QIODevice
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "PNG")
        forget_icon(self.items[0].user_thumb)
        res = self.lib.set_user_thumb(self.items[0], bytes(buf.data()))
        if res:
            self.items[0].user_thumb = res
            forget_icon(res)
            self.show_items(self.items)
            self.changed.emit()


class LibraryPage(QWidget):
    counts_changed = Signal()
    status_requested = Signal(list, str)

    def __init__(self, lib: Library):
        super().__init__()
        self.lib = lib
        self.model = ItemModel()
        self.proxy = ItemFilter()
        self.proxy.setSourceModel(self.model)
        self.proxy.sort(0)
        self.model.check_toggled.connect(self._check_toggled)

        self.grid = QListView()
        self.grid.setObjectName("grid")
        self.grid.setSpacing(4)
        self.grid.setViewMode(QListView.IconMode)
        self.grid_delegate = GridDelegate(self.grid, self._saved_thumb_size())
        self.grid.setItemDelegate(self.grid_delegate)
        self._apply_thumb_size()
        self.grid.viewport().installEventFilter(self)
        self.grid.setMouseTracking(True)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setWordWrap(True)
        self.grid.setTextElideMode(Qt.ElideRight)
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.setModel(self.proxy)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        for col, width in enumerate((320, 170, 90, 80, 90)):
            self.table.setColumnWidth(col, width)
        self.table.setIconSize(QSize(32, 32))
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.sortByColumn(0, Qt.AscendingOrder)

        self.views = QStackedWidget()
        self.views.addWidget(self.grid)
        self.views.addWidget(self.table)

        self.detail = DetailPanel(lib)
        self.detail.changed.connect(self.reload)

        for v in (self.grid, self.table):
            v.selectionModel().selectionChanged.connect(self._selection_changed)
            v.setContextMenuPolicy(Qt.CustomContextMenu)
            v.customContextMenuRequested.connect(self._context_menu)
            v.doubleClicked.connect(lambda _i: self.detail.request.emit("reveal"))

        self.header = QLabel()
        self.header.setObjectName("h1")
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.header)
        cl.addWidget(self.views)

        split = QSplitter()
        split.addWidget(center)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 1)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 12, 12)
        lay.addWidget(split)

    def _saved_thumb_size(self) -> int:
        size = self.lib.config.thumb_size
        return min(THUMB_SIZES, key=lambda s: abs(s - size))

    def _apply_thumb_size(self) -> None:
        size = self.grid_delegate.icon
        self.grid.setIconSize(QSize(size, size))
        self.grid.setGridSize(self.grid_delegate.cell + QSize(4, 4))
        self.grid.doItemsLayout()
        self.grid.viewport().update()

    def zoom(self, steps: int) -> None:
        i = THUMB_SIZES.index(self.grid_delegate.icon)
        size = THUMB_SIZES[max(0, min(len(THUMB_SIZES) - 1, i + steps))]
        if size == self.grid_delegate.icon:
            return
        current = self.grid.currentIndex()
        self.grid_delegate.icon = size
        self._apply_thumb_size()
        if current.isValid():
            self.grid.scrollTo(current)
        self.lib.config.thumb_size = size
        self.lib.config.save()

    def eventFilter(self, obj, e) -> bool:
        if obj is self.grid.viewport() and e.type() == QEvent.Wheel and e.modifiers() & Qt.ControlModifier:
            dy = e.angleDelta().y()
            if dy:
                self.zoom(1 if dy > 0 else -1)
            return True
        return super().eventFilter(obj, e)

    def current_view(self) -> QAbstractItemView:
        return self.views.currentWidget()

    def set_view_mode(self, mode: str) -> None:
        self.views.setCurrentWidget(self.grid if mode == "grid" else self.table)
        self._selection_changed()

    def selected(self) -> list[Item]:
        v = self.current_view()
        rows = {self.proxy.mapToSource(i).row() for i in v.selectionModel().selectedIndexes()}
        return [self.model.items[r] for r in sorted(rows)]

    def _check_toggled(self, item: Item, on: bool) -> None:
        sel = self.selected()
        items = sel if any(i.id == item.id for i in sel) else [item]
        self.status_requested.emit(items, "active" if on else "disabled")

    def _selection_changed(self, *_):
        self.detail.show_items(self.selected())

    def set_scope(self, title: str, **scope) -> None:
        self.proxy.set_scope(**scope)
        self.header.setText(f"{title}  ({self.proxy.rowCount()})")
        self.current_view().clearSelection()
        self._title = title

    def set_search(self, text: str) -> None:
        self.proxy.set_text(text)
        self.header.setText(f"{getattr(self, '_title', '')}  ({self.proxy.rowCount()})")

    def reload(self) -> None:
        sel = {i.id for i in self.selected()}
        self.model.set_items(self.lib.items())
        self.header.setText(f"{getattr(self, '_title', '')}  ({self.proxy.rowCount()})")
        v = self.current_view()
        if sel:

            for row, it in enumerate(self.model.items):
                if it.id in sel:
                    idx = self.proxy.mapFromSource(self.model.index(row, 0))
                    if idx.isValid():
                        v.selectionModel().select(idx, QItemSelectionModel.Select | QItemSelectionModel.Rows)
        self._selection_changed()
        self.counts_changed.emit()

    def _context_menu(self, pos) -> None:
        items = self.selected()
        if not items:
            return
        m = QMenu(self)
        m.addAction("폴더에서 보기", lambda: self.detail.request.emit("reveal")).setEnabled(len(items) == 1)
        m.addAction("활성화", lambda: self.detail.request.emit("enable")).setEnabled(
            any(i.status == "disabled" for i in items))
        m.addAction("비활성화 (Mods 밖으로)", lambda: self.detail.request.emit("disable")).setEnabled(
            any(i.status == "active" for i in items))
        if all(i.kind == "package" for i in items):
            sub = m.addMenu("분류 변경")
            for g, glabel in cats.GROUPS:
                if g == cats.SCRIPT_MOD:
                    continue
                cs = [c for c in cats.CATEGORIES if c.group == g]
                target = sub.addMenu(glabel) if len(cs) > 1 else sub
                for c in cs:
                    target.addAction(c.label, lambda k=c.key: self._set_category(items, k))
        m.addSeparator()
        m.addAction("휴지통으로 삭제", lambda: self.detail.request.emit("trash"))
        m.exec(self.current_view().viewport().mapToGlobal(pos))

    def _set_category(self, items: list[Item], key: str) -> None:
        for it in items:
            self.lib.set_category(it, key)
        self.reload()

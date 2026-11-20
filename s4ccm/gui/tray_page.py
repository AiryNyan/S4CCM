from __future__ import annotations

import html
import os

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap

from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QMessageBox,
                               QSizePolicy,
                               QPushButton, QScrollArea, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from .. import saves, tray
from ..config import thumbs_dir
from ..library import Library
from .common import STATUS_LABEL, category_path_label, item_icon, placeholder, reveal, tok
from .loading import run_with_loading

EntryRole = Qt.UserRole + 1


def _title(text: str, sub: str) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 6)
    t = QLabel(text)
    t.setObjectName("h1")
    s = QLabel(sub)
    s.setObjectName("muted")
    s.setWordWrap(True)
    lay.addWidget(t)
    lay.addWidget(s)
    return w


def _muted(text: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("muted")
    lbl.setWordWrap(True)
    return lbl


class SkillBars(QWidget):
    NAME_W, CELL, GAP, ROW_H, COLS = 78, 9, 2, 22, 2

    def __init__(self, skills: list[tray.TraySkill]):
        super().__init__()
        self.skills = skills
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(self._rows() * self.ROW_H + 4)

    def _rows(self) -> int:
        return (len(self.skills) + self.COLS - 1) // self.COLS

    def _col_width(self) -> int:
        return self.NAME_W + 8 + 10 * (self.CELL + self.GAP) + 14

    def sizeHint(self) -> QSize:
        return QSize(self.COLS * self._col_width(), self._rows() * self.ROW_H + 4)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rows = (len(self.skills) + self.COLS - 1) // self.COLS
        col_w = max(self._col_width(), self.width() // self.COLS)
        fm = p.fontMetrics()
        for i, sk in enumerate(self.skills):
            col, row = divmod(i, rows) if rows else (0, 0)
            x0, y0 = col * col_w, row * self.ROW_H + 2
            name = fm.elidedText(sk.name, Qt.ElideRight, self.NAME_W)
            p.setPen(QColor(tok("text")))
            p.drawText(QRectF(x0, y0, self.NAME_W, self.ROW_H - 4), Qt.AlignRight | Qt.AlignVCenter, name)
            cells = sk.max_level or 10
            for c in range(cells):
                x = x0 + self.NAME_W + 8 + c * (self.CELL + self.GAP)
                rect = QRectF(x, y0 + (self.ROW_H - 4 - 12) / 2, self.CELL, 12)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(tok("accent") if c < sk.level else tok("track")))
                p.drawRoundedRect(rect, 2, 2)
        p.end()


class TrayPage(QWidget):
    def __init__(self, lib: Library):
        super().__init__()
        self.lib = lib
        self.entries: list[tray.TrayEntry] = []
        self._loaded_dir = None
        self._id_map: dict[int, set[str]] | None = None
        self.pending: dict[str, dict] = {}
        self._select_key: str | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(_title("트레이", "게임 라이브러리에 저장해 둔 가족, 집, 방을 보여 줍니다. "
                                      "항목을 고르면 그 가족이나 집에 쓰인 CC를 확인할 수 있습니다."))

        bar = QHBoxLayout()
        self.kind = QComboBox()
        for label, value in (("전체", 0), ("가족", tray.TYPE_HOUSEHOLD), ("집", tray.TYPE_LOT), ("방", tray.TYPE_ROOM)):
            self.kind.addItem(label, value)
        self.kind.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.kind.currentIndexChanged.connect(self._fill)
        self.search = QLineEdit()
        self.search.setPlaceholderText("이름, 제작자, 심 이름 검색")
        self.search.setFixedWidth(260)
        self.search.textChanged.connect(self._fill)
        self.count = _muted()
        b_open = QPushButton("Tray 폴더 열기")
        b_open.clicked.connect(lambda: reveal(self._tray_dir()))
        b_reload = QPushButton("새로고침")
        b_reload.clicked.connect(lambda: self.refresh(force=True))
        bar.addWidget(self.kind)
        bar.addWidget(self.search)
        bar.addWidget(self.count, 1)
        bar.addWidget(b_open)
        bar.addWidget(b_reload)
        lay.addLayout(bar)

        self.list = QListWidget()
        self.list.setObjectName("grid")
        self.list.setViewMode(QListView.IconMode)
        self.list.setIconSize(QSize(180, 120))
        self.list.setGridSize(QSize(208, 168))
        self.list.setResizeMode(QListView.Adjust)
        self.list.setMovement(QListView.Static)
        self.list.setWordWrap(True)
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.currentItemChanged.connect(lambda cur, _prev: self._show(cur.data(EntryRole) if cur else None))

        self.detail = QWidget()
        dl = QVBoxLayout(self.detail)
        dl.setContentsMargins(14, 14, 14, 14)
        dl.setSpacing(8)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumHeight(200)
        self.name = QLabel()
        self.name.setObjectName("h1")
        self.name.setWordWrap(True)
        self.sub = _muted()
        self.desc = QLabel()
        self.desc.setWordWrap(True)
        self.actions = QWidget()
        al = QHBoxLayout(self.actions)
        al.setContentsMargins(0, 0, 0, 0)
        self.b_thumb = QPushButton("썸네일 바꾸기…")
        self.b_thumb.setToolTip("게임 라이브러리에 보이는 대표 사진을 바꿉니다.\n[사본으로 저장]을 눌러야 저장됩니다.")
        self.b_thumb.clicked.connect(self._replace_thumb)
        self.b_clean = QPushButton("외형만 남기기")
        self.b_clean.setToolTip(f"지우는 것:{', '.join(tray.CLEARED_LABELS)}\n"
                                f"남기는 것: {', '.join(tray.KEPT_LABELS)}\n"
                                "[사본으로 저장]을 눌러야 저장됩니다.")
        self.b_clean.clicked.connect(self._look_only)
        self.b_export = QPushButton("내보내기…")
        self.b_export.setToolTip("트레이 파일을 zip으로 묶습니다. 사용한 CC를 함께 넣을 수 있습니다.")
        self.b_export.clicked.connect(self._export)
        self.b_rename = QPushButton("이름 바꾸기…")
        self.b_rename.setToolTip("가족 이름이나 부지 이름을 바꿉니다.\n[사본으로 저장]을 눌러야 저장됩니다.")
        self.b_rename.clicked.connect(self._rename)
        self.b_desc = QPushButton("설명 바꾸기…")
        self.b_desc.setToolTip("게임 라이브러리에 보이는 설명을 바꿉니다.\n[사본으로 저장]을 눌러야 저장됩니다.")
        self.b_desc.clicked.connect(self._edit_desc)
        self.b_delete = QPushButton("지우기")
        self.b_delete.setObjectName("danger")
        self.b_delete.setToolTip("이 항목의 트레이 파일을 모두 휴지통으로 보냅니다.")
        self.b_delete.clicked.connect(self._delete)
        al.addWidget(self.b_thumb)
        al.addWidget(self.b_rename)
        al.addWidget(self.b_desc)
        al.addWidget(self.b_clean)
        al.addWidget(self.b_export)
        al.addStretch(1)
        al.addWidget(self.b_delete)
        self.pending_bar = QFrame()
        self.pending_bar.setObjectName("card")
        pl = QHBoxLayout(self.pending_bar)
        pl.setContentsMargins(10, 8, 10, 8)
        self.pending_label = QLabel()
        self.pending_label.setWordWrap(True)
        self.b_save = QPushButton("사본으로 저장")
        self.b_save.setObjectName("primary")
        self.b_save.setToolTip("원본은 그대로 두고, 바꾼 내용을 새 트레이 항목으로 저장합니다.")
        self.b_save.clicked.connect(self._save_copy)
        self.b_discard = QPushButton("변경 취소")
        self.b_discard.clicked.connect(self._discard)
        pl.addWidget(self.pending_label, 1)
        pl.addWidget(self.b_discard)
        pl.addWidget(self.b_save)
        self.sims_title = QLabel()
        self.sims_title.setObjectName("section")
        self.sims_box = QVBoxLayout()
        self.sims_box.setSpacing(10)
        self.cc_title = QLabel()
        self.cc_title.setObjectName("section")
        self.cc = QTreeWidget()
        self.cc.setHeaderLabels(["이름", "분류", "상태"])
        self.cc.setRootIsDecorated(False)
        self.cc.setIconSize(QSize(32, 32))
        self.cc.header().setSectionResizeMode(QHeaderView.Interactive)
        self.cc.header().setStretchLastSection(True)
        self.cc.setColumnWidth(0, 300)
        self.cc.setColumnWidth(1, 150)
        self.cc.setMinimumHeight(280)
        self.cc.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.cc.itemDoubleClicked.connect(self._open_cc)
        self.cc_hint = _muted("더블 클릭하면 CC 파일이 있는 폴더를 엽니다. 내 Mods에 없는 CC나 게임 기본 아이템은 목록에 나오지 않습니다.")
        for w in (self.image, self.name, self.sub, self.actions, self.pending_bar, self.desc, self.sims_title):
            dl.addWidget(w)
        dl.addLayout(self.sims_box)
        dl.addWidget(self.cc_title)
        dl.addWidget(self.cc_hint)
        dl.addWidget(self.cc, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.detail)
        scroll.setMinimumWidth(420)

        split = QSplitter()
        split.addWidget(self.list)
        split.addWidget(scroll)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        split.setSizes([520, 560])
        lay.addWidget(split, 1)
        self._show(None)

    def _tray_dir(self) -> str:
        return os.path.join(self.lib.sims_dir, "Tray")

    def refresh(self, force: bool = False) -> None:
        tray_dir = self._tray_dir()
        if not force and tray_dir == self._loaded_dir:
            self._id_map = self.lib.db.res_id_map()
            cur = self.list.currentItem()
            self._show(cur.data(EntryRole) if cur else None)
            return
        if not os.path.isdir(tray_dir):
            self.entries = []
            self._loaded_dir = tray_dir
            self._fill()
            return

        def work(progress):
            entries = tray.load_tray(tray_dir)
            saves_dir = os.path.join(self.lib.sims_dir, "saves")
            for i, e in enumerate(entries):
                progress(i, len(entries), e.name, "미리보기 그림과 관계를 준비하는 중")
                e.thumb = tray.entry_thumb(e, thumbs_dir())
                for sim in e.sims:
                    sim.relations_checked = True
                    try:
                        sim.relations = saves.find_relations(saves_dir, sim.first_name, sim.last_name)
                    except Exception:
                        sim.relations = None
            return entries, self.lib.db.res_id_map()

        try:
            self.entries, self._id_map = run_with_loading(self, "트레이를 읽는 중", work)
        except Exception as ex:
            QMessageBox.warning(self, "트레이", f"트레이를 읽는 중 오류: {ex}")
            self.entries, self._id_map = [], None
        self._loaded_dir = tray_dir
        self._fill()

    def _fill(self) -> None:
        kind = self.kind.currentData()
        terms = self.search.text().strip().lower().split()
        current = self.list.currentItem().data(EntryRole).key if self.list.currentItem() else None
        if self._select_key:
            current, self._select_key = self._select_key, None
        self.list.clear()
        shown = 0
        for e in self.entries:
            if kind and e.kind != kind:
                continue
            hay = " ".join([e.name, e.creator, e.description] + [s.full_name for s in e.sims]).lower()
            if terms and not all(t in hay for t in terms):
                continue
            thumb = self._thumb_of(e)
            pm = QPixmap(thumb) if thumb else placeholder("", 120)
            label = f"{e.name}\n{e.kind_label}" + (" · 저장 안 됨" if e.key in self.pending else "")
            it = QListWidgetItem(pm.scaled(180, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation), label)
            it.setData(EntryRole, e)
            it.setToolTip(f"{e.name}\n{e.kind_label} · {e.creator}")
            self.list.addItem(it)
            if e.key == current:
                self.list.setCurrentItem(it)
            shown += 1
        if not os.path.isdir(self._tray_dir()):
            self.count.setText("Tray 폴더가 없습니다.")
        else:
            self.count.setText(f"{shown}개" + (f" / 전체 {len(self.entries)}개" if shown != len(self.entries) else ""))
        if self.list.currentItem() is None:
            self._show(None)

    def _clear_sims(self) -> None:
        while self.sims_box.count():
            w = self.sims_box.takeAt(0).widget()
            if w is not None:
                w.deleteLater()

    def _show(self, e: tray.TrayEntry | None) -> None:
        self._clear_sims()
        self.cc.clear()
        has = e is not None
        self.current = e
        for w in (self.sims_title, self.cc_title, self.cc, self.cc_hint, self.desc, self.actions):
            w.setVisible(has)
        self.pending_bar.hide()
        if not has:
            self.image.setPixmap(placeholder("", 160))
            self.name.setText("항목을 선택하세요")
            self.sub.setText("왼쪽에서 가족, 집, 방을 고르면 자세한 정보와 사용한 CC가 표시됩니다.")
            self.sims_title.hide()
            return
        pend = self.pending.get(e.key, {})
        thumb = self._thumb_of(e)
        pm = QPixmap(thumb) if thumb else placeholder("", 160)
        self.image.setPixmap(pm.scaled(420, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        lines = []
        changed = [label for key, label in (("name", "이름"), ("thumb", "썸네일"), ("desc", "설명")) if key in pend]
        if changed:
            lines.append(f"<b>바뀌는 것</b>&nbsp;&nbsp;{' · '.join(changed)}")
        if pend.get("clean"):
            lines.append(f"<b>지워지는 것</b>&nbsp;&nbsp;{html.escape(', '.join(tray.CLEARED_LABELS))}")
        self.pending_bar.setVisible(bool(lines))
        self.pending_label.setText("<br>".join(lines))
        self.name.setText(pend.get("name") or e.name or "(이름 없음)")
        bits = [e.kind_label]
        if e.creator:
            bits.append(f"제작: {e.creator}")
        if e.saved_at:
            bits.append(e.saved_at.strftime("%Y-%m-%d %H:%M 저장"))
        self.sub.setText(" · ".join(bits))
        desc = pend["desc"] if "desc" in pend else e.description
        self.desc.setText(desc)
        self.desc.setVisible(bool(desc))

        self.sims_title.setVisible(bool(e.sims))
        self.sims_title.setText(f"구성원 {len(e.sims)}명")
        portraits = tray.sim_thumbs(e, thumbs_dir())
        for i, sim in enumerate(e.sims):
            self.sims_box.addWidget(self._sim_card(sim, portraits[i] if i < len(portraits) else None, pend.get("clean")))

        self.b_thumb.setEnabled(bool(e.main_images()))
        self.b_clean.setVisible(e.kind == tray.TYPE_HOUSEHOLD)
        self.b_clean.setEnabled(bool(e.sims) and not pend.get("clean"))
        cc = tray.used_cc(self.lib, e, self._id_map)
        self.current_cc = cc
        self.cc_title.setText(f"사용한 CC {len(cc)}개")
        for it in cc:
            row = QTreeWidgetItem([it.name, category_path_label(it.category), STATUS_LABEL.get(it.status, it.status)])
            row.setIcon(0, item_icon(it))
            row.setData(0, Qt.UserRole, it)
            row.setToolTip(0, it.rel_path)
            self.cc.addTopLevelItem(row)

    def _sim_card(self, sim: tray.TraySim, portrait: str | None, cleaned: bool = False) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        grid = QGridLayout(card)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setHorizontalSpacing(12)
        pic = QLabel()
        pm = QPixmap(portrait) if portrait else placeholder("", 72)
        pic.setPixmap(pm.scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        grid.addWidget(pic, 0, 0, 5, 1, Qt.AlignTop)
        name = QLabel(sim.full_name or "(이름 없음)")
        name.setObjectName("h2")
        grid.addWidget(name, 0, 1)
        row = 1
        for label, value in (("", sim.summary), ("특성", ", ".join(sim.traits)), ("열망", sim.aspiration),
                             ("좋아함", ", ".join(sim.likes)), ("싫어함", ", ".join(sim.dislikes))):
            if not value:
                continue
            text = QLabel(f"<b>{label}</b>&nbsp;&nbsp;{html.escape(value)}" if label else html.escape(value))
            text.setTextFormat(Qt.RichText)
            text.setWordWrap(True)
            if not label:
                text.setObjectName("muted")
            grid.addWidget(text, row, 1)
            row += 1
        known = [sk for sk in sim.skills if sk.name]
        if cleaned:
            note = QLabel("<b>스킬</b>&nbsp;&nbsp;지워짐")
            note.setTextFormat(Qt.RichText)
            grid.addWidget(note, row, 1)
            row += 1
            known = []
        if known:
            grid.addWidget(QLabel("<b>스킬</b>"), row, 1)
            row += 1
            grid.addWidget(SkillBars(known), row, 1)
            row += 1
        if not cleaned and any(not sk.name for sk in sim.skills):
            grid.addWidget(_muted(f"표에 없는 스킬 {sum(1 for sk in sim.skills if not sk.name)}개 (모드 스킬 등)"), row, 1)
            row += 1
        rel = getattr(sim, "relations", None)
        if rel is not None:
            source, rels = rel
            head = QLabel(f"<b>관계</b>&nbsp;&nbsp;{len(rels)}명"
                          f"<span style='color:{tok('muted')}'>&nbsp;·&nbsp;세이브 {html.escape(os.path.basename(source))}</span>")
            head.setTextFormat(Qt.RichText)
            grid.addWidget(head, row, 1)
            row += 1
            table = QTreeWidget()
            table.setHeaderLabels(["상대", "우정", "로맨스"])
            table.setRootIsDecorated(False)
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.setColumnWidth(0, 200)
            table.setColumnWidth(1, 80)
            for r in rels:
                fmt = lambda v: "-" if v is None else f"{v:.1f}"
                it = QTreeWidgetItem([r.name, fmt(r.friendship), fmt(r.romance)])
                for c in (1, 2):
                    it.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
                if r.friendship is not None and r.friendship < 0:
                    it.setForeground(1, QColor(tok("danger")))
                table.addTopLevelItem(it)
            table.setFixedHeight(min(240, 34 + 26 * max(1, len(rels))))
            grid.addWidget(table, row, 1)
            row += 1
        elif not getattr(sim, "relations_checked", False):
            pass
        else:
            grid.addWidget(_muted("관계: 세이브 파일에서 이 심을 찾지 못했습니다."), row, 1)
            row += 1
        grid.setColumnStretch(1, 1)
        return card

    def _thumb_of(self, e: tray.TrayEntry) -> str | None:
        return self.pending.get(e.key, {}).get("thumb") or getattr(e, "thumb", None)

    def _stage(self, e: tray.TrayEntry, **change) -> None:
        self.pending.setdefault(e.key, {}).update(change)
        self._fill()
        self._show(e)

    def _replace_thumb(self) -> None:
        e = getattr(self, "current", None)
        if e is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "새 썸네일 이미지 선택", "", "이미지 (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not path:
            return
        if QPixmap(path).isNull():
            QMessageBox.warning(self, "썸네일 바꾸기", "이 이미지는 열 수 없습니다.")
            return
        self._stage(e, thumb=path)

    def _look_only(self) -> None:
        e = getattr(self, "current", None)
        if e is not None:
            self._stage(e, clean=True)

    def _discard(self) -> None:
        e = getattr(self, "current", None)
        if e is not None and self.pending.pop(e.key, None) is not None:
            self._fill()
            self._show(e)

    def _stage_text(self, e: tray.TrayEntry, key: str, text: str, original: str) -> None:
        pend = self.pending.get(e.key, {})
        if text == original:
            if key in pend:
                pend.pop(key)
                if not pend:
                    self.pending.pop(e.key, None)
                self._fill()
                self._show(e)
            return
        self._stage(e, **{key: text})

    def _rename(self) -> None:
        e = getattr(self, "current", None)
        if e is None:
            return
        pend = self.pending.get(e.key, {})
        text, ok = QInputDialog.getText(self, "이름 바꾸기", "새 이름:", QLineEdit.Normal, pend.get("name") or e.name)
        if not ok:
            return
        text = text.strip()
        if not text:
            QMessageBox.information(self, "이름 바꾸기", "이름을 비워 둘 수는 없습니다.")
            return
        self._stage_text(e, "name", text, e.name)

    def _edit_desc(self) -> None:
        e = getattr(self, "current", None)
        if e is None:
            return
        pend = self.pending.get(e.key, {})
        text, ok = QInputDialog.getMultiLineText(self, "설명 바꾸기", "새 설명:",
                                                 pend["desc"] if "desc" in pend else e.description)
        if ok:
            self._stage_text(e, "desc", text.strip(), e.description)

    def _delete(self) -> None:
        e = getattr(self, "current", None)
        if e is None:
            return
        box = QMessageBox(self)
        box.setWindowTitle("지우기")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"'{e.name or e.key}' ({e.kind_label})을(를) 지울까요?")
        box.setInformativeText(f"트레이 파일 {len(e.files)}개를 휴지통으로 보냅니다. 휴지통에서 되살릴 수 있습니다.")
        yes = box.addButton("휴지통으로 보내기", QMessageBox.AcceptRole)
        box.addButton("취소", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        try:
            tray.delete_entry(e)
        except Exception as ex:
            QMessageBox.warning(self, "지우기", f"지우지 못했습니다: {ex}")
        self.pending.pop(e.key, None)
        self.refresh(force=True)

    def _save_copy(self) -> tray.TrayEntry | None:
        e = getattr(self, "current", None)
        pend = self.pending.get(e.key) if e is not None else None
        if not pend:
            return None
        name = pend.get("name") or e.name
        try:
            result = run_with_loading(self, "사본으로 저장하는 중", lambda progress: tray.save_copy(
                e, self._tray_dir(), name if name != e.name else "", pend.get("thumb"), bool(pend.get("clean")),
                pend.get("desc")))
        except Exception as ex:
            QMessageBox.warning(self, "사본으로 저장", f"저장하지 못했습니다. 원본은 바뀌지 않았습니다.\n\n{ex}")
            return None
        self.pending.pop(e.key, None)
        self._select_key = result.key
        self.refresh(force=True)
        done = [f"새 항목 '{name}'을(를) 만들었습니다. 원본은 그대로 있습니다."]
        if pend.get("clean"):
            done.append("스킬, 직업 같은 플레이 기록을 지우고 외형만 남겼습니다.")
        if result.thumbs:
            done.append("새 썸네일을 넣었습니다.")
        if "desc" in pend:
            done.append("설명을 바꿨습니다.")
        done.append("게임을 다시 켜면 라이브러리에 보입니다.")
        QMessageBox.information(self, "사본으로 저장", "\n".join(done))
        return next((x for x in self.entries if x.key == result.key), None)

    def _export(self) -> None:
        e = getattr(self, "current", None)
        if e is None:
            return
        if e.key in self.pending:
            ask = QMessageBox(self)
            ask.setWindowTitle("내보내기")
            ask.setIcon(QMessageBox.Question)
            ask.setText("저장하지 않은 변경이 있습니다.")
            ask.setInformativeText("바꾼 내용을 사본으로 저장한 뒤 그 사본을 내보낼까요?")
            save_first = ask.addButton("저장하고 내보내기", QMessageBox.AcceptRole)
            save_first.setObjectName("primary")
            save_first.style().polish(save_first)
            original = ask.addButton("원본 내보내기", QMessageBox.AcceptRole)
            ask.addButton("취소", QMessageBox.RejectRole)
            ask.exec()
            if ask.clickedButton() is save_first:
                e = self._save_copy()
                if e is None:
                    return
            elif ask.clickedButton() is not original:
                return
        cc = tray.used_cc(self.lib, e, self._id_map)
        box = QMessageBox(self)
        box.setWindowTitle("내보내기")
        box.setIcon(QMessageBox.Question)
        box.setText(f"'{e.export_name}'을(를) zip 파일로 내보냅니다.")
        box.setInformativeText(f"사용한 CC {len(cc)}개를 함께 넣을까요?\n"
                               "zip 안에서 트레이 파일은 Tray 폴더에, CC는 Mods 폴더에 나눠 담습니다.\n"
                               "받는 사람은 zip을 S4CCM에 끌어다 놓거나, 두 폴더를 심즈 4 폴더에 넣으면 됩니다.")
        with_cc = box.addButton(f"CC 포함 ({len(cc)}개)", QMessageBox.AcceptRole)
        with_cc.setObjectName("primary")
        with_cc.style().polish(with_cc)
        without = box.addButton("CC 없이", QMessageBox.AcceptRole)
        box.addButton("취소", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() not in (with_cc, without):
            return
        out_dir = QFileDialog.getExistingDirectory(self, "zip 파일을 저장할 폴더 선택", os.path.expanduser("~/Desktop"))
        if not out_dir:
            return
        try:
            path = run_with_loading(self, "내보내는 중", lambda progress: tray.export_zip(
                e, out_dir, cc if box.clickedButton() is with_cc else None, self.lib))
        except Exception as ex:
            QMessageBox.warning(self, "내보내기", f"내보내지 못했습니다: {ex}")
            return
        QMessageBox.information(self, "내보내기", f"저장했습니다:\n{path}")
        reveal(path)

    def _open_cc(self, row: QTreeWidgetItem, _col: int) -> None:
        it = row.data(0, Qt.UserRole)
        if it is not None:
            reveal(self.lib.abs(it.rel_path))

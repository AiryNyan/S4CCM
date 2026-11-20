from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
                               QRadioButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import categories as cats
from .. import paths
from ..importer import MODE_CC, MODE_SCRIPT, ImportPlan
from .common import CategoryCombo, category_path_label, tok


class SetupDialog(QDialog):

    def __init__(self, parent=None, current: str = ""):
        super().__init__(parent)
        self.setWindowTitle("심즈4 모드 폴더 설정")
        self.setMinimumWidth(620)
        self.result_dir = ""
        lay = QVBoxLayout(self)
        title = QLabel("심즈4 모드 폴더 찾기")
        title.setObjectName("h1")
        lay.addWidget(title)

        found = paths.find_sims_folders()
        self.group = QButtonGroup(self)
        if found:
            msg = "모드 폴더를 여러 개 찾았습니다. 사용할 폴더를 고르세요." if len(found) > 1 else "심즈4 모드 폴더를 자동으로 찾았습니다."
            lay.addWidget(QLabel(msg))
            for i, f in enumerate(sorted(found, key=lambda f: -f.cc_count)):
                rb = QRadioButton(f"{f.mods}\n    CC {f.cc_count}개")
                rb.setProperty("path", f.root)
                self.group.addButton(rb, i)
                lay.addWidget(rb)
                if i == 0:
                    rb.setChecked(True)
        else:
            warn = QLabel("심즈4 모드 폴더를 자동으로 찾지 못했습니다. 아래에서 Mods 폴더를 직접 지정하세요.")
            warn.setObjectName("warn")
            warn.setWordWrap(True)
            lay.addWidget(warn)

        self.manual_rb = QRadioButton("직접 지정:")
        self.group.addButton(self.manual_rb, 999)
        row = QHBoxLayout()
        row.addWidget(self.manual_rb)
        self.edit = QLineEdit(current)
        self.edit.setPlaceholderText(r"예: C:\Users\이름\Documents\Electronic Arts\The Sims 4\Mods")
        self.edit.textEdited.connect(lambda _: self.manual_rb.setChecked(True))
        self.edit.textChanged.connect(self._validate)
        row.addWidget(self.edit, 1)
        b = QPushButton("찾아보기…")
        b.clicked.connect(self._browse)
        row.addWidget(b)
        lay.addLayout(row)
        if not found:
            self.manual_rb.setChecked(True)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        hint = QLabel("팁: 폴더를 이 창에 끌어다 놓아도 됩니다.")
        hint.setObjectName("muted")
        lay.addWidget(hint)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.group.buttonToggled.connect(lambda *_: self._validate())
        self.setAcceptDrops(True)
        self._validate()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        p = e.mimeData().urls()[0].toLocalFile()
        if os.path.isdir(p):
            self.edit.setText(p)
            self.manual_rb.setChecked(True)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "심즈4 모드(Mods) 폴더 선택", self.edit.text())
        if d:
            self.edit.setText(d)
            self.manual_rb.setChecked(True)

    def _chosen(self) -> str:
        b = self.group.checkedButton()
        if b is None:
            return ""
        if b is self.manual_rb:
            return paths.normalize_user_choice(self.edit.text()) if self.edit.text().strip() else ""
        return b.property("path")

    def _validate(self):
        d = self._chosen()
        if not d:
            self.status.setText("")
            return
        ok, msg = paths.validate(d)
        self.status.setText(msg + f"\n{os.path.join(d, 'Mods')}")
        self.status.setObjectName("" if ok else "warn")
        self.status.style().polish(self.status)

    def _accept(self):
        d = self._chosen()
        if not d:
            return
        ok, msg = paths.validate(d)
        if not ok:
            from PySide6.QtWidgets import QMessageBox
            if not os.path.isdir(d):
                QMessageBox.warning(self, "폴더 오류", msg)
                return
            if QMessageBox.question(self, "확인", f"{msg}\n\n그래도 이 폴더를 사용할까요?"
                                    + ("\n(Mods 폴더를 새로 만듭니다)" if not os.path.isdir(os.path.join(d, "Mods")) else "")
                                    ) != QMessageBox.Yes:
                return
            os.makedirs(os.path.join(d, "Mods"), exist_ok=True)
        self.result_dir = d
        self.accept()


class ImportDialog(QDialog):

    def __init__(self, plan: ImportPlan, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.setWindowTitle("모드 추가")
        self.resize(1150, 620)
        lay = QVBoxLayout(self)
        t = QLabel("추가할 모드를 확인하세요")
        t.setObjectName("h1")
        lay.addWidget(t)
        sub = QLabel("자동으로 종류를 판정했습니다. 잘못 인식됐다면 '종류'와 '분류'를 직접 바꿀 수 있습니다.\n"
                     "· 스크립트 모드: 압축 안의 파일을 하나의 폴더로 묶어서 넣습니다.\n"
                     "· CC (개별 분류): .package 파일을 각각 분류 폴더(헤어, 상의 등)로 넣습니다.\n"
                     "· 트레이 (가족, 집, 방): Tray 폴더에 넣습니다.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        lay.addWidget(sub)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["파일", "종류 / 분류", "판정 근거", "비고"])
        header = self.tree.header()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        for col, width in enumerate((340, 230, 400)):
            self.tree.setColumnWidth(col, width)
        lay.addWidget(self.tree, 1)

        self._rows: list[tuple] = []
        for g in plan.groups:
            top = QTreeWidgetItem(self.tree)
            top.setText(0, g.name)
            f = top.font(0)
            f.setWeight(QFont.DemiBold)
            top.setFont(0, f)
            top.setText(2, g.mode_reason)
            top.setFirstColumnSpanned(False)
            mode = QComboBox()
            mode.addItem("스크립트 모드 (폴더째 추가)", MODE_SCRIPT)
            mode.addItem("CC (개별 분류)", MODE_CC)
            mode.setCurrentIndex(0 if g.mode == MODE_SCRIPT else 1)
            name_edit = QLineEdit(g.folder)
            name_edit.setPlaceholderText("Mods 바로 아래")
            name_edit.setToolTip("넣을 폴더 이름 (Mods/이름)\n모르는 스크립트 모드는 Scripts 폴더에 모읍니다.")
            if g.packages or g.scripts:
                self.tree.setItemWidget(top, 1, mode)
                self.tree.setItemWidget(top, 3, name_edit)
            else:
                top.setText(1, "트레이")
            tray_rows = []
            for t in g.tray_items:
                tr = QTreeWidgetItem(top)
                tr.setText(0, t.name)
                tr.setFlags(tr.flags() | Qt.ItemIsUserCheckable)
                tr.setCheckState(0, Qt.Checked if t.include else Qt.Unchecked)
                tr.setText(1, f"트레이 · {t.kind_label}")
                tr.setText(2, f"Tray 폴더에 넣습니다 (파일 {len(t.entries)}개)")
                if t.exists:
                    tr.setText(3, "이미 있음 (체크하면 덮어씁니다)")
                    tr.setForeground(3, QColor(tok("warn")))
                tray_rows.append((tr, t))
            children = []
            for e in g.entries:
                if e.kind not in ("package", "script"):
                    continue
                ch = QTreeWidgetItem(top)
                ch.setText(0, e.rel)
                ch.setFlags(ch.flags() | Qt.ItemIsUserCheckable)
                ch.setCheckState(0, Qt.Checked if e.include else Qt.Unchecked)
                combo = None
                if e.kind == "package":
                    combo = CategoryCombo()
                    combo.set_key(e.category)
                    self.tree.setItemWidget(ch, 1, combo)
                    ch.setText(2, f"{e.reason}" + (f" ({e.confidence:.0%})" if e.confidence else ""))
                else:
                    ch.setText(1, "스크립트 (.ts4script)")
                if e.duplicate:
                    ch.setText(3, f"이미 있음: {e.duplicate}")
                    ch.setForeground(3, QColor(tok("warn")))
                elif e.kind == "package" and e.confidence < 0.6:
                    ch.setText(3, "판정 불확실 — 확인 필요")
                    ch.setForeground(3, QColor(tok("warn")))
                children.append((ch, e, combo))
            others = [e for e in g.entries if e.kind not in ("package", "script", "tray")]
            if others:
                o = QTreeWidgetItem(top)
                o.setText(0, f"기타 파일 {len(others)}개 (이미지/설정 등)")
                o.setText(2, "스크립트 모드일 때만 함께 복사됩니다 (이미 있는 설정 파일은 덮어쓰지 않음)")
                o.setForeground(0, QColor(tok("muted")))
            replace_chk = None
            if g.replaces is not None:
                r = QTreeWidgetItem(top)
                replace_chk = QCheckBox("기존 버전 교체")
                replace_chk.setChecked(g.replace)
                self.tree.setItemWidget(r, 0, replace_chk)
                r.setText(1, f"설치됨: {g.replaces.name}")
                note = "옛 .ts4script/.package는 휴지통으로 보내고, 설정 파일(.cfg 등)은 그대로 둡니다."
                if g.known and g.known.keep_note:
                    note += " " + g.known.keep_note
                r.setText(2, note)
                r.setToolTip(2, note)
            for libmod in g.missing_libs:
                w = QTreeWidgetItem(top)
                w.setText(0, f"필요한 라이브러리 없음: {libmod.name}")
                w.setText(2, "설치되어 있지 않거나 비활성화되어 있습니다. 이 모드가 작동하려면 함께 설치해야 합니다." + (f" {libmod.url}" if libmod.url else ""))
                w.setToolTip(2, w.text(2))
                for col in (0, 2):
                    w.setForeground(col, QColor(tok("warn")))
            top.setExpanded(True)
            mode.currentIndexChanged.connect(lambda _i, m=mode, ch=children, n=name_edit: self._mode_changed(m, ch, n))
            self._mode_changed(mode, children, name_edit)
            self._rows.append((g, mode, name_edit, children, replace_chk, tray_rows))

        if plan.errors:
            err = QLabel("\n".join(plan.errors))
            err.setObjectName("warn")
            err.setWordWrap(True)
            lay.addWidget(err)

        bb = QDialogButtonBox()
        ok = bb.addButton("추가", QDialogButtonBox.AcceptRole)
        ok.setObjectName("primary")
        bb.addButton("취소", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    @staticmethod
    def _mode_changed(mode: QComboBox, children, name_edit: QLineEdit) -> None:
        script = mode.currentData() == MODE_SCRIPT
        name_edit.setEnabled(script)
        for ch, e, combo in children:
            if combo is not None:
                combo.setEnabled(not script)

    def _accept(self) -> None:
        for g, mode, name_edit, children, replace_chk, tray_rows in self._rows:
            for tr, t in tray_rows:
                t.include = tr.checkState(0) == Qt.Checked
            g.mode = mode.currentData()
            g.folder = name_edit.text().strip()
            if replace_chk is not None:
                g.replace = replace_chk.isChecked()
            for ch, e, combo in children:
                e.include = ch.checkState(0) == Qt.Checked
                if combo is not None and combo.key() != e.category:
                    e.category = combo.key()
                    e.user_changed = True
            if g.mode == MODE_SCRIPT:
                for e in g.entries:
                    if e.kind not in ("package", "script"):
                        e.include = True
        self.accept()

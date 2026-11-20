from __future__ import annotations

import datetime as dt
import os
import time

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog,
                               QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QSizePolicy, QSpinBox,
                               QTableWidget, QTableWidgetItem, QTabWidget, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import categories as cats
from .. import launcher, paths
from .. import resourcecfg as rc
from ..config import app_dir
from ..bisect import Bisect
from ..duplicates import SAME_FILE, SAME_ITEM, find_duplicates
from ..library import ORGANIZE_LABEL, Library
from .loading import run_with_loading
from .common import (STATUS_LABEL, CategoryCombo, category_path_label, confirm, human_size,
                     item_icon, reveal, tok, uses_nanum)


def _centered_check(checked: bool) -> tuple[QWidget, QCheckBox]:
    cell = QWidget()
    chk = QCheckBox()
    chk.setChecked(checked)
    box = QHBoxLayout(cell)
    box.setContentsMargins(0, 0, 0, 0)
    box.addWidget(chk, 0, Qt.AlignCenter)
    return cell, chk


def _title(text: str, sub: str = "") -> QWidget:
    w = QWidget()
    l = QVBoxLayout(w)
    l.setContentsMargins(0, 0, 0, 6)
    t = QLabel(text)
    t.setObjectName("h1")
    l.addWidget(t)
    if sub:
        s = QLabel(sub)
        s.setObjectName("muted")
        s.setWordWrap(True)
        l.addWidget(s)
    return w


class OrganizePage(QWidget):
    changed = Signal()

    def __init__(self, lib: Library):
        super().__init__()
        self.lib = lib
        self.plans = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(_title("자동 정리", "CC템을 분석하여 자동으로 분류합니다"))
        self.table = QTableWidget(0, 5)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setHorizontalHeaderLabels(["이동", "이름", "분류", "현재 위치 → 이동 위치", "판정 근거"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        for col, width in enumerate((44, 300, 200, 420)):
            self.table.setColumnWidth(col, width)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.checks: list[QCheckBox] = []
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.summary = QLabel()
        row.addWidget(self.summary, 1)
        for text, fn in (("모두 선택", lambda: self._check_all(True)), ("모두 해제", lambda: self._check_all(False)),
                         ("목록 새로고침", self.refresh)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        self.b_apply = QPushButton("자동 정리 시작")
        self.b_apply.setObjectName("primary")
        self.b_apply.clicked.connect(self.apply)
        row.addWidget(self.b_apply)
        lay.addLayout(row)

    def refresh(self) -> None:
        self.plans = self.lib.organize_plan()
        self.table.setRowCount(len(self.plans))
        self.checks = []
        for r, p in enumerate(self.plans):
            cell, chk = _centered_check(True)
            self.checks.append(chk)
            self.table.setCellWidget(r, 0, cell)
            self.table.setItem(r, 1, QTableWidgetItem(p.item.name))
            self.table.removeCellWidget(r, 2)
            if p.item.kind == "package":
                combo = CategoryCombo()
                combo.set_key(p.item.category)
                combo.activated.connect(lambda _i, row=r, c=combo: self._recat(row, c))
                self.table.setCellWidget(r, 2, combo)
                self.table.takeItem(r, 2)
            else:
                self.table.setItem(r, 2, QTableWidgetItem(cats.label(cats.SCRIPT_MOD)))
            cur = p.item.rel_path.rsplit("/", 1)[0]
            self.table.setItem(r, 3, QTableWidgetItem(f"{cur}  →  {p.target_rel.rsplit('/', 1)[0]}"))
            reason = QTableWidgetItem(p.item.reason)
            if p.item.confidence < 0.6 and not p.item.user_set:
                reason.setForeground(QColor(tok("warn")))
                reason.setText("확인 필요 · " + p.item.reason)
            self.table.setItem(r, 4, reason)
        self.summary.setText(f"이동할 CC {len(self.plans)}개" if self.plans else "모든 CC가 이미 정리되어 있습니다.")
        self.b_apply.setEnabled(bool(self.plans))

    def _recat(self, row: int, combo: CategoryCombo) -> None:
        p = self.plans[row]
        self.lib.set_category(p.item, combo.key(), move=False)
        p.target_rel = "Mods/" + self.lib.folder_for(combo.key()) + "/" + p.item.rel_path.rsplit("/", 1)[1]
        cur = p.item.rel_path.rsplit("/", 1)[0]
        self.table.item(row, 3).setText(f"{cur}  →  {p.target_rel.rsplit('/', 1)[0]}")
        self.table.item(row, 4).setText("직접 지정")
        self.table.item(row, 4).setForeground(self.palette().text().color())

    def _check_all(self, on: bool) -> None:
        for chk in self.checks:
            chk.setChecked(on)

    def apply(self) -> None:
        chosen = [p for p, chk in zip(self.plans, self.checks) if chk.isChecked()]
        if not chosen:
            return
        if not confirm(self, "자동 정리", f"CC {len(chosen)}개를 분류 폴더로 옮길까요?\n(설정의 '자동 정리 되돌리기'로 원래대로 돌릴 수 있습니다)"):
            return
        def work(progress):
            moved = self.lib.apply_moves(chosen, f"{ORGANIZE_LABEL} ({len(chosen)}개)",
                                         lambda i, n, text: progress(i, n, text, "파일을 분류 폴더로 옮기는 중"))
            self.lib.scan(lambda i, n, text: progress(i, n, text, "옮긴 파일을 목록에 반영하는 중"))
            return moved

        try:
            n = run_with_loading(self, "자동 정리 중", work)
        except Exception as ex:
            QMessageBox.warning(self, "자동 정리", f"정리 중 오류: {ex}")
            n = 0
        self.changed.emit()
        self.refresh()
        QMessageBox.information(self, "자동 정리", f"{n}개 파일을 옮겼습니다.")


class ResourceCfgPage(QWidget):
    def __init__(self, lib: Library):
        super().__init__()
        self.lib = lib
        self.cfg = rc.ResourceCfg.default()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(_title(
            "Resource.cfg 설정",
            "Resource.cfg는 게임이 Mods 폴더 안에서 어떤 CC 파일을 읽을지 정해 두는 파일입니다.\n"
            "\n"
            "우선순위: 숫자가 클수록 먼저 적용됩니다. 같은 아이템을 바꾸는 CC가 두 개 있으면 숫자가 큰 쪽이 적용됩니다\n"
            "폴더: 규칙을 적용할 폴더입니다. 비워두면 Mods 폴더 전체입니다.\n"
            "하위 폴더 깊이: 폴더 안으로 몇 단계까지 들어가서 읽을지입니다. (0 = 그 폴더만, 최대 5)\n"
            "\n"
            "잘 모르겠으면 [기본값으로]를 누르고 [저장]하거나 그냥 건드리지마세요"))

        body = QHBoxLayout()
        left = QVBoxLayout()
        self.table = QTableWidget(0, 4)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setHorizontalHeaderLabels(["우선순위", "폴더 (비워두면 Mods 전체)", "하위 폴더 깊이", ""])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        for col, width in enumerate((90, 340, 120, 70)):
            self.table.setColumnWidth(col, width)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setShowGrid(False)
        left.addWidget(self.table, 1)
        r = QHBoxLayout()
        b_add = QPushButton("규칙 추가")
        b_add.clicked.connect(lambda: self._add_row(rc.Rule(500, "", 4)))
        r.addWidget(b_add)
        r.addStretch(1)
        left.addLayout(r)

        opt = QGroupBox("게임 옵션 (Options.ini)")
        ol = QVBoxLayout(opt)
        self.chk_cc = QCheckBox("커스텀 콘텐츠와 모드 허용")
        self.chk_script = QCheckBox("스크립트 모드 허용")
        self.opt_status = QLabel()
        self.opt_status.setObjectName("muted")
        ol.addWidget(self.chk_cc)
        ol.addWidget(self.chk_script)
        ol.addWidget(self.opt_status)
        b_opt = QPushButton("게임 옵션 저장")
        b_opt.clicked.connect(self._save_options)
        ol.addWidget(b_opt, 0, Qt.AlignLeft)
        left.addWidget(opt)
        body.addLayout(left, 3)

        right = QVBoxLayout()
        right.addWidget(QLabel("미리보기 (저장될 내용)"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFont("Consolas", 9))
        right.addWidget(self.preview, 1)
        body.addLayout(right, 2)
        lay.addLayout(body, 1)

        bot = QHBoxLayout()
        self.status = QLabel()
        bot.addWidget(self.status, 1)
        for text, fn in (("다시 읽기", self.refresh), ("기본값으로", self._defaults)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bot.addWidget(b)
        b_save = QPushButton("저장 (자동 백업)")
        b_save.setObjectName("primary")
        b_save.clicked.connect(self._save)
        bot.addWidget(b_save)
        lay.addLayout(bot)

    def _folders(self) -> list[str]:
        mods = self.lib.config.mods_dir
        out = []
        if os.path.isdir(mods):
            out = sorted(e.name for e in os.scandir(mods) if e.is_dir())
        return out

    def _add_row(self, rule: rc.Rule) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        pr = QSpinBox()
        pr.setRange(-10000, 10000)
        pr.setValue(rule.priority)
        folder = QComboBox()
        folder.setEditable(True)
        folder.addItems([""] + self._folders())
        folder.setCurrentText(rule.folder)
        depth = QSpinBox()
        depth.setRange(0, rc.MAX_DEPTH)
        depth.setValue(rule.depth)
        for w in (pr, depth):
            w.valueChanged.connect(self._update_preview)
        folder.currentTextChanged.connect(self._update_preview)
        self.table.setCellWidget(r, 0, pr)
        self.table.setCellWidget(r, 1, folder)
        self.table.setCellWidget(r, 2, depth)
        b_del = QPushButton("삭제")
        b_del.clicked.connect(lambda: self._del_row(b_del))
        self.table.setCellWidget(r, 3, b_del)
        self._update_preview()

    def _del_row(self, button: QPushButton) -> None:
        for r in range(self.table.rowCount()):
            if self.table.cellWidget(r, 3) is button:
                self.table.removeRow(r)
                break
        self._update_preview()

    def _collect(self) -> rc.ResourceCfg:
        rules = []
        for r in range(self.table.rowCount()):
            rules.append(rc.Rule(self.table.cellWidget(r, 0).value(),
                                 self.table.cellWidget(r, 1).currentText().strip().strip("\\/"),
                                 self.table.cellWidget(r, 2).value()))
        return rc.ResourceCfg(rules, self.cfg.extra)

    def _update_preview(self, *_):
        self.preview.setPlainText(self._collect().render())

    def _load_cfg(self, cfg: rc.ResourceCfg) -> None:
        self.cfg = cfg
        self.table.setRowCount(0)
        for rule in cfg.rules:
            self._add_row(rule)
        self._update_preview()

    def refresh(self) -> None:
        mods = self.lib.config.mods_dir
        cfg, text = rc.load(mods)
        self._load_cfg(cfg)
        self.status.setText("Resource.cfg를 읽었습니다." if text else "Resource.cfg가 없어 기본값을 표시합니다. 저장하면 새로 만듭니다.")
        self._check_overrides_rule()
        opts = rc.read_options(self.lib.sims_dir)
        self.chk_cc.setChecked(opts.get("modsdisabled", "0") == "0")
        self.chk_script.setChecked(opts.get("scriptmodsenabled", "0") == "1")
        self.chk_cc.setEnabled(bool(opts))
        self.chk_script.setEnabled(bool(opts))
        self.opt_status.setText("Options.ini를 찾지 못했습니다 (게임을 한 번 실행하면 생깁니다)." if not opts else
                                "게임이 꺼져 있을 때만 변경하세요. 게임 업데이트 후에는 이 값이 꺼질 수 있습니다.")

    def _check_overrides_rule(self) -> None:
        mods = self.lib.config.mods_dir
        if os.path.isdir(os.path.join(mods, "00_Overrides")) and not any(
                r.folder.lower() == "00_overrides" for r in self.cfg.rules):
            self.status.setText(self.status.text() + "  00_Overrides 폴더에 높은 우선순위 규칙이 없습니다. '기본값으로'를 눌러보세요.")

    def _defaults(self) -> None:
        cfg = rc.ResourceCfg.default()
        cfg.extra = self.cfg.extra
        self._load_cfg(cfg)
        self.status.setText("기본값을 불러왔습니다. 저장해야 적용됩니다.")

    def _save(self) -> None:
        cfg = self._collect()
        if not cfg.rules:
            QMessageBox.warning(self, "Resource.cfg", "규칙이 하나도 없으면 게임이 CC를 읽지 않습니다.")
            return
        backup = rc.save(self.lib.config.mods_dir, cfg)
        self.cfg = cfg
        self.status.setText("저장했습니다." + (f" 백업: {backup}" if backup else ""))

    def _save_options(self) -> None:
        if launcher.game_running():
            QMessageBox.warning(self, "게임 옵션", "게임이 실행 중입니다. 게임을 끈 뒤 저장하세요.")
            return
        ok1 = rc.set_option(self.lib.sims_dir, "modsdisabled", "0" if self.chk_cc.isChecked() else "1")
        ok2 = rc.set_option(self.lib.sims_dir, "scriptmodsenabled", "1" if self.chk_script.isChecked() else "0")
        self.opt_status.setText("게임 옵션을 저장했습니다 (Options.ini.s4ccm.bak 백업)." if ok1 and ok2 else "저장 실패")


class BisectPage(QWidget):
    changed = Signal()

    WATCH_START_TIMEOUT = 180

    def __init__(self, lib: Library):
        super().__init__()
        self.lib = lib
        self.bisect = Bisect(lib)
        self._watch_state: str | None = None
        self._watch_started = 0.0
        self._watch_timer = QTimer(self, interval=3000)
        self._watch_timer.timeout.connect(self._watch_game)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(_title(
            "충돌 테스트 (절반 빼기)",
            "게임을 망가뜨리는 CC/모드를 찾는 방법입니다.\n"
            "\n"
            "1. [절반 빼고 테스트 시작]을 누르면 모드의 절반을 무작위로 Mods 밖으로 뺍니다.\n"
            "2. 심즈를 켜서 확인한 뒤 [정상 작동] 또는 [문제 발생]을 누릅니다.\n"
            "   • 정상 작동 → 문제는 뺀 쪽에 있습니다. 뺐던 모드의 절반을 다시 넣습니다.\n"
            "   • 문제 발생 → 문제는 남은 쪽에 있습니다. 적용된 모드에서 절반을 다시 뺍니다.\n"
            "3. 2번을 반복하면 원인 모드 하나가 남습니다."))

        self.setup_box = QGroupBox("테스트 범위")
        sl = QGridLayout(self.setup_box)
        self.scope = QButtonGroup(self)
        self.rb_all = QRadioButton("전체 (지금 모드 폴더에 있는 모든 CC와 모드)")
        self.rb_cat = QRadioButton("특정 분류만:")
        self.rb_recent = QRadioButton("최근 추가한 것만:")
        for i, rb in enumerate((self.rb_all, self.rb_cat, self.rb_recent)):
            self.scope.addButton(rb, i)
        self.rb_all.setChecked(True)
        self.cat_combo = QComboBox()
        for g, glabel in cats.GROUPS:
            self.cat_combo.addItem(f"[{glabel}]", f"group:{g}")
            for c in cats.CATEGORIES:
                if c.group == g and g not in (cats.UNSORTED, "tuning", "animations", "overrides"):
                    self.cat_combo.addItem(f"    {c.label}", f"cat:{c.key}")
        self.days = QSpinBox()
        self.days.setRange(1, 365)
        self.days.setValue(7)
        self.days.setSuffix(" 일 이내")
        self.chk_scripts = QCheckBox("스크립트 모드 포함")
        self.chk_scripts.setChecked(True)
        sl.addWidget(self.rb_all, 0, 0, 1, 2)
        sl.addWidget(self.rb_cat, 1, 0)
        sl.addWidget(self.cat_combo, 1, 1)
        sl.addWidget(self.rb_recent, 2, 0)
        sl.addWidget(self.days, 2, 1)
        sl.addWidget(self.chk_scripts, 3, 0, 1, 2)
        self.b_start = QPushButton("절반 빼고 테스트 시작")
        self.b_start.setObjectName("primary")
        self.b_start.clicked.connect(self._start)
        sl.addWidget(self.b_start, 4, 0, 1, 2, Qt.AlignLeft)
        lay.addWidget(self.setup_box)

        self.run_box = QGroupBox("진행")
        rl = QVBoxLayout(self.run_box)
        self.progress_label = QLabel()
        self.progress_label.setObjectName("h1")
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        rl.addWidget(self.progress_label)
        rl.addWidget(self.progress)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        rl.addWidget(self.hint)
        br = QHBoxLayout()
        self.b_launch = QPushButton("게임 실행")
        self.b_launch.setObjectName("primary")
        self.b_launch.clicked.connect(self._launch)
        self.b_ok = QPushButton("정상 작동")
        self.b_ok.clicked.connect(lambda: self._report(True))
        self.b_bad = QPushButton("문제 발생")
        self.b_bad.clicked.connect(lambda: self._report(False))
        self.b_end = QPushButton("테스트 종료 · 전부 원위치")
        self.b_end.setObjectName("danger")
        self.b_end.clicked.connect(self._end)
        for b in (self.b_launch, self.b_ok, self.b_bad):
            br.addWidget(b)
        br.addStretch(1)
        br.addWidget(self.b_end)
        rl.addLayout(br)
        tabs = QTabWidget()
        self.list_in = QListWidget()
        self.list_out = QListWidget()
        self.list_cleared = QListWidget()
        tabs.addTab(self.list_in, "의심 대상 · 적용 중")
        tabs.addTab(self.list_out, "의심 대상 · 빠져 있음")
        tabs.addTab(self.list_cleared, "문제 없음 확인")
        self.tabs = tabs
        rl.addWidget(tabs, 1)
        lay.addWidget(self.run_box, 1)

    def _scope_items(self):
        items = [i for i in self.lib.items() if i.status == "active"]
        if not self.chk_scripts.isChecked():
            items = [i for i in items if i.kind != "script"]
        sid = self.scope.checkedId()
        if sid == 1:
            kind, key = self.cat_combo.currentData().split(":", 1)
            items = [i for i in items if (cats.group_of(i.category) == key if kind == "group" else i.category == key)]
        elif sid == 2:
            cutoff = time.time() - self.days.value() * 86400
            items = [i for i in items if i.added_at >= cutoff or i.mtime >= cutoff]
        return items

    def _game_closed_or_warn(self, what: str) -> bool:
        if launcher.game_running():
            QMessageBox.warning(self, "충돌 테스트", f"게임이 실행 중입니다. 게임을 끈 뒤 {what}.")
            return False
        return True

    def _start(self) -> None:
        items = self._scope_items()
        if len(items) < 2:
            QMessageBox.information(self, "충돌 테스트", "테스트할 대상이 2개 이상이어야 합니다.")
            return
        if not self._game_closed_or_warn("시작하세요"):
            return
        if not confirm(self, "충돌 테스트", f"{len(items)}개 중 절반({len(items) // 2}개)을 무작위로 Mods 밖으로 뺍니다.\n"
                                         "테스트를 시작할까요?"):
            return
        n = self._run("충돌 테스트 준비 중", "모드 절반을 Mods 밖으로 빼는 중",
                      lambda: self.bisect.start([i.id for i in items]))
        self.changed.emit()
        self.refresh()
        self._ask_result(f"모드 {len(items)}개 중 절반({n}개)을 뺐습니다.")

    def _launch(self) -> None:
        exe = self.lib.config.game_exe
        if not exe or not os.path.isfile(exe):
            QMessageBox.warning(self, "게임 실행", "게임 실행 파일을 찾지 못했습니다. 설정에서 지정하세요.\n"
                                "직접 게임을 켜서 확인한 뒤 결과 버튼을 눌러도 됩니다.")
            return
        launcher.launch(exe, self.lib.sims_dir, self.lib.config.clear_thumbcache_on_launch)
        self._watch_state = "starting"
        self._watch_started = time.time()
        self._watch_timer.start()
        self.refresh()

    def _watch_game(self) -> None:
        running = launcher.game_running()
        if self._watch_state == "starting":
            if running:
                self._watch_state = "running"
            elif time.time() - self._watch_started > self.WATCH_START_TIMEOUT:
                self._stop_watch()
                self.hint.setText("게임이 켜지는 것을 확인하지 못했습니다. 게임을 확인한 뒤 결과 버튼을 눌러주세요.")
            return
        if self._watch_state == "running" and not running:
            self._stop_watch()
            self._ask_result("게임이 종료되었습니다.")

    def _stop_watch(self) -> None:
        self._watch_timer.stop()
        self._watch_state = None
        self.refresh()

    def _ask_result(self, note: str) -> None:
        s = self.bisect.state
        box = QMessageBox(self)
        box.setWindowTitle(f"충돌 테스트 {s.step + 1}단계")
        box.setIcon(QMessageBox.Question)
        box.setText(f"{note}\n심즈를 켜서 문제가 생기는지 확인해 보세요.")
        box.setInformativeText(f"의심 대상 {len(s.suspects)}개 · 남은 단계 약 {s.remaining_steps}\n"
                               "확인하려던 문제(오류, 멈춤, 깨짐 등)가 없었다면 [정상 작동]을 누르세요.")
        ok = box.addButton("정상 작동", QMessageBox.YesRole)
        bad = box.addButton("문제 발생", QMessageBox.NoRole)
        launch = None
        if self.lib.config.game_exe and not self._watch_state:
            launch = box.addButton("게임 실행", QMessageBox.ActionRole)
            launch.setToolTip("게임을 켜고, 게임을 끄면 이 창을 다시 띄웁니다.")
        box.addButton("나중에", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is ok:
            self._report(True)
        elif box.clickedButton() is bad:
            self._report(False)
        elif launch is not None and box.clickedButton() is launch:
            self._launch()

    def _report(self, worked: bool) -> None:
        if not self._game_closed_or_warn("결과를 눌러주세요 (파일을 옮겨야 합니다)"):
            return
        n = self._run("다음 단계 준비 중", "뺐던 모드의 절반을 다시 넣는 중" if worked else "적용된 모드의 절반을 빼는 중",
                      lambda: self.bisect.report(worked))
        self.changed.emit()
        self.refresh()
        s = self.bisect.state
        if s.testing:
            self._ask_result(f"뺐던 모드의 절반({n}개)을 다시 넣었습니다." if worked
                             else f"적용된 모드에서 절반({n}개)을 다시 뺐습니다.")
            return
        if s.found is not None:
            it = self.lib.db.get(s.found)
            QMessageBox.information(self, "충돌 테스트 결과",
                                    f"원인으로 보이는 CC/모드를 찾았습니다:\n\n{it.name if it else s.found}\n{it.rel_path if it else ''}\n\n"
                                    "[테스트 종료 · 전부 원위치]를 누르면 빼둔 모드가 모두 돌아옵니다.\n"
                                    "※ 두 개 이상이 함께 일으키는 충돌이면 결과가 정확하지 않을 수 있습니다.")
        elif not s.active:
            QMessageBox.information(self, "충돌 테스트", "의심 대상이 남지 않았습니다. 범위를 넓혀 다시 시도하세요.")

    def _run(self, title: str, phase: str, job, raise_errors: bool = False):
        def work(progress):
            self.bisect.progress = lambda i, n, text: progress(i, n, text, phase)
            try:
                result = job()
            finally:
                self.bisect.progress = None
            self.lib.scan(lambda i, n, text: progress(i, n, text, "목록에 반영하는 중"))
            return result

        try:
            return run_with_loading(self, title, work)
        except Exception as ex:
            if raise_errors:
                raise
            QMessageBox.warning(self, "충돌 테스트", f"파일을 옮기는 중 오류: {ex}")
            return 0

    def confirm_leave(self, closing: bool = False) -> bool:
        s = self.bisect.state
        if not s.active:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("충돌 테스트")
        box.setIcon(QMessageBox.Question)
        text = "충돌 테스트가 아직 끝나지 않았습니다."
        if s.out:
            text += f"\n지금 Mods 밖에 빼 둔 모드가 {len(s.out)}개 있습니다."
        box.setText(text + "\n원래대로 되돌릴까요?")
        box.setInformativeText("원래대로 되돌리면 빼 둔 모드가 모두 Mods로 돌아가고 테스트가 끝납니다.\n"
                               "그대로 두면 다음에 충돌 테스트 화면에서 이어서 할 수 있습니다.")
        revert = box.addButton("원래대로 되돌리기", QMessageBox.AcceptRole)
        revert.setObjectName("primary")
        revert.style().polish(revert)
        keep = box.addButton("그대로 두고 종료" if closing else "그대로 두고 나가기", QMessageBox.DestructiveRole)
        box.addButton("취소", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is keep:
            return True
        if box.clickedButton() is not revert or not self._game_closed_or_warn("되돌리세요"):
            return False
        self._stop_watch()
        try:
            self._run("원래대로 되돌리는 중", "빼 둔 모드를 Mods로 돌려놓는 중", self.bisect.end, raise_errors=True)
        except Exception as ex:
            QMessageBox.warning(self, "충돌 테스트", f"되돌리는 중 오류: {ex}")
            return False
        self.changed.emit()
        self.refresh()
        return True

    def _end(self) -> None:
        if not self._game_closed_or_warn("종료하세요"):
            return
        self._stop_watch()
        self._run("원래대로 되돌리는 중", "빼 둔 모드를 Mods로 돌려놓는 중", self.bisect.end)
        self.changed.emit()
        self.refresh()

    def _fill(self, lw: QListWidget, ids: list[int]) -> None:
        lw.clear()
        for i in ids:
            it = self.lib.db.get(i)
            lw.addItem(f"{it.name}   ({category_path_label(it.category)})" if it else f"#{i} (삭제됨)")

    def refresh(self) -> None:
        s = self.bisect.state
        self.setup_box.setEnabled(not s.active)
        self.run_box.setEnabled(s.active)
        total = s.total or (len(s.suspects) + len(s.cleared)) or 1
        if s.found is not None:
            it = self.lib.db.get(s.found)
            self.progress_label.setText(f"원인: {it.name if it else s.found}")
            self.hint.setText("확인이 끝나면 [테스트 종료 · 전부 원위치]를 누르세요. 원인 모드는 라이브러리에서 비활성화하거나 삭제할 수 있습니다.")
            self.progress.setValue(100)
        elif s.active:
            self.progress_label.setText(f"{s.step + 1}단계 · 의심 대상 {len(s.suspects)}개 · 남은 단계 약 {s.remaining_steps}")
            self.progress.setValue(int(100 * (1 - len(s.suspects) / total)))
            if self._watch_state:
                self.hint.setText("게임 실행 중… 확인한 뒤 게임을 끄면 정상이었는지 물어봅니다.")
            else:
                self.hint.setText(f"지금 {len(s.out)}개가 빠져 있습니다. [게임 실행]을 눌러 확인하세요.\n"
                                  "(게임을 직접 켜서 확인했다면 아래 결과 버튼을 바로 눌러도 됩니다.)")
        else:
            self.progress_label.setText("진행 중인 테스트가 없습니다")
            self.progress.setValue(0)
            self.hint.setText("")
        testing = s.testing and not self._watch_state
        self.b_ok.setEnabled(testing)
        self.b_bad.setEnabled(testing)
        self.b_launch.setEnabled(testing and bool(self.lib.config.game_exe))
        self._fill(self.list_in, s.suspects_in)
        self._fill(self.list_out, s.suspects_out)
        self._fill(self.list_cleared, s.cleared)
        self.tabs.setTabText(0, f"의심 대상 · 적용 중 ({len(s.suspects_in)})")
        self.tabs.setTabText(1, f"의심 대상 · 빠져 있음 ({len(s.suspects_out)})")
        self.tabs.setTabText(2, f"문제 없음 확인 ({len(s.cleared)})")


class _DupWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)

    def __init__(self, lib: Library):
        super().__init__()
        self.lib = lib

    def run(self):
        try:
            res = find_duplicates(self.lib, lambda i, n, s: self.progress.emit(i, n, s))
        except Exception as ex:
            res = ex
        self.finished.emit(res)


class DuplicatesPage(QWidget):
    changed = Signal()

    def __init__(self, lib: Library):
        super().__init__()
        self.lib = lib
        self._thread: QThread | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(_title(
            "중복 검사",
            "• 완전히 같은 파일: 같은 CC를 두 번 넣은 경우입니다. 하나만 남기면 됩니다.\n"
            "• 같은 아이템이 들어 있는 파일: 이름은 달라도 게임 안에서 같은 아이템인 경우입니다.\n"
            "   (예: 업데이트 전/후 버전이 둘 다 있음, 묶음 파일과 낱개 파일이 같이 있음)\n"
            "   게임은 겹치는 아이템을 하나만 보여줍니다.\n"
            "   단, 여러 아이템을 합친 파일은 일부만 겹칠 수 있으니 '겹치는 아이템' 칸을 보고 직접 체크하세요.\n"
            "   ('전부'라고 나오면 그 파일의 아이템이 모두 다른 파일에도 있다는 뜻이라 지워도 됩니다.)\n"
            "\n"
            "완전히 같은 파일은 하나만 남기고 미리 체크해 두었습니다. 체크된 항목이 정리 대상입니다."))
        top = QHBoxLayout()
        self.b_run = QPushButton("중복 검사 시작")
        self.b_run.setObjectName("primary")
        self.b_run.clicked.connect(self.run)
        top.addWidget(self.b_run)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        top.addWidget(self.progress, 1)
        self.summary = QLabel()
        top.addWidget(self.summary, 1)
        lay.addLayout(top)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["파일", "위치", "겹치는 아이템", "크기", "수정일", "상태"])
        header = self.tree.header()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        header.setMinimumSectionSize(60)
        for col, width in enumerate((420, 260, 160, 80, 90)):
            self.tree.setColumnWidth(col, width)
        self.tree.setIconSize(QSize(40, 40))
        self.tree.itemDoubleClicked.connect(self._open)
        lay.addWidget(self.tree, 1)

        bot = QHBoxLayout()
        bot.addStretch(1)
        self.b_disable = QPushButton("체크한 항목 비활성화")
        self.b_disable.clicked.connect(lambda: self._apply("disable"))
        self.b_trash = QPushButton("체크한 항목 휴지통으로")
        self.b_trash.setObjectName("danger")
        self.b_trash.clicked.connect(lambda: self._apply("trash"))
        bot.addWidget(self.b_disable)
        bot.addWidget(self.b_trash)
        lay.addLayout(bot)
        self._set_buttons(False)

    def refresh(self) -> None:
        pass

    def _set_buttons(self, on: bool) -> None:
        self.b_disable.setEnabled(on)
        self.b_trash.setEnabled(on)

    def run(self) -> None:
        if self._thread is not None:
            return
        self.tree.clear()
        self.summary.clear()
        self.progress.show()
        self.progress.setValue(0)
        self.b_run.setEnabled(False)
        self._thread = QThread(self)
        self._worker = _DupWorker(self.lib)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(lambda i, n, s: (self.progress.setMaximum(max(n, 1)), self.progress.setValue(i)))
        self._worker.finished.connect(self._done)
        self._thread.start()

    def _done(self, res) -> None:
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        self.progress.hide()
        self.b_run.setEnabled(True)
        if isinstance(res, Exception):
            QMessageBox.warning(self, "중복 검사", f"검사 중 오류: {res}")
            return
        self._show(res)

    def _show(self, groups) -> None:
        self.tree.clear()
        bold = QFont()
        bold.setBold(True)
        for g in groups:
            top = QTreeWidgetItem(self.tree, [g.title])
            top.setFont(0, bold)
            top.setFirstColumnSpanned(True)
            if g.kind == SAME_ITEM:
                top.setForeground(0, QColor(tok("warn")))
            for it in sorted(g.items, key=lambda i: i is not g.keep):
                folder = it.rel_path.rsplit("/", 1)[0]
                if g.kind == SAME_ITEM:
                    shared, total = g.overlap.get(it.id, (0, 0))
                    overlap = f"전체 {total}개 중 {shared}개" + (" (전부)" if shared == total else "")
                else:
                    overlap = "파일 전체"
                ch = QTreeWidgetItem(top, [it.name, folder, overlap, human_size(it.size),
                                           dt.datetime.fromtimestamp(it.mtime).strftime("%Y-%m-%d"),
                                           STATUS_LABEL.get(it.status, it.status)])
                ch.setIcon(0, item_icon(it))
                ch.setToolTip(0, it.name)
                ch.setToolTip(1, it.rel_path)
                ch.setData(0, Qt.UserRole, it)
                ch.setFlags(ch.flags() | Qt.ItemIsUserCheckable)
                ch.setCheckState(0, Qt.Checked if g.kind == SAME_FILE and it is not g.keep else Qt.Unchecked)
            top.setExpanded(True)
        same = sum(1 for g in groups if g.kind == SAME_FILE)
        self.summary.setText("중복이 없습니다." if not groups else
                             f"완전히 같은 파일 {same}묶음 · 같은 아이템 {len(groups) - same}묶음")
        self._set_buttons(bool(groups))

    def _checked(self):
        out = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                ch = top.child(j)
                if ch.checkState(0) == Qt.Checked:
                    out.append(ch.data(0, Qt.UserRole))
        return out

    def _open(self, wi: QTreeWidgetItem, _col: int) -> None:
        it = wi.data(0, Qt.UserRole)
        if it is not None:
            reveal(self.lib.abs(it.rel_path))

    def _apply(self, action: str) -> None:
        items = self._checked()
        if not items:
            return
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if top.childCount() and all(top.child(j).checkState(0) == Qt.Checked for j in range(top.childCount())):
                QMessageBox.warning(self, "중복 검사", f"'{top.text(0)}' 묶음이 전부 체크되어 있습니다.\n하나는 남겨두세요.")
                return
        names = "\n".join(f"· {i.name}" for i in items[:10]) + ("\n…" if len(items) > 10 else "")
        if action == "trash":
            if not confirm(self, "중복 정리", f"{len(items)}개를 휴지통으로 보낼까요?\n\n{names}"):
                return
            _n, failed = self.lib.trash(items)
            if failed:
                QMessageBox.warning(self, "중복 정리", "휴지통으로 보내지 못한 항목이 있습니다. 게임이 켜져 있으면 끈 뒤 다시 해 보세요.\n\n" + "\n".join(failed[:10]))
        else:
            if not confirm(self, "중복 정리", f"{len(items)}개를 비활성화할까요? (Mods 밖으로 옮김)\n\n{names}"):
                return
            self.lib.set_status([i for i in items if i.status == "active"], "disabled", f"중복 정리 ({len(items)}개)")
        self.changed.emit()
        self.run()


class SettingsPage(QWidget):
    sims_dir_changed = Signal()
    theme_changed = Signal(str)
    changed = Signal()

    def __init__(self, lib: Library):
        super().__init__()
        self.lib = lib
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(_title("설정"))
        form = QFormLayout()
        self.sims = QLineEdit()
        self.sims.setReadOnly(True)
        r1 = QHBoxLayout()
        r1.addWidget(self.sims, 1)
        b = QPushButton("변경 / 다시 찾기…")
        b.clicked.connect(self._change_sims)
        r1.addWidget(b)
        form.addRow("심즈4 모드 폴더", r1)
        self.exe = QLineEdit()
        self.exe.editingFinished.connect(self._save)
        r2 = QHBoxLayout()
        r2.addWidget(self.exe, 1)
        b2 = QPushButton("찾아보기…")
        b2.clicked.connect(self._browse_exe)
        b3 = QPushButton("자동 찾기")
        b3.clicked.connect(self._auto_exe)
        r2.addWidget(b2)
        r2.addWidget(b3)
        form.addRow("게임 실행 파일", r2)
        self.exe_status = QLabel()
        self.exe_status.setObjectName("muted")
        form.addRow("", self.exe_status)
        self.chk_cache = QCheckBox("게임 실행 전에 썸네일 캐시 비우기")
        self.chk_cache.toggled.connect(self._save)
        form.addRow("", self.chk_cache)
        cache_info = QLabel("게임이 CC 아이콘을 저장해 두는 파일(localthumbcache.package)입니다. CC를 지우거나 바꾼 뒤 "
                            "게임 안에서 아이콘이 깨지거나 예전 모습으로 보일 때 켜두면, 게임이 아이콘을 새로 만듭니다. "
                            "지운 파일은 휴지통으로 갑니다.")
        cache_info.setObjectName("muted")
        cache_info.setWordWrap(True)
        form.addRow("", cache_info)
        self.theme = QComboBox()
        self.theme.addItem("자동", "system")
        self.theme.setToolTip("자동: 윈도우가 다크 모드면 다크, 라이트 모드면 라이트로 바뀝니다.")
        self.theme.addItem("다크", "dark")
        self.theme.addItem("라이트", "light")
        self.theme.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.theme.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.theme.currentIndexChanged.connect(self._theme_picked)
        form.addRow("테마", self.theme)
        lay.addLayout(form)
        lay.addSpacing(18)
        b_data = QPushButton("프로그램 설정 파일 폴더 열기")
        b_data.setToolTip(app_dir())
        b_data.clicked.connect(lambda: os.startfile(app_dir()))
        lay.addWidget(b_data, 0, Qt.AlignLeft)

        lay.addSpacing(18)
        undo_box = QGroupBox("자동 정리 되돌리기")
        ul = QVBoxLayout(undo_box)
        undo_info = QLabel("자동 정리로 옮긴 CC를 모두 원래 있던 폴더로 되돌립니다. "
                           "자동 정리 뒤에 따로 비활성화하거나 지운 파일은 지금 있는 그대로 둡니다.")
        undo_info.setObjectName("muted")
        undo_info.setWordWrap(True)
        ul.addWidget(undo_info)
        self.undo_status = QLabel()
        ul.addWidget(self.undo_status)
        self.b_undo = QPushButton("자동 정리 되돌리기")
        self.b_undo.clicked.connect(self._undo_organize)
        ul.addWidget(self.b_undo, 0, Qt.AlignLeft)
        lay.addWidget(undo_box)
        lay.addStretch(1)
        if uses_nanum():
            credit = QLabel("이 프로그램에는 네이버에서 제공한 나눔글꼴을 사용하고 있습니다.")
            credit.setObjectName("muted")
            lay.addWidget(credit)

    def refresh(self) -> None:
        c = self.lib.config
        self.sims.setText(c.mods_dir)
        self.exe.setText(c.game_exe)
        self.chk_cache.setChecked(c.clear_thumbcache_on_launch)
        self.theme.blockSignals(True)
        self.theme.setCurrentIndex(max(0, self.theme.findData(c.theme)))
        self.theme.blockSignals(False)
        self._exe_status()
        self._undo_status()

    def _undo_status(self) -> None:
        runs, files = self.lib.organize_history()
        self.undo_status.setText(f"되돌릴 수 있는 자동 정리 {runs}번 · 파일 {files}개" if runs else
                                 "되돌릴 자동 정리가 없습니다.")
        self.b_undo.setEnabled(bool(runs))

    def _undo_organize(self) -> None:
        runs, files = self.lib.organize_history()
        if not runs or not confirm(self, "자동 정리 되돌리기",
                                   f"자동 정리 {runs}번으로 옮긴 파일 {files}개를 원래 폴더로 되돌릴까요?"):
            return
        def work(progress):
            moved = self.lib.undo_organize(lambda i, n, text: progress(i, n, text, "파일을 원래 폴더로 옮기는 중"))
            self.lib.scan(lambda i, n, text: progress(i, n, text, "목록에 반영하는 중"))
            return moved

        try:
            n = run_with_loading(self, "자동 정리 되돌리는 중", work)
        except Exception as ex:
            QMessageBox.warning(self, "자동 정리 되돌리기", f"되돌리는 중 오류: {ex}")
            n = 0
        self.changed.emit()
        self._undo_status()
        QMessageBox.information(self, "자동 정리 되돌리기", f"{n}개 파일을 원래 폴더로 되돌렸습니다.")

    def _exe_status(self):
        p = self.exe.text()
        self.exe_status.setText("찾음" if p and os.path.isfile(p) else "실행 파일이 없으면 [게임 실행] 버튼만 쓸 수 없습니다.")

    def _change_sims(self):
        from .dialogs import SetupDialog
        d = SetupDialog(self, self.lib.config.mods_dir)
        if d.exec() and d.result_dir:
            self.lib.config.sims_dir = d.result_dir
            self.lib.config.save()
            self.sims.setText(self.lib.config.mods_dir)
            self.sims_dir_changed.emit()

    def _browse_exe(self):
        p, _ = QFileDialog.getOpenFileName(self, "TS4_x64.exe 선택", self.exe.text(), "TS4_x64.exe (TS4_x64.exe);;실행 파일 (*.exe)")
        if p:
            self.exe.setText(os.path.normpath(p))
            self._save()

    def _auto_exe(self):
        p = paths.find_game_exe()
        if p:
            self.exe.setText(p)
        else:
            QMessageBox.information(self, "게임 실행 파일", "자동으로 찾지 못했습니다. [찾아보기]로 TS4_x64.exe를 지정하세요.\n"
                                    "(보통 ...\\The Sims 4\\Game\\Bin\\TS4_x64.exe)")
        self._save()

    def _save(self):
        c = self.lib.config
        c.game_exe = self.exe.text().strip()
        c.clear_thumbcache_on_launch = self.chk_cache.isChecked()
        c.save()
        self._exe_status()

    def _theme_picked(self) -> None:
        c = self.lib.config
        theme = self.theme.currentData()
        if theme != c.theme:
            c.theme = theme
            c.save()
            self.theme_changed.emit(theme)

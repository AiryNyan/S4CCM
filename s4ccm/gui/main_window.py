from __future__ import annotations

import os
import time

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence
from PySide6.QtWidgets import (QApplication, QFileDialog, QHeaderView, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QProgressBar, QSizePolicy, QSplitter, QStackedWidget, QToolBar,
                               QTreeWidget, QTreeWidgetItem, QWidget)

from .. import categories as cats
from .. import importer, launcher, paths, resourcecfg
from ..config import Config
from ..library import Library
from .common import STATUS_LABEL, apply_theme, confirm, reveal, tok
from .dialogs import ImportDialog, SetupDialog
from .library_view import INSTALLED, LibraryPage
from .loading import is_busy, run_with_loading
from .tray_page import TrayPage
from .pages import BisectPage, DuplicatesPage, OrganizePage, ResourceCfgPage, SettingsPage

ADD_FILTER = ("모드/트레이 파일 (*.package *.ts4script *.zip *.7z *.rar *.trayitem *.householdbinary *.blueprint "
              "*.room *.hhi *.sgi *.bpi *.rmi);;모든 파일 (*)")


class ScanWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(dict)

    def __init__(self, lib: Library):
        super().__init__()
        self.lib = lib

    def run(self):
        try:
            res = self.lib.scan(lambda i, n, s: self.progress.emit(i, n, s))
        except Exception as ex:
            res = {"error": str(ex)}
        self.finished.emit(res)


class MainWindow(QMainWindow):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.lib = Library(config)
        self.setWindowTitle("S4CCM")
        self.resize(1400, 860)
        self.setAcceptDrops(True)
        self._thread: QThread | None = None
        self._rescan_pending = False
        self._closing = False
        self._last_scan = time.monotonic()

        tb = QToolBar()
        tb.setObjectName("main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(tb)
        add = self._act(tb, "CC 추가", self.add_files, QKeySequence.Open, "파일이나 압축파일(.zip/.7z/.rar)로 모드 추가")
        tb.widgetForAction(add).setObjectName("accent")
        self._act(tb, "폴더 추가", self.add_folder, None, "압축을 푼 모드 폴더 추가")
        tb.addSeparator()
        self._act(tb, "다시 스캔", lambda: self.rescan(show=True), QKeySequence.Refresh)
        self._act(tb, "자동 정리", lambda: self.goto("organize"))
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.search = QLineEdit()
        self.search.setPlaceholderText("이름, 메모 검색")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(260)
        self.search.textChanged.connect(self._on_search)
        tb.addWidget(self.search)
        self.view_act = self._act(tb, "목록 보기", self.toggle_view)

        self.library = LibraryPage(self.lib)
        self.library.detail.request.connect(self._detail_action)
        self.library.status_requested.connect(self.set_status)
        self.library.counts_changed.connect(self._update_counts)
        self.organize = OrganizePage(self.lib)
        self.organize.changed.connect(self.library.reload)
        self.cfgpage = ResourceCfgPage(self.lib)
        self.bisect = BisectPage(self.lib)
        self.bisect.changed.connect(self.library.reload)
        self.tray = TrayPage(self.lib)
        self.dupes = DuplicatesPage(self.lib)
        self.dupes.changed.connect(self.library.reload)
        self.settings = SettingsPage(self.lib)
        self.settings.sims_dir_changed.connect(self._sims_dir_changed)
        self.settings.theme_changed.connect(self._theme_changed)
        QApplication.styleHints().colorSchemeChanged.connect(
            lambda _s: self._theme_changed(self.config.theme) if self.config.theme == "system" else None)
        self.settings.changed.connect(self.library.reload)
        self.pages = QStackedWidget()
        self.page_map = {"library": self.library, "organize": self.organize, "cfg": self.cfgpage,
                         "bisect": self.bisect, "dupes": self.dupes, "settings": self.settings, "tray": self.tray}
        for w in self.page_map.values():
            self.pages.addWidget(w)

        self.sidebar = QTreeWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setHeaderHidden(True)
        self.sidebar.setColumnCount(2)
        self.sidebar.header().setStretchLastSection(False)
        self.sidebar.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.sidebar.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.sidebar.setIndentation(14)
        self.sidebar.setRootIsDecorated(False)
        self.sidebar.setMinimumWidth(220)
        self.sidebar.setMaximumWidth(280)
        self.sidebar.itemClicked.connect(self._sidebar_clicked)
        self.sidebar.itemCollapsed.connect(
            lambda wi: wi.setExpanded(True) if wi.data(0, Qt.UserRole)[0].startswith("header") else None)
        self._build_sidebar()

        split = QSplitter()
        split.addWidget(self.sidebar)
        split.addWidget(self.pages)
        split.setStretchFactor(1, 1)
        self.setCentralWidget(split)

        self.status_label = QLabel()
        self.statusBar().addWidget(self.status_label, 1)
        self.path_label = QLabel()
        self.path_label.setObjectName("muted")
        self.statusBar().addPermanentWidget(self.path_label)
        self.scan_bar = QProgressBar()
        self.scan_bar.setTextVisible(False)
        self.scan_bar.setFixedWidth(160)
        self.scan_bar.hide()
        self.statusBar().addPermanentWidget(self.scan_bar)

        self.library.set_view_mode(config.view_mode)
        self.view_act.setText("그리드 보기" if config.view_mode == "list" else "목록 보기")
        self.library.set_scope("전체")
        QTimer.singleShot(0, self._startup)

    def _act(self, tb, text, fn, shortcut=None, tip=None) -> QAction:
        a = QAction(text, self)
        a.triggered.connect(fn)
        if shortcut:
            a.setShortcut(shortcut)
        if tip:
            a.setToolTip(tip)
        tb.addAction(a)
        return a

    def _startup(self):
        ok = bool(self.config.sims_dir) and paths.validate(self.config.sims_dir)[0]
        if not ok:
            found = paths.find_sims_folders()
            if len(found) == 1 and not self.config.sims_dir:
                self.config.sims_dir = found[0].root
            else:
                d = SetupDialog(self, self.config.mods_dir)
                if not d.exec() or not d.result_dir:
                    if not self.config.sims_dir:
                        QMessageBox.information(self, "S4CCM", "심즈4 모드 폴더를 지정해야 사용할 수 있습니다.\n설정에서 언제든 지정할 수 있습니다.")
                        self.goto("settings")
                        return
                else:
                    self.config.sims_dir = d.result_dir
        if not self.config.game_exe or not os.path.isfile(self.config.game_exe):
            exe = paths.find_game_exe()
            if exe:
                self.config.game_exe = exe
        self.config.save()
        self._sims_dir_changed()

    def _sims_dir_changed(self):
        self.lib.thumbcache.path = os.path.join(self.config.sims_dir, "localthumbcache.package")
        self.lib.thumbcache._index = None
        self.path_label.setText(f"Mods: {self.config.mods_dir}")
        self.settings.refresh()
        self.rescan(show=True)

    def _build_sidebar(self):
        sb = self.sidebar
        sb.clear()
        self.side_items: dict[tuple, QTreeWidgetItem] = {}

        def add(parent, text, data):
            it = QTreeWidgetItem(parent if parent else sb, [text, ""])
            it.setData(0, Qt.UserRole, data)
            it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            self.side_items[data] = it
            return it

        def section(text, data):
            it = add(None, text, data)
            it.setFlags(Qt.ItemIsEnabled)
            f = it.font(0)
            f.setPointSizeF(8.5)
            f.setWeight(QFont.DemiBold)
            it.setFont(0, f)
            it.setSizeHint(0, QSize(0, 34))
            return it

        lib_root = section("라이브러리", ("header",))
        add(lib_root, "전체", ("scope", None, None, INSTALLED))
        for g, glabel in cats.GROUPS:
            gi = add(lib_root, glabel, ("scope", g, None, INSTALLED))
            subs = [c for c in cats.CATEGORIES if c.group == g]
            if len(subs) > 1:
                for c in subs:
                    add(gi, c.label, ("scope", None, c.key, INSTALLED))
        add(lib_root, "비활성", ("scope", None, None, "disabled"))
        add(lib_root, "충돌 테스트로 잠시 뺀 것", ("scope", None, None, "quarantine"))
        lib_root.setExpanded(True)
        tools = section("도구", ("header2",))
        add(tools, "트레이", ("page", "tray"))
        add(tools, "자동 정리", ("page", "organize"))
        add(tools, "Resource.cfg", ("page", "cfg"))
        add(tools, "충돌 테스트", ("page", "bisect"))
        add(tools, "중복 검사", ("page", "dupes"))
        add(tools, "설정", ("page", "settings"))
        tools.setExpanded(True)
        self.side_items[("scope", None, None, INSTALLED)].setSelected(True)
        self._paint_sidebar()

    def _paint_sidebar(self):
        for data, wi in self.side_items.items():
            if data[0].startswith("header"):
                wi.setForeground(0, QColor(tok("muted")))
            wi.setForeground(1, QColor(tok("faint")))

    def _theme_changed(self, theme: str):
        apply_theme(QApplication.instance(), theme)
        self._paint_sidebar()
        self.library.reload()

    def _update_counts(self):
        items = self.lib.items()
        counts: dict[tuple, int] = {}
        for it in items:
            statuses = (it.status, INSTALLED) if it.status in ("active", "disabled") else (it.status,)
            for st in statuses:
                for key in (("scope", None, None, st),
                            ("scope", cats.group_of(it.category), None, st),
                            ("scope", None, it.category, st)):
                    counts[key] = counts.get(key, 0) + 1
        for data, wi in self.side_items.items():
            if data[0] != "scope":
                continue
            n = counts.get(data, 0)
            wi.setText(1, str(n) if n or data[3] == INSTALLED and data[1] is None and data[2] is None else "")
            if data[2] is not None or data[3] == "quarantine":
                wi.setHidden(n == 0)
        active = [i for i in items if i.status == "active"]
        unsorted = sum(1 for i in active if i.category == cats.UNSORTED)
        scripts = sum(1 for i in active if i.kind == "script")
        off = sum(1 for i in items if i.status == "disabled")
        testing = sum(1 for i in items if i.status == "quarantine")
        text = f"CC {len(active) - scripts}개 · 스크립트 모드 {scripts}개 · 미분류 {unsorted}개 · 비활성 {off}개"
        if testing:
            text += f" · 충돌 테스트로 빼둔 것 {testing}개"
        self.status_label.setText(text)

    def _sidebar_clicked(self, wi: QTreeWidgetItem, _col: int):
        data = wi.data(0, Qt.UserRole)
        if not data:
            return
        if data[0] == "scope":
            _, group, cat, status = data
            if not self.goto("library"):
                return
            title = wi.text(0)
            self.library.set_scope(title, group=group, category=cat, status=status)
        elif data[0] == "page":
            self.goto(data[1])

    def goto(self, page: str) -> bool:
        if self.pages.currentWidget() is self.bisect and page != "bisect" and not self.bisect.confirm_leave():
            for data, wi in self.side_items.items():
                wi.setSelected(data == ("page", "bisect"))
            return False
        w = self.page_map[page]
        self.pages.setCurrentWidget(w)
        if hasattr(w, "refresh") and page != "library":
            w.refresh()
        if page != "library":
            for data, wi in self.side_items.items():
                wi.setSelected(data == ("page", page))
        return True

    def rescan(self, show: bool = False):
        if not self.config.sims_dir or self._closing:
            return
        if show:
            try:
                run_with_loading(self, "CC 목록을 읽는 중", lambda progress: self.lib.scan(
                    lambda i, n, text: progress(i, n, text, "파일을 하나씩 확인하고 있습니다")))
            except Exception as ex:
                QMessageBox.warning(self, "스캔 오류", str(ex))
            self._last_scan = time.monotonic()
            self.library.reload()
            if self.pages.currentWidget() is not self.library and hasattr(self.pages.currentWidget(), "refresh"):
                self.pages.currentWidget().refresh()
            return
        if self._thread is not None:
            self._rescan_pending = True
            return
        self.scan_bar.show()
        self.scan_bar.setValue(0)
        self._thread = QThread(self)
        self._worker = ScanWorker(self.lib)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._scan_progress)
        self._worker.finished.connect(self._scan_done)
        self._thread.start()

    def _scan_progress(self, i, n, text):
        self.scan_bar.setMaximum(max(n, 1))
        self.scan_bar.setValue(i)
        self.status_label.setText(f"스캔 중… {i}/{n}  {text[-60:]}")

    def _scan_done(self, res: dict):
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        self._last_scan = time.monotonic()
        self.scan_bar.hide()
        if "error" in res:
            QMessageBox.warning(self, "스캔 오류", res["error"])
        self.library.reload()
        if self.pages.currentWidget() is not self.library and hasattr(self.pages.currentWidget(), "refresh"):
            self.pages.currentWidget().refresh()
        if self._rescan_pending:
            self._rescan_pending = False
            QTimer.singleShot(0, self.rescan)

    def changeEvent(self, e):
        if e.type() == QEvent.ActivationChange and self.isActiveWindow():
            if (QApplication.activeModalWidget() is None and self._thread is None and not is_busy()
                    and time.monotonic() - self._last_scan > 3):
                QTimer.singleShot(0, self.rescan)
        super().changeEvent(e)

    def _on_search(self, text: str):
        if self.pages.currentWidget() is not self.library:
            self.goto("library")
        self.library.set_search(text)

    def toggle_view(self):
        self.config.view_mode = "list" if self.config.view_mode == "grid" else "grid"
        self.config.save()
        self.library.set_view_mode(self.config.view_mode)
        self.view_act.setText("그리드 보기" if self.config.view_mode == "list" else "목록 보기")

    def _detail_action(self, action: str):
        items = self.library.selected()
        if not items:
            return
        if action == "reveal":
            reveal(self.lib.abs(items[0].rel_path))
        elif action in ("enable", "disable"):
            self.set_status(items, "active" if action == "enable" else "disabled")
        elif action == "trash":
            names = "\n".join(f"· {i.name}" for i in items[:10]) + ("\n…" if len(items) > 10 else "")
            if confirm(self, "휴지통으로 삭제", f"{len(items)}개를 휴지통으로 보낼까요?\n\n{names}\n\n(휴지통에서 복원할 수 있습니다)"):
                _n, failed = self.lib.trash(items)
                self.library.reload()
                if failed:
                    QMessageBox.warning(self, "휴지통으로 삭제", "휴지통으로 보내지 못한 항목이 있습니다. 게임이 켜져 있으면 끈 뒤 다시 해 보세요.\n\n" + "\n".join(failed[:10]))

    def set_status(self, items: list, to: str):
        if any(i.status == "quarantine" for i in items):
            QMessageBox.information(self, "S4CCM", "충돌 테스트로 빠진 항목은 충돌 테스트 화면에서 원위치하세요.")
            return
        todo = [i for i in items if i.status != to]
        if not todo:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            n = self.lib.set_status(todo, to, ("활성화" if to == "active" else "비활성화") + f" ({len(todo)}개)")
        finally:
            QApplication.restoreOverrideCursor()
        self.library.reload()
        self.status_label.setText(f"{n}개를 {STATUS_LABEL[to]} 상태로 바꿨습니다.")

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "추가할 모드 선택", "", ADD_FILTER)
        if files:
            self.import_paths(files)

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "추가할 모드 폴더 선택")
        if d:
            self.import_paths([d])

    def import_paths(self, paths_: list[str]):
        if not self.config.sims_dir:
            QMessageBox.warning(self, "모드 추가", "먼저 설정에서 심즈4 모드 폴더를 지정하세요.")
            return
        managed = [os.path.normcase(os.path.abspath(os.path.join(self.config.sims_dir, d)))
                   for d in ("Mods", "Tray", paths.DISABLED_DIR, paths.QUARANTINE_DIR)]

        def overlaps(p: str) -> bool:
            p = os.path.normcase(os.path.abspath(p))
            return any(p == m or p.startswith(m + os.sep) or m.startswith(p + os.sep) for m in managed)

        paths_ = [p for p in paths_ if not overlaps(p)]
        if not paths_:
            QMessageBox.information(self, "모드 추가", "이미 모드 폴더 안에 있는 파일입니다. [다시 스캔]을 누르세요.")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            plan = importer.analyze(self.lib, paths_)
        finally:
            QApplication.restoreOverrideCursor()
        if not plan.groups:
            QMessageBox.warning(self, "모드 추가", "추가할 모드나 트레이 파일이 없습니다.\n\n" + "\n".join(plan.errors))
            plan.cleanup()
            return
        dlg = ImportDialog(plan, self)
        if not dlg.exec():
            plan.cleanup()
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            n, added = importer.execute(self.lib, plan)
        except Exception as ex:
            plan.cleanup()
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "모드 추가", f"추가 중 오류: {ex}")
            return
        QApplication.restoreOverrideCursor()
        self.library.reload()
        if plan.trays_added:
            self.tray._loaded_dir = None
        if plan.trays_added and not plan.mods_added:
            self.goto("tray")
        else:
            self.goto("library")
        bits = []
        if plan.mods_added:
            bits.append(f"모드 파일 {plan.mods_added}개")
        if plan.trays_added:
            bits.append(f"트레이 항목 {plan.trays_added}개")
        self.status_label.setText(", ".join(bits) + "를 추가했습니다." if bits else "추가한 파일이 없습니다.")
        if any(g.mode == importer.MODE_SCRIPT for g in plan.groups):
            self._check_script_options()

    def _check_script_options(self):
        opts = resourcecfg.read_options(self.config.sims_dir)
        if not opts:
            return
        off = []
        if opts.get("modsdisabled", "0") != "0":
            off.append("커스텀 콘텐츠와 모드 허용")
        if opts.get("scriptmodsenabled", "0") != "1":
            off.append("스크립트 모드 허용")
        if not off:
            return
        if not confirm(self, "게임 옵션", "게임 옵션에서 다음 항목이 꺼져 있어 스크립트 모드가 작동하지 않습니다:\n\n· "
                       + "\n· ".join(off) + "\n\n지금 켤까요? (게임이 꺼져 있어야 합니다)"):
            return
        if launcher.game_running():
            QMessageBox.warning(self, "게임 옵션", "게임이 실행 중입니다. 게임을 끈 뒤 Resource.cfg 화면에서 켜주세요.")
            return
        resourcecfg.set_option(self.config.sims_dir, "modsdisabled", "0")
        resourcecfg.set_option(self.config.sims_dir, "scriptmodsenabled", "1")
        self.status_label.setText("게임 옵션에서 모드와 스크립트 모드를 켰습니다.")

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if files:
            QTimer.singleShot(0, lambda: self.import_paths(files))

    def closeEvent(self, e):
        if is_busy():
            e.ignore()
            QTimer.singleShot(300, self.close)
            return
        if not self.bisect.confirm_leave(closing=True):
            e.ignore()
            return
        self.library.detail.show_items([])
        self._closing = True
        for t in (self._thread, self.dupes._thread):
            if t is not None:
                t.quit()
                t.wait()
        super().closeEvent(e)

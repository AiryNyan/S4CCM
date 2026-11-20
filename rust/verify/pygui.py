"""Python 판 화면을 Rust+Qt 판의 selfTest 와 같은 순서로 이미지로 저장한다.

    set S4CCM_HOME=...  &&  python pygui.py <출력 폴더>
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
out = sys.argv[1]
os.makedirs(out, exist_ok=True)

from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication

from s4ccm.config import Config
from s4ccm.gui.common import apply_theme
from s4ccm.gui.main_window import MainWindow

app = QApplication(sys.argv)
app.setApplicationName("S4CCM")
config = Config.load()
apply_theme(app, config.theme)
win = MainWindow(config)
win.show()


def shot(name):
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QApplication.processEvents()
    QApplication.processEvents()
    win.grab().save(os.path.join(out, name + ".png"))


def run():
    from s4ccm.gui.loading import is_busy
    if is_busy() or not win.status_label.text():
        QTimer.singleShot(500, run)
        return
    shot("library_grid")
    win.library.grid.setCurrentIndex(win.library.proxy.index(0, 0))
    shot("library_selected")
    win._detail_action("disable")
    shot("library_disabled")
    win.library.set_scope("비활성", group=None, category=None, status="disabled")
    win.library.grid.setCurrentIndex(win.library.proxy.index(0, 0))
    shot("scope_disabled")
    win._detail_action("enable")
    win.library.set_scope("전체", group=None, category=None, status="installed")
    win.search.setText("hair")
    shot("search")
    win.search.clear()
    win.library.set_view_mode("list")
    shot("library_list")
    win.library.set_view_mode("grid")
    for page in ("tray", "organize", "cfg", "bisect", "dupes", "settings"):
        win.goto(page)
        shot(page)
        if page == "tray":
            win.tray.list.setCurrentRow(0)
            shot("tray_selected")
    app.quit()


QTimer.singleShot(1500, run)
app.exec()

from __future__ import annotations

import ctypes
import faulthandler
import os
import sys
import time
import traceback

from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from ..config import Config, app_dir
from .common import apply_theme
from .main_window import MainWindow

ICON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icon.png")
CRASH_LOG = os.path.join(app_dir(), "crash.log")


def _open_log():
    if os.path.exists(CRASH_LOG) and os.path.getsize(CRASH_LOG) > 1_000_000:
        os.replace(CRASH_LOG, CRASH_LOG + ".old")
    log = open(CRASH_LOG, "a", encoding="utf-8", buffering=1)
    log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} start ===\n")
    return log


def _install_logging(log) -> None:
    faulthandler.enable(log, all_threads=True)

    def qt_messages(mode, _ctx, msg):
        if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            log.write(f"[Qt {mode.name}] {msg}\n")

    qInstallMessageHandler(qt_messages)

    def excepthook(kind, value, tb):
        text = "".join(traceback.format_exception(kind, value, tb))
        log.write(text)
        app = QApplication.instance()
        if app is not None:
            QMessageBox.warning(None, "S4CCM 오류",
                                f"예상하지 못한 오류가 생겼습니다.\n\n{value}\n\n자세한 내용은 다음 파일에 저장했습니다:\n{CRASH_LOG}")

    sys.excepthook = excepthook


def main() -> int:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("S4CCM")
    except (AttributeError, OSError):
        pass
    log = _open_log()
    _install_logging(log)
    app = QApplication(sys.argv)
    app.setApplicationName("S4CCM")
    if os.path.exists(ICON):
        app.setWindowIcon(QIcon(ICON))
    config = Config.load()
    apply_theme(app, config.theme)
    win = MainWindow(config)
    win.show()
    code = app.exec()
    log.write(f"=== exit {code} ===\n")
    return code

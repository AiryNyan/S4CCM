from __future__ import annotations

import os
from typing import Callable

from PySide6.QtCore import QEvent, QEventLoop, QObject, Qt, QThread, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from .common import tok

Progress = Callable[..., None]

_busy = 0


def is_busy() -> bool:
    return _busy > 0


class LoadingOverlay(QWidget):
    def __init__(self, window: QWidget, title: str):
        super().__init__(window)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setFocusPolicy(Qt.StrongFocus)
        card = QFrame(self)
        card.setObjectName("card")
        card.setFixedWidth(440)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)
        self.title = QLabel(title)
        self.title.setObjectName("h2")
        lay.addWidget(self.title)
        row = QHBoxLayout()
        self.phase = QLabel("준비 중…")
        self.phase.setObjectName("muted")
        self.count = QLabel("")
        self.count.setObjectName("muted")
        row.addWidget(self.phase, 1)
        row.addWidget(self.count)
        lay.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setObjectName("gauge")
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 0)
        lay.addWidget(self.bar)
        bottom = QHBoxLayout()
        self.detail = QLabel("")
        self.detail.setObjectName("muted")
        self.percent = QLabel("")
        self.percent.setObjectName("percent")
        self.percent.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bottom.addWidget(self.detail, 1)
        bottom.addWidget(self.percent)
        self.count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.count.setMinimumWidth(self.count.fontMetrics().horizontalAdvance("000000 / 000000"))
        self.percent.setMinimumWidth(self.percent.fontMetrics().horizontalAdvance("100%") + 12)
        lay.addLayout(bottom)
        self.card = card
        window.installEventFilter(self)
        self._fit()

    def _fit(self) -> None:
        self.setGeometry(self.parentWidget().rect())
        self.card.adjustSize()
        self.card.move((self.width() - self.card.width()) // 2, (self.height() - self.card.height()) // 2)

    def eventFilter(self, obj, e) -> bool:
        if e.type() == QEvent.Resize:
            self._fit()
        return False

    def paintEvent(self, _e) -> None:
        dim = QColor(tok("window"))
        dim.setAlpha(200)
        QPainter(self).fillRect(self.rect(), dim)

    def mousePressEvent(self, e):
        e.accept()

    def keyPressEvent(self, e):
        e.accept()

    def set_progress(self, i: int, n: int, text: str = "", phase: str = "") -> None:
        if phase:
            self.phase.setText(phase)
        if n > 0:
            self.bar.setRange(0, n)
            self.bar.setValue(min(i, n))
            self.count.setText(f"{min(i, n)} / {n}")
            self.percent.setText(f"{int(100 * min(i, n) / n)}%")
        name = os.path.basename(text.rstrip("/\\")) if text else ""
        fm = self.detail.fontMetrics()
        self.detail.setText(fm.elidedText(name, Qt.ElideMiddle, 300))


class _Worker(QObject):
    progress = Signal(int, int, str, str)
    finished = Signal()

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.result = None
        self.error: Exception | None = None

    def run(self) -> None:
        try:
            self.result = self.fn(lambda i, n, text="", phase="": self.progress.emit(i, n, text, phase))
        except Exception as ex:
            self.error = ex
        self.finished.emit()


def run_with_loading(parent: QWidget, title: str, fn: Callable[[Progress], object]):
    global _busy
    window = parent.window()
    overlay = LoadingOverlay(window, title)
    overlay.show()
    overlay.raise_()
    overlay.setFocus()
    thread = QThread()
    worker = _Worker(fn)
    worker.moveToThread(thread)
    loop = QEventLoop()
    worker.progress.connect(overlay.set_progress)
    worker.finished.connect(loop.quit)
    thread.started.connect(worker.run)
    _busy += 1
    try:
        thread.start()
        loop.exec()
    finally:
        thread.quit()
        thread.wait()
        _busy -= 1
        window.removeEventFilter(overlay)
        overlay.deleteLater()
    if worker.error is not None:
        raise worker.error
    return worker.result

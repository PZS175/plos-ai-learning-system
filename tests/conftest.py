"""pytest 公共装置。

关键点：整个测试会话只允许存在一个 QApplication，且必须一直存活。

若某个测试模块自建的 QApplication 在该模块结束后被 GC 回收，
`QThreadPool.globalInstance()` 会随之销毁，而 plos.ui.workers.ThreadPool
仍缓存着这个已销毁的对象，后续模块一旦把任务丢进线程池就会直接
崩溃（access violation / wrapped C/C++ object has been deleted）。
因此这里用会话级 fixture 提前创建并**长期持有**应用实例。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session", autouse=True)
def qapp():
    """离屏 QApplication（会话级 + 自动启用，全程保活）。"""
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(["plos_tests"])
    return app

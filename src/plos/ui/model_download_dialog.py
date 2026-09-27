"""模型下载对话框：一键拉取 Ollama 模型，带实时进度。

通过 QProcess 运行 `ollama pull <模型名>`，解析输出中的百分比
进度并逐模型显示状态；下载完成后回调，供启动向导重新探测。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PyQt6.QtCore import QProcess
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from ..utils.logger import get_logger

logger = get_logger("ui.model_download")


class ModelDownloadDialog(QDialog):
    """顺序下载缺失模型；全部完成后 enable finish。"""

    def __init__(self, models: List[Tuple[str, str]], parent: Optional[QDialog] = None):
        """models: [(模型名, 展示标签), ...]，按序下载。"""
        super().__init__(parent)
        self._queue = list(models)
        self._proc: Optional[QProcess] = None
        self._failed = False

        self.setWindowTitle("下载模型")
        self.resize(520, 200)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self.status_label = QLabel("准备下载…")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.close_btn = QPushButton("后台下载并关闭")
        self.close_btn.clicked.connect(self._background_and_close)
        btn_row.addWidget(self.close_btn)
        layout.addLayout(btn_row)

        self._next()

    # ---- 下载循环 ----

    def _next(self) -> None:
        if not self._queue:
            if not self._failed:
                self.status_label.setText("全部模型下载完成！点击关闭返回。")
                self.close_btn.setText("完成")
            return
        model, label = self._queue[0]
        self.status_label.setText(f"正在下载 {label}（{model}）…")
        self.progress.setValue(0)

        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._proc.readyReadStandardOutput.connect(self._on_output)
        finished = getattr(self._proc, "finished")
        finished.connect(self._on_finished)
        logger.info("Pulling ollama model: %s", model)
        self._proc.start("ollama", ["pull", model])

    def _on_output(self) -> None:
        if self._proc is None:
            return
        text = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "ignore")
        import re

        percents = re.findall(r"(\d{1,3})%", text)
        if percents:
            try:
                self.progress.setValue(min(100, int(percents[-1])))
            except ValueError:
                pass

    def _on_finished(self, code: int, _status) -> None:
        if self._queue:
            self._queue.pop(0)
        if code != 0:
            self._failed = True
            self.status_label.setText(
                "下载失败：请检查网络后点「重试」，或复制启动页命令手动拉取。"
            )
            self.close_btn.setText("重试")
            self.close_btn.clicked.disconnect()
            self.close_btn.clicked.connect(self._retry)
            return
        self._next()

    def _retry(self) -> None:
        self.close_btn.setText("后台下载并关闭")
        self.close_btn.clicked.disconnect()
        self.close_btn.clicked.connect(self._background_and_close)
        self._next()

    def _background_and_close(self) -> None:
        """关闭对话框但下载继续在后台进行（QProcess 随对话框销毁则中断，
        这里改为脱离父对象由进程持有，应用退出时自然终止）。"""
        if self._proc is not None:
            self._proc.setParent(None)
        self.accept()

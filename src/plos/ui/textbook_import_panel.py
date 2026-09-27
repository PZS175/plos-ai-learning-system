"""智慧教育平台教材导入面板。

使用 QWebEngineView 内置浏览器加载 https://basic.smartedu.cn，
由用户在窗口内手动登录、浏览并打开教材详情页，
点击【导入当前教材到知识库】后通过浏览器自身会话来触发教材 PDF 下载，
下载完成后自动调用现有知识库接口完成文档入库。

约束：
- 不编写后台爬虫，不伪造请求头，不生成接口签名；
- 所有网络请求均交给 QWebEngine 浏览器内核完成；
- 仅导入用户当前手动打开的单本教材，禁止批量循环；
- 两次导入操作强制最小间隔 3 秒。
"""

from __future__ import annotations

import time
import urllib.parse
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..services import RAGService
from ..services.user_service import UserService
from ..utils.logger import get_logger
from ..utils.paths import get_data_dir
from .toast import ToastManager
from .ui_utils import apply_glass_style, set_primary_button_style, show_error
from .workers import RAGAddWorker, ThreadPool

logger = get_logger("ui.textbook_import_panel")

TEXTBOOK_BASE_URL = "https://basic.smartedu.cn"
DETAIL_PATH = "tchMaterial/detail"
IMPORT_COOLDOWN_SECONDS = 3


try:
    from PyQt6.QtWebEngineCore import (
        QWebEngineDownloadRequest,
        QWebEnginePage,
        QWebEngineProfile,
    )
    from PyQt6.QtWebEngineWidgets import QWebEngineView

    _WEBENGINE_AVAILABLE = True
except Exception as exc:  # pragma: no cover
    logger.warning("PyQt6-WebEngine not available: %s", exc)
    _WEBENGINE_AVAILABLE = False
    QWebEngineView = QWidget  # type: ignore[misc, assignment]
    QWebEngineProfile = object  # type: ignore[misc, assignment]
    QWebEnginePage = object  # type: ignore[misc, assignment]
    QWebEngineDownloadRequest = object  # type: ignore[misc, assignment]


class TextbookImportPanel(QWidget):
    """智慧教育平台教材导入面板。"""

    def __init__(
        self,
        rag_service: RAGService,
        user_service: Optional[UserService] = None,
        toast_manager: Optional[ToastManager] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.rag_service = rag_service
        self.user_service = user_service
        self.toast_manager = toast_manager

        self._last_import_time = 0.0
        self._expecting_download = False
        self._pending_content_id: Optional[str] = None
        self._pending_file_path: Optional[Path] = None

        self._download_dir = get_data_dir() / "downloads" / "textbooks"
        self._download_dir.mkdir(parents=True, exist_ok=True)
        self._cache_dir = get_data_dir() / "webengine_cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        self._build_ui()

    def refresh_data(self) -> None:
        """用户切换后的刷新：本面板无跨用户持久状态，导入记录按用户隔离存储。"""
        return

    def _build_ui(self) -> None:
        """构建界面布局。"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        main_layout.addWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 顶部工具栏
        toolbar = QHBoxLayout()
        toolbar.setSpacing(12)

        self.url_label = QLabel("未加载页面")
        self.url_label.setWordWrap(True)
        self.url_label.setStyleSheet("font-size: 12px;")
        toolbar.addWidget(self.url_label, 1)

        open_external_btn = QPushButton("外部浏览器打开")
        open_external_btn.setToolTip(
            "在系统浏览器中打开当前页\n"
            "适合在网站登录账号后正常浏览教材；下载 PDF 后可在知识库手动上传"
        )
        open_external_btn.clicked.connect(self._open_in_external_browser)
        toolbar.addWidget(open_external_btn)

        self.import_btn = QPushButton("导入当前教材到知识库")
        set_primary_button_style(self.import_btn)
        self.import_btn.setEnabled(False)
        self.import_btn.setToolTip("仅在教材详情页可用")
        self.import_btn.clicked.connect(self._on_import_clicked)
        toolbar.addWidget(self.import_btn)

        layout.addLayout(toolbar)

        # 浏览器主体
        if _WEBENGINE_AVAILABLE:
            self.browser = QWebEngineView()
            self.profile = QWebEngineProfile("TextbookImport", self.browser)
            self.profile.setDownloadPath(str(self._download_dir))
            self.profile.setCachePath(str(self._cache_dir / "cache"))
            self.profile.setPersistentStoragePath(str(self._cache_dir / "storage"))
            self.profile.downloadRequested.connect(self._on_download_requested)

            page = QWebEnginePage(self.profile, self.browser)
            self.browser.setPage(page)
            self.browser.setUrl(QUrl(TEXTBOOK_BASE_URL))
            self.browser.urlChanged.connect(self._on_url_changed)
            self.browser.titleChanged.connect(self._on_title_changed)
        else:
            self.browser = QLabel("缺少 PyQt6-WebEngine，无法使用教材导入功能。\n请安装依赖后重启应用。")
            self.browser.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.browser.setProperty("glass", True)
            apply_glass_style(self.browser)

        layout.addWidget(self.browser, 1)

        # 进度条（下载/导入时显示）
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # 版权提示
        copyright_label = QLabel(
            "教材版权归对应出版社所有，仅限个人学习研究，禁止商用分发"
        )
        copyright_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        copyright_label.setWordWrap(True)
        copyright_label.setStyleSheet("color: #8A93A8; font-size: 12px;")
        layout.addWidget(copyright_label)

    def _open_in_external_browser(self) -> None:
        """用系统默认浏览器打开当前页（登录/阅读更顺畅的兜底路径）。"""

        from PyQt6.QtGui import QDesktopServices

        target = self.browser.url()
        if target.isEmpty():
            target = QUrl(TEXTBOOK_BASE_URL)
        QDesktopServices.openUrl(target)

    def _on_url_changed(self, url: QUrl) -> None:
        """URL 变化时更新地址栏并启用/禁用导入按钮。"""
        url_str = url.toString()
        self.url_label.setText(url_str)
        is_detail = DETAIL_PATH in url_str
        self.import_btn.setEnabled(is_detail and not self.progress_bar.isVisible())
        self.import_btn.setToolTip(
            "导入当前打开的教材" if is_detail else "请打开教材详情页后再导入"
        )

    def _on_title_changed(self, title: str) -> None:
        """浏览器标题变化（当前无需额外处理）。"""

    def _current_user_id(self) -> int:
        """获取当前用户 ID，未登录则返回 0。"""
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def _toast(self, message: str, level: str = "info") -> None:
        """显示一条非阻塞 Toast 提示。"""
        if self.toast_manager is not None:
            self.toast_manager.show(message, level=level)
        else:
            QMessageBox.information(self, "提示", message)

    def _on_import_clicked(self) -> None:
        """点击导入按钮后的处理流程。"""
        if not _WEBENGINE_AVAILABLE:
            self._toast("缺少 PyQt6-WebEngine，无法导入教材", level="error")
            return

        now = time.time()
        if now - self._last_import_time < IMPORT_COOLDOWN_SECONDS:
            self._toast(
                f"两次导入操作请间隔至少 {IMPORT_COOLDOWN_SECONDS} 秒", level="warning"
            )
            return

        current_url = self.browser.url().toString()
        if DETAIL_PATH not in current_url:
            self._toast("请先在内置浏览器中打开一本教材详情页", level="warning")
            return

        parsed = urllib.parse.urlparse(current_url)
        params = urllib.parse.parse_qs(parsed.query)
        content_id = params.get("contentId", [None])[0]
        if not content_id:
            # 兼容 contentId 作为路径片段的情况
            segments = [s for s in parsed.path.split("/") if s]
            content_id = segments[-1] if segments else "unknown"

        self._pending_content_id = content_id
        self._last_import_time = now
        self._expecting_download = True
        self.import_btn.setEnabled(False)
        self.progress_bar.setVisible(True)

        # 读取 localStorage 鉴权凭证，确认用户已登录
        self.browser.page().runJavaScript(
            "localStorage.getItem('ND_UC_AUTH')",
            self._on_auth_token_ready,
        )

    def _on_auth_token_ready(self, token: object) -> None:
        """拿到 localStorage token 后的回调。"""
        if not token:
            self.progress_bar.setVisible(False)
            self.import_btn.setEnabled(DETAIL_PATH in self.browser.url().toString())
            self._toast("登录凭证已过期，请重新在内置浏览器登录账号", level="error")
            return

        # 触发页面自身的下载入口，由浏览器内核完成后续请求
        trigger_script = """
        (function(){
            var els = Array.from(document.querySelectorAll('button, a, div, span, i, em'));
            for (var i = 0; i < els.length; i++) {
                var el = els[i];
                var text = (el.textContent || el.getAttribute('title') || '').trim();
                if (text.indexOf('下载') !== -1 || el.getAttribute('download')) {
                    el.click();
                    return 'clicked-download';
                }
            }
            var pdfLink = Array.from(document.querySelectorAll('a')).find(function(a) {
                return (a.href || '').toLowerCase().endsWith('.pdf');
            });
            if (pdfLink) {
                pdfLink.click();
                return 'clicked-pdf-link';
            }
            return 'no-download-button';
        })()
        """
        self.browser.page().runJavaScript(trigger_script, self._on_trigger_result)

    def _on_trigger_result(self, result: object) -> None:
        """触发下载后的回调。"""
        result_str = str(result) if result is not None else ""
        if result_str not in ("clicked-download", "clicked-pdf-link"):
            self._expecting_download = False
            self.progress_bar.setVisible(False)
            self.import_btn.setEnabled(DETAIL_PATH in self.browser.url().toString())
            self._toast(
                "未在当前页面检测到教材下载入口，请切换到可下载页面后重试",
                level="error",
            )

    def _on_download_requested(self, download: object) -> None:
        """浏览器发起下载请求时的处理。"""
        if not isinstance(download, QWebEngineDownloadRequest):
            return

        if not self._expecting_download:
            download.cancel()
            return

        self._expecting_download = False
        filename = download.suggestedFileName() or f"{self._pending_content_id or 'textbook'}.pdf"
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"

        try:
            download.setDownloadDirectory(str(self._download_dir))
            download.setDownloadFileName(filename)
        except Exception as e:
            logger.warning("Failed to set download path: %s", e)

        self._pending_file_path = Path(download.downloadDirectory()) / download.downloadFileName()
        download.accept()
        download.stateChanged.connect(lambda: self._on_download_state_changed(download))

    def _on_download_state_changed(self, download: object) -> None:
        """下载状态变化时的处理。"""
        if not isinstance(download, QWebEngineDownloadRequest):
            return
        if not download.isFinished():
            return

        state = download.state()
        self.progress_bar.setVisible(False)

        if state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            file_path = self._pending_file_path
            if not file_path or not file_path.exists():
                file_path = Path(download.downloadDirectory()) / download.downloadFileName()
            self._import_to_knowledge(file_path)
        elif state == QWebEngineDownloadRequest.DownloadState.DownloadCancelled:
            self._toast("教材下载已取消", level="warning")
            self.import_btn.setEnabled(DETAIL_PATH in self.browser.url().toString())
        else:
            logger.warning("Textbook download failed or interrupted: state=%s", state)
            self._toast("教材下载失败，请检查登录状态或网络连接后重试", level="error")
            self.import_btn.setEnabled(DETAIL_PATH in self.browser.url().toString())

    def _import_to_knowledge(self, file_path: Path) -> None:
        """将下载完成的 PDF 加入知识库。"""
        if not file_path.exists():
            self._toast("教材文件未找到，请重试", level="error")
            self.import_btn.setEnabled(DETAIL_PATH in self.browser.url().toString())
            return

        try:
            worker = RAGAddWorker(self.rag_service, file_path)
            worker.signals.result.connect(self._on_import_finished)
            worker.signals.error.connect(self._on_import_error)
            ThreadPool.start_worker(worker)
        except Exception as e:
            logger.error("Failed to start textbook import worker: %s", e)
            self.progress_bar.setVisible(False)
            show_error(self, "导入失败", str(e))
            self.import_btn.setEnabled(DETAIL_PATH in self.browser.url().toString())

    def _on_import_finished(self, document_id: int) -> None:
        """文档入库成功。"""
        self.progress_bar.setVisible(False)
        self._toast(f"教材已导入知识库（文档 ID: {document_id}）", level="success")
        self.import_btn.setEnabled(DETAIL_PATH in self.browser.url().toString())

    def _on_import_error(self, message: str) -> None:
        """文档入库失败。"""
        self.progress_bar.setVisible(False)
        if "401" in message or "unauthorized" in message.lower():
            self._toast("登录凭证失效，请重新在内置浏览器登录账号", level="error")
        else:
            self._toast(f"导入失败：{message}", level="error")
        self.import_btn.setEnabled(DETAIL_PATH in self.browser.url().toString())

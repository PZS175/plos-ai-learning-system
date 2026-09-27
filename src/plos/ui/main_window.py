"""主窗口。

左侧玻璃拟态导航栏 + 右侧堆叠面板的经典桌面应用布局。
所有业务面板通过 services 层注入，不直接写业务逻辑。
新增顶部标题区，随页面切换自动更新页面名与功能说明。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
)
from PyQt6.QtGui import QAction, QKeySequence, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .theme_manager import ThemeManager
from .toast import ToastManager
from ..ai import ModelManager
from ..config.hardware import detect_hardware, get_hardware_tier, HardwareTier
from ..services import (
    BackupService,
    ChatService,
    DiagnosticReportService,
    DiagramService,
    ErrorBookService,
    ErrorPaperService,
    ExamService,
    FlashcardService,
    ASRService,
    ImageEnhanceService,
    InputRouter,
    KnowledgeGraphService,
    LearningPackageService,
    MindMapService,
    NoteAnnotationService,
    NoteService,
    OCRService,
    OllamaService,
    PracticeService,
    QuestionImportService,
    RAGService,
    ReviewService,
    SearchService,
    SessionManager,
    StatisticsService,
    StudyPlanService,
    TeachingStyleService,
    TerminologyService,
    TextbookCacheService,
    TTSService,
    UserService,
    WebClipService,
)
from ..utils.logger import get_logger
from .chat_panel import ChatPanel
from .dashboard_panel import DashboardPanel
from .diagnostic_report_panel import DiagnosticReportPanel
from .errorbook_panel import ErrorBookPanel
from .favorites_panel import FavoritesPanel
from .flashcard_panel import FlashcardPanel
from .exam_panel import ExamPanel
from .hotkey_listener import HotkeyListener
from .asr_panel import ASRPanel
from .knowledge_graph_panel import KnowledgeGraphPanel
from .knowledge_panel import KnowledgePanel
from .learning_package_panel import LearningPackagePanel
from .ocr_panel import OCRPanel
from .practice_panel import PracticePanel
from .settings_panel import SettingsPanel
from .study_plan_panel import StudyPlanPanel
from .terminology_panel import TerminologyPanel
from .textbook_import_panel import TextbookImportPanel
from .tray_icon import TrayIcon
from .ui_utils import create_header_widget
from .user_panel import UserPanel
from .log_panel import LogPanel
from .ollama_panel import OllamaManagerPanel
from .onboarding_wizard import OnboardingWizard
from .plugin_panel import PluginPanel
from ..plugins import PluginManager
from .textbook_cache_panel import TextbookCachePanel
from .notes_panel import NotesPanel
from .login_dialog import LoginDialog

logger = get_logger("ui.main_window")


def _optional_nav_keys(tier: HardwareTier, voice_enabled: bool = False) -> set:
    """按硬件分级返回可见的「可选」导航入口 key 集合。

    - 低配（≤7GB）：全部隐藏，仅保留基础功能；
    - 中配（8~12GB）：知识图谱、教材缓存可见；语音需用户在设置中显式开启；
    - 高配（>12GB）：知识图谱、教材缓存、语音全部可见。

    规则集中在此，便于回归测试，避免各处 if 判断漂移。
    """
    keys: set = set()
    if tier in (HardwareTier.MID, HardwareTier.HIGH):
        keys.add("knowledge_graph")
        keys.add("textbook_cache")
    if tier == HardwareTier.HIGH or (tier == HardwareTier.MID and voice_enabled):
        keys.add("asr")
    return keys


# 页面标题与副标题映射
_PANEL_HEADERS: Dict[str, tuple] = {
    "dashboard": ("总览", "学习数据一览，快速进入核心功能"),
    "chat": ("AI 对话", "基于本地模型或云端 API 的多轮学习助手"),
    "ocr": ("OCR / 拍题", "截图、粘贴或上传图片，提取文字与解题"),
    "knowledge": ("知识库", "管理学习文档，RAG 语义检索增强回答"),
    "favorites": ("收藏夹", "收藏的错题与知识库文档"),
    "errorbook": ("错题本", "录入、编辑错题，跟踪掌握度与知识点"),
    "flashcard": ("闪卡复习", "基于 SM-2 算法的间隔重复记忆"),
    "notes": ("笔记", "富文本笔记、截图批注与 AI 辅助"),
    "practice": ("自适应练习", "按知识点自动出题，难度随正确率动态调整"),
    "exam": ("试卷生成", "按知识点组卷，导出试卷与参考答案"),
    "knowledge_graph": ("知识图谱", "知识点关联网络与薄弱节点可视化"),
    "diagnostic_report": ("学习诊断", "一键生成学习复盘报告与后续建议"),
    "terminology": ("术语词典", "阅读笔记与 OCR 文本中提取的专业术语库"),
    "study_plan": ("学习计划", "AI 生成的周学习任务与进度追踪"),
    "learning_package": ("学习包", "打包、导入与分享学习资料"),
    "asr": ("语音转文字", "本地录音与离线 Whisper 转写"),
    "textbook_import": ("教材导入", "通过内置浏览器从智慧教育平台导入教材"),
    "user": ("用户管理", "多账户切换与数据隔离管理"),
    "settings": ("模型管理", "切换本地/云端后端，选择并测试模型"),
    "logs": ("系统日志", "实时查看、搜索与打包运行日志"),
    "ollama": ("本地模型", "查看与管理 Ollama 本地模型"),
    "plugins": ("插件管理", "扩展接口与插件管理"),
    "textbook_cache": ("教材缓存", "离线教材缓存空间管理"),
}


def _is_dark_theme() -> bool:
    """基于当前调色板判断是否为暗色主题。"""
    palette = QApplication.palette()
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


class _NavItemWidget(QWidget):
    """侧边栏菜单项：图标 + 文字，静态扁平样式，支持折叠后仅显示图标。"""

    def __init__(
        self,
        icon: str,
        text: str,
        theme_manager: ThemeManager,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._text = text
        self._checked = False
        self._collapsed = False
        self._hovered = False
        self._theme_manager = theme_manager

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 16, 0)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        self._icon_label = QLabel(icon)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setProperty("nav_icon", True)
        layout.addWidget(self._icon_label)

        self._text_label = QLabel(text)
        self._text_label.setProperty("nav_text", True)
        layout.addWidget(self._text_label)
        layout.addStretch()

        self.setMinimumHeight(36)
        self.setMaximumHeight(36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("nav_item", True)
        self._update_style()

    def setChecked(self, checked: bool) -> None:
        self._checked = checked
        self._update_style()

    def setCollapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self._text_label.setVisible(not collapsed)
        layout = self.layout()
        if isinstance(layout, QHBoxLayout):
            if collapsed:
                layout.setContentsMargins(20, 0, 20, 0)
                layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            else:
                layout.setContentsMargins(10, 0, 16, 0)
                layout.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self._update_style()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self._update_style()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self._update_style()
        super().leaveEvent(event)

    def _theme_colors(self) -> dict:
        return self._theme_manager.colors()

    def _update_style(self) -> None:
        """根据主题、选中态与 hover 状态刷新静态样式。"""
        c = self._theme_colors()
        fg = c["fg_primary"]
        fg_secondary = c["fg_secondary"]
        accent = c["accent"]
        accent_light = c["accent_light"]
        accent_lighter = c["accent_lighter"]
        border = "rgba(30, 111, 255, 0.35)"

        if self._checked:
            bg = accent_light
            text_color = accent
            weight = "700"
            border_style = f"1px solid {border}"
        elif self._hovered:
            bg = accent_lighter
            text_color = fg
            weight = "600"
            border_style = "1px solid transparent"
        else:
            bg = "transparent"
            text_color = fg_secondary
            weight = "600"
            border_style = "1px solid transparent"

        radius = "6px"
        self.setStyleSheet(
            f"""
            QWidget[nav_item="true"] {{
                background-color: {bg};
                border: {border_style};
                border-radius: {radius};
            }}
            QWidget[nav_item="true"] QLabel {{
                background-color: transparent;
                border: none;
            }}
            QWidget[nav_item="true"] QLabel[nav_icon="true"] {{
                color: {text_color};
                font-size: 14px;
                min-width: 20px;
                max-width: 20px;
            }}
            QWidget[nav_item="true"] QLabel[nav_text="true"] {{
                color: {text_color};
                font-size: 13px;
                font-weight: {weight};
                letter-spacing: 0.3px;
            }}
            """
        )


class MainWindow(QMainWindow):
    """PLOS AI 主窗口。"""

    def __init__(
        self,
        config: Dict[str, Any],
        model_manager: ModelManager,
        theme_manager: ThemeManager,
    ):
        super().__init__()
        self.config = config
        self.model_manager = model_manager
        self.theme_manager = theme_manager
        # 启动遮罩存在期间不显示主窗口：由启动页负责在遮罩淡出后再显示，
        # 避免主窗口标题栏 / 菜单栏 / 背景提前绘制到遮罩外圈
        self._deferred_show = True
        # 新手向导 / 账号选择窗延后到主窗口显示后再弹（否则会被全屏遮罩挡住）
        self._startup_flow_pending = False
        self.setWindowTitle("PLOS AI 个人学习操作系统")
        self.setMinimumSize(1280, 720)
        self.setAcceptDrops(True)

        # 业务服务初始化
        try:
            self.user_service = UserService()
            self.tts_service = TTSService()
            self.statistics_service = StatisticsService(user_service=self.user_service)
            self.review_service = ReviewService(user_service=self.user_service)
            self.textbook_cache_service = TextbookCacheService(user_service=self.user_service)
            self.question_import_service = QuestionImportService(user_service=self.user_service)
            self.note_service = NoteService(user_service=self.user_service)
            self.search_service = SearchService(user_service=self.user_service)
            self.session_manager = SessionManager(user_service=self.user_service)
            self.errorbook_service = ErrorBookService(
                user_service=self.user_service,
                model_manager=model_manager,
            )
            self.error_paper_service = ErrorPaperService(
                db=self.errorbook_service.db,
                errorbook_service=self.errorbook_service,
                user_service=self.user_service,
            )
            self.flashcard_service = FlashcardService(user_service=self.user_service)
            self.practice_service = PracticeService(
                user_service=self.user_service,
                model_manager=model_manager,
                errorbook_service=self.errorbook_service,
            )
            self.exam_service = ExamService(
                user_service=self.user_service,
                model_manager=model_manager,
            )
            self.rag_service = RAGService(model_manager, user_service=self.user_service)
            # 教学风格服务需先于 ChatService 初始化，以便注入 system prompt
            self.teaching_style_service = TeachingStyleService(
                user_service=self.user_service,
            )
            self.chat_service = ChatService(
                model_manager,
                self.session_manager,
                rag_service=self.rag_service,
                errorbook_service=self.errorbook_service,
                user_service=self.user_service,
                teaching_style_service=self.teaching_style_service,
            )
            self.ocr_service = OCRService(
                model_manager,
                user_service=self.user_service,
            )
            self.image_enhance_service = ImageEnhanceService()
            self.input_router = InputRouter(self.ocr_service, self.rag_service)
            self.study_plan_service = StudyPlanService(
                model_manager=model_manager,
                flashcard_service=self.flashcard_service,
                errorbook_service=self.errorbook_service,
                user_service=self.user_service,
            )
            # 错题本新增/掌握度变化需反向触发学习计划动态调优
            self.errorbook_service.set_study_plan_service(self.study_plan_service)
            self.note_annotation_service = NoteAnnotationService(user_service=self.user_service)
            self.knowledge_graph_service = KnowledgeGraphService(
                user_service=self.user_service,
            )
            self.diagnostic_report_service = DiagnosticReportService(
                user_service=self.user_service,
                knowledge_graph_service=self.knowledge_graph_service,
            )
            self.terminology_service = TerminologyService(
                user_service=self.user_service,
                model_manager=model_manager,
            )
            self.mindmap_service = MindMapService(
                model_manager=model_manager,
                user_service=self.user_service,
            )
            self.diagram_service = DiagramService(
                model_manager=model_manager,
                user_service=self.user_service,
            )
            self.asr_service = ASRService()
            self.web_clip_service = WebClipService(
                rag_service=self.rag_service,
                user_service=self.user_service,
            )
            self.backup_service = BackupService(
                user_service=self.user_service,
                errorbook_service=self.errorbook_service,
                flashcard_service=self.flashcard_service,
            )
            self.learning_package_service = LearningPackageService(
                user_service=self.user_service,
                errorbook_service=self.errorbook_service,
                flashcard_service=self.flashcard_service,
                study_plan_service=self.study_plan_service,
                rag_service=self.rag_service,
            )
        except Exception as e:
            logger.error("Failed to initialize services: %s", e)
            QMessageBox.critical(self, "启动失败", f"服务初始化失败：\n{e}")
            raise

        # 硬件分级：决定哪些功能入口可见
        try:
            self._hardware = detect_hardware()
            force_low = False
            try:
                row = self.user_service.db.fetchone(
                    "SELECT value FROM settings WHERE key = ?", ("force_low_spec",)
                )
                if row and row["value"] in ("1", "true", "yes", "on"):
                    force_low = True
            except Exception:
                pass
            self._hardware_tier = get_hardware_tier(self._hardware, force_low_spec=force_low)
            logger.info("Hardware tier: %s (force_low=%s)", self._hardware_tier.value, force_low)
        except Exception as e:
            logger.warning("Hardware detection failed, fallback to low tier: %s", e)
            self._hardware_tier = HardwareTier.LOW

        # Ollama 本地模型管理服务
        try:
            self.ollama_service = OllamaService()
        except Exception as e:
            logger.warning("OllamaService init failed: %s", e)
            self.ollama_service = None

        # Toast 反馈组件（同时注册为全局单例，供各面板 show_success 等直接调用）
        self.toast_manager = ToastManager(self)
        try:
            from .ui_utils import set_toast_manager

            set_toast_manager(self.toast_manager)
        except Exception as e:
            logger.warning("Failed to register toast manager: %s", e)

        # 插件管理器（内置 + 外部插件，启用状态持久化，加载失败不阻塞主程序）
        try:
            self.plugin_manager = PluginManager()
        except Exception as e:
            logger.warning("PluginManager init failed: %s", e)
            self.plugin_manager = None

        # UI 构建
        self._build_ui()
        self._build_task_status_bar()
        self.theme_manager.theme_changed.connect(self._refresh_nav_styles)
        self._build_menu()
        self._build_tray()
        self._build_hotkeys()
        self._check_backend_status()

        # 安装苹果式全局交互：动画时长上限、30fps、低配降级、闲置暂停
        try:
            from .interactions import install_interactions

            self.animation_policy = install_interactions(
                tier=getattr(self, "_hardware_tier", None)
            )
        except Exception as e:
            self.animation_policy = None
            logger.warning("Failed to install interactions: %s", e)

        # 应用窗口几何状态（默认最大化或恢复上次大小）
        self._apply_window_geometry()

        # 应用演示模式（放大 UI 元素）
        try:
            demo_mode = self.config.setdefault("ui", {}).get("demo_mode", False)
            self.theme_manager.set_demo_mode(demo_mode)
        except Exception as e:
            logger.warning("Failed to apply demo mode: %s", e)

    def _build_task_status_bar(self) -> None:
        """底部状态栏：后台长任务（导入、批量解析）进度常驻显示。

        只在有任务时出现，任务结束后自动隐藏；任务在后台线程执行，
        状态栏出现期间界面其余部分照常可操作。
        """
        try:
            from .ui_utils import task_status_bus

            bar = self.statusBar()
            bar.setSizeGripEnabled(False)

            self.task_label = QLabel("")
            self.task_label.setObjectName("subtitle_label")
            self.task_progress = QProgressBar()
            self.task_progress.setRange(0, 100)
            self.task_progress.setFixedWidth(180)
            self.task_progress.setFixedHeight(8)
            self.task_progress.setTextVisible(False)
            bar.addWidget(self.task_label, 1)
            bar.addPermanentWidget(self.task_progress)
            self._active_tasks: set = set()
            bar.hide()

            bus = task_status_bus()
            bus.started.connect(self._on_task_started)
            bus.progressed.connect(self._on_task_progress)
            bus.finished.connect(self._on_task_finished)
        except Exception as e:
            logger.warning("Failed to build task status bar: %s", e)

    def _set_task_indeterminate(self) -> None:
        """进度未知时用不确定态；低配 / 闲置降级时不播放滚动动画。"""
        try:
            from .interactions import policy

            animated = policy().enabled
        except Exception:
            animated = True
        if animated:
            self.task_progress.setRange(0, 0)
        else:
            self.task_progress.setRange(0, 100)
            self.task_progress.setValue(0)

    def _on_task_started(self, task_id: str, message: str) -> None:
        """任务开始：先以不确定进度出现，拿到百分比后自动切换为确定进度。"""
        try:
            self._active_tasks.add(task_id)
            self.task_label.setText(message)
            self._set_task_indeterminate()
            self.statusBar().show()
        except Exception:
            pass

    def _on_task_progress(self, task_id: str, percent: int, message: str) -> None:
        try:
            if task_id not in self._active_tasks:
                self._active_tasks.add(task_id)
            if percent < 0:
                self._set_task_indeterminate()
            else:
                self.task_progress.setRange(0, 100)
                self.task_progress.setValue(max(0, min(100, int(percent))))
            if message:
                self.task_label.setText(message)
            self.statusBar().show()
        except Exception:
            pass

    def _on_task_finished(self, task_id: str) -> None:
        try:
            self._active_tasks.discard(task_id)
            if not self._active_tasks:
                self.statusBar().hide()
        except Exception:
            pass

    def _apply_window_geometry(self) -> None:
        """根据配置恢复窗口位置/大小，或默认最大化。"""
        ui_cfg = self.config.setdefault("ui", {})
        maximized = ui_cfg.get("window_maximized", True)
        width = ui_cfg.get("window_width", 1280)
        height = ui_cfg.get("window_height", 800)
        x = ui_cfg.get("window_x", -1)
        y = ui_cfg.get("window_y", -1)

        try:
            if maximized:
                self._maximize(deferred=self._deferred_show)
                return

            self.resize(width, height)
            if x >= 0 and y >= 0:
                self.move(x, y)
            else:
                # 未记录位置时居中显示
                screen = QApplication.primaryScreen()
                if screen is not None:
                    center = screen.availableGeometry().center()
                    frame = self.frameGeometry()
                    frame.moveCenter(center)
                    self.move(frame.topLeft())
        except Exception as e:
            logger.warning("Failed to apply window geometry: %s", e)
            self.resize(1280, 800)
            self._maximize(deferred=self._deferred_show)

    def _maximize(self, deferred: bool) -> None:
        """最大化主窗口；启动阶段只记录状态，等遮罩淡出后再由 show() 生效。"""
        if deferred:
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)
        else:
            self.showMaximized()

    def showEvent(self, event) -> None:  # noqa: N802
        """窗口一旦真正显示，后续几何变更即可直接生效。"""
        self._deferred_show = False
        super().showEvent(event)
        if self._startup_flow_pending:
            self._startup_flow_pending = False
            # 此时启动遮罩已淡出，新手向导 / 账号选择窗才看得见也点得到
            QTimer.singleShot(0, self._run_startup_flow)

    def _save_window_geometry(self) -> None:
        """保存当前窗口位置、大小及最大化状态到配置。"""
        try:
            ui_cfg = self.config.setdefault("ui", {})
            is_maximized = self.isMaximized()
            ui_cfg["window_maximized"] = is_maximized
            if not is_maximized and not self.isMinimized():
                geo = self.geometry()
                ui_cfg["window_width"] = geo.width()
                ui_cfg["window_height"] = geo.height()
                ui_cfg["window_x"] = geo.x()
                ui_cfg["window_y"] = geo.y()
            from ..config import save_config

            save_config(self.config)
        except Exception as e:
            logger.warning("Failed to save window geometry: %s", e)

    def _build_ui(self) -> None:
        """构建主界面布局。"""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.splitter)

        # 左侧导航栏
        self._nav_collapsed = False
        self._nav_expanded_width = 180
        self._nav_collapsed_width = 64
        self.nav_widget = QWidget()
        self.nav_widget.setFixedWidth(self._nav_expanded_width)
        nav_layout = QVBoxLayout(self.nav_widget)
        nav_layout.setContentsMargins(14, 18, 14, 18)
        nav_layout.setSpacing(0)

        # 顶部品牌（固定，不参与滚动）
        self.brand_label = QLabel("PLOS AI")
        self.brand_label.setStyleSheet(
            "font-size: 22px; font-weight: bold; color: #1E6FFF; padding-bottom: 8px;"
        )
        nav_layout.addWidget(self.brand_label)

        self.motto_label = QLabel("个人学习操作系统")
        self.motto_label.setStyleSheet(
            "font-size: 11px; color: #86909C; padding-bottom: 10px;"
        )
        nav_layout.addWidget(self.motto_label)

        self.stack = QStackedWidget()

        # 面板注册：仅首屏「总览」立即构建，其余面板延迟到首次打开时才实例化，
        # 以降低启动耗时与常驻内存（切换面板时会自动构建）
        self._panels: Dict[str, QWidget] = {}
        self._panel_factories: Dict[str, Any] = {}
        dashboard_panel = DashboardPanel(
            chat_service=self.chat_service,
            errorbook_service=self.errorbook_service,
            flashcard_service=self.flashcard_service,
            rag_service=self.rag_service,
            study_plan_service=self.study_plan_service,
            statistics_service=self.statistics_service,
            tts_service=self.tts_service,
            theme_manager=self.theme_manager,
            model_manager=self.model_manager,
            user_service=self.user_service,
            review_service=self.review_service,
        )
        dashboard_panel.navigate_to.connect(self._switch_panel)
        self._add_panel("dashboard", dashboard_panel)
        self._add_lazy_panel(
            "chat",
            lambda: ChatPanel(
                self.chat_service,
                rag_service=self.rag_service,
                errorbook_service=self.errorbook_service,
                tts_service=self.tts_service,
                note_service=self.note_service,
                config=self.config,
                theme_manager=self.theme_manager,
            ),
        )
        self._add_lazy_panel(
            "ocr",
            lambda: OCRPanel(
                self.ocr_service,
                errorbook_service=self.errorbook_service,
                terminology_service=self.terminology_service,
                image_enhance_service=self.image_enhance_service,
            ),
        )
        self._add_lazy_panel(
            "knowledge",
            lambda: KnowledgePanel(
                self.rag_service,
                note_annotation_service=self.note_annotation_service,
                mindmap_service=self.mindmap_service,
                tts_service=self.tts_service,
                web_clip_service=self.web_clip_service,
                diagram_service=self.diagram_service,
            ),
        )

        def _make_favorites_panel() -> QWidget:
            panel = FavoritesPanel(
                self.errorbook_service,
                rag_service=self.rag_service,
            )
            panel.navigate_to.connect(self._on_favorite_navigate)
            return panel

        self._add_lazy_panel("favorites", _make_favorites_panel)
        self._add_lazy_panel(
            "errorbook",
            lambda: ErrorBookPanel(
                self.errorbook_service,
                flashcard_service=self.flashcard_service,
                note_annotation_service=self.note_annotation_service,
                mindmap_service=self.mindmap_service,
                tts_service=self.tts_service,
                error_paper_service=self.error_paper_service,
            ),
        )
        self._add_lazy_panel(
            "flashcard",
            lambda: FlashcardPanel(
                self.flashcard_service,
                tts_service=self.tts_service,
            ),
        )
        self._add_lazy_panel(
            "practice",
            lambda: PracticePanel(
                self.practice_service,
                errorbook_service=self.errorbook_service,
                question_import_service=self.question_import_service,
            ),
        )
        self._add_lazy_panel(
            "exam",
            lambda: ExamPanel(
                self.exam_service,
                errorbook_service=self.errorbook_service,
            ),
        )
        self._add_lazy_panel(
            "knowledge_graph",
            lambda: KnowledgeGraphPanel(
                self.knowledge_graph_service,
            ),
        )
        self._add_lazy_panel(
            "diagnostic_report",
            lambda: DiagnosticReportPanel(
                self.diagnostic_report_service,
            ),
        )
        self._add_lazy_panel(
            "terminology",
            lambda: TerminologyPanel(
                self.terminology_service,
            ),
        )
        self._add_lazy_panel(
            "study_plan",
            lambda: StudyPlanPanel(
                self.study_plan_service,
                flashcard_service=self.flashcard_service,
            ),
        )
        self._add_lazy_panel(
            "learning_package",
            lambda: LearningPackagePanel(self.learning_package_service),
        )
        self._add_lazy_panel(
            "asr",
            lambda: ASRPanel(self.asr_service),
        )
        self._add_lazy_panel(
            "textbook_import",
            lambda: TextbookImportPanel(
                self.rag_service,
                user_service=self.user_service,
                toast_manager=self.toast_manager,
            ),
        )
        self._add_lazy_panel(
            "user",
            lambda: UserPanel(
                self.user_service,
                on_user_switched=self._on_user_switched,
            ),
        )
        self._add_lazy_panel(
            "settings",
            lambda: SettingsPanel(
                self.config,
                self.model_manager,
                self.theme_manager,
                backup_service=self.backup_service,
                teaching_style_service=self.teaching_style_service,
                user_service=self.user_service,
                tts_service=self.tts_service,
            ),
        )
        # 系统日志面板（独立页面，从设置页或导航进入）
        self._add_lazy_panel("logs", lambda: LogPanel())
        # Ollama 本地模型管理面板
        self._add_lazy_panel(
            "ollama",
            lambda: OllamaManagerPanel(ollama_service=self.ollama_service),
        )
        # 插件管理面板（内置/外部插件的启用、禁用与扫描）
        self._add_lazy_panel(
            "plugins",
            lambda: PluginPanel(plugin_manager=getattr(self, "plugin_manager", None)),
        )
        # 教材缓存管理面板（低配机器不加载，由导航隐藏控制，面板仍注册供内部调用）
        self._add_lazy_panel(
            "textbook_cache",
            lambda: TextbookCachePanel(cache_service=self.textbook_cache_service),
        )
        # 富文本笔记面板
        self._add_lazy_panel(
            "notes",
            lambda: NotesPanel(
                note_service=self.note_service,
                chat_service=getattr(self, "chat_service", None),
                session_manager=getattr(self, "session_manager", None),
            ),
        )

        # 可滚动菜单区域（QScrollArea + QListWidget 分组）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        scroll_content = QWidget()
        scroll.setWidget(scroll_content)
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(0)

        self._nav_buttons: Dict[str, _NavItemWidget] = {}
        self._nav_items: Dict[str, QListWidgetItem] = {}
        self._nav_group_labels: list[QLabel] = []
        self._nav_list_widgets: list[QListWidget] = []
        # 插件动态导航（“扩展插件”分组按需创建）
        self._nav_scroll_layout = scroll_layout
        self._plugin_nav_group_label: Optional[QLabel] = None
        self._plugin_nav_list: Optional[QListWidget] = None
        self._plugin_nav_items: list[dict] = []
        self._dynamic_headers: Dict[str, tuple] = {}
        self._plugin_factories: Dict[str, tuple] = {}
        nav_groups = [
            ("核心工作区", [
                ("◈", "总览", "dashboard"),
                ("☰", "对话", "chat"),
                ("◉", "OCR / 搜题", "ocr"),
                ("▦", "知识库", "knowledge"),
            ]),
            ("学习工具", [
                ("★", "收藏夹", "favorites"),
                ("✎", "错题本", "errorbook"),
                ("□", "闪卡", "flashcard"),
                ("📓", "笔记", "notes"),
                ("➤", "自适应练习", "practice"),
                ("❖", "试卷生成", "exam"),
                ("◐", "学习诊断", "diagnostic_report"),
                ("≋", "术语词典", "terminology"),
                ("✦", "学习计划", "study_plan"),
                ("▣", "学习包", "learning_package"),
                ("↓", "教材导入", "textbook_import"),
            ]),
            ("系统设置", [
                ("●", "用户管理", "user"),
                ("⚙", "模型管理", "settings"),
                ("⬢", "本地模型", "ollama"),
                ("☰", "系统日志", "logs"),
                ("⌘", "插件管理", "plugins"),
            ]),
        ]
        # 可选入口按硬件分级显隐；规则集中在 _optional_nav_keys，便于回归测试
        voice_enabled = False
        try:
            row = self.user_service.db.fetchone(
                "SELECT value FROM settings WHERE key = ?", ("voice_enabled",)
            )
            voice_enabled = bool(row and row["value"] in ("1", "true", "yes", "on"))
        except Exception:
            voice_enabled = False
        optional_nav = _optional_nav_keys(self._hardware_tier, voice_enabled)
        if "knowledge_graph" in optional_nav:
            nav_groups[1][1].insert(4, ("⬡", "知识图谱", "knowledge_graph"))
        if "asr" in optional_nav:
            nav_groups[1][1].append(("○", "语音转文字", "asr"))
        if "textbook_cache" in optional_nav:
            nav_groups[1][1].append(("⛁", "教材缓存", "textbook_cache"))

        for group_title, items in nav_groups:
            group_label = QLabel(group_title)
            group_label.setStyleSheet(
                "font-size: 11px; font-weight: bold; color: #86909C; padding: 14px 0 6px 4px;"
            )
            self._nav_group_labels.append(group_label)
            scroll_layout.addWidget(group_label)

            list_widget = QListWidget()
            list_widget.setFrameShape(QFrame.Shape.NoFrame)
            list_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
            list_widget.setSpacing(2)
            list_widget.setContentsMargins(0, 0, 0, 0)
            list_widget.setStyleSheet("""
                QListWidget {
                    background-color: transparent;
                    border: none;
                    outline: none;
                }
                QListWidget::item {
                    background-color: transparent;
                    border: none;
                    padding: 0px;
                    margin: 0px;
                }
                QListWidget::item:selected,
                QListWidget::item:focus,
                QListWidget::item:hover {
                    background-color: transparent;
                    border: none;
                    outline: none;
                }
                QListWidget::item:selected:!active {
                    background-color: transparent;
                    border: none;
                }
                QListWidget:focus {
                    outline: none;
                }
            """)
            self._nav_list_widgets.append(list_widget)

            for icon, text, key in items:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, key)
                item.setSizeHint(QSize(160, 36))
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                list_widget.addItem(item)
                nav_item = _NavItemWidget(icon, text, self.theme_manager)
                list_widget.setItemWidget(item, nav_item)
                self._nav_buttons[key] = nav_item
                self._nav_items[key] = item

            list_widget.setFixedHeight(max(36, list_widget.count() * 40 - 2))
            list_widget.itemClicked.connect(
                lambda item, lw=list_widget: self._switch_panel(item.data(Qt.ItemDataRole.UserRole))
            )
            scroll_layout.addWidget(list_widget)
            scroll_layout.addSpacing(6)

        scroll_layout.addStretch()
        nav_layout.addWidget(scroll, 1)

        # 折叠/展开切换按钮
        self.collapse_btn = QPushButton("◀  收起")
        self.collapse_btn.setObjectName("nav_button")
        self.collapse_btn.setToolTip("收起/展开侧边栏")
        self.collapse_btn.clicked.connect(self._toggle_nav_collapse)
        nav_layout.addWidget(self.collapse_btn)

        # 主题切换按钮（固定底部）
        self.theme_btn = QPushButton("◐  切换主题")
        self.theme_btn.setObjectName("nav_button")
        self.theme_btn.setToolTip("在暗色/亮色主题间切换")
        self.theme_btn.clicked.connect(self._toggle_theme)
        nav_layout.addWidget(self.theme_btn)

        self.splitter.addWidget(self.nav_widget)

        # 右侧内容区：顶部标题区 + 堆叠面板
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(0)

        # 顶部标题区 + 全局搜索
        header_container = QWidget()
        header_layout = QHBoxLayout(header_container)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)

        self.header_widget = create_header_widget("总览", "学习数据一览")
        header_layout.addWidget(self.header_widget, 1)

        from PyQt6.QtWidgets import QLineEdit
        self.global_search_edit = QLineEdit()
        self.global_search_edit.setPlaceholderText("全局搜索：错题、闪卡、文档、OCR内容")
        self.global_search_edit.setMinimumWidth(320)
        self.global_search_edit.setMaximumWidth(420)
        self.global_search_edit.setClearButtonEnabled(True)
        self.global_search_edit.returnPressed.connect(self._on_global_search)
        header_layout.addWidget(self.global_search_edit)

        search_btn = QPushButton("搜索")
        search_btn.setObjectName("primary_btn")
        search_btn.setMaximumWidth(80)
        search_btn.clicked.connect(self._on_global_search)
        header_layout.addWidget(search_btn)

        right_layout.addWidget(header_container)

        right_layout.addWidget(self.stack, 1)
        self.splitter.addWidget(right_container)
        self.splitter.setSizes([self._nav_expanded_width, 1100])

        # 默认选中总览页
        self._switch_panel("dashboard")

        # 加载内置/外部插件（单个插件失败不阻塞主程序；禁用即时撤销导航入口）
        try:
            if getattr(self, "plugin_manager", None) is not None:
                self.plugin_manager.load_all(self)
        except Exception as e:
            logger.error("Failed to load plugins: %s", e)

        # 启动流程（首次新手向导 / 账号选择窗）延后到主窗口真正显示之后再触发：
        # 启动遮罩是铺满主屏的独占遮罩，遮罩期间弹出的对话框会被它挡住而无法操作
        self._startup_flow_pending = True

    def _run_startup_flow(self) -> None:
        """启动流程：首次启动 → 新手向导；否则 → 账号选择窗。

        任何异常都不阻断主程序启动（回退到默认用户）。
        """
        try:
            # 判断是否首次启动
            onboarding_done = False
            try:
                row = self.user_service.db.fetchone(
                    "SELECT value FROM settings WHERE key = ?", ("onboarding_completed",)
                )
                onboarding_done = bool(row and row["value"] in ("1", "true", "yes"))
            except Exception:
                pass

            if not onboarding_done:
                wizard = OnboardingWizard(self)
                if wizard.exec():
                    cfg = wizard.get_config()
                    # 持久化强制低配开关
                    self.user_service.db.execute(
                        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                        ("force_low_spec", "true" if cfg["force_low_spec"] else "false"),
                    )
                    logger.info("Onboarding completed, run_mode=%s", cfg["run_mode"])
                self.user_service.db.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    ("onboarding_completed", "true"),
                )
                return

            # 非首次：若存在多个账号，弹选择窗
            users = self.user_service.list_users()
            if len(users) > 1:
                dialog = LoginDialog(self.user_service, self)
                dialog.user_selected.connect(self._on_login_user)
                dialog.exec()
        except Exception as e:
            logger.warning("Startup flow failed, fallback to default user: %s", e)

    def _on_login_user(self, user_id: int) -> None:
        """登录对话框选择账号后回调。"""
        try:
            self.user_service.set_current_user(user_id)
            # 刷新依赖当前用户的面板数据
            if hasattr(self, "_panels"):
                for panel in self._panels.values():
                    if hasattr(panel, "refresh_data"):
                        try:
                            panel.refresh_data()
                        except Exception:
                            pass
            self._switch_panel("dashboard")
        except Exception as e:
            logger.error("Failed to switch user on login: %s", e)

    def _add_panel(self, key: str, widget: QWidget) -> None:
        """注册一个业务面板（立即构建）。"""
        self._panels[key] = widget
        self.stack.addWidget(widget)

    def _add_lazy_panel(self, key: str, factory) -> None:
        """登记面板工厂：首次切换到该面板时才真正构建。

        非首屏页面（错题本、试卷、知识图谱等）不再在启动时实例化，
        可显著缩短启动时间并降低常驻内存；切换时会自动构建。
        """
        self._panel_factories[key] = factory

    def _ensure_panel(self, key: str) -> Optional[QWidget]:
        """确保面板已构建（懒加载面板在此真正实例化）；失败返回 None。"""
        if key in self._panels:
            return self._panels[key]
        factory = self._panel_factories.get(key)
        if factory is None:
            return None
        try:
            widget = factory()
        except Exception as e:
            logger.error("构建面板 %s 失败: %s", key, e)
            self._panel_factories.pop(key, None)
            return None
        # 已构建完成，释放工厂闭包引用，避免长期持有
        self._panel_factories.pop(key, None)
        self._add_panel(key, widget)
        return widget

    # ------------------------------------------------------------------
    # 插件面板的动态注册 / 注销
    # ------------------------------------------------------------------
    def switch_to_panel(self, key: str) -> bool:
        """供插件调用的面板跳转；面板存在并切换成功返回 True。

        插件面板（plugin:<id>:<key>）尚未惰性构建时也会即时构建并切换。
        """
        if key.startswith("plugin:"):
            if key in self._panels or key in self._plugin_factories:
                self._switch_panel(key)
                return key in self._panels
            return False
        if key in self._panels:
            self._switch_panel(key)
            return True
        return False

    def _plugin_nav_style(self) -> str:
        return """
            QListWidget {
                background-color: transparent;
                border: none;
                outline: none;
            }
            QListWidget::item {
                background-color: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
            }
            QListWidget::item:hover,
            QListWidget::item:selected,
            QListWidget::item:focus,
            QListWidget::item:selected:!active {
                background-color: transparent;
                border: none;
                outline: none;
            }
            QListWidget:focus { outline: none; }
        """

    def _ensure_plugin_nav_group(self) -> QListWidget:
        """惰性创建“扩展插件”导航分组（插入到滚动区末尾拉伸项之前）。"""
        if self._plugin_nav_list is not None:
            return self._plugin_nav_list

        label = QLabel("扩展插件")
        label.setStyleSheet(
            "font-size: 11px; font-weight: bold; color: #86909C; padding: 14px 0 6px 4px;"
        )
        self._plugin_nav_group_label = label

        list_widget = QListWidget()
        list_widget.setFrameShape(QFrame.Shape.NoFrame)
        list_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        list_widget.setSpacing(2)
        list_widget.setContentsMargins(0, 0, 0, 0)
        list_widget.setStyleSheet(self._plugin_nav_style())
        list_widget.itemClicked.connect(
            lambda item: self._switch_panel(item.data(Qt.ItemDataRole.UserRole))
        )
        self._plugin_nav_list = list_widget
        self._nav_list_widgets.append(list_widget)
        self._nav_group_labels.append(label)

        # 末尾是 addStretch()，插在它之前
        insert_at = max(0, self._nav_scroll_layout.count() - 1)
        self._nav_scroll_layout.insertWidget(insert_at, label)
        self._nav_scroll_layout.insertWidget(insert_at + 1, list_widget)
        self._nav_scroll_layout.insertSpacing(insert_at + 2, 6)
        return list_widget

    def _add_plugin_nav_item(self, plugin_id: str, full_key: str, title: str, icon: str) -> None:
        list_widget = self._ensure_plugin_nav_group()
        if self._plugin_nav_group_label is not None:
            self._plugin_nav_group_label.show()
        list_widget.show()

        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, full_key)
        item.setSizeHint(QSize(160, 36))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        list_widget.addItem(item)
        nav_item = _NavItemWidget(icon, title, self.theme_manager)
        list_widget.setItemWidget(item, nav_item)
        list_widget.setFixedHeight(max(36, list_widget.count() * 40 - 2))

        self._nav_buttons[full_key] = nav_item
        self._nav_items[full_key] = item
        self._plugin_nav_items.append(
            {
                "plugin": plugin_id,
                "key": full_key,
                "item": item,
                "widget": nav_item,
                "list_widget": list_widget,
            }
        )

    def register_plugin_panel(
        self,
        plugin_id: str,
        full_key: str,
        title: str,
        icon: str,
        subtitle: str,
        factory,
        ctx,
    ) -> str:
        """PluginManager 调用：登记工厂并加入导航（面板首次点击时才构建）。"""
        try:
            if full_key in self._panels or full_key in self._plugin_factories:
                self.unregister_plugin_panel(full_key)
            self._plugin_factories[full_key] = (factory, ctx)
            self._dynamic_headers[full_key] = (title, subtitle or title)
            self._add_plugin_nav_item(plugin_id, full_key, title, icon)
            return full_key
        except Exception as e:
            logger.error("register_plugin_panel(%s) failed: %s", full_key, e)
            raise

    def _ensure_plugin_panel(self, full_key: str) -> Optional[QWidget]:
        """惰性构建插件面板；构建失败则撤销导航入口并返回 None。"""
        if full_key in self._panels:
            return self._panels[full_key]
        holder = self._plugin_factories.get(full_key)
        if holder is None:
            return None
        factory, ctx = holder
        try:
            widget = factory(ctx)
        except Exception as e:
            logger.error("Build plugin panel %s failed: %s", full_key, e)
            self.unregister_plugin_panel(full_key)
            return None
        self._add_panel(full_key, widget)
        return widget

    def unregister_plugin_panel(self, full_key: str) -> None:
        """禁用插件时调用：立即移除导航入口、堆栈面板并释放对象。"""
        was_current = self._panels.get(full_key) is self.stack.currentWidget()
        entry = next((e for e in self._plugin_nav_items if e["key"] == full_key), None)
        if entry is not None:
            list_widget = entry["list_widget"]
            try:
                list_widget.takeItem(list_widget.row(entry["item"]))
            except Exception:
                pass
            try:
                entry["widget"].deleteLater()
            except Exception:
                pass
            self._plugin_nav_items.remove(entry)
            self._nav_buttons.pop(full_key, None)
            self._nav_items.pop(full_key, None)
            list_widget.setFixedHeight(max(36, list_widget.count() * 40 - 2))
            if list_widget.count() == 0 and self._plugin_nav_group_label is not None:
                self._plugin_nav_group_label.hide()
                list_widget.hide()

        widget = self._panels.pop(full_key, None)
        if widget is not None:
            try:
                self.stack.removeWidget(widget)
                widget.deleteLater()
            except Exception:
                pass
        self._dynamic_headers.pop(full_key, None)
        self._plugin_factories.pop(full_key, None)
        # 若当前正在看被禁用的面板，立即回到总览
        if was_current:
            self._switch_panel("dashboard")

    def _refresh_nav_styles(self, theme: str = "") -> None:
        """主题切换后刷新侧边栏菜单项样式。"""
        try:
            for btn in self._nav_buttons.values():
                btn._update_style()
                btn.update()
        except Exception as e:
            logger.warning("Failed to refresh nav styles: %s", e)

    def _toggle_nav_collapse(self) -> None:
        """切换侧边栏折叠/展开状态。"""
        try:
            self._nav_collapsed = not self._nav_collapsed
            width = (
                self._nav_collapsed_width
                if self._nav_collapsed
                else self._nav_expanded_width
            )

            self.nav_widget.setFixedWidth(width)
            self.splitter.setSizes([width, self.splitter.width() - width])

            # 隐藏/显示品牌标语与分组标题
            self.brand_label.setVisible(not self._nav_collapsed)
            self.motto_label.setVisible(not self._nav_collapsed)
            for label in self._nav_group_labels:
                label.setVisible(not self._nav_collapsed)

            # 折叠时压缩侧边栏内边距
            nav_layout = self.nav_widget.layout()
            if nav_layout is not None:
                if self._nav_collapsed:
                    nav_layout.setContentsMargins(6, 18, 6, 18)
                else:
                    nav_layout.setContentsMargins(14, 18, 14, 18)

            # 调整列表项尺寸与居中
            for lw in self._nav_list_widgets:
                lw.setFixedHeight(
                    max(36, lw.count() * 40 - 2)
                    if not self._nav_collapsed
                    else max(36, lw.count() * 40 - 2)
                )

            # 通知菜单项进入/退出折叠模式
            for btn in self._nav_buttons.values():
                btn.setCollapsed(self._nav_collapsed)

            # 更新折叠按钮文字
            if self._nav_collapsed:
                self.collapse_btn.setText("▶")
                self.theme_btn.setText("◐")
            else:
                self.collapse_btn.setText("◀  收起")
                self.theme_btn.setText("◐  切换主题")
        except Exception as e:
            logger.error("Failed to toggle nav collapse: %s", e)

    def _switch_panel(self, key: str) -> None:
        """切换左侧选中的面板并更新顶部标题。"""
        try:
            if key not in self._panels:
                # 插件面板与懒加载面板：首次切换时才构建
                if key.startswith("plugin:"):
                    if self._ensure_plugin_panel(key) is None:
                        return
                elif key in self._panel_factories:
                    if self._ensure_panel(key) is None:
                        return
                else:
                    return
            self.stack.setCurrentWidget(self._panels[key])
            self._fade_panel(self._panels[key])
            for k, btn in self._nav_buttons.items():
                btn.setChecked(k == key)
            for k, item in self._nav_items.items():
                item.setSelected(k == key)
                if k == key:
                    lw = item.listWidget()
                    if lw is not None:
                        lw.setCurrentItem(item)

            title, subtitle = self._dynamic_headers.get(key) or _PANEL_HEADERS.get(key, (key, ""))
            self._update_header(title, subtitle)
            logger.debug("Switched to panel: %s", key)
        except Exception as e:
            logger.error("Failed to switch panel %s: %s", key, e)

    def _fade_panel(self, panel: Optional[QWidget]) -> None:
        """页面切换的淡入过渡（轻量、可降级）。

        含 WebEngine / OpenGL 原生渲染子控件的页面会自动跳过，
        避免图形特效破坏其渲染；低配设备与闲置时同样跳过。
        """
        if panel is None:
            return
        skip = getattr(self, "_no_fade_panels", None)
        if skip is None:
            skip = self._no_fade_panels = set()
        if panel in skip:
            return
        try:
            from .interactions import policy

            duration = policy().duration(140)
        except Exception:
            duration = 0
        if duration <= 0:
            return
        if not self._panel_fade_safe(panel):
            skip.add(panel)
            return
        try:
            from PyQt6.QtWidgets import QGraphicsOpacityEffect

            effect = QGraphicsOpacityEffect(panel)
            effect.setOpacity(0.0)
            panel.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b"opacity", panel)
            anim.setDuration(duration)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.finished.connect(lambda p=panel: p.setGraphicsEffect(None))
            anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
            # 兜底：动画被打断也必须恢复，绝不让页面停在半透明状态
            QTimer.singleShot(
                duration + 120, lambda p=panel: p.setGraphicsEffect(None)
            )
        except Exception:
            try:
                panel.setGraphicsEffect(None)
            except Exception:
                pass

    @staticmethod
    def _panel_fade_safe(panel: QWidget) -> bool:
        """判断页面是否适合叠加透明度特效（原生渲染控件会受影响）。"""
        try:
            for child in panel.findChildren(QWidget):
                name = type(child).__name__
                if "WebEngine" in name or "OpenGL" in name:
                    return False
        except Exception:
            return False
        return True

    def _update_header(self, title: str, subtitle: str) -> None:
        """更新顶部标题区文本。"""
        try:
            layout = self.header_widget.layout()
            if layout is not None:
                for i in range(layout.count()):
                    widget = layout.itemAt(i).widget()
                    if isinstance(widget, QLabel):
                        if widget.objectName() == "header_title":
                            widget.setText(title)
                        elif widget.objectName() == "subtitle_label":
                            widget.setText(subtitle)
        except Exception as e:
            logger.warning("Failed to update header: %s", e)

    def _build_menu(self) -> None:
        """构建顶部菜单栏。"""
        try:
            menubar = self.menuBar()
            file_menu = menubar.addMenu("文件")

            exit_action = QAction("退出", self)
            exit_action.setShortcut(QKeySequence("Ctrl+Q"))
            exit_action.triggered.connect(self.close)
            file_menu.addAction(exit_action)

            view_menu = menubar.addMenu("视图")
            for label, key in [
                ("总览", "dashboard"),
                ("对话", "chat"),
                ("OCR / 搜题", "ocr"),
                ("知识库", "knowledge"),
                ("收藏夹", "favorites"),
                ("错题本", "errorbook"),
                ("闪卡", "flashcard"),
                ("自适应练习", "practice"),
                ("试卷生成", "exam"),
                ("知识图谱", "knowledge_graph"),
                ("学习诊断", "diagnostic_report"),
                ("术语词典", "terminology"),
                ("学习计划", "study_plan"),
                ("学习包", "learning_package"),
                ("语音转文字", "asr"),
                ("教材导入", "textbook_import"),
                ("模型管理", "settings"),
            ]:
                action = QAction(label, self)
                action.triggered.connect(lambda checked, k=key: self._switch_panel(k))
                view_menu.addAction(action)

            theme_action = QAction("切换主题", self)
            theme_action.triggered.connect(self._toggle_theme)
            view_menu.addSeparator()
            view_menu.addAction(theme_action)
        except Exception as e:
            logger.error("Failed to build menu: %s", e)

    def _toggle_theme(self) -> None:
        """切换暗色/亮色主题。"""
        try:
            self.theme_manager.toggle(QApplication.instance())
            logger.info("Theme toggled to %s", self.theme_manager.theme)
        except Exception as e:
            logger.error("Failed to toggle theme: %s", e)

    def _build_tray(self) -> None:
        """构建系统托盘。"""
        try:
            self.tray_icon = TrayIcon(self)
            self.tray_icon.show()
        except Exception as e:
            logger.warning("Failed to build tray icon: %s", e)

    def _build_hotkeys(self) -> None:
        """注册全局快捷键。"""
        try:
            self.hotkey_listener = HotkeyListener(self, config=self.config)
            self.hotkey_listener.screenshot_requested.connect(self._on_screenshot)
            self.hotkey_listener.clipboard_requested.connect(self._on_clipboard)
            self.hotkey_listener.show_window_requested.connect(self._show_window)
            self.hotkey_listener.start()
        except Exception as e:
            logger.warning("Failed to start hotkey listener: %s", e)

    def _check_backend_status(self) -> None:
        """启动后检查模型后端状态，友好提示。"""
        try:
            if not self.model_manager.is_text_available():
                QMessageBox.warning(
                    self,
                    "模型服务未启动",
                    "Ollama 服务未检测到。\n"
                    "请先启动 Ollama，或在「模型管理」中切换后端。\n"
                    "程序可继续使用除 AI 对话外的其他功能。",
                )
        except Exception as e:
            logger.warning("Failed to check backend status: %s", e)

    def _on_screenshot(self) -> None:
        """快捷键触发截图。"""
        logger.info("Global screenshot hotkey triggered")
        self._switch_panel("ocr")
        try:
            self._panels["ocr"].start_screenshot()
        except Exception as e:
            logger.error("Failed to start screenshot from hotkey: %s", e)

    def _on_clipboard(self) -> None:
        """快捷键触发剪贴板识别。"""
        logger.info("Global clipboard hotkey triggered")
        self._switch_panel("ocr")
        try:
            self._panels["ocr"].process_clipboard()
        except Exception as e:
            logger.error("Failed to process clipboard from hotkey: %s", e)

    def _show_window(self) -> None:
        """显示主窗口。"""
        try:
            self.showNormal()
            self.raise_()
            self.activateWindow()
        except Exception as e:
            logger.error("Failed to show window: %s", e)

    def _on_global_search(self) -> None:
        """执行全局搜索并显示结果弹窗。"""
        keyword = self.global_search_edit.text().strip()
        if not keyword:
            return
        try:
            results = self.search_service.search(keyword, limit=20)
            if not results:
                QMessageBox.information(self, "全局搜索", f"未找到与「{keyword}」相关的内容。")
                return
            self._show_search_results(keyword, results)
        except Exception as e:
            logger.error("Global search failed: %s", e)
            QMessageBox.warning(self, "搜索失败", f"搜索出错：{e}")

    def _show_search_results(self, keyword: str, results: list) -> None:
        """弹出搜索结果列表，点击后跳转定位。"""
        from PyQt6.QtWidgets import (
            QDialog,
            QDialogButtonBox,
            QListWidget,
            QListWidgetItem,
            QVBoxLayout,
        )

        dialog = QDialog(self)
        dialog.setWindowTitle(f"全局搜索结果：{keyword}")
        dialog.resize(600, 400)
        layout = QVBoxLayout(dialog)
        list_widget = QListWidget()
        type_names = {
            "error_book": "错题",
            "flashcard": "闪卡",
            "document": "文档",
            "conversation": "对话",
            "ocr_record": "OCR 记录",
        }
        for r in results:
            item = QListWidgetItem(
                f"[{type_names.get(r['type'], r['type'])}] {r['title']}\n{r['snippet'][:120]}"
            )
            item.setData(Qt.ItemDataRole.UserRole, r)
            list_widget.addItem(item)
        list_widget.itemDoubleClicked.connect(
            lambda item: self._navigate_to_search_result(item.data(Qt.ItemDataRole.UserRole))
        )
        layout.addWidget(list_widget)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(dialog.reject)
        layout.addWidget(btns)
        dialog.exec()

    def _navigate_to_search_result(self, result: dict) -> None:
        """根据搜索结果跳转到对应页面。"""
        mapping = {
            "error_book": "errorbook",
            "flashcard": "flashcard",
            "document": "knowledge",
            "conversation": "chat",
            "ocr_record": "ocr",
        }
        panel_key = mapping.get(result.get("type"))
        if not panel_key:
            return
        self._switch_panel(panel_key)
        panel = self._panels.get(panel_key)
        if panel and hasattr(panel, "locate_item"):
            try:
                panel.locate_item(result.get("type"), result.get("id"))
            except Exception as e:
                logger.warning("Failed to locate search result: %s", e)

    def _on_user_switched(self) -> None:
        """用户切换后刷新各面板数据。"""
        for panel in self._panels.values():
            if hasattr(panel, "refresh_data"):
                try:
                    panel.refresh_data()
                except Exception as e:
                    logger.warning("Failed to refresh panel after user switch: %s", e)

    def _on_favorite_navigate(self, panel_key: str, item_id: int) -> None:
        """从收藏夹跳转定位到具体错题或文档。"""
        self._switch_panel(panel_key)
        panel = self._panels.get(panel_key)
        type_map = {
            "errorbook": "error_book",
            "knowledge": "document",
        }
        if panel and hasattr(panel, "locate_item"):
            try:
                panel.locate_item(type_map.get(panel_key, panel_key), item_id)
            except Exception as e:
                logger.warning("Failed to locate favorite item: %s", e)

    def resizeEvent(self, event) -> None:
        """窗口大小变化时重新定位 Toast。"""
        super().resizeEvent(event)
        if hasattr(self, "toast_manager"):
            self.toast_manager.reposition()

    def dragEnterEvent(self, event) -> None:
        """接受图片和 PDF 文件拖拽。"""
        mime = event.mimeData()
        if mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    path = Path(url.toLocalFile())
                    if self._is_droppable_file(path):
                        event.acceptProposedAction()
                        return
        event.ignore()

    def dropEvent(self, event) -> None:
        """处理拖拽文件：图片 -> OCR，PDF -> 知识库。"""
        mime = event.mimeData()
        if not mime.hasUrls():
            event.ignore()
            return

        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if not self._is_droppable_file(path):
                continue

            suffix = path.suffix.lower()
            if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}:
                self._switch_panel("ocr")
                ocr_panel = self._panels.get("ocr")
                if ocr_panel is not None:
                    ocr_panel.import_image(path)
                    self.show_toast(f"已加载图片：{path.name}", level="info")
            elif suffix == ".pdf":
                self._switch_panel("knowledge")
                knowledge_panel = self._panels.get("knowledge")
                if knowledge_panel is not None:
                    knowledge_panel.import_document(str(path))
                    self.show_toast(f"正在导入 PDF：{path.name}", level="info")

        event.acceptProposedAction()

    @staticmethod
    def _is_droppable_file(path: Path) -> bool:
        """判断是否支持拖拽的文件类型。"""
        suffix = path.suffix.lower()
        return suffix in {
            ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp", ".pdf"
        }

    def show_toast(
        self,
        message: str,
        level: str = "info",
        duration_ms: int = 3000,
    ) -> None:
        """显示一条轻量 Toast 消息。"""
        if hasattr(self, "toast_manager"):
            self.toast_manager.show(message, level=level, duration_ms=duration_ms)

    def closeEvent(self, event) -> None:
        """关闭时最小化到托盘或直接退出，并保存窗口状态。"""
        self._save_window_geometry()

        try:
            minimize_to_tray = self.config.get("ui", {}).get("minimize_to_tray", True)
            if minimize_to_tray and hasattr(self, "tray_icon") and self.tray_icon.isVisible():
                self.hide()
                event.ignore()
                return
        except Exception as e:
            logger.warning("Failed to handle close event tray logic: %s", e)

        try:
            if hasattr(self, "hotkey_listener"):
                self.hotkey_listener.stop()
        except Exception as e:
            logger.warning("Failed to stop hotkey listener: %s", e)
        event.accept()

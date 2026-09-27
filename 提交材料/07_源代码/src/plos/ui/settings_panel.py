"""模型管理与设置面板。

支持切换本地/云端模式、选择模型、查看硬件信息、低配模式开关、保存配置、测试连接。
新增数据备份与恢复卡片，支持全量学习数据导出/导入，以及错题、闪卡的 Markdown 导出。
采用分组卡片布局，推荐模型高亮标签，保存按钮右下角突出。
"""

from __future__ import annotations

import atexit
import copy
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6.QtCore import QObject, QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..ai import ModelManager
from ..config import MODEL_PROFILES, save_config
from .interactions import friendly_error_message
from ..core.constants import DEFAULT_OLLAMA_HOST
from ..core.constants import DEFAULT_DAILY_GOAL_MINUTES
from ..core.enums import InferenceBackend
from ..services import BackupService, DemoService, TeachingStyleService, UserService
from ..utils.logger import get_logger
from .theme_manager import ThemeManager
from .ui_utils import (
    theme_colors,
    apply_card_style,
    ask_confirm,
    busy_cursor,
    create_badge_label,
    fix_spinbox_text,
    set_primary_button_style,
    show_error,
    show_info,
    show_success,
    show_warning,
    repolish_tree,
)

logger = get_logger("ui.settings_panel")


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并 override 到 base（就地修改），标量与列表以 override 为准。"""
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


#: atexit 只需注册一次（用列表承载可变状态，避免 global 声明）
_qt_log_atexit_registered = [False]


def _detach_qt_log_handlers() -> None:
    """解释器退出前统一摘除 Qt 日志 handler。

    只从 root logger 摘除并不够：``logging.shutdown`` 遍历的是模块级
    ``_handlerList``（登记所有创建过的 handler），届时 Qt 对象可能已随父控件销毁，
    在 flush/close 时抛 ``RuntimeError: wrapped C/C++ object ... has been deleted``。
    因此还需把它从该登记表中移除。atexit 回调后注册先执行，一定早于 logging.shutdown。
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, _QtLogHandler):
            try:
                root.removeHandler(handler)
                handler.close()
            except Exception:
                pass

    registry = getattr(logging, "_handlerList", None)
    if not registry:
        return
    for ref in list(registry):
        try:
            target = ref() if callable(ref) else None
        except Exception:
            target = None
        if isinstance(target, _QtLogHandler):
            try:
                registry.remove(ref)
            except ValueError:
                pass


class _QtLogHandler(QObject, logging.Handler):
    """将日志记录转发到 Qt 信号，供界面日志面板安全显示。"""

    log_record = pyqtSignal(str)

    def __init__(self, level: int = logging.INFO, parent: Optional[QWidget] = None):
        QObject.__init__(self, parent)
        logging.Handler.__init__(self, level)
        self.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        if not _qt_log_atexit_registered[0]:
            atexit.register(_detach_qt_log_handlers)
            _qt_log_atexit_registered[0] = True

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.log_record.emit(self.format(record))
        except Exception:
            pass

    def flush(self) -> None:
        # Qt 对象可能在解释器退出前已被销毁，避免 shutdown 时抛 RuntimeError
        try:
            super().flush()
        except Exception:
            pass

    def close(self) -> None:
        """关闭 handler（幂等，Qt 对象已销毁时静默忽略）。"""
        try:
            super().close()
        except Exception:
            pass


class FlowLayout(QLayout):
    """流式布局：子控件水平排列，空间不足时自动换行。

    用于顶部硬件信息卡片组，保证宽屏三卡片并排、窄屏垂直堆叠。
    """

    def __init__(self, parent: Optional[QWidget] = None, spacing: int = 14):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def __del__(self):
        item = self.takeAt(0)
        while item is not None:
            item = self.takeAt(0)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> Optional[QLayoutItem]:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> Optional[QLayoutItem]:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margin = self.contentsMargins()
        size += QSize(margin.left() + margin.right(), margin.top() + margin.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        x = rect.x()
        y = rect.y()
        line_height = 0
        spacing = self.spacing()

        for item in self._items:
            widget = item.widget()
            space_x = spacing
            space_y = spacing
            if widget is not None:
                space_x += widget.style().layoutSpacing(
                    QSizePolicy.ControlType.PushButton,
                    QSizePolicy.ControlType.PushButton,
                    Qt.Orientation.Horizontal,
                )
                space_y += widget.style().layoutSpacing(
                    QSizePolicy.ControlType.PushButton,
                    QSizePolicy.ControlType.PushButton,
                    Qt.Orientation.Vertical,
                )

            next_x = x + item.sizeHint().width() + space_x
            if next_x - space_x > rect.right() and line_height > 0:
                x = rect.x()
                y += line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x = next_x
            line_height = max(line_height, item.sizeHint().height())

        return y + line_height - rect.y()


class SettingsPanel(QWidget):
    """模型管理与配置界面。"""

    def __init__(
        self,
        config: Dict[str, Any],
        model_manager: ModelManager,
        theme_manager: ThemeManager,
        backup_service: Optional[BackupService] = None,
        teaching_style_service: Optional[TeachingStyleService] = None,
        user_service: Optional[UserService] = None,
        tts_service=None,
    ):
        super().__init__()
        self.config = config
        self.model_manager = model_manager
        self.theme_manager = theme_manager
        self.backup_service = backup_service
        self.teaching_style_service = teaching_style_service
        self.user_service = user_service
        self.tts_service = tts_service
        self._build_ui()
        self._refresh_models()
        self._load_config()
        self._update_status_labels()
        self._check_model_health()

    def _create_setting_card(self, title: str) -> QGroupBox:
        """创建带标题与未保存指示器的设置卡片。"""
        group = QGroupBox(title)
        group.setProperty("card", True)
        apply_card_style(group)

        # 在标题区域右侧预留未保存指示器（通过内部布局实现）
        indicator = QLabel("● 已修改")
        self._bind_style(
            indicator,
            lambda: f"color: {theme_colors()['warning']}; font-size: 11px; font-weight: 600; background-color: transparent;",
        )
        indicator.hide()
        self._dirty_indicators[id(group)] = indicator

        # 把指示器放到 group 的顶部布局中，稍后由调用方添加到 group 的第一行
        group._dirty_indicator = indicator  # type: ignore[attr-defined]
        return group

    def _set_dirty(self, group: QGroupBox, dirty: bool) -> None:
        """设置指定卡片的未保存提示显示状态。"""
        indicator = self._dirty_indicators.get(id(group))
        if indicator is not None:
            indicator.setVisible(dirty)

    def _mark_dirty(self, widget: QWidget, group: QGroupBox) -> None:
        """对比原始值，标记卡片是否已修改。"""
        current = self._get_widget_value(widget)
        original = self._original_values.get(id(widget))
        self._set_dirty(group, current != original)

    def _get_widget_value(self, widget: QWidget) -> Any:
        """统一读取常见输入控件的当前值。"""
        if isinstance(widget, QLineEdit):
            return widget.text()
        if isinstance(widget, QComboBox):
            return widget.currentText()
        if isinstance(widget, QSpinBox):
            return widget.value()
        if isinstance(widget, QDoubleSpinBox):
            return round(widget.value(), widget.decimals())
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QRadioButton):
            return widget.isChecked()
        return None

    def _snapshot_original_values(self) -> None:
        """为所有被追踪控件保存当前值作为原始值，并重置未保存提示。"""
        self._original_values.clear()
        for widget in self._tracked_widgets():
            self._original_values[id(widget)] = self._get_widget_value(widget)
        self._clear_dirty_indicators()

    def _clear_dirty_indicators(self) -> None:
        """隐藏所有卡片的未保存提示。"""
        for indicator in self._dirty_indicators.values():
            indicator.hide()

    def _tracked_widgets(self) -> list[QWidget]:
        """返回所有参与修改追踪的输入控件。"""
        return [
            self.radio_local,
            self.radio_cloud,
            self.ollama_host_edit,
            self.profile_combo,
            self.cloud_url_edit,
            self.cloud_key_edit,
            self.text_model_combo,
            self.vision_model_combo,
            self.embedding_model_combo,
            self.temp_spin,
            self.max_tokens_spin,
            self.ctx_spin,
            self.low_spec_check,
            self.use_gpu_check,
            self.teaching_mode_combo,
            self.no_direct_answer_check,
            self.explanation_mode_combo,
            self.theme_combo,
            self.demo_check,
            self.asr_check,
            self.diagram_check,
            self.package_check,
            self.style_check,
            self.style_combo,
            self.depth_combo,
            self.example_combo,
            self.tone_combo,
            self.question_combo,
            self.local_lang_check,
            self.avoid_answer_check,
            self.focus_mistakes_check,
        ]

    def _build_ui(self) -> None:
        """构建模型管理页面，整体嵌入可滚动区域。"""
        c = theme_colors()

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        main_layout.addWidget(self.scroll_area)

        self.content_widget = QWidget()
        self.scroll_area.setWidget(self.content_widget)

        # 统一输入控件样式：高度 36px、圆角 6px、字号 14px，修复乱码压扁
        self.content_widget.setStyleSheet(
            f"""
            QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit {{
                min-height: 36px;
                max-height: 36px;
                min-width: 200px;
                padding: 0 12px;
                border-radius: 6px;
                font-size: 14px;
            }}
            QSpinBox::up-button, QSpinBox::down-button,
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
                width: 20px;
                subcontrol-origin: border;
            }}
            QComboBox::drop-down {{
                width: 24px;
                subcontrol-origin: border;
            }}
            QPlainTextEdit, QTextEdit {{
                border-radius: 6px;
                padding: 8px;
                font-size: 14px;
            }}
            QPushButton {{
                min-height: 36px;
                border-radius: 6px;
                padding: 0 14px;
            }}
            QGroupBox {{
                background-color: {c['card_bg']};
                border-radius: 8px;
                border: 1px solid {c['border']};
                margin-top: 14px;
                padding-top: 14px;
                font-weight: bold;
                font-size: 14px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 6px;
                color: {c['fg_primary']};
            }}
            """
        )

        layout = QVBoxLayout(self.content_widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        # 修改追踪
        self._original_values: Dict[int, Any] = {}
        self._dirty_indicators: Dict[int, QLabel] = {}

        # 模型健康自检警告条
        self.warning_banner = self._build_warning_banner()
        layout.addWidget(self.warning_banner)

        # 硬件信息卡片组（流式布局）
        hw_cards_layout = FlowLayout(spacing=14)
        self._hardware_cards = self._build_hardware_cards()
        for card in self._hardware_cards:
            hw_cards_layout.addWidget(card)
        # FlowLayout 不能直接 setLayout，需要包装成一个 QWidget
        hw_flow_widget = QWidget()
        hw_flow_widget.setLayout(hw_cards_layout)
        layout.addWidget(hw_flow_widget)

        # 运行模式
        mode_group = self._create_setting_card("运行模式")
        mode_layout = QVBoxLayout(mode_group)
        header = QHBoxLayout()
        header.addStretch()
        header.addWidget(mode_group._dirty_indicator)  # type: ignore[attr-defined]
        mode_layout.addLayout(header)

        mode_hint = QLabel("切换模式会清空当前对话会话，且两套接口数据相互隔离。")
        mode_hint.setWordWrap(True)
        mode_hint.setObjectName("subtitle_label")
        mode_layout.addWidget(mode_hint)

        mode_btn_layout = QHBoxLayout()
        self.radio_local = QRadioButton("本地 Ollama 离线模式")
        self.radio_cloud = QRadioButton("云端 API 模式")
        self.radio_local.toggled.connect(self._on_mode_changed)
        self.radio_local.toggled.connect(lambda: self._mark_dirty(self.radio_local, mode_group))
        self.radio_cloud.toggled.connect(lambda: self._mark_dirty(self.radio_cloud, mode_group))
        mode_btn_layout.addWidget(self.radio_local)
        mode_btn_layout.addWidget(self.radio_cloud)
        mode_btn_layout.addStretch()
        mode_layout.addLayout(mode_btn_layout)

        # 本地设置
        self.local_settings = QWidget()
        local_layout = QFormLayout(self.local_settings)
        self._configure_form_layout(local_layout)

        self.ollama_host_edit = QLineEdit()
        self.ollama_host_edit.textChanged.connect(
            lambda: self._mark_dirty(self.ollama_host_edit, mode_group)
        )
        local_layout.addRow("Ollama 地址：", self.ollama_host_edit)

        profile_layout = QHBoxLayout()
        self.profile_combo = QComboBox()
        self.profile_combo.addItems(["auto", "7b", "3b"])
        self.profile_combo.setToolTip(
            "auto=按硬件自动选择；7b=高性能全套；3b=低配全套"
        )
        self.profile_combo.currentTextChanged.connect(self._on_profile_changed)
        self.profile_combo.currentTextChanged.connect(
            lambda: self._mark_dirty(self.profile_combo, mode_group)
        )
        profile_layout.addWidget(self.profile_combo)
        profile_layout.addWidget(create_badge_label("推荐", c["success"]))
        profile_layout.addStretch()
        local_layout.addRow("模型套装：", profile_layout)

        mode_layout.addWidget(self.local_settings)

        # 云端设置
        self.cloud_settings = QWidget()
        cloud_layout = QFormLayout(self.cloud_settings)
        self._configure_form_layout(cloud_layout)

        self.cloud_url_edit = QLineEdit()
        self.cloud_url_edit.setPlaceholderText("https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.cloud_url_edit.textChanged.connect(
            lambda: self._mark_dirty(self.cloud_url_edit, mode_group)
        )
        cloud_layout.addRow("Base URL：", self.cloud_url_edit)

        self.cloud_key_edit = QLineEdit()
        self.cloud_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.cloud_key_edit.setPlaceholderText("请输入 API Key")
        self.cloud_key_edit.textChanged.connect(
            lambda: self._mark_dirty(self.cloud_key_edit, mode_group)
        )
        cloud_layout.addRow("API Key：", self.cloud_key_edit)

        self.show_key_btn = QPushButton("显示/隐藏")
        self.show_key_btn.setCheckable(True)
        self.show_key_btn.toggled.connect(self._toggle_key_visibility)
        cloud_layout.addRow("", self.show_key_btn)

        mode_layout.addWidget(self.cloud_settings)

        # 模型选择
        model_group = self._create_setting_card("模型选择")
        model_layout = QFormLayout(model_group)
        self._configure_form_layout(model_layout)
        model_header = QHBoxLayout()
        model_header.addStretch()
        model_header.addWidget(model_group._dirty_indicator)  # type: ignore[attr-defined]
        model_layout.addRow(model_header)

        self.text_model_combo = QComboBox()
        self.text_model_combo.setEditable(True)
        self.text_model_combo.currentTextChanged.connect(
            lambda: self._mark_dirty(self.text_model_combo, model_group)
        )
        model_layout.addRow("文本模型：", self.text_model_combo)

        self.vision_model_combo = QComboBox()
        self.vision_model_combo.setEditable(True)
        self.vision_model_combo.currentTextChanged.connect(
            lambda: self._mark_dirty(self.vision_model_combo, model_group)
        )
        model_layout.addRow("视觉模型：", self.vision_model_combo)

        self.embedding_model_combo = QComboBox()
        self.embedding_model_combo.setEditable(True)
        self.embedding_model_combo.currentTextChanged.connect(
            lambda: self._mark_dirty(self.embedding_model_combo, model_group)
        )
        model_layout.addRow("嵌入模型：", self.embedding_model_combo)

        vl_hint = QLabel("视觉模型仅用于图片 OCR/识图，是可选组件；缺少时不影响文本对话和 RAG。")
        vl_hint.setWordWrap(True)
        vl_hint.setObjectName("subtitle_label")
        model_layout.addRow(vl_hint)

        mode_layout.addWidget(model_group)

        # 测试连接
        btn_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("刷新模型列表")
        self.refresh_btn.clicked.connect(self._refresh_models)
        btn_layout.addWidget(self.refresh_btn)

        self.test_btn = QPushButton("测试连接")
        self.test_btn.setObjectName("primary_btn")
        self.test_btn.clicked.connect(self._test_connection)
        btn_layout.addWidget(self.test_btn)
        btn_layout.addStretch()
        mode_layout.addLayout(btn_layout)

        layout.addWidget(mode_group)

        # 参数设置
        param_group = self._create_setting_card("模型参数")
        param_layout = QFormLayout(param_group)
        self._configure_form_layout(param_layout)
        param_header = QHBoxLayout()
        param_header.addStretch()
        param_header.addWidget(param_group._dirty_indicator)  # type: ignore[attr-defined]
        param_layout.addRow(param_header)

        self.temp_spin = QDoubleSpinBox()
        fix_spinbox_text(self.temp_spin)
        self.temp_spin.setRange(0.0, 1.0)
        self.temp_spin.setSingleStep(0.05)
        self.temp_spin.setDecimals(2)
        self.temp_spin.setValue(0.7)
        self.temp_spin.setToolTip(
            "控制模型随机性：数值越高创造性越强；做理科刷题建议设置 0.2-0.4。"
        )
        self.temp_spin.valueChanged.connect(
            lambda: self._mark_dirty(self.temp_spin, param_group)
        )
        param_layout.addRow("温度 (Temperature)：", self.temp_spin)

        self.max_tokens_spin = QSpinBox()
        fix_spinbox_text(self.max_tokens_spin)
        self.max_tokens_spin.setRange(256, 8192)
        self.max_tokens_spin.setSingleStep(256)
        self.max_tokens_spin.setValue(2048)
        self.max_tokens_spin.setToolTip("限制 AI 单次输出最大 token 数量。")
        self.max_tokens_spin.valueChanged.connect(
            lambda: self._mark_dirty(self.max_tokens_spin, param_group)
        )
        param_layout.addRow("最大 Token：", self.max_tokens_spin)

        self.ctx_spin = QSpinBox()
        fix_spinbox_text(self.ctx_spin)
        self.ctx_spin.setRange(512, 32768)
        self.ctx_spin.setSingleStep(512)
        self.ctx_spin.setValue(8192)
        self.ctx_spin.setToolTip("模型能够记忆的对话上下文窗口大小。")
        self.ctx_spin.valueChanged.connect(
            lambda: self._mark_dirty(self.ctx_spin, param_group)
        )
        param_layout.addRow("上下文长度：", self.ctx_spin)

        layout.addWidget(param_group)

        # 性能设置
        perf_group = self._create_setting_card("性能设置")
        perf_layout = QFormLayout(perf_group)
        self._configure_form_layout(perf_layout)
        perf_header = QHBoxLayout()
        perf_header.addStretch()
        perf_header.addWidget(perf_group._dirty_indicator)  # type: ignore[attr-defined]
        perf_layout.addRow(perf_header)

        self.low_spec_check = QCheckBox("开启低配模式")
        self.low_spec_check.toggled.connect(lambda: self._mark_dirty(self.low_spec_check, perf_group))
        perf_layout.addRow(self.low_spec_check)

        self.use_gpu_check = QCheckBox("使用 GPU 加速")
        self.use_gpu_check.toggled.connect(lambda: self._mark_dirty(self.use_gpu_check, perf_group))
        perf_layout.addRow(self.use_gpu_check)

        layout.addWidget(perf_group)

        # 教学设置
        teaching_group = self._create_setting_card("教学设置")
        teaching_layout = QFormLayout(teaching_group)
        self._configure_form_layout(teaching_layout)
        teaching_header = QHBoxLayout()
        teaching_header.addStretch()
        teaching_header.addWidget(teaching_group._dirty_indicator)  # type: ignore[attr-defined]
        teaching_layout.addRow(teaching_header)

        self.teaching_mode_combo = QComboBox()
        self.teaching_mode_combo.addItem("普通模式", "normal")
        self.teaching_mode_combo.addItem("启发教学模式", "socratic")
        self.teaching_mode_combo.setToolTip("启发教学模式会引导用户独立思考，不直接给出完整答案")
        self.teaching_mode_combo.currentTextChanged.connect(
            lambda: self._mark_dirty(self.teaching_mode_combo, teaching_group)
        )
        teaching_layout.addRow("默认教学模式：", self.teaching_mode_combo)

        self.no_direct_answer_check = QCheckBox("默认禁止直接给出答案")
        self.no_direct_answer_check.setToolTip("开启后 AI 默认只输出提示、思路引导和下一步思考方向")
        self.no_direct_answer_check.toggled.connect(
            lambda: self._mark_dirty(self.no_direct_answer_check, teaching_group)
        )
        teaching_layout.addRow(self.no_direct_answer_check)

        self.explanation_mode_combo = QComboBox()
        self.explanation_mode_combo.addItem("基础版", "basic")
        self.explanation_mode_combo.addItem("进阶版", "advanced")
        self.explanation_mode_combo.addItem("应试版", "exam")
        self.explanation_mode_combo.addItem("科研深度版", "research")
        self.explanation_mode_combo.setToolTip("切换知识点讲解的深度与风格")
        self.explanation_mode_combo.currentTextChanged.connect(
            lambda: self._mark_dirty(self.explanation_mode_combo, teaching_group)
        )
        teaching_layout.addRow("默认讲解模式：", self.explanation_mode_combo)

        layout.addWidget(teaching_group)

        # 界面设置
        ui_group = self._create_setting_card("界面设置")
        ui_layout = QFormLayout(ui_group)
        self._configure_form_layout(ui_layout)
        ui_header = QHBoxLayout()
        ui_header.addStretch()
        ui_header.addWidget(ui_group._dirty_indicator)  # type: ignore[attr-defined]
        ui_layout.addRow(ui_header)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["dark", "light"])
        self.theme_combo.currentTextChanged.connect(self._on_theme_changed)
        self.theme_combo.currentTextChanged.connect(
            lambda: self._mark_dirty(self.theme_combo, ui_group)
        )
        ui_layout.addRow("主题：", self.theme_combo)

        goal_row = QHBoxLayout()
        goal_label = QLabel("每日学习目标（分钟）：")
        self.daily_goal_spin = QSpinBox()
        self.daily_goal_spin.setRange(5, 720)
        self.daily_goal_spin.setSingleStep(5)
        self.daily_goal_spin.setToolTip("仪表盘热力图区会展示今日完成进度")
        goal_row.addWidget(goal_label)
        goal_row.addWidget(self.daily_goal_spin)
        goal_row.addStretch()
        ui_layout.addRow(goal_row)

        sync_header = QLabel("学习包同步（WebDAV 网盘，可选）")
        sync_header.setStyleSheet("font-weight: 600; background-color: transparent;")
        ui_layout.addRow(sync_header)
        self.sync_url_edit = QLineEdit()
        self.sync_url_edit.setPlaceholderText("WebDAV 地址，如 https://dav.jianguoyun.com/dav/")
        ui_layout.addRow("同步地址：", self.sync_url_edit)
        self.sync_user_edit = QLineEdit()
        self.sync_user_edit.setPlaceholderText("网盘账号（可留空）")
        ui_layout.addRow("同步账号：", self.sync_user_edit)
        self.sync_pass_edit = QLineEdit()
        self.sync_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.sync_pass_edit.setPlaceholderText("网盘密码/应用密码（可留空）")
        ui_layout.addRow("同步密码：", self.sync_pass_edit)

        self.demo_check = QCheckBox("演示模式（放大 UI 元素，适合投屏展示）")
        self.demo_check.toggled.connect(self._on_demo_mode_toggled)
        self.demo_check.toggled.connect(lambda: self._mark_dirty(self.demo_check, ui_group))
        ui_layout.addRow(self.demo_check)

        demo_btn_layout = QHBoxLayout()
        self.load_demo_btn = QPushButton("加载演示样例数据")
        self.load_demo_btn.setObjectName("primary_btn")
        self.load_demo_btn.setToolTip("一键导入模拟错题、闪卡与学习计划，用于快速体验各功能")
        self.load_demo_btn.clicked.connect(self._load_demo_data)
        demo_btn_layout.addWidget(self.load_demo_btn)
        demo_btn_layout.addStretch()
        ui_layout.addRow("演示数据：", demo_btn_layout)

        layout.addWidget(ui_group)

        # 高级功能预览（低优先级预留，弱化展示）
        preview_group = self._create_setting_card("高级功能预览")
        preview_layout = QFormLayout(preview_group)
        self._configure_form_layout(preview_layout)
        preview_header = QHBoxLayout()
        preview_header.addStretch()
        preview_header.addWidget(preview_group._dirty_indicator)  # type: ignore[attr-defined]
        preview_layout.addRow(preview_header)

        preview_hint = QLabel(
            "以下为进阶功能开关，开启后即可在对应模块使用。"
        )
        preview_hint.setWordWrap(True)
        preview_hint.setObjectName("subtitle_label")
        preview_layout.addRow(preview_hint)

        self.asr_check = QCheckBox("录音转文字 ASR")
        self.asr_check.setToolTip("开启后在「语音转文字」模块录音并离线转写")
        self.asr_check.toggled.connect(lambda: self._mark_dirty(self.asr_check, preview_group))
        preview_layout.addRow(self.asr_check)

        self.diagram_check = QCheckBox("AI 生成示意图（流程图 / 概念图 / Mermaid）")
        self.diagram_check.setToolTip("开启后可由 AI 生成流程图 / 概念图 / Mermaid 示意图")
        self.diagram_check.toggled.connect(lambda: self._mark_dirty(self.diagram_check, preview_group))
        preview_layout.addRow(self.diagram_check)

        self.package_check = QCheckBox("学习包导入与分享")
        self.package_check.setToolTip("开启后可在「学习包」模块打包、导入与分享学习资料")
        self.package_check.toggled.connect(lambda: self._mark_dirty(self.package_check, preview_group))
        preview_layout.addRow(self.package_check)

        layout.addWidget(preview_group)

        # 教学风格精细配置
        style_group = self._create_setting_card("教学风格精细配置")
        style_layout = QFormLayout(style_group)
        self._configure_form_layout(style_layout)
        style_header = QHBoxLayout()
        style_header.addStretch()
        style_header.addWidget(style_group._dirty_indicator)  # type: ignore[attr-defined]
        style_layout.addRow(style_header)

        self.style_check = QCheckBox("启用教学风格注入")
        self.style_check.setToolTip("启用后会在 AI 对话 system prompt 中注入以下风格约束")
        self.style_check.toggled.connect(lambda: self._mark_dirty(self.style_check, style_group))
        style_layout.addRow(self.style_check)

        self.style_combo = QComboBox()
        self.style_combo.addItem("严格", "strict")
        self.style_combo.addItem("鼓励", "encouraging")
        self.style_combo.addItem("简洁", "concise")
        self.style_combo.addItem("苏格拉底式", "socratic")
        self.style_combo.currentTextChanged.connect(
            lambda: self._mark_dirty(self.style_combo, style_group)
        )
        style_layout.addRow("基础风格：", self.style_combo)

        self.depth_combo = QComboBox()
        self.depth_combo.addItem("精简", "brief")
        self.depth_combo.addItem("适中", "moderate")
        self.depth_combo.addItem("详细", "detailed")
        self.depth_combo.currentTextChanged.connect(
            lambda: self._mark_dirty(self.depth_combo, style_group)
        )
        style_layout.addRow("讲解深度：", self.depth_combo)

        self.example_combo = QComboBox()
        self.example_combo.addItem("不举例", "none")
        self.example_combo.addItem("简单例子", "simple")
        self.example_combo.addItem("生活化例子", "life")
        self.example_combo.addItem("类比", "analogy")
        self.example_combo.addItem("代码示例", "code")
        self.example_combo.currentTextChanged.connect(
            lambda: self._mark_dirty(self.example_combo, style_group)
        )
        style_layout.addRow("示例偏好：", self.example_combo)

        self.tone_combo = QComboBox()
        self.tone_combo.addItem("正式", "formal")
        self.tone_combo.addItem("轻松", "casual")
        self.tone_combo.addItem("热情", "enthusiastic")
        self.tone_combo.addItem("平和", "calm")
        self.tone_combo.currentTextChanged.connect(
            lambda: self._mark_dirty(self.tone_combo, style_group)
        )
        style_layout.addRow("语气风格：", self.tone_combo)

        self.question_combo = QComboBox()
        self.question_combo.addItem("不主动提问", "none")
        self.question_combo.addItem("提示性确认", "hint")
        self.question_combo.addItem("挑战性提问", "challenge")
        self.question_combo.addItem("每个知识点后确认", "confirm")
        self.question_combo.currentTextChanged.connect(
            lambda: self._mark_dirty(self.question_combo, style_group)
        )
        style_layout.addRow("提问方式：", self.question_combo)

        self.local_lang_check = QCheckBox("优先使用中文回答")
        self.local_lang_check.toggled.connect(
            lambda: self._mark_dirty(self.local_lang_check, style_group)
        )
        style_layout.addRow(self.local_lang_check)

        self.avoid_answer_check = QCheckBox("不直接给答案，引导学生独立思考")
        self.avoid_answer_check.toggled.connect(
            lambda: self._mark_dirty(self.avoid_answer_check, style_group)
        )
        style_layout.addRow(self.avoid_answer_check)

        self.focus_mistakes_check = QCheckBox("重点提示常见错误与易混淆点")
        self.focus_mistakes_check.toggled.connect(
            lambda: self._mark_dirty(self.focus_mistakes_check, style_group)
        )
        style_layout.addRow(self.focus_mistakes_check)

        preview_hint.setText(
            "提示：教学风格、学习包、示意图、录音转文字均已可用，"
            "开启开关并保存后生效。"
        )

        layout.addWidget(style_group)

        # 语音朗读设置（<8GB 低配机器自动隐藏，中配默认关闭可手动开启，高配默认开启）
        self._maybe_build_voice_card(layout)

        # 数据备份与恢复
        backup_group = self._create_setting_card("数据备份与恢复")
        backup_layout = QVBoxLayout(backup_group)

        backup_hint = QLabel(
            "全量备份会将当前用户的错题、闪卡、知识库、对话、学习计划等数据打包为 zip。\n"
            "恢复备份会覆盖当前数据，请谨慎操作。错题与闪卡也可单独导出为 Markdown。"
        )
        backup_hint.setWordWrap(True)
        backup_hint.setObjectName("subtitle_label")
        backup_layout.addWidget(backup_hint)

        backup_btn_layout = QHBoxLayout()
        backup_btn_layout.setSpacing(10)

        self.export_backup_btn = QPushButton("导出全量备份")
        self.export_backup_btn.setObjectName("primary_btn")
        self.export_backup_btn.clicked.connect(self._export_full_backup)
        backup_btn_layout.addWidget(self.export_backup_btn)

        self.import_backup_btn = QPushButton("导入备份恢复")
        self.import_backup_btn.clicked.connect(self._import_full_backup)
        backup_btn_layout.addWidget(self.import_backup_btn)

        self.export_errors_md_btn = QPushButton("导出错题为 Markdown")
        self.export_errors_md_btn.clicked.connect(self._export_errors_markdown)
        backup_btn_layout.addWidget(self.export_errors_md_btn)

        self.export_flashcards_md_btn = QPushButton("导出闪卡为 Markdown")
        self.export_flashcards_md_btn.clicked.connect(self._export_flashcards_markdown)
        backup_btn_layout.addWidget(self.export_flashcards_md_btn)

        backup_btn_layout.addStretch()
        backup_layout.addLayout(backup_btn_layout)

        # 分项 JSON 导出
        json_btn_layout = QHBoxLayout()
        json_btn_layout.setSpacing(10)
        self.export_errors_json_btn = QPushButton("导出错题为 JSON")
        self.export_errors_json_btn.clicked.connect(self._export_errors_json)
        json_btn_layout.addWidget(self.export_errors_json_btn)
        self.export_notes_json_btn = QPushButton("导出笔记为 JSON")
        self.export_notes_json_btn.clicked.connect(self._export_notes_json)
        json_btn_layout.addWidget(self.export_notes_json_btn)
        json_btn_layout.addStretch()
        backup_layout.addLayout(json_btn_layout)

        # 自动备份配置
        auto_layout = QHBoxLayout()
        auto_layout.setSpacing(10)
        auto_layout.addWidget(QLabel("自动备份："))
        self.auto_backup_freq = QComboBox()
        self.auto_backup_freq.addItems(["关闭", "每天", "每周"])
        self.auto_backup_freq.setCurrentText(self._get_setting("auto_backup_frequency", "off"))
        auto_layout.addWidget(self.auto_backup_freq)
        auto_layout.addWidget(QLabel("保留份数："))
        self.auto_backup_keep = QSpinBox()
        self.auto_backup_keep.setRange(1, 50)
        self.auto_backup_keep.setValue(int(self._get_setting("auto_backup_keep_count", "5")))
        auto_layout.addWidget(self.auto_backup_keep)
        self.save_auto_backup_btn = QPushButton("保存自动备份设置")
        self.save_auto_backup_btn.clicked.connect(self._save_auto_backup_settings)
        auto_layout.addWidget(self.save_auto_backup_btn)
        auto_layout.addStretch()
        backup_layout.addLayout(auto_layout)

        layout.addWidget(backup_group)

        # 可折叠日志面板
        self.log_panel = self._build_log_panel()
        layout.addWidget(self.log_panel)

        # 底部操作区：保存设置 + 恢复默认，右下角对齐
        action_card = self._create_setting_card("操作")
        action_layout = QHBoxLayout(action_card)
        action_layout.setContentsMargins(16, 12, 16, 12)
        action_layout.addStretch()

        self.reset_btn = QPushButton("恢复默认")
        self.reset_btn.setToolTip("将当前页面参数恢复为默认配置")
        self.reset_btn.clicked.connect(self._reset_config)
        action_layout.addWidget(self.reset_btn)

        self.save_btn = QPushButton("保存设置")
        set_primary_button_style(self.save_btn)
        self.save_btn.setMinimumWidth(120)
        self.save_btn.clicked.connect(self._save_config)
        action_layout.addWidget(self.save_btn)

        layout.addWidget(action_card)
        layout.addStretch()

    def _build_hardware_cards(self) -> list:
        """构建三张硬件信息卡片：设备信息、显存配置、模型状态。"""
        c = theme_colors()
        hw = self.model_manager.hardware
        cards = []

        # ---- 设备信息卡片 ----
        device_card, device_form = self._create_card("设备信息")
        if hw:
            self._add_form_row(device_form, "CPU", self._create_value_label(hw.cpu_model))
            self._add_form_row(device_form, "内存", self._create_value_label(f"{hw.ram_total_gb:.1f} GB"))
            self._add_form_row(device_form, "系统", self._create_value_label(hw.os_name))
        else:
            device_form.addRow(QLabel("未检测到硬件信息"))
        cards.append(device_card)

        # ---- 显存配置卡片 ----
        vram_card, vram_form = self._create_card("显存配置")
        vram_text = f"{hw.gpu_vram_gb:.1f} GB" if (hw and hw.gpu_vram_gb > 0) else "未检测到独立显存"
        self._add_form_row(vram_form, "显存", self._create_value_label(vram_text))

        rec_text = "推荐配置：3B 轻量模型" if (hw and hw.is_low_spec) else "推荐配置：7B 高性能模型"
        rec_color = c["warning"] if (hw and hw.is_low_spec) else c["success"]
        self._add_form_row(vram_form, "推荐模型", create_badge_label(rec_text, rec_color))
        cards.append(vram_card)

        # ---- 模型状态卡片 ----
        status_card, status_form = self._create_card("模型状态")
        self.status_labels: Dict[str, QLabel] = {}
        for name, label_text in [("text", "文本模型"), ("vision", "视觉模型")]:
            badge = create_badge_label("待检测", c["fg_secondary"])
            self.status_labels[name] = badge
            self._add_form_row(status_form, label_text, badge)

        self.mode_status_label = create_badge_label("本地模式", c["accent"])
        self._add_form_row(status_form, "当前模式", self.mode_status_label)
        cards.append(status_card)

        return cards

    def _create_card(self, title: str) -> tuple:
        """创建带标题的扁平卡片，返回 (card_widget, form_layout)。"""
        card = QWidget()
        card.setProperty("card", True)
        apply_card_style(card)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # 设置卡片最小宽度，避免流式布局换行时被压得过窄
        card.setMinimumWidth(240)

        main_layout = QVBoxLayout(card)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        title_label = QLabel(title)
        title_label.setObjectName("section_title")
        main_layout.addWidget(title_label)

        form_layout = QFormLayout()
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(10)
        form_layout.setHorizontalSpacing(12)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        main_layout.addLayout(form_layout)
        main_layout.addStretch()
        return card, form_layout

    def _create_value_label(self, text: str) -> QLabel:
        """创建右对齐的数值标签。"""
        label = QLabel(str(text))
        label.setObjectName("value_label")
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        label.setWordWrap(False)
        return label

    def _add_form_row(self, form_layout: QFormLayout, label_text: str, field_widget: QWidget) -> None:
        """向 QFormLayout 添加一行，标签固定宽度，字段占据剩余空间。"""
        label = QLabel(label_text)
        label.setObjectName("subtitle_label")
        label.setMinimumWidth(64)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        label.setWordWrap(False)
        field_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        form_layout.addRow(label, field_widget)

    @staticmethod
    def _configure_form_layout(form_layout: QFormLayout) -> None:
        """统一设置表单布局策略，防止标签与控件错位、截断。"""
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(10)
        form_layout.setHorizontalSpacing(12)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

    @staticmethod
    def _set_combo_by_data(combo: QComboBox, data: Any) -> None:
        """根据 userData 设置 QComboBox 当前选中项，找不到时默认选第一项。"""
        index = combo.findData(data)
        combo.setCurrentIndex(max(0, index))

    def _build_warning_banner(self) -> QWidget:
        """构建顶部非阻塞健康警告条。"""
        c = theme_colors()
        banner = QWidget()
        banner.setProperty("card", True)
        apply_card_style(banner)
        self._bind_style(
            banner,
            lambda: (
                f"QWidget {{ border-left: 4px solid {theme_colors()['warning']};"
                f" background-color: {theme_colors()['accent_light']}; }}"
                "QLabel { background-color: transparent; }"
            ),
        )
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(10)

        self.warning_icon = QLabel("!")
        self._bind_style(
            self.warning_icon,
            lambda: f"color: {theme_colors()['warning']}; font-size: 16px; font-weight: bold; background-color: transparent;",
        )
        layout.addWidget(self.warning_icon)

        self.warning_label = QLabel("正在检测模型健康状态...")
        self.warning_label.setWordWrap(True)
        self._bind_style(
            self.warning_label,
            lambda: f"font-size: 13px; color: {theme_colors()['warning']}; background-color: transparent;",
        )
        layout.addWidget(self.warning_label, 1)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22, 22)
        close_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: transparent;
                color: {c['warning']};
                border: none;
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {c['accent_lighter']};
                border-radius: 4px;
            }}
            """
        )
        close_btn.clicked.connect(lambda: banner.hide())
        layout.addWidget(close_btn)

        self.warning_banner = banner
        return banner

    def _check_model_health(self) -> None:
        """执行模型健康自检并更新警告条内容。"""
        try:
            health = self.model_manager.health_check()
            issues = health.get("issues", [])
            if not issues:
                self.warning_banner.hide()
                return

            text = "\n".join(f"• {issue}" for issue in issues)
            self.warning_label.setText(text)
            self.warning_banner.show()
        except Exception as e:
            logger.warning("Failed to check model health: %s", e)
            self.warning_label.setText(f"健康自检失败：{e}")
            self.warning_banner.show()

    def _build_log_panel(self) -> QWidget:
        """构建可折叠的 AI / OCR 运行日志面板。"""
        panel = QWidget()
        panel.setProperty("card", True)
        apply_card_style(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(8)

        title = QLabel("运行日志（AI / OCR）")
        title.setObjectName("section_title")
        header.addWidget(title)

        header.addStretch()

        self.log_toggle_btn = QPushButton("收起")
        self.log_toggle_btn.setCheckable(True)
        self.log_toggle_btn.setChecked(True)
        self.log_toggle_btn.setMaximumWidth(80)
        self.log_toggle_btn.toggled.connect(self._toggle_log_panel)
        header.addWidget(self.log_toggle_btn)

        clear_btn = QPushButton("清空")
        clear_btn.setMaximumWidth(60)
        clear_btn.clicked.connect(self._clear_log)
        header.addWidget(clear_btn)

        layout.addLayout(header)

        self._style_bindings = []
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(500)
        self.log_text.setStyleSheet(
            "font-family: 'Consolas', 'Microsoft YaHei UI', 'Microsoft YaHei', monospace; font-size: 12px;"
        )
        layout.addWidget(self.log_text)

        level = getattr(
            logging,
            self.config.get("logging", {}).get("level", "INFO").upper(),
            logging.INFO,
        )
        self.log_handler = _QtLogHandler(level=level, parent=self)
        self.log_handler.log_record.connect(self._append_log)
        logging.getLogger().addHandler(self.log_handler)
        # 面板销毁时同步摘除 handler，避免解释器退出时访问已销毁的 Qt 对象
        self.destroyed.connect(self._detach_log_handler)

        return panel

    def _bind_style(self, widget, style_fn) -> None:
        """注册主题相关样式：主题切换时用新令牌重算并应用。"""
        if not hasattr(self, "_style_bindings"):
            self._style_bindings = []
        self._style_bindings.append((widget, style_fn))
        widget.setStyleSheet(style_fn())

    def _detach_log_handler(self, *args) -> None:
        handler = getattr(self, "log_handler", None)
        if handler is not None:
            try:
                logging.getLogger().removeHandler(handler)
            except Exception:
                pass
            self.log_handler = None

    def _toggle_log_panel(self, expanded: bool) -> None:
        """展开/收起日志文本区。"""
        self.log_text.setVisible(expanded)
        self.log_toggle_btn.setText("收起" if expanded else "展开")

    def _append_log(self, text: str) -> None:
        """追加一条日志到面板。"""
        try:
            self.log_text.appendPlainText(text)
            scrollbar = self.log_text.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
        except Exception as e:
            logger.debug("Failed to append log to UI: %s", e)

    def _clear_log(self) -> None:
        """清空日志面板。"""
        self.log_text.clear()

    def _on_demo_mode_toggled(self, enabled: bool) -> None:
        """切换演示模式并即时生效。"""
        try:
            self.theme_manager.set_demo_mode(enabled)
        except Exception as e:
            logger.warning("Failed to toggle demo mode: %s", e)

    def _load_demo_data(self) -> None:
        """一键导入演示样例数据。"""
        if not ask_confirm(
            self,
            "加载演示样例数据",
            "将导入模拟错题、闪卡与学习计划，用于快速体验各功能。\n是否继续？",
        ):
            return

        try:
            demo_service = DemoService(
                model_manager=self.model_manager,
                user_service=self.user_service,
            )
            counts = demo_service.load_demo_data()
            show_info(
                self,
                "演示数据已加载",
                f"成功导入 {counts['errors']} 道错题、{counts['flashcards']} 张闪卡、"
                f"{counts['plans']} 个学习计划。",
            )
        except Exception as e:
            logger.error("Load demo data failed: %s", e)
            show_error(self, "加载失败", f"导入演示数据失败：\n{e}")

    def refresh_data(self) -> None:
        """用户切换后重新同步配置显示（配置全局共享，界面以已保存值为准）。"""
        try:
            self._load_config()
        except Exception as e:
            logger.warning("Failed to reload settings after user switch: %s", e)

    def _load_config(self) -> None:
        backend_type = self.config.get("backend", {}).get("type", "ollama")
        if backend_type == InferenceBackend.CLOUD_API.value:
            self.radio_cloud.setChecked(True)
        else:
            self.radio_local.setChecked(True)

        backend_cfg = self.config.get("backend", {})
        self.ollama_host_edit.setText(backend_cfg.get("ollama_host", DEFAULT_OLLAMA_HOST))

        cloud_cfg = self.config.get("cloud", {})
        self.cloud_url_edit.setText(
            cloud_cfg.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        )
        self.cloud_key_edit.setText(cloud_cfg.get("api_key", ""))

        model_cfg = self.config.get("models", {})
        self.text_model_combo.setCurrentText(model_cfg.get("text_model", ""))
        self.vision_model_combo.setCurrentText(model_cfg.get("vision_model", ""))
        self.embedding_model_combo.setCurrentText(model_cfg.get("embedding_model", ""))

        self.temp_spin.setValue(float(model_cfg.get("temperature", 0.7)))
        self.max_tokens_spin.setValue(int(model_cfg.get("max_tokens", 2048)))
        self.ctx_spin.setValue(int(model_cfg.get("context_length", 8192)))

        hw_cfg = self.config.get("hardware", {})
        self.low_spec_check.setChecked(hw_cfg.get("low_spec_mode", "off") == "on")
        self.use_gpu_check.setChecked(hw_cfg.get("use_gpu", True))

        ui_cfg = self.config.get("ui", {})
        # 初始化同步不触发 set_theme：启动时主题已由 ThemeManager 按配置应用，
        # 构造期误触发会在主窗口 _build_ui 途中切换全局主题，造成样式错配
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentText(ui_cfg.get("theme", "light"))
        self.theme_combo.blockSignals(False)
        self.daily_goal_spin.setValue(
            int(ui_cfg.get("daily_goal_minutes", DEFAULT_DAILY_GOAL_MINUTES))
        )
        sync_cfg = self.config.get("sync", {})
        self.sync_url_edit.setText(sync_cfg.get("base_url", ""))
        self.sync_user_edit.setText(sync_cfg.get("username", ""))
        self.sync_pass_edit.setText(sync_cfg.get("password", ""))
        self.demo_check.setChecked(ui_cfg.get("demo_mode", False))

        profile = self.config.get("model_profile", "auto")
        self.profile_combo.setCurrentText(profile)

        chat_cfg = self.config.get("chat", {})
        mode = chat_cfg.get("teaching_mode", "normal")
        index = self.teaching_mode_combo.findData(mode)
        if index >= 0:
            self.teaching_mode_combo.setCurrentIndex(index)
        self.no_direct_answer_check.setChecked(bool(chat_cfg.get("no_direct_answer", False)))
        explain_mode = chat_cfg.get("explanation_mode", "basic")
        exp_index = self.explanation_mode_combo.findData(explain_mode)
        if exp_index >= 0:
            self.explanation_mode_combo.setCurrentIndex(exp_index)

        preview_cfg = self.config.get("preview_features", {})
        self.asr_check.setChecked(bool(preview_cfg.get("asr_enabled", False)))
        self.diagram_check.setChecked(bool(preview_cfg.get("diagram_enabled", False)))
        self.package_check.setChecked(bool(preview_cfg.get("package_enabled", False)))

        # 教学风格配置从数据库读取，未初始化时回退到配置文件
        if self.teaching_style_service is not None:
            try:
                style_cfg = self.teaching_style_service.get_config()
                self.style_check.setChecked(style_cfg.get("enabled", False))
                self._set_combo_by_data(self.style_combo, style_cfg.get("style", "encouraging"))
                self._set_combo_by_data(self.depth_combo, style_cfg.get("explanation_depth", "moderate"))
                self._set_combo_by_data(self.example_combo, style_cfg.get("example_style", "simple"))
                self._set_combo_by_data(self.tone_combo, style_cfg.get("tone", "calm"))
                self._set_combo_by_data(self.question_combo, style_cfg.get("question_style", "hint"))
                self.local_lang_check.setChecked(style_cfg.get("use_local_language", True))
                self.avoid_answer_check.setChecked(style_cfg.get("avoid_direct_answer", False))
                self.focus_mistakes_check.setChecked(style_cfg.get("focus_on_common_mistakes", False))
            except Exception as e:
                logger.warning("Failed to load teaching style config: %s", e)
                self.style_check.setChecked(bool(preview_cfg.get("teaching_style_enabled", False)))
                style_text = preview_cfg.get("teaching_style", "鼓励")
                style_index = self.style_combo.findText(style_text)
                self.style_combo.setCurrentIndex(max(0, style_index))

        self._on_mode_changed()
        self._snapshot_original_values()

    def _on_mode_changed(self) -> None:
        """根据运行模式切换显示本地或云端设置。"""
        is_cloud = self.radio_cloud.isChecked()
        self.local_settings.setVisible(not is_cloud)
        self.cloud_settings.setVisible(is_cloud)

    def _toggle_key_visibility(self, checked: bool) -> None:
        self.cloud_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )

    def _on_profile_changed(self, profile: str) -> None:
        """切换模型套装时自动填充对应模型名。"""
        if profile in MODEL_PROFILES:
            self.text_model_combo.setCurrentText(MODEL_PROFILES[profile]["text_model"])
            self.vision_model_combo.setCurrentText(MODEL_PROFILES[profile]["vision_model"])

    def _on_theme_changed(self, theme: str) -> None:
        """切换主题，即时生效。"""
        try:
            self.theme_manager.set_theme(theme)
        except Exception as e:
            logger.warning("Failed to change theme from settings: %s", e)

    def on_theme_changed(self) -> None:
        """主题切换：重算所有绑定样式，刷新徽章与整树样式。"""
        try:
            for widget, style_fn in getattr(self, "_style_bindings", []):
                try:
                    widget.setStyleSheet(style_fn())
                except RuntimeError:
                    continue  # 控件已销毁
            self._update_status_labels()
        except Exception as e:
            logger.warning("Failed to restyle settings panel: %s", e)
        repolish_tree(self)

    def _refresh_models(self) -> None:
        """刷新可用模型列表。"""
        try:
            with busy_cursor():
                text_models = self.model_manager.list_text_models()
                vision_models = self.model_manager.list_vision_models()
                embedding_models = self.model_manager.list_embedding_models()
        except Exception as e:
            logger.warning("Failed to list models: %s", e)
            show_warning(self, "模型列表", f"获取模型列表失败：\n{e}")
            return

        self._fill_combo(self.text_model_combo, text_models)
        self._fill_combo(self.vision_model_combo, vision_models)
        self._fill_combo(self.embedding_model_combo, embedding_models)
        self._update_status_labels()
        self._check_model_health()

    def _update_status_labels(self) -> None:
        """更新模型可用状态标签与当前运行模式。"""
        try:
            c = theme_colors()
            status = {
                "text": self.model_manager.is_text_available(),
                "vision": self.model_manager.is_vision_available(),
            }
            for name, available in status.items():
                badge = self.status_labels[name]
                if available:
                    self._set_badge_style(badge, "已就绪", c["success"])
                elif name == "vision":
                    # 视觉模型为可选组件，未检测到显示灰色提示
                    self._set_badge_style(badge, "可选安装", c["fg_secondary"])
                else:
                    self._set_badge_style(badge, "未检测到", c["error"])

            # 当前运行模式
            backend_type = self.config.get("backend", {}).get("type", "ollama")
            if backend_type == InferenceBackend.CLOUD_API.value:
                self._set_badge_style(self.mode_status_label, "云端 API", c["warning"])
            else:
                self._set_badge_style(self.mode_status_label, "本地 Ollama", c["accent"])
        except Exception as e:
            logger.warning("Failed to update status labels: %s", e)

    @staticmethod
    def _set_badge_style(label: QLabel, text: str, color: str) -> None:
        """设置徽章标签的文本与颜色。"""
        label.setText(text)
        label.setStyleSheet(
            f"""
            QLabel {{
                color: {color};
                background-color: {color}22;
                border: 1px solid {color}55;
                border-radius: 10px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: 600;
            }}
            """
        )

    def _test_connection(self) -> None:
        """测试当前后端连接。"""
        test_config = self._gather_config()
        self.test_btn.setEnabled(False)
        self.test_btn.setText("测试中...")
        try:
            with busy_cursor():
                self.model_manager.apply_config(test_config)
                text_ok = self.model_manager.is_text_available()
                vision_ok = self.model_manager.is_vision_available()
                embedding_ok = self.model_manager.is_embedding_available()
            self._update_status_labels()
            self._check_model_health()

            msg = (
                f"文本模型：{'可用' if text_ok else '不可用'}\n"
                f"视觉模型：{'可用' if vision_ok else '不可用'}\n"
                f"嵌入模型：{'可用' if embedding_ok else '不可用'}"
            )
            show_info(self, "连接测试", msg)
        except Exception as e:
            logger.error("Connection test failed: %s", e)
            show_error(self, "连接测试失败", str(e))
        finally:
            try:
                self.model_manager.apply_config(self.config)
            except Exception as e:
                logger.warning("Failed to restore model config: %s", e)
            self.test_btn.setEnabled(True)
            self.test_btn.setText("测试连接")

    @staticmethod
    def _fill_combo(combo: QComboBox, items: list) -> None:
        current = combo.currentText()
        combo.clear()
        combo.addItems(items)
        if current:
            combo.setCurrentText(current)

    def _gather_config(self) -> Dict[str, Any]:
        """从 UI 控件收集配置（仅覆盖面板中可编辑的小节）。"""
        is_cloud = self.radio_cloud.isChecked()
        backend_type = InferenceBackend.CLOUD_API.value if is_cloud else InferenceBackend.OLLAMA.value

        config: Dict[str, Any] = {
            "model_profile": self.profile_combo.currentText(),
            "backend": {
                "type": backend_type,
                "ollama_host": self.ollama_host_edit.text().strip(),
            },
            "cloud": {
                "base_url": self.cloud_url_edit.text().strip(),
                "api_key": self.cloud_key_edit.text().strip(),
                # 超时等未在面板中暴露的字段保留原值
                "timeout": self.config.get("cloud", {}).get("timeout", 120),
            },
            "models": {
                "text_model": self.text_model_combo.currentText().strip(),
                "vision_model": self.vision_model_combo.currentText().strip(),
                "embedding_model": self.embedding_model_combo.currentText().strip(),
                "temperature": self.temp_spin.value(),
                "max_tokens": self.max_tokens_spin.value(),
                "context_length": self.ctx_spin.value(),
            },
            "hardware": {
                "low_spec_mode": "on" if self.low_spec_check.isChecked() else "off",
                "use_gpu": self.use_gpu_check.isChecked(),
            },
            "ui": {
                "theme": self.theme_combo.currentText(),
                "daily_goal_minutes": self.daily_goal_spin.value(),
                "demo_mode": self.demo_check.isChecked(),
            },
            "sync": {
                "base_url": self.sync_url_edit.text().strip(),
                "username": self.sync_user_edit.text().strip(),
                "password": self.sync_pass_edit.text(),
            },
            "chat": {
                "teaching_mode": self.teaching_mode_combo.currentData() or "normal",
                "no_direct_answer": self.no_direct_answer_check.isChecked(),
                "explanation_mode": self.explanation_mode_combo.currentData() or "basic",
            },
            "preview_features": {
                "asr_enabled": self.asr_check.isChecked(),
                "diagram_enabled": self.diagram_check.isChecked(),
                "package_enabled": self.package_check.isChecked(),
                "teaching_style_enabled": self.style_check.isChecked(),
                "teaching_style": self.style_combo.currentText(),
            },
        }
        return config

    def _save_config(self) -> None:
        gathered = self._gather_config()
        old_type = self.config.get("backend", {}).get("type", "ollama")
        new_type = gathered.get("backend", {}).get("type", "ollama")

        if old_type != new_type:
            if not ask_confirm(
                self,
                "切换运行模式",
                "切换运行模式将清空当前对话会话，是否继续？",
            ):
                return

        # 面板只编辑部分小节，基于现有配置深合并，
        # 避免 save_config 整文件覆盖时抹掉 storage/ocr/rag/hotkeys 等未展示配置
        merged = copy.deepcopy(self.config)
        _deep_merge(merged, gathered)

        try:
            save_config(merged)
            self.config = merged
            self.model_manager.apply_config(self.config)
            self._save_teaching_style_config()
            self._update_status_labels()
            self._check_model_health()
            self._snapshot_original_values()
            show_success(self, "配置已保存并生效。")
        except Exception as e:
            logger.error("Save config failed: %s", e)
            show_error(self, "保存失败", f"保存配置失败：\n{e}")

    def _reset_config(self) -> None:
        """恢复为当前已保存的配置，撤销未保存的修改。"""
        dirty = any(indicator.isVisible() for indicator in self._dirty_indicators.values())
        if dirty:
            if not ask_confirm(
                self,
                "恢复默认配置",
                "存在未保存的修改，确定要恢复到上次保存的状态吗？",
            ):
                return
        self._load_config()
        show_info(self, "已恢复", "页面参数已恢复为上次保存的配置。")

    def _save_teaching_style_config(self) -> None:
        """将教学风格配置持久化到数据库。"""
        if self.teaching_style_service is None:
            return
        try:
            config = {
                "style": self.style_combo.currentData() or "encouraging",
                "explanation_depth": self.depth_combo.currentData() or "moderate",
                "example_style": self.example_combo.currentData() or "simple",
                "tone": self.tone_combo.currentData() or "calm",
                "question_style": self.question_combo.currentData() or "hint",
                "use_local_language": self.local_lang_check.isChecked(),
                "avoid_direct_answer": self.avoid_answer_check.isChecked(),
                "focus_on_common_mistakes": self.focus_mistakes_check.isChecked(),
            }
            enabled = self.style_check.isChecked()
            self.teaching_style_service.save_config(config, enabled)
        except Exception as e:
            logger.error("Save teaching style config failed: %s", e)
            show_error(self, "保存失败", f"教学风格配置保存失败：\n{e}")

    def _export_full_backup(self) -> None:
        """导出全量学习数据备份（.plosbackup）。"""
        if self.backup_service is None:
            show_error(self, "备份失败", "备份服务未初始化")
            return
        # 导出前校验数据库完整性
        try:
            ok, msg = self.backup_service.check_database_integrity()
            if not ok:
                if not ask_confirm(
                    self, "数据库完整性警告",
                    f"数据库检测到问题：{msg}\n\n是否仍继续导出备份？",
                ):
                    return
        except Exception as e:
            logger.warning("Integrity check skipped: %s", e)

        default_name = f"plos_backup_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')}.plosbackup"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出全量备份",
            default_name,
            "PLOS 备份 (*.plosbackup)",
        )
        if not file_path:
            return
        try:
            output = self.backup_service.export_full_backup(Path(file_path))
            show_info(self, "备份成功", f"备份已保存到：\n{output}")
        except Exception as e:
            logger.error("Export backup failed: %s", e)
            show_error(self, "备份失败", friendly_error_message(e, "备份未完成，请稍后重试"))

    def _import_full_backup(self) -> None:
        """从备份文件恢复数据。"""
        if self.backup_service is None:
            show_error(self, "恢复失败", "备份服务未初始化")
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "导入备份恢复",
            "",
            "PLOS 备份 (*.plosbackup *.zip);;所有文件 (*.*)",
        )
        if not file_path:
            return
        if not ask_confirm(
            self, "确认恢复",
            "恢复备份将覆盖当前用户的所有数据（错题、笔记、知识库等），且操作不可撤销！\n\n是否继续？",
        ):
            return
        try:
            count = self.backup_service.import_full_backup(Path(file_path))
            show_info(self, "恢复成功", f"已成功恢复约 {count} 条数据。\n建议重启程序以加载恢复后的数据。")
        except Exception as e:
            logger.error("Import backup failed: %s", e)
            show_error(self, "恢复失败", friendly_error_message(e, "备份文件无法恢复，请确认文件完整后重试"))

    def _get_setting(self, key: str, default: str = "") -> str:
        """读取 settings 表中的配置值。"""
        try:
            if self.user_service is None:
                return default
            row = self.user_service.db.fetchone(
                "SELECT value FROM settings WHERE key = ?", (key,)
            )
            return row["value"] if row else default
        except Exception:
            return default

    def _maybe_build_voice_card(self, layout) -> None:
        """根据硬件分级决定是否构建语音设置卡片。

        低配（≤7GB）：不构建，语音入口禁用；
        中配（8~12GB）：默认关闭，用户可手动勾选开启；
        高配（>12GB）：默认开启。
        """
        try:
            from ..config.hardware import detect_hardware, get_hardware_tier, HardwareTier
            hw = detect_hardware()
            tier = get_hardware_tier(hw)
            if tier == HardwareTier.LOW:
                logger.info("Low-spec: voice settings card hidden")
                return

            voice_group = self._create_setting_card("语音朗读设置")
            v_layout = QVBoxLayout(voice_group)

            hint = QLabel(
                "本地离线语音朗读（TTS），无需联网。低配机器自动禁用；中配默认关闭可手动开启，高配默认开启。"
            )
            hint.setWordWrap(True)
            hint.setObjectName("subtitle_label")
            v_layout.addWidget(hint)

            # 启用开关
            self.voice_enabled_check = QCheckBox("启用语音朗读功能")
            saved_enabled = self._get_setting("voice_enabled", "")
            if saved_enabled == "":
                # 未设置过：高配默认开，中配默认关
                self.voice_enabled_check.setChecked(tier == HardwareTier.HIGH)
            else:
                self.voice_enabled_check.setChecked(saved_enabled in ("1", "true", "yes", "on"))
            v_layout.addWidget(self.voice_enabled_check)

            # 音色
            voice_row = QHBoxLayout()
            voice_row.addWidget(QLabel("朗读音色："))
            self.voice_combo = QComboBox()
            self.voice_combo.addItem("（系统默认）", "")
            try:
                if self.tts_service is not None:
                    for v in self.tts_service.list_voices():
                        self.voice_combo.addItem(v.get("name", "未知"), v.get("id", ""))
            except Exception:
                pass
            cur_voice = self._get_setting("tts_voice_id", "")
            for i in range(self.voice_combo.count()):
                if self.voice_combo.itemData(i) == cur_voice:
                    self.voice_combo.setCurrentIndex(i)
                    break
            voice_row.addWidget(self.voice_combo, 1)
            v_layout.addLayout(voice_row)

            # 语速
            rate_row = QHBoxLayout()
            rate_row.addWidget(QLabel("朗读语速："))
            self.voice_rate_spin = QSpinBox()
            self.voice_rate_spin.setRange(80, 300)
            self.voice_rate_spin.setSingleStep(10)
            try:
                cur_rate = int(self._get_setting("tts_rate", "180"))
            except Exception:
                cur_rate = 180
            self.voice_rate_spin.setValue(cur_rate)
            rate_row.addWidget(self.voice_rate_spin)
            rate_row.addWidget(QLabel("（数值越大读得越快，默认 180）"))
            rate_row.addStretch()
            v_layout.addLayout(rate_row)

            # 保存按钮
            save_row = QHBoxLayout()
            save_voice_btn = QPushButton("保存语音设置")
            save_voice_btn.clicked.connect(self._save_voice_settings)
            save_row.addWidget(save_voice_btn)
            save_row.addStretch()
            v_layout.addLayout(save_row)

            layout.addWidget(voice_group)
        except Exception as e:
            logger.warning("Build voice card failed: %s", e)

    def _save_voice_settings(self) -> None:
        """保存语音启用开关、音色、语速。"""
        try:
            if self.user_service is None:
                show_error(self, "保存失败", "用户服务未初始化")
                return
            enabled = "true" if self.voice_enabled_check.isChecked() else "false"
            self.user_service.db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                ("voice_enabled", enabled),
            )
            voice_id = self.voice_combo.currentData() or ""
            self.user_service.db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                ("tts_voice_id", voice_id),
            )
            rate = str(self.voice_rate_spin.value())
            self.user_service.db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                ("tts_rate", rate),
            )
            # 立即应用到 TTS 服务
            if self.tts_service is not None:
                try:
                    self.tts_service.set_rate(self.voice_rate_spin.value())
                    self.tts_service.set_voice(voice_id)
                except Exception:
                    pass
            show_info(self, "保存成功", "语音设置已保存。")
        except Exception as e:
            logger.error("Save voice settings failed: %s", e)
            show_error(self, "保存失败", str(e))

    def _save_auto_backup_settings(self) -> None:
        """保存自动备份配置到 settings 表。"""
        freq_map = {"关闭": "off", "每天": "daily", "每周": "weekly"}
        freq = freq_map.get(self.auto_backup_freq.currentText(), "off")
        keep = str(self.auto_backup_keep.value())
        try:
            if self.user_service is None:
                show_error(self, "保存失败", "用户服务未初始化")
                return
            self.user_service.db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                ("auto_backup_frequency", freq),
            )
            self.user_service.db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                ("auto_backup_keep_count", keep),
            )
            show_info(self, "保存成功", f"自动备份已设置为：{self.auto_backup_freq.currentText()}，保留 {keep} 份")
        except Exception as e:
            logger.error("Save auto backup settings failed: %s", e)
            show_error(self, "保存失败", str(e))

    def _export_errors_json(self) -> None:
        """导出错题为 JSON。"""
        if self.backup_service is None:
            show_error(self, "导出失败", "备份服务未初始化")
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出错题为 JSON", "errors.json", "JSON (*.json)"
        )
        if not file_path:
            return
        try:
            count = self.backup_service.export_errorbook_json(Path(file_path))
            show_info(self, "导出成功", f"成功导出 {count} 道错题到 {file_path}")
        except Exception as e:
            logger.error("Export errors json failed: %s", e)
            show_error(self, "导出失败", friendly_error_message(e, "导出未完成，请稍后重试"))

    def _export_notes_json(self) -> None:
        """导出笔记为 JSON。"""
        if self.backup_service is None:
            show_error(self, "导出失败", "备份服务未初始化")
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出笔记为 JSON", "notes.json", "JSON (*.json)"
        )
        if not file_path:
            return
        try:
            count = self.backup_service.export_notes_json(Path(file_path))
            show_info(self, "导出成功", f"成功导出 {count} 条笔记到 {file_path}")
        except Exception as e:
            logger.error("Export notes json failed: %s", e)
            show_error(self, "导出失败", friendly_error_message(e, "导出未完成，请稍后重试"))


    def _export_errors_markdown(self) -> None:
        """导出错题为 Markdown。"""
        if self.backup_service is None:
            show_error(self, "导出失败", "备份服务未初始化")
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出错题为 Markdown",
            "errors.md",
            "Markdown (*.md)",
        )
        if not file_path:
            return
        try:
            count = self.backup_service.export_errors_to_markdown(Path(file_path))
            show_info(self, "导出成功", f"成功导出 {count} 道错题到 {file_path}")
        except Exception as e:
            logger.error("Export errors markdown failed: %s", e)
            show_error(self, "导出失败", friendly_error_message(e, "导出未完成，请稍后重试"))

    def _export_flashcards_markdown(self) -> None:
        """导出闪卡为 Markdown。"""
        if self.backup_service is None:
            show_error(self, "导出失败", "备份服务未初始化")
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出闪卡为 Markdown",
            "flashcards.md",
            "Markdown (*.md)",
        )
        if not file_path:
            return
        try:
            count = self.backup_service.export_flashcards_to_markdown(Path(file_path))
            show_info(self, "导出成功", f"成功导出 {count} 张闪卡到 {file_path}")
        except Exception as e:
            logger.error("Export flashcards markdown failed: %s", e)
            show_error(self, "导出失败", friendly_error_message(e, "导出未完成，请稍后重试"))

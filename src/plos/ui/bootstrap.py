"""启动编排（异步版）：非 UI 初始化全部在后台线程执行，UI 构建回到主线程。

阶段与进度（与闪屏底部文案一一对应）：

===== ======= ==================================================
区间   阶段     执行位置   真实工作
0-20   ollama   后台线程  创建模型管理器 + 探测 Ollama（超时 1.5s）
20-40  index    后台线程  打开知识库、读取索引（不加载原文 / 向量）
40-60  hardware 后台线程  检测电脑硬件并自动识别高低配
60-80  ui       主线程    构建基础 UI 组件（主窗口骨架与首屏）
80-95  model    主线程    应用基础模型配置（按档次补全默认模型）
95-100 enter    主线程    收尾，准备进入主程序
===== ======= ==================================================

关键点：
- **不阻塞 UI**：后台线程跑耗时初始化，主线程用 ``QEventLoop`` 等待，
  期间闪屏动画照常播放、窗口可拖拽；
- **懒加载**：OCR 只建模块对象（模型在首次识别时才加载）、
  知识库只读索引（文档原文与向量按需加载）、
  ASR/Whisper 不在启动期加载模型；
- **超时**：Ollama 探测 1.5s 超时即判定未检测到；
  整体加载超过 20s 弹窗提示，可选择继续等待或跳过非核心模块；
- **兜底**：Ollama 不可用提示「继续使用 API 模式 / 退出」；
  数据库损坏提示「重置知识库 / 退出」；异常不闪退。

依赖均可注入，便于离屏测试各种场景。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PyQt6.QtCore import QEventLoop, QObject, QTimer, pyqtSignal

from plos.utils.logger import get_logger

logger = get_logger("ui.bootstrap")

#: Ollama 探测超时（秒）：超时直接判定未检测到，不长期阻塞启动
DEFAULT_OLLAMA_TIMEOUT_S = 1.5
#: 整体加载超时（秒）：超过则提示用户，避免误以为卡死
DEFAULT_LOAD_TIMEOUT_S = 20.0

#: 阶段文案（与需求逐字对应）
STAGE_OLLAMA = "正在检测本地 Ollama 服务…"
STAGE_INDEX = "正在读取知识库索引…"
STAGE_HARDWARE = "正在检测硬件配置…"
STAGE_UI = "正在加载基础 UI 组件…"
STAGE_MODEL = "正在加载基础模型配置…"
STAGE_ENTER = "准备完成，进入主程序…"


@dataclass
class StartupOutcome:
    """启动结果。"""

    action: str = "ok"                 # ok | exit
    window: Any = None
    config: Dict[str, Any] = field(default_factory=dict)
    skipped: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# 默认依赖（延迟导入，避免启动时加载重模块）
# ----------------------------------------------------------------------

def _default_model_manager(config: Dict[str, Any], hardware: Any) -> Any:
    from plos.ai import ModelManager

    return ModelManager(config, hardware=hardware)


def _default_db() -> Any:
    from plos.db import get_db

    return get_db()


def _default_ocr_module(model_manager: Any) -> Any:
    """只构造 OCR 模块对象（不加载 VL 模型，模型在首次识别时按需加载）。"""
    from plos.services.ocr_service import OCRService

    return OCRService(model_manager)


def _default_window(config: Dict[str, Any], model_manager: Any, theme_manager: Any) -> Any:
    from plos.ui.main_window import MainWindow

    return MainWindow(config=config, model_manager=model_manager, theme_manager=theme_manager)


# ----------------------------------------------------------------------
# 后台工作线程
# ----------------------------------------------------------------------

class StartupWorker(QObject):
    """在后台线程执行所有非 UI 初始化，并把进度推送给主线程。"""

    #: 进度（百分比, 文案）
    progress = pyqtSignal(float, str)
    #: 请求主线程弹出提示卡片（标题, 正文, 选项列表）
    ask_user = pyqtSignal(str, str, object)
    #: 全部完成（StartupOutcome）
    finished = pyqtSignal(object)

    def __init__(
        self,
        config: Dict[str, Any],
        hardware: Any = None,
        *,
        model_manager_factory: Optional[Callable[[Dict[str, Any], Any], Any]] = None,
        db_factory: Optional[Callable[[], Any]] = None,
        ocr_module_factory: Optional[Callable[[Any], Any]] = None,
        hardware_probe: Optional[Callable[[], Any]] = None,
        ollama_timeout_s: float = DEFAULT_OLLAMA_TIMEOUT_S,
        ask_timeout_s: float = 120.0,
    ) -> None:
        super().__init__()
        self._config = config
        self._hardware = hardware
        self._model_manager_factory = model_manager_factory or _default_model_manager
        self._db_factory = db_factory or _default_db
        self._ocr_module_factory = ocr_module_factory or _default_ocr_module
        self._hardware_probe = hardware_probe
        self._ollama_timeout_s = float(ollama_timeout_s)
        self._ask_timeout_s = float(ask_timeout_s)

        self.outcome = StartupOutcome(config=config)
        self.model_manager: Any = None
        self.db: Any = None
        self.ocr_module: Any = None
        self.hardware: Any = hardware

        self._thread: Optional[threading.Thread] = None
        self._skip_event = threading.Event()
        self._ask_event = threading.Event()
        self._ask_holder: Dict[str, Any] = {"key": None}
        self._ask_lock = threading.Lock()
        self._started_at = 0.0

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._started_at = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="plos-startup", daemon=True)
        self._thread.start()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def request_skip(self) -> None:
        """请求跳过非核心模块（OCR 预加载 / 知识库预扫描）。"""
        self._skip_event.set()
        logger.info("已请求跳过非核心模块")

    @property
    def skip_requested(self) -> bool:
        return self._skip_event.is_set()

    def elapsed(self) -> float:
        return time.monotonic() - self._started_at if self._started_at else 0.0

    # ------------------------------------------------------------------
    # 与主线程交互
    # ------------------------------------------------------------------

    def provide_answer(self, key: str) -> None:
        """主线程回填用户选择。"""
        with self._ask_lock:
            self._ask_holder["key"] = key
            self._ask_event.set()

    def _ask(self, title: str, message: str, options: Sequence[Tuple[str, str]]) -> str:
        """后台线程内请求用户选择：发信号给主线程并等待结果。"""
        with self._ask_lock:
            self._ask_holder = {"key": None}
            self._ask_event.clear()
        self.ask_user.emit(title, message, list(options))
        if not self._ask_event.wait(self._ask_timeout_s):
            logger.warning("等待用户选择超时，按默认选项继续: %s", title)
        key = self._ask_holder.get("key")
        return key or (options[0][1] if options else "")

    def _report(self, percent: float, text: str = "") -> None:
        try:
            self.progress.emit(float(percent), text)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 主流程（后台线程）
    # ------------------------------------------------------------------

    def _run(self) -> None:
        try:
            if not self._stage_ollama():
                return
            if not self._stage_index():
                return
            self._stage_hardware()
            self._stage_ocr_module()
        except Exception as exc:  # 兜底：后台异常不影响主线程
            logger.exception("后台启动初始化异常: %s", exc)
            self.outcome.errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            try:
                self.finished.emit(self.outcome)
            except Exception:
                pass

    # ---- 阶段 1：Ollama（0-20%） ----

    def _stage_ollama(self) -> bool:
        self._report(4, STAGE_OLLAMA)
        # 模型档次的补全必须早于 ModelManager 构造，否则默认模型不生效
        self._prepare_model_config()
        try:
            self.model_manager = self._model_manager_factory(self._config, self._hardware)
        except Exception as exc:
            logger.error("模型管理器初始化失败: %s", exc)
            self.outcome.errors.append(f"model_manager: {exc}")
            self._ask(
                "模型服务初始化失败",
                f"初始化模型管理器失败：\n{exc}\n\n请检查安装是否完整，或查看 logs/plos.log。",
                [("退出", "exit")],
            )
            self.outcome.action = "exit"
            return False

        if self._probe_ollama(self.model_manager):
            self._report(20)
            return True

        choice = self._ask(
            "未检测到 Ollama 服务",
            f"本地模型服务在 {self._ollama_timeout_s:g} 秒内未响应（可能未启动）。\n"
            "你可以继续使用 API 模式，稍后在「模型管理」中填写云端密钥；\n"
            "也可以退出程序，启动 Ollama 后重新打开。",
            [("继续使用 API 模式", "continue"), ("退出", "exit")],
        )
        if choice == "exit":
            self.outcome.action = "exit"
            return False
        logger.info("用户选择在未检测到 Ollama 的情况下继续（API 模式）")
        self._report(20)
        return True

    def _prepare_model_config(self) -> None:
        """按硬件档次补全默认模型配置（必须在 ModelManager 构造之前完成）。"""
        try:
            from plos.config import MODEL_PROFILES, get_recommended_model_profile

            if self._config.get("backend", {}).get("type", "ollama") != "ollama":
                return
            setting = self._config.get("model_profile", "auto")
            if setting == "auto":
                profile = (
                    get_recommended_model_profile(self.hardware)
                    if self.hardware is not None
                    else "7b"
                )
            else:
                profile = setting if setting in MODEL_PROFILES else "7b"
            models = self._config.setdefault("models", {})
            models.setdefault("text_model", MODEL_PROFILES[profile]["text_model"])
            models.setdefault("vision_model", MODEL_PROFILES[profile]["vision_model"])
            models.setdefault("embedding_model", "nomic-embed-text")
        except Exception as exc:
            logger.warning("模型配置补全失败（使用现有配置）: %s", exc)

    def _probe_ollama(self, model_manager: Any) -> bool:
        """带超时的 Ollama 探测（默认 1.5 秒），超时即判定未检测到。"""
        result = {"ok": False}
        done = threading.Event()

        def _work() -> None:
            try:
                result["ok"] = bool(model_manager.is_text_available())
            except Exception as exc:
                logger.warning("Ollama 探测失败: %s", exc)
                result["ok"] = False
            finally:
                done.set()

        thread = threading.Thread(target=_work, name="ollama-probe", daemon=True)
        thread.start()
        if not done.wait(self._ollama_timeout_s):
            logger.warning("Ollama 探测超时（%.1fs），判定为未检测到", self._ollama_timeout_s)
            return False
        return bool(result["ok"])

    # ---- 阶段 2：知识库索引（20-40%） ----

    def _stage_index(self) -> bool:
        self._report(26, STAGE_INDEX)
        if self.skip_requested:
            logger.info("跳过知识库预扫描（用户选择跳过非核心模块）")
            self.outcome.skipped.append("index")
            self._report(40)
            return True
        try:
            # get_db 只做建表 / 迁移 / 索引准备，不加载文档原文与向量
            self.db = self._db_factory()
            self._report(40)
            return True
        except Exception as exc:
            logger.error("知识库数据库打开失败: %s", exc)
            self.outcome.errors.append(f"database: {exc}")
            choice = self._ask(
                "知识库异常",
                f"无法打开知识库数据库：\n{exc}\n\n"
                "可以重置知识库（会清空本地的错题、笔记、闪卡等数据，"
                "建议先备份 data 目录），或退出程序手工处理。",
                [("重置知识库", "reset"), ("退出", "exit")],
            )
            if choice != "reset":
                self.outcome.action = "exit"
                return False
            if not self._reset_database():
                self._ask(
                    "重置失败",
                    "未能重建知识库数据库，请关闭程序后手工删除 data/plos.db 再试。",
                    [("退出", "exit")],
                )
                self.outcome.action = "exit"
                return False
            try:
                self.db = self._db_factory()
            except Exception as retry_exc:
                logger.error("重置后仍无法打开知识库: %s", retry_exc)
                self._ask(
                    "知识库仍不可用",
                    f"重置后依旧无法打开知识库：\n{retry_exc}",
                    [("退出", "exit")],
                )
                self.outcome.action = "exit"
                return False
            self.outcome.skipped.append("database-reset")
            self._report(40)
            return True

    def _reset_database(self) -> bool:
        """关闭单例并删除数据库文件（含 WAL/SHM），以便重建。"""
        try:
            from plos.db.database import Database
            from plos.utils.paths import get_db_path

            db_path = Path(get_db_path())
            try:
                instance = Database._instances.pop(db_path.resolve(), None)
                if instance is not None:
                    instance.close()
            except Exception as exc:
                logger.warning("关闭旧数据库连接失败: %s", exc)
            for suffix in ("", "-wal", "-shm"):
                target = Path(str(db_path) + suffix)
                if target.exists():
                    target.unlink()
            logger.info("知识库已重置: %s", db_path)
            return True
        except Exception as exc:
            logger.error("重置知识库失败: %s", exc)
            return False

    # ---- 阶段 3：硬件检测（40-60%） ----

    def _stage_hardware(self) -> None:
        self._report(46, STAGE_HARDWARE)
        try:
            if self._hardware_probe is not None:
                self.hardware = self._hardware_probe()
            elif self.hardware is None:
                from plos.config import detect_hardware

                self.hardware = detect_hardware()
            tier = getattr(self.hardware, "tier", None) or getattr(self.hardware, "tier_name", "")
            logger.info("硬件检测完成: tier=%s", tier or "unknown")
        except Exception as exc:
            logger.warning("硬件检测失败（按默认档次继续）: %s", exc)
            self.outcome.errors.append(f"hardware: {exc}")
        self._report(60)

    # ---- 阶段 3.5：OCR 模块对象（不加载模型） ----

    def _stage_ocr_module(self) -> None:
        if self.skip_requested:
            logger.info("跳过 OCR 预加载（用户选择跳过非核心模块）")
            self.outcome.skipped.append("ocr")
            return
        try:
            # 只构造对象；VL 模型在用户首次打开识别功能时才加载
            self.ocr_module = self._ocr_module_factory(self.model_manager)
            logger.info("OCR 模块对象已就绪（模型延迟到首次使用时加载）")
        except Exception as exc:
            logger.warning("OCR 模块初始化异常（按降级处理）: %s", exc)
            self.outcome.errors.append(f"ocr: {exc}")


# ----------------------------------------------------------------------
# 主线程编排
# ----------------------------------------------------------------------

class StartupCoordinator(QObject):
    """主线程侧：驱动闪屏、等待后台完成、构建 UI 并收尾。"""

    def __init__(
        self,
        splash: Any,
        config: Dict[str, Any],
        hardware: Any = None,
        *,
        window_factory: Optional[Callable[[Dict[str, Any], Any, Any], Any]] = None,
        worker_factory: Optional[Callable[..., StartupWorker]] = None,
        load_timeout_s: float = DEFAULT_LOAD_TIMEOUT_S,
        ollama_timeout_s: float = DEFAULT_OLLAMA_TIMEOUT_S,
        apply_theme: bool = True,
        run_backup: bool = True,
        **worker_kwargs: Any,
    ) -> None:
        super().__init__()
        self._splash = splash
        self._config = config
        self._hardware = hardware
        self._window_factory = window_factory or _default_window
        self._worker_factory = worker_factory or StartupWorker
        self._worker_kwargs = worker_kwargs
        self._load_timeout_s = float(load_timeout_s)
        self._ollama_timeout_s = float(ollama_timeout_s)
        self._apply_theme = apply_theme
        self._run_backup = run_backup

        self._worker: Optional[StartupWorker] = None
        self._theme_manager: Any = None
        self._loop: Optional[QEventLoop] = None
        self._timeout_timer: Optional[QTimer] = None
        self._timeout_notified = False
        self._finished = False
        self._started_at = 0.0

    # ------------------------------------------------------------------
    # 对外
    # ------------------------------------------------------------------

    @property
    def model_manager(self) -> Any:
        return self._worker.model_manager if self._worker else None

    @property
    def theme_manager(self) -> Any:
        return self._theme_manager

    def run(self) -> StartupOutcome:
        """执行完整启动编排（后台初始化 + 主线程 UI 构建）。"""
        self._started_at = time.monotonic()
        worker = self._worker_factory(
            self._config,
            self._hardware,
            ollama_timeout_s=self._ollama_timeout_s,
            **self._worker_kwargs,
        )
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.ask_user.connect(self._on_ask_user)
        worker.finished.connect(self._on_worker_finished)
        self._start_timeout_watch()

        splash = self._splash
        try:
            if splash is not None:
                splash.set_skip_available(True)   # 后台阶段允许跳过非核心模块
                # 用户点击「跳过非核心模块」→ 通知后台线程跳过剩余非核心工作
                if hasattr(splash, "skip_requested"):
                    splash.skip_requested.connect(self._on_skip_clicked)
                splash.pump()
        except Exception as exc:
            logger.debug("初始化闪屏状态失败: %s", exc)

        # 后台跑初始化；主线程在事件循环中等待（闪屏动画/拖拽照常）
        loop = QEventLoop()
        self._loop = loop
        worker.start()
        loop.exec()

        outcome = worker.outcome
        if outcome.action != "exit":
            try:
                self._after_background(outcome)
            except Exception as exc:
                logger.exception("UI 构建阶段异常: %s", exc)
                outcome.errors.append(f"ui: {exc}")
                self._safe_ask(
                    "界面加载失败",
                    f"主窗口初始化失败：\n{exc}\n\n可查看 logs/plos.log 了解详情。",
                    [("退出", "exit")],
                )
                outcome.action = "exit"
        return self._finalize(outcome)

    # ------------------------------------------------------------------
    # 后台完成后的主线程阶段（UI 60-80%，模型配置 80-95%，收尾 95-100%）
    # ------------------------------------------------------------------

    def _after_background(self, outcome: StartupOutcome) -> None:
        worker = self._worker

        # 主题必须在主窗口之前就绪（MainWindow 依赖 theme_manager）
        if self._apply_theme:
            self._setup_theme()

        self._set_stage(62, STAGE_UI)
        self._set_skip_available(False)   # UI 构建属核心步骤，不再允许跳过
        build_started = time.monotonic()
        outcome.window = self._window_factory(
            self._config, worker.model_manager, self._theme_manager
        )
        # 启动耗时诊断：主窗口构建是启动期最容易劣化的一步，单独记录
        logger.info("主窗口构建完成: %.2fs", time.monotonic() - build_started)
        self._set_stage(80)

        # 基础模型配置：把补全后的模型配置写回磁盘，并做启动期自动备份
        self._set_stage(84, STAGE_MODEL)
        tail_started = time.monotonic()
        try:
            self._persist_model_config()
            if self._run_backup and worker.db is not None:
                self._run_auto_backup(worker.db)
        except Exception as exc:
            logger.error("模型配置阶段失败: %s", exc)
            outcome.errors.append(f"model: {exc}")
        logger.info("模型配置与备份完成: %.2fs", time.monotonic() - tail_started)
        self._set_stage(95)

        self._set_stage(100, STAGE_ENTER)

    def _persist_model_config(self) -> None:
        """把补全后的模型配置写回配置文件，保证下次启动一致。"""
        try:
            from plos.config import save_config

            save_config(self._config)
        except Exception as exc:
            logger.warning("保存启动配置失败: %s", exc)

    def _setup_theme(self) -> None:
        from PyQt6.QtWidgets import QApplication

        from plos.ui import ui_utils
        from plos.ui.theme_manager import ThemeManager

        theme_name = self._config.get("ui", {}).get("theme", "light")
        self._theme_manager = ThemeManager(theme_name)
        ui_utils.set_theme_manager(self._theme_manager)
        self._theme_manager.apply(QApplication.instance())

    def _run_auto_backup(self, db: Any) -> None:
        try:
            from plos.services.backup_service import BackupService

            backup_path = BackupService(db=db).run_auto_backup()
            if backup_path:
                logger.info("Auto backup ready: %s", backup_path)
        except Exception as exc:
            logger.warning("启动期自动备份跳过: %s", exc)

    # ------------------------------------------------------------------
    # 信号槽
    # ------------------------------------------------------------------

    def _on_progress(self, percent: float, text: str) -> None:
        try:
            if text:
                self._splash.set_stage(percent, text)
            else:
                self._splash.set_progress(percent)
        except Exception as exc:
            logger.debug("更新闪屏进度失败: %s", exc)

    def _on_ask_user(self, title: str, message: str, options: object) -> None:
        """主线程弹出提示卡片，并把结果回填给后台线程。"""
        pairs = list(options) if options else []
        key = self._safe_ask(title, message, pairs)
        if self._worker is not None:
            self._worker.provide_answer(key)

    def _on_worker_finished(self, _outcome: object) -> None:
        try:
            if self._loop is not None:
                self._loop.quit()
        except Exception as exc:
            logger.debug("结束等待循环失败: %s", exc)

    def _on_skip_clicked(self) -> None:
        """闪屏「跳过非核心模块」被点击。"""
        if self._worker is not None:
            self._worker.request_skip()

    # ------------------------------------------------------------------
    # 超时监控
    # ------------------------------------------------------------------

    def _start_timeout_watch(self) -> None:
        try:
            self._timeout_timer = QTimer()
            self._timeout_timer.setSingleShot(True)
            self._timeout_timer.timeout.connect(self._on_load_timeout)
            self._timeout_timer.start(int(self._load_timeout_s * 1000))
        except Exception as exc:
            logger.warning("启动超时监控不可用: %s", exc)

    def _on_load_timeout(self) -> None:
        if self._finished or self._timeout_notified:
            return
        self._timeout_notified = True
        elapsed = time.monotonic() - self._started_at
        logger.warning("启动耗时已超过 %.0f 秒，提示用户", elapsed)
        choice = self._safe_ask(
            "加载耗时较长",
            f"启动已用时 {int(elapsed)} 秒，可能正在首次初始化本地数据。\n"
            "是否跳过 OCR 预加载、知识库预扫描等非核心步骤，直接进入程序？",
            [("跳过并立即进入", "skip"), ("继续等待", "wait")],
        )
        if choice == "skip":
            if self._worker is not None:
                self._worker.request_skip()
            try:
                self._splash.set_progress(60, "已跳过非核心模块，正在进入主界面…")
            except Exception as exc:
                logger.debug("更新闪屏提示失败: %s", exc)

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _set_stage(self, percent: float, text: Optional[str] = None) -> None:
        try:
            if text:
                self._splash.set_stage(percent, text)
            else:
                self._splash.set_progress(percent)
            self._splash.pump()
        except Exception as exc:
            logger.debug("更新闪屏进度失败: %s", exc)

    def _set_skip_available(self, available: bool) -> None:
        try:
            self._splash.set_skip_available(available)
            self._splash.pump()
        except Exception as exc:
            logger.debug("更新跳过按钮失败: %s", exc)

    def _safe_ask(
        self,
        title: str,
        message: str,
        options: List[Tuple[str, str]],
        default_key: str = "",
    ) -> str:
        try:
            return self._splash.ask_user(title, message, options, default_key)
        except Exception as exc:
            logger.error("提示卡片显示失败（按默认选项继续）: %s", exc)
            return default_key or (options[0][1] if options else "")

    def _finalize(self, outcome: StartupOutcome) -> StartupOutcome:
        self._finished = True
        if self._timeout_timer is not None:
            try:
                self._timeout_timer.stop()
            except Exception:
                pass
        logger.info(
            "启动流程结束: action=%s skipped=%s errors=%s 用时=%.1fs",
            outcome.action,
            outcome.skipped,
            outcome.errors,
            time.monotonic() - self._started_at,
        )
        return outcome


__all__ = [
    "StartupCoordinator",
    "StartupWorker",
    "StartupOutcome",
    "DEFAULT_LOAD_TIMEOUT_S",
    "DEFAULT_OLLAMA_TIMEOUT_S",
    "STAGE_OLLAMA",
    "STAGE_INDEX",
    "STAGE_HARDWARE",
    "STAGE_UI",
    "STAGE_MODEL",
    "STAGE_ENTER",
]

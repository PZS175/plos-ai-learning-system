"""UI 离屏冒烟测试（吸收根目录 gui_init_test.py 的意图）。

- 所有面板可实例化并离屏渲染
- 闪卡复习闭环（启动 -> 评分 -> 自动结束 -> 小结）
- 设置保存的深合并保留未展示小节
- Database 按路径缓存：同路径复用、异路径隔离

说明：不构造完整 MainWindow，以避免测试触达真实 data/ 目录
（ChromaDB、WebEngine 缓存等）；面板级冒烟已覆盖初始化回归。
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Dict

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from plos.core.enums import FlashcardRating  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(["ui_smoke"])
    yield app


@pytest.fixture()
def ui_env(qapp, tmp_path: Path):
    from plos.ai import ModelManager
    from plos.config import load_config
    from plos.db import Database
    from plos.services import (
        ChatService,
        DiagnosticReportService,
        ErrorBookService,
        ExamService,
        FlashcardService,
        LearningPackageService,
        PracticeService,
        RAGService,
        SessionManager,
        StatisticsService,
        StudyPlanService,
        TerminologyService,
        UserService,
    )
    from plos.services.ocr_service import OCRService
    from plos.ui import ui_utils
    from plos.ui.theme_manager import ThemeManager
    from plos.ui.toast import ToastManager

    db = Database(db_path=tmp_path / "ui_smoke.db")
    user_service = UserService(db=db)
    model_manager = ModelManager(config=load_config())
    session_manager = SessionManager(db=db, user_service=user_service)
    errorbook_service = ErrorBookService(db=db, user_service=user_service, model_manager=model_manager)
    flashcard_service = FlashcardService(db=db, user_service=user_service)
    chat_service = ChatService(
        model_manager=model_manager,
        session_manager=session_manager,
        rag_service=None,
        errorbook_service=errorbook_service,
        user_service=user_service,
    )
    rag_service = RAGService(db=db, model_manager=model_manager)
    statistics_service = StatisticsService(db=db, user_service=user_service)
    study_plan_service = StudyPlanService(
        model_manager=model_manager,
        flashcard_service=flashcard_service,
        errorbook_service=errorbook_service,
    )
    exam_service = ExamService(db=db, user_service=user_service, model_manager=model_manager)
    practice_service = PracticeService(
        db=db, user_service=user_service, model_manager=model_manager, errorbook_service=errorbook_service
    )
    terminology_service = TerminologyService(db=db, user_service=user_service, model_manager=model_manager)
    report_service = DiagnosticReportService(db=db, user_service=user_service)
    package_service = LearningPackageService(
        user_service=user_service,
        errorbook_service=errorbook_service,
        flashcard_service=flashcard_service,
        study_plan_service=study_plan_service,
        rag_service=rag_service,
    )
    ocr_service = OCRService(model_manager=model_manager)
    theme_manager = ThemeManager()

    # 注册 ToastManager，防止 show_success 降级为阻塞模态框
    toast_host = QWidget()
    ui_utils.set_toast_manager(ToastManager(toast_host))

    env = {
        "db": db,
        "theme_manager": theme_manager,
        "chat_service": chat_service,
        "errorbook_service": errorbook_service,
        "flashcard_service": flashcard_service,
        "rag_service": rag_service,
        "statistics_service": statistics_service,
        "study_plan_service": study_plan_service,
        "exam_service": exam_service,
        "practice_service": practice_service,
        "terminology_service": terminology_service,
        "report_service": report_service,
        "package_service": package_service,
        "ocr_service": ocr_service,
        "user_service": user_service,
        "model_manager": model_manager,
    }
    yield env

    ui_utils.set_toast_manager(None)
    db.close()


def _render(panel: QWidget, name: str) -> None:
    panel.resize(1200, 800)
    pixmap = panel.grab()
    assert not pixmap.isNull(), f"{name} 渲染失败"


def test_all_panels_instantiate_and_render(ui_env):
    from plos.ui.dashboard_panel import DashboardPanel
    from plos.ui.diagnostic_report_panel import DiagnosticReportPanel
    from plos.ui.errorbook_panel import ErrorBookPanel
    from plos.ui.exam_panel import ExamPanel
    from plos.ui.flashcard_panel import FlashcardPanel
    from plos.ui.knowledge_panel import KnowledgePanel
    from plos.ui.ocr_panel import OCRPanel
    from plos.ui.practice_panel import PracticePanel
    from plos.ui.study_plan_panel import StudyPlanPanel
    from plos.ui.terminology_panel import TerminologyPanel
    from plos.ui.user_panel import UserPanel

    panels = {
        "FlashcardPanel": FlashcardPanel(ui_env["flashcard_service"]),
        "UserPanel": UserPanel(ui_env["user_service"]),
        "StudyPlanPanel": StudyPlanPanel(ui_env["study_plan_service"], ui_env["flashcard_service"]),
        "TerminologyPanel": TerminologyPanel(ui_env["terminology_service"]),
        "ErrorBookPanel": ErrorBookPanel(ui_env["errorbook_service"], ui_env["flashcard_service"]),
        "ExamPanel": ExamPanel(ui_env["exam_service"], ui_env["errorbook_service"]),
        "PracticePanel": PracticePanel(ui_env["practice_service"], ui_env["errorbook_service"]),
        "KnowledgePanel": KnowledgePanel(ui_env["rag_service"]),
        "ChatPanel": __import__("plos.ui.chat_panel", fromlist=["x"]).ChatPanel(
            ui_env["chat_service"], ui_env["rag_service"], ui_env["errorbook_service"], config={"chat": {}}
        ),
        "OCRPanel": OCRPanel(
            ui_env["ocr_service"], ui_env["errorbook_service"], ui_env["terminology_service"]
        ),
        "DiagnosticReportPanel": DiagnosticReportPanel(ui_env["report_service"]),
        "DashboardPanel": DashboardPanel(
            ui_env["chat_service"],
            ui_env["errorbook_service"],
            ui_env["flashcard_service"],
            ui_env["rag_service"],
            ui_env["study_plan_service"],
            ui_env["statistics_service"],
            theme_manager=ui_env["theme_manager"],
        ),
    }
    for name, panel in panels.items():
        _render(panel, name)


def test_flashcard_review_loop(ui_env):
    from plos.ui.flashcard_panel import FlashcardPanel

    service = ui_env["flashcard_service"]
    panel = FlashcardPanel(service)
    service.add_card(front_content="1+1=?", back_content="2", subject="数学")
    panel.refresh_data()

    panel._start_review()
    assert panel._review_card_id is not None, "复习队列未启动"
    assert "1" in panel.review_progress_label.text(), "剩余数未显示"

    panel._rate_card(FlashcardRating.GOOD)
    assert panel._review_card_id is None, "评分后未清空当前卡"
    assert panel._review_queue == [], "评分后队列未清空"
    assert "1" in panel.review_progress_label.text(), "小结未显示本轮完成数"


def test_flashcard_skip_moves_card_to_end(ui_env):
    from plos.ui.flashcard_panel import FlashcardPanel

    service = ui_env["flashcard_service"]
    panel = FlashcardPanel(service)
    service.add_card(front_content="Q1", back_content="A1", subject="数学")
    service.add_card(front_content="Q2", back_content="A2", subject="数学")
    panel.refresh_data()
    panel._start_review()

    first_id = panel._review_card_id
    panel._skip_current_card()
    assert len(panel._review_queue) == 2, "跳过后队列长度应保持不变"
    assert panel._review_queue[-1]["id"] == first_id, "跳过的卡应排到队尾"
    assert panel._review_card_id != first_id, "跳过后应展示下一张"


def test_settings_deep_merge_preserves_unshown_sections(ui_env):
    from plos.ui.settings_panel import SettingsPanel, _deep_merge

    panel = SettingsPanel(
        config={
            "cloud": {"timeout": 42},
            "storage": {"attachments_dir_name": "x"},
            "ocr": {"use_vlm": False},
        },
        model_manager=ui_env["model_manager"],
        theme_manager=ui_env["theme_manager"],
    )
    _render(panel, "SettingsPanel")

    merged = copy.deepcopy(panel.config)
    _deep_merge(merged, panel._gather_config())
    assert merged["storage"]["attachments_dir_name"] == "x", "storage 小节丢失"
    assert merged["ocr"]["use_vlm"] is False, "ocr 小节丢失"
    assert merged["cloud"]["timeout"] == 42, "cloud.timeout 未保留"


def test_vl_degradation_disables_solve_button(ui_env, tmp_path: Path):
    """VL 模型不可用时，OCR 面板的拍照搜题按钮应被禁用且不崩溃。"""
    from plos.ai import ModelManager
    from plos.config import load_config
    from plos.services import InputRouter, OCRService
    from plos.ui.ocr_panel import OCRPanel

    config = load_config()
    config["models"]["vision_model"] = "non-existent-vl-model"
    manager = ModelManager(config, hardware=None)
    assert not manager.is_vision_available(), "VL 模型应不可用（降级场景）"

    ocr_service = OCRService(manager)
    panel = OCRPanel(InputRouter(ocr_service))
    _render(panel, "OCRPanel(VL降级)")
    assert not panel.btn_solve.isEnabled(), "VL 不可用时拍照搜题按钮应被禁用"


def test_study_heatmap_renders(ui_env):
    """学习热力图可离屏渲染（防 QPainterPath 崩溃回归：进程存活即通过）。"""
    from plos.ui.study_heatmap import StudyHeatMapWidget

    heatmap = StudyHeatMapWidget()
    heatmap.resize(900, 140)
    heatmap.set_data(
        [{"date": "2026-09-0%d" % d, "minutes": 30} for d in range(1, 6)]
    )
    pixmap = heatmap.grab()
    assert not pixmap.isNull()


def test_dashboard_weekly_and_goal_widgets(ui_env):
    """仪表盘集成：周报文本与每日目标进度随数据刷新。"""
    from plos.ui.dashboard_panel import DashboardPanel

    db = ui_env["db"]
    uid = ui_env["user_service"].get_current_user_id()
    db.execute(
        "INSERT INTO statistics_records (user_id, record_type, record_date, value) "
        "VALUES (?, 'study_duration', date('now','localtime'), 45)",
        (uid,),
    )

    panel = DashboardPanel(
        ui_env["chat_service"],
        ui_env["errorbook_service"],
        ui_env["flashcard_service"],
        ui_env["rag_service"],
        ui_env["study_plan_service"],
        ui_env["statistics_service"],
        theme_manager=ui_env["theme_manager"],
    )
    panel.refresh_data()
    assert "专注 45 分钟" in panel.weekly_label.text()
    assert "今日 45/" in panel.goal_label.text()
    assert panel.goal_bar.value() > 0


def test_settings_daily_goal_roundtrip(ui_env):
    """设置面板：每日学习目标可收集、可回读。"""
    from plos.ui.settings_panel import SettingsPanel

    config = {"ui": {"theme": "dark", "daily_goal_minutes": 45}}
    panel = SettingsPanel(config, ui_env["model_manager"], ui_env["theme_manager"])
    assert panel.daily_goal_spin.value() == 45
    gathered = panel._gather_config()
    assert gathered["ui"]["daily_goal_minutes"] == 45


def test_database_path_cache(tmp_path: Path):
    from plos.db import Database

    db1 = Database(db_path=tmp_path / "cache_a.db")
    db1_again = Database(db_path=tmp_path / "cache_a.db")
    db2 = Database(db_path=tmp_path / "cache_b.db")
    assert db1_again is db1, "同一路径应复用实例"
    assert db2 is not db1, "不同路径应产生不同实例"
    db1.close()
    db2.close()


def test_theme_tokens_available():
    from plos.ui.ui_utils import theme_colors

    colors: Dict[str, str] = theme_colors()
    for token in ("fg_primary", "fg_secondary", "accent", "success", "warning", "error",
                  "info", "teal", "purple", "border", "card_bg", "accent_border",
                  "chart_1", "chart_2", "chart_3", "chart_4", "chart_5", "chart_6"):
        assert token in colors, f"缺少主题令牌 {token}"

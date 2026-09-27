"""渲染双主题 UI 预览截图（真实平台离屏抓图，不依赖 Ollama，不碰真实数据）。

用法：
    python tools/render_ui_preview.py [输出目录]

为每个核心面板与完整主窗口在暗色/亮色两套 Aurora 主题下各生成一张 PNG，
保存到 docs/ui_preview/，供视觉自检与设计走查。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "ui_preview"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 数据库重定向：必须在导入业务模块前打补丁，让 get_db() 指向临时库
TMP = Path(tempfile.mkdtemp())
import plos.db.database as _dbmod  # noqa: E402

_dbmod.get_db_path = lambda: TMP / "preview.db"
print("[stage] db patched", flush=True)

from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

app = QApplication(["ui_preview"])
print("[stage] qapp", flush=True)
from PyQt6.QtCore import QLocale  # noqa: E402

QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.China))  # 修复 zh_HK 数字乱码

from plos.ai import ModelManager  # noqa: E402
from plos.config import load_config  # noqa: E402
from plos.db import Database  # noqa: E402
from plos.services import (  # noqa: E402
    ChatService,
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
from plos.services.ocr_service import OCRService  # noqa: E402
from plos.ui import ui_utils  # noqa: E402
from plos.ui.theme_manager import ThemeManager  # noqa: E402
from plos.ui.toast import ToastManager  # noqa: E402

print("[stage] services importing…", flush=True)
db = Database(db_path=TMP / "preview.db")
user_service = UserService(db=db)
uid = user_service.get_current_user_id()
model_manager = ModelManager(config=load_config())
session_manager = SessionManager(db=db, user_service=user_service)
errorbook_service = ErrorBookService(db=db, user_service=user_service, model_manager=model_manager)
flashcard_service = FlashcardService(db=db, user_service=user_service)
chat_service = ChatService(model_manager=model_manager, session_manager=session_manager,
                           rag_service=None, errorbook_service=errorbook_service, user_service=user_service)
rag_service = RAGService(db=db, model_manager=model_manager)
statistics_service = StatisticsService(db=db, user_service=user_service)
study_plan_service = StudyPlanService(model_manager=model_manager, flashcard_service=flashcard_service,
                                      errorbook_service=errorbook_service)
exam_service = ExamService(db=db, user_service=user_service, model_manager=model_manager)
practice_service = PracticeService(db=db, user_service=user_service, model_manager=model_manager,
                                   errorbook_service=errorbook_service)
terminology_service = TerminologyService(db=db, user_service=user_service, model_manager=model_manager)
package_service = LearningPackageService(user_service=user_service, errorbook_service=errorbook_service,
                                         flashcard_service=flashcard_service, study_plan_service=study_plan_service,
                                         rag_service=rag_service)
ocr_service = OCRService(model_manager=model_manager)
print("[stage] managers", flush=True)
theme_manager = ThemeManager(initial_theme="dark")
ui_utils.set_theme_manager(theme_manager)  # 关键：让 theme_colors() 与全局 QSS 同源

print("[stage] seeding", flush=True)
toast_host = QWidget()
ui_utils.set_toast_manager(ToastManager(toast_host))

# 预置演示数据（截图里看得见内容，而不是全空态）
for i, q in enumerate([
    "已知函数 f(x) = x² - 2x + 1，求 f'(x) 在 x=1 处的值。",
    "一元二次方程 x² - 5x + 6 = 0 的两根之和为多少？",
    "简述牛顿第二定律的内容及其数学表达式。",
    ("计算定积分 ∫₀¹ 2x dx 的值。"),
]):
    errorbook_service.add_error(question=q, answer="见解析", analysis="解析内容", subject="数学",
                                knowledge_tags="数学", mastery_level=i % 3)
for i in range(6):
    flashcard_service.add_card(front_content=f"概念卡片 {i+1}：什么是导数？", back_content="导数是变化率……",
                               subject="数学", tags="微积分")

# 近半年学习时长种子：随机但保证连续天数（热力图与 streak 可见）
import random
from datetime import datetime, timedelta

random.seed(42)
_today = datetime.now()
for _i in range(180):
    _day = _today - timedelta(days=_i)
    if _i < 12 or random.random() < 0.6:  # 最近 12 天连续，之前 60% 概率有学习
        db.execute(
            "INSERT INTO statistics_records (user_id, record_type, record_date, value) "
            "VALUES (?, 'study_duration', ?, ?)",
            (uid, _day.strftime("%Y-%m-%d"), random.choice([12, 20, 35, 50, 75, 100])),
        )


print("[stage] building panels", flush=True)

def build_panels() -> dict:
    from plos.ui.dashboard_panel import DashboardPanel
    from plos.ui.errorbook_panel import ErrorBookPanel
    from plos.ui.exam_panel import ExamPanel
    from plos.ui.flashcard_panel import FlashcardPanel
    from plos.ui.knowledge_panel import KnowledgePanel
    from plos.ui.ocr_panel import OCRPanel
    from plos.ui.practice_panel import PracticePanel
    from plos.ui.settings_panel import SettingsPanel
    from plos.ui.study_plan_panel import StudyPlanPanel
    from plos.ui.terminology_panel import TerminologyPanel
    import plos.ui.chat_panel as chat_mod

    return {
        "dashboard": lambda: DashboardPanel(chat_service, errorbook_service, flashcard_service,
                                            rag_service, study_plan_service, statistics_service,
                                            theme_manager=theme_manager),
        "chat": lambda: chat_mod.ChatPanel(chat_service, rag_service=rag_service,
                                           errorbook_service=errorbook_service, config={"chat": {}}),
        "errorbook": lambda: ErrorBookPanel(errorbook_service, flashcard_service=flashcard_service),
        "flashcard": lambda: FlashcardPanel(flashcard_service),
        "knowledge": lambda: KnowledgePanel(rag_service),
        "practice": lambda: PracticePanel(practice_service, errorbook_service=errorbook_service),
        "exam": lambda: ExamPanel(exam_service, errorbook_service=errorbook_service),
        "study_plan": lambda: StudyPlanPanel(study_plan_service, flashcard_service=flashcard_service),
        "terminology": lambda: TerminologyPanel(terminology_service),
        "settings": lambda: SettingsPanel(load_config(), model_manager, theme_manager),
        "ocr": lambda: OCRPanel(ocr_service, errorbook_service=errorbook_service,
                                terminology_service=terminology_service),
    }


def build_main_window():
    """构造完整主窗口（规避后端检查模态框）。"""
    from plos.ui.main_window import MainWindow

    model_manager.is_text_available = lambda: True  # 跳过 Ollama 检查弹窗
    return MainWindow(config=load_config(), model_manager=model_manager, theme_manager=theme_manager)


PANELS = build_panels()
EXTRA = {"main_window": build_main_window}

print("[stage] rendering", flush=True)

for theme in ("dark", "light"):
    theme_manager.set_theme(theme, app)
    theme_manager.apply(app)  # set_theme 同值时是 no-op，首轮需显式应用全局 QSS
    jobs: dict = dict(PANELS)
    jobs.update(EXTRA)
    for name, factory in jobs.items():
        try:
            print(f"[stage] constructing {name}", flush=True)
            widget = factory()
            print(f"[stage] constructed {name}", flush=True)
            height = 1250 if name in ("dashboard", "main_window") else 800
            widget.resize(1280, height)
            pixmap = widget.grab()
            print(f"[stage] grabbed {name}", flush=True)
            out = OUT_DIR / f"{theme}_{name}.png"
            pixmap.save(str(out))
            print(f"[OK] {out.name}")
            if name == "dashboard" and hasattr(widget, "heatmap"):
                # 直接从整图裁剪热力卡区域（单独 grab 子控件会触发进程异常退出）
                from PyQt6.QtCore import QPoint

                card = widget.heatmap.parentWidget()
                top_left = card.mapTo(widget, QPoint(0, 0))
                dpr = pixmap.devicePixelRatio()  # 高 DPI：位图为物理像素
                crop = pixmap.copy(
                    int(top_left.x() * dpr), int(top_left.y() * dpr),
                    int(card.width() * dpr), int(card.height() * dpr),
                )
                crop.setDevicePixelRatio(1.0)
                crop.save(str(OUT_DIR / f"{theme}_heatmap.png"))
                print(f"[OK] {theme}_heatmap.png")
            widget.deleteLater()
        except Exception as e:
            print(f"[FAIL] {theme}/{name}: {e}")

# 主题切换模拟：亮色下构造主窗口 → 运行时切到暗色 → 截图，验证无"烘焙残影"
try:
    theme_manager.set_theme("light", app)
    theme_manager.apply(app)
    mw = build_main_window()
    mw.resize(1280, 1250)
    mw.grab().save(str(OUT_DIR / "light_main_window.png"))
    print("[OK] light_main_window.png (重建)")
    theme_manager.set_theme("dark", app)  # 运行时切换，触发 _on_theme_changed 广播
    mw.repaint()
    mw.grab().save(str(OUT_DIR / "switched_main_window.png"))
    print("[OK] switched_main_window.png (亮→暗运行时切换)")
    mw.deleteLater()
except Exception as e:
    print(f"[FAIL] theme-switch simulation: {e}")

db.close()
print(f"\n预览图已输出到 {OUT_DIR}")

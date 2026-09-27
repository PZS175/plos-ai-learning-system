"""启动冒烟测试（迁移自根目录 smoke_test.py）。

不显示 GUI：验证核心模块可导入、临时数据库可连接、服务可初始化、
教学模式 prompt 注入正确。原脚本连接真实用户库，现改用临时数据库。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

from plos.ai import ModelManager  # noqa: E402
from plos.config import load_config  # noqa: E402
from plos.db import Database, get_db  # noqa: E402
from plos.services import (  # noqa: E402
    ChatService,
    ErrorBookService,
    FlashcardService,
    RAGService,
    StatisticsService,
    StudyPlanService,
    UserService,
)
from plos.services.session_manager import SessionManager  # noqa: E402


@pytest.fixture()
def smoke_env(tmp_path: Path):
    db = Database(db_path=tmp_path / "smoke.db")
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
    yield {
        "db": db,
        "user_service": user_service,
        "errorbook_service": errorbook_service,
        "flashcard_service": flashcard_service,
        "chat_service": chat_service,
        "rag_service": rag_service,
        "statistics_service": statistics_service,
        "study_plan_service": study_plan_service,
    }
    db.close()


def test_core_services_initialize(smoke_env):
    """所有核心服务能在临时库上完成初始化。"""
    assert get_db() is not None  # 默认路径单例仍可用
    for name in (
        "chat_service",
        "rag_service",
        "statistics_service",
        "study_plan_service",
        "errorbook_service",
        "flashcard_service",
    ):
        assert smoke_env[name] is not None, f"{name} 初始化失败"


def test_dashboard_summary_interfaces(smoke_env):
    """仪表盘/错题/闪卡的数据接口不抛异常。"""
    assert isinstance(smoke_env["statistics_service"].get_dashboard_summary(), dict)
    assert isinstance(smoke_env["errorbook_service"].get_weak_knowledge_points(top_n=5), list)
    assert isinstance(smoke_env["flashcard_service"].get_stats(), dict)


def test_teaching_mode_prompt_injection(smoke_env):
    """教学模式和输出约束 prompt 注入正确。"""
    chat_service = smoke_env["chat_service"]
    conv_id = chat_service.create_conversation(title="smoke_test")

    msgs_normal = chat_service._build_messages(
        conv_id, "测试", teaching_mode="normal", no_direct_answer=False
    )
    system_normal = [m.content for m in msgs_normal if m.role.value == "system"]
    # 基础讲解模式下注入讲解版 system prompt，而非苏格拉底式
    assert any("讲解模式" in c for c in system_normal)
    assert all(chat_service.SYSTEM_PROMPT_SOCRATIC != c for c in system_normal)

    msgs_socratic = chat_service._build_messages(
        conv_id, "测试", teaching_mode="socratic", no_direct_answer=True
    )
    system_socratic = [m.content for m in msgs_socratic if m.role.value == "system"]
    assert any(chat_service.SYSTEM_PROMPT_SOCRATIC in c for c in system_socratic)
    assert any(chat_service.NO_DIRECT_ANSWER_CONSTRAINT in c for c in system_socratic)


def test_rag_similarity_stays_in_user_facing_range():
    """回归：知识库检索的「相关度」必须是 0~1 之间的数。

    向量集合用的是 Chroma 默认 L2 距离，而 nomic-embed-text 返回的向量不是
    单位向量：实测同一份讲义的距离有 317.5，沿用 ``1 - distance`` 会让界面
    显示「相关度：-317.517」，等于把唯一命中的那一段标成最不相关。
    """
    from plos.services.rag_service import _similarity

    assert _similarity([1.0, 0.0], [1.0, 0.0], 5.0) == pytest.approx(1.0)
    assert _similarity([1.0, 0.0], [0.0, 1.0], 5.0) == pytest.approx(0.0)

    # 拿不到向量时退化为单调压缩：既不能是负数，也不能越界
    fallback = _similarity([1.0, 0.0], None, 317.5)
    assert 0.0 < fallback < 1.0

    # 向量维度对不上（换过嵌入模型）时同样要给出合法分数
    mismatch = _similarity([1.0, 0.0], [1.0], 12.0)
    assert 0.0 < mismatch < 1.0


def test_auto_tag_prompt_requires_chinese():
    """错题打标的 prompt 必须明确要求中文。

    否则小模型会把知识点写成 function / minimization 这类英文标识，
    直接显示在错题本与薄弱知识点列表里。
    """
    from plos.services.errorbook_service import _AUTO_TAG_PROMPT

    assert "中文" in _AUTO_TAG_PROMPT
    assert "{" in _AUTO_TAG_PROMPT and "text" in _AUTO_TAG_PROMPT, "占位符不能丢"


def test_resolve_ollama_models_matches_model_size():
    """回归：高配机器必须挑到 7B，而不是列表里先出现的 3B。

    同系列模型的 base 名一样（qwen2.5:3b 与 qwen2.5:7b 的 base 都是 qwen2.5），
    只比 base 名会先撞上靠前的 3B，于是「硬件推荐 7b 套装」实际跑的是 3B，
    问答与出题质量明显下降。
    """
    from plos.core.models import HardwareInfo
    from plos.utils.startup_probe import _pick_installed_model, resolve_ollama_models

    installed = [
        "qwen2.5-coder:7b",
        "qwen2.5:3b-instruct-q4_K_M",
        "qwen2.5:7b-instruct-q4_k_m",
        "qwen2.5vl:3b",
        "qwen2.5vl:3b-q4_K_M",
        "qwen2.5vl:7b-q4_K_M",
        "nomic-embed-text:latest",
    ]
    assert _pick_installed_model(installed, "qwen2.5:7b-q4_K_M") == "qwen2.5:7b-instruct-q4_k_m"
    assert (
        _pick_installed_model(installed, "qwen2.5:3b-instruct-q4_K_M")
        == "qwen2.5:3b-instruct-q4_K_M"
    )
    assert (
        _pick_installed_model(
            installed, "qwen2.5-vl:7b-q4_K_M", extra_prefixes=("qwen2.5vl", "qwen2.5-vl")
        )
        == "qwen2.5vl:7b-q4_K_M"
    )

    # 机器上只有 3B 时退回同系列，不能变成「找不到模型」
    only_3b = ["qwen2.5:3b-instruct-q4_K_M"]
    assert _pick_installed_model(only_3b, "qwen2.5:7b-q4_K_M") == "qwen2.5:3b-instruct-q4_K_M"

    high_end = HardwareInfo(
        os_name="Windows", cpu_model="test-cpu", cpu_cores=16, ram_total_gb=32.0
    )
    resolved = resolve_ollama_models(installed, high_end)
    assert resolved["profile"] == "7b"
    assert resolved["text_model"] == "qwen2.5:7b-instruct-q4_k_m"
    assert resolved["vision_model"] == "qwen2.5vl:7b-q4_K_M"
    assert resolved["vl_available"] is True

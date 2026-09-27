"""教学风格精细配置服务测试。

验证：
1. 多维教学风格配置读写数据库
2. 未启用时提示词为空
3. 启用后提示词包含对应风格关键词
4. ChatService 构建消息时注入风格提示词
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

from plos.core.enums import Role
from plos.core.models import InferenceResponse
from plos.services import ChatService, TeachingStyleService
from plos.services.session_manager import SessionManager
from plos.services.user_service import UserService


class _MockUserService(UserService):
    def __init__(self):
        pass

    def get_current_user_id(self) -> int:
        return 0


class _MockSessionManager(SessionManager):
    def __init__(self):
        pass

    def get_messages(self, conversation_id: int, user_id: Optional[int] = None, limit: int = 100):
        return []

    def add_message(
        self,
        conversation_id: int,
        role: Role,
        content: str,
        user_id: Optional[int] = None,
    ) -> int:
        return 1

    def create_conversation(self, title: str = "新对话", user_id: Optional[int] = None) -> int:
        return 1


class _MockModelManager:
    def is_text_available(self) -> bool:
        return True

    def chat(self, messages, **kwargs):
        return InferenceResponse(content="ok", model="mock")

    @property
    def backend_type(self):
        from plos.core.enums import InferenceBackend
        return InferenceBackend.OLLAMA


class _MockDatabase:
    """模拟支持 teaching_style_config 表操作的内存数据库。"""

    def __init__(self):
        self._rows: Dict[int, Dict[str, Any]] = {}

    def fetchone(self, query: str, parameters: tuple = ()) -> Optional[Dict[str, Any]]:
        if "teaching_style_config" not in query:
            return None
        user_id = parameters[0]
        row = self._rows.get(user_id)
        if row is None:
            return None
        return {
            "id": row["id"],
            "style": row["style"],
            "enabled": row["enabled"],
            "config_json": row["config_json"],
        }

    def execute(self, query: str, parameters: tuple = ()) -> Any:
        if "UPDATE" in query:
            user_id = parameters[-1]
            self._rows[user_id]["style"] = parameters[0]
            self._rows[user_id]["enabled"] = parameters[1]
            self._rows[user_id]["config_json"] = parameters[2]
        return MagicMock()

    def insert(self, query: str, parameters: tuple = ()) -> int:
        user_id = parameters[0]
        self._rows[user_id] = {
            "id": len(self._rows) + 1,
            "user_id": user_id,
            "style": parameters[1],
            "enabled": parameters[2],
            "config_json": parameters[3],
        }
        return self._rows[user_id]["id"]


def test_teaching_style_service():
    db = _MockDatabase()
    user_service = _MockUserService()
    service = TeachingStyleService(db=db, user_service=user_service)

    # 默认未启用
    cfg = service.get_config()
    assert cfg["enabled"] is False
    assert cfg["style"] == "encouraging"
    assert cfg["explanation_depth"] == "moderate"
    assert service.get_prompt() == ""

    # 启用严格风格 + 详细 + 代码示例
    service.save_config(
        {
            "style": "strict",
            "explanation_depth": "detailed",
            "example_style": "code",
            "tone": "formal",
            "question_style": "challenge",
            "use_local_language": True,
            "avoid_direct_answer": True,
            "focus_on_common_mistakes": True,
        },
        True,
    )
    cfg = service.get_config()
    assert cfg["enabled"] is True
    assert cfg["style"] == "strict"
    assert cfg["explanation_depth"] == "detailed"
    assert cfg["example_style"] == "code"

    prompt = service.get_prompt()
    assert "严格" in prompt
    assert "推导" in prompt or "边界条件" in prompt
    assert "代码" in prompt or "伪代码" in prompt
    assert "独立思考" in prompt
    assert "常见错误" in prompt

    # 禁用后提示词为空
    service.save_config({"style": "encouraging"}, False)
    assert service.get_prompt() == ""

    print("test_teaching_style_service passed")


def test_chat_service_injects_style():
    db = _MockDatabase()
    user_service = _MockUserService()
    style_service = TeachingStyleService(db=db, user_service=user_service)
    style_service.save_config({"style": "strict"}, True)

    chat_service = ChatService(
        model_manager=_MockModelManager(),
        session_manager=_MockSessionManager(),
        teaching_style_service=style_service,
    )

    messages = chat_service._build_messages(
        conversation_id=1,
        user_text="你好",
        user_id=0,
    )
    system_contents = [m.content for m in messages if m.role == Role.SYSTEM]
    combined = "\n".join(system_contents)
    assert "严格" in combined

    print("test_chat_service_injects_style passed")


def test_config_json_persistence():
    db = _MockDatabase()
    service = TeachingStyleService(db=db)
    config = {
        "style": "socratic",
        "explanation_depth": "brief",
        "example_style": "analogy",
        "tone": "enthusiastic",
        "question_style": "confirm",
        "use_local_language": False,
        "avoid_direct_answer": True,
        "focus_on_common_mistakes": False,
    }
    service.save_config(config, True)
    stored = db._rows[0]
    parsed = json.loads(stored["config_json"])
    assert parsed["style"] == "socratic"
    assert parsed["example_style"] == "analogy"
    assert parsed["use_local_language"] is False

    print("test_config_json_persistence passed")


if __name__ == "__main__":
    test_teaching_style_service()
    test_chat_service_injects_style()
    test_config_json_persistence()
    print("All teaching style tests passed")

"""知识点讲解模式切换测试。

验证：
1. 不同 explanation_mode 会传入对应的 system prompt
2. 启发教学模式下讲解模式不覆盖
3. 禁止直接给答案约束会追加到 system prompt
"""

from __future__ import annotations

from plos.ai import ModelManager
from plos.core.enums import InferenceBackend, Role
from plos.core.models import ChatMessage, InferenceResponse
from plos.services import ChatService, SessionManager


class _MockModelManager(ModelManager):
    """记录最后一次 messages 的模拟模型管理器。"""

    def __init__(self):
        self.backend_type = InferenceBackend.OLLAMA
        self.last_messages = None

    def is_text_available(self) -> bool:
        return True

    def chat(self, messages, **kwargs):
        self.last_messages = messages
        return InferenceResponse(content="ok", model="mock")


class _MockSessionManager(SessionManager):
    """不依赖数据库的会话管理器。"""

    def __init__(self):
        self._messages = []

    def add_message(self, conversation_id: int, role: Role, content: str, **kwargs):
        self._messages.append(ChatMessage(role=role, content=content))

    def get_messages(self, conversation_id: int, **kwargs):
        return list(self._messages)

    def create_conversation(self, **kwargs) -> int:
        return 1


def _find_system_prompt(messages):
    for m in messages:
        if m.role == Role.SYSTEM and "PLOS AI" in m.content:
            return m.content
    return ""


def test_explanation_modes():
    model = _MockModelManager()
    session = _MockSessionManager()
    chat_service = ChatService(model, session)

    for mode, keyword in [
        ("basic", "基础版讲解模式"),
        ("advanced", "进阶版讲解模式"),
        ("exam", "应试版讲解模式"),
        ("research", "科研深度版讲解模式"),
    ]:
        chat_service.send_message(
            1, "讲解牛顿第一定律", explanation_mode=mode
        )
        prompt = _find_system_prompt(model.last_messages)
        assert keyword in prompt, f"mode={mode} 应包含 {keyword}"

    # 启发教学模式下，讲解模式不生效
    chat_service.send_message(
        1, "讲解牛顿第一定律", teaching_mode="socratic", explanation_mode="exam"
    )
    prompt = _find_system_prompt(model.last_messages)
    assert "启发教学模式" in prompt
    assert "应试版讲解模式" not in prompt

    # 禁止直接给答案约束
    chat_service.send_message(
        1, "讲解牛顿第一定律", explanation_mode="basic", no_direct_answer=True
    )
    prompt = _find_system_prompt(model.last_messages)
    assert "禁止直接给出答案" in prompt

    print("test_explanation_modes passed")


if __name__ == "__main__":
    test_explanation_modes()

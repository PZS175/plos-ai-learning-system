"""主观题批改与错题本关联测试。

不依赖真实模型，使用 Mock ModelManager 验证：
1. 提交主观题答案后自动写入错题本
2. 主观题批改记录（得分点、失分点等）可查询
3. 错题本弹窗查询接口返回结构化数据
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from plos.ai import ModelManager
from plos.core.enums import Role
from plos.core.models import ChatMessage
from plos.db import Database
from plos.services import ErrorBookService, PracticeService, UserService


class _MockModelManager(ModelManager):
    """模拟模型管理器，固定返回题目/批改 JSON。"""

    def __init__(self):
        # 不调用父类，避免加载真实配置
        pass

    def is_text_available(self) -> bool:
        return True

    def chat(self, messages, max_tokens: int = 2048, **kwargs):
        content = messages[-1].content if messages else ""
        if "出题老师" in content:
            return ChatMessage(
                role=Role.ASSISTANT,
                content=json.dumps({
                    "question": "简述牛顿第一定律的内容。",
                    "options": [],
                    "answer": "一切物体在没有受到外力作用时，总保持静止或匀速直线运动状态。",
                    "analysis": "牛顿第一定律又称惯性定律，强调力是改变物体运动状态的原因。",
                }, ensure_ascii=False),
            )
        if "阅卷老师" in content:
            return ChatMessage(
                role=Role.ASSISTANT,
                content=json.dumps({
                    "is_correct": False,
                    "score": 45,
                    "scoring_points": ["提到了外力作用"],
                    "lost_points": ["未说明匀速直线运动", "未说明静止状态"],
                    "error_reasons": ["对定律表述不完整"],
                    "improvement": "建议背诵标准表述，注意关键词：静止、匀速直线运动、外力。",
                    "feedback": "回答不完整，需要补充完整表述。",
                }, ensure_ascii=False),
            )
        return ChatMessage(role=Role.ASSISTANT, content="{}")


class _MockUserService(UserService):
    def get_current_user_id(self) -> int:
        return 0


def test_subjective_grade_sync():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_subjective.db"
        db = Database(db_path=db_path)
        user_service = _MockUserService()
        errorbook_service = ErrorBookService(db=db, user_service=user_service)
        practice_service = PracticeService(
            db=db,
            user_service=user_service,
            model_manager=_MockModelManager(),
            errorbook_service=errorbook_service,
        )

        # 生成一道主观题
        question = practice_service.generate_question(
            knowledge_point="牛顿第一定律",
            question_type="short",
            difficulty=3,
            user_id=0,
        )
        assert question["id"] > 0

        # 提交不完整答案
        result = practice_service.submit_answer(
            question_id=question["id"],
            user_answer="物体在没有外力作用时会保持原来的运动状态。",
            user_id=0,
        )
        assert result["score"] == 45
        assert result["scoring_points"] == ["提到了外力作用"]
        assert result["lost_points"]

        # 验证错题本中新增记录
        errors = errorbook_service.list_errors(user_id=0)
        assert len(errors) == 1
        error_id = errors[0]["id"]

        # 验证主观题批改记录已关联
        grades = errorbook_service.list_subjective_grades(error_id, user_id=0)
        assert len(grades) == 1
        grade = grades[0]
        assert grade["score"] == 45
        assert "提到了外力作用" in grade["scoring_points"]
        assert "未说明匀速直线运动" in grade["lost_points"]
        assert "建议背诵标准表述" in grade["improvement"]

        # 关闭数据库连接，避免 Windows 临时目录清理失败
        if db._connection is not None:
            db._connection.close()
            db._connection = None

        print("test_subjective_grade_sync passed")


if __name__ == "__main__":
    test_subjective_grade_sync()

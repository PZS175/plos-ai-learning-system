"""术语词典服务测试。

验证：
1. 从文本中提取术语并存入数据库
2. 列出、搜索、更新释义、删除术语
3. 重复术语不会重复添加
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from plos.core.models import InferenceResponse
from plos.db import Database
from plos.services import TerminologyService
from plos.services.user_service import UserService


class _MockUserService(UserService):
    def get_current_user_id(self) -> int:
        return 0


class _MockModelManager:
    """模拟模型管理器，返回固定术语 JSON。"""

    def is_text_available(self) -> bool:
        return True

    def chat(self, messages, **kwargs):
        return InferenceResponse(
            content='[{"term": "导数", "definition": "函数变化率"}, '
                    '{"term": "极限", "definition": "趋近值"}]',
            model="mock",
        )


def test_terminology_service():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_terms.db"
        db = Database(db_path=db_path)
        user_service = _MockUserService()
        service = TerminologyService(
            db=db, user_service=user_service, model_manager=_MockModelManager()
        )

        text = "导数和极限是微积分的基础概念。"
        terms = service.extract_terms(text, source_type="test", source_id=1, user_id=0)
        assert len(terms) == 2
        assert any(t["term"] == "导数" for t in terms)
        assert any(t["term"] == "极限" for t in terms)

        # 重复提取不会新增
        terms2 = service.extract_terms(text, source_type="test", source_id=1, user_id=0)
        assert len(terms2) == 0

        # 搜索
        found = service.search_terms("导数", user_id=0)
        assert len(found) == 1

        # 更新释义
        term_id = found[0]["id"]
        service.update_definition(term_id, "函数在某点的瞬时变化率", user_id=0)
        updated = service.get_term(term_id, user_id=0)
        assert "瞬时变化率" in updated["definition"]

        # 删除
        service.delete_term(term_id, user_id=0)
        assert service.get_term(term_id, user_id=0) is None

        # 关闭数据库连接
        if db._connection is not None:
            db._connection.close()
            db._connection = None

        print("test_terminology_service passed")


if __name__ == "__main__":
    test_terminology_service()

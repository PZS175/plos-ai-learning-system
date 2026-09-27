"""低优先级功能预留数据表测试。

验证 schema 初始化/迁移后，ASR、示意图、学习包、教学风格配置表已创建。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from plos.db import Database


_EXPECTED_TABLES = [
    "asr_records",
    "diagram_records",
    "learning_packages",
    "teaching_style_config",
]


def test_low_priority_tables():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_low_priority.db"
        db = Database(db_path=db_path)

        rows = db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        table_names = {r["name"] for r in rows}

        for table in _EXPECTED_TABLES:
            assert table in table_names, f"缺少预留表：{table}"

        # 关闭数据库连接
        if db._connection is not None:
            db._connection.close()
            db._connection = None

        print("test_low_priority_tables passed")


if __name__ == "__main__":
    test_low_priority_tables()

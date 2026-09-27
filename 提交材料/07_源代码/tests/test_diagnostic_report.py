"""学习诊断报告服务测试。

验证：
1. 生成报告包含概览、牢固知识点、盲区、高频错误类型、建议
2. Markdown 渲染包含必要章节
3. 导出文件可正常写入
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from plos.db import Database
from plos.services import DiagnosticReportService, ErrorBookService
from plos.services.user_service import UserService


class _MockUserService(UserService):
    def get_current_user_id(self) -> int:
        return 0


def test_diagnostic_report():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_report.db"
        db = Database(db_path=db_path)
        # 连接必须在 finally 中释放：否则断言失败时临时目录清理会抛
        # WinError 32，把真正的断言错误盖掉，导致排障方向完全跑偏。
        try:
            user_service = _MockUserService()
            errorbook_service = ErrorBookService(db=db, user_service=user_service)
            report_service = DiagnosticReportService(db=db, user_service=user_service)

            # 添加错题
            errorbook_service.add_error(
                question="求 f(x)=x^2 的导数",
                answer="2x",
                analysis="幂函数求导法则",
                knowledge_points=["导数", "幂函数"],
                question_type="解答题",
                subject="数学",
                chapter="函数与导数",
                knowledge_point="导数",
                difficulty=3,
                mastery_level=0,
                user_id=0,
            )
            errorbook_service.add_error(
                question="求 f(x)=sin(x) 的导数",
                answer="cos(x)",
                analysis="三角函数求导法则",
                knowledge_points=["导数", "三角函数"],
                question_type="解答题",
                subject="数学",
                chapter="函数与导数",
                knowledge_point="导数",
                difficulty=3,
                mastery_level=2,
                user_id=0,
            )

            report = report_service.generate_report(user_id=0)
            assert "summary" in report
            assert report["summary"]["total_errors"] == 2
            # 掌握率阈值（≥80 绿 / 40~79 黄 / <40 红）：
            #   导数 掌握率 = (0+2)/2/2 = 50% → 黄(normal)，不算知识盲区
            #   幂函数 掌握率 = 0% → 红(weak)，进入盲区
            assert "幂函数" in report["weak_points"]
            assert "导数" not in report["weak_points"]
            assert any(e["type"] == "解答题" for e in report["error_types"])
            assert any(s["subject"] == "数学" for s in report["error_subjects"])
            assert len(report["suggestions"]) > 0

            markdown = report_service.render_markdown(report)
            assert "# 学习复盘报告" in markdown
            assert "## 三、知识盲区" in markdown
            assert "导数" in markdown  # 作为已掌握知识点出现在「掌握牢固知识点」章节

            export_path = Path(tmp) / "report.md"
            report_service.export_markdown(report, export_path, user_id=0)
            assert export_path.exists()
            assert "学习复盘报告" in export_path.read_text(encoding="utf-8")
        finally:
            db.close()

        print("test_diagnostic_report passed")


if __name__ == "__main__":
    test_diagnostic_report()

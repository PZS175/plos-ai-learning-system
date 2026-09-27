"""学习诊断与复盘报告服务。

一键生成学习复盘报告：掌握牢固知识点、知识盲区、高频错误类型、后续学习建议。
支持预览（返回 Markdown/HTML）与导出 Markdown 文件。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..db import Database, get_db
from ..utils.logger import get_logger
from .knowledge_graph_service import KnowledgeGraphService
from .user_service import UserService

logger = get_logger("services.diagnostic_report_service")


class DiagnosticReportService:
    """学习诊断报告业务服务。"""

    def __init__(
        self,
        db: Optional[Database] = None,
        user_service: Optional[UserService] = None,
        knowledge_graph_service: Optional[KnowledgeGraphService] = None,
    ):
        self.db = db or get_db()
        self.user_service = user_service
        self.knowledge_graph_service = knowledge_graph_service or KnowledgeGraphService(
            db=self.db, user_service=user_service
        )

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def generate_report(self, user_id: Optional[int] = None) -> Dict[str, Any]:
        """生成学习复盘报告。"""
        uid = self._user_id(user_id)
        today = datetime.now().strftime("%Y-%m-%d")

        # 1. 基础统计
        row = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM error_book WHERE user_id = ?", (uid,)
        )
        total_errors = row["c"] if row else 0

        row = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM error_book WHERE user_id = ? AND mastery_level = 2",
            (uid,),
        )
        mastered_errors = row["c"] if row else 0

        row = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM error_book WHERE user_id = ? AND mastery_level = 0",
            (uid,),
        )
        weak_errors = row["c"] if row else 0

        # 2. 学习时长（近 7 天）
        start = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
        rows = self.db.fetchall(
            "SELECT record_date, SUM(value) AS total FROM statistics_records "
            "WHERE user_id = ? AND record_type = 'study_duration' AND record_date >= ? "
            "GROUP BY record_date ORDER BY record_date ASC",
            (uid, start),
        )
        study_minutes = {r["record_date"]: int(r["total"] or 0) for r in rows}
        total_minutes = sum(study_minutes.values())

        # 3. 练习正确率
        rows = self.db.fetchall(
            "SELECT is_correct FROM practice_records WHERE user_id = ?", (uid,)
        )
        practice_total = len(rows)
        practice_correct = sum(1 for r in rows if r.get("is_correct"))
        practice_accuracy = (
            round(practice_correct / practice_total * 100, 1) if practice_total > 0 else 0.0
        )

        # 4. 掌握牢固知识点
        strong_points = self.db.fetchall(
            "SELECT knowledge_point, COUNT(*) AS c FROM error_book "
            "WHERE user_id = ? AND mastery_level = 2 AND knowledge_point != '' "
            "GROUP BY knowledge_point ORDER BY c DESC LIMIT 10",
            (uid,),
        )
        strong_points = [r["knowledge_point"] for r in strong_points]

        # 5. 知识盲区（来自知识图谱服务）
        weak_points = self.knowledge_graph_service.get_weak_points(user_id=uid)

        # 6. 高频错误类型
        rows = self.db.fetchall(
            "SELECT question_type, COUNT(*) AS c FROM error_book "
            "WHERE user_id = ? AND question_type != '' "
            "GROUP BY question_type ORDER BY c DESC LIMIT 5",
            (uid,),
        )
        error_types = [{"type": r["question_type"], "count": r["c"]} for r in rows]

        # 7. 高频错误科目
        rows = self.db.fetchall(
            "SELECT subject, COUNT(*) AS c FROM error_book "
            "WHERE user_id = ? AND subject != '' "
            "GROUP BY subject ORDER BY c DESC LIMIT 5",
            (uid,),
        )
        error_subjects = [{"subject": r["subject"], "count": r["c"]} for r in rows]

        # 8. 后续学习建议
        suggestions = self._generate_suggestions(
            weak_points=weak_points,
            error_types=error_types,
            error_subjects=error_subjects,
            practice_accuracy=practice_accuracy,
            total_minutes=total_minutes,
        )

        return {
            "generated_at": today,
            "summary": {
                "total_errors": total_errors,
                "mastered_errors": mastered_errors,
                "weak_errors": weak_errors,
                "total_study_minutes": total_minutes,
                "practice_total": practice_total,
                "practice_correct": practice_correct,
                "practice_accuracy": practice_accuracy,
            },
            "strong_points": strong_points,
            "weak_points": weak_points,
            "error_types": error_types,
            "error_subjects": error_subjects,
            "suggestions": suggestions,
        }

    def _generate_suggestions(
        self,
        weak_points: List[str],
        error_types: List[Dict[str, Any]],
        error_subjects: List[Dict[str, Any]],
        practice_accuracy: float,
        total_minutes: int,
    ) -> List[str]:
        """基于统计数据生成后续学习建议。"""
        suggestions = []

        if weak_points:
            top_weak = "、".join(weak_points[:3])
            suggestions.append(
                f"近期应优先攻克知识盲区：{top_weak}。建议通过自适应练习针对这些知识点进行专项训练。"
            )
        else:
            suggestions.append("当前没有发现明显知识盲区，保持良好节奏继续复习即可。")

        if error_types:
            top_type = error_types[0]["type"]
            suggestions.append(
                f"「{top_type}」是你的高频错误题型，建议集中练习同类题目并总结解题模板。"
            )

        if error_subjects:
            top_subject = error_subjects[0]["subject"]
            suggestions.append(
                f"在「{top_subject}」科目上错题相对集中，可适当增加该科目的学习时长与错题回顾。"
            )

        if practice_accuracy < 60:
            suggestions.append(
                f"近期练习正确率为 {practice_accuracy}%，建议降低练习难度、加强基础巩固，避免盲目刷题。"
            )
        elif practice_accuracy >= 85:
            suggestions.append(
                f"近期练习正确率达到 {practice_accuracy}%，可适当提升题目难度或拓展相关高阶知识点。"
            )

        if total_minutes < 60:
            suggestions.append("近 7 天学习时长较短，建议制定每日固定学习时段，保持学习连续性。")

        if not suggestions:
            suggestions.append("继续按计划学习，定期复盘错题与闪卡。")

        return suggestions

    def render_markdown(self, report: Dict[str, Any]) -> str:
        """将报告渲染为 Markdown 文本。"""
        s = report["summary"]
        lines = [
            "# 学习复盘报告\n",
            f"生成时间：{report['generated_at']}\n",
            "## 一、学习概览\n",
            f"- 错题总数：**{s['total_errors']}**",
            f"- 已掌握错题：**{s['mastered_errors']}**",
            f"- 未掌握/薄弱错题：**{s['weak_errors']}**",
            f"- 近 7 天学习时长：**{s['total_study_minutes']} 分钟**",
            f"- 练习总次数：**{s['practice_total']}**",
            f"- 练习正确率：**{s['practice_accuracy']}%**",
            "",
            "## 二、掌握牢固知识点\n",
        ]
        if report["strong_points"]:
            for p in report["strong_points"]:
                lines.append(f"- {p}")
        else:
            lines.append("暂无数据，继续录入错题后会自动生成。")
        lines.append("")

        lines.append("## 三、知识盲区\n")
        if report["weak_points"]:
            for p in report["weak_points"]:
                lines.append(f"- ⚠ {p}")
        else:
            lines.append("暂无薄弱知识点，继续保持。")
        lines.append("")

        lines.append("## 四、高频错误类型\n")
        if report["error_types"]:
            for t in report["error_types"]:
                lines.append(f"- {t['type']}：{t['count']} 次")
        else:
            lines.append("暂无数据。")
        lines.append("")

        lines.append("## 五、高频错误科目\n")
        if report["error_subjects"]:
            for sub in report["error_subjects"]:
                lines.append(f"- {sub['subject']}：{sub['count']} 次")
        else:
            lines.append("暂无数据。")
        lines.append("")

        lines.append("## 六、后续学习建议\n")
        for idx, suggestion in enumerate(report["suggestions"], start=1):
            lines.append(f"{idx}. {suggestion}")

        return "\n".join(lines)

    def export_markdown(
        self, report: Dict[str, Any], output_path: Path, user_id: Optional[int] = None
    ) -> Path:
        """导出报告为 Markdown 文件。"""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        content = self.render_markdown(report)
        output_path.write_text(content, encoding="utf-8")
        logger.info(
            "Exported diagnostic report for user_id=%d to %s",
            self._user_id(user_id),
            output_path,
        )
        return output_path

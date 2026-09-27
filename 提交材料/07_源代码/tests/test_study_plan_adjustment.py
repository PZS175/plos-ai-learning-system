"""学习路径动态调优测试。

验证：
1. 错题新增后，当前活跃计划自动插入复习任务并记录变更
2. 掌握度变化后，计划自动记录变更
3. list_adjustments 返回结构化变更记录
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from plos.db import Database
from plos.services import ErrorBookService, StudyPlanService, UserService


class _MockUserService(UserService):
    def get_current_user_id(self) -> int:
        return 0


def test_study_plan_adjustment():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_plan_adjust.db"
        db = Database(db_path=db_path)
        user_service = _MockUserService()

        errorbook_service = ErrorBookService(db=db, user_service=user_service)
        study_plan_service = StudyPlanService(
            db=db,
            user_service=user_service,
            errorbook_service=errorbook_service,
            model_manager=None,
        )
        errorbook_service.set_study_plan_service(study_plan_service)

        # 创建一个学习计划（模型不可用，使用默认任务）
        plan_id = study_plan_service.create_plan(
            title="测试计划",
            goal="巩固函数与导数",
            weak_subjects="数学",
            daily_minutes=60,
            user_id=0,
        )
        plan = study_plan_service.get_plan(plan_id, user_id=0)
        assert plan is not None
        initial_task_count = len(plan.get("tasks", []))
        assert initial_task_count > 0

        # 添加错题，触发动态调优
        error_id = errorbook_service.add_error(
            question="求函数 f(x)=x^2 在 x=1 处的导数。",
            answer="2",
            analysis="f'(x)=2x，代入 x=1 得 2。",
            knowledge_points=["导数的几何意义", "基本求导"],
            question_type="解答题",
            subject="数学",
            chapter="函数与导数",
            knowledge_point="导数的几何意义",
            difficulty=3,
            user_id=0,
        )
        assert error_id > 0

        # 验证任务增加
        plan = study_plan_service.get_plan(plan_id, user_id=0)
        assert len(plan.get("tasks", [])) == initial_task_count + 1

        # 验证变更记录
        adjustments = study_plan_service.list_adjustments(plan_id, user_id=0)
        assert len(adjustments) == 1
        assert adjustments[0]["trigger_type"] == "error_added"
        assert "导数的几何意义" in adjustments[0]["trigger_desc"]

        # 掌握度变化，再次触发
        errorbook_service.set_mastery(error_id, level=0, user_id=0)
        adjustments = study_plan_service.list_adjustments(plan_id, user_id=0)
        assert len(adjustments) == 2
        assert adjustments[0]["trigger_type"] == "mastery_changed"

        # 关闭数据库连接
        if db._connection is not None:
            db._connection.close()
            db._connection = None

        print("test_study_plan_adjustment passed")


def test_default_plan_tasks_are_distinguishable():
    """离线降级生成的 7 天任务不能长一个样。

    任务列表只显示「标题 / 科目 / 时长」，若七天标题完全相同，
    学生看到的是一堆一模一样的行，演示现场会以为程序坏了。
    """
    tasks = StudyPlanService._default_tasks("数学", 60)
    assert len(tasks) == 7
    titles = [task["title"] for task in tasks]
    assert len(set(titles)) == 7, f"任务标题重复：{titles}"
    for task in tasks:
        assert task["day"] in task["title"], f"标题里应带上周几：{task}"
        assert task["estimated_minutes"] == 60


if __name__ == "__main__":
    test_study_plan_adjustment()
    test_default_plan_tasks_are_distinguishable()

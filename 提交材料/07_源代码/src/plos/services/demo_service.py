"""演示样例数据服务。

用于答辩或首次体验时一键导入模拟错题、闪卡、学习计划、学习时长与复习记录，
不依赖模型可用性，所有数据在本地 CPU 上直接写入数据库。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..ai import ModelManager
from ..db import Database, get_db
from ..utils.logger import get_logger
from .errorbook_service import ErrorBookService
from .flashcard_service import FlashcardService
from .statistics_service import StatisticsService
from .study_plan_service import StudyPlanService
from .user_service import UserService

logger = get_logger("services.demo_service")


# 演示错题：数学 / 物理 / 化学 / 英语 各 4 条，覆盖不同掌握等级与难度
# 说明：公式统一用可读 Unicode（x²、aₙ、√、π、≤ 等），不使用 LaTeX 命令
_SAMPLE_ERRORS: List[Dict[str, Any]] = [
    # ------------------------- 数学 -------------------------
    {
        "question": "已知函数 f(x)=x³-3x²+2，求 f(x) 的极值点。",
        "answer": "x=0 为极大值点，x=2 为极小值点。",
        "analysis": "先求导 f′(x)=3x²-6x=3x(x-2)，令 f′(x)=0 得 x=0 或 x=2；在 x=0 两侧 f′ 由正变负，为极大值点；在 x=2 两侧由负变正，为极小值点。",
        "knowledge_tags": "导数,极值,函数性质",
        "knowledge_points": ["导数", "极值", "函数单调性"],
        "question_type": "解答题",
        "subject": "数学",
        "chapter": "导数及其应用",
        "knowledge_point": "利用导数判断函数极值",
        "difficulty": 3,
        "mastery_level": 2,
    },
    {
        "question": "函数 y=sin(2x+π/3) 的最小正周期是多少？",
        "answer": "π",
        "analysis": "对 y=sin(ωx+φ)，最小正周期 T=2π/|ω|；此处 ω=2，故 T=2π/2=π。",
        "knowledge_tags": "三角函数,周期",
        "knowledge_points": ["三角函数", "周期性"],
        "question_type": "选择题",
        "subject": "数学",
        "chapter": "三角函数",
        "knowledge_point": "三角函数周期计算",
        "difficulty": 2,
        "mastery_level": 1,
    },
    {
        "question": "等差数列 {aₙ} 中 a₁=2，公差 d=3，求 a₁₀。",
        "answer": "a₁₀=2+(10-1)×3=29。",
        "analysis": "等差数列通项公式 aₙ=a₁+(n-1)d，代入 n=10：a₁₀=2+9×3=29。",
        "knowledge_tags": "等差数列,通项公式",
        "knowledge_points": ["等差数列", "通项公式"],
        "question_type": "填空题",
        "subject": "数学",
        "chapter": "数列",
        "knowledge_point": "等差数列通项公式",
        "difficulty": 1,
        "mastery_level": 1,
    },
    {
        "question": "抛物线 y²=4x 的焦点坐标是什么？",
        "answer": "(1, 0)",
        "analysis": "标准抛物线 y²=2px 的焦点为 (p/2, 0)；此处 2p=4，得 p=2，故焦点为 (1, 0)。",
        "knowledge_tags": "抛物线,焦点",
        "knowledge_points": ["圆锥曲线", "抛物线"],
        "question_type": "填空题",
        "subject": "数学",
        "chapter": "圆锥曲线",
        "knowledge_point": "抛物线的几何性质",
        "difficulty": 2,
        "mastery_level": 0,
    },
    # ------------------------- 物理 -------------------------
    {
        "question": "物体做初速度为 2 m/s、加速度为 3 m/s² 的匀加速直线运动，求第 3 s 末的速度。",
        "answer": "v=v₀+at=2+3×3=11 m/s。",
        "analysis": "匀变速直线运动速度公式 v=v₀+at，代入 v₀=2、a=3、t=3，得 11 m/s。",
        "knowledge_tags": "运动学,速度公式",
        "knowledge_points": ["匀变速直线运动", "速度-时间关系"],
        "question_type": "填空题",
        "subject": "物理",
        "chapter": "匀变速直线运动",
        "knowledge_point": "速度-时间关系",
        "difficulty": 1,
        "mastery_level": 1,
    },
    {
        "question": "质量为 2 kg 的物体在 10 N 的水平拉力下做匀加速运动，忽略摩擦，求加速度。",
        "answer": "a=F/m=10/2=5 m/s²。",
        "analysis": "根据牛顿第二定律 F=ma，变形得 a=F/m=10/2=5 m/s²。",
        "knowledge_tags": "牛顿定律,加速度",
        "knowledge_points": ["牛顿运动定律", "加速度"],
        "question_type": "解答题",
        "subject": "物理",
        "chapter": "牛顿运动定律",
        "knowledge_point": "牛顿第二定律的应用",
        "difficulty": 2,
        "mastery_level": 0,
    },
    {
        "question": "一段电阻为 10 Ω 的导体两端电压为 5 V，求通过导体的电流。",
        "answer": "I=U/R=5/10=0.5 A。",
        "analysis": "欧姆定律 I=U/R，代入 U=5 V、R=10 Ω，得 I=0.5 A。",
        "knowledge_tags": "电路,欧姆定律",
        "knowledge_points": ["电路", "欧姆定律"],
        "question_type": "填空题",
        "subject": "物理",
        "chapter": "恒定电流",
        "knowledge_point": "欧姆定律",
        "difficulty": 1,
        "mastery_level": 2,
    },
    {
        "question": "两球在光滑水平面上碰撞，碰撞前总动量为 10 kg·m/s，碰撞过程系统不受外力，碰后总动量为多少？",
        "answer": "10 kg·m/s",
        "analysis": "系统不受外力或所受合外力为零时总动量守恒，故碰撞前后总动量相等，仍为 10 kg·m/s。",
        "knowledge_tags": "动量,动量守恒",
        "knowledge_points": ["动量", "动量守恒定律"],
        "question_type": "选择题",
        "subject": "物理",
        "chapter": "动量",
        "knowledge_point": "动量守恒定律",
        "difficulty": 3,
        "mastery_level": 1,
    },
    # ------------------------- 化学 -------------------------
    {
        "question": "铁在氧气中燃烧生成四氧化三铁，写出配平后的化学方程式。",
        "answer": "3Fe + 2O₂ = Fe₃O₄（点燃）",
        "analysis": "按原子守恒配平：生成物含 3 个 Fe、4 个 O，故反应物为 3Fe + 2O₂，条件为点燃。",
        "knowledge_tags": "化学方程式,配平",
        "knowledge_points": ["化学方程式", "质量守恒"],
        "question_type": "填空题",
        "subject": "化学",
        "chapter": "化学方程式",
        "knowledge_point": "化学方程式配平",
        "difficulty": 2,
        "mastery_level": 1,
    },
    {
        "question": "4 g NaOH 的物质的量是多少？（NaOH 的摩尔质量为 40 g/mol）",
        "answer": "n=m/M=4/40=0.1 mol。",
        "analysis": "物质的量 n=质量 m/摩尔质量 M=4/40=0.1 mol。",
        "knowledge_tags": "物质的量,摩尔质量",
        "knowledge_points": ["物质的量", "摩尔质量"],
        "question_type": "解答题",
        "subject": "化学",
        "chapter": "物质的量",
        "knowledge_point": "物质的量计算",
        "difficulty": 1,
        "mastery_level": 2,
    },
    {
        "question": "配制 100 mL 0.1 mol/L NaCl 溶液的步骤顺序是什么？",
        "answer": "计算→称量→溶解→转移→洗涤→定容→摇匀",
        "analysis": "配制一定物质的量浓度溶液的顺序：计算称量→溶解并冷却→转移→洗涤烧杯与玻璃棒→定容→摇匀。",
        "knowledge_tags": "溶液配制,物质的量浓度",
        "knowledge_points": ["溶液配制", "物质的量浓度"],
        "question_type": "解答题",
        "subject": "化学",
        "chapter": "物质的量",
        "knowledge_point": "配制一定物质的量浓度溶液",
        "difficulty": 2,
        "mastery_level": 0,
    },
    {
        "question": "在反应 2KClO₃ →（加热、MnO₂）2KCl + 3O₂↑ 中，哪种元素被氧化？",
        "answer": "氧元素被氧化（化合价由 -2 升到 0）；氯元素被还原（由 +5 降到 -1）。",
        "analysis": "KClO₃ 中氯为 +5 价、氧为 -2 价；产物 O₂ 中氧为 0 价（升高，被氧化），KCl 中氯为 -1 价（降低，被还原）。",
        "knowledge_tags": "氧化还原,化合价",
        "knowledge_points": ["氧化还原反应", "化合价"],
        "question_type": "填空题",
        "subject": "化学",
        "chapter": "氧化还原反应",
        "knowledge_point": "氧化还原反应的判断",
        "difficulty": 3,
        "mastery_level": 1,
    },
    # ------------------------- 英语 -------------------------
    {
        "question": "用所给词的适当形式填空：By the time we arrived, the film ____ (start)。",
        "answer": "had started",
        "analysis": "by the time 从句用一般过去时，主句描述“过去的过去”，用过去完成时 had started。",
        "knowledge_tags": "时态,过去完成时",
        "knowledge_points": ["时态", "过去完成时"],
        "question_type": "填空题",
        "subject": "英语",
        "chapter": "动词时态",
        "knowledge_point": "过去完成时",
        "difficulty": 3,
        "mastery_level": 1,
    },
    {
        "question": "选择正确答案：This book is ____ than that one. A. more interesting  B. interesting  C. most interesting",
        "answer": "A（more interesting）",
        "analysis": "两者之间比较用比较级；interesting 为多音节词，比较级用 more + 原级，故选 A。",
        "knowledge_tags": "形容词,比较级",
        "knowledge_points": ["形容词", "比较级"],
        "question_type": "选择题",
        "subject": "英语",
        "chapter": "形容词与副词",
        "knowledge_point": "形容词比较级",
        "difficulty": 1,
        "mastery_level": 2,
    },
    {
        "question": "用所给词的适当形式填空：Could you tell me where the nearest post office ____ (be)?",
        "answer": "is",
        "analysis": "宾语从句用陈述语序；描述就近地点用一般现在时 is，故填 is。",
        "knowledge_tags": "宾语从句,语序",
        "knowledge_points": ["宾语从句", "复合句"],
        "question_type": "填空题",
        "subject": "英语",
        "chapter": "复合句",
        "knowledge_point": "宾语从句的语序",
        "difficulty": 2,
        "mastery_level": 0,
    },
    {
        "question": "把下列句子改为被动语态：The workers build the bridge every year.",
        "answer": "The bridge is built by the workers every year.",
        "analysis": "一般现在时的被动结构为 be（am/is/are）+ 过去分词；主语 the bridge 为单数，用 is built。",
        "knowledge_tags": "被动语态,一般现在时",
        "knowledge_points": ["被动语态", "动词时态"],
        "question_type": "解答题",
        "subject": "英语",
        "chapter": "动词时态与语态",
        "knowledge_point": "被动语态的构成",
        "difficulty": 3,
        "mastery_level": 1,
    },
]

# 演示闪卡：数学 / 物理 / 化学 / 英语 均衡覆盖，附学习方法类卡片
# 公式使用可读 Unicode（x²、aₙ、√、±、½ 等），不使用 LaTeX 命令
_SAMPLE_FLASHCARDS: List[Dict[str, Any]] = [
    # 数学
    {"front_content": "导数的几何意义是什么？", "back_content": "函数 y=f(x) 在点 x₀ 处的导数 f′(x₀) 等于其图像在该点处切线的斜率。", "subject": "数学", "tags": "导数,几何意义"},
    {"front_content": "等差数列前 n 项和公式是什么？", "back_content": "Sₙ=n(a₁+aₙ)/2=na₁+n(n-1)d/2。", "subject": "数学", "tags": "数列,等差数列,求和"},
    {"front_content": "一元二次方程 ax²+bx+c=0（a≠0）的求根公式是什么？", "back_content": "x=(-b±√(b²-4ac))/(2a)，其中 b²-4ac≥0。", "subject": "数学", "tags": "方程,求根公式"},
    # 物理
    {"front_content": "写出匀变速直线运动的位移公式。", "back_content": "x=v₀t+½at²，也可写为 x=(v₀+v)t/2。", "subject": "物理", "tags": "运动学,位移公式"},
    {"front_content": "匀变速直线运动中，v-t 图像的斜率与面积分别表示什么？", "back_content": "斜率表示加速度 a；图线与时间轴围成的面积表示位移 x。", "subject": "物理", "tags": "运动学,v-t图像"},
    {"front_content": "牛顿第二定律的内容是什么？", "back_content": "物体加速度与所受合外力成正比、与质量成反比，F=ma，加速度方向与合外力方向一致。", "subject": "物理", "tags": "牛顿定律,加速度"},
    # 化学
    {"front_content": "摩尔质量的定义是什么？", "back_content": "单位物质的量的物质所具有的质量，M=m/n，常用单位 g/mol。", "subject": "化学", "tags": "物质的量,摩尔质量"},
    {"front_content": "判断氧化还原反应的根本标志是什么？", "back_content": "反应前后有元素化合价发生升降，其实质是电子的转移。", "subject": "化学", "tags": "氧化还原,化合价"},
    # 英语
    {"front_content": "过去完成时的构成与常见时间状语有哪些？", "back_content": "构成：had+过去分词；常与 by the time、before、when 等连用，表示“过去的过去”。", "subject": "英语", "tags": "时态,过去完成时"},
    {"front_content": "一般现在时主语为第三人称单数时，动词如何变化？", "back_content": "动词原形加 -s/-es，如 work→works、watch→watches、go→goes。", "subject": "英语", "tags": "时态,一般现在时"},
    # 学习方法
    {"front_content": "什么是 SM-2 间隔重复复习法？", "back_content": "根据对卡片的记忆质量动态调整复习间隔与难度系数，在遗忘临界点前安排复习，用最少时间获得最佳记忆效果。", "subject": "学习方法", "tags": "SM-2,间隔重复"},
    {"front_content": "艾宾浩斯遗忘曲线给复习安排什么启示？", "back_content": "遗忘先快后慢：学后当天、第 2 天、第 7 天、第 30 天左右安排复习，记忆保持率更高。", "subject": "学习方法", "tags": "记忆,遗忘曲线"},
]


class DemoService:
    """演示样例数据导入服务。"""

    def __init__(
        self,
        db: Optional[Database] = None,
        user_service: Optional[UserService] = None,
        model_manager: Optional[ModelManager] = None,
    ):
        self.db = db or get_db()
        self.user_service = user_service
        self.errorbook_service = ErrorBookService(
            db=self.db,
            user_service=self.user_service,
            model_manager=model_manager,
        )
        self.flashcard_service = FlashcardService(
            db=self.db,
            user_service=self.user_service,
        )
        self.statistics_service = StatisticsService(
            db=self.db,
            user_service=self.user_service,
        )
        self.study_plan_service = StudyPlanService(
            model_manager=model_manager,
            flashcard_service=self.flashcard_service,
            errorbook_service=self.errorbook_service,
            db=self.db,
            user_service=self.user_service,
        )

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def _clear_user_demo_data(self, user_id: int) -> None:
        """清空当前用户的演示相关数据，便于重新加载。"""
        # 删除本次会导入的演示数据；保留用户已有自定义数据
        tables = [
            ("study_plan_tasks", "user_id"),
            ("study_plans", "user_id"),
            ("flashcards", "user_id"),
            ("error_book", "user_id"),
            ("statistics_records", "user_id"),
            ("user_stat", "user_id"),
        ]
        for table, col in tables:
            try:
                self.db.execute(f"DELETE FROM {table} WHERE {col} = ?", (user_id,))
            except Exception as e:
                logger.warning("Failed to clear %s for demo reset: %s", table, e)

    def load_demo_data(
        self,
        user_id: Optional[int] = None,
        reset: bool = True,
    ) -> Dict[str, Any]:
        """一键导入演示用的错题、闪卡、学习计划、学习时长与复习记录。

        Args:
            user_id: 目标用户 ID，默认当前用户。
            reset: 是否在导入前清空现有演示数据，默认 True。

        Returns:
            导入数量统计，如 {"errors": 16, "flashcards": 12, "plans": 3,
            "study_minutes": 315, "reviews": 36}。
        """
        uid = self._user_id(user_id)
        if reset:
            self._clear_user_demo_data(uid)

        counts = {
            "errors": 0,
            "flashcards": 0,
            "plans": 0,
            "study_minutes": 0,
            "reviews": 0,
        }

        # 1. 导入错题
        for error in _SAMPLE_ERRORS:
            try:
                self.errorbook_service.add_error(user_id=uid, **error)
                counts["errors"] += 1
            except Exception as e:
                logger.warning("Failed to insert demo error: %s", e)

        # 2. 导入闪卡
        for card in _SAMPLE_FLASHCARDS:
            try:
                self.flashcard_service.add_card(user_id=uid, **card)
                counts["flashcards"] += 1
            except Exception as e:
                logger.warning("Failed to insert demo flashcard: %s", e)

        # 3. 导入学习计划并设置不同进度/状态
        plans_config = [
            {
                "title": "函数·数列·圆锥曲线专项",
                "goal": "巩固导数极值、三角函数周期、等差数列通项与抛物线焦点",
                "weak_subjects": "数学",
                "daily_minutes": 60,
                "complete_tasks": 3,
                "manual_status": None,
            },
            {
                "title": "物理力与运动强化训练",
                "goal": "掌握匀变速直线运动、牛顿第二定律、欧姆定律与动量守恒",
                "weak_subjects": "物理",
                "daily_minutes": 45,
                "complete_tasks": 99,  # 全部完成
                "manual_status": None,
            },
            {
                "title": "化学基础概念巩固",
                "goal": "掌握方程式配平、物质的量、溶液配制与氧化还原判断",
                "weak_subjects": "化学",
                "daily_minutes": 30,
                "complete_tasks": 0,
                "manual_status": "archived",
            },
        ]

        for config in plans_config:
            try:
                plan_id = self.study_plan_service.create_plan(
                    title=config["title"],
                    goal=config["goal"],
                    weak_subjects=config["weak_subjects"],
                    daily_minutes=config["daily_minutes"],
                    user_id=uid,
                )
                tasks = self.study_plan_service.list_tasks(plan_id, user_id=uid)
                complete_n = min(config["complete_tasks"], len(tasks))
                for task in tasks[:complete_n]:
                    self.study_plan_service.toggle_task_complete(
                        task["id"], True, user_id=uid
                    )
                if config["manual_status"]:
                    self.db.execute(
                        "UPDATE study_plans SET status = ? WHERE id = ? AND user_id = ?",
                        (config["manual_status"], plan_id, uid),
                    )
                counts["plans"] += 1
            except Exception as e:
                logger.warning("Failed to insert demo study plan: %s", e)

        # 4. 导入近 7 天学习时长
        daily_minutes = [35, 50, 45, 70, 40, 60, 55]
        today = datetime.now().date()
        for i, minutes in enumerate(reversed(daily_minutes)):
            date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            try:
                self.statistics_service.record_study_duration(
                    minutes=minutes, subject="综合", user_id=uid, record_date=date_str
                )
                counts["study_minutes"] += minutes
            except Exception as e:
                logger.warning("Failed to insert demo study duration: %s", e)

        # 5. 导入近 7 天闪卡复习记录（rating 0-3，3 为完全掌握）
        daily_reviews = [
            [2, 3, 1, 2, 3],
            [3, 3, 2, 3],
            [2, 2, 3, 1, 2, 3],
            [3, 3, 3, 2, 3],
            [2, 3, 2, 3],
            [3, 3, 2, 3, 2, 3, 3],
            [2, 2, 3, 3, 3],
        ]
        for i, ratings in enumerate(reversed(daily_reviews)):
            date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            for rating in ratings:
                try:
                    self.statistics_service.record_flashcard_review(
                        rating_value=rating, subject="综合", user_id=uid, record_date=date_str
                    )
                    counts["reviews"] += 1
                except Exception as e:
                    logger.warning("Failed to insert demo flashcard review: %s", e)

        logger.info(
            "Loaded demo data for user_id=%d: errors=%d flashcards=%d plans=%d "
            "study_minutes=%d reviews=%d",
            uid,
            counts["errors"],
            counts["flashcards"],
            counts["plans"],
            counts["study_minutes"],
            counts["reviews"],
        )
        return counts

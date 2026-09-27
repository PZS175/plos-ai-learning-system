"""卷子全流程流水线：拍照 → 解答 → 入库 → 变式 → 计划联动。

把已有能力串成一条一键链路：批量识别试卷图片 → 逐题 AI 解答 →
作为错题入库（掌握度 0，自动打标与学习计划联动由既有钩子完成）→
为前 N 题生成变式题。全流程本地，进度回调驱动 UI。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..utils.logger import get_logger

logger = get_logger("services.exam_pipeline")


class ExamPipelineService:
    """组装 OCR / 错题 / 练习（变式）三个服务的旗舰流水线。"""

    def __init__(
        self,
        ocr_service,
        errorbook_service,
        practice_service=None,
        variant_limit: int = 3,
    ):
        self.ocr_service = ocr_service
        self.errorbook_service = errorbook_service
        self.practice_service = practice_service
        self.variant_limit = variant_limit

    def run(
        self,
        image_paths: List[Path],
        subject: str = "",
        user_id: Optional[int] = None,
        progress: Optional[Callable[[int, str], None]] = None,
    ) -> Dict[str, Any]:
        """执行全流程，返回统计摘要。

        任意一题失败不中断批次（计入 failed）。
        """
        def report(pct: int, msg: str) -> None:
            if progress:
                progress(pct, msg)

        total_steps = len(image_paths) * 2 + self.variant_limit + 1
        step = 0

        summary: Dict[str, Any] = {
            "images": len(image_paths),
            "errors_added": 0,
            "variants": 0,
            "failed": [],
            "error_ids": [],
        }
        solved: List[Dict[str, str]] = []

        # 1) 逐图：识别 + 解答
        for index, path in enumerate(image_paths, start=1):
            name = path.name
            try:
                report(int(step / total_steps * 100), f"[{index}/{len(image_paths)}] 识别 {name}…")
                question_text = self.ocr_service.extract_text(path).strip()
                report(int((step + 1) / total_steps * 100), f"[{index}/{len(image_paths)}] 解答 {name}…")
                solution = self.ocr_service.solve_image_question(path).strip()
                step += 2
            except Exception as e:
                step += 2
                summary["failed"].append(f"{name}: {e}")
                logger.warning("Pipeline OCR failed for %s: %s", name, e)
                continue

            if not question_text:
                summary["failed"].append(f"{name}: 未识别到文字")
                continue

            # 识别结果统一套上 LaTeX 公式标记并清理 OCR 噪声，
            # 入库后由全局公式渲染系统展示、由导出层转成原生公式
            question_text, solution = self._format_pair(question_text, solution)

            solved.append(
                {
                    "question": question_text[:800],
                    "solution": solution[:2000],
                    "image": str(path),
                }
            )

        # 2) 批量入库（掌握度 0；add_error 内部联动学习计划调优）
        for item in solved:
            try:
                error_id = self.errorbook_service.add_error(
                    question=item["question"],
                    answer=item["solution"],
                    analysis=item["solution"],
                    subject=subject,
                    mastery_level=0,
                    image_path=item["image"],
                    user_id=user_id,
                )
                summary["error_ids"].append(error_id)
                summary["errors_added"] += 1
                step += 1
                report(int(step / total_steps * 100), f"已入库：{item['question'][:24]}…")
            except Exception as e:
                summary["failed"].append(f"入库失败 {item['question'][:24]}: {e}")
                logger.warning("Pipeline add_error failed: %s", e)

        # 3) 为前 N 道入库错题生成变式题
        for error_id in summary["error_ids"][: self.variant_limit]:
            try:
                report(int(step / total_steps * 100), f"生成变式题（错题 {error_id}）…")
                variant = self.errorbook_service.generate_variant_question(
                    error_id, user_id=user_id
                )
                if variant and variant.get("question"):
                    summary["variants"] += 1
                step += 1
            except Exception as e:
                step += 1
                logger.warning("Pipeline variant failed: %s", e)

        report(100, f"完成：入库 {summary['errors_added']} 题，变式 {summary['variants']} 题")
        return summary

    @staticmethod
    def _format_pair(question: str, solution: str) -> tuple:
        """给识别文本套 LaTeX 标记；失败时原样返回，绝不影响入库。"""
        try:
            from .text_format_service import get_text_format_service

            formatter = get_text_format_service()
            return formatter.format_text(question), formatter.format_text(solution)
        except Exception as e:
            logger.warning("流水线文本格式化失败，保持原样: %s", e)
            return question, solution

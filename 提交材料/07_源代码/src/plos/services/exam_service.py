"""试卷生成与导出服务。

功能：
- 按知识点范围组卷：自定义选择/填空/判断/简答/主观题数量与难度范围
- 题目由大模型批量生成（JSON 数组），存入 practice_questions 并关联试卷
- 导出 Word（python-docx）与 PDF（PyMuPDF），同时输出【试卷本体】+【参考答案】两套文档
- 兼容本地 Ollama 与云端 API（统一走 ModelManager.chat）

第三方依赖缺失时抛出友好异常，由 UI 层提示，不影响其他模块。
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..ai import ModelManager
from ..core.enums import Role
from ..core.models import ChatMessage
from ..db import Database, get_db
from ..utils.json_utils import extract_json_array, raw_snippet
from ..utils.logger import get_logger
from ..utils.latex_clean import latex_to_unicode, split_fractions
from .practice_service import QUESTION_TYPES
from .user_service import UserService

logger = get_logger("services.exam_service")

# 试卷导出默认模板（字体/字号/行距/页边距/答案位置/知识点标注）
DEFAULT_PAPER_TEMPLATE: Dict[str, Any] = {
    "font_name": "Microsoft YaHei",
    "font_size": 11.0,
    "line_spacing": 1.5,
    "margin_mm": 25.0,
    "answer_mode": "separate",  # separate=分开两个文件；end=答案在试卷文末
    "show_kp": False,
}

# 各题型默认分值
_TYPE_SCORES: Dict[str, int] = {
    "choice": 3,
    "fill": 3,
    "judge": 2,
    "short": 10,
    "open": 15,
}

# 单次 AI 生成单题型题量上限（防止上下文过长）
_MAX_PER_TYPE = 10

_BATCH_PROMPT = (
    "你是专业的出题老师。请围绕知识点集合 [{knowledge_points}] 出 {count} 道{type_label}，"
    "难度在 {dmin} 到 {dmax} 之间（1 最简单，5 最难），难度尽量分布均匀。\n\n"
    "要求：\n"
    "1. 题目之间不重复，考查角度尽量多样，覆盖上述知识点；\n"
    "2. 选择题每题提供 4 个选项，answer 中只写正确选项字母（如 \"A\"）；\n"
    "3. 填空题的 answer 为标准答案文本；\n"
    "4. 判断题的 answer 只能是 \"正确\" 或 \"错误\"；\n"
    "5. 简答题/主观开放题的 answer 为参考答案要点；\n"
    "6. 每题给出 analysis 解析；\n"
    "7. 分数统一写成 \\frac{{分子}}{{分母}} 形式（导出时会渲染为上下结构的真分数）；"
    "其余数学内容不要用 LaTeX 命令，用 Unicode 文本：平方写 x²，下标写 a₁，"
    "根号写 √2，希腊字母直接写 α β π，比较符写 ≤ ≥ ≠。\n\n"
    "严格输出 JSON 数组，不要输出任何其他内容：\n"
    "[\n"
    "  {{\n"
    "    \"knowledge_point\": \"所属知识点\",\n"
    "    \"question\": \"题干\",\n"
    "    \"options\": [\"选项A内容\", \"选项B内容\", \"选项C内容\", \"选项D内容\"],\n"
    "    \"answer\": \"答案\",\n"
    "    \"analysis\": \"解析\"\n"
    "  }}\n"
    "]\n"
    "非选择题的 options 输出空数组 []。"
)


def _extract_json_array(text: str) -> Optional[List[dict]]:
    """委托健壮解析工具（容忍围栏/前后缀/包裹对象/尾逗号）。"""
    return extract_json_array(text)


def _extract_json_array_legacy(text: str) -> Optional[List[dict]]:
    """旧实现，保留用于对照。"""
    if not text:
        return None
    cleaned = text.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json")[1].split("```")[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```")[1].split("```")[0].strip()
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start >= 0 and end > start:
        cleaned = cleaned[start : end + 1]
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, list) else None
    except Exception:
        return None


def _safe_filename(name: str) -> str:
    """清理文件名中的非法字符。"""
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", (name or "").strip())
    return cleaned or "试卷"


class ExamService:
    """试卷生成与导出业务服务。"""

    def __init__(
        self,
        db: Optional[Database] = None,
        user_service: Optional[UserService] = None,
        model_manager: Optional[ModelManager] = None,
    ):
        self.db = db or get_db()
        self.user_service = user_service
        self.model_manager = model_manager

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def _chat(self, prompt: str, max_tokens: int = 4096) -> str:
        if self.model_manager is None or not self.model_manager.is_text_available():
            raise RuntimeError("模型服务不可用，请检查 Ollama 或云端 API 配置")
        messages = [ChatMessage(role=Role.USER, content=prompt)]
        response = self.model_manager.chat(messages=messages, max_tokens=max_tokens)
        if response is None or not response.content:
            raise RuntimeError("模型返回为空")
        return response.content

    # ------------------------------------------------------------------
    # 组卷
    # ------------------------------------------------------------------

    def generate_paper(
        self,
        title: str,
        knowledge_points: List[str],
        type_counts: Dict[str, int],
        difficulty_min: int = 1,
        difficulty_max: int = 5,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """按题型配比生成一份试卷。

        type_counts 形如 {"choice": 5, "fill": 5, "judge": 5, "short": 2, "open": 1}。
        返回试卷信息（含题目列表）。
        """
        title = (title or "").strip()
        points = [p.strip() for p in knowledge_points if p and p.strip()]
        if not title:
            raise ValueError("请输入试卷标题")
        if not points:
            raise ValueError("请至少输入一个知识点")

        counts = {
            key: max(0, min(_MAX_PER_TYPE, int(type_counts.get(key, 0) or 0)))
            for key in QUESTION_TYPES
        }
        total_requested = sum(counts.values())
        if total_requested == 0:
            raise ValueError("各题型题量不能全为 0")
        if total_requested > 30:
            raise ValueError("单份试卷题目总数不能超过 30 题")

        difficulty_min = max(1, min(5, int(difficulty_min)))
        difficulty_max = max(difficulty_min, min(5, int(difficulty_max)))

        uid = self._user_id(user_id)
        all_questions: List[Dict[str, Any]] = []
        failures: List[str] = []

        for q_type, count in counts.items():
            if count <= 0:
                continue
            prompt = _BATCH_PROMPT.format(
                knowledge_points="、".join(points),
                count=count,
                type_label=QUESTION_TYPES[q_type],
                dmin=difficulty_min,
                dmax=difficulty_max,
            )
            try:
                data = None
                last_raw = ""
                # 长题型（选择/填空/简答）输出易在 max_tokens 截断导致 JSON 解析失败，
                # 拆成小批（每批 2 题）多次拉取，凑满数量为止
                per_call = 2 if q_type in ("choice", "fill", "short", "open") else count
                collected: List[dict] = []
                for round_index in range(max(1, -(-count // per_call))):
                    batch_prompt = prompt
                    if round_index > 0:
                        batch_prompt = (
                            prompt
                            + "\n\n（已生成 "
                            + str(len(collected))
                            + " 题，继续输出新的 "
                            + str(min(per_call, count - len(collected)))
                            + " 题，不要重复）"
                        )
                    last_raw = self._chat(batch_prompt, max_tokens=4096)
                    data = _extract_json_array(last_raw)
                    if not data:
                        data = _extract_json_array(last_raw + "]")
                    if data:
                        collected.extend(data)
                        if len(collected) >= count:
                            break
                data = collected[:count]
                if not data:
                    raise RuntimeError(
                        f"模型未返回有效 JSON 数组（已重试）。原始输出片段：{raw_snippet(last_raw)}"
                    )
                for item in data[:count]:
                    if not isinstance(item, dict):
                        continue
                    question_text = str(item.get("question", "")).strip()
                    if not question_text:
                        continue
                    options_raw = item.get("options", [])
                    if isinstance(options_raw, str):
                        try:
                            options_raw = json.loads(options_raw)
                        except Exception:
                            options_raw = []
                    if not isinstance(options_raw, list):
                        options_raw = []
                    options = [str(o).strip() for o in options_raw if str(o).strip()]
                    if q_type == "choice" and len(options) < 2:
                        continue
                    all_questions.append(
                        {
                            "knowledge_point": str(item.get("knowledge_point", "") or points[0]),
                            "question_type": q_type,
                            "question": question_text,
                            "options": options,
                            "answer": str(item.get("answer", "")).strip(),
                            "analysis": str(item.get("analysis", "")).strip(),
                        }
                    )
            except Exception as e:
                failures.append(f"{QUESTION_TYPES[q_type]}：{e}")
                logger.warning("Exam question batch failed for %s: %s", q_type, e)

        if not all_questions:
            detail = "；".join(failures) if failures else "模型未返回任何有效题目"
            raise RuntimeError(f"试卷生成失败：{detail}")

        # 入库：题目 -> practice_questions，关联 -> exam_paper_questions
        paper_id = self.db.insert(
            "INSERT INTO exam_papers "
            "(user_id, title, knowledge_points, type_counts, difficulty_min, difficulty_max, total_score) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                uid,
                title,
                json.dumps(points, ensure_ascii=False),
                json.dumps(counts, ensure_ascii=False),
                difficulty_min,
                difficulty_max,
                0,
            ),
        )

        total_score = 0
        sort_order = 0
        for q in all_questions:
            question_id = self.db.insert(
                "INSERT INTO practice_questions "
                "(user_id, knowledge_point, question_type, difficulty, question, options, answer, analysis) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uid,
                    q["knowledge_point"],
                    q["question_type"],
                    (difficulty_min + difficulty_max) // 2,
                    q["question"],
                    json.dumps(q["options"], ensure_ascii=False),
                    q["answer"],
                    q["analysis"],
                ),
            )
            score = _TYPE_SCORES.get(q["question_type"], 5)
            total_score += score
            self.db.insert(
                "INSERT INTO exam_paper_questions (paper_id, question_id, sort_order, score) "
                "VALUES (?, ?, ?, ?)",
                (paper_id, question_id, sort_order, score),
            )
            sort_order += 1

        self.db.execute(
            "UPDATE exam_papers SET total_score = ? WHERE id = ?",
            (total_score, paper_id),
        )
        logger.info(
            "Generated exam paper id=%d title=%s questions=%d score=%d",
            paper_id, title, len(all_questions), total_score,
        )
        paper = self.get_paper(paper_id, user_id=uid) or {}
        paper["failures"] = failures
        return paper

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def list_papers(self, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """列出当前用户的试卷。"""
        uid = self._user_id(user_id)
        rows = self.db.fetchall(
            "SELECT * FROM exam_papers WHERE user_id = ? ORDER BY created_at DESC, id DESC",
            (uid,),
        )
        for row in rows:
            try:
                row["knowledge_point_list"] = json.loads(row.get("knowledge_points", "[]") or "[]")
            except Exception:
                row["knowledge_point_list"] = []
            try:
                row["type_count_map"] = json.loads(row.get("type_counts", "{}") or "{}")
            except Exception:
                row["type_count_map"] = {}
        return rows

    def get_paper(
        self, paper_id: int, user_id: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """获取试卷详情（含题目列表）。"""
        uid = self._user_id(user_id)
        row = self.db.fetchone(
            "SELECT * FROM exam_papers WHERE id = ? AND user_id = ?",
            (paper_id, uid),
        )
        if row is None:
            return None
        try:
            kp_list = json.loads(row.get("knowledge_points", "[]") or "[]")
        except Exception:
            kp_list = []
        try:
            type_map = json.loads(row.get("type_counts", "{}") or "{}")
        except Exception:
            type_map = {}

        questions = self.db.fetchall(
            "SELECT q.id, q.knowledge_point, q.question_type, q.difficulty, "
            "q.question, q.options, q.answer, q.analysis, pq.score, pq.sort_order "
            "FROM exam_paper_questions pq "
            "JOIN practice_questions q ON q.id = pq.question_id "
            "WHERE pq.paper_id = ? "
            "ORDER BY pq.sort_order",
            (paper_id,),
        )
        question_list = []
        for q in questions:
            try:
                options = json.loads(q.get("options", "[]") or "[]")
            except Exception:
                options = []
            question_list.append(
                {
                    "id": q["id"],
                    "knowledge_point": q.get("knowledge_point", ""),
                    "question_type": q.get("question_type", "choice"),
                    "difficulty": q.get("difficulty", 3),
                    "question": q.get("question", ""),
                    "options": options if isinstance(options, list) else [],
                    "answer": q.get("answer", ""),
                    "analysis": q.get("analysis", ""),
                    "score": q.get("score", 0),
                }
            )
        return {
            "id": row["id"],
            "title": row.get("title", ""),
            "knowledge_points": kp_list,
            "type_counts": type_map,
            "difficulty_min": row.get("difficulty_min", 1),
            "difficulty_max": row.get("difficulty_max", 5),
            "total_score": row.get("total_score", 0),
            "created_at": row.get("created_at", ""),
            "questions": question_list,
        }

    def delete_paper(self, paper_id: int, user_id: Optional[int] = None) -> None:
        """删除试卷（题目保留在题库中，仅解除关联）。"""
        uid = self._user_id(user_id)
        row = self.db.fetchone(
            "SELECT id FROM exam_papers WHERE id = ? AND user_id = ?",
            (paper_id, uid),
        )
        if row is None:
            raise ValueError(f"试卷不存在：{paper_id}")
        self.db.execute(
            "DELETE FROM exam_paper_questions WHERE paper_id = ?", (paper_id,)
        )
        self.db.execute(
            "DELETE FROM exam_papers WHERE id = ? AND user_id = ?", (paper_id, uid)
        )
        logger.info("Deleted exam paper id=%d", paper_id)

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------

    def export_paper(
        self,
        paper_id: int,
        output_dir: Path,
        fmt: str = "docx",
        user_id: Optional[int] = None,
        template: Optional[Dict[str, Any]] = None,
    ) -> List[Path]:
        """导出试卷与参考答案，返回文件路径列表。

        fmt: "docx" 或 "pdf"。
        template: 模板配置，支持字段：
            - font_name (str): 正文字体，默认 "Microsoft YaHei"
            - font_size (float): 正文字号(pt)，默认 11
            - line_spacing (float): 行间距倍数，默认 1.5
            - margin_mm (float): 页边距(毫米)，默认 25
            - answer_mode (str): "separate"=试卷与答案分开两个文件（默认）；
                                  "end"=答案附在试卷文末，仅生成一个文件
            - show_kp (bool): 是否在每题后标注知识点名称，默认 False
        """
        paper = self.get_paper(paper_id, user_id=user_id)
        if paper is None:
            raise ValueError(f"试卷不存在：{paper_id}")
        if not paper.get("questions"):
            raise ValueError("试卷没有题目，无法导出")

        tpl = {**DEFAULT_PAPER_TEMPLATE, **(template or {})}
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        base = _safe_filename(paper.get("title", "试卷"))

        paths: List[Path] = []
        answer_mode = tpl.get("answer_mode", "separate")
        if fmt == "docx":
            paper_path = output_dir / f"{base}_试卷.docx"
            if answer_mode == "end":
                # 单一文件：题目 + 文末答案
                self._export_docx(paper, paper_path, with_answers=False,
                                  template=tpl, answers_at_end=True)
                paths.append(paper_path)
            else:
                answer_path = output_dir / f"{base}_参考答案.docx"
                self._export_docx(paper, paper_path, with_answers=False, template=tpl)
                self._export_docx(paper, answer_path, with_answers=True, template=tpl)
                paths.extend([paper_path, answer_path])
        elif fmt == "pdf":
            paper_path = output_dir / f"{base}_试卷.pdf"
            if answer_mode == "end":
                self._export_pdf(paper, paper_path, with_answers=False,
                                 template=tpl, answers_at_end=True)
                paths.append(paper_path)
            else:
                answer_path = output_dir / f"{base}_参考答案.pdf"
                self._export_pdf(paper, paper_path, with_answers=False, template=tpl)
                self._export_pdf(paper, answer_path, with_answers=True, template=tpl)
                paths.extend([paper_path, answer_path])
        else:
            raise ValueError(f"不支持的导出格式：{fmt}")
        logger.info("Exported exam paper id=%d to %s (template=%s)", paper_id, paths, answer_mode)
        return paths

    # ------------------------------------------------------------------
    # Word 导出（python-docx）
    # ------------------------------------------------------------------

    @staticmethod
    def _omml_fraction(num: str, den: str):
        """构造 OMML 上下分数元素（Word 原生公式格式）。"""
        from docx.oxml import parse_xml
        from xml.sax.saxutils import escape

        omml = (
            '<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'
            ' xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<m:f>"
            f'<m:num><m:r><m:t xml:space="preserve">{escape(num)}</m:t></m:r></m:num>'
            f'<m:den><m:r><m:t xml:space="preserve">{escape(den)}</m:t></m:r></m:den>'
            "</m:f></m:oMath>"
        )
        return parse_xml(omml)

    def _add_exam_paragraph(self, document, raw_text: str, prefix: str = "", suffix: str = "") -> None:
        """添加试卷段落：普通 LaTeX 转 Unicode，分数渲染为原生上下分数。"""
        paragraph = document.add_paragraph()
        if prefix:
            paragraph.add_run(prefix)
        for chunk, frac in split_fractions(raw_text):
            if chunk:
                cleaned = latex_to_unicode(chunk)
                if cleaned:
                    paragraph.add_run(cleaned)
            if frac:
                num = latex_to_unicode(frac[0]).strip() or " "
                den = latex_to_unicode(frac[1]).strip() or " "
                paragraph._p.append(self._omml_fraction(num, den))
        if suffix:
            paragraph.add_run(suffix)

    def _export_docx(
        self,
        paper: Dict[str, Any],
        path: Path,
        with_answers: bool,
        template: Optional[Dict[str, Any]] = None,
        answers_at_end: bool = False,
    ) -> None:
        try:
            from docx import Document
            from docx.shared import Cm, Pt
        except ImportError as e:
            raise RuntimeError("缺少 python-docx 依赖，请先运行：pip install python-docx") from e

        tpl = {**DEFAULT_PAPER_TEMPLATE, **(template or {})}
        font_name = str(tpl.get("font_name", "Microsoft YaHei"))
        font_size = float(tpl.get("font_size", 11))
        line_spacing = float(tpl.get("line_spacing", 1.5))
        margin_mm = float(tpl.get("margin_mm", 25))
        show_kp = bool(tpl.get("show_kp", False))

        document = Document()
        # 页边距
        margin_cm = margin_mm / 10.0
        for section in document.sections:
            section.top_margin = Cm(margin_cm)
            section.bottom_margin = Cm(margin_cm)
            section.left_margin = Cm(margin_cm)
            section.right_margin = Cm(margin_cm)

        style = document.styles["Normal"]
        style.font.name = font_name
        style.font.size = Pt(font_size)
        # 中文字体需要单独设置 east-asian 字体
        try:
            from docx.oxml.ns import qn
            style.element.rPr.rFonts.set(qn("w:eastAsia"), font_name)
        except Exception:
            pass
        # 行间距
        try:
            style.paragraph_format.line_spacing = line_spacing
        except Exception:
            pass

        title = paper.get("title", "试卷")
        heading = document.add_heading(title, level=0)
        heading.alignment = 1  # 居中

        subtitle = "参考答案" if with_answers else "试卷"
        info_parts = [
            f"知识点：{'、'.join(paper.get('knowledge_points', []))}",
            f"难度范围：{paper.get('difficulty_min', 1)}-{paper.get('difficulty_max', 5)}",
            f"题目数：{len(paper.get('questions', []))}",
            f"总分：{paper.get('total_score', 0)}",
            f"类型：{subtitle}",
        ]
        info = document.add_paragraph("    |    ".join(info_parts))
        info.alignment = 1

        # 按题型分节
        sections: Dict[str, List[Dict[str, Any]]] = {}
        for q in paper.get("questions", []):
            sections.setdefault(q["question_type"], []).append(q)

        section_names = ["choice", "fill", "judge", "short", "open"]
        # answers_at_end：先输出题目，文末统一附答案
        end_answers: List[str] = []
        for index, q_type in enumerate(section_names, 1):
            questions = sections.get(q_type)
            if not questions:
                continue
            document.add_heading(f"{_CN_NUMBERS[index]}、{QUESTION_TYPES[q_type]}", level=1)
            for number, q in enumerate(questions, 1):
                score_text = f"（{q.get('score', 0)}分）" if q.get("score") else ""
                kp_text = ""
                if show_kp and q.get("knowledge_point"):
                    kp_text = f" ［知识点：{q['knowledge_point']}］"
                self._add_exam_paragraph(
                    document, q["question"],
                    prefix=f"{number}. ", suffix=f" {score_text}{kp_text}",
                )
                if q_type == "choice":
                    for opt_index, option in enumerate(q.get("options", [])):
                        letter = chr(ord("A") + opt_index)
                        self._add_exam_paragraph(document, option, prefix=f"    {letter}. ")
                if q_type in ("short", "open") and not with_answers and not answers_at_end:
                    document.add_paragraph("    ______________________")
                if with_answers:
                    self._add_exam_paragraph(document, q.get("answer", ""), prefix="    【答案】")
                    self._add_exam_paragraph(document, q.get("analysis", ""), prefix="    【解析】")
                elif answers_at_end:
                    end_answers.append(f"{number}. {q.get('answer', '')}")

        if answers_at_end and end_answers:
            document.add_page_break()
            document.add_heading("参考答案", level=1)
            for ans in end_answers:
                self._add_exam_paragraph(document, ans)

        document.save(str(path))

    # ------------------------------------------------------------------
    # PDF 导出（PyMuPDF）
    # ------------------------------------------------------------------

    def _export_pdf(
        self,
        paper: Dict[str, Any],
        path: Path,
        with_answers: bool,
        template: Optional[Dict[str, Any]] = None,
        answers_at_end: bool = False,
    ) -> None:
        try:
            import pymupdf
        except ImportError:
            try:
                import fitz as pymupdf
            except ImportError as e:
                raise RuntimeError("缺少 PyMuPDF 依赖，请先运行：pip install pymupdf") from e

        tpl = {**DEFAULT_PAPER_TEMPLATE, **(template or {})}
        # PDF 单一文件模式：答案随卷输出，保证答案不丢失（Word 版严格放在文末）
        if answers_at_end:
            with_answers = True
        width, height = 595, 842  # A4
        # 页边距：毫米转点（1mm ≈ 2.835pt），默认 25mm ≈ 71pt
        margin = max(28.0, float(tpl.get("margin_mm", 25)) * 2.835)
        # 字号缩放：基准 11pt 对应内部 page_size=14
        font_scale = float(tpl.get("font_size", 11)) / 11.0
        font_name, font_file = _find_pdf_font()
        font_obj = (
            pymupdf.Font(fontfile=font_file) if font_file else pymupdf.Font("china-s")
        )
        y = margin
        page_size = 14 * font_scale

        doc = pymupdf.open()

        def new_page() -> None:
            nonlocal y
            doc.new_page(width=width, height=height)
            if font_file:
                doc[-1].insert_font(fontname=font_name, fontfile=font_file)
            y = margin

        def need_space(h: float) -> None:
            nonlocal y
            if y + h > height - margin:
                new_page()

        def _display_width(s: str) -> int:
            """估算显示宽度：全角/宽字符计 2，其余计 1。"""
            return sum(
                2 if unicodedata.east_asian_width(c) in ("F", "W") else 1 for c in s
            )

        def _pdf_safe(text: str) -> str:
            """替换当前字体缺失的字符为可显示文本。"""
            for src, dst in _PDF_TEXT_FALLBACK.items():
                if src in text:
                    text = text.replace(src, dst)
            return text

        def _write_mixed(x: float, baseline: float, text: str, size: float) -> None:
            """在指定基线写文本，Unicode 上下标绘制为小号偏移字符（练习册样式）。"""
            cur_x = x
            i = 0
            n = len(text)
            while i < n:
                ch = text[i]
                if ch in _PDF_SUPER_BASE:
                    j = i
                    while j < n and text[j] in _PDF_SUPER_BASE:
                        j += 1
                    seg = "".join(_PDF_SUPER_BASE[c] for c in text[i:j])
                    small = size * 0.65
                    doc[-1].insert_text(
                        (cur_x, baseline - size * 0.32),
                        seg,
                        fontname=font_name,
                        fontfile=font_file,
                        fontsize=small,
                    )
                    cur_x += font_obj.text_length(seg, fontsize=small) + size * 0.04
                    i = j
                elif ch in _PDF_SUB_BASE:
                    j = i
                    while j < n and text[j] in _PDF_SUB_BASE:
                        j += 1
                    seg = "".join(_PDF_SUB_BASE[c] for c in text[i:j])
                    small = size * 0.65
                    doc[-1].insert_text(
                        (cur_x, baseline + size * 0.22),
                        seg,
                        fontname=font_name,
                        fontfile=font_file,
                        fontsize=small,
                    )
                    cur_x += font_obj.text_length(seg, fontsize=small) + size * 0.04
                    i = j
                else:
                    j = i
                    while (
                        j < n
                        and text[j] not in _PDF_SUPER_BASE
                        and text[j] not in _PDF_SUB_BASE
                    ):
                        j += 1
                    seg = text[i:j]
                    doc[-1].insert_text(
                        (cur_x, baseline),
                        seg,
                        fontname=font_name,
                        fontfile=font_file,
                        fontsize=size,
                    )
                    cur_x += font_obj.text_length(seg, fontsize=size)
                    i = j

        def _write_fraction(frac, size: int, indent: int) -> None:
            """把分数绘制为练习题样式的上下结构：分子、横线、分母。"""
            nonlocal y
            num = _pdf_safe(latex_to_unicode(frac[0]).strip()) or " "
            den = _pdf_safe(latex_to_unicode(frac[1]).strip()) or " "
            small = max(7, int(size * 0.8))
            w_num, w_den = _display_width(num), _display_width(den)
            # 横线长度覆盖较宽一侧（"─" 为全角宽，显示宽 2）
            bar_units = max(w_num, w_den, 2)
            bar = "\u2500" * max(1, (bar_units + 1) // 2)
            w_bar = _display_width(bar)

            def px(units: float) -> float:
                return units * small * 0.5

            left = margin + indent + 6
            bar_px = px(w_bar)
            need_space(small * 2 + 18)
            # 分子（居中于横线上方）
            _write_mixed(
                left + (bar_px - px(w_num)) / 2,
                y + small,
                num,
                small,
            )
            # 横线
            doc[-1].insert_text(
                (left, y + small + 4),
                bar,
                fontname=font_name,
                fontfile=font_file,
                fontsize=small,
            )
            # 分母（居中于横线下方）
            _write_mixed(
                left + (bar_px - px(w_den)) / 2,
                y + small * 2 + 8,
                den,
                small,
            )
            y += small * 2 + 16

        def write_line(text: str, size: int = 11, indent: int = 0) -> None:
            nonlocal y
            for chunk, frac in split_fractions(text):
                if chunk:
                    cleaned = _pdf_safe(latex_to_unicode(chunk))
                    if not cleaned:
                        cleaned = " "
                    max_chars = max(10, int((width - margin * 2 - indent) / (size * 0.95)))
                    for start in range(0, len(cleaned), max_chars):
                        piece = cleaned[start : start + max_chars]
                        need_space(size + 6)
                        _write_mixed(margin + indent, y + size, piece, size)
                        y += size + 5
                if frac:
                    _write_fraction(frac, size, indent)

        new_page()
        title = paper.get("title", "试卷")
        subtitle = "参考答案" if with_answers else "试卷"
        write_line(title, size=18)
        write_line(f"（{subtitle}）", size=12)
        write_line(
            f"知识点：{'、'.join(paper.get('knowledge_points', []))}    "
            f"难度：{paper.get('difficulty_min', 1)}-{paper.get('difficulty_max', 5)}    "
            f"题目数：{len(paper.get('questions', []))}    总分：{paper.get('total_score', 0)}",
            size=10,
        )
        y += page_size

        sections: Dict[str, List[Dict[str, Any]]] = {}
        for q in paper.get("questions", []):
            sections.setdefault(q["question_type"], []).append(q)

        for index, q_type in enumerate(["choice", "fill", "judge", "short", "open"], 1):
            questions = sections.get(q_type)
            if not questions:
                continue
            write_line(f"{_CN_NUMBERS[index]}、{QUESTION_TYPES[q_type]}", size=page_size)
            y += 4
            for number, q in enumerate(questions, 1):
                score_text = f"（{q.get('score', 0)}分）" if q.get("score") else ""
                write_line(f"{number}. {q['question']} {score_text}")
                if q_type == "choice":
                    for opt_index, option in enumerate(q.get("options", [])):
                        letter = chr(ord("A") + opt_index)
                        write_line(f"      {letter}. {option}")
                if q_type in ("short", "open") and not with_answers:
                    write_line("      ______________________")
                if with_answers:
                    write_line(f"      【答案】{q.get('answer', '')}")
                    write_line(f"      【解析】{q.get('analysis', '')}")
                y += 6

        self._save_pdf(doc, path)

    @staticmethod
    def _save_pdf(doc, path: Path) -> None:
        """保存 PDF，并先把嵌入字体子集化。

        PyMuPDF 的 insert_font 会把**整个字体文件**塞进 PDF（SimSun 约 17MB），
        一份几道题的小测卷也会导出十几 MB，学生没法分享、打开也慢。
        子集化只保留实际用到的字形，体积可降到几十 KB，显示与文本完全一致。
        """
        try:
            doc.subset_fonts()
        except Exception as e:  # pragma: no cover - 旧版 PyMuPDF 无此方法
            logger.warning("PDF 字体子集化失败，导出文件会明显偏大: %s", e)
        doc.save(str(path), garbage=4, deflate=True)
        doc.close()


# 中文节序号
_CN_NUMBERS = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六"}

# PDF 渲染用：Unicode 上标字符 → 基字符（渲染为小号上移文本）
_PDF_SUPER_BASE = {
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5",
    "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
    "⁺": "+", "⁻": "-", "⁼": "=", "⁽": "(", "⁾": ")",
    "ⁿ": "n", "ⁱ": "i",
    "ᵃ": "a", "ᵇ": "b", "ᶜ": "c", "ᵈ": "d", "ᵉ": "e", "ᶠ": "f",
    "ᵍ": "g", "ʰ": "h", "ʲ": "j", "ᵏ": "k", "ˡ": "l", "ᵐ": "m",
    "ᵒ": "o", "ᵖ": "p", "ʳ": "r", "ˢ": "s", "ᵗ": "t", "ᵘ": "u",
    "ᵛ": "v", "ʷ": "w", "ˣ": "x", "ʸ": "y", "ᶻ": "z",
}

# PDF 渲染用：Unicode 下标字符 → 基字符（渲染为小号下移文本）
_PDF_SUB_BASE = {
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
    "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
    "₊": "+", "₋": "-", "₌": "=", "₍": "(", "₎": ")",
    "ₐ": "a", "ₑ": "e", "ₕ": "h", "ᵢ": "i", "ⱼ": "j", "ₖ": "k",
    "ₗ": "l", "ₘ": "m", "ₙ": "n", "ₒ": "o", "ₚ": "p", "ᵣ": "r",
    "ₛ": "s", "ₜ": "t", "ᵤ": "u", "ᵥ": "v", "ₓ": "x",
}

# PDF 字体缺失字符的文本回退（常见数学字体均不含双线体等）
_PDF_TEXT_FALLBACK = {
    "·": "×",
    "\u2207": "grad",
    "ℝ": "R", "ℤ": "Z", "ℕ": "N", "ℂ": "C", "ℍ": "H",
    "\u2111": "Im", "\u2118": "P",
}


def _find_pdf_font() -> tuple:
    """返回 (fontname, fontfile)。优先系统微软雅黑，找不到回退内置 china-s。"""
    for candidate in (
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msyh.ttf",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
    ):
        if Path(candidate).exists():
            return "examfont", candidate
    return "china-s", None

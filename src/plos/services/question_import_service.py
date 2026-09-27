"""批量题库导入服务：解析 Excel(.xlsx)/Word(.docx) 题库文件。

自动识别字段：题干、选项、标准答案、得分要点(解析)、知识点、难度。
解析阶段不写库，返回题目列表（含行级错误标记），由预览界面确认后再入库；
格式错误的行被标记跳过，不会污染现有题库。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..db import Database, get_db
from ..utils.logger import get_logger
from .user_service import UserService

logger = get_logger("services.question_import")

# 字段别名映射（全部小写后匹配）
# 注意：别名不可跨字段重复，否则会按字典顺序被先声明的字段吞掉
# （历史问题：「考点」同时出现在 analysis 与 knowledge_point，实际被当成解析列）
_FIELD_ALIASES: Dict[str, Tuple[str, ...]] = {
    "question": ("题干", "题目", "问题", "question", "stem"),
    "options": ("选项", "choices", "options"),
    "answer": ("标准答案", "答案", "answer", "正确答案"),
    "analysis": ("得分要点", "解析", "答案解析", "analysis"),
    "knowledge_point": ("知识点", "考点", "章节", "knowledge", "kp"),
    "difficulty": ("难度", "difficulty", "difficulty_level"),
    "question_type": ("题型", "类型", "type", "question_type"),
}

_VALID_TYPES = {"choice", "fill", "judge", "short", "open"}
_TYPE_ALIASES = {
    "选择": "choice", "选择题": "choice", "单选": "choice", "choice": "choice",
    "多选": "choice",
    "填空": "fill", "填空题": "fill", "fill": "fill",
    "判断": "judge", "判断题": "judge", "judge": "judge",
    "简答": "short", "简答题": "short", "short": "short",
    "主观": "open", "主观题": "open", "论述": "open", "open": "open",
}


def _match_field(header: str) -> Optional[str]:
    """把表头文字映射到标准字段名。"""
    h = str(header).strip().lower()
    for field, aliases in _FIELD_ALIASES.items():
        for alias in aliases:
            if alias.lower() in h:
                return field
    return None


def _parse_difficulty(value: Any) -> int:
    """难度归一化到 1-5。"""
    try:
        s = str(value).strip()
        if not s:
            return 3
        # 数字
        d = int(float(s))
        return max(1, min(5, d))
    except Exception:
        # 文字难度
        s = str(value)
        if "易" in s or "简单" in s:
            return 1
        if "难" in s:
            return 5
        if "中" in s:
            return 3
        return 3


def _parse_options(raw: Any, answer: str) -> List[str]:
    """解析选项。支持：A. xxx | A、xxx | 换行/分号分隔的完整选项文本，
    也支持 Excel 中多个独立列（由调用方合并传入）。"""
    if not raw:
        return []
    text = str(raw)
    options: List[str] = []
    # 按 A. B. C. D. 或 A、 B、 模式切分
    import re
    parts = re.split(r"(?:^|[\n;；])\s*([A-Ha-h])\s*[\.、\)）:：]\s*", text)
    if len(parts) >= 3:
        # parts: ['', 'A', '内容A', 'B', '内容B', ...]
        for i in range(1, len(parts) - 1, 2):
            content = parts[i + 1].strip()
            if content:
                options.append(f"{parts[i].upper()}. {content}")
    else:
        for line in re.split(r"[\n;；]+", text):
            line = line.strip()
            if line:
                options.append(line)
    return options


class QuestionImportService:
    """批量题库导入。"""

    def __init__(self, db: Optional[Database] = None, user_service: Optional[UserService] = None) -> None:
        self.db = db or get_db()
        self.user_service = user_service

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def parse_file(self, file_path: Path) -> List[Dict[str, Any]]:
        """解析题库文件，返回题目 dict 列表。

        每个 dict: {question, options, answer, analysis, knowledge_point,
                    difficulty, question_type, _row, _error(可选)}
        """
        path = Path(file_path)
        suffix = path.suffix.lower()
        try:
            if suffix == ".xlsx":
                return self._parse_xlsx(path)
            if suffix == ".docx":
                return self._parse_docx(path)
            raise ValueError(f"不支持的文件格式：{suffix}（仅支持 .xlsx / .docx）")
        except Exception as e:
            logger.error("Parse question file failed %s: %s", path, e)
            raise RuntimeError(f"文件解析失败：{e}") from e

    def _normalize_row(self, row: Dict[str, Any], row_no: int) -> Dict[str, Any]:
        """把一行原始字段归一化为标准题目结构，带错误标记。"""
        def _s(key: str) -> str:
            val = row.get(key)
            return "" if val is None else str(val).strip()

        question = _s("question")
        options_raw = row.get("options")
        options_raw = "" if options_raw is None else str(options_raw)
        answer = _s("answer")
        analysis = _s("analysis")
        kp = _s("knowledge_point")
        qtype_raw = _s("question_type")
        difficulty = _parse_difficulty(row.get("difficulty") if row.get("difficulty") is not None else 3)

        qtype = _TYPE_ALIASES.get(qtype_raw, qtype_raw.lower() if qtype_raw else "")
        if qtype not in _VALID_TYPES:
            # 根据有无选项推断
            qtype = "choice" if options_raw else "short"

        options = _parse_options(options_raw, answer)
        if qtype == "choice" and len(options) < 2:
            qtype = "short"
            options = []

        item: Dict[str, Any] = {
            "question": question,
            "options": options,
            "answer": answer,
            "analysis": analysis,
            "knowledge_point": kp,
            "difficulty": difficulty,
            "question_type": qtype,
            "_row": row_no,
        }
        if not question:
            item["_error"] = "题干为空"
        elif not answer and qtype != "open":
            item["_error"] = "缺少标准答案"
        return item

    def _parse_xlsx(self, path: Path) -> List[Dict[str, Any]]:
        try:
            import openpyxl
        except ImportError as e:
            raise RuntimeError("缺少 openpyxl 依赖，请运行：pip install openpyxl") from e

        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []

        # 第一行表头
        headers = [str(c).strip() if c is not None else "" for c in rows[0]]
        field_map: Dict[int, str] = {}
        option_letters: List[Tuple[int, str]] = []
        # 独立选项列正则：A / A. / A、 / 选项A / 选项 A / 选项A.
        opt_col_re = re.compile(r"^(?:选项\s*)?([A-Ha-h])\s*[\.、\)）]?$")
        for idx, h in enumerate(headers):
            m_opt = opt_col_re.match(h)
            if m_opt:
                # 独立选项列优先，避免被 "选项" 别名误吞
                option_letters.append((idx, m_opt.group(1).upper()))
                continue
            field = _match_field(h)
            if field and field != "options":
                field_map[idx] = field
            elif field == "options" and not option_letters:
                # 只有在没有独立选项列时，"选项"整列才作为合并文本
                field_map[idx] = field

        results: List[Dict[str, Any]] = []
        for row_no, raw in enumerate(rows[1:], start=2):
            if raw is None or all(c is None or str(c).strip() == "" for c in raw):
                continue
            item_map: Dict[str, Any] = {}
            for idx, field in field_map.items():
                item_map[field] = raw[idx] if idx < len(raw) else ""
            # 合并独立选项列
            if option_letters:
                opt_parts = []
                for idx, letter in sorted(option_letters):
                    if idx < len(raw) and raw[idx] is not None and str(raw[idx]).strip():
                        opt_parts.append(f"{letter}. {str(raw[idx]).strip()}")
                if opt_parts:
                    item_map["options"] = "\n".join(opt_parts)
            results.append(self._normalize_row(item_map, row_no))

        wb.close()
        return results

    def _parse_docx(self, path: Path) -> List[Dict[str, Any]]:
        try:
            from docx import Document
        except ImportError as e:
            raise RuntimeError("缺少 python-docx 依赖，请运行：pip install python-docx") from e

        doc = Document(str(path))
        results: List[Dict[str, Any]] = []

        # 优先解析表格（题库常见格式）
        for table in doc.tables:
            if not table.rows:
                continue
            header_cells = [c.text.strip() for c in table.rows[0].cells]
            field_map: Dict[int, str] = {}
            for idx, h in enumerate(header_cells):
                field = _match_field(h)
                if field:
                    field_map[idx] = field
            if not field_map:
                continue
            for row_no, row in enumerate(table.rows[1:], start=2):
                cells = [c.text.strip() for c in row.cells]
                if not any(cells):
                    continue
                item_map = {field: cells[idx] for idx, field in field_map.items() if idx < len(cells)}
                results.append(self._normalize_row(item_map, row_no))

        if results:
            return results

        # 兜底：按段落解析（题干以数字开头，答案行以"答案"开头）
        return self._parse_docx_paragraphs(doc)

    def _parse_docx_paragraphs(self, doc: Any) -> List[Dict[str, Any]]:
        import re
        results: List[Dict[str, Any]] = []
        current: Dict[str, Any] = {}
        buf: List[str] = []

        def flush() -> None:
            if current or buf:
                text = "\n".join(buf).strip()
                if text:
                    current.setdefault("question", text)
                if current.get("question"):
                    results.append(self._normalize_row(current, len(results) + 2))
            buf.clear()

        for para in doc.paragraphs:
            line = para.text.strip()
            if not line:
                continue
            if re.match(r"^\d+\s*[\.、\)）]", line):
                # 新题目开始
                flush()
                current = {"question": re.sub(r"^\d+\s*[\.、\)）]\s*", "", line)}
            elif line.startswith("答案"):
                current["answer"] = re.sub(r"^答案[:：]?\s*", "", line)
            elif line.startswith("解析") or line.startswith("得分要点"):
                current["analysis"] = re.sub(r"^(解析|得分要点)[:：]?\s*", "", line)
            elif line.startswith("知识点") or line.startswith("考点"):
                current["knowledge_point"] = re.sub(r"^(知识点|考点)[:：]?\s*", "", line)
            elif line.startswith("难度"):
                current["difficulty"] = re.sub(r"^难度[:：]?\s*", "", line)
            else:
                buf.append(line)
        flush()
        return results

    def commit_questions(
        self, questions: List[Dict[str, Any]], user_id: Optional[int] = None
    ) -> int:
        """把预览确认后的题目写入题库，返回成功数量。"""
        uid = self._user_id(user_id)
        saved = 0
        for q in questions:
            if q.get("_error"):
                continue
            try:
                self.db.insert(
                    "INSERT INTO practice_questions "
                    "(user_id, knowledge_point, question_type, difficulty, question, options, answer, analysis) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        uid,
                        q.get("knowledge_point", ""),
                        q.get("question_type", "short"),
                        int(q.get("difficulty", 3)),
                        q.get("question", ""),
                        json.dumps(q.get("options", []), ensure_ascii=False),
                        q.get("answer", ""),
                        q.get("analysis", ""),
                    ),
                )
                saved += 1
            except Exception as e:
                logger.error("Save imported question failed (row %s): %s", q.get("_row"), e)
        logger.info("Committed %d/%d imported questions", saved, len(questions))
        return saved

"""错题本试卷导出服务测试。

验证：
1. 题型自动分块、有序题号、选择题选项拆分与答题空括号
2. 选题范围（全部 / 按知识点 / 自定义勾选）
3. Word 试卷：页眉、页脚页码域、标题、答案统一放文末
4. PDF 试卷：可正常生成、页数正确、含页眉与页码
5. 异常与文件名清洗
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from plos.db import Database  # noqa: E402
from plos.services import ErrorBookService, ErrorPaperService  # noqa: E402
from plos.services.errorpaper_service import (  # noqa: E402
    PAPER_HEADER,
    default_filename,
    sanitize_filename,
)
from plos.services.user_service import UserService  # noqa: E402


class _MockUserService(UserService):
    def get_current_user_id(self) -> int:
        return 0


@pytest.fixture()
def paper_env(tmp_path: Path):
    db = Database(db_path=tmp_path / "paper.db")
    users = _MockUserService()
    errorbook = ErrorBookService(db=db, user_service=users)
    service = ErrorPaperService(
        db=db, errorbook_service=errorbook, user_service=users
    )
    try:
        yield {"db": db, "errorbook": errorbook, "service": service, "tmp": tmp_path}
    finally:
        db.close()


def _seed(errorbook: ErrorBookService) -> dict:
    """造四类题型的错题，返回 id 方便后续按范围筛选。"""
    ids = {}
    ids["choice"] = errorbook.add_error(
        question="下列说法正确的是 A. 甲 B. 乙 C. 丙 D. 丁",
        answer="B",
        analysis="逐项判断即可。",
        knowledge_points=["函数"],
        question_type="选择题",
        knowledge_point="函数",
        subject="数学",
        user_id=0,
    )
    ids["fill"] = errorbook.add_error(
        question="函数 f(x)=x² 的导数是 ______。",
        answer="2x",
        analysis="幂函数求导法则。",
        knowledge_points=["导数"],
        question_type="填空题",
        knowledge_point="导数",
        subject="数学",
        user_id=0,
    )
    ids["judge"] = errorbook.add_error(
        question="两个负数相乘结果一定为正数。",
        answer="正确",
        analysis="负负得正。",
        knowledge_points=["有理数"],
        question_type="判断题",
        knowledge_point="有理数",
        subject="数学",
        user_id=0,
    )
    ids["short"] = errorbook.add_error(
        question="求 f(x)=x³-3x 的极值。",
        answer="极大值 f(0)=0，极小值 f(2)=-4。",
        analysis="求导后令导数为零，判断符号变化。",
        knowledge_points=["导数", "极值"],
        question_type="解答题",
        knowledge_point="极值",
        subject="数学",
        user_id=0,
    )
    return ids


# ----------------------------------------------------------------------
# 一、试卷模型
# ----------------------------------------------------------------------

def test_build_paper_sections_and_numbering(paper_env):
    """四类题型分块展示，题号跨题型连续；选择题选项被拆分。"""
    ids = _seed(paper_env["errorbook"])
    errors = paper_env["service"].select_errors(scope="all")

    paper = paper_env["service"].build_paper(errors, {"knowledge_label": "全部错题"})

    labels = [s["label"] for s in paper["sections"]]
    assert labels == ["选择题", "填空题", "判断题", "简答题"]
    assert paper["total"] == 4

    numbers = [q["number"] for s in paper["sections"] for q in s["questions"]]
    assert numbers == [1, 2, 3, 4], "题号必须在整卷范围内连续"

    choice = paper["sections"][0]["questions"][0]
    assert choice["stem"].endswith("（　　）"), "选择题应预留答题空括号"
    assert choice["options"] == ["A. 甲", "B. 乙", "C. 丙", "D. 丁"]

    judge = paper["sections"][2]["questions"][0]
    assert judge["stem"].endswith("（　　）"), "判断题应预留答题空括号"

    short = paper["sections"][3]["questions"][0]
    assert short["blank_lines"] >= 4, "简答题必须预留大片作答空白"
    assert "极值" in short["stem"], "解答题应归入简答题分块"


def test_meta_contains_date_and_scope(paper_env):
    _seed(paper_env["errorbook"])
    errors = paper_env["service"].select_errors(scope="all")
    paper = paper_env["service"].build_paper(errors, {"knowledge_label": "导数"})

    assert "生成日期：" in paper["meta"]
    assert "知识点范围：导数" in paper["meta"]
    assert "题量：4 题" in paper["meta"]


# ----------------------------------------------------------------------
# 二、选题范围
# ----------------------------------------------------------------------

def test_select_scope_all_knowledge_and_custom(paper_env):
    ids = _seed(paper_env["errorbook"])
    service = paper_env["service"]

    assert len(service.select_errors(scope="all")) == 4

    by_kp = service.select_errors(scope="knowledge_point", knowledge_point="导数")
    assert [e["question_type"] for e in by_kp] == ["填空题"]

    custom = service.select_errors(
        scope="custom", error_ids=[ids["judge"], ids["choice"]]
    )
    assert [int(e["id"]) for e in custom] == [ids["judge"], ids["choice"]]

    assert service.select_errors(scope="custom", error_ids=[]) == []
    assert service.select_errors(scope="knowledge_point", knowledge_point="") == []


def test_knowledge_points_listed_for_filter(paper_env):
    _seed(paper_env["errorbook"])
    points = paper_env["service"].list_knowledge_points()
    assert set(points) == {"函数", "导数", "有理数", "极值"}


# ----------------------------------------------------------------------
# 三、Word 导出
# ----------------------------------------------------------------------

def test_export_docx_layout(paper_env):
    """Word 试卷：标题、页眉、页脚页码域、题型分块、文末答案。"""
    docx = pytest.importorskip("docx")
    ids = _seed(paper_env["errorbook"])
    service = paper_env["service"]
    errors = service.select_errors(scope="all")

    out = paper_env["tmp"] / "试卷.docx"
    service.export(errors, out, fmt="docx", options={"with_answers": True})
    assert out.exists() and out.stat().st_size > 0

    document = docx.Document(str(out))
    texts = [p.text for p in document.paragraphs]
    joined = "\n".join(texts)

    assert "PLOS AI 错题练习卷" in texts[0]
    assert "一、选择题" in joined and "四、简答题" in joined
    assert "参考答案与解析" in joined, "答案必须集中放在文末"
    # 答案不穿插在题目中间：最后一道题的题干之后才出现答案板块
    answer_index = texts.index("参考答案与解析")
    last_question_index = max(
        i for i, t in enumerate(texts) if "极值" in t and t.startswith("4．")
    )
    assert answer_index > last_question_index

    # 页眉 / 页脚
    section = document.sections[0]
    assert PAPER_HEADER in section.header.paragraphs[0].text
    footer_xml = section.footer.paragraphs[0]._p.xml
    assert "PAGE" in footer_xml and "NUMPAGES" in footer_xml, "页脚必须是真实页码域"

    # 字号规范：标题二号 22pt
    title_run = document.paragraphs[0].runs[0]
    assert title_run.font.size.pt == 22.0


def test_export_docx_without_answers_is_blank_paper(paper_env):
    docx = pytest.importorskip("docx")
    _seed(paper_env["errorbook"])
    service = paper_env["service"]
    errors = service.select_errors(scope="all")

    out = paper_env["tmp"] / "空白卷.docx"
    service.export(errors, out, fmt="docx", options={"with_answers": False})

    joined = "\n".join(p.text for p in docx.Document(str(out)).paragraphs)
    assert "参考答案与解析" not in joined
    assert "【答案】" not in joined


def test_export_docx_includes_wrong_answer(paper_env):
    docx = pytest.importorskip("docx")
    ids = _seed(paper_env["errorbook"])
    db = paper_env["db"]
    db.execute(
        "INSERT INTO subjective_grades (user_id, error_id, question_id, user_answer) "
        "VALUES (?, ?, ?, ?)",
        (0, ids["short"], 0, "我写成了 x=1 时有极值"),
    )
    service = paper_env["service"]
    errors = service.select_errors(scope="all")

    out = paper_env["tmp"] / "带错答.docx"
    service.export(
        errors, out, fmt="docx",
        options={"with_answers": True, "with_wrong_answer": True},
    )
    joined = "\n".join(p.text for p in docx.Document(str(out)).paragraphs)
    assert "【你的错误作答】我写成了 x=1 时有极值" in joined

    # 不勾选时不应出现该板块
    out2 = paper_env["tmp"] / "不带错答.docx"
    service.export(
        errors, out2, fmt="docx",
        options={"with_answers": True, "with_wrong_answer": False},
    )
    joined2 = "\n".join(p.text for p in docx.Document(str(out2)).paragraphs)
    assert "【你的错误作答】" not in joined2


# ----------------------------------------------------------------------
# 四、PDF 导出
# ----------------------------------------------------------------------

def test_export_pdf_pages_and_decorations(paper_env):
    pymupdf = pytest.importorskip("pymupdf")
    _seed(paper_env["errorbook"])
    service = paper_env["service"]
    errors = service.select_errors(scope="all")

    out = paper_env["tmp"] / "试卷.pdf"
    service.export(errors, out, fmt="pdf", options={"with_answers": True})
    assert out.exists() and out.stat().st_size > 0

    with pymupdf.open(str(out)) as doc:
        assert doc.page_count == 2, "题目页 + 文末答案页 = 2 页"
        first = doc[0].get_text()
        assert "PLOS AI 错题练习卷" in first
        assert "第 1 页 / 共 2 页" in first
        assert "一、选择题" in first
        second = doc[1].get_text()
        assert "第 2 页 / 共 2 页" in second
        assert "参考答案与解析" in second


def test_export_pdf_subsets_embedded_font(paper_env):
    """回归：PDF 里的嵌入字体必须子集化。

    PyMuPDF 的 insert_font 会把整个字体文件塞进 PDF（SimSun 约 17MB），
    一份四道题的练习卷就能导出 18MB+，学生没法用微信/邮件发出去。
    """
    pymupdf = pytest.importorskip("pymupdf")
    _seed(paper_env["errorbook"])
    service = paper_env["service"]
    errors = service.select_errors(scope="all")

    out = paper_env["tmp"] / "瘦身卷.pdf"
    service.export(errors, out, fmt="pdf", options={"with_answers": True})

    size_mb = out.stat().st_size / 1024 / 1024
    assert size_mb < 1.0, f"PDF 体积 {size_mb:.2f}MB，嵌入字体可能没做子集化"

    with pymupdf.open(str(out)) as doc:
        # 子集化后的字体名带 6 位随机前缀，例如 ZSLRFW+SimSun
        names = [font[3] for font in doc.get_page_fonts(0)]
        assert names, "PDF 里没有字体？"
        assert any("+" in name for name in names), f"字体未被子集化：{names}"
        assert "PLOS AI 错题练习卷" in doc[0].get_text(), "子集化后正文不应丢失"


def test_export_pdf_without_answers_single_page(paper_env):
    pymupdf = pytest.importorskip("pymupdf")
    _seed(paper_env["errorbook"])
    service = paper_env["service"]
    errors = service.select_errors(scope="all")

    out = paper_env["tmp"] / "空白卷.pdf"
    service.export(errors, out, fmt="pdf", options={"with_answers": False})

    with pymupdf.open(str(out)) as doc:
        text = "".join(page.get_text() for page in doc)
        assert "参考答案与解析" not in text
        assert "第 1 页 / 共 1 页" in text


# ----------------------------------------------------------------------
# 四、公式与图片
# ----------------------------------------------------------------------

def test_formula_normalized_to_readable_unicode(paper_env):
    """公式必须转成可读 Unicode（1/2、x²、√x），文档里不能残留 LaTeX 命令。"""
    paper_env["errorbook"].add_error(
        question="计算 \\frac{1}{2}+\\sqrt{x} 的值。",
        answer="x^{2}+1",
        analysis="先化简再代入。",
        question_type="解答题",
        user_id=0,
    )
    service = paper_env["service"]
    errors = service.select_errors(scope="all")

    docx = pytest.importorskip("docx")
    docx_path = paper_env["tmp"] / "公式.docx"
    service.export(errors, docx_path, fmt="docx")
    texts = "\n".join(p.text for p in docx.Document(str(docx_path)).paragraphs)
    assert "\\frac" not in texts and "\\sqrt" not in texts
    assert "1/2" in texts and "√x" in texts
    assert "x²+1" in texts

    pymupdf = pytest.importorskip("pymupdf")
    pdf_path = paper_env["tmp"] / "公式.pdf"
    service.export(errors, pdf_path, fmt="pdf")
    with pymupdf.open(str(pdf_path)) as doc:
        pdf_text = "".join(page.get_text() for page in doc)
    assert "\\frac" not in pdf_text and "\\sqrt" not in pdf_text
    assert "1/2" in pdf_text


def test_missing_image_is_skipped_without_failure(paper_env):
    """图片路径失效时必须跳过，不能中断整份试卷导出。"""
    paper_env["errorbook"].add_error(
        question="看图作答：求阴影面积。",
        answer="12",
        question_type="解答题",
        image_path="C:/definitely/not/exists/missing.png",
        user_id=0,
    )
    service = paper_env["service"]
    errors = service.select_errors(scope="all")
    out = paper_env["tmp"] / "缺图.docx"
    service.export(errors, out, fmt="docx")
    assert out.exists() and out.stat().st_size > 0


# ----------------------------------------------------------------------
# 五、异常与工具函数
# ----------------------------------------------------------------------

def test_export_converts_latex_markers_to_real_math(paper_env):
    """带 \\( \\) / \\[ \\] 标记的公式：导出成真公式，不能残留 LaTeX 源码。"""
    paper_env["errorbook"].add_error(
        question="计算 \\(\\frac{7}{18}\\times0.36\\) 的值。",
        answer="\\[\\frac{7}{18}\\times0.36=\\frac{7}{50}\\]",
        analysis="先约分，再相乘。",
        question_type="解答题",
        user_id=0,
    )
    service = paper_env["service"]
    errors = service.select_errors(scope="all")

    docx = pytest.importorskip("docx")
    docx_path = paper_env["tmp"] / "带公式.docx"
    service.export(errors, docx_path, fmt="docx")
    document = docx.Document(str(docx_path))
    xml = "\n".join(p._p.xml for p in document.paragraphs)
    texts = "\n".join(p.text for p in document.paragraphs)

    assert "oMath" in xml and "m:f" in xml, "Word 里分式必须是原生上下分数(OMML)"
    assert "\\frac" not in texts, "不能把 LaTeX 源码导出去"
    assert "\\(" not in texts and "\\[" not in texts, "公式标记也要被剥掉"

    pymupdf = pytest.importorskip("pymupdf")
    pdf_path = paper_env["tmp"] / "带公式.pdf"
    service.export(errors, pdf_path, fmt="pdf")
    with pymupdf.open(str(pdf_path)) as doc:
        pdf_text = "".join(page.get_text() for page in doc)
    assert "\\frac" not in pdf_text
    assert "7" in pdf_text and "18" in pdf_text, "分子分母都要画出来"


def test_export_empty_raises_value_error(paper_env):
    with pytest.raises(ValueError):
        paper_env["service"].export([], paper_env["tmp"] / "x.docx", fmt="docx")


def test_export_unsupported_format(paper_env):
    _seed(paper_env["errorbook"])
    errors = paper_env["service"].select_errors(scope="all")
    with pytest.raises(ValueError):
        paper_env["service"].export(errors, paper_env["tmp"] / "x.txt", fmt="txt")


def test_progress_callback_reports_completion(paper_env):
    _seed(paper_env["errorbook"])
    service = paper_env["service"]
    errors = service.select_errors(scope="all")
    seen = []
    service.export(
        errors, paper_env["tmp"] / "进度.docx", fmt="docx",
        progress=lambda percent, message: seen.append((percent, message)),
    )
    assert seen and seen[-1][0] == 100


def test_sanitize_and_default_filename():
    assert sanitize_filename('a/b:c*?"<>|d') == "abcd"
    assert sanitize_filename("   ") == "PLOS-错题试卷"
    assert sanitize_filename("我的错题卷") == "我的错题卷"
    assert default_filename("PLOS-错题试卷").startswith("PLOS-错题试卷_")


def test_export_writes_into_nonexistent_directory(paper_env):
    """保存到尚不存在的子目录时应自动创建，不抛异常。"""
    _seed(paper_env["errorbook"])
    service = paper_env["service"]
    errors = service.select_errors(scope="all")
    target = paper_env["tmp"] / "子目录" / "试卷.docx"
    service.export(errors, target, fmt="docx")
    assert target.exists()


# ----------------------------------------------------------------------
# 六、导出弹窗与 Toast（离屏渲染，qapp 由 conftest 提供并全程保活）
# ----------------------------------------------------------------------

def test_dialog_defaults_and_validation(qapp, paper_env):
    """弹窗默认：Word 格式、默认文件名、保存目录已填、校验通过。"""
    from plos.ui.error_paper_dialog import ErrorPaperExportDialog

    _seed(paper_env["errorbook"])
    dialog = ErrorPaperExportDialog(paper_env["service"])
    try:
        assert dialog.radio_docx.isChecked()
        assert dialog.check_answers.isChecked(), "默认应带答案"
        assert dialog.name_edit.text().startswith("PLOS-错题试卷_")
        assert dialog.dir_edit.text().strip()
        assert dialog._suffix_label.text() == ".docx"
        assert dialog._validate() is None

        # 切到 PDF 后扩展名提示同步变化
        dialog.radio_pdf.setChecked(True)
        assert dialog._suffix_label.text() == ".pdf"
        assert dialog._target_path().suffix == ".pdf"

        # 自定义选题但未勾选任何题目 → 校验必须拦下
        dialog.radio_custom.setChecked(True)
        assert dialog._scope() == "custom"
        assert dialog.error_list.isEnabled()
        assert dialog._validate() is not None

        dialog._set_all_checked(True)
        assert dialog._validate() is None
        assert len(dialog._checked_ids()) == 4

        dialog._set_all_checked(False)
        assert dialog._checked_ids() == []
    finally:
        dialog.deleteLater()


def test_dialog_knowledge_scope_options(qapp, paper_env):
    from plos.ui.error_paper_dialog import ErrorPaperExportDialog

    _seed(paper_env["errorbook"])
    dialog = ErrorPaperExportDialog(paper_env["service"])
    try:
        points = {dialog.kp_combo.itemText(i) for i in range(dialog.kp_combo.count())}
        assert points == {"函数", "导数", "有理数", "极值"}
        dialog.radio_kp.setChecked(True)
        assert dialog._scope() == "knowledge_point"
        assert dialog.kp_combo.isEnabled()
    finally:
        dialog.deleteLater()


def test_dialog_reports_friendly_error_and_recovers(qapp, paper_env):
    """导出失败要转成自然语言提示，且按钮要恢复可用（程序不卡死）。"""
    from plos.ui.error_paper_dialog import ErrorPaperExportDialog

    _seed(paper_env["errorbook"])
    dialog = ErrorPaperExportDialog(paper_env["service"])
    captured = []
    dialog.export_failed.connect(captured.append)
    dialog._set_busy(True)
    try:
        dialog._on_error("PermissionError: [WinError 5] 拒绝访问。: 'D:\\\\只读目录\\\\卷.docx'")

        assert captured, "失败必须发出 export_failed 信号"
        assert "访问权限" in captured[0]
        label = dialog.progress_label.text()
        assert "导出失败：" in label
        assert "PermissionError" not in label and "WinError" not in label

        # 失败后必须恢复可操作状态，便于用户改路径重试
        assert dialog.export_btn.isEnabled()
        assert dialog.export_btn.text() == "导出生成"
        assert not dialog._busy
    finally:
        dialog.deleteLater()


def test_panel_exposes_paper_export_entry(qapp, paper_env):
    """错题本面板必须挂上「导出试卷」入口，并能唤起导出弹窗。"""
    from PyQt6.QtWidgets import QPushButton

    from plos.ui.error_paper_dialog import ErrorPaperExportDialog
    from plos.ui.errorbook_panel import ErrorBookPanel

    _seed(paper_env["errorbook"])
    panel = ErrorBookPanel(
        paper_env["errorbook"], error_paper_service=paper_env["service"]
    )
    try:
        labels = [b.text() for b in panel.findChildren(QPushButton)]
        assert "导出试卷" in labels
        assert panel.error_paper_service is paper_env["service"]

        dialog = ErrorPaperExportDialog(panel.error_paper_service, parent=panel)
        dialog.deleteLater()
    finally:
        panel.deleteLater()


def test_panel_builds_paper_service_without_injection(qapp, paper_env):
    """不注入服务时面板应自行兜底创建，保证老调用方式不报错。"""
    from plos.ui.errorbook_panel import ErrorBookPanel

    panel = ErrorBookPanel(paper_env["errorbook"])
    try:
        assert panel.error_paper_service is not None
        assert panel.error_paper_service.list_knowledge_points() == []
    finally:
        panel.deleteLater()


def test_toast_renders_action_buttons(qapp):
    """Toast 必须能渲染「打开文件 / 打开所在文件夹」快捷按钮。"""
    from PyQt6.QtWidgets import QPushButton

    from plos.ui.toast import Toast

    clicked = []
    toast = Toast(
        "试卷已导出",
        level="success",
        duration_ms=8000,
        actions=[("打开文件", lambda: clicked.append("file"))],
    )
    try:
        labels = [b.text() for b in toast.findChildren(QPushButton)]
        assert "打开文件" in labels
        next(b for b in toast.findChildren(QPushButton) if b.text() == "打开文件").click()
        assert clicked == ["file"]
    finally:
        toast.deleteLater()

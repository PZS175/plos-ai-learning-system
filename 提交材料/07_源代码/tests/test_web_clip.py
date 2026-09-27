"""网页剪藏服务测试。

验证：
1. URL 格式校验
2. 网页正文提取与标题解析
3. 调用 RAGService 入库并返回 document_id
4. 依赖缺失或请求失败时抛出 WebClipError
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from plos.db import Database
from plos.services import WebClipError, WebClipService
from plos.services.user_service import UserService


class _MockUserService(UserService):
    def get_current_user_id(self) -> int:
        return 0


class _MockResponse:
    def __init__(self, text: str, status_code: int = 200, encoding: str = "utf-8"):
        self.text = text
        self.status_code = status_code
        self.encoding = encoding
        self.apparent_encoding = "utf-8"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


def _make_sample_html() -> str:
    return """<!DOCTYPE html>
<html>
<head><title>示例文章标题</title></head>
<body>
<header>导航栏</header>
<article>
<h1>文章主标题</h1>
<p>这是第一段正文内容。</p>
<p>这是第二段正文内容，包含一些学习资料。</p>
<h2>小节标题</h2>
<p>小节下的详细说明。</p>
</article>
<footer>页脚信息</footer>
</body>
</html>
"""


def test_url_validation():
    rag_service = MagicMock()
    service = WebClipService(rag_service=rag_service)

    for bad_url in ["", "ftp://example.com", "example.com", "not a url"]:
        try:
            service.clip_url(bad_url)
            assert False, f"应抛出 WebClipError: {bad_url}"
        except WebClipError:
            pass

    print("test_url_validation passed")


def test_extract_article():
    html = _make_sample_html()
    article = WebClipService._extract_article(html, "https://example.com/article")

    assert article["title"] == "文章主标题"
    assert "这是第一段正文内容" in article["content"]
    assert "导航栏" not in article["content"]
    assert "页脚信息" not in article["content"]
    assert article["filename"].endswith(".txt")
    print("test_extract_article passed")


def test_clip_url_to_rag():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_web_clip.db"
        db = Database(db_path=db_path)
        user_service = _MockUserService()

        rag_service = MagicMock()
        rag_service.add_document.return_value = 42

        service = WebClipService(
            rag_service=rag_service,
            user_service=user_service,
        )

        html = _make_sample_html()
        with patch("requests.get", return_value=_MockResponse(html)):
            doc_id = service.clip_url("https://example.com/article")

        assert doc_id == 42
        rag_service.add_document.assert_called_once()
        call_args = rag_service.add_document.call_args
        file_path = call_args.kwargs.get("file_path") or call_args.args[0]
        assert Path(file_path).exists()
        content = Path(file_path).read_text(encoding="utf-8")
        assert "来源：https://example.com/article" in content
        assert "文章主标题" in content

        if db._connection is not None:
            db._connection.close()
            db._connection = None

    print("test_clip_url_to_rag passed")


def test_request_failure():
    rag_service = MagicMock()
    service = WebClipService(rag_service=rag_service)

    with patch("requests.get", side_effect=Exception("网络错误")):
        try:
            service.clip_url("https://example.com/article")
            assert False, "应抛出 WebClipError"
        except WebClipError as e:
            assert "请求网页失败" in str(e)

    print("test_request_failure passed")


if __name__ == "__main__":
    test_url_validation()
    test_extract_article()
    test_clip_url_to_rag()
    test_request_failure()
    print("All web_clip tests passed")

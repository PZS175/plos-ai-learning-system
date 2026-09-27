"""通用网页剪藏服务。

粘贴任意 URL，自动抓取网页正文并保存到本地知识库。
依赖 requests 与 beautifulsoup4；缺失时抛出友好提示，不导致软件崩溃。
"""

from __future__ import annotations

import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional

from ..utils.logger import get_logger
from ..utils.paths import get_attachments_dir
from .rag_service import RAGService
from .user_service import UserService

logger = get_logger("services.web_clip_service")


class WebClipError(Exception):
    """网页剪藏业务异常。"""


class WebClipService:
    """网页剪藏业务服务。"""

    def __init__(
        self,
        rag_service: RAGService,
        user_service: Optional[UserService] = None,
    ):
        self.rag_service = rag_service
        self.user_service = user_service

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    @staticmethod
    def _ensure_dependencies() -> None:
        """检查必要第三方依赖是否已安装。"""
        try:
            import requests  # noqa: F401
        except ImportError as e:
            raise WebClipError(
                "缺少 requests 库，请运行：pip install requests"
            ) from e
        try:
            import bs4  # noqa: F401
        except ImportError as e:
            raise WebClipError(
                "缺少 beautifulsoup4 库，请运行：pip install beautifulsoup4"
            ) from e

    @staticmethod
    def _fetch_url(url: str, timeout: int = 20) -> str:
        """抓取网页 HTML 文本。"""
        import requests

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
        except Exception as e:
            raise WebClipError(f"请求网页失败：{e}") from e

        # 简单处理编码
        if response.encoding == "ISO-8859-1":
            response.encoding = response.apparent_encoding or "utf-8"
        return response.text

    @staticmethod
    def _extract_article(html: str, url: str) -> Dict[str, Any]:
        """从 HTML 中提取标题与正文。"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")

        # 移除脚本、样式、导航、页脚等噪音节点
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe", "noscript"]):
            tag.decompose()

        title = ""
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True) or title

        # 优先选择 article 或 main 标签
        body_tag = soup.find("article") or soup.find("main") or soup.find("body")
        if body_tag is None:
            raise WebClipError("无法解析网页正文结构")

        paragraphs = []
        for p in body_tag.find_all(["p", "h1", "h2", "h3", "h4", "li", "pre"]):
            text = p.get_text(strip=True)
            if text:
                paragraphs.append(text)

        content = "\n\n".join(paragraphs)
        if not content.strip():
            raise WebClipError("未从网页中提取到有效正文")

        # 使用标题或域名生成文件名
        safe_title = "".join(c for c in (title or urllib.parse.urlparse(url).netloc) if c.isalnum() or c in "_- ")
        safe_title = safe_title.strip()[:80] or "web_clip"
        return {
            "title": title or safe_title,
            "content": content,
            "filename": f"{safe_title}.txt",
        }

    def clip_url(self, url: str, user_id: Optional[int] = None) -> int:
        """剪藏网页并入库知识库，返回 document_id。"""
        self._ensure_dependencies()
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            raise WebClipError("请输入以 http:// 或 https:// 开头的完整 URL")

        html = self._fetch_url(url)
        article = self._extract_article(html, url)

        uid = self._user_id(user_id)
        attachments_dir = get_attachments_dir()
        attachments_dir.mkdir(parents=True, exist_ok=True)
        safe_filename = article["filename"]
        file_path = attachments_dir / f"{uid}_{safe_filename}"
        # 避免覆盖：追加序号
        counter = 1
        stem = Path(safe_filename).stem
        suffix = Path(safe_filename).suffix
        while file_path.exists():
            file_path = attachments_dir / f"{uid}_{stem}_{counter}{suffix}"
            counter += 1

        file_path.write_text(
            f"标题：{article['title']}\n来源：{url}\n\n{article['content']}",
            encoding="utf-8",
        )

        document_id = self.rag_service.add_document(file_path, user_id=uid)
        logger.info(
            "Clipped url=%s into document_id=%d user_id=%d", url, document_id, uid
        )
        return document_id

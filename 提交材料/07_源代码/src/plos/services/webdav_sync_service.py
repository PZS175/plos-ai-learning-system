"""WebDAV 同步：学习包上传/下载/列表，挂用户已有网盘。

支持坚果云、OneDrive（WebDAV 第三方）、自建 NAS 等。
凭据由用户在设置中提供，本服务不做任何远端注册。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from ..utils.logger import get_logger

logger = get_logger("services.sync")


class WebDAVSyncService:
    """学习包的远端同步（WebDAV 协议，PUT/GET/PROPFIND）。"""

    def __init__(
        self,
        base_url: str = "",
        username: str = "",
        password: str = "",
        remote_dir: str = "plos-sync",
        timeout: int = 60,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.username = username
        self.password = password
        self.remote_dir = remote_dir.strip("/")
        self.timeout = timeout

    def _url(self, name: str) -> str:
        return f"{self.base_url}/{self.remote_dir}/{name}"

    def _client(self) -> httpx.Client:
        return httpx.Client(
            auth=(self.username, self.password) if self.username else None,
            timeout=self.timeout,
            follow_redirects=True,
        )

    def is_configured(self) -> bool:
        return bool(self.base_url)

    def ensure_dir(self) -> None:
        """远端目录不存在则创建（MKCOL，已存在忽略 405/301）。"""
        if not self.base_url:
            raise ValueError("WebDAV 地址未配置")
        url = f"{self.base_url}/{self.remote_dir}/"
        with self._client() as client:
            resp = client.request("MKCOL", url)
            if resp.status_code not in (200, 201, 301, 405):
                resp.raise_for_status()

    def upload_package(self, file_path: Path, remote_name: Optional[str] = None) -> str:
        """上传学习包 zip，返回远端名。"""
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在：{file_path}")
        name = remote_name or file_path.name
        self.ensure_dir()
        data = file_path.read_bytes()
        with self._client() as client:
            resp = client.put(self._url(name), content=data)
            resp.raise_for_status()
        logger.info("Uploaded %s to %s (%d bytes)", name, self.base_url, len(data))
        return name

    def download_package(self, remote_name: str, output_dir: Path) -> Path:
        """下载远端学习包到本地目录，返回文件路径。"""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        with self._client() as client:
            resp = client.get(self._url(remote_name))
            resp.raise_for_status()
            target = output_dir / remote_name
            target.write_bytes(resp.content)
        logger.info("Downloaded %s (%d bytes)", remote_name, len(resp.content))
        return target

    def list_remote(self) -> List[Dict[str, Any]]:
        """列出远端学习包（PROPFIND 解析 <D:response> 文件名）。"""
        import re

        if not self.base_url:
            raise ValueError("WebDAV 地址未配置")
        url = f"{self.base_url}/{self.remote_dir}/"
        with self._client() as client:
            resp = client.request(
                "PROPFIND", url, headers={"Depth": "1"}
            )
            resp.raise_for_status()
        names = re.findall(r"<(?:[a-zA-Z0-9]+:)?displayname>([^<]+)</", resp.text)
        return [
            {"name": n, "is_dir": False}
            for n in names
            if n and n != self.remote_dir and n.lower().endswith(".zip")
        ]

    def delete_remote(self, remote_name: str) -> None:
        with self._client() as client:
            resp = client.request("DELETE", self._url(remote_name))
            resp.raise_for_status()

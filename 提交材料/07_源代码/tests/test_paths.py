"""数据根目录解析测试。

背景：程序默认把 data/config/logs 放在程序自身目录下（便携模式）。
若安装目录权限受限（例如装到 D:\\APP、Program Files 这类位置），
旧实现会直接创建失败，表现为"装完了但程序起不来"。
这里验证：可写时保持便携模式，不可写时退回用户数据目录。
"""

from __future__ import annotations

from pathlib import Path

from plos.utils import paths


def test_data_dir_stays_next_to_app_when_writable(tmp_path: Path, monkeypatch):
    """能写就还用程序目录，保证"整个文件夹拷走就带走数据"的便携特性。"""
    app_dir = tmp_path / "PLOS AI"
    app_dir.mkdir()
    monkeypatch.setattr(paths, "_project_root", lambda: app_dir)
    paths._ROOT_CACHE.clear()

    assert paths.get_data_dir() == app_dir / "data"
    assert paths.get_logs_dir() == app_dir / "logs"
    assert paths.get_config_path() == app_dir / "config" / "config.toml"
    paths._ROOT_CACHE.clear()


def test_data_dir_falls_back_when_app_dir_not_writable(tmp_path: Path, monkeypatch):
    """程序目录不可写时退回用户数据目录，而不是让程序无法初始化数据库。"""
    app_dir = tmp_path / "protected"
    app_dir.mkdir()
    fallback = tmp_path / "localappdata" / "PLOS AI"

    monkeypatch.setattr(paths, "_project_root", lambda: app_dir)
    monkeypatch.setattr(paths, "_is_writable", lambda directory: False)
    monkeypatch.setattr(paths, "_fallback_root", lambda: fallback)
    paths._ROOT_CACHE.clear()

    assert paths.get_data_dir() == fallback / "data"
    assert paths.get_logs_dir() == fallback / "logs"
    assert paths.get_config_path() == fallback / "config" / "config.toml"
    paths._ROOT_CACHE.clear()


def test_fallback_root_uses_local_appdata(tmp_path: Path, monkeypatch):
    """兜底位置取当前用户的本地应用数据目录（Windows 上一定可写）。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert paths._fallback_root() == tmp_path / "Local" / "PLOS AI"


def test_root_probe_is_cached(tmp_path: Path, monkeypatch):
    """重复取路径不应该反复探测磁盘（路径函数调用非常频繁）。"""
    app_dir = tmp_path / "PLOS AI"
    app_dir.mkdir()
    calls = {"count": 0}

    def fake_probe(directory: Path) -> bool:
        calls["count"] += 1
        return True

    monkeypatch.setattr(paths, "_project_root", lambda: app_dir)
    monkeypatch.setattr(paths, "_is_writable", fake_probe)
    paths._ROOT_CACHE.clear()

    for _ in range(5):
        paths.get_data_dir()
    assert calls["count"] == 1
    paths._ROOT_CACHE.clear()


def test_overrides_still_win(tmp_path: Path, monkeypatch):
    """显式传入的路径优先级最高（沙箱运行、测试都依赖这一点）。"""
    monkeypatch.setattr(paths, "_is_writable", lambda directory: False)
    paths._ROOT_CACHE.clear()
    override = tmp_path / "custom.db"
    assert paths.get_db_path(str(override)) == override.resolve()
    assert paths.get_logs_dir(str(tmp_path / "mylogs")) == (tmp_path / "mylogs").resolve()
    paths._ROOT_CACHE.clear()

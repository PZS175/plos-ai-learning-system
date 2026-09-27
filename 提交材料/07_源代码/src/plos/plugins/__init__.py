"""PLOS-AI 插件系统。

- ``manager.PluginManager``：发现 / 加载 / 启用 / 禁用内置与外部插件，
  启用状态持久化到数据目录 ``plugins_config.json``。
- ``context.PluginContext``：注入给每个插件 ``register(ctx)`` 的运行上下文，
  提供面板注册、私有数据读写、主题色与通用 UI 工具。

内置插件位于 ``plos/plugins/builtin/<id>/``，外部插件位于数据目录
``plugins/<id>/``；两者均为 ``plugin.json`` 清单 + 入口 ``.py``（暴露
``register(ctx)``）的声明式结构。
"""

from .context import PluginContext
from .manager import PluginInfo, PluginManager

__all__ = ["PluginContext", "PluginManager", "PluginInfo"]

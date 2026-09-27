# 开发指南

## 环境准备

```powershell
cd plos_ai
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

要求 Python ≥ 3.11；OCR 依赖 PaddlePaddle（首次运行会下载模型到 `.paddlex/`）。

## 常用命令

| 命令 | 用途 |
|------|------|
| `python -m plos` / `python -m plos.ui.app` | 启动桌面应用 |
| `python -m plos.cli doctor --with-models` | 环境体检（数据库/目录/模型） |
| `python -m plos.cli backup` | 立即滚动备份 |
| `python -m plos.cli migrate` | 手动执行待应用的数据库迁移 |
| `python -m pytest` | 全量测试（73+ 用例，临时库，不碰真实数据） |
| `python -m ruff check --select F,E9 src/plos tests tools` | 静态检查（保持 0 问题） |
| `python tools/render_ui_preview.py` | 渲染双主题 UI 截图到 `docs/ui_preview/` |

## 必须遵守的约定

1. **测试隔离**：任何测试/脚本不得写真实 `data/` 目录。构造
   `Database(db_path=tmp_path / "x.db")`（按路径缓存单例，同路径复用）。
2. **主题令牌**：面板代码禁止新增十六进制颜色字面量，取色一律
   `theme_colors()["token"]`；新增令牌要同时更新 `theme_manager`
   两张令牌表与 `ui_utils` 回退表，并同步
   `tests/test_ui_smoke.py::test_theme_tokens_available`。
3. **数据库变更**：禁止直接改 `schema.sql` 了事——在
   `db/migrations.py` 的 `MIGRATIONS` 末尾追加幂等迁移步骤，
   version 严格递增；`schema.sql` 仅用于新库基线（同步补齐）。
4. **服务装配**：面板不要自行 new 服务；需要新服务时改
   `core/context.py` 的 `AppContext.build()`。
5. **文案与注释**：面向用户的文案用中文；注释只写代码本身表达
   不了的约束（为什么），不复述代码（是什么）。

## 主题切换的正确姿势

面板构造时读取令牌生成内联样式，因此：

- 支持实时刷新的面板实现 `on_theme_changed()`（主窗口会广播）；
- 纯 QSS/属性驱动的样式切主题后由 `repolish_tree` 自动生效；
- 离屏渲染双主题验证：`tools/render_ui_preview.py`，逐张检查
  `docs/ui_preview/*.png`。

## 已知环境问题

- 系统 locale 为 zh_HK 时，Qt6 QSpinBox 数字会渲染成注音符号——
  `app.py` 已通过 `QLocale.setDefault(英文)` 全局修复，不要移除；
- offscreen 平台下中文会渲染成方块，UI 预览一律用真实 Windows
  平台运行（`widget.grab()` 不需要 show）；
- 进程退出时可能打印一条 `_QtLogHandler` 的 atexit RuntimeError，
  纯噪音，不影响退出码，不要追修。

## 发布清单

1. 更新 `src/plos/__init__.py` 的 `__version__` 与 `CHANGELOG.md`；
2. `python -m pytest` 全绿 + ruff 干净；
3. `python tools/render_ui_preview.py` 走查关键页面；
4. `python -m plos.cli doctor` 在干净环境全部通过。

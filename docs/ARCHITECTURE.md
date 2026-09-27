# PLOS AI 架构说明

> 本文面向开发者，描述代码分层、关键设计决策与数据流。
> 上手开发请先读 [DEVELOPING.md](DEVELOPING.md)；远期规划见 [ROADMAP.md](ROADMAP.md)。

## 总体分层

```
┌─────────────────────────────────────────────────────┐
│  UI 层  src/plos/ui/                                 │
│  main_window + 16 个业务面板 + 组件（theme/toast/…）  │
│  原则：面板只做展示与交互，业务逻辑下沉服务层          │
├─────────────────────────────────────────────────────┤
│  服务层  src/plos/services/                          │
│  Chat / ErrorBook / Flashcard(SM-2) / Practice /     │
│  Exam / RAG / OCR / ASR / StudyPlan / Statistics /   │
│  Backup / User / Search / …                          │
│  原则：不 import Qt；以 Database 与 ModelManager 为依赖│
├─────────────────────────────────────────────────────┤
│  AI 层  src/plos/ai/     ModelManager（本地/云端后端） │
│  数据层  src/plos/db/    Database 单例 + schema + 迁移 │
│  核心   src/plos/core/   枚举/模型/常量/AppContext     │
│  工具   src/plos/utils/  路径/日志/单例/崩溃处理        │
└─────────────────────────────────────────────────────┘
```

## 关键设计

### 1. AppContext 服务容器（`core/context.py`）

25 个业务服务的装配集中在一处，按依赖顺序构建（教学风格先于对话、
错题本回挂学习计划等约束都有注释）。收益：

- 主窗口构造函数从 90 行服务装配缩减为 4 行；
- 测试与预览工具可以复用或部分替换装配逻辑；
- 新增服务只改 `AppContext.build()`，杜绝"面板里随手 new 服务"。

### 2. 版本化数据库迁移（`db/migrations.py`）

- `PRAGMA user_version` 记录 schema 版本，`MIGRATIONS` 列表只增不改；
- 迁移必须幂等（`IF NOT EXISTS` / 列存在性检查），旧库从 v0 逐级升级，
  新库由 `schema.sql` 建基线后直接跳到最新；
- 当前版本 v2：v1 历史补丁合集，v2 高频查询索引（52 个）。
- **新增迁移的唯一正确姿势**：在 `MIGRATIONS` 末尾追加 `Migration(N+1, …)`。

### 3. 主题系统（`ui/theme_manager.py` + `ui/ui_utils.py`）

- `ThemeManager` 是设计令牌唯一权威源（深浅两套 Aurora 令牌 + 全局 QSS）；
- `ui_utils.theme_colors()` 优先读已注册的 ThemeManager（app 启动时注册），
  未注册时按调色板启发式回退——离屏测试与预览工具因此不依赖主窗口；
- 硬性规则：**面板代码禁止新增十六进制颜色字面量**，一律走令牌。

### 4. 数据安全

- SQLite：WAL + 外键 + `busy_timeout=5000` + `synchronous=NORMAL`；
- 每日启动自动备份（`BackupService.run_auto_backup`，SQLite backup API，
  保留最近 7 份到 `data/backups/auto/`）；手动全量 zip 备份含附件；
- 测试一律使用临时数据库，禁止触碰真实 `data/` 目录。

### 5. 双离线优先

文本/视觉模型走本地 Ollama，可切换云端 API；RAG 用本地 ChromaDB +
嵌入式模型；OCR/ASR 均为本地推理。CLI 工具（`python -m plos.cli`）
不依赖 Qt，可在无界面环境做体检/备份/迁移。

## 数据流示例：错题录入 → 全链路

```
OCRPanel ──录入──▶ ErrorBookService.add_error()
                     ├─▶ SQLite error_book
                     ├─▶ StudyPlanService 动态调优（set_study_plan_service 回挂）
                     └─▶ DashboardPanel.refresh_data()（用户切换/返回面板时）
```

## 测试策略

| 层级 | 位置 | 说明 |
|------|------|------|
| 服务单元 | `tests/test_*_service.py` | 临时库 + 不依赖模型 |
| 算法 | `tests/test_sm2.py` | SM-2 边界用例 |
| UI 冒烟 | `tests/test_ui_smoke.py` | 离屏渲染全部面板 + 复习闭环 |
| 迁移/备份/CLI | `tests/test_migrations.py` 等 | 企业底座回归 |
| 视觉走查 | `tools/render_ui_preview.py` | 双主题 24 张截图人工审阅 |

# 变更日志

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 规范，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.4.0] - 2026-09-06

### 新增：零门槛分发（Phase 1）
- **PyInstaller 打包体系**：`installer/plos_ai.spec` + `tools/build_installer.py`，
  一条命令产出 `dist/PLOS AI/`；内置冒烟自检（打包环境全量导入链验证），
  schema.sql 随包。构建：`pip install pyinstaller && python tools/build_installer.py`
- **首启向导一键下载模型**：检测到缺模型时无需再复制命令行，点
  「一键下载缺失模型」即按硬件推荐套装自动拉取（QProcess 实时进度、
  失败重试、可后台下载）
- `python -m plos` 标准入口（含打包冒烟模式）

### 修复（全码库搜查 12 项）
- 高危：番茄钟休息结束重复记录 25 分钟幽灵专注并无限循环；编辑闪卡
  100% 失败（幻影列 updated_at）；学习包导出 100% 失败（幻影列
  terms.subject）；「重置演示数据」静默清空用户全部数据（现强制确认）；
  备份恢复不清理即插入、单条冲突整表放弃且谎报成功（改为按主键预删、
  逐表事务、失败如实抛错）
- 中危：闪卡到期判定延迟一天（ISO 'T' 与 SQLite 空格格式不一致）；
  PDF 目录分块同页章节边界膨胀污染 RAG；OCR 缩图覆盖用户原图（改临时副本）；
  学习包导入任务 N×M 复制
- 结构性：SQLite 线程安全——Database 进程内 RLock + transaction()
  原子上下文；统计计数改原子 UPSERT（迁移 v3 唯一索引）

### 性能
- Ollama 全请求 keep_alive 30m：模型常驻内存，消除空闲后重载的 10-40s
- 对话流式输出（打字机效果，全链路 Ollama→Manager→Service→Worker→面板）
- OCR 超大图（>2048px）自动等比缩放后再识别

## [0.3.0] - 2026-09-05

### 新增：自学闭环
- **专注模式（番茄钟）**：仪表盘内置计时卡，选科目开始专注，25 分钟一轮（休息 5 分钟），结束自动写入学习时长——热力图/连续天数/周报从此由真实学习行为点亮
- **每日学习目标**：设置页可配每日目标分钟数（默认 30），仪表盘热力卡显示今日进度条
- **本周学习速览**：本周 vs 上周的专注时长/新增错题/复习量/正确率四指标对比（趋势箭头）
- 闪卡复习键盘流：开始复习与每张卡评分后自动聚焦卡片，空格翻卡、1~4 评分全程无鼠标

### 修复
- **真实闪卡复习从不写入统计**（仅演示数据写入，仪表盘复习量/正确率恒为 0）：评分成功后现在记录到 statistics_records
- 番茄钟测试基建：无 QApplication 时构造 Qt 控件的用例会在 Windows 上原生崩溃，已为全部 UI 用例统一提供应用 fixture

## [0.2.0] - 2026-09-05

### 新增
- **企业级工程底座**：
  - 版本化数据库迁移框架（`PRAGMA user_version` + 有序迁移步骤），历史补丁固化为迁移 v1，热点查询索引为迁移 v2（52 个索引）
  - `AppContext` 服务容器：25 个业务服务的装配集中到 `src/plos/core/context.py`，主窗口/测试/工具共用
  - 启动期自动滚动备份（每日一次，SQLite backup API，保留最近 7 份，失败不阻塞启动）
  - CLI 运维工具 `python -m plos.cli`：`doctor`（完整性/版本/孤儿行/目录/模型体检）、`backup`、`migrate`、`version`
  - CI 工作流（GitHub Actions）与 pre-commit 配置
  - `docs/ARCHITECTURE.md`、`docs/DEVELOPING.md`、`docs/ROADMAP.md`
- **学习热力图**（创新）：仪表盘新增近半年 GitHub 风格学习活动网格，色阶随主题令牌，悬浮显示每日时长；配套「连续学习 N 天」streak 统计（`StatisticsService.get_study_streak`）
- 双主题 UI 预览渲染工具 `tools/render_ui_preview.py`（24 张截图，供设计走查与回归）

### 变更
- 数据库连接硬化：`busy_timeout=5000`、`synchronous=NORMAL`（WAL/外键此前已开启）
- 主窗口服务构造迁移至 `AppContext.build()`，UI 层不再手工装配服务
- 全局字体切换为微软雅黑 UI（应用/SVG 导出/日志框），清除 SimSun 依赖

### 修复
- 系统 locale 为 zh_HK 时 Qt6 QSpinBox 数字渲染为注音符号的乱码问题（温度/Token/上下文长度等所有数字框）
- `KnowledgePanel` 构造时 `theme_manager` 未保存导致主窗口启动崩溃
- 设置面板初始化时主题下拉框误触发 `set_theme`，在主窗口构建途中切换全局主题造成样式错配
- 编辑 Mermaid 源码后缩放仍按旧图基准尺寸计算（闭包变量未更新）
- 知识库搜索无结果时中间区域空白（现显示空状态提示）

### UI 重设计（Aurora 设计语言）
- 主色鸢尾蓝紫（暗 `#6C7CFF` / 亮 `#4F5DF5`），三阶背景层次，12px 大圆角卡片
- 深浅双主题令牌表 + 全局 QSS 重写：渐变主按钮、focus 光圈、纤细滚动条
- 侧边栏独立背景、导航选中指示条、品牌区/分组标题令牌化、全局搜索胶囊化
- 视图菜单与侧边栏共用 `NAV_GROUPS` 单一数据源，新增 Ctrl+1~9 快捷切换、Ctrl+F 搜索

## [0.1.0] - 初始版本

- 本地离线 AI 学习桌面应用：AI 对话、错题本、SM-2 闪卡、自适应练习、
  试卷生成、RAG 知识库、OCR 搜题、学习计划、学习诊断、术语词典等 16 个面板。

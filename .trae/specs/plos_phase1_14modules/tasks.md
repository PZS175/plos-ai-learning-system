# PLOS-AI 14 模块扩展 - 实施计划

## 基础设施层

### Task 1: 硬件三档分级扩展
- **Status**: `pending`
- **Priority**: high
- **Depends On**: None
- **Description**:
  - 修改 `config/hardware.py`：新增 `HardwareTier` 枚举（LOW/MID/HIGH），阈值 RAM≤7/8-12/>12GB
  - 新增 `get_hardware_tier()`、`is_low_spec_forced()`（读 settings 的 force_low_spec）
  - 保留原有 `is_high_spec`/`is_low_spec` 兼容
  - 新增"强制低配"设置项到 constants/default config
- **Acceptance Criteria Addressed**: AC-1, AC-2
- **Test Requirements**:
  - `rule` TR-1.1: mock RAM=6/10/16 → tier=LOW/MID/HIGH
  - `rule` TR-1.2: force_low_spec=true → 任意硬件返回 LOW

### Task 2: 数据库 user_id 隔离补全
- **Status**: `pending`
- **Priority**: high
- **Depends On**: None
- **Description**:
  - 为 `conversations`/`messages`/`documents`/`chunks`/`error_book`/`flashcards`/`study_logs` 加 `user_id` 字段（ALTER TABLE + 索引）
  - 写迁移脚本：现有数据归到 default 用户
  - 所有 service 层查询加 `WHERE user_id=?`
- **Acceptance Criteria Addressed**: AC-3
- **Test Requirements**:
  - `rule` TR-2.1: 双账号各录 1 条错题，切换后互不可见
  - `rule` TR-2.2: 迁移后默认用户拥有全部旧数据

### Task 3: 用户密码加密
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 2
- **Description**:
  - users 表加 `password_hash TEXT` 字段
  - `user_service.py`：创建用户传 password，用 PBKDF2-HMAC-SHA256 哈希；新增 `verify_password()`
  - 兼容旧账号（无密码则直接登录）
- **Acceptance Criteria Addressed**: AC-9
- **Test Requirements**:
  - `rule` TR-3.1: 创建用户后密码字段非明文
  - `rule` TR-3.2: verify_password 正确通过/拒绝

## 第一阶段：高优先级

### Task 4: 备份服务增强（.plosbackup + 自动备份 + 分项导出）
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 2
- **Description**:
  - `backup_service.py`：导出格式 `.plosbackup`（zip + 版本头），含 integrity check
  - 新增 `auto_backup()`：按 settings 配置（daily/weekly/exit），保留份数轮转
  - 新增 `export_errorbook_json()`、`export_notes_json()` 分项导出
  - 导入前二次确认逻辑在 UI 层
- **Acceptance Criteria Addressed**: AC-4, AC-5, FR-1.1~1.7
- **Test Requirements**:
  - `rule` TR-4.1: 导出→清空→导入，记录数一致
  - `rule` TR-4.2: 保留 3 份，备份 5 次后剩 3 份
  - `rule` TR-4.3: 坏包导入不破坏现有库

### Task 5: 设置页备份管理子页面
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 4
- **Description**:
  - `settings_panel.py` 增加"数据备份管理"子页签
  - 立即备份、自动备份配置、导入恢复、分项导出按钮
  - 导入二次确认弹窗
- **Acceptance Criteria Addressed**: FR-1.1, FR-1.3, FR-1.5
- **Test Requirements**:
  - `rule` TR-5.1: 点击立即备份生成 .plosbackup
  - `rule` TR-5.2: 导入时弹确认框

### Task 6: 知识图谱增强（掌握率 + 交互 + 降级）
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 1, Task 2
- **Description**:
  - 增强 `knowledge_graph_panel.py`：从 practice_records/error_book 计算知识点掌握率
  - 树状布局（学科→章节→知识点），节点着色
  - 节点右键菜单：查看习题/教材片段/笔记
  - 缩放、拖拽、搜索
  - 中低配时 `main_window` 隐藏导航入口
- **Acceptance Criteria Addressed**: AC-6, FR-2.1~2.6
- **Test Requirements**:
  - `rule` TR-6.1: 掌握率计算正确（答对/总数）
  - `rule` TR-6.2: RAM≤12G 入口隐藏

### Task 7: 艾宾浩斯复习服务
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 2
- **Description**:
  - 新增 `services/review_service.py`：记录题目的作答/对错/难度，按 1/3/7/15/30 天算 next_review
  - 新增 `review_schedules` 表（user_id, source_type, source_id, next_review, interval_days, difficulty）
  - `get_due_reviews()`、`record_review_result()` 延长/缩短周期
- **Acceptance Criteria Addressed**: AC-7, FR-3.1, FR-3.4
- **Test Requirements**:
  - `rule` TR-7.1: 昨天答对的题 next_review 符合节点
  - `rule` TR-7.2: 复习后 interval 变化正确

### Task 8: 首页待复习卡片 + 复习弹窗
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 7
- **Description**:
  - `dashboard_panel.py` 增加"待复习"卡片，显示今日数量，点击跳转复习
  - 新增非阻塞复习提醒窗口（QTimer 后台检查 due reviews）
  - 推迟 1/3 天功能
- **Acceptance Criteria Addressed**: FR-3.2, FR-3.3
- **Test Requirements**:
  - `rule` TR-8.1: 有待复习题时卡片显示数量
  - `rule` TR-8.2: 推迟按钮更新 next_review

### Task 9: 仪表盘图表 + 报告导出
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 1, Task 2
- **Description**:
  - 增强 `dashboard_panel.py`：今日统计、知识点柱状图、错题分类、周趋势折线图
  - 低配（≤7G）隐藏图表，仅数字
  - 新增报告服务：周/月学习报告导出 PDF
- **Acceptance Criteria Addressed**: AC-8, FR-3.5, FR-3.6
- **Test Requirements**:
  - `rule` TR-9.1: ≤7G 时无图表组件
  - `rule` TR-9.2: 报告 PDF 生成成功

### Task 10: 多账号启动选择窗 + UserPanel 增强
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 3
- **Description**:
  - 新增 `ui/login_dialog.py`：账号选择/登录/注册
  - 程序入口判断：无账号→新手向导；有账号→选择窗
  - `UserPanel` 增强：显示当前账号、切换、删除（二次确认）
- **Acceptance Criteria Addressed**: AC-9, FR-4.1~4.3
- **Test Requirements**:
  - `rule` TR-10.1: 多账号启动弹选择窗
  - `rule` TR-10.2: 删除账号后数据清除

### Task 11: 新手向导
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 1
- **Description**:
  - 新增 `ui/onboarding_wizard.py`：5 步 QWizard
  - settings 记 `onboarding_completed` 标记
  - 设置页"重新运行向导"按钮
  - 主题适配
- **Acceptance Criteria Addressed**: AC-10, FR-5.1~5.3
- **Test Requirements**:
  - `rule` TR-11.1: 无标记时启动弹向导
  - `rule` TR-11.2: 5 步可前进后退

### Task 12: 日志面板 + 旧日志清理
- **Status**: `pending`
- **Priority**: high
- **Depends On**: None
- **Description**:
  - 新增 `ui/log_panel.py`：实时滚动、搜索、打包 zip
  - `utils/logger.py`：30 天清理任务（启动时扫描）
  - 设置页"系统日志"子页签
- **Acceptance Criteria Addressed**: AC-11, FR-6.1~6.4
- **Test Requirements**:
  - `rule` TR-12.1: 日志面板显示实时日志
  - `rule` TR-12.2: 30 天前文件被删除

## 第二阶段：中优先级

### Task 13: 富文本笔记模块
- **Status**: `pending`
- **Priority**: medium
- **Depends On**: Task 2
- **Description**:
  - 新增 `services/note_service.py` + `ui/note_panel.py`
  - 笔记本文件夹分类、QTextEdit 富文本（加粗/斜体/标题/列表/图片）
  - 粘贴图片、OCR 文本插入
  - 截图批注（QScreen 截图 + 画笔标注）
  - AI 辅助：提炼考点/生成题/摘要
  - 笔记绑定知识点/习题
- **Acceptance Criteria Addressed**: AC-12, FR-7.1~7.5
- **Test Requirements**:
  - `rule` TR-13.1: 富文本保存后格式保留
  - `rule` TR-13.2: 笔记与习题双向跳转

### Task 14: 教材缓存管理
- **Status**: `pending`
- **Priority**: medium
- **Depends On**: Task 1
- **Description**:
  - 新增 `services/textbook_cache_service.py`
  - 教材页"缓存管理"面板：列表/大小/时间/删除/清空
  - 磁盘上限设置
  - ≤7G 关闭离线缓存
- **Acceptance Criteria Addressed**: FR-8.1~8.4
- **Test Requirements**:
  - `rule` TR-14.1: 缓存列表显示正确
  - `rule` TR-14.2: ≤7G 离线缓存禁用

### Task 15: Ollama 模型管理面板
- **Status**: `pending`
- **Priority**: medium
- **Depends On**: None
- **Description**:
  - 新增 `ui/ollama_manager_panel.py`
  - 调 `ollama list` / `ollama stop`，展示模型大小/显存
  - 状态定时刷新
  - 设置页"本地模型管理"子页签
- **Acceptance Criteria Addressed**: AC-13, FR-9.1~9.3
- **Test Requirements**:
  - `rule` TR-15.1: 显示已下载模型列表
  - `rule` TR-15.2: 停止按钮调用 ollama stop

### Task 16: 批量题库导入
- **Status**: `pending`
- **Priority**: medium
- **Depends On**: Task 2
- **Description**:
  - 新增 `ui/batch_import_panel.py`
  - 解析 .xlsx（openpyxl）/ .docx（python-docx）
  - 预览表格可编辑，确认入库
  - 错误提示不污染
- **Acceptance Criteria Addressed**: AC-14, FR-10.1~10.4
- **Test Requirements**:
  - `rule` TR-16.1: xlsx 解析预览正确
  - `rule` TR-16.2: 坏文件不入库

### Task 17: 试卷导出模板
- **Status**: `pending`
- **Priority**: medium
- **Depends On**: None
- **Description**:
  - 增强试卷导出服务：字体/字号/行距/页边距配置
  - 答案文末 / 独立文件选项
  - 知识点标注开关
- **Acceptance Criteria Addressed**: AC-15, FR-11.1~11.3
- **Test Requirements**:
  - `rule` TR-17.1: 按模板生成 Word
  - `rule` TR-17.2: 答案分离生成两个文件

## 第三阶段：低优先级

### Task 18: 语音朗读与输入
- **Status**: `pending`
- **Priority**: low
- **Depends On**: Task 1
- **Description**:
  - 增强 `tts_service.py`：音色/语速设置
  - 语音输入（speech_recognition 或 Whisper）
  - 按硬件分级显示/隐藏入口
- **Acceptance Criteria Addressed**: AC-16, FR-12.1~12.3
- **Test Requirements**:
  - `rule` TR-18.1: <8G 语音入口隐藏
  - `rule` TR-18.2: TTS 能朗读

### Task 19: 专注学习模式
- **Status**: `pending`
- **Priority**: low
- **Depends On**: Task 2
- **Description**:
  - 首页"专注模式"按钮：隐藏侧边栏
  - 计时器组件（开始/暂停/结束）
  - 时长写入 study_logs / statistics
- **Acceptance Criteria Addressed**: AC-17, FR-13.1, FR-13.2
- **Test Requirements**:
  - `rule` TR-19.1: 专注模式侧边栏隐藏
  - `rule` TR-19.2: 计时结束写入统计

### Task 20: 插件扩展接口
- **Status**: `pending`
- **Priority**: low
- **Depends On**: None
- **Description**:
  - 新增 `core/plugin_interface.py`：插件基类、加载器
  - `plugins/` 目录结构
  - 新增 `ui/plugin_manager_panel.py`：UI + 接口框架
- **Acceptance Criteria Addressed**: AC-18, FR-14.1, FR-14.2
- **Test Requirements**:
  - `rule` TR-20.1: 示例插件可被发现加载
  - `rule` TR-20.2: 插件管理页显示列表

## 验收与自检

### Task 21: 全量自检与回归
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 1-20
- **Description**:
  - 硬件三档模拟测试
  - 深浅色主题遍历
  - 多账号隔离测试
  - 备份往返测试
  - 并发单任务测试
  - 异常注入测试（断 Ollama/坏 DB/坏备份）
  - 现有功能回归
- **Acceptance Criteria Addressed**: AC-19, AC-20
- **Test Requirements**:
  - `rule` TR-21.1: 所有异常场景不崩溃
  - `rubric` TR-21.2: 主题一致性；scale 1-5；threshold >=4

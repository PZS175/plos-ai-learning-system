-- PLOS AI SQLite schema
-- pragma foreign_keys = ON should be enabled in the connection.

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL DEFAULT '新对话',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    conversation_id INTEGER NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('system', 'user', 'assistant')),
    content TEXT NOT NULL DEFAULT '',
    images TEXT DEFAULT NULL, -- comma-separated image paths
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    filename TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_type TEXT NOT NULL,
    page_count INTEGER DEFAULT 0,
    is_favorite INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_documents_filename ON documents(filename);
CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    document_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    content TEXT NOT NULL DEFAULT '',
    page_num INTEGER DEFAULT 0,
    chapter TEXT DEFAULT '',
    level INTEGER DEFAULT 0,
    vector_id TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_user ON chunks(user_id);

CREATE TABLE IF NOT EXISTS error_book (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    question TEXT NOT NULL DEFAULT '',
    answer TEXT NOT NULL DEFAULT '',
    analysis TEXT NOT NULL DEFAULT '',
    knowledge_tags TEXT NOT NULL DEFAULT '',
    knowledge_points TEXT NOT NULL DEFAULT '[]',
    question_type TEXT NOT NULL DEFAULT '',
    subject TEXT DEFAULT '',
    chapter TEXT DEFAULT '',
    knowledge_point TEXT DEFAULT '',
    difficulty INTEGER DEFAULT 1,
    mastery_level INTEGER NOT NULL DEFAULT 0 CHECK(mastery_level IN (0, 1, 2)),
    is_favorite INTEGER DEFAULT 0,
    image_path TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_errorbook_mastery ON error_book(mastery_level);
CREATE INDEX IF NOT EXISTS idx_errorbook_tags ON error_book(knowledge_tags);
CREATE INDEX IF NOT EXISTS idx_error_book_user ON error_book(user_id);
CREATE INDEX IF NOT EXISTS idx_error_book_user_created ON error_book(user_id, created_at);

CREATE TABLE IF NOT EXISTS flashcards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    front_content TEXT NOT NULL DEFAULT '',
    back_content TEXT NOT NULL DEFAULT '',
    front_image TEXT DEFAULT NULL,
    card_type TEXT NOT NULL DEFAULT 'text' CHECK(card_type IN ('text', 'image')),
    subject TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    ease REAL NOT NULL DEFAULT 2.5,
    interval INTEGER NOT NULL DEFAULT 0,
    repetitions INTEGER NOT NULL DEFAULT 0,
    next_review TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_flashcards_review ON flashcards(next_review);
CREATE INDEX IF NOT EXISTS idx_flashcards_user ON flashcards(user_id);

CREATE TABLE IF NOT EXISTS study_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    action_type TEXT NOT NULL DEFAULT 'chat',
    duration INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_study_logs_created ON study_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_study_logs_user_created ON study_logs(user_id, created_at);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 多用户与扩展功能表（2026-08 迭代新增）
-- ============================================================

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    nickname TEXT DEFAULT '',
    avatar TEXT DEFAULT NULL,
    password_hash TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

CREATE TABLE IF NOT EXISTS study_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL DEFAULT '',
    goal TEXT DEFAULT '',
    weak_subjects TEXT DEFAULT '',
    daily_minutes INTEGER DEFAULT 60,
    start_date TEXT DEFAULT '',
    end_date TEXT DEFAULT '',
    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'completed', 'archived')),
    progress INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_study_plans_user ON study_plans(user_id);

CREATE TABLE IF NOT EXISTS study_plan_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL DEFAULT '',
    description TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    estimated_minutes INTEGER DEFAULT 0,
    is_completed INTEGER DEFAULT 0,
    due_date TEXT DEFAULT '',
    linked_flashcard_id INTEGER DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (plan_id) REFERENCES study_plans(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_study_plan_tasks_plan ON study_plan_tasks(plan_id);
CREATE INDEX IF NOT EXISTS idx_study_plan_tasks_user ON study_plan_tasks(user_id);

CREATE TABLE IF NOT EXISTS note_annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    target_type TEXT NOT NULL CHECK(target_type IN ('error_book', 'document', 'flashcard', 'chat')),
    target_id INTEGER NOT NULL,
    selected_text TEXT DEFAULT '',
    note_content TEXT DEFAULT '',
    highlight_color TEXT DEFAULT '#F59E0B',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_note_annotations_target ON note_annotations(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_note_annotations_user ON note_annotations(user_id);

-- ============================================================
-- 术语词典（中优先级4）
-- ============================================================

CREATE TABLE IF NOT EXISTS terms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    term TEXT NOT NULL DEFAULT '',
    definition TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT '',
    source_id INTEGER NOT NULL DEFAULT 0,
    context TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_terms_user ON terms(user_id);
CREATE INDEX IF NOT EXISTS idx_terms_term ON terms(term);
CREATE INDEX IF NOT EXISTS idx_terms_source ON terms(source_type, source_id);

--- ============================================================
--- 主观题批改记录（高优先级4）
--- ============================================================

CREATE TABLE IF NOT EXISTS subjective_grades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    error_id INTEGER NOT NULL DEFAULT 0,
    question_id INTEGER NOT NULL DEFAULT 0,
    user_answer TEXT NOT NULL DEFAULT '',
    score REAL NOT NULL DEFAULT 0,
    total_score REAL NOT NULL DEFAULT 0,
    scoring_points TEXT NOT NULL DEFAULT '[]',
    lost_points TEXT NOT NULL DEFAULT '[]',
    error_reasons TEXT NOT NULL DEFAULT '[]',
    improvement TEXT NOT NULL DEFAULT '',
    feedback TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_subjective_grades_user ON subjective_grades(user_id);
CREATE INDEX IF NOT EXISTS idx_subjective_grades_error ON subjective_grades(error_id);

-- ============================================================
-- 学习计划动态调优变更记录（高优先级5）
-- ============================================================

CREATE TABLE IF NOT EXISTS study_plan_adjustments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL DEFAULT 0,
    trigger_type TEXT NOT NULL DEFAULT 'error_added' CHECK(trigger_type IN ('error_added', 'mastery_changed', 'knowledge_weak', 'manual')),
    trigger_desc TEXT NOT NULL DEFAULT '',
    adjustment_desc TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_study_plan_adjustments_plan ON study_plan_adjustments(plan_id);
CREATE INDEX IF NOT EXISTS idx_study_plan_adjustments_user ON study_plan_adjustments(user_id);

CREATE TABLE IF NOT EXISTS statistics_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    record_type TEXT NOT NULL CHECK(record_type IN ('study_duration', 'error_count', 'flashcard_review', 'subject_distribution')),
    record_date TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    value REAL DEFAULT 0,
    detail TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_statistics_user_date ON statistics_records(user_id, record_date);
CREATE INDEX IF NOT EXISTS idx_statistics_type ON statistics_records(record_type);

CREATE TABLE IF NOT EXISTS mindmaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    target_type TEXT NOT NULL CHECK(target_type IN ('error_book', 'document')),
    target_id INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    graph_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mindmaps_target ON mindmaps(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_mindmaps_user ON mindmaps(user_id);

CREATE TABLE IF NOT EXISTS ocr_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    source_type TEXT DEFAULT 'file',
    source_path TEXT DEFAULT '',
    recognized_text TEXT DEFAULT '',
    imported_error_ids TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ocr_records_user ON ocr_records(user_id);

CREATE TABLE IF NOT EXISTS user_stat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    knowledge_point TEXT NOT NULL DEFAULT '',
    error_count INTEGER NOT NULL DEFAULT 0,
    total_count INTEGER NOT NULL DEFAULT 0,
    error_rate REAL NOT NULL DEFAULT 0.0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_user_stat_user_point ON user_stat(user_id, knowledge_point);
CREATE INDEX IF NOT EXISTS idx_user_stat_user_rate ON user_stat(user_id, error_rate);
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_stat_user_point_uq ON user_stat(user_id, knowledge_point);

-- ============================================================
-- 自动出题与自适应练习（高优先级2）
-- ============================================================

CREATE TABLE IF NOT EXISTS practice_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    knowledge_point TEXT NOT NULL DEFAULT '',
    question_type TEXT NOT NULL DEFAULT 'choice' CHECK(question_type IN ('choice', 'fill', 'judge', 'short', 'open')),
    difficulty INTEGER NOT NULL DEFAULT 3,
    question TEXT NOT NULL DEFAULT '',
    options TEXT NOT NULL DEFAULT '[]',
    answer TEXT NOT NULL DEFAULT '',
    analysis TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_practice_questions_user ON practice_questions(user_id);
CREATE INDEX IF NOT EXISTS idx_practice_questions_kp ON practice_questions(user_id, knowledge_point);

CREATE TABLE IF NOT EXISTS practice_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    question_id INTEGER NOT NULL,
    knowledge_point TEXT NOT NULL DEFAULT '',
    user_answer TEXT NOT NULL DEFAULT '',
    is_correct INTEGER NOT NULL DEFAULT 0,
    score REAL NOT NULL DEFAULT 0,
    feedback TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (question_id) REFERENCES practice_questions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_practice_records_user ON practice_records(user_id);
CREATE INDEX IF NOT EXISTS idx_practice_records_question ON practice_records(question_id);

-- ============================================================
-- 试卷生成与导出（高优先级3）
-- ============================================================

CREATE TABLE IF NOT EXISTS exam_papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL DEFAULT '',
    knowledge_points TEXT NOT NULL DEFAULT '[]',
    type_counts TEXT NOT NULL DEFAULT '{}',
    difficulty_min INTEGER NOT NULL DEFAULT 1,
    difficulty_max INTEGER NOT NULL DEFAULT 5,
    total_score INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_exam_papers_user ON exam_papers(user_id);

CREATE TABLE IF NOT EXISTS exam_paper_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    score INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_exam_paper_questions_paper ON exam_paper_questions(paper_id);

-- ============================================================
-- 低优先级功能预留数据表（仅做字段与 UI 占位，暂不实现业务逻辑）
-- ============================================================

-- 录音转文字 ASR
CREATE TABLE IF NOT EXISTS asr_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    audio_path TEXT NOT NULL DEFAULT '',
    transcript TEXT NOT NULL DEFAULT '',
    duration_seconds REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'processing', 'completed', 'failed')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_asr_records_user ON asr_records(user_id);

-- AI 生成示意图（流程图/概念图/Mermaid）
CREATE TABLE IF NOT EXISTS diagram_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    prompt TEXT NOT NULL DEFAULT '',
    diagram_type TEXT NOT NULL DEFAULT 'flowchart' CHECK(diagram_type IN ('flowchart', 'concept', 'mindmap', 'mermaid')),
    source_type TEXT NOT NULL DEFAULT '',
    source_id INTEGER NOT NULL DEFAULT 0,
    mermaid_code TEXT NOT NULL DEFAULT '',
    svg_data TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_diagram_records_user ON diagram_records(user_id);
CREATE INDEX IF NOT EXISTS idx_diagram_records_source ON diagram_records(source_type, source_id);

-- 学习包导入分享（小组学习）
CREATE TABLE IF NOT EXISTS learning_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    content_json TEXT NOT NULL DEFAULT '{}',
    is_shared INTEGER NOT NULL DEFAULT 0,
    share_code TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_learning_packages_user ON learning_packages(user_id);
CREATE INDEX IF NOT EXISTS idx_learning_packages_share ON learning_packages(share_code);

-- 教学风格精细配置
CREATE TABLE IF NOT EXISTS teaching_style_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0 UNIQUE,
    style TEXT NOT NULL DEFAULT 'encouraging' CHECK(style IN ('strict', 'encouraging', 'concise', 'socratic')),
    enabled INTEGER NOT NULL DEFAULT 0,
    config_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_teaching_style_user ON teaching_style_config(user_id);

-- ============================================================
-- 新增模块数据表：富文本笔记 / 艾宾浩斯复习 / 教材离线缓存
-- （与各服务 _ensure_table 定义保持一致，确保全新库也存在，
--   以便备份恢复、删号级联等场景能正常操作这些表）
-- ============================================================

-- 富文本笔记
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    notebook TEXT NOT NULL DEFAULT '默认笔记本',
    title TEXT NOT NULL DEFAULT '无标题笔记',
    content_html TEXT NOT NULL DEFAULT '',
    content_text TEXT NOT NULL DEFAULT '',
    linked_kp TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id);
CREATE INDEX IF NOT EXISTS idx_notes_nb ON notes(user_id, notebook);

-- 艾宾浩斯复习计划
CREATE TABLE IF NOT EXISTS review_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    source_type TEXT NOT NULL DEFAULT 'practice',
    source_id INTEGER NOT NULL,
    last_review TIMESTAMP DEFAULT NULL,
    next_review TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    interval_days INTEGER NOT NULL DEFAULT 1,
    interval_index INTEGER NOT NULL DEFAULT 0,
    difficulty REAL NOT NULL DEFAULT 0.5,
    correct_count INTEGER NOT NULL DEFAULT 0,
    wrong_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, source_type, source_id)
);

CREATE INDEX IF NOT EXISTS idx_review_schedules_user_next
    ON review_schedules(user_id, next_review);

-- 教材离线缓存
CREATE TABLE IF NOT EXISTS textbook_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_size INTEGER NOT NULL DEFAULT 0,
    downloaded_at TEXT NOT NULL,
    subject TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_textbook_cache_user ON textbook_cache(user_id);

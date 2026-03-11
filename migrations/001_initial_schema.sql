-- =============================================================================
-- あしあとプロジェクト - Supabase 初期スキーマ
-- 適用方法: Supabase Dashboard → SQL Editor に貼り付けて実行
-- =============================================================================

-- updated_at 自動更新トリガー用関数
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- マスタ / ルックアップテーブル
-- =============================================================================

-- 学校種別（小学校・中学校・高校など）
CREATE TABLE IF NOT EXISTS school_types (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code       TEXT NOT NULL UNIQUE,   -- '小学校', '中学校', '高校'
  label      TEXT NOT NULL,
  sort_order INT  NOT NULL DEFAULT 0
);

-- 学校（具体的な学校エンティティ）
CREATE TABLE IF NOT EXISTS schools (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name           TEXT NOT NULL UNIQUE,
  school_type_id UUID NOT NULL REFERENCES school_types(id),
  address        TEXT,
  contact        TEXT,
  notes          TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE TRIGGER schools_updated_at
  BEFORE UPDATE ON schools
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 観点マスタ（指導要録の3観点）
CREATE TABLE IF NOT EXISTS viewpoints (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code        TEXT NOT NULL UNIQUE,  -- '知識・技能' など
  label       TEXT NOT NULL,
  description TEXT,
  sort_order  INT  NOT NULL DEFAULT 0
);

-- 活動場所マスタ
CREATE TABLE IF NOT EXISTS locations (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name       TEXT NOT NULL UNIQUE,
  address    TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 活動種別マスタ（自然観察・火起こし・昼食調理 など）
CREATE TABLE IF NOT EXISTS activity_types (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT NOT NULL UNIQUE,
  description TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- エンティティテーブル
-- =============================================================================

-- 支援者（スタッフ）
CREATE TABLE IF NOT EXISTS supporters (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name       TEXT NOT NULL UNIQUE,
  role       TEXT,
  is_active  BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE TRIGGER supporters_updated_at
  BEFORE UPDATE ON supporters
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 児童（school_id で通っている学校を参照）
CREATE TABLE IF NOT EXISTS children (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name            TEXT NOT NULL UNIQUE,
  school_id       UUID REFERENCES schools(id),   -- 任意: 入学時に紐付け
  grade           INT,                            -- 学年
  enrollment_date DATE,
  is_active       BOOLEAN NOT NULL DEFAULT TRUE,
  notes           TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE TRIGGER children_updated_at
  BEFORE UPDATE ON children
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- =============================================================================
-- セッション関連
-- =============================================================================

-- セッション（1回の活動）
CREATE TABLE IF NOT EXISTS sessions (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  date             DATE NOT NULL,
  location_id      UUID REFERENCES locations(id),
  activity_type_id UUID REFERENCES activity_types(id),
  activity_detail  TEXT,         -- 自由記述の活動補足（activity_typeと併用可）
  notes            TEXT,
  imported_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(date);

-- セッション × 児童 参加テーブル（多対多）
CREATE TABLE IF NOT EXISTS session_children (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  child_id   UUID NOT NULL REFERENCES children(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(session_id, child_id)
);

CREATE INDEX IF NOT EXISTS idx_session_children_session ON session_children(session_id);
CREATE INDEX IF NOT EXISTS idx_session_children_child   ON session_children(child_id);

-- セッション × 支援者 担当テーブル（多対多）
CREATE TABLE IF NOT EXISTS session_supporters (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id   UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  supporter_id UUID NOT NULL REFERENCES supporters(id),
  role         TEXT,    -- 'lead', 'assistant' など
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(session_id, supporter_id)
);

CREATE INDEX IF NOT EXISTS idx_session_supporters_session   ON session_supporters(session_id);
CREATE INDEX IF NOT EXISTS idx_session_supporters_supporter ON session_supporters(supporter_id);

-- =============================================================================
-- エビデンス（根拠発言）
-- =============================================================================

CREATE TABLE IF NOT EXISTS session_evidence (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id   UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  child_id     UUID NOT NULL REFERENCES children(id),
  viewpoint_id UUID NOT NULL REFERENCES viewpoints(id),
  utterance    TEXT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_evidence_child   ON session_evidence(child_id);
CREATE INDEX IF NOT EXISTS idx_evidence_session ON session_evidence(session_id);

-- =============================================================================
-- 個別支援計画
-- =============================================================================

CREATE TABLE IF NOT EXISTS support_plans (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  child_id     UUID NOT NULL REFERENCES children(id),
  version      INT  NOT NULL DEFAULT 1,
  period_start TEXT,            -- 例: '2026年4月'
  period_end   TEXT,            -- 例: '2026年6月'
  content      TEXT NOT NULL,   -- Markdown 本文
  status       TEXT NOT NULL DEFAULT 'active'
               CHECK(status IN ('active', 'archived', 'draft')),
  created_by   UUID REFERENCES supporters(id),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(child_id, version)
);

CREATE INDEX IF NOT EXISTS idx_plans_child ON support_plans(child_id);

CREATE OR REPLACE TRIGGER support_plans_updated_at
  BEFORE UPDATE ON support_plans
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 計画目標（goals_json を正規化: 1行 = 1観点の目標）
CREATE TABLE IF NOT EXISTS support_plan_goals (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  support_plan_id UUID NOT NULL REFERENCES support_plans(id) ON DELETE CASCADE,
  viewpoint_id    UUID REFERENCES viewpoints(id),
  goal_text       TEXT NOT NULL,
  is_achieved     BOOLEAN NOT NULL DEFAULT FALSE,
  sort_order      INT  NOT NULL DEFAULT 0,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plan_goals_plan ON support_plan_goals(support_plan_id);

-- =============================================================================
-- 将来拡張: 文字起こし管理
-- =============================================================================

CREATE TABLE IF NOT EXISTS transcripts (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id        UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  original_filename TEXT,
  whisper_model     TEXT,
  pyannote_version  TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 文字起こしセグメント（話者・時刻付き）
CREATE TABLE IF NOT EXISTS transcript_segments (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  transcript_id UUID NOT NULL REFERENCES transcripts(id) ON DELETE CASCADE,
  start_time    FLOAT,
  end_time      FLOAT,
  speaker_label TEXT,           -- pyannote 元ラベル
  child_id      UUID REFERENCES children(id),  -- マッピング後（nullable）
  text          TEXT NOT NULL,
  sort_order    INT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_segments_transcript ON transcript_segments(transcript_id);

-- =============================================================================
-- 将来拡張: 生成レポート管理
-- =============================================================================

CREATE TABLE IF NOT EXISTS reports (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id  UUID REFERENCES sessions(id),
  child_id    UUID REFERENCES children(id),
  report_type TEXT NOT NULL,    -- '校長向け', '担任向け' など
  content     TEXT NOT NULL,    -- Markdown 本文
  llm_model   TEXT,
  status      TEXT NOT NULL DEFAULT 'draft'
              CHECK(status IN ('draft', 'reviewed', 'submitted')),
  reviewed_by UUID REFERENCES supporters(id),
  reviewed_at TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- 初期マスタデータ（冪等: 重複挿入は無視）
-- =============================================================================

INSERT INTO school_types (code, label, sort_order) VALUES
  ('小学校', '小学校', 1),
  ('中学校', '中学校', 2),
  ('高校',   '高校',   3)
ON CONFLICT (code) DO NOTHING;

INSERT INTO viewpoints (code, label, sort_order) VALUES
  ('知識・技能',                 '知識・技能',                 1),
  ('思考・判断・表現',           '思考・判断・表現',           2),
  ('主体的に学習に取り組む態度', '主体的に学習に取り組む態度', 3)
ON CONFLICT (code) DO NOTHING;

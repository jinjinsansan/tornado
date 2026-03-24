-- TornadoAI - Supabase テーブル作成
-- SQL Editorに貼って実行してください

-- ユーザー
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    line_user_id TEXT UNIQUE,
    display_name TEXT,
    plan TEXT DEFAULT 'free',
    plan_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    last_active_at TIMESTAMPTZ DEFAULT now()
);

-- 招待コード（事前発行してログインに使用）
CREATE TABLE invite_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code TEXT UNIQUE NOT NULL,
    used_by UUID REFERENCES users(id) ON DELETE SET NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- WIN5対象レース（毎週更新）
CREATE TABLE win5_races (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date TEXT NOT NULL,
    race_order INT NOT NULL,
    race_id TEXT NOT NULL,
    venue TEXT NOT NULL,
    race_number INT NOT NULL,
    race_name TEXT,
    distance TEXT,
    field_size INT,
    volatility_rank INT,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(date, race_order)
);

-- 全馬スコア
CREATE TABLE win5_horse_scores (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    win5_race_id UUID REFERENCES win5_races(id) ON DELETE CASCADE,
    horse_number INT NOT NULL,
    horse_name TEXT NOT NULL,
    ai_win_prob FLOAT,
    market_prob FLOAT,
    value_score FLOAT,
    odds FLOAT,
    popularity_rank INT,
    engine_ranks JSONB,
    total_score FLOAT
);

-- ユーザーの買い目パターン
CREATE TABLE win5_tickets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    budget INT,
    target_payout INT,
    risk_level TEXT,
    ticket_data JSONB,
    total_combinations INT,
    expected_value FLOAT,
    hit_probability FLOAT,
    scenario_type TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- WIN5結果（毎週更新）
CREATE TABLE win5_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date TEXT UNIQUE NOT NULL,
    winners JSONB,
    payout INT,
    carryover INT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- ユーザーの成績履歴
CREATE TABLE win5_user_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    ticket_id UUID REFERENCES win5_tickets(id) ON DELETE SET NULL,
    is_hit BOOLEAN DEFAULT FALSE,
    payout INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, date, ticket_id)
);

-- インデックス
CREATE INDEX idx_win5_races_date ON win5_races(date);
CREATE INDEX idx_win5_horse_scores_race ON win5_horse_scores(win5_race_id);
CREATE INDEX idx_win5_tickets_user ON win5_tickets(user_id, date);
CREATE INDEX idx_win5_user_history_user ON win5_user_history(user_id);

-- RLS有効化
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE invite_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE win5_races ENABLE ROW LEVEL SECURITY;
ALTER TABLE win5_horse_scores ENABLE ROW LEVEL SECURITY;
ALTER TABLE win5_tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE win5_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE win5_user_history ENABLE ROW LEVEL SECURITY;

-- サービスロールは全テーブルフルアクセス
CREATE POLICY "Service role full access" ON users FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON invite_codes FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON win5_races FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON win5_horse_scores FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON win5_tickets FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON win5_results FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON win5_user_history FOR ALL USING (true) WITH CHECK (true);

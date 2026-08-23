-- fsoft-ai — schema khởi tạo. SPEC muc 7.
-- Chạy tự động lúc khởi động nếu bảng chưa có.

-- Thẻ và vector trong cùng một bảng.
-- Không còn MySQL để join, nên phải tự giữ đủ text.
CREATE TABLE IF NOT EXISTS card (
    card_id           INTEGER PRIMARY KEY,
    deck_id           INTEGER NOT NULL,
    deck_title        TEXT,
    word              TEXT    NOT NULL,
    word_lower        TEXT    NOT NULL,   -- cho tầng khớp chính xác
    phonetic          TEXT,
    part_of_speech    TEXT,
    meaning           TEXT    NOT NULL,
    definition_en     TEXT,
    example_sentence  TEXT,
    example_meaning   TEXT,
    audio_url         TEXT,
    note              TEXT,
    source_updated_at TEXT    NOT NULL,   -- ISO-8601 UTC lấy từ backend
    content_hash      TEXT    NOT NULL,   -- SHA-256 của text đã dựng
    model_version     TEXT    NOT NULL,   -- 'multilingual-e5-small@t1'
    vector            BLOB    NOT NULL,   -- float32 little-endian, đã L2-normalize
    synced_at         TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_card_deck ON card(deck_id);
CREATE INDEX IF NOT EXISTS idx_card_word ON card(word_lower);

-- Con trỏ đồng bộ
-- key dùng: last_sync_ts, last_full_sweep_at, last_sync_error
CREATE TABLE IF NOT EXISTS sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Nhật ký gọi LLM
CREATE TABLE IF NOT EXISTS usage_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    task              TEXT    NOT NULL,   -- CHAT | REWRITE | QUIZ | VOCAB_EXTRACT | VOCAB_GENERATE | EMBED
    provider          TEXT    NOT NULL,
    model             TEXT    NOT NULL,
    answer_source     TEXT,
    intent            TEXT,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms        INTEGER NOT NULL,
    success           INTEGER NOT NULL,   -- 0 | 1
    error_code        TEXT,
    created_at        TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_usage_created ON usage_log(created_at);

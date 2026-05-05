-- VerseSync Bible engine schema
--
-- Idempotent: every CREATE uses IF NOT EXISTS so re-running is safe.
-- Verse uniqueness is enforced by (translation, book, chapter, verse).

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS translations (
    code        TEXT PRIMARY KEY,        -- "KJV", "WEB", "YOR"
    name        TEXT NOT NULL,           -- "King James Version"
    language    TEXT NOT NULL,           -- ISO 639-1: "en", "yo"
    license     TEXT NOT NULL,           -- "Public Domain", "CC BY-SA 4.0"
    copyright   TEXT,                    -- attribution string, may be NULL for PD
    source_url  TEXT,
    ingested_at TEXT NOT NULL            -- ISO 8601 UTC
);

CREATE TABLE IF NOT EXISTS books (
    code      TEXT PRIMARY KEY,          -- USFM 3-letter, e.g. "JHN"
    ord       INTEGER NOT NULL UNIQUE,   -- 1..66
    name_en   TEXT NOT NULL,             -- "John"
    name_yo   TEXT,                      -- populated from YOR \h header
    testament TEXT NOT NULL CHECK (testament IN ('OT', 'NT'))
);

CREATE TABLE IF NOT EXISTS verses (
    id          INTEGER PRIMARY KEY,
    translation TEXT NOT NULL,
    book        TEXT NOT NULL,
    chapter     INTEGER NOT NULL,
    verse       INTEGER NOT NULL,
    text        TEXT NOT NULL,
    UNIQUE (translation, book, chapter, verse),
    FOREIGN KEY (translation) REFERENCES translations(code) ON DELETE CASCADE,
    FOREIGN KEY (book)        REFERENCES books(code)
);

CREATE INDEX IF NOT EXISTS idx_verses_lookup
    ON verses (translation, book, chapter, verse);

CREATE INDEX IF NOT EXISTS idx_verses_book_ch
    ON verses (book, chapter);

-- Full-text search (used by Module 3 for translation auto-match
-- when the preacher reads a verse aloud).
CREATE VIRTUAL TABLE IF NOT EXISTS verses_fts USING fts5(
    text,
    content='verses',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 0'
);

CREATE TRIGGER IF NOT EXISTS verses_ai AFTER INSERT ON verses BEGIN
    INSERT INTO verses_fts (rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS verses_ad AFTER DELETE ON verses BEGIN
    INSERT INTO verses_fts (verses_fts, rowid, text)
        VALUES ('delete', old.id, old.text);
END;

CREATE TRIGGER IF NOT EXISTS verses_au AFTER UPDATE ON verses BEGIN
    INSERT INTO verses_fts (verses_fts, rowid, text)
        VALUES ('delete', old.id, old.text);
    INSERT INTO verses_fts (rowid, text) VALUES (new.id, new.text);
END;

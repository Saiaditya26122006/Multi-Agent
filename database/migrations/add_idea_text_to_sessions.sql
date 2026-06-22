-- EPI-34: Add idea_text column to sessions for topic-change detection
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS idea_text TEXT;

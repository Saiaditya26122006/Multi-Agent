-- Migration: Add channel support to messages table
-- Allows tracking whether a message came from Telegram or Web

CREATE TYPE message_channel AS ENUM ('telegram', 'web');

ALTER TABLE messages
    ADD COLUMN channel message_channel NOT NULL DEFAULT 'telegram';

-- Make telegram_message_id nullable since web messages won't have one
ALTER TABLE messages
    ALTER COLUMN telegram_message_id DROP NOT NULL;

-- Add a unique index only for non-null telegram_message_id (dedup still works)
ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_telegram_message_id_key;
CREATE UNIQUE INDEX messages_telegram_message_id_unique
    ON messages (telegram_message_id)
    WHERE telegram_message_id IS NOT NULL;

-- Add channel to events_logs for observability
ALTER TABLE events_logs
    ADD COLUMN channel message_channel DEFAULT 'telegram';

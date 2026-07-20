-- Migration: Add channel support to messages table
-- Allows tracking message source

CREATE TYPE message_channel AS ENUM ('web', 'api');

ALTER TABLE messages
    ADD COLUMN channel message_channel NOT NULL DEFAULT 'web';

-- Ensure message_id is nullable for API messages
ALTER TABLE messages
    ALTER COLUMN message_id DROP NOT NULL;

-- Add a unique index only for non-null message_id (dedup still works)
ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_message_id_key;
CREATE UNIQUE INDEX messages_message_id_unique
    ON messages (message_id)
    WHERE message_id IS NOT NULL;

-- Add channel to events_logs for observability
ALTER TABLE events_logs
    ADD COLUMN channel message_channel DEFAULT 'web';

-- Migration: Add telegram_chat_id to ceo_context table
-- This allows L0 Input Guard to validate message senders

ALTER TABLE ceo_context
ADD COLUMN IF NOT EXISTS telegram_chat_id BIGINT;

-- Add index for performance
CREATE INDEX IF NOT EXISTS idx_ceo_context_telegram_chat_id ON ceo_context(telegram_chat_id);

-- Update the test CEO with the test chat ID
-- NOTE: Update this with the actual CEO's telegram chat ID
UPDATE ceo_context
SET telegram_chat_id = 8866294087
WHERE name = 'Alex Chen';

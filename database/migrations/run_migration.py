"""
Script to run database migrations using psycopg2 or similar.
For Supabase, you may need to run migrations via the SQL Editor in the dashboard.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client

# Load environment variables
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

print("=" * 60)
print("Database Migration Runner")
print("=" * 60)

# Check current CEO context
print("\n1. Checking current ceo_context data...")
try:
    result = supabase.table("ceo_context").select("*").execute()
    if result.data:
        print(f"✓ Found {len(result.data)} CEO context record(s)")
        for ceo in result.data:
            print(f"  - {ceo.get('name')} at {ceo.get('company')}")
            if 'chat_id' in ceo:
                print(f"    chat_id: {ceo.get('chat_id')}")
            else:
                print(f"    chat_id: NOT SET (column may not exist)")
    else:
        print("⚠ No CEO context found")
except Exception as e:
    print(f"✗ Error: {e}")

print("\n" + "=" * 60)
print("Migration Instructions:")
print("=" * 60)
print("""
To add chat_id field to ceo_context:

1. Go to your Supabase Dashboard
2. Navigate to: SQL Editor
3. Run this SQL:

ALTER TABLE ceo_context
ADD COLUMN IF NOT EXISTS chat_id TEXT;

CREATE INDEX IF NOT EXISTS idx_ceo_context_chat_id
ON ceo_context(chat_id);

UPDATE ceo_context
SET chat_id = 'your-unique-chat-id'
WHERE name = 'Alex Chen';

4. Then re-run this script to verify
""")
print("=" * 60)

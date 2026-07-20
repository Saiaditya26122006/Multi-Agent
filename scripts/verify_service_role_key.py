#!/usr/bin/env python3
"""
Verify Supabase service role key is valid.
Run this to test if your service_role key is correct.
"""

import os
from dotenv import load_dotenv
from pathlib import Path
from supabase import create_client

# Load environment variables
dotenv_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=dotenv_path)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

print("=" * 60)
print("Supabase Service Role Key Verification")
print("=" * 60)

if not SUPABASE_URL:
    print("❌ SUPABASE_URL not found in .env")
    exit(1)

if not SUPABASE_SERVICE_ROLE_KEY:
    print("❌ SUPABASE_SERVICE_ROLE_KEY not found in .env")
    print("\nTo fix:")
    print("1. Go to Supabase Dashboard → Settings → API")
    print("2. Copy the 'Service Role' secret key")
    print("3. Add to .env: SUPABASE_SERVICE_ROLE_KEY=<key_here>")
    exit(1)

print(f"✅ SUPABASE_URL: {SUPABASE_URL[:40]}...")
print(f"✅ SUPABASE_SERVICE_ROLE_KEY: {SUPABASE_SERVICE_ROLE_KEY[:20]}...{SUPABASE_SERVICE_ROLE_KEY[-10:]}")

try:
    print("\n🔍 Testing connection...")
    admin_db = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    # Try a simple query
    result = admin_db.table("business_plan_sections").select("id").limit(1).execute()

    print("✅ Connection successful!")
    print(f"✅ Service role key is valid")
    print(f"\n✅ All tests should now pass!")

except Exception as e:
    print(f"❌ Connection failed: {e}")
    print("\nPossible issues:")
    print("1. Service role key is invalid or expired")
    print("2. Service role key is actually the anon key (wrong one)")
    print("3. URL is incorrect")
    print("\nTo fix:")
    print("1. Go to Supabase Dashboard → Settings → API")
    print("2. Make sure you're copying 'Service Role' (not 'anon')")
    print("3. The key should be ~50+ characters long")
    print("4. Paste it exactly into .env as SUPABASE_SERVICE_ROLE_KEY=<key>")
    exit(1)

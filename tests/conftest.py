"""
Pytest configuration for database tests.
Provides fixtures for admin access (bypasses RLS).
"""

import os
import pytest
from dotenv import load_dotenv
from pathlib import Path
from supabase import create_client, Client

# Load environment variables
dotenv_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=dotenv_path)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")


@pytest.fixture
def admin_db():
    """
    Provide admin database client (bypasses RLS).
    Use this for tests that need to INSERT/UPDATE/DELETE test data.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        pytest.skip("SUPABASE_SERVICE_ROLE_KEY not configured for tests")

    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

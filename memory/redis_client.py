"""
Redis Client for Multi-Agent AI System
Provides session state caching and management using Upstash Redis.
"""

import os
import json
from typing import Optional, Dict, Any
from pathlib import Path
from dotenv import load_dotenv
from upstash_redis import Redis

# Load environment variables
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

# Initialize Upstash Redis client
UPSTASH_REDIS_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_REDIS_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")

if not UPSTASH_REDIS_REST_URL or not UPSTASH_REDIS_REST_TOKEN:
    raise ValueError("Missing UPSTASH_REDIS_REST_URL or UPSTASH_REDIS_REST_TOKEN in environment variables")

# Remove quotes if they exist in the environment variables
UPSTASH_REDIS_REST_URL = UPSTASH_REDIS_REST_URL.strip('"\'')
UPSTASH_REDIS_REST_TOKEN = UPSTASH_REDIS_REST_TOKEN.strip('"\'')

redis_client = Redis(url=UPSTASH_REDIS_REST_URL, token=UPSTASH_REDIS_REST_TOKEN)


def _get_session_key(session_id: str) -> str:
    """Generate Redis key for a session."""
    return f"session:{session_id}"


def set_session_state(session_id: str, data: Dict[str, Any], ttl_seconds: int = 86400) -> bool:
    """
    Stores session state as JSON in Redis with TTL.

    Args:
        session_id: UUID of the session
        data: Dictionary containing session state
        ttl_seconds: Time to live in seconds (default: 86400 = 24 hours)

    Returns:
        True if successful, False otherwise
    """
    try:
        key = _get_session_key(session_id)
        json_data = json.dumps(data)
        redis_client.set(key, json_data, ex=ttl_seconds)
        return True
    except Exception as e:
        print(f"Error setting session state for {session_id}: {e}")
        return False


def get_session_state(session_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieves session state from Redis and parses JSON.

    Args:
        session_id: UUID of the session

    Returns:
        Dict containing session state or None if not found
    """
    try:
        key = _get_session_key(session_id)
        data = redis_client.get(key)

        if data is None:
            return None

        # Handle both string and bytes response
        if isinstance(data, bytes):
            data = data.decode('utf-8')

        return json.loads(data)
    except Exception as e:
        print(f"Error getting session state for {session_id}: {e}")
        return None


def delete_session_state(session_id: str) -> bool:
    """
    Deletes session state from Redis.

    Args:
        session_id: UUID of the session

    Returns:
        True if deleted, False otherwise
    """
    try:
        key = _get_session_key(session_id)
        result = redis_client.delete(key)
        return result > 0
    except Exception as e:
        print(f"Error deleting session state for {session_id}: {e}")
        return False


def update_session_field(session_id: str, field: str, value: Any) -> bool:
    """
    Updates a single field in existing session state.

    Args:
        session_id: UUID of the session
        field: Field name to update
        value: New value for the field

    Returns:
        True if successful, False otherwise
    """
    try:
        # Get existing state
        existing_state = get_session_state(session_id)

        if existing_state is None:
            print(f"Session {session_id} does not exist, cannot update field")
            return False

        # Update the field
        existing_state[field] = value

        # Save back to Redis (preserve original TTL by getting it first)
        key = _get_session_key(session_id)
        ttl = redis_client.ttl(key)

        # If TTL is -1 (no expiry) or -2 (key doesn't exist), use default
        if ttl <= 0:
            ttl = 86400

        return set_session_state(session_id, existing_state, ttl_seconds=ttl)
    except Exception as e:
        print(f"Error updating session field for {session_id}: {e}")
        return False


def session_exists(session_id: str) -> bool:
    """
    Checks if a session exists in Redis.

    Args:
        session_id: UUID of the session

    Returns:
        True if session exists, False otherwise
    """
    try:
        key = _get_session_key(session_id)
        result = redis_client.exists(key)
        return bool(result > 0)
    except Exception as e:
        print(f"Error checking if session {session_id} exists: {e}")
        return False

"""
Retry decorator for API calls with exponential backoff.
"""

import time
import functools
from typing import Callable, Any


NON_RETRYABLE_CODES = {"403", "401", "PERMISSION_DENIED", "API_KEY_INVALID"}


def _is_non_retryable(error: Exception) -> bool:
    """Check if an error should not be retried (auth failures, key issues)."""
    error_str = str(error)
    return any(code in error_str for code in NON_RETRYABLE_CODES)


def retry_with_fallback(max_retries: int = 3, wait_seconds: int = 5):
    """
    Decorator that retries a function call with fixed wait between attempts.
    Fails immediately on non-retryable errors (403, 401, key issues).

    Args:
        max_retries: Maximum number of retry attempts
        wait_seconds: Seconds to wait between retries

    Raises:
        Exception: The last exception encountered after all retries fail
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None

            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    print(f"[RETRY] Attempt {attempt}/{max_retries} failed: {str(e)}")

                    if _is_non_retryable(e):
                        print(f"[RETRY] Non-retryable error — failing immediately")
                        break

                    if attempt < max_retries:
                        print(f"[RETRY] Waiting {wait_seconds} seconds before retry...")
                        time.sleep(wait_seconds)
                    else:
                        print(f"[RETRY] All {max_retries} attempts failed")

            raise Exception(f"Failed after {attempt} attempts. Last error: {str(last_exception)}")

        return wrapper
    return decorator

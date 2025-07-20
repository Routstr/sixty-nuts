"""Utilities for integration tests.

Common functions and constants used across integration tests.
"""

import os
import asyncio
from typing import TypeVar, Callable, Any
from functools import wraps

# Type variable for generic async functions
T = TypeVar('T')

# Constants for timeouts and retries
DEFAULT_RETRY_COUNT = 5
DEFAULT_RETRY_DELAY = 2.0
LOCAL_TIMEOUT = 30.0
REMOTE_TIMEOUT = 60.0


def get_relay_wait_time(base_seconds: float = 2.0) -> float:
    """Get appropriate wait time based on service type.
    
    Args:
        base_seconds: Base wait time for local services
        
    Returns:
        Wait time in seconds (longer for public relays due to rate limiting)
    """
    if os.getenv("USE_LOCAL_SERVICES"):
        return base_seconds
    return base_seconds * 3.0  # 3x longer for public relays


def get_timeout() -> float:
    """Get appropriate timeout based on service type.
    
    Returns:
        Timeout in seconds
    """
    return LOCAL_TIMEOUT if os.getenv("USE_LOCAL_SERVICES") else REMOTE_TIMEOUT


async def retry_async(
    func: Callable[..., T],
    *args: Any,
    max_retries: int = DEFAULT_RETRY_COUNT,
    delay: float = DEFAULT_RETRY_DELAY,
    **kwargs: Any
) -> T:
    """Retry an async function with exponential backoff.
    
    Args:
        func: Async function to retry
        *args: Arguments to pass to func
        max_retries: Maximum number of retries
        delay: Initial delay between retries
        **kwargs: Keyword arguments to pass to func
        
    Returns:
        Result of the function call
        
    Raises:
        Exception: The last exception if all retries fail
    """
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            if attempt < max_retries - 1:
                wait_time = delay * (2 ** attempt)  # Exponential backoff
                await asyncio.sleep(wait_time)
            else:
                raise
    
    # This should never be reached but satisfies type checker
    raise last_exception if last_exception else RuntimeError("Retry failed")


def integration_test(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator for integration test methods that adds common setup/teardown.
    
    Args:
        func: Test method to decorate
        
    Returns:
        Decorated test method
    """
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Add initial delay for rate limiting
        await asyncio.sleep(get_relay_wait_time(0.5))
        
        try:
            result = await func(*args, **kwargs)
            return result
        finally:
            # Add cleanup delay
            await asyncio.sleep(get_relay_wait_time(0.5))
    
    return wrapper
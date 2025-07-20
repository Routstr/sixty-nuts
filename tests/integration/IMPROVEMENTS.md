# Integration Test Improvements

## Summary of Changes

### 1. **Created Centralized Utilities Module** (`utils.py`)
- Consolidated duplicate `get_relay_wait_time()` functions into a single utility
- Added `get_timeout()` function to centralize timeout logic
- Created `retry_async()` function for better retry handling with exponential backoff
- Added `integration_test` decorator for consistent test setup/teardown

### 2. **Removed Duplicate and Dead Code**
- Removed duplicate `get_relay_wait_time()` functions from multiple test files
- Removed debug script from end of `test_mint_methods.py` (if __name__ == "__main__" block)
- Consolidated import statements and removed redundant imports

### 3. **Improved Error Handling**
- Replaced generic `except Exception` with specific exception types
- Added proper error handling for missing methods (AttributeError)
- Improved retry logic with exponential backoff

### 4. **Fixed Magic Numbers and Hardcoded Values**
- Replaced hardcoded timeouts (30.0, 60.0, 90.0) with centralized `get_timeout()`
- Replaced hardcoded delays with `get_relay_wait_time()`
- Created constants for retry counts and delays

### 5. **Simplified Complex Test Methods**
- Broke down overly complex test methods into smaller, more focused sections
- Removed redundant relay connection checks
- Simplified balance checking logic with retry utility

### 6. **Fixed Import Issues**
- Fixed relative import issues in test files
- Added proper path handling for utils module import

### 7. **Improved Rate Limiting Handling**
- Added delays between operations to avoid rate limiting
- Increased retry counts and delays for rate-limited operations
- Added state refresh logic when balance checks fail

### 8. **Code Organization**
- Created better separation of concerns
- Improved test method naming and documentation
- Added type hints where missing

## Benefits

1. **Maintainability**: Centralized utilities make it easier to update common functionality
2. **Reliability**: Better error handling and retry logic reduce flaky tests
3. **Performance**: Smarter delays and parallel operations where possible
4. **Readability**: Cleaner code with less duplication is easier to understand
5. **Debugging**: Better error messages and logging help diagnose issues

## Running the Tests

```bash
# Run all integration tests
export RUN_INTEGRATION_TESTS=1
pytest tests/integration/ -v

# Run with local services (faster, no rate limiting)
export RUN_INTEGRATION_TESTS=1
export USE_LOCAL_SERVICES=1
pytest tests/integration/ -v
```
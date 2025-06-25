# Integration Tests

This directory contains integration tests that test the complete wallet functionality against real mint and relay infrastructure.

## Overview

The integration tests verify:

- Wallet creation and initialization
- Minting tokens (creating Lightning invoices)
- Sending and redeeming tokens
- Balance checking and proof validation
- Fee calculation and handling
- Multi-mint operations
- Error handling

## Running Integration Tests

### Method 1: Using the Integration Test Runner (Recommended)

The easiest way to run integration tests is using the automated script that manages Docker containers:

```bash
# From the project root
./run_integration_tests.sh
```

This script will:

1. Start fresh Docker containers (mint + relay)
2. Wait for services to be ready
3. Run all integration tests
4. Clean up containers afterward

### Method 2: Manual Docker + pytest

If you prefer manual control:

```bash
# Start services
docker-compose up -d

# Wait for services to be ready (check logs)
docker-compose logs -f

# In another terminal, run integration tests
RUN_INTEGRATION_TESTS=1 pytest tests/integration/ -v

# Clean up
docker-compose down -v
```

### Method 3: Against External Services

You can run tests against existing services by setting the environment variables:

```bash
export RUN_INTEGRATION_TESTS=1
export TEST_MINT_URL=https://testnut.cashu.space
export TEST_RELAY_URL=wss://relay.damus.io

pytest tests/integration/ -v
```

## Test Categories

### Basic Operations (`TestWalletBasicOperations`)

- Wallet creation and initialization
- Balance checking on empty wallet
- Mint quote creation

### Minting (`TestWalletMinting`)

- Asynchronous minting flow
- Invoice generation

### Transactions (`TestWalletTransactions`)

- Send and redeem flow
- Multiple send operations
- End-to-end token transfers

### Proof Management (`TestWalletProofManagement`)

- Proof validation
- Proof consolidation

### Fee Calculation (`TestWalletFeeCalculation`)

- Input fee calculation
- Fee handling with different rates

### Token Parsing (`TestWalletTokenParsing`)

- CashuA (V3) token parsing
- Invalid token handling

### Error Handling (`TestWalletErrorHandling`)

- Insufficient balance errors
- Invalid currency units

## Test Environment

### Docker Services

The integration tests use these Docker services (defined in `compose.yml`):

- **Cashu Mint**: `localhost:3338` - Local mint for testing token operations
- **Nostr Relay**: `localhost:8080` - Local relay for NIP-60 wallet events

### Fresh State

Each test run uses fresh containers with no persistent data, ensuring:

- Clean mint state (no existing tokens)
- Empty relay state (no existing events)
- Isolated test environment

## Test Configuration

Integration tests are controlled by environment variables:

- `RUN_INTEGRATION_TESTS=1` - Must be set to run integration tests
- Without this variable, integration tests are skipped

This ensures:

- `pytest` alone runs only unit tests
- Integration tests run only when explicitly requested
- CI/CD can control test execution

## Debugging Integration Tests

### View Service Logs

```bash
# View all service logs
docker-compose logs -f

# View specific service
docker-compose logs -f cashu-mint
docker-compose logs -f nostr-relay
```

### Manual Service Testing

```bash
# Test mint directly
curl http://localhost:3338/v1/info

# Test relay (requires WebSocket client)
wscat -c ws://localhost:8080
```

### Running Single Tests

```bash
# Run specific test class
RUN_INTEGRATION_TESTS=1 pytest tests/integration/test_wallet_complete_flow.py::TestWalletBasicOperations -v

# Run specific test method
RUN_INTEGRATION_TESTS=1 pytest tests/integration/test_wallet_complete_flow.py::TestWalletBasicOperations::test_wallet_creation_and_initialization -v
```

## Adding New Integration Tests

When adding new integration tests:

1. Add them to the appropriate test class in `test_wallet_complete_flow.py`
2. Use the `wallet` fixture for basic tests
3. Use the `funded_wallet` fixture for tests requiring balance
4. Mark tests with `@pytest.mark.skip()` if they require manual intervention
5. Ensure tests clean up after themselves

Example test:

```python
async def test_new_feature(self, wallet):
    """Test a new wallet feature."""
    # Setup
    initial_balance = await wallet.get_balance()
    
    # Test action
    result = await wallet.new_feature()
    
    # Assertions
    assert result is not None
    
    # Cleanup (if needed)
    # ...
```

## Troubleshooting

### Services Not Starting

```bash
# Check Docker daemon
docker --version
docker-compose --version

# Check port conflicts
sudo lsof -i :3338
sudo lsof -i :8080

# Force clean state
docker-compose down -v
docker system prune -f
```

### Tests Failing

1. Check service health: `docker-compose logs`
2. Verify services are ready: `curl http://localhost:3338/v1/info`
3. Run tests with more verbose output: `pytest -vvv`
4. Check for port conflicts or firewall issues

### Memory/Performance Issues

The Docker containers are lightweight but may consume resources:

```bash
# Monitor resource usage
docker stats

# Limit resources in compose.yml if needed
```

## Notes

- Integration tests may take longer to run (30-60 seconds)
- Tests require network access for Docker image pulls
- Some tests are skipped by default (require manual intervention like paying invoices)
- The test runner automatically handles container lifecycle

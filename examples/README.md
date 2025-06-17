# Sixty Nuts Examples

This directory contains example scripts demonstrating various features of the sixty_nuts library. Each example serves as both a learning resource and an integration test.

## Prerequisites

Before running the examples, you need to install the dependencies:

1. Install [uv](https://github.com/astral-sh/uv) (a fast Python package manager):

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. Install dependencies using uv:

   ```bash
   # From the project root directory
   uv sync
   ```

This will install all required dependencies including the sixty_nuts library itself.

## Basic Operations

### mint_and_send.py

Shows the basic flow of minting tokens from Lightning and sending Cashu tokens.

```bash
python mint_and_send.py
```

### check_balance_and_proofs.py

Check wallet balance and see detailed breakdown by mint and denomination.

```bash
python check_balance_and_proofs.py
```

### send_to_lightning_address.py

Send tokens to any Lightning Address (LNURL).

```bash
python send_to_lightning_address.py user@getalby.com 1000
```

### clear_wallet.py

Melt all tokens back to Lightning, clearing the wallet.

```bash
python clear_wallet.py
```

## Advanced Token Management

### split_tokens.py

Split tokens into specific denominations for privacy or payment preparation.

```bash
# Split into multiple amounts
python split_tokens.py 100 50 25 10 5

# Prepare exact amount token
python split_tokens.py 137
```

### validate_token.py

Validate tokens before accepting them - essential for merchants.

```bash
# Simple validation
python validate_token.py cashuAey...

# Merchant acceptance flow
python validate_token.py merchant cashuAey... 1000

# Batch validation
python validate_token.py batch token1 token2 token3
```

## Payment Processing

### merchant_accept_token.py

Accept tokens from any mint and automatically swap to your trusted mint.

```bash
python merchant_accept_token.py cashuAey...
```

### monitor_payments.py

Create Lightning invoices and monitor for payment in real-time.

```bash
python monitor_payments.py
```

### one_off_redeem.py

Use temporary wallets for one-off token redemptions without storing keys.

```bash
# Single redemption
python one_off_redeem.py cashuAey... user@getalby.com

# Batch redemption
python one_off_redeem.py user@getalby.com token1 token2 token3
```

## Multi-Mint Operations

### multi_mint_operations.py

Work with multiple mints, check balances per mint, and move funds between mints.

```bash
python multi_mint_operations.py
```

### auto_multi_mint_melt.py

Demonstrates the enhanced melt functionality that automatically consolidates proofs from multiple mints when no single mint has enough balance to pay a Lightning invoice.

```bash
python auto_multi_mint_melt.py
```

Features:
- Automatic invoice amount detection
- Total balance verification across all mints
- Smart mint selection based on balance
- Automatic proof consolidation when needed
- Transparent fee handling

This is particularly useful when your funds are distributed across multiple mints and you need to pay an invoice larger than any single mint's balance.

## Common Patterns

### Error Handling

All examples include proper error handling and user-friendly messages.

### Async/Await

All operations use Python's async/await for efficient concurrent operations.

### Context Managers

Examples use `async with Wallet(...)` to ensure proper resource cleanup.

### Type Hints

Full type annotations for better IDE support and code clarity.

## Testing with Examples

These examples can be used as integration tests:

```bash
# Run all examples (requires funded wallet)
for script in *.py; do
    echo "Running $script..."
    python "$script"
done
```

## Notes

- All examples use the same test wallet key for consistency
- Replace the `nsec` with your own for production use
- Some examples require command-line arguments - run without args to see usage
- Examples demonstrate best practices for the library

# Sixty Nuts - Codebase Structure Overview

## Project Overview
**Sixty Nuts** is a NIP-60 Cashu Wallet Implementation - a lightweight stateless Cashu wallet implementing the Nostr Improvement Proposal 60 for ecash operations over the Nostr protocol.

## Dependencies
- `httpx>=0.28.1` - HTTP client for mint API calls
- `websockets>=15.0.1` - WebSocket client for Nostr relay connections
- `bech32>=1.2.0` - Bech32 encoding/decoding
- `coincurve>=21.0.0` - Elliptic curve cryptography
- `cryptography>=45.0.3` - Cryptographic primitives
- `cbor2>=5.6.5` - CBOR encoding/decoding

## Package Structure

### Main Package: `sixty_nuts/`

#### 1. `__init__.py` (13 lines)
**Public API Exports:**
- `Wallet` - Main wallet class
- `TempWallet` - Temporary wallet for one-off operations
- `redeem_to_lnurl` - Utility function for LNURL redemption

#### 2. `wallet.py` (2,275 lines) - **CORE MODULE**

##### Classes and Types:

**EventKind (IntEnum):**
- `RELAY_RECOMMENDATIONS = 10019` - Nostr relay recommendations
- `Wallet = 17375` - Wallet metadata events
- `Token = 7375` - Unspent proof events
- `History = 7376` - Transaction log events
- `QuoteTracker = 7374` - Mint quote tracker events
- `Delete = 5` - NIP-09 delete events

**ProofDict (TypedDict):**
- `id: str` - Keyset identifier
- `amount: int` - Proof amount
- `secret: str` - Base64 encoded secret
- `C: str` - Signature
- `mint: str | None` - Mint URL tracking

**WalletState (Dataclass):**
- `balance: int` - Current balance
- `proofs: list[ProofDict]` - Available proofs
- `mint_keysets: dict[str, list[dict[str, str]]]` - Mint keyset data
- `proof_to_event_id: dict[str, str] | None` - Proof to event mapping

**WalletError (Exception):**
- Base exception class for wallet operations

##### Main Classes:

**Wallet Class:**

*Constructor and Setup:*
- `__init__()` - Initialize wallet with nsec, mint URLs, currency, etc.
- `aclose()` - Cleanup resources
- `__aenter__()` / `__aexit__()` - Async context manager support
- `create()` - Class method factory

*Cryptographic Helper Methods:*
- `_create_blinded_message()` - Create blinded message for mint
- `_get_mint_pubkey_for_amount()` - Get mint public key for amount
- `_decode_nsec()` - Decode bech32 nsec or hex private key
- `_generate_privkey()` - Generate new secp256k1 private key
- `_get_pubkey()` - Get hex public key (x-only, 32 bytes)
- `_get_pubkey_compressed()` - Get compressed public key (33 bytes)
- `_sign_event()` - Sign Nostr events
- `_compute_event_id()` - Compute Nostr event ID

*NIP-44 Encryption:*
- `_nip44_encrypt()` - Encrypt using NIP-44 v2
- `_nip44_decrypt()` - Decrypt using NIP-44 v2

*Event Management:*
- `_create_event()` - Create unsigned Nostr event
- `_publish_to_relays()` - Publish events to relays
- `_rate_limit_relay_operations()` - Rate limiting for relay ops
- `_estimate_event_size()` - Estimate event size in bytes

*Relay Operations:*
- `_get_relay_connections()` - Get/create relay connections
- `_discover_relays()` - Discover relays from kind:10019 events

*Mint Operations:*
- `_get_mint()` - Get or create mint instance
- `_serialize_proofs_for_token()` - Convert proofs to Cashu token format
- `_parse_cashu_token()` - Parse Cashu token (v3/v4 support)
- `_proofdict_to_mint_proof()` - Convert ProofDict to Mint.Proof
- `_filter_proofs_by_keyset()` - Filter proofs by keyset compatibility

*State Management:*
- `create_wallet_event()` - Create/update wallet metadata event
- `fetch_wallet_state()` - Fetch complete wallet state from relays
- `get_balance()` - Get current balance
- `_compute_proof_y_values()` - Compute Y values for proof validation
- `_is_proof_state_cached()` - Check proof state cache
- `_cache_proof_state()` - Cache proof validation results
- `clear_spent_proof_cache()` - Clear validation cache
- `_validate_proofs_with_cache()` - Validate proofs with caching

*Token Event Management:*
- `publish_token_event()` - Publish encrypted token events
- `_split_large_token_events()` - Split large events into chunks
- `delete_token_event()` - Delete token events via NIP-09

*History Events:*
- `publish_spending_history()` - Publish transaction history

*Core Wallet Operations:*
- `redeem()` - Redeem Cashu token
- `swap_mints()` - Swap tokens between mints
- `create_quote()` - Create Lightning invoice quote
- `check_quote_status()` - Check quote payment status
- `mint_async()` - Mint new tokens asynchronously
- `melt()` - Melt tokens to pay Lightning invoice
- `send()` - Send tokens (create Cashu token)
- `send_to_lnurl()` - Send to Lightning address/LNURL
- `roll_over_proofs()` - Update proof storage events

**TempWallet Class (extends Wallet):**
- `__init__()` - Initialize temporary wallet
- `_encode_nsec()` - Encode private key as bech32 nsec
- `create()` - Factory method for temporary wallets

**Standalone Functions:**
- `redeem_to_lnurl()` - Utility to redeem token directly to LNURL

#### 3. `mint.py` (370 lines) - **MINT API CLIENT**

##### Type Definitions:

**Request/Response Types:**
- `BlindedMessage` - Blinded message for mint signing
- `BlindedSignature` - Mint's blind signature response
- `Proof` - Cashu proof/token structure
- `MintInfo` - Mint information response
- `KeysResponse` - Mint public keys response
- `KeysetsResponse` - Active keysets response
- `PostMintQuoteRequest/Response` - Mint quote operations
- `PostMintRequest/Response` - Token minting
- `PostMeltQuoteRequest/Response` - Melt quote operations
- `PostMeltRequest` - Token melting
- `PostSwapRequest/Response` - Proof swapping
- `PostCheckStateRequest/Response` - Proof state checking
- `PostRestoreRequest/Response` - Proof restoration

##### Classes:

**MintError (Exception):**
- Exception for mint API errors

**Mint Class:**
- `__init__()` - Initialize mint client
- `aclose()` - Close HTTP client
- `_request()` - Make HTTP requests to mint

*Info & Keys:*
- `get_info()` - Get mint information
- `get_keys()` - Get mint public keys
- `get_keysets()` - Get active keyset IDs

*Minting (receive):*
- `create_mint_quote()` - Request Lightning invoice
- `get_mint_quote()` - Check mint quote status
- `mint()` - Mint tokens after payment

*Melting (send):*
- `create_melt_quote()` - Get quote for Lightning payment
- `get_melt_quote()` - Check melt quote status
- `melt()` - Pay Lightning invoice with tokens

*Token Management:*
- `swap()` - Swap proofs for new signatures
- `check_state()` - Check if proofs are spent
- `restore()` - Restore proofs from blinded messages

#### 4. `relay.py` (328 lines) - **NOSTR RELAY CLIENT**

##### Type Definitions:
- `NostrEvent` - Nostr event structure
- `NostrFilter` - REQ subscription filters

##### Classes:

**RelayError (Exception):**
- Exception for relay operations

**NostrRelay Class:**
- `__init__()` - Initialize relay client
- `connect()` - Connect to relay WebSocket
- `disconnect()` - Disconnect from relay
- `_send()` - Send message to relay
- `_recv()` - Receive message from relay

*Event Publishing:*
- `publish_event()` - Publish event to relay

*Event Fetching:*
- `fetch_events()` - Fetch events matching filters

*Subscriptions:*
- `subscribe()` - Subscribe to event stream
- `unsubscribe()` - Close subscription
- `process_messages()` - Process subscription messages

*NIP-60 Helpers:*
- `fetch_wallet_events()` - Fetch wallet-related events
- `fetch_relay_recommendations()` - Get relay recommendations

#### 5. `crypto.py` (414 lines) - **CRYPTOGRAPHIC PRIMITIVES**

##### Functions:

**BDHKE (Blind Diffie-Hellmann Key Exchange):**
- `hash_to_curve()` - Hash message to secp256k1 curve point
- `blind_message()` - Blind message for mint signing
- `unblind_signature()` - Unblind mint signature
- `verify_signature()` - Verify signature validity

##### Classes:

**NIP44Error (Exception):**
- Exception for NIP-44 encryption errors

**NIP44Encrypt Class:**
- `calc_padded_len()` - Calculate padded plaintext length
- `pad()` - Apply NIP-44 padding
- `unpad()` - Remove NIP-44 padding
- `get_conversation_key()` - Derive conversation key from ECDH
- `get_message_keys()` - Derive encryption keys from conversation key
- `hmac_aad()` - HMAC with additional authenticated data
- `chacha20_encrypt()` - ChaCha20 encryption
- `chacha20_decrypt()` - ChaCha20 decryption
- `encrypt()` - Full NIP-44 v2 encryption
- `decrypt()` - Full NIP-44 v2 decryption

#### 6. `lnurl.py` (153 lines) - **LIGHTNING URL SUPPORT**

##### Type Definitions:
- `LNURLData` - LNURL payRequest data structure

##### Classes:

**LNURLError (Exception):**
- Exception for LNURL operations

##### Functions:
- `decode_lnurl()` - Decode various LNURL formats
- `get_lnurl_data()` - Fetch LNURL payRequest data
- `get_lnurl_invoice()` - Request Lightning invoice from LNURL

### Examples Directory (11 files)

**Core Examples:**
- `mint_and_send.py` - Basic minting and sending
- `redeem_token.py` - Token redemption
- `check_balance_and_proofs.py` - Balance and proof checking
- `send_to_lightning_address.py` - Lightning address payments

**Advanced Examples:**
- `multi_mint_operations.py` - Multi-mint operations
- `swap_mints.py` - Cross-mint swapping
- `monitor_payments.py` - Payment monitoring
- `validate_token.py` - Token validation
- `split_tokens.py` - Token splitting
- `merchant_accept_token.py` - Merchant integration
- `clear_wallet.py` - Wallet cleanup

### Tests Directory (7 files)

**Test Coverage:**
- `test_wallet_integration.py` - Full wallet integration tests
- `test_mint.py` - Mint API client tests
- `test_relay.py` - Nostr relay client tests
- `test_nip44.py` - NIP-44 encryption tests
- `test_hash_to_curve.py` - Hash-to-curve function tests
- `test_lnurl.py` - LNURL functionality tests
- `test_temp_wallet.py` - Temporary wallet tests

## Key Features

### Protocol Support
- **NIP-60**: Wallet event storage on Nostr
- **NIP-44**: Event encryption/decryption
- **NIP-09**: Event deletion
- **Cashu Protocol**: v3 and v4 token support
- **Lightning Network**: Invoice creation/payment
- **LNURL**: Lightning address support

### Wallet Operations
- **Minting**: Receive ecash via Lightning payments
- **Melting**: Send ecash via Lightning payments
- **Swapping**: Exchange proofs between mints
- **Token Management**: Send/receive Cashu tokens
- **State Persistence**: Encrypted storage on Nostr relays

### Advanced Features
- **Multi-mint Support**: Work with multiple Cashu mints
- **Proof Validation**: Caching and batch validation
- **Event Splitting**: Handle large token events
- **Rate Limiting**: Prevent relay spam
- **Auto-discovery**: Relay recommendation discovery

## Architecture Patterns

### Async/Await Design
- All I/O operations are asynchronous
- Context manager support for resource cleanup
- Background task support for monitoring

### Event-Driven Architecture
- Nostr events for state persistence
- Encrypted content for privacy
- Rollover mechanism for state updates

### Caching Strategy
- Proof validation caching
- Keyset caching per mint
- Relay connection pooling

### Error Handling
- Custom exception hierarchy
- Graceful degradation for network issues
- Timeout management for operations
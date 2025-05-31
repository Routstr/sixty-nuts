# Sixty Nuts - A NIP-60 Cashu Wallet in Python

A lightweight, stateless Cashu wallet implementation following [NIP-60](https://github.com/nostr-protocol/nips/blob/master/60.md) specification for Nostr-based wallet state management.

## Features

- **NIP-60 Compliant**: Full implementation of the NIP-60 specification
- **NIP-44 Encryption**: Secure encryption using the NIP-44 v2 standard
- **Stateless Design**: Wallet state stored on Nostr relays
- **Multi-Mint Support**: Can work with multiple Cashu mints
- **Async/Await**: Modern Python async implementation

## Installation

```bash
pip install sixty-nuts
```

## Usage

```python
from sixty_nuts import Wallet

async def main():
    # Create wallet with private key (hex or nsec format)
    wallet = Wallet(
        nsec="your_nostr_private_key_hex",
        mint_urls=["https://mint.minibits.cash/Bitcoin"],
        relays=["wss://relay.damus.io", "wss://nos.lol"]
    )
    
    # TODO write example
```

## Architecture

- `wallet.py` - Main wallet implementation
- `crypto.py` - Cryptographic primitives (BDHKE and NIP-44 v2 encryption)
- `mint.py` - Cashu mint API client
- `relay.py` - Nostr relay WebSocket client

## Security Notes

⚠️ **Important**: This implementation includes proper NIP-44 encryption for wallet data stored on relays. However:

- The Cashu blinding implementation is simplified and needs proper BDHKE implementation for production use
- Proof-to-event tracking needs to be implemented for full NIP-60 compliance
- Consider the security limitations of storing wallet state on public relays

## TODO

- [ ] Implement proper BDHKE blinding for Cashu operations
- [ ] Add proof-to-event-id mapping for accurate token event management
- [ ] Implement quote tracking (kind 7374 events)
- [ ] Add better coin selection algorithm
- [ ] Support for P2PK ecash (NIP-61)
- [ ] Add comprehensive test suite

## License

MIT

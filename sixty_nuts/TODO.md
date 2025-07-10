# todo

## minting

nuts mint should request any non defined data
but wallet mint should always select one if not defined
mint and keyset selection
no timeout if timeout=None on wallet mint but cli nuts mint sets a timeout of 3 minutes

## mint and keysets

a wallet has all the mint instances open and each mint has
all the active keysets loaded with the currency information

## send & melt

for spending operations if nonthing specific is defined there should be an algorithm selecting
the most fitting mint and keyset and if none are fitting it should automatically
rebalance in the most optimal way then sending or melting is handleded the same way

## redeem

default should just check if the mint is trusted and redeem all proofs,
if non trusted mint it should swap into the trusted mint active keyset with biggest balance

# currency exchange

metthod that can be used to check rates of swap between keysets
method to do the swap between to keysets of proofs

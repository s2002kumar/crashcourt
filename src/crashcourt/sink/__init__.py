"""The effect sink: an independent ledger of real-world effects (spec sections 7, 8, and 9).

The ledger is the only definition of "happened". It lives in its own process and failure
domain, is never behind Toxiproxy, never deduplicates, and commits before it answers.
"""

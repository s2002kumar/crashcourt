"""The effect sink: an independent ledger of real-world effects.

The ledger is the only definition of "happened". It lives in its own process and failure
domain, is never behind Toxiproxy, never deduplicates, and commits before it answers.
"""

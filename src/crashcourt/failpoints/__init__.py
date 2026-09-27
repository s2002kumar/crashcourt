"""Failpoints: named pause and crash points around any checkpoint saver (spec sections 7 and 8).

Disarmed, the wrapper is a pass-through that only records `saver.*` events (control C4 checks
that it changes nothing). Armed, an action fires exactly at the named point, for the matching
worker, operation, and occurrence, and every hit is logged before its action.
"""

"""Legal fault primitives (spec section 10): signals, network toxics, backend termination, restart.

Every primitive writes `fault.inject` when it takes effect and exactly one `fault.heal` when it
ends. Faults target only worker processes, the Toxiproxy path to the target database, and the
target database container; never the effect sink or the history files (spec section 7).
"""

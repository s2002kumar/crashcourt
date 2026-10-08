"""Legal fault primitives: signals, network toxics, backend termination, restart.

Every primitive writes `fault.inject` before it takes effect and exactly one `fault.heal` when it
ends. Faults target only worker processes, the Toxiproxy path to the target database, and the
target database container; never the effect sink or the history files.
"""

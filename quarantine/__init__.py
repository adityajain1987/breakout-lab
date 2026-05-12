"""Edge-case quarantine — detect, flag, don't drop.

Per Phase 1 office hours Item 3: 12 known data-corruption / signal-distortion risks.
Each check writes a flag to quarantine.db rather than silently filtering — preserves
the audit trail and lets analytics decide whether to exclude or annotate.

Modules:
  store     — SQLite schema + insert + query API
  checks    — pure check functions (DataFrame in, list of flags out)
  run_sweep — CLI that runs all checks across the universe and writes flags

Phase 1 scope (5 checks implemented):
  Tier 1 (must-pass):  check_split_anomaly, check_dummy_ticker
  Tier 2 (flag):       check_circuit_hits, is_fno_expiry (helper, not stored)
  Tier 3 (universe):   check_recent_ipo, check_suspended_periods

Deferred to P0.7b (need data we don't have yet):
  - Bhavcopy holiday-gap detection (needs Bhavcopy fetcher)
  - Block deal time-window flag (needs intraday timestamp)
  - Index inclusion / earnings day tags (need separate calendars)
"""

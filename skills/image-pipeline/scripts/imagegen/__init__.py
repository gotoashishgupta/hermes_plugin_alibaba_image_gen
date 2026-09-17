"""imagegen — the generation driver behind scripts/imagegen.py.

Import discipline (unit-enforced): importing this package and every module in it
touches ONLY the Python standard library at module level. All Hermes imports are
confined to functions inside ``imagegen.hermes_mode``; all network calls go
through ``imagegen.http`` (stdlib urllib). That keeps the skill runnable by any
harness with a bare python3, while still driving Hermes' provider registry when
a checkout is present.
"""

__all__ = ["cli", "envelope"]

"""`recad` — dispatcher for the L1 backend adapters (01_ARCHITECTURE §2, 05_TOOLS).

`recad <backend> <subcommand> …` runs `backends/<backend>/adapter/adapter.py` in a fresh
subprocess with that backend's interpreter, then validates / completes its JSON result.
The dispatcher never imports backend code.
"""

__version__ = "0.1.0"

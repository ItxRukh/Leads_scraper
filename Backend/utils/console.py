"""
utils/console.py — A rich Console that does not crash on Windows
-----------------------------------------------------------------
Windows terminals often use a legacy code page (cp1252) that cannot print
emoji such as ✅ or ❌, and rich then raises UnicodeEncodeError in the middle
of a job. Switching the streams to UTF-8 (unprintable characters replaced,
never fatal) avoids that.
"""

import sys

from rich.console import Console


def make_console() -> Console:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass  # not a real text stream (e.g. captured in tests): leave it alone
    return Console()

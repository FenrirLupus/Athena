"""Puts the project root on sys.path for a suite run as a script.

Every suite needs the same two lines before it can import `core`, `server` or `providers`. Keeping them here means a
suite starts with its own subject instead of a prelude, and the path rule changes in one place.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

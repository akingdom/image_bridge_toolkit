# scripts/__init__.py
"""Shared utilities for patch scripts.

Exists so that patch scripts at the project root can do
`from scripts._patcher import Patch, run` without sys.path games.
"""
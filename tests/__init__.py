"""Marks tests/ as a package so tests/ha/ can use relative imports for its shared helpers.

Without this, `from .conftest import ...` inside tests/ha/ raises "attempted relative import with
no known parent package" -- pytest imports each test module standalone unless the directory is a
real package.
"""

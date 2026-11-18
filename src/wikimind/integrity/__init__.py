"""Integrity tooling: provenance checks, audit writers, and ledger verification.

This package adds application write paths for the four tables that exist in the
frozen schema but had no writer (``events``, ``cost_events``, ``ledger_blocks``,
``jobs``), plus read-only verification commands.  It never issues an LLM call:
there is no call site and no cost-ledger entry for anything in here.

The frozen migrations are authoritative.  No migration 005 is defined here;
every writer fits the columns that already exist in migrations 001-004.
"""

from __future__ import annotations

from .model import IntegrityReport

__all__ = ["IntegrityReport"]

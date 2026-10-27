"""Compile-lane errors."""

from __future__ import annotations


class CompileError(Exception):
    """Base class for compile-lane failures."""


class ProjectNotFoundError(CompileError):
    pass


class AmbiguousProjectError(CompileError):
    pass

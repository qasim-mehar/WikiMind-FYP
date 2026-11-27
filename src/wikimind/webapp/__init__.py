"""A small, local-only web interface prototype for the compiled wiki.

This package is a prototype surface for manual exploration. It is not the Stage 1
product interface: there is no authentication, no multi-user support, and it
binds to the loopback interface by default. All model calls remain metered, and
the interface reuses the existing compile and QA pipelines unchanged.
"""

from __future__ import annotations

__all__ = ["app", "build_service", "create_app"]

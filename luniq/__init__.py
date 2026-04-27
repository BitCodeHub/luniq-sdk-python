"""Luniq server-side SDK for Python.

Track events, identify users, and evaluate feature flags from any Python
backend. Mirrors the @luniq/node SDK on the wire.
"""

from .client import Luniq

__all__ = ["Luniq"]
__version__ = "1.0.0"

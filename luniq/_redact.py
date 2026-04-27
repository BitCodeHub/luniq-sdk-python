"""PII redaction helpers for the Luniq Python SDK.

Mirrors the regex set used by the Node SDK so server-side events look
identical on the wire regardless of language.
"""

from __future__ import annotations

import re
from typing import Any, Dict

# Order matters: card before phone, since 16-digit card numbers can otherwise
# be partially eaten by the phone pattern.
_PII_PATTERNS = [
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[email]"),
    (re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"), "[card]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[ssn]"),
    (re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[phone]"),
]


def redact_value(value: Any) -> Any:
    """Redact PII from a single value. Non-strings pass through unchanged."""
    if not isinstance(value, str):
        return value
    out = value
    for pattern, replacement in _PII_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def redact_object(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Mutate a dict in place, redacting any string values. Returns the dict."""
    for key, val in list(obj.items()):
        if isinstance(val, str):
            obj[key] = redact_value(val)
    return obj

"""Unit tests for the Luniq Python SDK.

Uses unittest.mock to stub the HTTP layer so tests run without network or
external dependencies beyond ``requests``.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from luniq import Luniq
from luniq._redact import redact_value


class FakeResponse:
    def __init__(self, status: int = 200, payload: Any = None) -> None:
        self.status_code = status
        self.ok = 200 <= status < 300
        self._payload = payload if payload is not None else {}

    def json(self) -> Any:
        return self._payload


def make_session(events_status: int = 200, flags_payload: Dict[str, Any] | None = None):
    """Build a session that records every POST and returns canned responses."""
    calls: List[Dict[str, Any]] = []

    def post(url, json=None, headers=None, timeout=None):  # noqa: A002
        calls.append({"url": url, "json": json, "headers": headers})
        if url.endswith("/v1/events"):
            return FakeResponse(events_status)
        if url.endswith("/v1/sdk/flags/evaluate"):
            return FakeResponse(200, flags_payload or {})
        return FakeResponse(404)

    sess = MagicMock()
    sess.post.side_effect = post
    return sess, calls


# --- redaction --------------------------------------------------------------

def test_redaction_strips_email_phone_card_ssn():
    assert redact_value("hello a@b.co") == "hello [email]"
    assert redact_value("call 555-123-4567") == "call [phone]"
    assert redact_value("card 4111 1111 1111 1111") == "card [card]"
    assert redact_value("ssn 123-45-6789") == "ssn [ssn]"
    # Mixed string
    out = redact_value("u@x.io and 555.123.4567")
    assert "[email]" in out and "[phone]" in out


def test_redaction_skips_non_strings():
    assert redact_value(42) == 42
    assert redact_value(None) is None


# --- construction -----------------------------------------------------------

def test_requires_api_key():
    with pytest.raises(ValueError):
        Luniq(api_key="")


def test_endpoint_defaults_and_strips_trailing_slash():
    sess, _ = make_session()
    c = Luniq(api_key="k", session=sess, flush_interval_ms=999_999)
    assert c.endpoint == "https://uselunaai.com"
    c.shutdown()
    c2 = Luniq(api_key="k", endpoint="https://x.test/", session=sess, flush_interval_ms=999_999)
    assert c2.endpoint == "https://x.test"
    c2.shutdown()


# --- track / flush ----------------------------------------------------------

def test_track_buffers_without_flushing():
    sess, calls = make_session()
    c = Luniq(api_key="k", session=sess, flush_interval_ms=999_999)
    c.track("clicked", visitor_id="v1", properties={"x": 1})
    # Background thread shouldn't have fired yet at this interval.
    assert calls == []
    assert len(c._queue) == 1
    ev = c._queue[0]
    assert ev["name"] == "clicked"
    assert ev["visitorId"] == "v1"
    assert ev["properties"]["os_type"] == "SERVER"
    assert ev["properties"]["env"] == "PRD"
    assert ev["properties"]["brand"] == "H"
    assert ev["properties"]["x"] == 1
    c.shutdown()


def test_track_requires_visitor_id():
    sess, _ = make_session()
    c = Luniq(api_key="k", session=sess, flush_interval_ms=999_999)
    with pytest.raises(ValueError):
        c.track("x", visitor_id="")
    c.shutdown()


def test_flush_sends_batch_with_auth_header():
    sess, calls = make_session()
    c = Luniq(api_key="secret", session=sess, flush_interval_ms=999_999)
    c.track("e1", visitor_id="v1")
    c.track("e2", visitor_id="v1")
    c.flush()
    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "https://uselunaai.com/v1/events"
    assert call["headers"]["X-Luniq-Key"] == "secret"
    assert len(call["json"]["events"]) == 2
    assert c._queue == []
    c.shutdown()


def test_flush_batches_in_groups_of_100():
    sess, calls = make_session()
    c = Luniq(api_key="k", session=sess, flush_interval_ms=999_999)
    for i in range(250):
        c.track("e", visitor_id=f"v{i}")
    c.flush()
    sizes = [len(call["json"]["events"]) for call in calls]
    assert sizes == [100, 100, 50]
    c.shutdown()


def test_flush_requeues_on_failure():
    sess, calls = make_session(events_status=500)
    c = Luniq(api_key="k", session=sess, flush_interval_ms=999_999)
    c.track("e", visitor_id="v1")
    c.flush()
    assert len(calls) == 1
    # Event re-queued at the head for the next attempt.
    assert len(c._queue) == 1
    c.shutdown()


def test_max_queue_size_drops_oldest():
    sess, _ = make_session()
    c = Luniq(api_key="k", session=sess, flush_interval_ms=999_999, max_queue_size=3)
    for i in range(5):
        c.track("e", visitor_id=f"v{i}")
    assert len(c._queue) == 3
    # Oldest two were dropped: queue holds v2, v3, v4.
    assert [e["visitorId"] for e in c._queue] == ["v2", "v3", "v4"]
    c.shutdown()


# --- identify ---------------------------------------------------------------

def test_identify_emits_identify_event_with_traits():
    sess, _ = make_session()
    c = Luniq(api_key="k", session=sess, flush_interval_ms=999_999)
    c.identify(visitor_id="v1", account_id="a1", traits={"plan": "pro"})
    assert len(c._queue) == 1
    ev = c._queue[0]
    assert ev["name"] == "$identify"
    assert ev["accountId"] == "a1"
    assert ev["properties"]["plan"] == "pro"
    c.shutdown()


# --- redaction in track -----------------------------------------------------

def test_track_redacts_pii_in_properties():
    sess, _ = make_session()
    c = Luniq(api_key="k", session=sess, flush_interval_ms=999_999)
    c.track("signup", visitor_id="v1", properties={"note": "email me at a@b.co"})
    assert c._queue[0]["properties"]["note"] == "email me at [email]"
    c.shutdown()


def test_track_redact_pii_can_be_disabled():
    sess, _ = make_session()
    c = Luniq(api_key="k", session=sess, flush_interval_ms=999_999, redact_pii=False)
    c.track("signup", visitor_id="v1", properties={"note": "a@b.co"})
    assert c._queue[0]["properties"]["note"] == "a@b.co"
    c.shutdown()


# --- flags ------------------------------------------------------------------

def test_flags_caches_response_and_flag_reads_it():
    sess, calls = make_session(flags_payload={"new_ui": True, "variant": "B"})
    c = Luniq(api_key="k", session=sess, flush_interval_ms=999_999)
    out = c.flags(visitor_id="v1", traits={"plan": "pro"})
    assert out == {"new_ui": True, "variant": "B"}
    assert c.flag("v1", "new_ui") is True
    assert c.flag("v1", "variant") == "B"
    assert c.flag("v1", "missing") is False
    assert c.flag("unknown_visitor", "new_ui") is False
    # Verify wire format
    flag_call = [c for c in calls if c["url"].endswith("/flags/evaluate")][0]
    assert flag_call["json"] == {"visitorId": "v1", "accountId": "", "traits": {"plan": "pro"}}
    c.shutdown()


def test_flags_returns_empty_on_http_error():
    sess = MagicMock()
    sess.post.return_value = FakeResponse(500)
    c = Luniq(api_key="k", session=sess, flush_interval_ms=999_999)
    assert c.flags(visitor_id="v1") == {}
    assert c.flag("v1", "x") is False
    c.shutdown()


def test_flags_requires_visitor_id():
    sess, _ = make_session()
    c = Luniq(api_key="k", session=sess, flush_interval_ms=999_999)
    with pytest.raises(ValueError):
        c.flags(visitor_id="")
    c.shutdown()


# --- shutdown ---------------------------------------------------------------

def test_shutdown_drains_queue():
    sess, calls = make_session()
    c = Luniq(api_key="k", session=sess, flush_interval_ms=999_999)
    c.track("e", visitor_id="v1")
    c.shutdown()
    # Shutdown should have fired the events.
    event_calls = [x for x in calls if x["url"].endswith("/v1/events")]
    assert len(event_calls) == 1
    assert c._queue == []
    # Background thread should be stopping.
    assert c._stop.is_set()

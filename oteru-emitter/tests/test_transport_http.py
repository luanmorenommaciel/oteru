"""Tests for ``oteru_emitter.transport.otlp_http`` (no network needed)."""

from __future__ import annotations

from oteru_emitter.transport.otlp_http import HttpTransport


def test_default_headers():
    transport = HttpTransport("http://localhost:4318")
    assert transport.session.headers["Content-Type"] == "application/x-protobuf"
    transport.close()


def test_extra_headers_set_on_session():
    transport = HttpTransport(
        "http://localhost:4318",
        headers={"authorization": "test-api-key", "x-extra": "1"},
    )
    assert transport.session.headers["authorization"] == "test-api-key"
    assert transport.session.headers["x-extra"] == "1"
    # extra headers must not clobber the protobuf content type
    assert transport.session.headers["Content-Type"] == "application/x-protobuf"
    transport.close()


def test_endpoint_trailing_slash_stripped():
    transport = HttpTransport("http://localhost:4318/")
    assert transport.base == "http://localhost:4318"
    transport.close()

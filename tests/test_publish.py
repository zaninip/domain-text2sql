"""Network retries of the publication script (t2sql.export.publish), without any network."""

import httpx
import pytest

from t2sql.export import publish


def test_network_errors_are_tried_again_and_other_errors_are_not(monkeypatch):
    monkeypatch.setattr(publish.time, "sleep", lambda seconds: None)
    calls = []

    def dropped_twice(value):
        calls.append(value)
        if len(calls) < 3:
            raise httpx.ConnectError("connection reset")
        return value * 2

    assert publish.retry("flaky", dropped_twice, 21) == 42
    assert len(calls) == 3

    def refused():
        calls.append("refused")
        raise PermissionError("no write access")

    with pytest.raises(PermissionError):
        publish.retry("refused", refused)
    assert calls.count("refused") == 1


def test_network_errors_give_up_after_the_last_attempt(monkeypatch):
    monkeypatch.setattr(publish.time, "sleep", lambda seconds: None)

    def always_dropped():
        raise httpx.ConnectError("connection reset")

    with pytest.raises(httpx.ConnectError):
        publish.retry("down", always_dropped)

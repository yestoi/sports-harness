import httpx
import pytest
import respx

from harness.feeds.http import FetchError, HttpClient


@respx.mock
def test_get_returns_json_and_headers():
    respx.get("https://x/a").mock(return_value=httpx.Response(200, json={"ok": 1}, headers={"x-requests-last": "3"}))
    c = HttpClient(timeout_s=1, sleep=lambda s: None)
    r = c.get("https://x/a", params={"apiKey": "SECRET", "q": "1"})
    assert r.status == 200 and r.body == {"ok": 1}
    assert r.headers["x-requests-last"] == "3"
    assert "SECRET" not in r.url and "q=1" in r.url


@respx.mock
def test_retries_once_on_5xx_then_succeeds():
    route = respx.get("https://x/b").mock(side_effect=[httpx.Response(502), httpx.Response(200, json=[])])
    c = HttpClient(timeout_s=1, sleep=lambda s: None)
    r = c.get("https://x/b")
    assert r.status == 200 and route.call_count == 2


@respx.mock
def test_429_sleeps_retry_after_then_retries():
    slept = []
    route = respx.get("https://x/c").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "5"}), httpx.Response(200, json={})])
    c = HttpClient(timeout_s=1, sleep=slept.append)
    r = c.get("https://x/c")
    assert r.status == 200 and slept == [5.0] and route.call_count == 2


@respx.mock
def test_transport_error_twice_raises():
    respx.get("https://x/d").mock(side_effect=httpx.ConnectError("boom"))
    c = HttpClient(timeout_s=1, sleep=lambda s: None)
    with pytest.raises(FetchError):
        c.get("https://x/d")


@respx.mock
def test_non_json_body_is_none():
    respx.get("https://x/e").mock(return_value=httpx.Response(200, text="<html>"))
    r = HttpClient(timeout_s=1, sleep=lambda s: None).get("https://x/e")
    assert r.status == 200 and r.body is None


@respx.mock
def test_fetched_at_uses_injected_clock():
    from datetime import datetime, timezone
    fixed = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)
    respx.get("https://x/f").mock(return_value=httpx.Response(200, json={}))
    r = HttpClient(timeout_s=1, sleep=lambda s: None, clock=lambda: fixed).get("https://x/f")
    assert r.fetched_at == fixed


@respx.mock
def test_user_agent_is_httpx_default():
    route = respx.get("https://x/ua").mock(return_value=httpx.Response(200, json={}))
    HttpClient(timeout_s=1, sleep=lambda s: None).get("https://x/ua")
    assert route.calls[0].request.headers["user-agent"].startswith("python-httpx/")

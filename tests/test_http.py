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

"""The LAN listener's owner login (addendum §6). No secret is created or read here: every
fixture writes its own throwaway hash line into `tmp_path`, never into `secrets/`."""

import ast
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from harness.dashboard.app import create_dashboard
from harness.dashboard.auth import (COOKIE_MAX_AGE_S, COOKIE_NAME, hash_password, issue_cookie,
                                    lan_active, read_cookie, session_key, verify_password)

T0 = datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc)

#: Throwaway values, generated in this process for this process. Nothing here is read from or
#: written to `secrets/`, and nothing here is the user's password.
PASSWORD = "a throwaway password for this test process"
HASH_LINE = hash_password(PASSWORD)
TOKEN = "throwaway-dashboard-token"

#: Not a secret and not a hash: the realistic mix-up is the owner putting `lan_tls.crt` -- which
#: every LAN client is handed in the TLS handshake -- where the hash line belongs (review IM-2).
#: Shape only; no key material, real or otherwise, is written by this file.
PUBLIC_MATERIAL = ("-----BEGIN CERTIFICATE-----\n"
                   "bm90IGEgY2VydGlmaWNhdGU6IHRoaXMgaXMgcHVibGljIHRlc3QgZmlsbGVy\n"
                   "-----END CERTIFICATE-----\n")


@pytest.fixture
def env_settings(env_settings, tmp_path):
    """The suite's own `env_settings` (tests/conftest.py) with the three LAN paths pointed at
    throwaway names under `tmp_path`. The real paths are container paths the user owns; no test
    ever touches them."""
    return env_settings.model_copy(update={
        "owner_password_hash_file": tmp_path / "owner_password_hash",
        "lan_tls_cert_file": tmp_path / "lan_tls.crt",
        "lan_tls_key_file": tmp_path / "lan_tls.key",
    })


def _lan_settings(env_settings, tmp_path, hash_file: str) -> object:
    """`hash_file` is one of the fail-closed states plus `ok`; the file is written under
    `tmp_path` every time."""
    path = Path(env_settings.owner_password_hash_file)
    if hash_file == "ok":
        path.write_text(HASH_LINE)
    elif hash_file == "empty":
        path.write_text("")
    elif hash_file == "directory":
        path.mkdir()
    elif hash_file == "malformed":
        path.write_text("not-a-hash-line")
    elif hash_file == "public_material":
        path.write_text(PUBLIC_MATERIAL)
    elif hash_file != "missing":
        raise AssertionError(f"unknown hash-file state {hash_file!r}")
    token_file = tmp_path / "dashboard_token"
    token_file.write_text(TOKEN)
    return env_settings.model_copy(update={"dashboard_token_file": token_file,
                                           "snapshots_enabled": False})


@pytest.fixture
def lan_app(db_session, env_settings, tmp_path):
    """A TestClient over the LAN app. `https://` because the session cookie is `Secure`: a
    client would not send it back over plain http, which is the point of the attribute."""
    def _make(hash_file: str = "ok", raise_server_exceptions: bool = True) -> TestClient:
        settings = _lan_settings(env_settings, tmp_path, hash_file)
        factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
        app = create_dashboard(factory, settings, clock=lambda: T0, lan=True)
        # `raise_server_exceptions=False` is what a real client sees: Starlette's own
        # `ServerErrorMiddleware` answers instead of the exception reaching the test.
        return TestClient(app, base_url="https://testserver",
                          raise_server_exceptions=raise_server_exceptions)

    return _make


@pytest.fixture
def loopback_app(db_session, env_settings, tmp_path):
    """The app as it is today: `lan` defaults to false and nothing below is added."""
    settings = _lan_settings(env_settings, tmp_path, "ok")
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    return TestClient(create_dashboard(factory, settings, clock=lambda: T0))


def _login(client: TestClient) -> None:
    response = client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    assert response.status_code == 303


def test_a_hash_line_round_trips_with_the_pinned_parameters():
    line = hash_password("correct horse battery staple")
    assert line.startswith("scrypt$16384$8$1$")
    assert verify_password("correct horse battery staple", line) is True
    assert verify_password("wrong", line) is False


def test_a_hash_with_other_parameters_is_invalid_not_merely_wrong():
    """A-I6: the parameters are pinned, so a line claiming n=1024 is a weaker hash someone else
    wrote, not a password to check against."""
    line = hash_password("pw").replace("scrypt$16384", "scrypt$1024")
    with pytest.raises(ValueError):
        verify_password("pw", line)


def test_a_cookie_verifies_by_signature_then_by_server_side_age():
    key = session_key("scrypt$16384$8$1$c2FsdA==$aGFzaA==")
    cookie = issue_cookie(key, T0)
    assert read_cookie(key, cookie, T0 + timedelta(days=179)) is True
    assert read_cookie(key, cookie, T0 + timedelta(days=181)) is False       # A-I5
    assert read_cookie(key, cookie[:-1] + "0", T0) is False                  # tampered
    assert read_cookie(session_key("scrypt$16384$8$1$b3RoZXI=$aGFzaA=="), cookie, T0) is False


@pytest.mark.parametrize("state", ["missing", "empty", "directory", "malformed"])
def test_the_app_fails_closed_on_a_bad_hash_file(lan_app, state):
    """A-I6/A-I7: missing, empty, unreadable or malformed all mean every login fails, exactly as
    /unkill fails on a missing token file. Nothing is ever allowed through."""
    client = lan_app(hash_file=state)
    assert client.post("/login", data={"password": "anything"}).status_code == 403


def test_lan_active_requires_three_non_empty_regular_files(tmp_path, env_settings):
    assert lan_active(env_settings) is False
    for name in ("owner_password_hash", "lan_tls.crt", "lan_tls.key"):
        (tmp_path / name).write_text("")
    assert lan_active(env_settings) is False          # present but empty
    for name in ("owner_password_hash", "lan_tls.crt", "lan_tls.key"):
        (tmp_path / name).write_text("x")
    assert lan_active(env_settings) is True
    (tmp_path / "lan_tls.key").unlink()
    (tmp_path / "lan_tls.key").mkdir()
    assert lan_active(env_settings) is False          # a directory is not a file


def test_an_api_read_without_a_cookie_is_401_and_a_navigation_is_302(lan_app):
    client = lan_app()
    assert client.get("/api/snap/ticket").status_code == 401
    assert client.get("/api/snap/ticket").json() == {"refusal": "session_required"}
    assert client.get("/ui/", follow_redirects=False).status_code == 302
    assert client.get("/healthz").status_code in (200, 503)      # the one exemption


def test_the_kill_pair_needs_the_session_as_well_as_its_token_on_the_lan_app(lan_app):
    """D15. On loopback both are untouched (invariant 9), which the next test pins."""
    client = lan_app()
    assert client.post("/unkill", headers={"X-Dashboard-Token": TOKEN}).status_code == 401
    _login(client)
    assert client.post("/unkill", headers={"X-Dashboard-Token": TOKEN}).status_code == 200
    assert client.post("/unkill").status_code == 403      # db_session but no token


def test_the_loopback_app_has_no_login_page_and_no_write_routes(loopback_app):
    for path in ("/login", "/logout", "/api/parlay/placed", "/api/parlay/correct"):
        assert loopback_app.post(path).status_code == 404
    assert loopback_app.get("/login").status_code == 404


def test_five_failed_logins_a_minute_then_429(lan_app):
    client = lan_app()
    for _ in range(5):
        assert client.post("/login", data={"password": "no"}).status_code == 403
    assert client.post("/login", data={"password": "no"}).status_code == 429
    assert client.post("/login", data={"password": "no"}, headers={"X-Forwarded-For": "x"}
                       ).status_code == 429          # the header is not trusted as an identity


def test_no_credential_substring_ever_reaches_a_log_record(lan_app, caplog):
    """§11.7. The filter in harness/logging_setup.py is not changed and is not what is being
    tested: this asserts the handlers themselves never hand a secret to logging."""
    client = lan_app()
    with caplog.at_level(logging.DEBUG):
        client.post("/login", data={"password": PASSWORD})
        client.post("/login", data={"password": "wrong"})
        client.cookies.clear()
        client.cookies.set(COOKIE_NAME, "garbage.deadbeef")
        client.get("/api/snap/ticket")
    blob = "\n".join(r.getMessage() for r in caplog.records) + "\n".join(
        repr(r.args) for r in caplog.records)
    for secret in (PASSWORD, HASH_LINE, HASH_LINE.split("$")[-1], f"{COOKIE_NAME}="):
        assert secret not in blob


def test_the_login_template_never_echoes_the_submitted_password(lan_app):
    client = lan_app()
    body = client.post("/login", data={"password": "hunter2"}).text
    assert "hunter2" not in body


def test_the_lan_app_sets_x_frame_options_deny(lan_app):
    assert lan_app().get("/healthz").headers["X-Frame-Options"] == "DENY"


def test_no_dashboard_module_imports_a_venue_client_or_an_http_client():
    """§11.11 and §14.5, widened to `auth.py` (plan review IM-10).

    A static walk, not an import-time check: the assertion is that the *source* of every module
    under `harness/dashboard/` names no venue package and no outbound client, so no future edit
    can reach one lazily inside a function either. `ast.parse` rather than a grep, so a word in
    a docstring or a comment cannot fail it and a real import cannot hide from it.
    """
    root = Path(__file__).resolve().parents[1] / "harness" / "dashboard"
    forbidden = {"httpx", "requests", "urllib.request", "urllib3", "aiohttp", "socket"}
    offenders = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            for name in names:
                if name.startswith("harness.venues") or name in forbidden:
                    offenders.append((path.name, name))
    assert offenders == []


def test_serve_refuses_the_lan_listener_without_both_tls_paths(tmp_path):
    """Step 5: the LAN listener never starts in the clear. Exit 2, and `uvicorn.run` is never
    reached -- a missing certificate must not fall back to plaintext on a LAN port."""
    from typer.testing import CliRunner

    from harness.cli import app as cli_app

    cert = tmp_path / "lan_tls.crt"
    cert.write_text("x")
    empty_key = tmp_path / "lan_tls.key"
    empty_key.write_text("")
    runner = CliRunner()
    for args in (["serve", "--lan"],
                 ["serve", "--lan", "--tls-cert", str(cert)],
                 ["serve", "--lan", "--tls-cert", str(cert), "--tls-key", str(empty_key)],
                 ["serve", "--lan", "--tls-cert", str(cert), "--tls-key", str(tmp_path)]):
        assert runner.invoke(cli_app, args).exit_code == 2


# --- Fix round 1: the review's three Important findings --------------------------------------


def test_a_non_ascii_cookie_signature_is_refused_and_never_crashes_the_gate(lan_app):
    """IM-1. `hmac.compare_digest` raises `TypeError` on two `str`s when either carries a byte
    >= 0x80, and headers are latin-1 on the wire, so one byte from an unauthenticated client
    turned the gate into a `500` that carried no `X-Frame-Options` and logged a traceback.
    Failing closed means 401/302 -- never 500.
    """
    client = lan_app(raise_server_exceptions=False)
    # Raw bytes, not `client.cookies.set`: the header is latin-1 on the wire, which is exactly
    # how a hostile client sends a byte the client library would refuse to encode itself.
    latin1_cookie = [(b"cookie", b"sports_session=1789322400.caf\xe9")]

    api = client.get("/api/snap/ticket", headers=latin1_cookie)
    assert api.status_code == 401
    assert api.json() == {"refusal": "session_required"}
    assert api.headers["X-Frame-Options"] == "DENY"

    page = client.get("/ui/", headers=latin1_cookie, follow_redirects=False)
    assert page.status_code == 302
    assert page.headers["X-Frame-Options"] == "DENY"


def test_a_hash_file_holding_public_material_grants_no_session(lan_app, tmp_path, env_settings):
    """IM-2. §6's fail-closed rule is about the *file*, not only about `POST /login`: a
    missing, empty, unreadable or malformed hash file means nothing is ever allowed through.
    The session key is derived from the file's bytes, so a file whose contents are public --
    the certificate every LAN client is handed -- let anyone who has seen them derive the key,
    forge a cookie and reach every route, including the token-less `POST /kill`.
    """
    client = lan_app(hash_file="public_material")
    forged = issue_cookie(session_key(PUBLIC_MATERIAL), T0)
    client.cookies.set(COOKIE_NAME, forged)

    assert client.get("/ui/app.css", follow_redirects=False).status_code == 302
    assert client.post("/unkill", headers={"X-Dashboard-Token": TOKEN}).status_code == 401
    assert client.post("/kill", data={"reason": "forged"}).status_code == 401


def test_an_empty_hash_file_grants_no_session_either(lan_app):
    """IM-2, the same rule on the other fail-closed state: an empty file derives an empty-input
    key, which is a constant anyone can compute."""
    client = lan_app(hash_file="empty")
    client.cookies.set(COOKIE_NAME, issue_cookie(session_key(""), T0))

    assert client.get("/ui/app.css", follow_redirects=False).status_code == 302
    assert client.post("/unkill", headers={"X-Dashboard-Token": TOKEN}).status_code == 401


def test_logout_needs_the_csrf_header_and_then_clears_the_cookie(lan_app):
    """IM-3(a). §6: `POST /logout` clears the cookie (CSRF header required)."""
    client = lan_app()
    _login(client)

    refused = client.post("/logout")
    assert refused.status_code == 403
    assert refused.json() == {"refusal": "csrf_header_required"}

    ok = client.post("/logout", headers={"X-Requested-With": "sports-ui"})
    assert ok.status_code == 200
    assert "Max-Age=0" in ok.headers["set-cookie"]
    # The jar is empty again, so the next navigation is back at the login page.
    assert client.get("/ui/", follow_redirects=False).status_code == 302


def test_the_session_cookie_carries_the_whole_attribute_set(lan_app):
    """IM-3(b). §6's cookie contract, attribute by attribute:
    `Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=15552000` (180 days)."""
    client = lan_app()
    response = client.post("/login", data={"password": PASSWORD}, follow_redirects=False)

    raw = response.headers["set-cookie"]
    assert raw.startswith(f"{COOKIE_NAME}=")
    attributes = [part.strip() for part in raw.split(";")[1:]]
    assert "HttpOnly" in attributes
    assert "Secure" in attributes
    assert "Path=/" in attributes
    assert f"Max-Age={COOKIE_MAX_AGE_S}" in attributes
    assert COOKIE_MAX_AGE_S == 15_552_000
    # RFC 6265bis attribute values are case-insensitive; Starlette writes `strict`.
    assert any(a.lower() == "samesite=strict" for a in attributes)


def test_x_frame_options_is_on_every_lan_refusal_and_on_no_loopback_response(lan_app,
                                                                             loopback_app):
    """IM-3(c). §5.5: the response headers on the LAN app include `X-Frame-Options: DENY` --
    on refusals too, which is where a framing attack would land. The loopback app gains no
    header at all (invariant 9: it is unchanged)."""
    client = lan_app()
    unauthorised = client.get("/api/snap/ticket")
    redirected = client.get("/ui/", follow_redirects=False)
    forbidden = client.post("/login", data={"password": "no"})

    assert unauthorised.status_code == 401
    assert redirected.status_code == 302
    assert forbidden.status_code == 403
    for response in (unauthorised, redirected, forbidden):
        assert response.headers["X-Frame-Options"] == "DENY"

    assert "X-Frame-Options" not in loopback_app.get("/healthz").headers
    assert "X-Frame-Options" not in loopback_app.get("/ui/app.css").headers

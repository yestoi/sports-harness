"""`deploy/nas.env` is copied verbatim to the NAS as `.env` by `make deploy-nas`, so it is both
the deployed configuration and a committed, dated record of the harness's posture. These tests
guard the two properties a later reader depends on: the paper-mode assertions are present, and
no secret has ever been pasted into the file."""

from pathlib import Path

NAS_ENV = Path(__file__).parent.parent / "deploy" / "nas.env"

# A value longer than this in a compose env file is far more likely to be a pasted API key,
# token, or PEM body than a real setting. Raise it deliberately (and look at the line) if a
# legitimate value ever outgrows it; do not delete the check.
MAX_VALUE_LEN = 60

SECRETISH_NAMES = ("SECRET", "TOKEN", "PRIVATE")


def _assignments() -> list[tuple[str, str]]:
    out = []
    for line in NAS_ENV.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        out.append((name.strip(), value.strip()))
    return out


def test_paper_posture_is_asserted_positively():
    """F30: the absence of a live-trading flag proves nothing years later. A committed
    `LIVE_TRADING=0` in every revision of the deployed env file does."""
    text = NAS_ENV.read_text()
    assert "LIVE_TRADING=0" in text
    assert "HARNESS_MODE=paper" in text


def test_app_uid_is_the_nas_login_user():
    """F54: the containers run as uid 1000 so the 0600 secret files are readable without chown.
    The runbook's rollback and secret steps depend on this staying true."""
    assert "APP_UID=1000" in NAS_ENV.read_text()


def test_no_value_looks_like_a_pasted_secret():
    long_values = [n for n, v in _assignments() if len(v) > MAX_VALUE_LEN]
    assert long_values == [], f"suspiciously long values in deploy/nas.env: {long_values}"


def test_no_variable_is_named_like_a_secret():
    named = [n for n, _ in _assignments() if any(w in n.upper() for w in SECRETISH_NAMES)]
    assert named == [], f"secret-looking names in deploy/nas.env: {named}"


def test_key_variables_point_at_files_rather_than_holding_keys():
    """`ODDS_API_KEY_FILE=/run/secrets/...` is a path and is fine; a bare `..._KEY=` would be the
    key itself, which belongs in secrets/ and is mounted, never committed."""
    inline = [n for n, _ in _assignments() if "KEY" in n.upper() and not n.upper().endswith("_FILE")]
    assert inline == [], f"key-valued names in deploy/nas.env: {inline}"

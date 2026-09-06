import json
import logging

from harness.logging_setup import configure_logging, redact


def test_redact_api_key_and_auth():
    s = "GET https://x/v4/odds?apiKey=SECRET123&markets=h2h Authorization: Bearer tok KALSHI-ACCESS-SIGNATURE: sig=="
    out = redact(s)
    assert "SECRET123" not in out
    assert "tok" not in out.split("Authorization: ")[1][:10]
    assert "sig==" not in out
    assert "markets=h2h" in out


def test_json_logging_redacts(capsys):
    configure_logging("INFO")
    logging.getLogger("t").info("calling url apiKey=ABC")
    line = capsys.readouterr().err.strip().splitlines()[-1]
    rec = json.loads(line)
    assert rec["message"] == "calling url apiKey=[REDACTED]"
    assert "levelname" in rec

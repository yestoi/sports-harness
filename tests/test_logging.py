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


def test_json_logging_redacts_exception_traceback(capsys):
    configure_logging("INFO")
    try:
        raise ValueError("request failed: apiKey=SECRET123")
    except ValueError:
        logging.getLogger("t").exception("boom")
    line = capsys.readouterr().err.strip().splitlines()[-1]
    rec = json.loads(line)
    assert "SECRET123" not in line
    assert "apiKey=[REDACTED]" in rec["exc_info"]
    assert "ValueError" in rec["exc_info"]


def test_redact_anthropic_api_key():
    out = redact("key sk-ant-api03-abcDEF_123-xyz tail")
    assert out == "key [REDACTED] tail"


def test_redact_pem_private_key_with_algorithm():
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIBOgIBAAJBAK8Q\n"
        "b3JlbSBpcHN1bSBk\n"
        "b2xvciBzaXQgYW1l\n"
        "-----END RSA PRIVATE KEY-----"
    )
    assert redact(pem) == "[REDACTED PEM]"


def test_redact_pem_private_key_without_algorithm():
    pem = (
        "-----BEGIN PRIVATE KEY-----\n"
        "MIIBOgIBAAJBAK8Q\n"
        "b3JlbSBpcHN1bSBk\n"
        "b2xvciBzaXQgYW1l\n"
        "-----END PRIVATE KEY-----"
    )
    assert redact(pem) == "[REDACTED PEM]"


def test_redact_two_pem_blocks_non_greedy():
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "aaaaaaaaaaaaaaaa\n"
        "-----END RSA PRIVATE KEY-----\n"
        "middle text\n"
        "-----BEGIN PRIVATE KEY-----\n"
        "bbbbbbbbbbbbbbbb\n"
        "-----END PRIVATE KEY-----"
    )
    out = redact(pem)
    assert out == "[REDACTED PEM]\nmiddle text\n[REDACTED PEM]"


def test_json_logging_redacts_anthropic_key(capsys):
    configure_logging("INFO")
    logging.getLogger("t").info("using key sk-ant-api03-abcDEF_123-xyz now")
    line = capsys.readouterr().err.strip().splitlines()[-1]
    rec = json.loads(line)
    assert "sk-ant-api03-abcDEF_123-xyz" not in line
    assert rec["message"] == "using key [REDACTED] now"


def test_json_logging_redacts_anthropic_key_in_traceback(capsys):
    configure_logging("INFO")
    try:
        raise ValueError("auth failed: sk-ant-api03-abcDEF_123-xyz")
    except ValueError:
        logging.getLogger("t").exception("boom")
    line = capsys.readouterr().err.strip().splitlines()[-1]
    rec = json.loads(line)
    assert "sk-ant-api03-abcDEF_123-xyz" not in line
    assert "[REDACTED]" in rec["exc_info"]
    assert "ValueError" in rec["exc_info"]

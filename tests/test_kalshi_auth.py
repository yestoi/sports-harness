import base64

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from harness.venues.kalshi.auth import sign_request


def test_sign_request_verifies_with_public_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    headers = sign_request("kid-123", pem, "get", "/trade-api/ws/v2", 1788710400000)
    assert headers["KALSHI-ACCESS-KEY"] == "kid-123" and headers["KALSHI-ACCESS-TIMESTAMP"] == "1788710400000"
    sig = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    key.public_key().verify(sig, b"1788710400000GET/trade-api/ws/v2",
                            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())

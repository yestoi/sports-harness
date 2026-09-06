import base64

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def sign_request(key_id: str, private_key_pem: bytes, method: str, path: str, ts_ms: int) -> dict[str, str]:
    key = serialization.load_pem_private_key(private_key_pem, password=None)
    msg = f"{ts_ms}{method.upper()}{path}".encode()
    sig = key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
    return {"KALSHI-ACCESS-KEY": key_id, "KALSHI-ACCESS-TIMESTAMP": str(ts_ms),
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode()}

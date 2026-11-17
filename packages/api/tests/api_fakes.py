"""Values the api tests share."""

import base64
import hashlib
import hmac
import json
import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

REQUIRED = {
    "HTRFLOW_API_ISSUER": "https://id.example/realms/htrflow",
    "HTRFLOW_API_AUDIENCE": "htrflow-batch",
    "HTRFLOW_API_CLIENT_ID": "htrflow-cli",
    "HTRFLOW_API_GROUP": "htr-operators",
    "HTRFLOW_API_NAMESPACE": "htr-batch",
    "HTRFLOW_API_QUEUE": "htr-batch",
}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Signer:
    """A fresh RSA key and the tokens it signs, for tests."""

    def __init__(self, kid: str = "k1") -> None:
        self.kid = kid
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwks(self) -> dict:
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self._key.public_key()))
        return {"keys": [{**jwk, "kid": self.kid, "use": "sig", "alg": "RS256"}]}

    def token(self, **overrides) -> str:
        alg = overrides.pop("alg", "RS256")
        kid = overrides.pop("kid", self.kid)
        claims = {
            "iss": REQUIRED["HTRFLOW_API_ISSUER"],
            "aud": REQUIRED["HTRFLOW_API_AUDIENCE"],
            "exp": int(time.time()) + 300,
            "preferred_username": "ada",
            "groups": ["htr-operators"],
            **overrides,
        }
        claims = {k: v for k, v in claims.items() if v is not None}
        if alg in ("none", "HS256"):
            header = {"alg": alg, "typ": "JWT", "kid": kid}
            head = _b64(json.dumps(header).encode())
            body = _b64(json.dumps(claims).encode())
            signing_input = f"{head}.{body}".encode()
            if alg == "none":
                return f"{head}.{body}."
            pem = self._key.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            sig = hmac.new(pem, signing_input, hashlib.sha256).digest()
            return f"{head}.{body}.{_b64(sig)}"
        return jwt.encode(claims, self._key, algorithm=alg, headers={"kid": kid})

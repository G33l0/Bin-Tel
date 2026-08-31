"""Ed25519 signature verification for license tokens.

A license is only trustworthy if the client can tell a server-issued state
from one a user typed into a file. The client therefore holds only a **public**
key and verifies a detached signature over the license payload; forging a
license means forging an Ed25519 signature, not editing JSON.

The implementation follows the RFC 8032 reference formulation so the desktop
client needs no third-party cryptography dependency. Signing is included for
the bundled development license server; production signing happens server-side
with a private key that never ships with the application.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# --- curve parameters (RFC 8032, Ed25519) ---------------------------------
_P = 2**255 - 19
_Q = 2**252 + 27742317777372353535851937790883648493
_COFACTOR_BITS = 3


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _sha512_int(data: bytes) -> int:
    return int.from_bytes(_sha512(data), "little")


def _inv(value: int) -> int:
    return pow(value, _P - 2, _P)


_D = -121665 * _inv(121666) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _x_recover(y: int) -> int:
    xx = (y * y - 1) * _inv(_D * y * y + 1)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = (x * _I) % _P
    if x % 2 != 0:
        x = _P - x
    return x


_BY = 4 * _inv(5)
_BX = _x_recover(_BY)
#: Base point in extended homogeneous coordinates (X, Y, Z, T).
_B = (_BX % _P, _BY % _P, 1, (_BX * _BY) % _P)
_IDENTITY = (0, 1, 1, 0)

Point = tuple[int, int, int, int]


def _add(point: Point, other: Point) -> Point:
    a = (point[1] - point[0]) * (other[1] - other[0]) % _P
    b = (point[1] + point[0]) * (other[1] + other[0]) % _P
    c = 2 * point[3] * other[3] * _D % _P
    d = 2 * point[2] * other[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _double(point: Point) -> Point:
    return _add(point, point)


def _scalar_mult(point: Point, scalar: int) -> Point:
    """Iterative double-and-add — no recursion depth to worry about."""
    result = _IDENTITY
    addend = point
    while scalar > 0:
        if scalar & 1:
            result = _add(result, addend)
        addend = _double(addend)
        scalar >>= 1
    return result


def _encode_point(point: Point) -> bytes:
    z_inverse = _inv(point[2])
    x = point[0] * z_inverse % _P
    y = point[1] * z_inverse % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decode_point(data: bytes) -> Point:
    if len(data) != 32:
        raise ValueError("An Ed25519 point must be 32 bytes")
    encoded = int.from_bytes(data, "little")
    y = encoded & ((1 << 255) - 1)
    if y >= _P:
        raise ValueError("Point is out of range")
    x = _x_recover(y)
    if (x & 1) != ((encoded >> 255) & 1):
        x = _P - x
    point = (x, y, 1, x * y % _P)
    if not _on_curve(point):
        raise ValueError("Point is not on the curve")
    return point


def _on_curve(point: Point) -> bool:
    x, y, z, t = point
    if z % _P == 0:
        return False
    if x * y % _P != z * t % _P:
        return False
    return (y * y - x * x - z * z - _D * t * t) % _P == 0


def _expand_secret(secret_key: bytes) -> tuple[int, bytes]:
    digest = _sha512(secret_key)
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - (1 << _COFACTOR_BITS)
    scalar |= 1 << 254
    return scalar, digest[32:]


# --- public API ------------------------------------------------------------


def generate_secret_key() -> bytes:
    """A new 32-byte Ed25519 seed. Used by the development license server."""
    return secrets.token_bytes(32)


def public_key(secret_key: bytes) -> bytes:
    """Derive the 32-byte public key from a seed."""
    scalar, _ = _expand_secret(secret_key)
    return _encode_point(_scalar_mult(_B, scalar))


def sign(message: bytes, secret_key: bytes) -> bytes:
    """Produce a 64-byte detached signature.

    Present so the development license server can issue real, verifiable
    licenses. Production licenses are signed by the hosted service.
    """
    if len(secret_key) != 32:
        raise ValueError("An Ed25519 seed must be 32 bytes")
    scalar, prefix = _expand_secret(secret_key)
    encoded_public = _encode_point(_scalar_mult(_B, scalar))
    r = _sha512_int(prefix + message) % _Q
    encoded_r = _encode_point(_scalar_mult(_B, r))
    challenge = _sha512_int(encoded_r + encoded_public + message) % _Q
    s = (r + challenge * scalar) % _Q
    return encoded_r + int.to_bytes(s, 32, "little")


def verify(message: bytes, signature: bytes, verifying_key: bytes) -> bool:
    """Check a detached signature. Returns ``False`` rather than raising."""
    if len(signature) != 64 or len(verifying_key) != 32:
        return False
    try:
        point_a = _decode_point(verifying_key)
        point_r = _decode_point(signature[:32])
    except ValueError:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _Q:
        return False
    challenge = _sha512_int(signature[:32] + verifying_key + message) % _Q
    left = _scalar_mult(_B, s)
    right = _add(point_r, _scalar_mult(point_a, challenge))
    return _encode_point(left) == _encode_point(right)


# --- token encoding --------------------------------------------------------


def b64encode(data: bytes) -> str:
    """URL-safe base64 without padding, so a token stays copy-pasteable."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def fingerprint(key: bytes) -> str:
    """Short, stable identifier for a key, safe to show in diagnostics."""
    return hashlib.sha256(key).hexdigest()[:16]


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)

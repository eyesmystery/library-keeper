"""Ed25519 signatures (RFC 8032), standard library only (AUTOUPD, 2026-10-16).

The library computer checks that an update or a message really comes from Ibrahim: the channel file is signed on
his Mac with a private key that never leaves it (scripts/channel_publish.py), and the app holds only the public key
(backend/channel_key.py). Written from the reference code of RFC 8032 §6 and checked against its test vectors
(tests/test_channel.py). Slow but small: one verification takes well under a second.
"""
import hashlib

p = 2 ** 255 - 19
q = 2 ** 252 + 27742317777372353535851937790883648493
d = -121665 * pow(121666, p - 2, p) % p
I = pow(2, (p - 1) // 4, p)


def _sha512(m):
    return hashlib.sha512(m).digest()


def _inv(x):
    return pow(x, p - 2, p)


def _recover_x(y, sign):
    if y >= p:
        return None
    x2 = (y * y - 1) * _inv(d * y * y + 1)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (p + 3) // 8, p)
    if (x * x - x2) % p != 0:
        x = x * I % p
    if (x * x - x2) % p != 0:
        return None
    if (x & 1) != sign:
        x = p - x
    return x


_gy = 4 * _inv(5) % p
_gx = _recover_x(_gy, 0)
G = (_gx, _gy, 1, _gx * _gy % p)


def _add(P, Q):
    A, B = (P[1] - P[0]) * (Q[1] - Q[0]) % p, (P[1] + P[0]) * (Q[1] + Q[0]) % p
    C, D = 2 * P[3] * Q[3] * d % p, 2 * P[2] * Q[2] % p
    E, F, Gg, H = B - A, D - C, D + C, B + A
    return (E * F % p, Gg * H % p, F * Gg % p, E * H % p)


def _mul(s, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _equal(P, Q):
    return (P[0] * Q[2] - Q[0] * P[2]) % p == 0 and (P[1] * Q[2] - Q[1] * P[2]) % p == 0


def _compress(P):
    zinv = _inv(P[2])
    x, y = P[0] * zinv % p, P[1] * zinv % p
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % p)


def _expand(secret):
    if len(secret) != 32:
        raise ValueError("a private key is 32 bytes")
    h = _sha512(secret)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(secret):
    a, _ = _expand(secret)
    return _compress(_mul(a, G))


def sign(secret, msg):
    a, prefix = _expand(secret)
    A = _compress(_mul(a, G))
    r = int.from_bytes(_sha512(prefix + msg), "little") % q
    R = _compress(_mul(r, G))
    h = int.from_bytes(_sha512(R + A + msg), "little") % q
    return R + int.to_bytes((r + h * a) % q, 32, "little")


def verify(public, msg, signature):
    """-> True only for a valid signature of msg by the key `public` (32 bytes)."""
    try:
        if len(public) != 32 or len(signature) != 64:
            return False
        A = _decompress(public)
        if not A:
            return False
        R = _decompress(signature[:32])
        if not R:
            return False
        s = int.from_bytes(signature[32:], "little")
        if s >= q:
            return False
        h = int.from_bytes(_sha512(signature[:32] + public + msg), "little") % q
        return _equal(_mul(s, G), _add(R, _mul(h, A)))
    except (TypeError, ValueError):
        return False

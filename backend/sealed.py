"""A sealed file that only Ibrahim's private key opens (INBOXSEND, Ibrahim 2026-10-23: the library's inbox sent to
him every week or when he asks, «رفع تلقائي مشفّر»). Standard library only, so it runs the same at the library on
Windows and on Ibrahim's Mac.

  X25519 (RFC 7748)      a new key pair for every file; its public half travels in the file
  HKDF-SHA256 (RFC 5869) the shared secret -> an encryption key and a separate MAC key
  HMAC-SHA256 counter    keystream XOR the (zlib-compressed) content
  HMAC-SHA256 tag        over the header and the ciphertext (encrypt-then-MAC), checked before anything is opened

File: MAGIC | sender public key (32) | nonce (16) | ciphertext | tag (32).
The library holds only Ibrahim's public key (backend/inbox_key.py); his private key never leaves his Mac.
"""
import hashlib
import hmac
import os
import zlib

MAGIC = b"MANAR-INBOX-1\n"
INFO = b"manar-inbox-1"
_P = 2 ** 255 - 19
_A24 = 121665
BASE = (9).to_bytes(32, "little")


class SealedError(Exception):
    pass


def _clamp(k):
    k = bytearray(k)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    return int.from_bytes(k, "little")


def x25519(k, u):
    """RFC 7748 §5: scalar k (32 bytes) times the point with u-coordinate u (32 bytes)."""
    if len(k) != 32 or len(u) != 32:
        raise SealedError("X25519 takes 32-byte values")
    k = _clamp(k)
    x1 = int.from_bytes(u, "little") & ((1 << 255) - 1)
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in reversed(range(255)):
        kt = (k >> t) & 1
        swap ^= kt
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = kt
        a = (x2 + z2) % _P
        aa = a * a % _P
        b = (x2 - z2) % _P
        bb = b * b % _P
        e = (aa - bb) % _P
        c = (x3 + z3) % _P
        d = (x3 - z3) % _P
        da = d * a % _P
        cb = c * b % _P
        x3 = (da + cb) * (da + cb) % _P
        z3 = x1 * (da - cb) * (da - cb) % _P
        x2 = aa * bb % _P
        z2 = e * (aa + _A24 * e) % _P
    if swap:
        x2, x3, z2, z3 = x3, x2, z3, z2
    return (x2 * pow(z2, _P - 2, _P) % _P).to_bytes(32, "little")


def public_key(private):
    return x25519(private, BASE)


def new_private():
    return os.urandom(32)


def _hkdf(ikm, salt, info, length):
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    out, block, i = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([i]), hashlib.sha256).digest()
        out += block
        i += 1
    return out[:length]


def _keystream_xor(key, nonce, data):
    out = bytearray(len(data))
    for i in range(0, len(data), 32):
        block = hmac.new(key, nonce + (i // 32).to_bytes(8, "big"), hashlib.sha256).digest()
        chunk = data[i:i + 32]
        out[i:i + len(chunk)] = bytes(x ^ y for x, y in zip(chunk, block))
    return bytes(out)


def _keys(shared, eph_pub, recipient_pub):
    if shared == bytes(32):
        raise SealedError("مفتاح غير صالح")
    k = _hkdf(shared, eph_pub + recipient_pub, INFO, 64)
    return k[:32], k[32:]


def seal(recipient_pub, content):
    """content (bytes) -> a sealed file (bytes) for the holder of recipient_pub's private key."""
    if not recipient_pub or len(recipient_pub) != 32:
        raise SealedError("مفتاح المطوّر العام غير موجود")
    eph = new_private()
    eph_pub = public_key(eph)
    k_enc, k_mac = _keys(x25519(eph, recipient_pub), eph_pub, recipient_pub)
    nonce = os.urandom(16)
    header = MAGIC + eph_pub + nonce
    ct = _keystream_xor(k_enc, nonce, zlib.compress(content, 9))
    return header + ct + hmac.new(k_mac, header + ct, hashlib.sha256).digest()


def open_sealed(private, blob, max_size=200 * 1024 * 1024):
    """A sealed file -> its content (bytes). Raises SealedError when the file was changed or is for another key."""
    n = len(MAGIC)
    if len(blob) < n + 32 + 16 + 32 or blob[:n] != MAGIC:
        raise SealedError("ليس ملفًا مختومًا من «منار»")
    eph_pub, nonce = blob[n:n + 32], blob[n + 32:n + 48]
    header, ct, tag = blob[:n + 48], blob[n + 48:-32], blob[-32:]
    k_enc, k_mac = _keys(x25519(private, eph_pub), eph_pub, public_key(private))
    if not hmac.compare_digest(tag, hmac.new(k_mac, header + ct, hashlib.sha256).digest()):
        raise SealedError("الملف تغيّر أو ليس لهذا المفتاح — لم يُفتح")
    d = zlib.decompressobj()
    out = d.decompress(_keystream_xor(k_enc, nonce, ct), max_size)
    if d.unconsumed_tail:
        raise SealedError("الملف أكبر من المسموح")
    return out

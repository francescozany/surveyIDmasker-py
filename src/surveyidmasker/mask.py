"""Deterministic encryption of identifiers into alphanumeric codes.

Compatible with the R package: the same key file gives the same codes.
"""
import hashlib
import hmac
import os
import string

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .key import Key, derive_key, read_key_file
from .utils import is_blank, to_str

CODE_ALPHABET = string.ascii_uppercase + string.ascii_lowercase + string.digits
TAG_LENGTH = 8
# Values are padded to at least this many bytes before encryption, so every
# code has at least 35 characters and short IDs don't stand out by length.
MIN_PLAIN_BYTES = 18


def _ctr(key: bytes, tag: bytes, data: bytes) -> bytes:
    iv = tag + bytes(16 - TAG_LENGTH)
    c = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()
    return c.update(data) + c.finalize()


def _tag(key: Key, plaintext: bytes) -> bytes:
    return hmac.new(key.mac, plaintext, hashlib.sha256).digest()[:TAG_LENGTH]


def encrypt_code(value: str, key: Key) -> str:
    """Original value encrypted with the key (AES-256-CTR, synthetic IV: the
    IV is an HMAC of the value, so equal values give equal codes)."""
    pt = _pad(value.encode("utf-8"))
    tag = _tag(key, pt)
    return raw_to_base62(tag + _ctr(key.enc, tag, pt))


def decrypt_code(code: str, key: Key):
    """The original value, or None when the code was not made with this key."""
    data = base62_to_raw(code)
    if data is None or len(data) <= TAG_LENGTH:
        return None
    tag, ct = data[:TAG_LENGTH], data[TAG_LENGTH:]
    pt = _ctr(key.enc, tag, ct)
    if not hmac.compare_digest(_tag(key, pt), tag):
        return None
    pt = _unpad(pt)
    if pt is None:
        return None
    try:
        return pt.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _pad(b: bytes) -> bytes:
    """ISO/IEC 7816-4 padding: a 0x80 byte, then zeros up to the minimum length."""
    b = b + b"\x80"
    return b + bytes(max(0, MIN_PLAIN_BYTES - len(b)))


def _unpad(b: bytes):
    stripped = b.rstrip(b"\x00")
    if not stripped or stripped[-1] != 0x80:
        return None
    return stripped[:-1]


def raw_to_base62(b: bytes) -> str:
    """Base-62 so codes stay alphanumeric. A leading 0x01 byte keeps leading
    zero bytes from being lost."""
    n = int.from_bytes(b"\x01" + b, "big")
    digits = []
    while n > 0:
        n, r = divmod(n, 62)
        digits.append(CODE_ALPHABET[r])
    return "".join(reversed(digits))


def base62_to_raw(code: str):
    if not code:
        return None
    n = 0
    for ch in code:
        i = CODE_ALPHABET.find(ch)
        if i < 0:
            return None
        n = n * 62 + i
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    if not b or b[0] != 1:
        return None
    return b[1:]


class Codebook:
    """Caches one code per distinct value for the whole run, so the same value
    gets the same code in every column without encrypting it twice."""

    def __init__(self, key: Key):
        self.key = key
        self.map = {}

    def codes_for(self, keys):
        for k in keys:
            if k not in self.map:
                self.map[k] = encrypt_code(k, self.key)
        return [self.map[k] for k in keys]


def mask_with(cb: Codebook, x, keys=None, prefix="MSK"):
    x = [to_str(v) for v in x]
    if keys is None:
        keys = [None if v is None else v.strip(" \t\r\n") for v in x]
    prefixes = [prefix] * len(x) if isinstance(prefix, str) else list(prefix)
    out = ["MSKblank"] * len(x)
    ok = [i for i, v in enumerate(x) if not is_blank(v)]
    codes = cb.codes_for([keys[i] for i in ok])
    for i, c in zip(ok, codes):
        out[i] = prefixes[i] + c
    return out


def mask_values(x, key_file=None, prefix="MSK"):
    """Replace every non-blank value with "MSK" plus an encrypted code.

    Equal values get equal codes; blank or missing values become "MSKblank".
    With no ``key_file`` a temporary key is used and the codes cannot be
    turned back.
    """
    key = derive_key(os.urandom(32)) if key_file is None else read_key_file(key_file)
    return mask_with(Codebook(key), list(x), prefix=prefix)

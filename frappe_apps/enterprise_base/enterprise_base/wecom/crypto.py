"""企业微信官方回调协议；AES 使用 Frappe 已依赖的 cryptography。"""

import base64
import binascii
import hashlib
import hmac
import re
import secrets
import struct
import time
from xml.etree import ElementTree as ET

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

LIMIT = 65536
MAX_MESSAGE = 48000


class InvalidCallback(ValueError):
    def __init__(self):
        super().__init__("企业微信回调校验失败。")


def xml_fields(body: bytes) -> dict[str, str]:
    if (
        not body
        or len(body) > LIMIT
        or b"\x00" in body
        or b"<!" in body.upper().replace(b"<![CDATA[", b"")
    ):
        raise InvalidCallback()
    try:
        root = ET.fromstring(body)
        if root.tag != "xml" or root.attrib or len(root) > 32:
            raise InvalidCallback()
        fields = {}
        for node in root:
            if node.tag in fields or node.attrib:
                raise InvalidCallback()
            fields[node.tag] = node.text or "" if not len(node) else ""
        return fields
    except ET.ParseError:
        raise InvalidCallback() from None


def signature(token: str, timestamp: str, nonce: str, encrypted: str) -> str:
    return hashlib.sha1("".join(sorted((token, timestamp, nonce, encrypted))).encode()).hexdigest()


def aes_key(encoded: str) -> bytes:
    try:
        if not re.fullmatch(r"[A-Za-z0-9+/]{43}", encoded):
            raise InvalidCallback()
        key = base64.b64decode(encoded + "=", validate=True)
        if len(key) == 32:
            return key
    except (ValueError, TypeError):
        pass
    raise InvalidCallback()


def decrypt(
    encrypted: str,
    sig: str,
    timestamp: str,
    nonce: str,
    *,
    token: str,
    key: str,
    corpid: str,
    now: float | None = None,
) -> bytes:
    try:
        if not (
            isinstance(encrypted, str)
            and 0 < len(encrypted) <= LIMIT
            and re.fullmatch(r"[0-9a-f]{40}", sig)
        ):
            raise InvalidCallback()
        if not re.fullmatch(r"[0-9]{1,12}", timestamp) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", nonce
        ):
            raise InvalidCallback()
        if abs((time.time() if now is None else now) - int(timestamp)) > 300:
            raise InvalidCallback()
        expected = signature(token, timestamp, nonce, encrypted)
        if not hmac.compare_digest(expected, sig):
            raise InvalidCallback()
        raw_key = aes_key(key)
        raw = base64.b64decode(encrypted, validate=True)
        if not raw or len(raw) % 16:
            raise InvalidCallback()
        decoder = Cipher(algorithms.AES(raw_key), modes.CBC(raw_key[:16])).decryptor()
        raw = decoder.update(raw) + decoder.finalize()
        pad = raw[-1]
        if not 1 <= pad <= 32 or raw[-pad:] != bytes([pad]) * pad:
            raise InvalidCallback()
        raw = raw[:-pad]
        if len(raw) < 20:
            raise InvalidCallback()
        size = struct.unpack("!I", raw[16:20])[0]
        if not 0 < size <= min(MAX_MESSAGE, len(raw) - 20) or not hmac.compare_digest(
            raw[20 + size :], corpid.encode()
        ):
            raise InvalidCallback()
        return raw[20 : 20 + size]
    except (ValueError, TypeError, binascii.Error, struct.error):
        raise InvalidCallback() from None


def encrypt(message: bytes, *, token: str, key: str, corpid: str) -> bytes:
    if not message or len(message) > MAX_MESSAGE:
        raise InvalidCallback()
    raw_key = aes_key(key)
    raw = secrets.token_bytes(16) + struct.pack("!I", len(message)) + message + corpid.encode()
    pad = 32 - len(raw) % 32
    encoder = Cipher(algorithms.AES(raw_key), modes.CBC(raw_key[:16])).encryptor()
    encrypted = base64.b64encode(
        encoder.update(raw + bytes([pad]) * pad) + encoder.finalize()
    ).decode()
    stamp, nonce = str(int(time.time())), secrets.token_hex(16)
    root = ET.Element("xml")
    for name, value in dict(
        Encrypt=encrypted,
        MsgSignature=signature(token, stamp, nonce, encrypted),
        TimeStamp=stamp,
        Nonce=nonce,
    ).items():
        ET.SubElement(root, name).text = value
    return ET.tostring(root, encoding="utf-8")

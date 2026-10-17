import base64
import binascii
import hashlib
import hmac
import re
import secrets


PREFIX = 's1$'
SALT_BYTES = 16
KEY_BYTES = 24


def _valid_digest(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _derive(digest, salt):
    return hashlib.scrypt(
        digest.encode('ascii'), salt=salt, n=32768, r=8, p=3,
        maxmem=64 * 1024 * 1024, dklen=KEY_BYTES,
    )


def hash_password_digest(digest):
    if not _valid_digest(digest):
        raise ValueError('Invalid password digest')
    salt = secrets.token_bytes(SALT_BYTES)
    payload = salt + _derive(digest, salt)
    return PREFIX + base64.urlsafe_b64encode(payload).decode('ascii').rstrip('=')


def hash_password(password):
    return hash_password_digest(hashlib.sha256(password.encode('utf-8')).hexdigest())


def is_legacy_password(value):
    return _valid_digest(value)


def verify_password_digest(digest, stored):
    if not _valid_digest(digest) or not isinstance(stored, str):
        return False
    if is_legacy_password(stored):
        return hmac.compare_digest(digest, stored)
    if not stored.startswith(PREFIX) or len(stored) != 57:
        return False
    encoded = stored[len(PREFIX):]
    try:
        payload = base64.b64decode(encoded + '==', altchars=b'-_', validate=True)
    except (ValueError, binascii.Error):
        return False
    if len(payload) != SALT_BYTES + KEY_BYTES:
        return False
    if base64.urlsafe_b64encode(payload).decode('ascii').rstrip('=') != encoded:
        return False
    return hmac.compare_digest(_derive(digest, payload[:SALT_BYTES]), payload[SALT_BYTES:])
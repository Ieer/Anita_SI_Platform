import hashlib
import hmac
import os
import secrets
import socket

from rpyc.core.stream import SocketStream
from rpyc.utils.authenticators import AuthenticationError
from rpyc.utils.factory import connect_stream


RPC_SECRET_ENV = 'SMART_AGENT_SCHEDULER_RPC_SECRET'
_NONCE_SIZE = 32
_PROOF_SIZE = hashlib.sha256().digest_size
_PROOF_CONTEXT = b'smart-agent-scheduler-rpc-v1:'


def _load_secret(secret=None):
    secret = secret if secret is not None else os.environ.get(RPC_SECRET_ENV)
    if not secret or len(secret.encode('utf-8')) < 32:
        raise RuntimeError(f'{RPC_SECRET_ENV} must be set to at least 32 UTF-8 bytes')
    return secret.encode('utf-8')


def _recv_exact(sock, size):
    received = bytearray()
    while len(received) < size:
        chunk = sock.recv(size - len(received))
        if not chunk:
            raise AuthenticationError('scheduler RPC authentication connection closed')
        received.extend(chunk)
    return bytes(received)


def _make_proof(secret, nonce):
    return hmac.new(secret, _PROOF_CONTEXT + nonce, hashlib.sha256).digest()


class SchedulerRPCAuthenticator:
    def __init__(self, secret=None):
        self._secret = _load_secret(secret)

    def __call__(self, sock):
        previous_timeout = sock.gettimeout()
        try:
            sock.settimeout(5)
            nonce = secrets.token_bytes(_NONCE_SIZE)
            sock.sendall(nonce)
            received_proof = _recv_exact(sock, _PROOF_SIZE)
            if not hmac.compare_digest(received_proof, _make_proof(self._secret, nonce)):
                raise AuthenticationError('invalid scheduler RPC credentials')
            return sock, {'authenticated': True}
        except AuthenticationError:
            raise
        except OSError as error:
            raise AuthenticationError('scheduler RPC authentication failed') from error
        finally:
            sock.settimeout(previous_timeout)


def connect_scheduler_rpc(host, port, timeout=5):
    secret = _load_secret()
    stream = SocketStream.connect(host, port, timeout=timeout)
    try:
        sock = stream.sock
        sock.settimeout(timeout)
        nonce = _recv_exact(sock, _NONCE_SIZE)
        sock.sendall(_make_proof(secret, nonce))
        sock.settimeout(None)
        return connect_stream(
            stream,
            config={
                'allow_pickle': False,
                'allow_public_attrs': False,
                'sync_request_timeout': timeout,
            },
        )
    except Exception:
        stream.close()
        raise
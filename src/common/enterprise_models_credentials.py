import hashlib
import json
import os
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from common.agent import AgentError


PREFIX = 'SMART_AGENT_ENTERPRISE_MODELS_'


class EnterpriseAccessError(AgentError):
    pass


def require_access(access):
    owner = os.environ.get(PREFIX + 'OWNER', '').strip()
    roles = getattr(getattr(access, 'user_info', None), 'user_roles', ())
    if not owner or getattr(access, 'user_name', None) != owner or 'admin' not in roles:
        raise EnterpriseAccessError('Enterprise Models 仅限绑定的系统管理员使用。')


@dataclass(frozen=True)
class FileCredential:
    token: str = field(repr=False)
    expires_at: datetime
    version: str


def read_credential(now=None):
    try:
        configured = os.environ.get(PREFIX + 'TOKEN_FILE', '').strip()
        zone = ZoneInfo(os.environ.get(PREFIX + 'TOKEN_TIMEZONE', '').strip())
        if not configured:
            raise ValueError
        path = Path(configured)
        if not path.is_absolute():
            raise ValueError
        for parent in (path, *path.parents):
            attributes = parent.lstat()
            if (stat.S_ISLNK(attributes.st_mode)
                    or getattr(attributes, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise ValueError
        with path.open('rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError
            raw = source.read(65537)
        if len(raw) > 65536:
            raise ValueError
        data = json.loads(raw.decode('utf-8-sig'))
        token = data['access_token']
        expiry = data['token_expiry']
        if (not isinstance(token, str) or not token.strip() or len(token) > 8192
                or any(character.isspace() for character in token)
                or not isinstance(expiry, str)):
            raise ValueError
        expires_at = datetime.fromisoformat(expiry)
        if expires_at.year < 2000:
            raise ValueError
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=zone)
        expires_at = expires_at.astimezone(timezone.utc)
        if expires_at <= (now or datetime.now(timezone.utc)):
            raise ValueError
        return FileCredential(token, expires_at, hashlib.sha256(token.encode()).hexdigest())
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ZoneInfoNotFoundError):
        raise AgentError('Enterprise Models 登录文件不可用或已过期，请重新完成企业 AI 登录。') from None
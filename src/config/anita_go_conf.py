from configparser import ConfigParser
from pathlib import Path
from typing import List
import platform


def resolve_config_file_path(config_dir: Path) -> Path:
    config_dir = Path(config_dir)
    current_config = config_dir / 'anita_go.ini'
    legacy_config = config_dir / 'dashgo.ini'
    if current_config.is_file() or not legacy_config.is_file():
        return current_config
    return legacy_config


class PathProj:
    ROOT_PATH = Path(__file__).parent.parent
    CONF_FILE_PATH = resolve_config_file_path(ROOT_PATH / 'config')
    AVATAR_DIR_PATH = (ROOT_PATH / '..' / 'user_data' / 'avatars').resolve()
    AVATAR_DIR_PATH.mkdir(parents=True, exist_ok=True)

    @classmethod
    def resolve_sqlite_db_path(cls, configured_path: str) -> Path:
        path = Path(configured_path).expanduser()
        if not path.is_absolute():
            path = cls.ROOT_PATH / path
        return path.resolve()


conf = ConfigParser()
conf.read(PathProj.CONF_FILE_PATH, encoding='utf-8')


class BaseMetaConf(type):
    def __new__(cls, name, bases, dct):
        sub_conf = conf[name]
        for stat_var_name, type_ in dct['__annotations__'].items():
            if sub_conf.get(stat_var_name) is not None:
                if type_ is List:
                    dct[stat_var_name] = sub_conf.get(stat_var_name).split() # type: ignore
                elif type_ is bool:
                    dct[stat_var_name] = sub_conf.getboolean(stat_var_name)
                else:
                    dct[stat_var_name] = type_(sub_conf.get(stat_var_name))
        return super().__new__(cls, name, bases, dct)


class LogConf(metaclass=BaseMetaConf):
    LOG_LEVEL: str = 'WARNING'
    HANDLER_CONSOLE: bool = True
    HANDLER_LOG_FILE: bool = False
    LOG_FILE_PATH: str
    MAX_MB_PER_LOG_FILE: int = 50
    MAX_COUNT_LOG_FILE: int = 3


class CommonConf(metaclass=BaseMetaConf):
    ENCRYPT_KEY: str
    DASH_SERVE_LOCALLY: bool
    SYSTEM_IS_UNIX: str = platform.system() != 'Windows' # type: ignore


class LoginConf(metaclass=BaseMetaConf):
    VERIFY_CODE_SHOW_LOGIN_FAIL_COUNT: int = 5
    VERIFY_CODE_CHAR_NUM: int = 4
    JWT_EXPIRED_FORCE_LOGOUT: bool = False


class FlaskConf(metaclass=BaseMetaConf):
    COMPRESS_ALGORITHM: str = 'br'
    COMPRESS_BR_LEVEL: int = 9
    COOKIE_SESSION_SECRET_KEY: str


class ShowConf(metaclass=BaseMetaConf):
    WEB_TITLE: str
    APP_NAME: str


class JwtConf(metaclass=BaseMetaConf):
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = 'HS256'
    JWT_EXPIRE_MINUTES: int = 1440


class ProxyConf(metaclass=BaseMetaConf):
    NGINX_PROXY: bool = False


class OAuth2Conf(metaclass=BaseMetaConf):
    OAuth2AuthorizationCodeExpiresInMinutes: int
    OAuth2AuthorizationCodeLength: int
    OAuth2TokenExpiresInMinutes: int


class ApSchedulerConf(metaclass=BaseMetaConf):
    DATA_EXPIRE_DAY: int
    HOST: str
    PORT: int
    MISFIRE_GRACE_TIME_SECONDS: int = 300


class SqlDbConf(metaclass=BaseMetaConf):
    RDB_TYPE: str
    SQLITE_DB_PATH: str
    HOST: str
    PORT: int
    USER: str
    PASSWORD: str
    DATABASE: str
    POOL_SIZE: int


class ListenTaskConf(metaclass=BaseMetaConf):
    PERIOD_MINTUES: int

from config.anita_go_conf import PathProj, SqlDbConf
from playhouse.pool import PooledMySQLDatabase
from peewee import SqliteDatabase
from playhouse.shortcuts import ReconnectMixin


if SqlDbConf.RDB_TYPE == 'mysql':
    # 断线重连+连接池
    class ReconnectPooledMySQLDatabase(ReconnectMixin, PooledMySQLDatabase): # type: ignore
        _instance = None

        @classmethod
        def get_db_instance(cls):
            if not cls._instance:
                cls._instance = cls(
                    database=SqlDbConf.DATABASE,
                    max_connections=SqlDbConf.POOL_SIZE,
                    user=SqlDbConf.USER,
                    password=SqlDbConf.PASSWORD,
                    host=SqlDbConf.HOST,
                    port=SqlDbConf.PORT,
                    stale_timeout=300,
                )
            return cls._instance
elif SqlDbConf.RDB_TYPE == 'sqlite':
    sqlite_db = SqliteDatabase(PathProj.resolve_sqlite_db_path(SqlDbConf.SQLITE_DB_PATH), timeout=20)
else:
    raise NotImplementedError('Unsupported database type')


def db():
    if SqlDbConf.RDB_TYPE == 'mysql':
        return ReconnectPooledMySQLDatabase.get_db_instance()
    elif SqlDbConf.RDB_TYPE == 'sqlite':
        return sqlite_db
    else:
        raise NotImplementedError('Unsupported database type')


# 判断是否存在SysUser表，如不存在则初始化库
def create_rds_table():
    db_instance = db()
    from .entity.table_user import SysUser, SysRoleAccessMeta, SysUserRole, SysGroupUser, SysRole, SysGroupRole, SysGroup
    from .entity.table_announcement import SysAnnouncement
    from .entity.table_oauth2 import OAuth2Client, OAuth2AuthorizationCode, OAuth2Token
    from .entity.table_apscheduler import SysApschedulerResults, SysApschedulerExtractValue, SysApschedulerRunning
    from .entity.table_notify_api import SysNotifyApi
    from .entity.table_listen_api import SysListenApi
    from .entity.table_listen_task import ApschedulerJobsActiveListen, ApschedulerListenCheckpoint
    from .entity.table_agent import (
        AgentAction, AgentConversation, AgentConversationProfile, AgentMessage,
        AgentProviderOptions, AgentProviderProfile, AgentProviderState, AgentRemoteSession,
    )
    from .entity.table_task_center import MODELS as TASK_CENTER_MODELS

    db_instance.create_tables(
        [
            SysUser,
            SysRoleAccessMeta,
            SysUserRole,
            SysGroupUser,
            SysRole,
            SysGroupRole,
            SysGroup,
            SysAnnouncement,
            OAuth2Client,
            OAuth2AuthorizationCode,
            OAuth2Token,
            SysApschedulerResults,
            SysApschedulerExtractValue,
            SysApschedulerRunning,
            SysNotifyApi,
            SysListenApi,
            ApschedulerJobsActiveListen,
            ApschedulerListenCheckpoint,
            AgentConversation,
            AgentConversationProfile,
            AgentProviderProfile,
            AgentProviderOptions,
            AgentProviderState,
            AgentRemoteSession,
            AgentMessage,
            AgentAction,
            *TASK_CENTER_MODELS,
        ],
        safe=True,
    )


def init_rds_data(initial_admin_password=None):
    db_instance = db()
    from .entity.table_user import SysUser, SysUserRole, SysRole
    from datetime import datetime
    import getpass
    import os
    import sys
    from common.utilities.util_password import hash_password

    with db_instance.atomic():
        existing_admin = SysUser.get_or_none(SysUser.user_name == 'admin')
        password_hash = existing_admin.password_sha256 if existing_admin else None
        if existing_admin is None:
            password = initial_admin_password
            if password is None:
                password = os.environ.get('SMART_AGENT_INITIAL_ADMIN_PASSWORD')
            if password is None and sys.stdin.isatty():
                password = getpass.getpass('Initial admin password (16-1024 characters): ')
                if password != getpass.getpass('Confirm initial admin password: '):
                    raise ValueError('Initial admin passwords do not match')
            if not isinstance(password, str) or not 16 <= len(password) <= 1024 or password.isspace():
                raise ValueError(
                    'A unique 16-1024 character initial admin password is required. '
                    'Use an interactive terminal or SMART_AGENT_INITIAL_ADMIN_PASSWORD.')
            password_hash = hash_password(password)
        SysRole.get_or_create(
            role_name='admin',
            defaults={
                'role_status': True,
                'update_datetime': datetime.now(),
                'update_by': 'admin',
                'create_datetime': datetime.now(),
                'create_by': 'admin',
                'role_remark': '超级管理员角色',
            },
        )
        SysUser.get_or_create(
            user_name='admin',
            defaults={
                'user_full_name': '超级管理员',
                'password_sha256': password_hash,
                'user_status': True,
                'user_sex': '未知',
                'user_email': '',
                'phone_number': '',
                'create_by': 'admin',
                'create_datetime': datetime.now(),
                'update_by': 'admin',
                'update_datetime': datetime.now(),
                'user_remark': '',
                'otp_secret': '',
            },
        )
        SysUserRole.get_or_create(user_name='admin', role_name='admin')

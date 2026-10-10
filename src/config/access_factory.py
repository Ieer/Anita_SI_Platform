# 本应用的权限工厂，此处手动导入应用模块 - 内置应用，请勿修改
import importlib
import json
import keyword
from pathlib import Path

from dash_view.application.access_ import role_mgmt, user_mgmt, group_auth, group_mgmt
from dash_view.application.dashboard_ import monitor
from dash_view.application.person_ import personal_info
from dash_view.application.message_ import announcement
from dash_view.application.task_ import overview, task_mgmt, windows_scheduler as task_windows_scheduler, task_log
from dash_view.application.setting_ import notify_api, listen_api

##################### 【新增】内置应用 #####################
################## 【开始】此处导入您的应用 ###################
from dash_view.application.example_app import subapp1, subapp2  # noqa: F401

# 内置应用列表
def load_application_views(registry_path=None):
    path = Path(registry_path) if registry_path else Path(__file__).with_name('applications.json')
    if not path.exists():
        return []
    entries = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(entries, list) or not all(isinstance(entry, str) for entry in entries):
        raise ValueError('applications.json must contain a list of page module paths')
    if len(set(entries)) != len(entries):
        raise ValueError('Duplicate page modules in applications.json')
    views = []
    for entry in entries:
        parts = entry.split('.')
        if len(parts) < 2 or any(not part.isascii() or not part.isidentifier() or keyword.iskeyword(part) or part.startswith('_') for part in parts):
            raise ValueError(f'Invalid application page path: {entry!r}')
        view = importlib.import_module(f'dash_view.application.{entry}')
        if not callable(getattr(view, 'render_content', None)) or not getattr(view, 'title', None):
            raise ValueError(f'{entry} must define title and render_content(menu_access, **kwargs)')
        views.append(view)
    return views


apps = [subapp2, subapp1, *load_application_views()]

################## 【结束】此处导入您的应用 ###################


class AccessFactory:
    from common.utilities.util_menu_access import MenuAccess
    from cacheout import Cache

    cache_dict_access_meta2menu_item = Cache()
    functional_access_meta = ('工作台-页面', '调度启动配置')

    views = [
        role_mgmt,        # 角色管理页面
        user_mgmt,        # 用户管理页面
        group_auth,       # 组授权页面
        group_mgmt,       # 组管理页面
        monitor,          # 监控页面
        personal_info,    # 个人信息页面
        announcement,     # 公告管理页面
        overview,        # 任务概览
        task_mgmt,        # 任务管理页面
        task_windows_scheduler,  # Windows任务页面
        task_log,         # 任务日志页面
        notify_api,       # 通知接口页面
        listen_api,       # 监听接口页面
        *apps,
    ]

    # 读取每个VIEW中配置的所有权限，生成权限与模块路径的映射，缓存10秒
    @classmethod
    @cache_dict_access_meta2menu_item.memoize(ttl=10, typed=True)
    def get_dict_access_meta2menu_item(cls):
        mapping = dict.fromkeys(cls.functional_access_meta)
        for view in cls.views:
            permissions = getattr(view, 'access_metas', None)
            permissions = permissions() if callable(permissions) else permissions
            if not isinstance(permissions, (tuple, list)) or not permissions:
                raise ValueError(f'{view.__name__}: access_metas must be a non-empty tuple or list')
            for permission in permissions:
                if not isinstance(permission, str) or not permission.strip() or len(permission) > 32:
                    raise ValueError(f'{view.__name__}: invalid permission {permission!r}')
                if permission in mapping:
                    raise ValueError(f'Duplicate permission {permission!r}: {mapping[permission]} and {view.__name__}')
                mapping[permission] = view.__name__.removeprefix('dash_view.application.')
        return mapping

    # 基础默认权限，主页和个人中心，每人都有，无需分配
    default_access_meta = (
        '个人信息-页面',   
        '工作台-页面', 
        '监控页-页面',
    )

    # 团队管理员默认权限
    group_access_meta = ('团队授权-页面',)

    # 系统管理员默认权限
    admin_access_meta = (
        'rpa_scheduler',
        '调度启动配置',
        'Windows任務管理-页面',
        '用户管理-页面',
        '角色管理-页面',
        '团队管理-页面',
        '公告管理-页面',
        '任务管理-页面',
        '任务日志-页面',
        '通知接口-页面',
        '监听接口-页面',
    )

    # 内置可以分配的权限 
    assignable_access_meta = ('rpa_scheduler', '任务管理-页面', '任务日志-页面', 'Windows任務管理-页面')

    # 检查数据库和应用权限
    @classmethod
    def check_access_meta(cls) -> None:
        from common.utilities.util_logger import Log

        logger = Log.get_logger(__name__)

        # 附加权限检查
        outliers = (set(cls.default_access_meta) | set(cls.group_access_meta) | set(cls.admin_access_meta)) - set(cls.get_dict_access_meta2menu_item().keys())
        if outliers:
            logger.error(f'附加权限中存在未定义的权限：{outliers}')
            raise ValueError(f'附加权限中存在未定义的权限：{outliers}')

        # 数据库检查
        from database.sql_db.repositories import user_repository

        outliers = set(user_repository.get_all_access_meta_for_setup_check()) - set(cls.get_dict_access_meta2menu_item().keys())
        if outliers:
            logger.warning(f'数据库中存在已停用页面的权限，将忽略且不授予访问：{outliers}')

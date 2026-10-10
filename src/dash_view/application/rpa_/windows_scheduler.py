from dash import dcc
from common.utilities.util_menu_access import MenuAccess

title = '任务排程器'
icon = 'antd-windows'
order = 2
access_metas = ('Windows任務管理-页面',)


def render_content(menu_access: MenuAccess, **kwargs):
    return dcc.Location(id='legacy-windows-scheduler-redirect', href='/task_/windows_scheduler', refresh=True)

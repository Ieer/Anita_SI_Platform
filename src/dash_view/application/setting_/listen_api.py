from common.utilities.util_menu_access import MenuAccess
import feffery_antd_components as fac
from common.utilities.util_logger import Log
from dash_components.channel_settings import render_channel_settings
from dash_callback.application.setting_ import listen_api_c  # noqa
from i18n import t__setting


# 二级菜单的标题、图标和显示顺序
title = '监听接口'
icon = None
logger = Log.get_logger(__name__)
order = 2
access_metas = ('监听接口-页面',)


def render_content(menu_access: MenuAccess, **kwargs):
    return render_channel_settings(
        'listen',
        [
            fac.AntdButton(t__setting('添加邮件POP3协议'), type='primary',
                           id='listen-api-add-email-pop3', icon=fac.AntdIcon(icon='antd-plus')),
        ],
        listen_api_c.get_listen_api(), listen_api_c.get_tabs_items(),
    )

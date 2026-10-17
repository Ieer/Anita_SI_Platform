from common.utilities.util_menu_access import MenuAccess
import feffery_antd_components as fac
from common.utilities.util_logger import Log
from dash_components.channel_settings import render_channel_settings
from dash_callback.application.setting_ import notify_api_c  # noqa
from i18n import t__setting


# 二级菜单的标题、图标和显示顺序
title = '通知接口'
icon = None
logger = Log.get_logger(__name__)
order = 1
access_metas = ('通知接口-页面',)


def render_content(menu_access: MenuAccess, **kwargs):
    return render_channel_settings(
        'notify',
        [
            fac.AntdButton(t__setting('添加Server酱'), id='notify-api-add-serverchan',
                           icon=fac.AntdIcon(icon='antd-plus')),
            fac.AntdButton(t__setting('添加Gewechat'), id='notify-api-add-Gewechat',
                           icon=fac.AntdIcon(icon='antd-plus')),
            fac.AntdButton(t__setting('添加企业微信群机器人'), id='notify-api-add-wecom-group-robot',
                           icon=fac.AntdIcon(icon='antd-plus')),
            fac.AntdButton(t__setting('添加邮件SMTP协议'), id='notify-api-add-email-smtp',
                           icon=fac.AntdIcon(icon='antd-plus')),
        ],
        notify_api_c.get_notify_api(), notify_api_c.get_tabs_items(),
    )

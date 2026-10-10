import feffery_antd_components as fac
from dash import html
from config.dashgo_conf import ShowConf
from common.utilities.util_menu_access import MenuAccess


def render_aside_content(menu_access: MenuAccess):
    navigation_items = [item for item in menu_access.menu if item['props']['key'] != '/person_']
    return html.Nav([
        html.Div([
            html.Div(fac.AntdIcon(icon='antd-code'), className='platform-brand-mark'),
            html.Div([
                html.Span(ShowConf.APP_NAME, id='logo-text'),
                html.Span('OPERATIONS CONSOLE', className='platform-brand-caption'),
            ], className='platform-brand-copy'),
        ], className='platform-sidebar-brand'),
        html.Button(fac.AntdIcon(icon='antd-close'), id='platform-navigation-close',
                className='platform-navigation-close', type='button', title='关闭主导航',
                **{'aria-label': '关闭主导航'}),
        html.Div('应用导航', className='platform-nav-caption'),
        fac.AntdMenu(
            id='main-menu', menuItems=navigation_items, mode='inline', theme='light',
            onlyExpandCurrentSubMenu=True,
            expandIcon={'expand': fac.AntdIcon(icon='antd-right'),
                        'collapse': fac.AntdIcon(icon='antd-caret-down')},
        ),
        html.Div([html.Span('SMART AGENT'), html.Span('OPERATIONS / AUTOMATION')], className='platform-nav-footer'),
    ], id='platform-navigation', **{'aria-label': '主导航'})

import os

import feffery_antd_components as fac
from dash import html
from dash_view.framework.aside import render_aside_content
from dash_view.framework.head import render_head_content
from common.utilities.util_browser import get_browser_info
from common.utilities.util_menu_access import MenuAccess
import feffery_utils_components as fuc
from i18n import t__default
from dash_view.framework import func_main, agent_widget
from dash_callback.pages.main_c import parse_url, is_independent, resolve_page
from dash_view.pages import page_403, page_404
import dash_callback.pages.main_c  # noqa

def render_content(menu_access: MenuAccess, href: str):
    demo_mode = os.environ.get('SMART_AGENT_DEMO_MODE') == '1'
    _, url_menu_item, url_query, _, param = parse_url(href=href)
    module_page = resolve_page(url_menu_item, menu_access)
    if module_page in (page_403, page_404):
        return module_page.render_content()
    # 是否为独立页面，直接返回独立页面，无需渲染框架
    if is_independent(url_query):
        return module_page.render_content(menu_access, **param)
    return fac.AntdRow(
        [
            agent_widget.render_content(menu_access),
            # 功能组件注入
            fac.Fragment(func_main.render()),
            # 菜单列
            fac.AntdCol(
                fac.AntdSider(
                    render_aside_content(menu_access),
                    collapsed=False,
                    collapsible=True,
                    collapsedWidth=56,
                    width=232,
                    trigger=None,
                    id='menu-collapse-sider',
                ),
                flex='None',
                className='platform-sidebar-column',
            ),
            html.Button(
                id='platform-nav-backdrop',
                className='platform-nav-backdrop',
                tabIndex=-1,
                title='关闭主导航',
                **{'aria-label': '关闭主导航'},
            ),
            # 内容列
            fac.AntdCol(
                [
                    # head块，包括菜单折叠、面包屑导航、用户信息、全局功能按钮
                    fac.AntdRow(
                        render_head_content(menu_access),
                        align='middle',
                        className='platform-topbar',
                        style={'height': '50px'},
                    ),
                    html.Div([
                        fac.AntdIcon(icon='antd-info-circle'),
                        html.Span('演示模式'),
                        html.Span('合成指标与任务 · Anita 固定剧本 · 无模型与外部执行'),
                    ], id='platform-demo-banner', className='platform-demo-banner', role='status') if demo_mode else None,
                    # tabs块
                    fac.AntdRow(
                        fuc.FefferyDiv(
                            fac.AntdTabs(
                                id='tabs-container',
                                tabPaneAnimated=False,
                                tabBarRightExtraContent=fac.AntdSpace(
                                    [
                                        fac.AntdTooltip(
                                            fac.AntdIcon(
                                                id='tabs-open-independent',
                                                icon='antd-export',
                                                debounceWait=300,
                                                style={'cursor': 'pointer', 'marginRight': '10px', 'fontSize': '1.2em'},
                                            ),
                                            title=t__default('新页面打开'),
                                            placement='left',
                                        ),
                                        fac.AntdTooltip(
                                            fac.AntdIcon(
                                                id='tabs-refresh',
                                                icon='fc-synchronize',
                                                debounceWait=300,
                                                style={'cursor': 'pointer', 'marginRight': '10px', 'fontSize': '1.2em'},
                                            ),
                                            title=t__default('刷新'),
                                            placement='left',
                                        ),
                                    ]
                                ),
                                size='small',
                                items=[],
                                type='editable-card',
                                className={
                                    'width': '100%',
                                    'height': 'var(--platform-tabs-height)',
                                },
                            ),
                            className={
                                'width': '100%',
                                'height': 'var(--platform-tabs-height)',
                                ### 美化滚动条
                                '& .ant-tabs-content-holder': {
                                    'height': '100%',
                                    'overflow': 'auto',
                                    'scrollbar-width': 'thin',
                                    'scrollbar-color': 'var(--platform-border) var(--platform-surface)',
                                },
                                **(
                                    {
                                        '& .ant-tabs-content-holder::-webkit-scrollbar': {'width': '8px'},
                                        '& .ant-tabs-content-holder::-webkit-scrollbar-track': {'background': 'var(--platform-surface)', 'border-radius': '10px'},
                                        '& .ant-tabs-content-holder::-webkit-scrollbar-thumb': {'background': 'var(--platform-border)', 'border-radius': '10px'},
                                        '& .ant-tabs-content-holder::-webkit-scrollbar-thumb:hover': {'background': 'var(--platform-muted)'},
                                    }
                                    if get_browser_info().type != 'firefox'
                                    else {}
                                ),  ## 火狐浏览器不支持通过伪类自定义滚动条样式
                                '& .ant-tabs-content-holder > .ant-tabs-content': {'height': '100%'},
                                '& .ant-tabs-content-holder > .ant-tabs-content > .ant-tabs-tabpane': {'height': '100%', 'paddingBottom': '8px'},
                                '& .ant-tabs-nav': {'margin': '8px 0 8px 0'},
                            },
                        ),
                        className={'height': 'var(--platform-tabs-height)'},
                        gutter=0,
                    ),
                ],
                flex='auto',
                className='platform-content-column',
                style={'minWidth': 0, 'overflow': 'hidden'},
            ),
        ],
        className='platform-shell platform-demo-shell' if demo_mode else 'platform-shell',
        style={'width': '100vw', 'height': '100dvh'},
        wrap=False,
    )

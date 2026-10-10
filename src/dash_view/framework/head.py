import feffery_antd_components as fac
from dash_view.framework.lang import render_lang_content
from common.utilities.util_menu_access import MenuAccess
from dash_view.framework.user_avatar import render_user_avatar
from dash import html
from server import app
from dash.dependencies import Input, Output, State
import dash
from dash import set_props
from dash.exceptions import PreventUpdate
from database.sql_db.dao import dao_announcement
from i18n import t__access, t__other, t__default


def render_appearance():
    return html.Details([
        html.Summary(fac.AntdIcon(icon='antd-setting'), title=t__default('外观'),
                     **{'aria-label': t__default('外观')}),
        html.Div([
            html.Span(t__default('显示模式'), className='platform-appearance-label'),
            html.Div([
                html.Button(t__default(label), type='button', **{
                    'data-theme-mode': mode, 'aria-pressed': 'false'})
                for mode, label in [('light', '日间'), ('dark', '夜间'), ('system', '跟随系统')]
            ], className='platform-theme-modes', role='group', **{'aria-label': t__default('显示模式')}),
            html.Span(t__default('配色'), className='platform-appearance-label'),
            html.Div([
                html.Button([html.Span(className=f'platform-swatch is-{palette}'), t__default(label)],
                            type='button', **{'data-theme-palette': palette, 'aria-pressed': 'false'})
                for palette, label in [('teal', '青绿'), ('blue', '蓝色'), ('rose', '玫红')]
            ], className='platform-theme-palettes', role='group', **{'aria-label': t__default('配色')}),
            html.Div([html.Label(t__default('高对比'), htmlFor='platform-theme-contrast'),
                      fac.AntdSwitch(id='platform-theme-contrast', checked=False,
                                     **{'aria-label': t__default('高对比')})],
                     className='platform-theme-contrast'),
        ], className='platform-appearance-panel'),
    ], id='platform-appearance', className='platform-appearance')


app.clientside_callback(
    """checked => {
        window.platformTheme.update({contrast: checked ? 'high' : 'normal'});
        return window.dash_clientside.no_update;
    }""",
    Output('platform-appearance', 'title'), Input('platform-theme-contrast', 'checked'),
    prevent_initial_call=True,
)


def render_head_content(menu_access: MenuAccess):
    return [
        # 页首左侧折叠按钮区域
        fac.AntdCol(
            fac.AntdButton(
                fac.AntdIcon(id='btn-menu-collapse-sider-menu-icon', icon='antd-menu-fold'),
                id='btn-menu-collapse-sider-menu',
                title='展开或折叠主导航',
                **{'aria-label': '展开或折叠主导航'},
                type='text',
                shape='circle',
                size='large',
                style={
                    'marginLeft': '5px',
                    'marginRight': '15px',
                    'background': 'rgba(0,0,0,0)',
                },
            ),
            style={
                'height': '100%',
                'display': 'flex',
                'alignItems': 'center',
            },
            flex='None',
        ),
        # 页首面包屑区域
        fac.AntdCol(
            fac.AntdSpace(
                [
                    fac.AntdBreadcrumb(
                        items=[{'title': t__access('首页'), 'href': '/dashboard_/monitor'}],
                        id='main-header-breadcrumb',
                        style={
                            'height': '100%',
                            'display': 'flex',
                            'alignItems': 'center',
                        },
                    ),
                    *(
                        [
                            fac.AntdAlert(
                                message=dao_announcement.get_announcement(),
                                type='info',
                                banner=True,
                                showIcon=True,
                                icon='📢',
                                messageRenderMode='loop-text',
                                className={
                                    'backgroundColor': 'rgba(0,0,0,0)',
                                    'maxWidth': '20em',
                                    'marginLeft': '50px',
                                    'border': '1px',
                                },
                            )
                        ]
                        if dao_announcement.get_announcement()
                        else []
                    ),
                ],
                style={
                    'height': '100%',
                    'alignItems': 'center',
                },
                wrap=False,
            ),
            id='main-header-breadcrumb-col',
            flex='1',
        ),
        # 页首右侧用户信息区域
        fac.AntdCol(
            fac.AntdSpace(
                [
                    fac.AntdBadge(
                        render_user_avatar(menu_access.user_info, size=36, component_id='global-head-avatar'),
                        # count=6, # TODO: 消息通知
                        size='small',
                    ),
                    html.Div(fac.AntdDropdown(
                        id='global-head-user-name-dropdown',
                        title=menu_access.user_name,
                        arrow=True,
                        trigger='click',
                        menuItems=[
                            {
                                'title': t__access('个人中心'),
                                'key': '个人信息',
                                'icon': 'antd-idcard',
                            },
                            {'isDivider': True},
                            {
                                'title': t__other('退出登录'),
                                'key': '退出登录',
                                'icon': 'antd-logout',
                            },
                        ],
                        placement='bottomRight',
                    ), id='main-head-user-menu'),
                    fac.AntdTooltip(
                        fac.AntdIcon(
                            id='global-full-screen',
                            icon='antd-full-screen',
                            debounceWait=300,
                            style={'cursor': 'pointer', 'marginLeft': '12px', 'fontSize': '1.2em'},
                        ),
                        title=t__default('全屏'),
                        placement='left',
                    ),
                    render_appearance(),
                    render_lang_content(),
                ],
                style={
                    'height': '100%',
                    'display': 'flex',
                    'alignItems': 'center',
                    'paddingRight': '10px',
                },
                wrap=False,
            ),
            id='main-head-user-region',
            flex='None',
        ),
    ]


# 个人信息，退出登录
@app.callback(
    Output('main-dcc-url', 'pathname', allow_duplicate=True),
    Input('global-head-user-name-dropdown', 'nClicks'),
    State('global-head-user-name-dropdown', 'clickedKey'),
    prevent_initial_call=True,
)
def callback_func(nClicks, clickedKey):
    if clickedKey == '退出登录':
        from common.utilities.util_jwt import clear_access_token_from_session

        clear_access_token_from_session()
        set_props('global-reload', {'reload': True})
        return dash.no_update
    elif clickedKey == '个人信息':
        return '/person_/personal_info'
    return PreventUpdate


# 全屏
app.clientside_callback(
    """
    (nClicks) => {
        if (!document.fullscreenElement) {
            document.documentElement.requestFullscreen();
        } else {
            if (document.exitFullscreen) {
            document.exitFullscreen();
            }
        }
    }
    """,
    Input('global-full-screen', 'nClicks'),
    prevent_initial_call=True,
)

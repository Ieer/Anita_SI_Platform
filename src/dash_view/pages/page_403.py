import feffery_antd_components as fac
from dash import html
from common.constant import HttpStatusConstant
from i18n import t__default

title = "403"

def render_content(*args, **kwargs):
    return html.Div(
        [
            html.Div(
                [
                    fac.AntdResult(
                        status=str(HttpStatusConstant.FORBIDDEN), # type: ignore
                        title=t__default('您没有权限访问该页面'),
                        subTitle=t__default('如需访问，请联系系统管理员'),
                        style={'paddingBottom': 0, 'paddingTop': 0},
                    ),
                    fac.AntdButton(
                        t__default('回到首页'), type='link', href='/', target='_self'
                    ),
                ],
                style={'textAlign': 'center'},
            )
        ],
        style={
            'height': '100vh',
            'width': '100vw',
            'display': 'flex',
            'alignItems': 'center',
            'justifyContent': 'center',
        },
    )

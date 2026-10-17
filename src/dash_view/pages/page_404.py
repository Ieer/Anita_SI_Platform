import feffery_antd_components as fac
from dash import html
from common.constant import HttpStatusConstant
from i18n import t__default

title = "404"

def render_content(*args, **kwargs):
    return html.Div(
        [
            html.Div(
                [
                    fac.AntdResult(
                        status=str(HttpStatusConstant.NOT_FOUND), # type: ignore
                        title=t__default('页面不存在'),
                        subTitle=t__default('检查您的网址输入是否正确'),
                        style={'paddingBottom': 0, 'paddingTop': 0},
                    ),
                    fac.AntdButton(t__default('回到首页'), type='link', href='/', target='_self'),
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

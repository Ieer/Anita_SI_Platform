import feffery_utils_components as fuc
import feffery_antd_components as fac
from config.dashgo_conf import ShowConf, JwtConf, LoginConf
from dash import dcc, html
from dash_view.framework.lang import render_lang_content
import dash_callback.pages.login_c  # noqa
from i18n import t__other


def render_content():
    stages = [
        ('antd-unordered-list', '01', '任务队列', 'QUEUE'),
        ('antd-lock', '02', '人工审批', 'APPROVAL'),
        ('antd-code', '03', '执行任务', 'EXECUTION'),
        ('antd-file-text', '04', '运行记录', 'TRACE'),
    ]
    return html.Main([
        fac.Fragment(id='login-message-container'),
        html.Div(id='login-webgl', className='login-webgl', **{'aria-hidden': 'true'}),
        html.Header([
            html.Div([
                html.Div(fac.AntdIcon(icon='antd-code'), className='platform-brand-mark'),
                html.Span(ShowConf.APP_NAME),
            ], className='login-brand'),
            render_lang_content(),
        ], className='login-header'),
        html.Div([
            html.Div([
                html.Div('AGENT / OPERATIONS', className='login-scene-caption'),
                html.H1(ShowConf.APP_NAME, className='login-product-name'),
                html.Div([
                    html.Div([
                        html.Span(number, className='login-stage-number'),
                        html.Div([html.Strong(t__other(label)), html.Span(code)], className='login-stage-name'),
                    ], className=f'login-stage login-stage-{index}')
                    for index, (icon, number, label, code) in enumerate(stages)
                ], className='login-pipeline'),
                html.Div([html.Span('HUMAN IN THE LOOP'), html.Span('01 — 04')],
                         className='login-scene-footer'),
            ], className='login-scene', **{'aria-hidden': 'true'}),
            html.Section([
                html.Div('WORKSPACE ACCESS', className='login-eyebrow'),
                html.H2(t__other('登录'), id='login-title'),
                fac.AntdTabs(
                    id='login-tabs',
                    items=[
                        {
                            'key': '密码登录', 'label': t__other('密码登录'),
                            'children': html.Div([
                                html.Div([
                                    html.Label(t__other('用户名'), htmlFor='login-username'),
                                    fac.AntdInput(
                                        id='login-username', prefix=fac.AntdIcon(icon='antd-user'),
                                        placeholder=t__other('请输入用户名'), autoComplete='on',
                                        **{'aria-label': t__other('用户名')},
                                    ),
                                ], className='login-field'),
                                html.Div([
                                    html.Label(t__other('密码'), htmlFor='login-password'),
                                    fac.AntdInput(
                                        id='login-password', prefix=fac.AntdIcon(icon='antd-lock'),
                                        mode='password', passwordUseMd5=False,
                                        placeholder=t__other('请输入密码'), autoComplete='on',
                                        **{'aria-label': t__other('密码')},
                                    ),
                                ], className='login-field'),
                                html.Div([
                                    fac.AntdInput(
                                        id='login-verify-code-input', placeholder=t__other('请输入验证码'),
                                        allowClear=True, **{'aria-label': t__other('验证码')},
                                    ),
                                    fuc.FefferyCaptcha(id='login-verify-code-pic', width=100,
                                                       charNum=LoginConf.VERIFY_CODE_CHAR_NUM),
                                ], id='login-verify-code-container', className='login-captcha',
                                    style={'display': 'none'}),
                                fac.AntdCheckbox(
                                    id='login-keep-login-status', checked=False,
                                    label=t__other('保持{}小时免登录').format(JwtConf.JWT_EXPIRE_MINUTES // 60),
                                ),
                                fac.AntdButton(
                                    t__other('登录'), id='login-submit', type='primary', block=True,
                                    icon=fac.AntdIcon(icon='antd-arrow-right'), className='login-submit',
                                ),
                                dcc.Store(id='login-store-need-vc', storage_type='local'),
                                dcc.Store(id='login-store-fc', storage_type='local'),
                                dcc.Store(id='login-password-sha256'),
                            ], className='login-fields'),
                        },
                        {
                            'key': '动态码登录', 'label': t__other('动态码登录'),
                            'children': html.Div([
                                html.Div([
                                    html.Label(t__other('用户名'), htmlFor='login-username-otp'),
                                    fac.AntdInput(
                                        id='login-username-otp', prefix=fac.AntdIcon(icon='antd-user'),
                                        placeholder=t__other('请输入用户名'), autoComplete='on',
                                        **{'aria-label': t__other('用户名')},
                                    ),
                                ], className='login-field'),
                                html.Div([
                                    html.Div(t__other('动态码：'), id='login-otp-label', className='login-field-label'),
                                    fac.AntdOTP(id='login-otp', length=6, **{'aria-labelledby': 'login-otp-label'}),
                                ], className='login-field'),
                            ], className='login-fields'),
                        },
                    ], size='default', tabBarGutter=28,
                ),
                html.Div(id='login-status', className='login-status', role='status',
                         **{'aria-live': 'polite', 'aria-atomic': 'true'}),
                html.Div([fac.AntdIcon(icon='antd-lock'), html.Span('授权访问 / AUTHORIZED ACCESS')],
                         className='login-access-note'),
                fuc.FefferyLocation(id='login-url-location'),
            ], id='login-container', className='login-form-section', **{'aria-labelledby': 'login-title'}),
        ], className='login-layout'),
        html.Footer([html.Span(ShowConf.APP_NAME), html.Span('OPERATIONS · AUTOMATION')], className='login-footer'),
    ], className='platform-login')

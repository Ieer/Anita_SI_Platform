import feffery_antd_components as fac
from dash import dcc, html

from common.agent import model_status
from common.agent_providers import registered_provider_ids
from common.utilities.util_menu_access import MenuAccess
from common.utilities.util_logger import Log
from database.sql_db.repositories import agent_provider_repository
from dash_callback.application.setting_ import agent_provider_c
from i18n import t__setting


title = 'Agent 接入'
icon = None
order = 3
access_metas = ('Agent配置-页面',)
logger = Log.get_logger(__name__)


def render_content(menu_access: MenuAccess, **kwargs):
    if not menu_access.has_access('Agent配置-页面'):
        return None
    try:
        providers = registered_provider_ids()
        provider_error = ''
    except Exception:
        providers = ()
        logger.exception('Could not load registered Agent providers.')
        provider_error = t__setting('外部 Provider 加载失败，请检查服务端模块配置。')

    try:
        profiles = agent_provider_repository.list_profiles()
        active_profile_id = agent_provider_repository.default_profile_id()
        profile_error = ''
    except Exception:
        logger.exception('Could not load Agent provider profile settings page.')
        profiles = []
        active_profile_id = None
        profile_error = t__setting('配置数据库不可用，当前无法读取或保存 Agent 配置。')
    provider_options = [
        {'label': {'openai_compatible': t__setting('OpenAI 兼容接口'),
                   'columbus_agent': 'Columbus Agent'}.get(provider, provider),
         'value': provider}
        for provider in providers
    ]
    active_status = model_status(active_profile_id) if active_profile_id else model_status()
    environment_status = model_status()
    return html.Main([
        dcc.Interval(id='agent-provider-init', interval=50, max_intervals=1),
        dcc.Store(id='agent-provider-refresh', data=0),
        dcc.Store(id='agent-provider-edit-id'),
        fac.AntdAlert(
            message=t__setting('凭证只在服务端加密保存，不会回传到浏览器。更新已有凭证时填写新值；留空表示保留已保存的凭证。'),
            description=t__setting('只有系统管理员可以维护服务配置；普通用户只能在对话中选择已保存的配置。连接测试为当前参数的一次验证，不保证服务始终可用。'),
            type='info', showIcon=True, className='agent-config-notice',
        ),
        html.Div([
            html.Section([
                html.Div([
                    html.Div([
                        html.Span('01', className='agent-config-step'),
                        html.Div([html.H2(t__setting('配置档')), html.P(t__setting('快速切换不同比赛平台与模型服务。'))]),
                    ], className='agent-config-card-heading'),
                    fac.AntdButton(t__setting('新建配置'), id='agent-provider-add', type='primary',
                                   icon=fac.AntdIcon(icon='antd-plus')),
                ], className='agent-config-card-topline'),
                fac.AntdSelect(
                    id='agent-provider-profile-list',
                    options=[{'label': row['name'], 'value': row['profile_id']} for row in profiles],
                    value=profiles[0]['profile_id'] if profiles else None,
                    placeholder=t__setting('选择一个配置档'),
                    style={'width': '100%'},
                    **{'aria-label': t__setting('选择要编辑的 Agent 配置档')},
                ),
                html.Div([
                    html.Span(t__setting('{count} / 12 个配置档').format(count=len(profiles)), id='agent-provider-count'),
                    html.Span(t__setting('切换配置不会共享远端会话'), className='agent-config-safe-note'),
                ], className='agent-config-list-foot'),
                html.Div([
                    html.Span(t__setting('当前默认服务'), className='agent-config-status-label'),
                    html.Strong(active_status['model'] or t__setting('尚未连接'), id='agent-config-live-status'),
                    html.Span(agent_provider_c.translate_provider_message(active_status['message']),
                              id='agent-config-live-detail', className='agent-config-status-detail'),
                ], className='agent-config-live-card'),
                html.Div([
                    html.Span('ENV', className='agent-config-env-icon'),
                    html.Div([
                        html.Strong(t__setting('服务端环境配置')),
                        html.Span(t__setting('保留现有环境变量接入方式，可作为兼容配置使用。')),
                    ]),
                    html.Span(t__setting('已配置' if environment_status['ready'] else '未配置'),
                              className='agent-config-tag'),
                ], className='agent-config-env-row'),
            ], className='agent-config-card agent-config-profile-card'),
            html.Section([
                html.Div([
                    html.Span('02', className='agent-config-step'),
                    html.Div([html.H2(t__setting('连接参数')), html.P(t__setting('保存后先测试，再分配给团队对话。'))]),
                ], className='agent-config-card-heading'),
                html.Div(id='agent-provider-editor-status', className='agent-config-editor-hint',
                         role='status'),
                html.Div([
                    html.Label([t__setting('配置名称'), html.Span(t__setting('必填'))], htmlFor='agent-provider-name'),
                    fac.AntdInput(id='agent-provider-name', placeholder=t__setting('例如：比赛主 Agent / Dify 生产环境'),
                                  maxLength=64),
                ], className='agent-config-field'),
                html.Div([
                    html.Label(['Provider', html.Span(t__setting('必填'))], htmlFor='agent-provider-kind'),
                    fac.AntdSelect(id='agent-provider-kind', options=provider_options,
                                   value='openai_compatible' if 'openai_compatible' in providers else None,
                                   placeholder=t__setting('选择服务协议'), style={'width': '100%'}),
                    html.Small(t__setting('Columbus 使用专用协议；其他非兼容平台需注册独立 Provider。')),
                ], className='agent-config-field'),
                html.Div([
                    html.Label([t__setting('服务地址'), html.Span(t__setting('必填'))], htmlFor='agent-provider-url'),
                    fac.AntdInput(id='agent-provider-url', placeholder='https://api.example.com/v1',
                                  maxLength=512),
                    html.Small(t__setting('远程服务必须使用 HTTPS；仅本机服务可使用 HTTP。')),
                ], className='agent-config-field'),
                html.Div([
                    html.Label([t__setting('模型名称'), html.Span(t__setting('OpenAI 兼容接口必填'))], htmlFor='agent-provider-model'),
                    fac.AntdInput(id='agent-provider-model', placeholder=t__setting('填写服务商提供的模型 ID'),
                                  maxLength=191),
                ], className='agent-config-field', id='agent-provider-model-field'),
                html.Div([
                    html.Label([t__setting('API 凭证 / APIM Subscription Key'), html.Span(t__setting('仅服务端可见'))],
                               htmlFor='agent-provider-key'),
                    fac.AntdInput(id='agent-provider-key', mode='password',
                                  placeholder=t__setting('新建配置请输入密钥；已有配置可留空保留'),
                                  autoComplete='new-password', maxLength=8192),
                    html.Small(t__setting('保存后不会再次显示凭证明文。请勿将密钥发送到聊天窗口。')),
                ], className='agent-config-field'),
                html.Div([
                    html.Div([
                        html.Span('COLUMBUS / OAUTH2', className='agent-config-eyebrow'),
                        html.P(t__setting('使用文本完整结果模式，不支持上游 SSE、异步任务查询及图片上传。'
                               '多轮问答要求上游返回原生 messages；连接测试会真实调用 Agent，可能产生用量或远端动作。'
                               'Columbus 远端插件不受本地任务审批控制，请优先使用只读 Agent。')),
                    ], className='agent-config-editor-hint'),
                    *[
                        html.Div([
                            html.Label(t__setting(label), htmlFor='agent-columbus-' + key),
                            fac.AntdInput(
                                id='agent-columbus-' + key,
                                mode='password' if key == 'client_secret' else 'default',
                                placeholder=t__setting(placeholder), maxLength=8192 if key == 'client_secret' else 512,
                                **({'autoComplete': 'new-password'} if key == 'client_secret' else {}),
                            ),
                        ], className='agent-config-field')
                        for key, label, placeholder in [
                            ('agent_id', 'Agent ID', '已发布 Agent 的 UUID'),
                            ('tenant_id', 'Tenant ID', 'Azure AD 租户 UUID'),
                            ('client_id', 'Client ID', '应用客户端 UUID'),
                            ('scope', 'Scope', 'api://资源标识/.default（按 QAS / PRD 分别配置）'),
                            ('client_secret', 'Client Secret', '新建请输入；编辑留空保留，不会回显'),
                        ]
                    ],
                ], id='agent-columbus-fields', hidden=True),
                fac.AntdCheckbox(id='agent-provider-default', checked=False,
                                 label=t__setting('设为默认；不勾选时保留当前默认项')),
                html.Div([
                    dcc.Markdown(id='agent-provider-status', className='agent-config-feedback',
                                 dangerously_allow_html=False),
                    html.Div([
                        fac.AntdButton(t__setting('测试连接'), id='agent-provider-test', type='default',
                                       icon=fac.AntdIcon(icon='antd-link')),
                        fac.AntdButton(t__setting('保存配置'), id='agent-provider-save', type='primary',
                                       icon=fac.AntdIcon(icon='antd-save')),
                        fac.AntdButton(t__setting('设为默认'), id='agent-provider-make-default', type='text'),
                        fac.AntdPopconfirm(
                            fac.AntdButton(t__setting('删除'), id='agent-provider-delete', type='text', danger=True),
                            title=t__setting('删除该配置？相关会话将切换到剩余默认服务，并清除远端会话映射；本地聊天记录保留。'),
                            okText=t__setting('确认删除'), cancelText=t__setting('取消'),
                            id='agent-provider-delete-confirm',
                        ),
                    ], className='agent-config-actions'),
                ], className='agent-config-command-bar'),
                html.Div(' '.join(item for item in [provider_error, profile_error] if item),
                         className='agent-config-error', role='status'),
            ], className='agent-config-card agent-config-form-card'),
        ], className='agent-config-grid'),
        html.Footer([
            fac.AntdIcon(icon='antd-safety-certificate'),
            html.Span(t__setting('本地权限与任务审批不随 Provider 切换而改变。外部 Agent 不能绕过平台白名单和人工审批。')),
        ], className='agent-config-security-footer'),
    ], className='agent-config-page', **{'aria-label': t__setting('Agent 接入')})

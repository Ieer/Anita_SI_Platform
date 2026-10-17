import os

from dash import dcc, html
import feffery_antd_components as fac

from common.utilities.util_menu_access import MenuAccess
from common.agent import model_status
from database.sql_db.repositories import agent_provider_repository
from dash_view.framework.agent_widget_i18n import agent_widget_i18n_data, translate_agent_message
from dash_view.framework.user_avatar import avatar_file_exists, render_user_avatar
from dash_view.framework.page_assistant import render_content as render_page_assistant
from i18n import t__dashboard
import dash_callback.pages.agent_widget_c  # noqa


def render_messages(messages, user_info=None):
    if not messages:
        return html.Div([
            html.Img(src='/assets/imgs/digital-human-mark.svg', alt='', className='agent-empty-portrait'),
            html.H2(t__dashboard('有什么想聊的？')),
        ], className='agent-empty')
    user_avatar_exists = avatar_file_exists(user_info) if any(message['role'] == 'user' for message in messages) else False
    tool_titles = {'list_tasks': t__dashboard('查询任务'),
                   'read_latest_log': t__dashboard('读取日志'),
                   'propose_run_task': t__dashboard('提议运行')}
    status_titles = {'ok': t__dashboard('完成'), 'unavailable': t__dashboard('不可用'),
                     'not_found': t__dashboard('未找到'), 'pending_approval': t__dashboard('待审批')}
    role_titles = {'user': t__dashboard('你'), 'assistant': 'Anita',
                   'error': t__dashboard('请求失败')}
    return [
        html.Article([
            html.Div([
                *([html.Img(src='/assets/imgs/digital-human-mark.svg', alt='Anita',
                            className='agent-message-avatar agent-message-avatar-assistant')]
                  if message['role'] == 'assistant' else []),
                html.Strong(role_titles.get(message['role'], 'Agent')),
                html.Time(message.get('created_at', '')[11:16],
                          title=message.get('created_at', '')[:19].replace('T', ' ')),
                     *([render_user_avatar(user_info, size=28,
                                      class_name='agent-message-avatar agent-message-avatar-user',
                                      avatar_exists=user_avatar_exists)] if message['role'] == 'user' else []),
                *([html.Span('!', className='agent-message-avatar agent-message-avatar-error',
                             **{'aria-label': t__dashboard('请求失败')})] if message['role'] == 'error' else []),
            ], className='agent-message-meta'),
            dcc.Markdown(message['content'], dangerously_allow_html=False, className='agent-message-content agent-markdown')
            if message['role'] == 'assistant' else html.Div(message['content'], className='agent-message-content'),
            *([
                html.Details([
                    html.Summary(f"{t__dashboard('工具记录')} · {len(message['trace'])}"),
                    html.Ol([
                        html.Li([
                            html.Strong(tool_titles.get(step['tool'], step['tool'])),
                            html.Span(status_titles.get(step['status'], step['status'])),
                            html.Code(step.get('arguments', '{}')),
                        ]) for step in message['trace']
                    ], className='agent-tool-trace'),
                ], className='agent-trace')
            ] if message.get('trace') else []),
        ], className=f"agent-message agent-message-{message['role']}")
        for message in messages
    ]


def render_actions(actions, can_approve):
    if not actions:
        return []
    statuses = {key: t__dashboard(label) for key, label in {
        'pending': '待审批', 'rejected': '已拒绝', 'expired': '已过期', 'blocked': '校验阻断',
        'submitting': '提交中或中断待核对', 'submitted': '调度器已接收',
        'unknown': '提交结果未知，待核对'}.items()}
    return html.Details([
        html.Summary(f"{t__dashboard('执行审批')} · {len(actions)}"),
        *[html.Article([
            html.Div([html.Strong(action['job_id']), html.Span(statuses.get(action['status'], action['status']))],
                     className='agent-action-heading'),
            html.P(action['summary']),
            html.Dl([
                html.Dt(t__dashboard('任务版本')), html.Dd(html.Code(action['task_digest'])),
                html.Dt(t__dashboard('运行 ID')), html.Dd(html.Code(action['run_id'])),
                html.Dt(t__dashboard('有效期至')), html.Dd(str(action['expires_at'])[:19]),
                *([html.Dt(t__dashboard('确认时间')), html.Dd(str(action['decided_at'])[:19])]
                  if action['decided_at'] else []),
            ], className='agent-action-metadata'),
            *([html.Div([
                html.P(t__dashboard('将以调度服务账户在本机运行已有脚本，可能写入文件或访问外部系统。'),
                       className='agent-action-warning'),
                fac.AntdCheckbox(id={'type': 'agent-action-consent', 'action_id': action['action_id']},
                                 checked=False, label=t__dashboard('已核对任务脚本及影响'),
                                 disabled=not can_approve),
                html.Div([
                    html.Button([fac.AntdIcon(icon='antd-check'), t__dashboard('批准并运行')],
                                id={'type': 'agent-action-decision', 'action_id': action['action_id'], 'decision': 'approve'},
                                n_clicks=0, disabled=not can_approve, className='agent-action-approve',
                                **{'aria-label': t__dashboard('批准并运行')}),
                    html.Button([fac.AntdIcon(icon='antd-close'), t__dashboard('拒绝')],
                                id={'type': 'agent-action-decision', 'action_id': action['action_id'], 'decision': 'reject'},
                                n_clicks=0, **{'aria-label': t__dashboard('拒绝')}),
                ], className='agent-action-buttons'),
            ])] if action['status'] == 'pending' else []),
            *([html.P(translate_agent_message(action['result']), role='status')]
              if action['result'] else []),
        ], className='agent-action') for action in actions],
    ], open=any(action['status'] == 'pending' for action in actions))


def render_content(menu_access: MenuAccess, **kwargs):
    if not menu_access.has_access('工作台-页面'):
        return None
    demo_mode = os.environ.get('SMART_AGENT_DEMO_MODE') == '1'
    profile_id = None
    try:
        profile_id = agent_provider_repository.default_profile_id()
        profile_options = agent_provider_repository.profile_options(menu_access)
        status = model_status(profile_id, access=menu_access)
        if profile_id not in {option['value'] for option in profile_options}:
            profile_id = None
    except Exception:
        profile_options = []
        status = {'ready': False, 'model': None,
                  'message': t__dashboard('Agent 配置不可用，请检查系统设置。')}
    status_message = translate_agent_message(status['message'])
    return html.Div([
        html.Button([
            html.Img(src='/assets/imgs/agent-robot.png', className='agent-robot-fallback', alt=''),
            html.Div(id='agent-robot-scene', className='agent-robot-scene', **{'aria-hidden': 'true'}),
            html.Span('Anita', className='agent-launcher-label'),
        ], id='agent-launcher', type='button', title=t__dashboard('打开 Agent 对话'),
            **{'aria-label': t__dashboard('打开 Agent 对话'), 'aria-expanded': 'false',
               'aria-controls': 'agent-panel'}),
        html.Section([
            html.Header([
                html.Div([
                    html.Img(src='/assets/imgs/digital-human-mark.svg', alt='', className='agent-header-portrait'),
                    html.Div([
                        html.H2('Anita', id='agent-panel-title'),
                        html.Span(status['model'] or t__dashboard('模型未配置'), id='agent-model-status',
                                  className='agent-model-ready' if status['ready'] else 'agent-model-offline'),
                    ], className='agent-header-text'),
                ], className='agent-panel-identity'),
                html.Div([
                    html.Button(fac.AntdIcon(icon='antd-export'), id='agent-expand', type='button',
                                title=t__dashboard('放大对话'),
                                **{'aria-label': t__dashboard('放大对话')}),
                    html.Button(fac.AntdIcon(icon='antd-close'), id='agent-close', type='button',
                                title=t__dashboard('收起对话'),
                                **{'aria-label': t__dashboard('收起对话')}),
                ], className='agent-panel-controls'),
            ], className='agent-panel-header'),
            html.Div([
                fac.AntdSelect(id='agent-conversation-select', options=[],
                               placeholder=t__dashboard('选择会话'),
                               style={'flex': 1, 'minWidth': 0},
                               **{'aria-label': t__dashboard('会话')}),
                fac.AntdTooltip(fac.AntdButton(id='agent-new-conversation', type='text',
                    icon=fac.AntdIcon(icon='antd-plus'), title=t__dashboard('新建会话'),
                    **{'aria-label': t__dashboard('新建会话')}),
                    title=t__dashboard('新建会话')),
            ], className='agent-session-toolbar'),
            html.Div([
                html.Div([
                    html.Span(t__dashboard('Agent 服务'), className='agent-service-label'),
                    fac.AntdSelect(id='agent-provider-profile', options=profile_options,
                               value=profile_id if profile_options else None,
                               placeholder=t__dashboard('选择 Agent 服务'),
                               style={'flex': 1, 'minWidth': 0},
                               **{'aria-label': t__dashboard('当前对话使用的 Agent 服务')}),
                    html.Details([
                        html.Summary(fac.AntdIcon(icon='antd-info-circle'),
                                     title=t__dashboard('Agent 服务'),
                                     **{'aria-label': t__dashboard('Agent 服务')}),
                        html.Div(id='agent-conversation-profile-feedback', role='status',
                                 className='agent-conversation-profile-feedback'),
                    ], className='agent-service-details'),
                ], className='agent-service-toolbar'),
                render_page_assistant(),
            ], className='agent-tools-toolbar'),
            html.Div([
                html.Span(status_message),
                *([dcc.Link(t__dashboard('前往配置'), href='/setting_/agent_provider')]
                  if not status['ready'] and menu_access.has_access('Agent配置-页面') else []),
            ], id='agent-setup-hint', className='agent-setup-hint' if not status['ready'] else '',
               role='status', hidden=status['ready']),
            html.Span(t__dashboard('当前会话'), id='agent-conversation-title', hidden=True,
                      **{'data-conversation-id': ''}),
            html.Div([
                html.Div(render_messages([]), id='agent-messages', className='agent-messages',
                         **{'aria-live': 'polite', 'aria-relevant': 'additions'}),
                html.Div(id='agent-stream-output', className='agent-stream-output',
                         role='status', **{'aria-live': 'polite'}),
                html.Div(id='agent-approvals', className='agent-approvals'),
                html.Div(id='agent-approval-status', role='status', className='agent-approval-status',
                         **{'aria-live': 'polite'}),
            ], className='agent-panel-scroll'),
            html.Div([
                html.Div([
                    fac.AntdInput(id='agent-prompt', mode='text-area',
                                  placeholder=t__dashboard('输入消息…'),
                                  maxLength=4000, autoSize={'minRows': 2, 'maxRows': 4},
                                  **{'aria-label': t__dashboard('消息')}),
                    html.Div([
                        fac.AntdCheckbox(id='agent-log-consent', checked=False,
                                          label=(t__dashboard('允许读取脱敏演示日志（仅本地）') if demo_mode
                                              else t__dashboard('允许将脱敏日志发送给模型')),
                                         disabled=not menu_access.has_access('任务日志-页面')),
                        fac.AntdButton(id='agent-send', type='primary',
                                       icon=fac.AntdIcon(icon='antd-arrow-up'), disabled=True,
                                       title=t__dashboard('发送消息'),
                                       **{'aria-label': t__dashboard('发送')}),
                    ], className='agent-composer-actions'),
                ], className='agent-input-surface'),
                html.Div(id='agent-progress', role='status', className='agent-progress',
                         **{'aria-live': 'polite'}),
                html.Div(id='agent-status', role='status', className='agent-status',
                         **{'aria-live': 'polite'}),
            ], id='agent-composer', className='agent-composer'),
        ], id='agent-panel', hidden=True, role='dialog', tabIndex=-1,
            **{'aria-labelledby': 'agent-panel-title'}),
        dcc.Store(id='agent-active-conversation', storage_type='memory'),
        dcc.Store(id='agent-stream-refresh', storage_type='memory'),
        html.Div(id='agent-response-event', hidden=True),
        dcc.Interval(id='agent-init', interval=300, max_intervals=1, disabled=True),
    ], id='agent-widget', className='agent-widget',
       **{'data-agent-i18n': agent_widget_i18n_data()})
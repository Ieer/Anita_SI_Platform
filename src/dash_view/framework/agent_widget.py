from dash import dcc, html
import feffery_antd_components as fac

from common.utilities.util_menu_access import MenuAccess
from common.agent import model_status
import dash_callback.pages.agent_widget_c  # noqa


def render_messages(messages):
    if not messages:
        return html.Div([
            html.Img(src='/assets/imgs/agent-robot.png', alt='', className='agent-empty-portrait'),
            html.H2('有什么想聊的？'),
        ], className='agent-empty')
    tool_titles = {'list_tasks': '查询任务', 'read_latest_log': '读取日志', 'propose_run_task': '提议运行'}
    status_titles = {'ok': '完成', 'unavailable': '不可用', 'not_found': '未找到', 'pending_approval': '待审批'}
    role_titles = {'user': '你', 'assistant': 'Agent', 'error': '请求失败'}
    return [
        html.Article([
            html.Div([
                html.Strong(role_titles.get(message['role'], 'Agent')),
                html.Time(message.get('created_at', '')[11:16],
                          title=message.get('created_at', '')[:19].replace('T', ' ')),
            ], className='agent-message-meta'),
            dcc.Markdown(message['content'], dangerously_allow_html=False, className='agent-message-content agent-markdown')
            if message['role'] == 'assistant' else html.Div(message['content'], className='agent-message-content'),
            *([
                html.Details([
                    html.Summary(f"工具记录 · {len(message['trace'])}"),
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
    statuses = {'pending': '待审批', 'rejected': '已拒绝', 'expired': '已过期', 'blocked': '校验阻断',
                'submitting': '提交中或中断待核对', 'submitted': '调度器已接收', 'unknown': '提交结果未知，待核对'}
    return html.Details([
        html.Summary(f'执行审批 · {len(actions)}'),
        *[html.Article([
            html.Div([html.Strong(action['job_id']), html.Span(statuses.get(action['status'], action['status']))],
                     className='agent-action-heading'),
            html.P(action['summary']),
            html.Dl([
                html.Dt('任务版本'), html.Dd(html.Code(action['task_digest'])),
                html.Dt('运行 ID'), html.Dd(html.Code(action['run_id'])),
                html.Dt('有效期至'), html.Dd(str(action['expires_at'])[:19]),
                *([html.Dt('确认时间'), html.Dd(str(action['decided_at'])[:19])] if action['decided_at'] else []),
            ], className='agent-action-metadata'),
            *([html.Div([
                html.P('将以调度服务账户在本机运行已有脚本，可能写入文件或访问外部系统。',
                       className='agent-action-warning'),
                fac.AntdCheckbox(id={'type': 'agent-action-consent', 'action_id': action['action_id']},
                                 checked=False, label='已核对任务脚本及影响', disabled=not can_approve),
                html.Div([
                    html.Button([fac.AntdIcon(icon='antd-check'), '批准并运行'],
                                id={'type': 'agent-action-decision', 'action_id': action['action_id'], 'decision': 'approve'},
                                n_clicks=0, disabled=not can_approve, className='agent-action-approve',
                                **{'aria-label': '批准并运行'}),
                    html.Button([fac.AntdIcon(icon='antd-close'), '拒绝'],
                                id={'type': 'agent-action-decision', 'action_id': action['action_id'], 'decision': 'reject'},
                                n_clicks=0, **{'aria-label': '拒绝'}),
                ], className='agent-action-buttons'),
            ])] if action['status'] == 'pending' else []),
            *([html.P(action['result'], role='status')] if action['result'] else []),
        ], className='agent-action') for action in actions],
    ], open=any(action['status'] == 'pending' for action in actions))


def render_content(menu_access: MenuAccess, **kwargs):
    if not menu_access.has_access('工作台-页面'):
        return None
    status = model_status()
    return html.Div([
        html.Button([
            html.Img(src='/assets/imgs/agent-robot.png', className='agent-robot-fallback', alt=''),
            html.Div(id='agent-robot-scene', className='agent-robot-scene', **{'aria-hidden': 'true'}),
            html.Span('Agent', className='agent-launcher-label'),
        ], id='agent-launcher', type='button', title='打开 Agent 对话',
            **{'aria-label': '打开 Agent 对话', 'aria-expanded': 'false', 'aria-controls': 'agent-panel'}),
        html.Section([
            html.Header([
                html.Div([
                    html.Img(src='/assets/imgs/agent-robot.png', alt='', className='agent-header-portrait'),
                    html.Div([
                        html.H2('Agent', id='agent-panel-title'),
                        html.Span(status['model'] or '模型未配置', id='agent-model-status',
                                  className='agent-model-ready' if status['ready'] else 'agent-model-offline'),
                    ], className='agent-header-text'),
                ], className='agent-panel-identity'),
                html.Div([
                    html.Button(fac.AntdIcon(icon='antd-export'), id='agent-expand', type='button',
                                title='放大对话', **{'aria-label': '放大对话'}),
                    html.Button(fac.AntdIcon(icon='antd-close'), id='agent-close', type='button',
                                title='收起对话', **{'aria-label': '收起对话'}),
                ], className='agent-panel-controls'),
            ], className='agent-panel-header'),
            html.Div([
                fac.AntdSelect(id='agent-conversation-select', options=[], placeholder='选择会话',
                               style={'flex': 1, 'minWidth': 0}, **{'aria-label': '会话'}),
                fac.AntdTooltip(fac.AntdButton(id='agent-new-conversation', type='text',
                    icon=fac.AntdIcon(icon='antd-plus'), title='新建会话',
                    **{'aria-label': '新建会话'}), title='新建会话'),
            ], className='agent-session-toolbar'),
            html.Span('当前会话', id='agent-conversation-title', hidden=True),
            html.Div([
                html.Div(render_messages([]), id='agent-messages', className='agent-messages',
                         **{'aria-live': 'polite', 'aria-relevant': 'additions'}),
                html.Div(id='agent-approvals', className='agent-approvals'),
                html.Div(id='agent-approval-status', role='status', className='agent-approval-status',
                         **{'aria-live': 'polite'}),
            ], className='agent-panel-scroll'),
            html.Div([
                html.Div([
                    fac.AntdInput(id='agent-prompt', mode='text-area', placeholder='输入消息…',
                                  maxLength=4000, autoSize={'minRows': 2, 'maxRows': 4},
                                  **{'aria-label': '消息'}),
                    html.Div([
                        fac.AntdCheckbox(id='agent-log-consent', checked=False,
                                         label='允许将脱敏日志发送给模型',
                                         disabled=not menu_access.has_access('任务日志-页面')),
                        fac.AntdButton(id='agent-send', type='primary', title='发送消息',
                                       icon=fac.AntdIcon(icon='antd-arrow-up'), disabled=True,
                                       **{'aria-label': '发送'}),
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
        html.Div(id='agent-response-event', hidden=True),
        dcc.Interval(id='agent-init', interval=300, max_intervals=1, disabled=True),
    ], id='agent-widget', className='agent-widget')
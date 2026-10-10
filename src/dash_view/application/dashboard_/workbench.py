from dash import dcc, html
import feffery_antd_components as fac

from common.utilities.util_menu_access import MenuAccess
from common.agent import model_status
from common.agent_actions import can_propose
from i18n import t__dashboard
import dash_callback.application.dashboard_.workbench_c  # noqa

title = 'Agent'
icon = 'antd-appstore'
order = 3
access_metas = ('工作台-页面',)


def available_pages(menu_access):
    from config.access_factory import AccessFactory

    return sorted(
        [view for view in AccessFactory.views
         if view.__name__.removeprefix('dash_view.application.') in menu_access.menu_items
         and view.__name__ != __name__],
        key=lambda view: (view.__name__.split('.')[-2], getattr(view, 'order', 0)),
    )


def render_messages(messages):
    if not messages:
        return html.Div([
            fac.AntdIcon(icon='antd-comment', className='agent-empty-icon'),
            html.H2(t__dashboard('新会话')),
        ], className='agent-empty')
    tool_titles = {
        'list_tasks': t__dashboard('查询任务'),
        'read_latest_log': t__dashboard('读取日志'),
        'propose_run_task': t__dashboard('提议运行'),
    }
    status_titles = {
        'ok': t__dashboard('完成'),
        'unavailable': t__dashboard('不可用'),
        'not_found': t__dashboard('未找到'),
        'pending_approval': t__dashboard('待审批'),
    }
    role_titles = {'user': t__dashboard('你'), 'assistant': 'Agent', 'error': t__dashboard('请求失败')}
    return [
        html.Article([
            html.Div([html.Strong(role_titles.get(message['role'], 'Agent')),
                      html.Time(message.get('created_at', '')[:19].replace('T', ' '))], className='agent-message-meta'),
            html.Div(message['content'], className='agent-message-content'),
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
    statuses = {
        'pending': t__dashboard('待审批'),
        'rejected': t__dashboard('已拒绝'),
        'expired': t__dashboard('已过期'),
        'blocked': t__dashboard('校验阻断'),
        'submitting': t__dashboard('提交中或中断待核对'),
        'submitted': t__dashboard('调度器已接收'),
        'unknown': t__dashboard('提交结果未知，待核对'),
    }
    return html.Details([
        html.Summary(f"{t__dashboard('执行审批')} · {len(actions)}"),
        *[html.Article([
            html.Div([html.Strong(action['job_id']), html.Span(statuses.get(action['status'], action['status']))],
                     className='agent-action-heading'),
            html.P(action['summary']),
            html.Dl([
                html.Dt(t__dashboard('任务版本')), html.Dd(html.Code(action['task_digest'])),
                html.Dt('Run ID'), html.Dd(html.Code(action['run_id'])),
                html.Dt(t__dashboard('有效期至')), html.Dd(str(action['expires_at'])[:19]),
                *([html.Dt(t__dashboard('确认时间')), html.Dd(str(action['decided_at'])[:19])] if action['decided_at'] else []),
            ], className='agent-action-metadata'),
            *([html.Div([
                html.P(t__dashboard('将以调度服务账户在本机运行已有脚本，可能写入文件或访问外部系统。'),
                       className='agent-action-warning'),
                fac.AntdCheckbox(id={'type': 'agent-action-consent', 'action_id': action['action_id']},
                                 checked=False, label=t__dashboard('已核对任务脚本及影响'), disabled=not can_approve),
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
            *([html.P(action['result'], role='status')] if action['result'] else []),
        ], className='agent-action') for action in actions],
    ], open=any(action['status'] == 'pending' for action in actions))


def render_content(menu_access: MenuAccess, **kwargs):
    if not menu_access.has_access('工作台-页面'):
        return None
    status = model_status()
    return html.Div([
        html.Button([
            html.Div(id='agent-robot-scene', className='agent-robot-scene', **{'aria-hidden': 'true'}),
            html.Span('Agent', className='agent-launcher-label'),
        ], id='agent-launcher', type='button', title=t__dashboard('打开 Agent 对话'),
            **{'aria-label': t__dashboard('打开 Agent 对话'), 'aria-expanded': 'false', 'aria-controls': 'agent-panel'}),
        html.Section([
        html.Header([
            html.H2('Agent', id='agent-panel-title'),
            html.Div([
                html.Button(fac.AntdIcon(icon='antd-fullscreen'), id='agent-expand', type='button',
                            title=t__dashboard('放大对话'), **{'aria-label': t__dashboard('放大对话')}),
                html.Button(fac.AntdIcon(icon='antd-close'), id='agent-close', type='button',
                            title=t__dashboard('收起对话'), **{'aria-label': t__dashboard('收起对话')}),
            ], className='agent-panel-controls'),
        ], className='agent-panel-header'),
        html.Div([
                fac.AntdSelect(id='agent-conversation-select', options=[], placeholder=t__dashboard('选择会话'),
                               style={'flex': 1, 'minWidth': 0}, **{'aria-label': t__dashboard('会话')}),
                fac.AntdTooltip(fac.AntdButton(id='agent-new-conversation', type='text',
                    icon=fac.AntdIcon(icon='antd-plus'), title=t__dashboard('新建会话'),
                    **{'aria-label': t__dashboard('新建会话')}), title=t__dashboard('新建会话')),
        ], className='agent-session-toolbar'),
                html.Div([
                    html.Span(t__dashboard('当前会话'), id='agent-conversation-title'),
                    html.Span(status['model'] or t__dashboard('模型未配置'), id='agent-model-status',
                              className='agent-model-ready' if status['ready'] else 'agent-model-offline'),
                ], className='agent-conversation-heading'),
            html.Div([
                html.Div(render_messages([]), id='agent-messages', className='agent-messages',
                         **{'aria-live': 'polite', 'aria-relevant': 'additions'}),
                html.Div(id='agent-approvals', className='agent-approvals'),
                html.Div(id='agent-approval-status', role='status', className='agent-approval-status',
                     **{'aria-live': 'polite'}),
            ], className='agent-panel-scroll'),
                html.Div([
                    fac.AntdInput(id='agent-prompt', mode='text-area', placeholder=t__dashboard('输入消息…'),
                                   maxLength=4000, autoSize={'minRows': 2, 'maxRows': 4},
                                   **{'aria-label': t__dashboard('消息')}),
                    html.Div([
                        fac.AntdCheckbox(id='agent-log-consent', checked=False,
                                         label=t__dashboard('允许将脱敏日志发送给模型'),
                                         disabled=not menu_access.has_access('任务日志-页面')),
                        fac.AntdButton(t__dashboard('发送'), id='agent-send', type='primary',
                                       icon=fac.AntdIcon(icon='antd-arrow-up'), disabled=not status['ready']),
                    ], className='agent-composer-actions'),
                    html.Div(id='agent-progress', role='status', className='agent-progress',
                             **{'aria-live': 'polite'}),
                    html.Div(id='agent-status', role='status', className='agent-status',
                             **{'aria-live': 'polite'}),
                ], id='agent-composer', className='agent-composer'),
        ], id='agent-panel', hidden=True, role='dialog', tabIndex=-1,
            **{'aria-labelledby': 'agent-panel-title'}),
        dcc.Store(id='agent-active-conversation', storage_type='memory'),
        dcc.Interval(id='agent-init', interval=300, max_intervals=1, disabled=True),
    ], id='agent-widget', className='agent-widget')
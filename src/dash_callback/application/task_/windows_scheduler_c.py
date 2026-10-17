from uuid import uuid4
import getpass
import socket

import dash
from dash import Input, Output, State, dcc, html
from datetime import datetime
import feffery_antd_components as fac
from common import task_center
from common.utilities.util_menu_access import get_menu_access
from server import app
from .i18n import task_label as t_task, task_message, task_operation_label, task_timestamp


class _PageAccess:
    def __init__(self, access):
        self.user_name = access.user_name
        self._access = access

    def has_access(self, permission):
        if permission == task_center.PAGE_PERMISSION:
            return self._access.has_access('Windows任務管理-页面') or self._access.has_access('任务管理-页面')
        return self._access.has_access(permission)


def _access():
    return _PageAccess(get_menu_access())


def load_windows(access, keyword='', status='', page=1, page_size=20):
    result = task_center.list_tasks(access, engine='windows', keyword=keyword or '',
                                    status=status or '', page=page, page_size=page_size)
    account = f'{socket.gethostname()}\\{getpass.getuser()}'
    rows = []
    for row in result.get('rows', []):
        raw_status = row.get('last_status') or ''
        rows.append({
            'key': row.get('task_id') or row['job_id'], 'task_id': row.get('task_id'),
            'job_id': row['job_id'], 'name': row['name'], 'group_name': row.get('group_name') or t_task('未认领'),
            'next_run': task_timestamp(row.get('next_run')),
            'state': f"{t_task('运行中') if row.get('native_status') == 'Running' else t_task('就绪') if row.get('enabled') else t_task('已禁用')} · LastTaskResult {raw_status or '—'}",
            'policy': row.get('multiple_instances_policy') or t_task('不可用'),
            'account': account, 'claimed': row.get('claimed'), 'enabled': row.get('enabled'),
            'actions': _actions(row, task_center.task_capabilities(access, row['task_id']) if row.get('task_id') else set()),
        })
    return rows, result.get('total', 0), result


def _actions(row, capabilities=None):
    if not row.get('task_id'):
        return [{'content': t_task('先认领'), 'type': 'default', 'disabled': True, 'custom': 'unclaimed'}]
    operation = 'pause' if row.get('enabled') else 'resume'
    capabilities = capabilities if capabilities is not None else {'run', operation, 'delete'}
    return [{'content': label, 'type': 'link', 'custom': f'{action}:{row["task_id"]}', **extra}
            for label, action, extra in [
                (t_task('启动'), 'run', {}), (t_task('暂停') if operation == 'pause' else t_task('启用'), operation, {}),
                (t_task('删除'), 'delete', {'danger': True}), (t_task('核实定义'), 'resolve_definition', {})]
            if action in capabilities or (action == 'resolve_definition' and 'resolve' in capabilities)]


@app.callback(
    Output('tc-windows-table', 'data'), Output('tc-windows-pagination', 'total'),
    Output('tc-windows-summary', 'children'), Output('tc-windows-service-notice', 'children'),
    Input('tc-windows-init', 'n_intervals'), Input('tc-windows-refresh', 'nClicks'),
    Input('tc-windows-search', 'value'), Input('tc-windows-status', 'value'),
    Input('tc-windows-pagination', 'current'), Input('tc-windows-pagination', 'pageSize'),
)
def refresh_windows(_init, _refresh, keyword, status, page, page_size):
    try:
        rows, total, result = load_windows(_access(), keyword, status, page or 1, page_size or 20)
        summary = html.Div([
            html.Div([html.Span(str(total), className='tc-summary-value'),
                      html.Span(t_task('原生任务快照'), className='tc-summary-label')], className='tc-summary-item'),
            html.Div([html.Span(str(sum(row['enabled'] for row in rows)), className='tc-summary-value'),
                      html.Span(t_task('就绪（本页）'), className='tc-summary-label')], className='tc-summary-item'),
        ], className='tc-summary-strip')
        notice = None if result.get('online') else fac.AntdAlert(
            message=f"{t_task('Windows 任务服务不可用：')}{result.get('error') or t_task('请稍后刷新')}",
            type='warning', showIcon=True)
        return rows, total, summary, notice
    except Exception as exc:
        return [], 0, None, fac.AntdAlert(message=task_message(str(exc)), type='error', showIcon=True)


@app.callback(
    Output('tc-windows-confirm', 'visible'), Output('tc-windows-confirm', 'children'),
    Output('tc-windows-action', 'data'), Input('tc-windows-table', 'nClicksButton'),
    State('tc-windows-table', 'clickedCustom'), State('tc-windows-table', 'recentlyButtonClickedRow'),
    prevent_initial_call=True,
)
def prepare_windows_action(_click, custom, row):
    if not custom or ':' not in custom:
        return dash.no_update, dash.no_update, dash.no_update
    operation, task_id = custom.split(':', 1)
    action = {'operation': operation, 'task_id': task_id, 'request_key': uuid4().hex}
    return True, html.Div([html.P(f"{t_task('任务')}：{(row or {}).get('name', task_id)}"),
                           html.P(f"{t_task('操作：')}{task_operation_label(operation)}"),
                           html.P(t_task('平台请求只代表服务已受理，不代表原生任务已完成。')),
                           fac.AntdInput(id='tc-windows-resolve-reason', value='', mode='text-area', placeholder=t_task('核实依据'),
                               style={} if operation == 'resolve_definition' else {'display': 'none'})]), action


@app.callback(
    Output('tc-windows-confirm', 'visible', allow_duplicate=True),
    Output('tc-windows-notice', 'children', allow_duplicate=True),
    Output('tc-windows-refresh', 'nClicks', allow_duplicate=True),
    Input('tc-windows-confirm', 'okCounts'), State('tc-windows-action', 'data'),
    State('tc-windows-resolve-reason', 'value'),
    running=[(Output('tc-windows-confirm', 'confirmLoading'), True, False)], prevent_initial_call=True,
)
def execute_windows_action(_ok, action, reason=None):
    if not action:
        return dash.no_update, dash.no_update, dash.no_update
    try:
        if action['operation'] == 'run':
            result = task_center.request_run(_access(), action['task_id'], action['request_key'])
        elif action['operation'] == 'resolve_definition':
            result = task_center.resolve_task_definition(_access(), action['task_id'], reason)
        else:
            result = task_center.operate_task(_access(), action['task_id'], action['operation'],
                                              request_key=action['request_key'])
        return False, fac.AntdAlert(message=task_message(result.get('message') or '操作已受理'),
                        type='warning' if result.get('status') == 'unknown' else 'success', showIcon=True), int(datetime.now().timestamp() * 1000)
    except Exception as exc:
        return False, fac.AntdAlert(message=task_message(str(exc)), type='error', showIcon=True), dash.no_update


@app.callback(
    Output('tc-windows-claim', 'visible'), Output('tc-windows-claim', 'children'),
    Input('tc-windows-claim-open', 'nClicks'), prevent_initial_call=True,
)
def open_windows_claim(_click):
    access = _access()
    rows, _, _ = load_windows(access, page=1, page_size=100)
    teams = task_center.list_teams(access)
    return True, html.Div([
        fac.AntdSelect(id='tc-windows-claim-job', options=[
            {'label': row['name'], 'value': row['job_id']} for row in rows if not row['claimed']],
            placeholder=t_task('选择原生任务'), style={'width': '100%'}),
        fac.AntdSelect(id='tc-windows-claim-team', options=teams, placeholder=t_task('归属团队'),
                       style={'width': '100%'}),
    ], className='tc-form-grid')


@app.callback(
    Output('tc-windows-claim', 'visible', allow_duplicate=True),
    Output('tc-windows-notice', 'children', allow_duplicate=True),
    Output('tc-windows-refresh', 'nClicks', allow_duplicate=True),
    Input('tc-windows-claim', 'okCounts'), State('tc-windows-claim-job', 'value'),
    State('tc-windows-claim-team', 'value'), prevent_initial_call=True,
)
def claim_windows(_ok, job_id, group_name):
    try:
        task_center.claim_task(_access(), job_id, group_name, engine='windows')
        return False, fac.AntdAlert(message=t_task('Windows 任务已认领。'), type='success', showIcon=True), int(datetime.now().timestamp() * 1000)
    except Exception as exc:
        return True, fac.AntdAlert(message=task_message(str(exc)), type='error', showIcon=True), dash.no_update


@app.callback(Output('tc-windows-download', 'data'), Input('tc-windows-export', 'nClicks'),
              State('tc-windows-search', 'value'), State('tc-windows-status', 'value'), prevent_initial_call=True)
def export_windows(_click, keyword, status):
    return dcc.send_string(task_center.export_windows(_access(), keyword or '', status or ''), 'windows-tasks.csv')
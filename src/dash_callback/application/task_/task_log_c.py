import dash
from dash import Input, Output, State, dcc, html
import feffery_antd_components as fac
from common import task_center
from common.utilities.util_menu_access import get_menu_access
from server import app


class _PageAccess:
    def __init__(self, access):
        self.user_name = access.user_name
        self._access = access

    def has_access(self, permission):
        if permission == task_center.PAGE_PERMISSION:
            return self._access.has_access('任务日志-页面') or self._access.has_access('任务管理-页面')
        return self._access.has_access(permission)


def _access():
    return _PageAccess(get_menu_access())


def load_runs(access, keyword='', status='', page=1, page_size=20, task_id=None, run_id=None):
    result = task_center.list_runs(access, task_id=task_id, run_id=run_id,
                                   keyword=keyword or '', status=status or '',
                                   page=page, page_size=page_size)
    values = result['rows']
    rows = [{
        'key': run['run_id'], 'run_id': run['run_id'], 'task_id': run['task_id'],
        'name': run['name'], 'owner': f"{run.get('group_name') or '—'} · {run.get('actor') or '—'}",
        'source': task_center.SOURCE_LABELS.get(run.get('source'), run.get('source') or '—'),
        'started_at': task_center.display_timestamp(run.get('started_at') or run.get('requested_at')),
        'duration_label': f"{run['duration']}s" if run.get('duration') is not None else '—',
        'state': run.get('status') or '未知',
        'state_label': task_center.RUN_STATUS_LABELS.get(run.get('status'), run.get('status')),
        'detail': [{'content': '查看', 'type': 'link', 'custom': f"detail:{run['run_id']}"}],
    } for run in values]
    return rows, result.get('total', 0)


@app.callback(
    Output('tc-log-table', 'data'), Output('tc-log-pagination', 'total'),
    Output('tc-log-notice', 'children'),
    Input('tc-log-init', 'n_intervals'), Input('tc-log-query-button', 'nClicks'),
    Input('tc-log-search', 'value'), Input('tc-log-status', 'value'),
    Input('tc-log-pagination', 'current'), Input('tc-log-pagination', 'pageSize'),
    State('tc-log-query', 'data'),
)
def refresh_runs(_init, _refresh, keyword, status, page, page_size, query):
    try:
        try:
            if dash.ctx.triggered_id in {'tc-log-search', 'tc-log-status'}:
                page = 1
                dash.set_props('tc-log-pagination', {'current': 1})
        except dash.exceptions.MissingCallbackContextException:
            pass
        query = query or {}
        rows, total = load_runs(_access(), keyword, status, page or 1, page_size or 20,
                                query.get('task_id'), query.get('run_id'))
        return rows, total, None
    except Exception as exc:
        return [], 0, fac.AntdAlert(message=str(exc), type='error', showIcon=True)


def _detail(row, run_id, text='', active=False, can_resolve=False):
    resolve = []
    if row.get('state') == 'unknown' and can_resolve:
        resolve = [
            fac.AntdAlert(message='结果未知。外部核实前不要重跑；提交核实将解除任务护栏。',
                          type='warning', showIcon=True),
            fac.AntdSelect(id='tc-log-resolve-outcome', value='ended', options=[
                {'label': '确认运行已结束', 'value': 'ended'},
                {'label': '确认未执行', 'value': 'not_executed'}], style={'width': '100%'}),
            fac.AntdInput(id='tc-log-resolve-reason', mode='text-area', placeholder='核实依据（必填）',
                          autoSize={'minRows': 2, 'maxRows': 5}),
            fac.AntdButton('提交核实', id='tc-log-resolve', type='primary', danger=True),
        ]
    return html.Div([
        html.Div([html.Span(run_id, className='tc-run-id'), html.Span(row.get('state_label', row.get('state', '')))], className='tc-log-tools'),
        html.Div([html.Span(f"任务：{row.get('name', '—')}"),
                  html.Span(f"来源：{row.get('source', '—')}"),
                  html.Span(f"发起人：{row.get('owner', '—')}")], className='tc-log-tools'),
        html.Div([
            html.Button('复制 Run ID', title='复制运行编号', **{'data-tc-copy': run_id}),
            fac.AntdButton('打开任务', id='tc-log-open-task', icon=fac.AntdIcon(icon='antd-link')),
            fac.AntdSwitch(id='tc-log-auto-scroll', checked=True, checkedChildren='自动滚动',
                           unCheckedChildren='暂停滚动'),
            fac.AntdSwitch(id='tc-log-wrap', checked=True, checkedChildren='自动换行',
                           unCheckedChildren='不换行'),
            fac.AntdInput(id='tc-log-text-search', placeholder='在已读取日志中查找', allowClear=True,
                          prefix=fac.AntdIcon(icon='antd-search'), style={'width': 'min(260px, 100%)'}),
        ], className='tc-log-tools'),
        html.Div(resolve, className='tc-log-tools'),
        html.Pre(text or '等待日志片段…', id='tc-log-output', className='tc-log-output'),
        html.Div(id='tc-log-detail-notice'),
        html.Div(id='tc-log-active', **{'data-active': str(bool(active)).lower()}, style={'display': 'none'}),
    ], **{'data-task-id': row.get('task_id') or '', 'data-run-id': run_id})


@app.callback(
    Output('tc-log-detail', 'visible'), Output('tc-log-detail', 'children'),
    Output('tc-log-selected-run', 'data'), Output('tc-log-cursor', 'data'),
    Output('tc-log-buffer', 'data'), Output('tc-log-refresh', 'disabled'),
    Output('tc-log-export', 'disabled'),
    Input('tc-log-table', 'nClicksButton'), State('tc-log-table', 'clickedCustom'),
    State('tc-log-table', 'recentlyButtonClickedRow'), prevent_initial_call=True,
)
def open_run_detail(_click, custom, row):
    if not custom or not custom.startswith('detail:'):
        return (dash.no_update,) * 7
    row = row or {}
    run_id = custom.split(':', 1)[1]
    active = row.get('state') in {'requested', 'submitted', 'running'}
    selected = {'run_id': run_id, 'task_id': row.get('task_id')}
    result = task_center.read_log(_access(), run_id, 0, limit=100)
    text = ''.join(result['chunks'])[-200_000:]
    capabilities = task_center.run_capabilities(_access(), run_id)
    return (True, _detail(row, run_id, text, not result['complete'], 'resolve' in capabilities), selected, result['cursor'], text,
            result['complete'], 'export' not in capabilities)


@app.callback(
    Output('tc-log-buffer', 'data', allow_duplicate=True),
    Output('tc-log-cursor', 'data', allow_duplicate=True),
    Output('tc-log-refresh', 'disabled', allow_duplicate=True),
    Input('tc-log-refresh', 'n_intervals'), State('tc-log-selected-run', 'data'),
    State('tc-log-detail', 'visible'),
    State('tc-log-cursor', 'data'), State('tc-log-buffer', 'data'), prevent_initial_call=True,
)
def poll_log(_tick, selected, visible, cursor, buffer):
    if not selected or not visible:
        return dash.no_update, dash.no_update, True
    try:
        result = task_center.read_log(_access(), selected['run_id'], cursor or 0, limit=100)
        combined = ((buffer or '') + ''.join(result.get('chunks') or ''))[-200_000:]
        complete = bool(result.get('complete'))
        if complete:
            dash.set_props('tc-log-active', {'data-active': 'false'})
        return combined, result.get('cursor', cursor or 0), complete
    except Exception as exc:
        dash.set_props('tc-log-detail-notice', {'children': fac.AntdAlert(message=str(exc), type='error')})
        return dash.no_update, cursor, True


@app.callback(
    Output('tc-log-output', 'children'), Output('tc-log-output', 'style'),
    Input('tc-log-buffer', 'data'), Input('tc-log-text-search', 'value'),
    Input('tc-log-wrap', 'checked'), State('tc-log-auto-scroll', 'checked'), prevent_initial_call=True,
)
def render_log(buffer, search, wrap, auto_scroll):
    text = buffer or ''
    if search:
        text = '\n'.join(line for line in text.splitlines() if search.casefold() in line.casefold())
    return text or '等待日志片段…', {
        'whiteSpace': 'pre-wrap' if wrap else 'pre',
        'overflowWrap': 'anywhere' if wrap else 'normal',
    }


app.clientside_callback(
    """(text, auto) => {
        if (auto) {
            const node = document.getElementById('tc-log-output');
            if (node) node.scrollTop = node.scrollHeight;
        }
        return window.dash_clientside.no_update;
    }""",
    Output('tc-log-output', 'title'), Input('tc-log-output', 'children'),
    State('tc-log-auto-scroll', 'checked'), prevent_initial_call=True,
)


@app.callback(
    Output('tc-log-download', 'data'), Input('tc-log-export', 'nClicks'),
    State('tc-log-selected-run', 'data'), prevent_initial_call=True,
)
def export_selected_log(_click, selected):
    if not selected:
        return dash.no_update
    text = task_center.export_log(_access(), selected['run_id'])
    return dcc.send_string(text, f"run-{selected['run_id']}.log")


@app.callback(
    Output('tc-log-detail-notice', 'children'), Input('tc-log-resolve', 'nClicks'),
    State('tc-log-selected-run', 'data'), State('tc-log-resolve-outcome', 'value'),
    State('tc-log-resolve-reason', 'value'), prevent_initial_call=True,
)
def resolve_unknown(_click, selected, outcome, reason):
    try:
        result = task_center.resolve_run(_access(), selected['run_id'], outcome, reason or '')
        dash.set_props('tc-log-refresh', {'disabled': True})
        dash.set_props('tc-log-active', {'data-active': 'false'})
        return fac.AntdAlert(message=result.get('message', '运行已核实'), type='success', showIcon=True)
    except Exception as exc:
        return fac.AntdAlert(message=str(exc), type='error', showIcon=True)


@app.callback(
    Output('main-dcc-url', 'pathname', allow_duplicate=True),
    Output('main-dcc-url', 'search', allow_duplicate=True),
    Input('tc-log-open-task', 'nClicks'), State('tc-log-selected-run', 'data'),
    prevent_initial_call=True,
)
def open_task(_click, selected):
    if not selected or not selected.get('task_id'):
        return dash.no_update, dash.no_update
    return '/task_/task_mgmt', f"?task_id={selected['task_id']}"

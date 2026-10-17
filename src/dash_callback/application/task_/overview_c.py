from datetime import datetime, timedelta
from html import escape
import threading
import time
from zoneinfo import ZoneInfo

import dash
from dash import Input, Output, State, html
import feffery_antd_components as fac
from server import app
from common.utilities.util_menu_access import get_menu_access
from common import task_center
from common.utilities.util_theme import restyle_figure
from common.utilities import util_apscheduler
from common.utilities.util_logger import Log
from common.utilities.util_startup import (
    STARTUP_MODES,
    StartupConfigError,
    load_startup_config,
    save_scheduler_mode,
)
from i18n import t__task
from .i18n import task_label as t_task, task_message, task_status_label, task_timestamp


_STATUS_CACHE_LOCK = threading.Lock()
_STATUS_CACHE = {'expires_at': 0.0, 'value': None}
_logger = Log.get_logger(__name__)


class _OverviewAccess:
    def __init__(self, access):
        self.user_name = access.user_name
        self._access = access

    def has_access(self, permission):
        if permission == task_center.PAGE_PERMISSION:
            return self._access.has_access('rpa_scheduler') or self._access.has_access('任务管理-页面')
        return self._access.has_access(permission)


def _stamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(ZoneInfo('Asia/Shanghai'))
    except (TypeError, ValueError):
        return None


def _timeline(tasks, schedule_points, runs, mode, start, end, days):
    rows = tasks if mode == 'schedule' else runs
    row_ids = [row['task_id'] if mode == 'schedule' else row['run_id'] for row in rows]
    labels = []
    for index, row in enumerate(rows):
        name = row['name']
        name = name if len(name) <= 12 else name[:11] + '...'
        suffix = ''
        if mode == 'schedule':
            suffix = ' · ' + t_task('未知') if row.get('guard_state') == 'unknown' else ' · ' + t_task('已暂停') if not row.get('enabled') else ''
        labels.append(escape(f'{index + 1:02d} {name}{suffix}'))
    figure = {'data': [], 'layout': {
        'height': max(320, 136 + len(rows) * 34),
        'margin': {'l': 132, 'r': 24, 't': 42, 'b': 76},
        'paper_bgcolor': '#ffffff', 'plot_bgcolor': '#ffffff',
        'font': {'color': '#34484b', 'size': 13},
        'xaxis': {'type': 'date', 'range': [start.isoformat(), end.isoformat()],
                  'gridcolor': '#e7eeee', 'tickangle': 0,
                  'dtick': (6 if days == 1 else 24 if days == 7 else 120) * 3600000,
                  'tick0': start.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
                  'tickformat': '%m-%d<br>%H:%M', 'automargin': True,
                  'title': {'text': f'{start:%m-%d} ~ {end:%m-%d} · Asia/Shanghai', 'standoff': 16}},
        'yaxis': {'type': 'category', 'categoryorder': 'array', 'categoryarray': row_ids,
                  'tickmode': 'array', 'tickvals': row_ids, 'ticktext': labels,
                  'autorange': 'reversed', 'automargin': True, 'showgrid': False},
        'showlegend': False, 'hovermode': 'closest', 'dragmode': 'pan',
        'uirevision': f'{mode}:{start.isoformat()}:{end.isoformat()}',
        'bargap': 0.55,
    }}
    if mode == 'schedule':
        bucket_seconds = (3 if days == 1 else 24 if days == 7 else 120) * 3600
        buckets = {}
        task_by_id = {task['task_id']: task for task in tasks}
        for point in schedule_points:
            bucket = int((_stamp(point['at']) - start).total_seconds() // bucket_seconds)
            buckets.setdefault((point['task_id'], bucket), []).append(point)
        for unknown in (False, True):
            groups = [(task_id, bucket, points) for (task_id, bucket), points in buckets.items()
                      if (task_by_id[task_id].get('guard_state') == 'unknown') == unknown]
            if not groups:
                continue
            figure['data'].append({
                'type': 'scatter', 'mode': 'markers+text',
                'name': t_task('结果未知的计划') if unknown else t_task('计划预览'),
                        'x': [points[len(points) // 2]['at'] for _, _, points in groups],
                'y': [task_id for task_id, _, _ in groups],
                'marker': {'color': '#a6610b' if unknown else '#087f78',
                           'size': [24 if len(points) > 1 else 12 for _, _, points in groups],
                           'symbol': 'circle', 'line': {'color': '#ffffff', 'width': 1}},
                'text': [str(len(points)) if len(points) > 1 else '' for _, _, points in groups],
                'textfont': {'color': '#ffffff', 'size': 11}, 'textposition': 'middle center',
                'customdata': [task_id for task_id, _, _ in groups],
                'hovertext': [
                    f"{escape(task_by_id[task_id]['name'])}<br>{t_task('团队：')}{escape(task_by_id[task_id].get('group_name') or t_task('未认领'))}"
                    f"<br>{t_task('预览触发：{count} 次（每任务最多50次）').format(count=len(points))}"
                    f"<br>{t_task('首个：')}{task_timestamp(points[0]['at'])}"
                    f"<br>{t_task('末个：')}{task_timestamp(points[-1]['at'])}"
                    + ('<br>' + t_task('结果未知，待核实') if unknown else '')
                    for task_id, _, points in groups],
                'hovertemplate': '%{hovertext}<extra></extra>',
            })
    elif mode == 'actual' and runs:
        colors = {'succeeded': '#087f78', 'failed': '#bd4141', 'timeout': '#bd4141',
                  'unknown': '#a6610b', 'running': '#286bb0', 'submitted': '#286bb0',
                  'requested': '#286bb0'}
        hover = {}
        actual = []
        for run in runs:
            finished = run['_finish']
            recorded = run.get('_start_recorded', True)
            duration = None
            if recorded and finished and finished >= run['_start']:
                actual.append(run)
                duration = (finished - run['_start']).total_seconds()
            duration_text = f"{duration:,.1f} {t_task('秒')}" if duration is not None else t_task('未记录完整运行区间')
            hover[run['run_id']] = (
                f"{escape(run['name'])}<br>{t_task('团队：')}{escape(run.get('group_name') or t_task('未认领'))}"
                f"<br>{t_task('状态：')}{escape(task_status_label(run.get('status')) or '')}"
                f"<br>{t_task('开始' if recorded else '申请')}：{task_timestamp(run['_start'].isoformat())}"
                f"<br>{t_task('结束：')}{task_timestamp(finished.isoformat()) if finished else t_task('未记录')}"
                f"<br>{t_task('时长：')}{duration_text}<br>{t_task('Run ID：')}{escape(run['run_id'])}"
            )
        if actual:
            figure['data'].append({
                'type': 'bar', 'orientation': 'h', 'name': t_task('真实运行区间'),
                'x': [(run['_finish'] - run['_start']).total_seconds() * 1000 for run in actual],
                'base': [run['_start'].isoformat() for run in actual],
                'y': [run['run_id'] for run in actual],
                'marker': {'color': [colors.get(run['status'], '#78898c') for run in actual]},
                'customdata': [run['run_id'] for run in actual],
                'hovertext': [hover[run['run_id']] for run in actual],
                'hovertemplate': '%{hovertext}<extra></extra>',
            })
        figure['data'].append({
            'type': 'scatter', 'mode': 'markers', 'name': t_task('运行时间标记'),
            'x': [run['_start'].isoformat() for run in runs],
            'y': [run['run_id'] for run in runs],
            'marker': {'color': [colors.get(run['status'], '#78898c') for run in runs], 'size': 14,
                       'symbol': ['circle' if run in actual else 'circle-open' for run in runs],
                       'line': {'width': 2}},
            'customdata': [run['run_id'] for run in runs],
            'hovertext': [hover[run['run_id']] for run in runs],
            'hovertemplate': '%{hovertext}<extra></extra>',
        })
    if not figure['data']:
        label = (t_task('任务已暂停，暂无计划触发') if tasks and not any(task.get('enabled') for task in tasks)
                 else t_task('此时间范围无计划触发')) if mode == 'schedule' else t_task('此时间范围无运行记录')
        figure['layout'].update(height=220, margin={'l': 20, 'r': 20, 't': 20, 'b': 20})
        figure['layout']['xaxis']['visible'] = False
        figure['layout']['yaxis']['visible'] = False
        figure['layout']['annotations'] = [{'text': label,
                                             'xref': 'paper', 'yref': 'paper', 'x': 0.5, 'y': 0.5,
                                             'showarrow': False, 'font': {'size': 18}}]
    return figure


def build_overview(access, mode='schedule', window='1d', page=1, page_size=20):
    task_result = task_center.list_tasks(access, engine='platform', page=page, page_size=page_size)
    tasks = task_result['rows']
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    days = {'1d': 1, '7d': 7, '30d': 30}.get(window, 1)
    until = now + timedelta(days=days)
    run_result = task_center.list_runs(access, page=page, page_size=page_size,
                                      since=now - timedelta(days=days))
    runs = run_result['rows']
    schedule_points = []
    for task in tasks:
        if task.get('enabled') and task.get('trigger') in {'cron', 'interval', 'date'}:
            for point in task_center.preview_schedule(
                    task['trigger'], task.get('plan') or {},
                    (task.get('plan') or {}).get('timezone', 'Asia/Shanghai'), limit=50, until=until):
                parsed = _stamp(point)
                if parsed and now <= parsed <= until:
                    schedule_points.append({'task': task['name'], 'at': parsed.isoformat(), 'task_id': task['task_id']})

    selected_runs = []
    for run in runs:
        recorded_start = _stamp(run.get('started_at'))
        started = recorded_start or _stamp(run.get('requested_at'))
        finished = _stamp(run.get('finished_at'))
        if not started or started < now - timedelta(days=days):
            continue
        selected_runs.append({**run, '_start': started, '_finish': finished,
                      '_start_recorded': recorded_start is not None})
    selected_runs.sort(key=lambda item: item['_start'], reverse=True)

    table_rows = []
    if mode == 'list':
        table_rows = [{
            'key': row['task_id'], 'name': row['name'], 'group_name': row.get('group_name') or t_task('未认领'),
            'state': t_task('结果未知') if row.get('guard_state') == 'unknown' else t_task('运行中') if row.get('active_run_id') else (t_task('启用') if row.get('enabled') else t_task('已暂停')),
            'next_run': task_timestamp(row.get('next_run')), 'task_id': row['task_id'],
        } for row in tasks]
    else:
        table_rows = [{
            'key': row['run_id'], 'name': row['name'], 'group_name': row.get('group_name') or '—',
            'state': task_status_label(row.get('status')),
            'next_run': task_timestamp(row.get('started_at') or row.get('requested_at')),
            'task_id': row['task_id'], 'run_id': row['run_id'],
        } for row in selected_runs]

    timeline = _timeline(tasks, schedule_points, selected_runs, mode,
                         now if mode == 'schedule' else now - timedelta(days=days),
                         until if mode == 'schedule' else now, days)

    summary = html.Div([
        html.Div([html.Span(str(task_result.get('total', 0)), className='tc-summary-value'),
                  html.Span(t_task('平台任务'), className='tc-summary-label')], className='tc-summary-item'),
        html.Div([html.Span(str(len(tasks)), className='tc-summary-value'),
                  html.Span(t_task('当前页任务'), className='tc-summary-label')], className='tc-summary-item'),
        html.Div([html.Span(str(sum(bool(row.get('active_run_id')) for row in tasks)), className='tc-summary-value'),
                  html.Span(t_task('在途（本页）'), className='tc-summary-label')], className='tc-summary-item'),
        html.Div([html.Span(str(sum(row.get('guard_state') == 'unknown' for row in tasks)), className='tc-summary-value'),
              html.Span(t_task('未知（本页）'), className='tc-summary-label')], className='tc-summary-item is-warning'),
    ], className='tc-summary-strip')
    columns = [
        {'title': t_task('任务 / 运行'), 'dataIndex': 'name', 'width': 240},
        {'title': t_task('团队'), 'dataIndex': 'group_name', 'width': 150},
        {'title': t_task('时间'), 'dataIndex': 'next_run', 'width': 220},
        {'title': t_task('状态'), 'dataIndex': 'state', 'width': 130},
    ]
    table_total = task_result.get('total', 0) if mode in {'list', 'schedule'} else run_result.get('total', 0)
    return summary, timeline, columns, table_rows, table_total, task_result


@app.callback(Output('tc-overview-window', 'options'),
              Output('tc-overview-legend', 'children'),
              Output('tc-overview-legend', 'style'),
              Output('tc-overview-pagination', 'current'),
              Input('tc-overview-mode', 'value'), Input('tc-overview-window', 'value'))
def overview_controls(mode, _window):
    direction = '过去' if mode == 'actual' else '未来'
    options = [{'label': t_task(f'{direction} 24 小时'), 'value': '1d'},
               {'label': t_task(f'{direction} 7 天'), 'value': '7d'},
               {'label': t_task(f'{direction} 30 天'), 'value': '30d'}]
    entries = [(t_task('预览触发数 · 每任务最多50次'), 'planned'), (t_task('结果未知'), 'unknown')] if mode != 'actual' else [
        (t_task('成功'), 'succeeded'), (t_task('失败 / 超时'), 'failed'), (t_task('结果未知'), 'unknown'),
        (t_task('申请 / 在途'), 'running'), (t_task('其他终态'), 'other'), (t_task('时间不完整'), 'open')]
    legend = [html.Span([html.Span(className=f'tc-chart-dot is-{state}'), label],
                        className='tc-chart-key') for label, state in entries]
    return options, legend, {'display': 'none'} if mode == 'list' else {}, 1


@app.callback(Output('main-dcc-url', 'pathname', allow_duplicate=True),
              Output('main-dcc-url', 'search', allow_duplicate=True),
              Input('tc-overview-chart', 'clickData'), State('tc-overview-mode', 'value'), prevent_initial_call=True)
def open_timeline_point(click, mode):
    if not click or not click.get('points'):
        return dash.no_update, dash.no_update
    identifier = click['points'][0].get('customdata')
    if not isinstance(identifier, str):
        return dash.no_update, dash.no_update
    return ('/task_/task_log', f'?run_id={identifier}') if mode == 'actual' else ('/task_/task_mgmt', f'?task_id={identifier}')


@app.callback(
    Output('tc-overview-summary', 'children'), Output('tc-overview-chart', 'figure'),
    Output('tc-overview-table', 'columns'), Output('tc-overview-table', 'data'),
    Output('tc-overview-pagination', 'total'), Output('tc-overview-notice', 'children'),
    Output('tc-overview-chart-wrap', 'style'), Output('tc-overview-table', 'style'),
    Output('tc-overview-chart-wrap', 'className'), Output('tc-overview-pagination-wrap', 'style'),
    Input('tc-overview-init', 'n_intervals'), Input('tc-overview-refresh', 'nClicks'),
    Input('tc-overview-reset', 'nClicks'),
    Input('tc-overview-mode', 'value'), Input('tc-overview-window', 'value'),
    Input('tc-overview-pagination', 'current'), Input('tc-overview-pagination', 'pageSize'),
    Input('platform-theme', 'data'), State('tc-overview-chart', 'figure'),
)
def refresh_overview(_init, _refresh, _reset, mode, window, page, page_size, theme=None, current_figure=None):
    if dash.ctx.triggered_id == 'platform-theme' and current_figure:
        return (dash.no_update, restyle_figure(current_figure, theme), *([dash.no_update] * 8))
    try:
        summary, figure, columns, rows, total, result = build_overview(
            _OverviewAccess(get_menu_access()), mode or 'schedule', window or '1d', page or 1, page_size or 20)
        notice = fac.AntdAlert(message=t_task('调度服务暂不可用；概览仅包含安全的已认领历史元数据。'),
                               type='warning', showIcon=True) if not result.get('online', True) else None
        chart_style = {'display': 'none'} if mode == 'list' else {'width': '100%'}
        table_style = {'display': 'block'} if mode == 'list' else {'display': 'none'}
        chart_class = 'tc-timeline-scroll' + (' is-empty' if not figure['data'] else '')
        pagination_style = {'display': 'none'} if not total else {}
        return (summary, restyle_figure(figure, theme), columns, rows, total, notice,
            chart_style, table_style, chart_class, pagination_style)
    except Exception as exc:
        return (None, {'data': [], 'layout': {'annotations': [{'text': t_task('概览暂不可用'), 'showarrow': False}]}},
                [], [], 0, fac.AntdAlert(message=task_message(str(exc)), type='error', showIcon=True),
                {'display': 'none'}, {'display': 'none'}, 'tc-timeline-scroll is-empty', {'display': 'none'})


def _scheduler_status_snapshot():
    now = time.monotonic()
    with _STATUS_CACHE_LOCK:
        if _STATUS_CACHE['value'] is not None and now < _STATUS_CACHE['expires_at']:
            return _STATUS_CACHE['value']
        try:
            status = util_apscheduler.get_scheduler_status(timeout=2)
            checked_at = datetime.now().astimezone().isoformat()
            if status.get('service') != 'si-platform-scheduler' or not status.get('ready'):
                result = {'state': status.get('state', 'unavailable'), 'status': status,
                          'checked_at': checked_at}
            else:
                result = {'state': status.get('state', 'running'), 'status': status,
                          'checked_at': checked_at}
        except Exception as error:
            error_text = str(error).lower()
            result = {'state': 'authentication' if 'auth' in error_text or 'secret' in error_text else 'offline',
                      'status': None}
        _STATUS_CACHE.update({'expires_at': now + 5, 'value': result})
        return result


@app.callback(
    Output('tc-scheduler-status', 'children'),
    Input('tc-scheduler-status-refresh', 'n_intervals'),
)
def refresh_scheduler_status(_tick):
    access = get_menu_access()
    can_view_details = access.has_access('调度启动配置')
    snapshot = _scheduler_status_snapshot()
    state = snapshot['state']
    if state in {'running', 'paused'}:
        status = snapshot['status']
        label = t__task('调度服务运行中') if state == 'running' else t__task('调度服务在线，任务保持暂停')
        details = [html.Span(f"{t__task('进程 ID')} {status['process_id']}", className='tc-scheduler-detail')]
        if status.get('started_at'):
            details.append(html.Span(f"{t__task('启动于')} {status['started_at']}", className='tc-scheduler-detail'))
        if snapshot.get('checked_at'):
            details.append(html.Span(f"{t__task('最近检测')} {snapshot['checked_at']}", className='tc-scheduler-detail'))
        if can_view_details:
            details.append(html.Span(f"{t__task('任务数')} {status.get('job_count', 0)}", className='tc-scheduler-detail'))
        return html.Div([
            html.Span(label, className='tc-scheduler-state'),
            html.Details([html.Summary(fac.AntdIcon(icon='antd-info-circle'), title=t__task('最近检测')),
                          html.Div(details, className='tc-scheduler-detail-popover')],
                         className='tc-scheduler-details'),
        ], className=f'tc-scheduler-status-inner is-{state}')
    if state == 'authentication':
        label = t__task('调度服务认证失败或未配置 RPC 密钥')
    elif state == 'offline':
        label = t__task('调度服务离线或暂不可达')
    else:
        label = t__task('调度服务尚未就绪')
    return html.Div(html.Span(label, className='tc-scheduler-state'),
                    className='tc-scheduler-status-inner is-offline')


def _save_policy(access, mode, revision, keep_awake=None):
    if not access.has_access('调度启动配置'):
        raise PermissionError('需要系统管理员权限才能修改全局启动策略')
    if mode not in STARTUP_MODES:
        raise StartupConfigError('无效的调度启动策略')
    current = load_startup_config()
    if current['revision'] != revision:
        raise StartupConfigError('配置已被其他管理员修改，请刷新后重试')
    updated = save_scheduler_mode(mode, revision, keep_awake=keep_awake)
    _logger.info(
        'scheduler_startup_policy_changed actor=%s old=%s new=%s keep_awake=%s revision=%s',
        access.user_name, current['scheduler_mode'], mode, updated['keep_awake'], updated['revision'],
    )
    return updated


@app.callback(
    Output('tc-scheduler-policy-confirm', 'visible'),
    Output('tc-scheduler-policy-pending', 'data'),
    Output('tc-scheduler-policy-notice', 'children'),
    Output('tc-scheduler-policy-revision', 'data'),
    Input('tc-scheduler-policy-save', 'nClicks'),
    Input('tc-scheduler-policy-confirm', 'okCounts'),
    Input('tc-scheduler-policy-confirm', 'cancelCounts'),
    State('tc-scheduler-policy-mode', 'value'),
    State('tc-scheduler-keep-awake', 'checked'),
    State('tc-scheduler-policy-revision', 'data'),
    State('tc-scheduler-policy-pending', 'data'),
    prevent_initial_call=True,
)
def save_startup_policy(_save_clicks, _confirm_clicks, _cancel_clicks, mode, keep_awake, revision, pending):
    access = get_menu_access()
    if not access.has_access('调度启动配置'):
        raise PermissionError('需要系统管理员权限才能修改全局启动策略')
    trigger = dash.ctx.triggered_id
    if trigger == 'tc-scheduler-policy-confirm' and dash.ctx.triggered_prop_ids:
        triggered = set(dash.ctx.triggered_prop_ids)
        if 'tc-scheduler-policy-confirm.cancelCounts' in triggered:
            return False, None, t__task('已取消，配置未更改。'), dash.no_update
        if 'tc-scheduler-policy-confirm.okCounts' in triggered:
            try:
                if (not pending or pending.get('mode') != 'paired_running'
                    or pending.get('mode') != mode or pending.get('keep_awake') != keep_awake
                    or pending.get('revision') != revision):
                    raise StartupConfigError('确认已过期，请重新发起保存。')
                updated = _save_policy(access, pending['mode'], revision, pending['keep_awake'])
                return False, None, t__task('已保存；下次通过统一启动器启动时生效。'), updated['revision']
            except Exception as error:
                return False, None, f"{t__task('保存失败')}：{task_message(str(error))}", dash.no_update
    try:
        current = load_startup_config()
        if current['revision'] != revision:
            raise StartupConfigError('配置已被其他管理员修改，请刷新后重试')
        if mode not in STARTUP_MODES:
            raise StartupConfigError('无效的调度启动策略')
        if mode == 'paired_running' and current['scheduler_mode'] != 'paired_running':
            return True, {'mode': mode, 'keep_awake': keep_awake, 'revision': revision}, None, dash.no_update
        updated = _save_policy(access, mode, revision, keep_awake)
        return False, None, t__task('已保存；下次通过统一启动器启动时生效。'), updated['revision']
    except Exception as error:
        return False, None, f"{t__task('保存失败')}：{task_message(str(error))}", dash.no_update


app.clientside_callback(
    """
    checked => {
        const result = window.platformKeepAwake?.set(Boolean(checked));
        if (result === false) return '当前浏览器不支持保持设备唤醒';
        return checked ? '已开启（仅对当前浏览器生效）' : '已关闭';
    }
    """,
    Output('tc-scheduler-keep-awake-state', 'children'),
    Input('tc-scheduler-keep-awake', 'checked'),
)

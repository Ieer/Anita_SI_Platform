from server import app
import dash
from dash import Input, Output, State, html, ctx
import feffery_antd_components as fac
from common.utilities import util_sys
from i18n import t__dashboard
from datetime import datetime, timedelta


def percent(value):
    return f'{value * 100:.1f}%' if value is not None else 'N/A'


def descriptions(info, fields):
    return [{'label': t__dashboard(label), 'children': percent(info[key]) if ratio else (
        info[key] if info[key] is not None else 'N/A')}
        for label, key, ratio in fields]


def metric(label, value, note, ratio=None, tone='normal'):
    return html.Div([
        html.Span(t__dashboard(label), className='monitor-metric-label'),
        html.Strong(value, className='monitor-metric-value'),
        html.Div(html.Div(style={'width': f'{max(0, min(100, ratio * 100)):.1f}%'}),
                 className='monitor-meter') if ratio is not None else None,
        html.Span(note, className='monitor-metric-note'),
    ], className=f'monitor-metric monitor-tone-{tone}')


def build_snapshot(info, history, theme=None):
    from common.utilities.util_theme import restyle_figure
    readable = [disk for disk in info['sys_files'] if disk['readable']]
    maximum = max(readable, key=lambda disk: disk['usage_percent']) if readable else None
    disk_ratio = maximum['usage_percent'] / 100 if maximum else None
    summary = [
        metric('CPU 使用率', percent(info['cpu_all_usage_percent']),
               f"{info['cpu_num']} " + t__dashboard('逻辑核数'), info['cpu_all_usage_percent'],
               'warning' if (info['cpu_all_usage_percent'] or 0) >= .9 else 'normal'),
        metric('内存使用率', percent(info['memory_usage_percent']),
               f"{info['memory_used']} / {info['memory_total']}", info['memory_usage_percent'],
               'warning' if info['memory_usage_percent'] >= .9 else 'normal'),
        metric('最高磁盘使用率', percent(disk_ratio), maximum['type_name'] if maximum else 'N/A', disk_ratio,
               'danger' if (disk_ratio or 0) >= .9 else 'warning' if (disk_ratio or 0) >= .8 else 'normal'),
        metric('进程内存', info['current_process_memory_usage'], f"PID {info['process_id']}"),
    ]
    sampled_at = datetime.fromisoformat(info['sampled_at'])
    cutoff = (sampled_at - timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
    history = [point for point in (history or []) if isinstance(point, dict)
               and all(key in point for key in ('time', 'cpu', 'memory'))
               and isinstance(point['time'], str) and cutoff <= point['time'] <= info['sampled_at']][-60:]
    if not history or history[-1]['time'] != info['sampled_at']:
        history.append({'time': info['sampled_at'],
                        'cpu': info['cpu_all_usage_percent'] * 100 if info['cpu_all_usage_percent'] is not None else None,
                        'memory': info['memory_usage_percent'] * 100})
    history = history[-60:]
    figure = {'data': [
        {'type': 'scatter', 'mode': 'lines+markers', 'name': t__dashboard(label),
         'x': [point['time'] for point in history], 'y': [point[key] for point in history],
         'line': {'color': color, 'width': 2}, 'marker': {'size': 3}, 'connectgaps': False,
         'hovertemplate': '%{y:.1f}%<extra>%{fullData.name}</extra>'}
        for label, key, color in [('CPU', 'cpu', '#087f77'), ('内存', 'memory', '#5272b5')]
    ], 'layout': {
        'margin': {'l': 40, 'r': 14, 't': 12, 'b': 35},
        'height': 220, 'autosize': True, 'hovermode': 'x unified',
        'font': {'family': 'Bahnschrift, Microsoft YaHei UI, sans-serif', 'size': 11, 'color': '#637478'},
        'paper_bgcolor': 'rgba(0,0,0,0)', 'plot_bgcolor': 'rgba(0,0,0,0)',
        'xaxis': {'type': 'date', 'tickformat': '%H:%M:%S', 'showgrid': False,
              'range': [cutoff, info['sampled_at']]},
        'yaxis': {'range': [0, 100], 'ticksuffix': '%', 'gridcolor': '#e9eeee', 'zeroline': False},
        'legend': {'orientation': 'h', 'y': 1.2, 'x': 1, 'xanchor': 'right'},
        'uirevision': 'monitor',
    }}
    disks = []
    warnings = 0
    for disk in info['sys_files']:
        value = disk['usage_percent']
        state, color = ('无法读取', 'default') if value is None else (
            ('空间紧张', 'red') if value >= 90 else ('注意容量', 'gold') if value >= 80 else ('正常', 'green'))
        warnings += value is not None and value >= 80
        progress = fac.AntdProgress(percent=value, size='small', strokeColor=(
            'var(--platform-danger)' if value >= 90 else 'var(--platform-warning)' if value >= 80
            else 'var(--platform-accent)')) if value is not None else html.Span('N/A')
        disks.append({**disk, 'usage': progress,
            'status': [{'tag': t__dashboard(state), 'color': color}]})
    unavailable = len(info['sys_files']) - len(readable)
    disk_status = (f"{len(disks)} " + t__dashboard('个磁盘') + ' · ' +
                   f"{warnings} " + t__dashboard('容量预警') + ' · ' +
                   f"{unavailable} " + t__dashboard('无法读取'))
    return summary, history, restyle_figure(figure, theme), disks, disk_status


@app.callback(
    [Output('monitor-intervals', 'disabled'), Output('monitor-pause', 'children'),
     Output('monitor-pause', 'title'), Output('monitor-pause', 'aria-label'),
     Output('monitor-pause', 'aria-pressed')],
    Input('monitor-pause', 'n_clicks'),
)
def toggle_refresh(clicks):
    paused = bool((clicks or 0) % 2)
    label = t__dashboard('恢复自动刷新' if paused else '暂停自动刷新')
    return paused, fac.AntdIcon(icon='antd-caret-right' if paused else 'antd-pause'), label, label, str(paused).lower()


@app.callback(
    [
        Output('monitor-sys-desc', 'items'),
        Output('monitor-cpu-load-desc', 'items'),
        Output('monitor-mem-load-desc', 'items'),
        Output('monitor-process-desc', 'items'),
        Output('monitor-disk-desc', 'data'),
        Output('monitor-io-desc', 'items'),
        Output('monitor-summary', 'children'),
        Output('monitor-history', 'data'),
        Output('monitor-trend', 'figure'),
        Output('monitor-updated', 'children'),
        Output('monitor-disk-status', 'children'),
    ],
    [
        Input('monitor-intervals', 'n_intervals'),
        Input('monitor-intervals-init', 'timeoutCount'),
        Input('monitor-refresh', 'n_clicks'),
        Input('monitor-intervals', 'disabled'),
        Input('platform-theme', 'data'),
    ],
    [State('monitor-history', 'data'), State('monitor-trend', 'figure')],
)
def callback_func(n_intervals, timeoutCount, refresh_clicks, paused, theme, history, current_figure):
    if ctx.triggered_id == 'platform-theme' and current_figure:
        from common.utilities.util_theme import restyle_figure
        return [dash.no_update] * 8 + [restyle_figure(current_figure, theme)] + [dash.no_update] * 2
    if ctx.triggered_id == 'monitor-refresh':
        util_sys.cache.clear()
    info = util_sys.get_sys_info()
    summary, history, figure, disks, disk_status = build_snapshot(info, history, theme)
    return [
        descriptions(info, [('主机名', 'hostname', False), ('系统类型', 'os_name', False),
                            ('计算机名', 'computer_name', False), ('核心架构', 'os_arch', False),
                            ('主机启动时间', 'boot_time', False), ('主机运行时长', 'host_uptime', False)]),
        descriptions(info, [('型号', 'cpu_name', False), ('物理核数', 'cpu_physical_num', False),
                            ('逻辑核数', 'cpu_num', False), ('当前频率', 'cpu_frequency', False),
                            ('总使用率', 'cpu_all_usage_percent', True), ('用户使用率', 'cpu_user_usage_percent', True),
                            ('系统使用率', 'cpu_sys_usage_percent', True), ('空闲率', 'cpu_free_percent', True)]),
        descriptions(info, [('总量', 'memory_total', False), ('已用', 'memory_used', False),
                            ('可用内存', 'memory_available', False), ('空闲内存', 'memory_free', False),
                            ('使用率', 'memory_usage_percent', True), ('交换内存总量', 'swap_total', False),
                            ('交换内存已用', 'swap_used', False), ('交换内存使用率', 'swap_usage_percent', True)]),
        descriptions(info, [('Python版本', 'python_version', False), ('进程 ID', 'process_id', False),
                            ('启动时间', 'start_time', False), ('运行时长', 'run_time', False),
                            ('线程数', 'process_threads', False), ('内存使用量', 'current_process_memory_usage', False),
                            ('占主机内存', 'process_memory_percent', True)]),
        disks,
        descriptions(info, [('累计接收', 'network_received', False), ('累计发送', 'network_sent', False),
                            ('网络错误', 'network_errors', False), ('网络丢包', 'network_dropped', False),
                            ('累计磁盘读取', 'disk_read', False), ('累计磁盘写入', 'disk_written', False)]),
        summary, history, figure,
        info['sampled_at'] + ' · ' + t__dashboard('自动刷新已暂停' if paused else '每5秒刷新'), disk_status,
    ]

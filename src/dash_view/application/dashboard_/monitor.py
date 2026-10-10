from common.utilities.util_menu_access import MenuAccess
import feffery_utils_components as fuc
import feffery_antd_components as fac
from common.utilities.util_logger import Log
from dash import html, dcc
import dash_callback.application.dashboard_.monitor_c  # noqa
from i18n import t__dashboard


# 二级菜单的标题、图标和显示顺序
title = '系统监控'
icon = None
order = 2
logger = Log.get_logger(__name__)

access_metas = ('监控页-页面',)


def detail_section(label, identifier):
    return html.Section([
        html.H3(t__dashboard(label)),
        fac.AntdDescriptions(id=identifier, items=[], column=1, size='small'),
    ], className='monitor-detail')


def render_content(menu_access: MenuAccess, **kwargs):
    return html.Div([
        dcc.Interval(id='monitor-intervals', interval=5000),
        fuc.FefferyTimeout(id='monitor-intervals-init', delay=0),
        dcc.Store(id='monitor-history', data=[]),
        html.Header([
            html.H2(t__dashboard('系统监控')),
            html.Div([
                html.Span(t__dashboard('等待采样'), id='monitor-updated', role='status'),
                html.Button(fac.AntdIcon(icon='antd-pause'), id='monitor-pause',
                            title=t__dashboard('暂停自动刷新'), n_clicks=0,
                            **{'aria-label': t__dashboard('暂停自动刷新'), 'aria-pressed': 'false'}),
                html.Button(fac.AntdIcon(icon='antd-reload'), id='monitor-refresh',
                            title=t__dashboard('立即刷新'), n_clicks=0,
                            **{'aria-label': t__dashboard('立即刷新')}),
            ], className='monitor-controls'),
        ], className='monitor-heading'),
        html.Div(id='monitor-summary', className='monitor-summary', **{'aria-live': 'polite'}),
        html.Div([
            html.Section([
                html.Div([html.H3(t__dashboard('资源趋势')),
                          html.Span(t__dashboard('最近5分钟'), className='monitor-section-note')],
                         className='monitor-section-heading'),
                dcc.Graph(id='monitor-trend', config={'displayModeBar': False, 'responsive': True},
                          style={'height': '220px'}, figure={'data': [], 'layout': {
                              'margin': {'l': 38, 'r': 12, 't': 12, 'b': 35},
                              'yaxis': {'range': [0, 100], 'ticksuffix': '%'},
                              'paper_bgcolor': 'rgba(0,0,0,0)', 'plot_bgcolor': 'rgba(0,0,0,0)'}}),
            ], className='monitor-trend-section'),
            detail_section('网络与磁盘 I/O', 'monitor-io-desc'),
        ], className='monitor-activity'),
        html.Section([
            html.Div([html.H3(t__dashboard('磁盘状态')),
                      html.Span(id='monitor-disk-status', className='monitor-section-note')],
                     className='monitor-section-heading'),
            html.Div(fac.AntdTable(
                id='monitor-disk-desc',
                columns=[
                    {'title': t__dashboard('设备名'), 'dataIndex': 'dir_name', 'width': 110},
                    {'title': t__dashboard('文件系统类型'), 'dataIndex': 'sys_type_name', 'width': 120},
                    {'title': t__dashboard('总容量'), 'dataIndex': 'total', 'width': 100},
                    {'title': t__dashboard('已用'), 'dataIndex': 'used', 'width': 100},
                    {'title': t__dashboard('可用'), 'dataIndex': 'free', 'width': 100},
                    {'title': t__dashboard('使用率'), 'dataIndex': 'usage', 'width': 190},
                    {'title': t__dashboard('状态'), 'dataIndex': 'status', 'width': 110,
                     'renderOptions': {'renderType': 'tags'}},
                    {'title': t__dashboard('挂载点'), 'dataIndex': 'type_name', 'width': 110},
                ],
                data=[], bordered=False, pagination=False, size='small',
            ), className='monitor-table-scroll'),
        ], className='monitor-disks'),
        html.Div([
            detail_section('系统信息', 'monitor-sys-desc'),
            detail_section('CPU', 'monitor-cpu-load-desc'),
            detail_section('内存', 'monitor-mem-load-desc'),
            detail_section('进程运行状态', 'monitor-process-desc'),
        ], className='monitor-details'),
    ], id='system-monitor', className='system-monitor')

from common.utilities.util_menu_access import MenuAccess
import feffery_antd_components as fac
from dash import dcc, html
import dash_callback.application.task_.overview_c  # noqa: F401
from common.utilities.util_startup import StartupConfigError
from common.utilities.util_startup import load_startup_config
from i18n import t__task

title = '任务概览'
icon = 'antd-dashboard'
order = 1
access_metas = ('rpa_scheduler',)


def render_content(menu_access: MenuAccess, **kwargs):
    try:
        startup_config = load_startup_config()
        policy_error = None
    except StartupConfigError as error:
        startup_config = {'revision': None, 'scheduler_mode': None, 'keep_awake': False}
        policy_error = str(error)
    children = [
        html.Div([html.Div([html.Span('TASK CENTER', className='tc-eyebrow'),
                   html.H2(t__task('任务概览'))]),
              html.Div([html.Div(id='tc-scheduler-status', className='tc-scheduler-status')],
                       className='tc-overview-heading-controls')], className='tc-page-heading'),
        dcc.Store(id='tc-overview-data'),
        dcc.Interval(id='tc-overview-init', interval=100, max_intervals=1),
        dcc.Interval(id='tc-scheduler-status-refresh', interval=5000, n_intervals=0),
        html.Div(id='tc-overview-notice'),
    ]
    if menu_access is not None and menu_access.has_access('调度启动配置'):
        children[0].children[1].children.append(html.Details([
            html.Summary(fac.AntdIcon(icon='antd-setting'), title=t__task('启动策略'),
                         **{'aria-label': t__task('启动策略')}),
            dcc.Store(id='tc-scheduler-policy-revision', data=startup_config['revision']),
            dcc.Store(id='tc-scheduler-policy-pending'),
            html.Div([html.Div([
                html.Strong(t__task('启动策略')),
                fac.AntdSelect(id='tc-scheduler-policy-mode', value=startup_config['scheduler_mode'],
                               options=[
                                   {'label': t__task('仅 Web'), 'value': 'web_only'},
                                   {'label': t__task('联启并运行任务'), 'value': 'paired_running'},
                                   {'label': t__task('联启但保持暂停'), 'value': 'paired_paused'},
                               ], style={'width': 230}, disabled=bool(policy_error)),
                fac.AntdButton(t__task('保存下次启动策略'), id='tc-scheduler-policy-save',
                               type='primary', disabled=bool(policy_error)),
            ], className='tc-scheduler-policy-row'),
            html.Div([
                html.Strong(t__task('保持设备唤醒')),
                fac.AntdSwitch(
                    id='tc-scheduler-keep-awake',
                    checked=startup_config['keep_awake'],
                    disabled=bool(policy_error),
                    checkedChildren=t__task('开启'),
                    unCheckedChildren=t__task('关闭'),
                ),
                html.Span(id='tc-scheduler-keep-awake-state', role='status'),
            ], className='tc-scheduler-policy-row'),
            html.Div(policy_error, id='tc-scheduler-policy-notice', className='tc-scheduler-policy-notice',
                     role='status'),
            ], className='tc-overview-settings-panel'),
            fac.AntdModal(
                [html.P(t__task('启用后，下次通过统一启动器启动时会恢复计划和监听任务；已到期任务可能运行。'))],
                id='tc-scheduler-policy-confirm', title=t__task('确认启用任务调度'), visible=False,
                forceRender=True, okClickClose=False, okText=t__task('确认启用'), cancelText=t__task('取消'),
            ),
        ], className='tc-scheduler-policy tc-overview-settings'))
    children.insert(0, html.Div(
        className='page-agent-context',
        hidden=True,
        **{
            'data-page': 'task-overview',
            'data-capabilities': 'list_tasks,open_task_log,filter_task',
            'data-agent-policy': 'no_execute',
        },
    ))
    children.extend([
        html.Div([
            fac.AntdSegmented(id='tc-overview-mode', value='schedule', block=False, options=[
                {'label': t__task('计划'), 'value': 'schedule', 'icon': 'antd-calendar'},
                {'label': t__task('实际运行'), 'value': 'actual', 'icon': 'antd-line-chart'},
                {'label': t__task('任务列表'), 'value': 'list', 'icon': 'antd-unordered-list'},
            ]),
            html.Div([fac.AntdSelect(id='tc-overview-window', value='1d', style={'width': 150}, options=[
                {'label': t__task('未来 24 小时'), 'value': '1d'},
                {'label': t__task('未来 7 天'), 'value': '7d'}, {'label': t__task('未来 30 天'), 'value': '30d'},
            ]),
            fac.AntdButton(id='tc-overview-reset', title=t__task('还原当前时间范围'),
                           icon=fac.AntdIcon(icon='antd-home')),
            fac.AntdButton(id='tc-overview-refresh', title=t__task('刷新概览'),
                           icon=fac.AntdIcon(icon='antd-reload')),
            ], className='tc-overview-tools'),
        ], className='tc-toolbar'),
        html.Div(id='tc-overview-summary'),
        html.Div(id='tc-overview-legend', className='tc-timeline-legend'),
        html.Div(dcc.Graph(id='tc-overview-chart', config={
            'displayModeBar': True, 'displaylogo': False, 'responsive': True,
            'modeBarButtons': [['pan2d', 'zoom2d', 'zoomIn2d', 'zoomOut2d']],
        }, className='tc-timeline'), id='tc-overview-chart-wrap', className='tc-timeline-scroll'),
        fac.AntdTable(id='tc-overview-table', columns=[], data=[], size='small',
                      tableLayout='fixed', pagination=False, className='tc-table'),
        html.Div(fac.AntdPagination(id='tc-overview-pagination', current=1, pageSize=20, total=0,
                   showSizeChanger=True, pageSizeOptions=[10, 20, 50, 100]),
             id='tc-overview-pagination-wrap', className='tc-overview-pagination'),
    ])
    return html.Div(children, className='tc-page tc-overview-page')
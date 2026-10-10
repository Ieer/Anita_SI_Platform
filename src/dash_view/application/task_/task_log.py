from common.utilities.util_menu_access import MenuAccess
import feffery_antd_components as fac
from dash import dcc, html
import dash_callback.application.task_.task_log_c  # noqa: F401
from common.task_center import RUN_STATUS_LABELS


# 二级菜单的标题、图标和显示顺序
title = '运行日志'
icon = 'antd-file-search'
order = 4
access_metas = ('任务日志-页面',)


def render_content(menu_access: MenuAccess, **kwargs):
    return html.Div([
        html.Div([html.Div([html.Span('TASK CENTER', className='tc-eyebrow'), html.H2('运行日志')]),
              html.Span('运行记录', className='tc-engine-label')], className='tc-page-heading'),
        dcc.Store(id='tc-log-query', data={'page': 1, 'page_size': 20,
                                           'task_id': kwargs.get('task_id'), 'run_id': kwargs.get('run_id')}),
        dcc.Store(id='tc-log-buffer', data=''),
        dcc.Store(id='tc-log-selected-run'),
        dcc.Store(id='tc-log-cursor', data=0),
        dcc.Download(id='tc-log-download'),
        dcc.Interval(id='tc-log-init', interval=100, max_intervals=1),
        dcc.Interval(id='tc-log-refresh', interval=2500, n_intervals=0, disabled=True),
        html.Div(id='tc-log-notice'),
        html.Div([
            fac.AntdInput(id='tc-log-search', placeholder='筛选任务 / Run ID', allowClear=True,
                          prefix=fac.AntdIcon(icon='antd-search'), style={'width': 'min(280px, 100%)'}),
            fac.AntdSelect(id='tc-log-status', allowClear=True, placeholder='运行状态', style={'width': 150},
                           options=[{'label': label, 'value': state} for state, label in RUN_STATUS_LABELS.items()]),
            fac.AntdButton('刷新', id='tc-log-query-button', icon=fac.AntdIcon(icon='antd-reload')),
            fac.AntdButton('导出原始日志', id='tc-log-export', disabled=True,
                           icon=fac.AntdIcon(icon='antd-download')),
        ], className='tc-toolbar'),
        fac.AntdTable(id='tc-log-table', columns=[
            {'title': '运行', 'dataIndex': 'run_id', 'width': 230},
            {'title': '任务', 'dataIndex': 'name', 'width': 180},
            {'title': '团队 / 发起人', 'dataIndex': 'owner', 'width': 180},
            {'title': '来源', 'dataIndex': 'source', 'width': 100},
            {'title': '开始时间', 'dataIndex': 'started_at', 'width': 190},
            {'title': '耗时', 'dataIndex': 'duration_label', 'width': 100},
            {'title': '结果', 'dataIndex': 'state_label', 'width': 130},
            {'title': '详情', 'dataIndex': 'detail', 'width': 110,
             'renderOptions': {'renderType': 'button'}},
        ], data=[], size='small', tableLayout='fixed', pagination=False,
            className='tc-table'),
        fac.AntdPagination(id='tc-log-pagination', current=1, pageSize=20, total=0,
                           showSizeChanger=True, pageSizeOptions=[10, 20, 50, 100]),
        fac.AntdModal(id='tc-log-detail', title='运行详情', visible=False, width='min(1100px, 96vw)',
                      renderFooter=True, className='tc-modal tc-log-detail-modal'),
    ], id='tc-log-page', className='tc-page tc-log-page')

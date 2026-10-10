from common.utilities.util_menu_access import MenuAccess
from dash import dcc, html
import dash_callback.application.task_.windows_scheduler_c  # noqa: F401
import feffery_antd_components as fac

title = 'Windows 任务'
icon = 'antd-windows'
order = 3
access_metas = ('Windows任務管理-页面',)


def render_content(menu_access: MenuAccess, **kwargs):
    return html.Div([
        html.Div([html.Div([html.Span('TASK CENTER', className='tc-eyebrow'), html.H2('Windows 任务')]),
              html.Span('原生调度', className='tc-engine-label')], className='tc-page-heading'),
        dcc.Store(id='tc-windows-query', data={'page': 1, 'page_size': 20, 'keyword': '', 'status': ''}),
        dcc.Store(id='tc-windows-action'),
        dcc.Download(id='tc-windows-download'),
        dcc.Interval(id='tc-windows-init', interval=100, max_intervals=1),
        html.Div(id='tc-windows-notice'),
        html.Div(id='tc-windows-service-notice'),
        html.Div([
            fac.AntdInput(id='tc-windows-search', placeholder='搜索完整任务路径', allowClear=True,
                          prefix=fac.AntdIcon(icon='antd-search'), style={'width': 'min(280px, 100%)'}),
            fac.AntdSelect(id='tc-windows-status', value='', options=[
                {'label': '全部状态', 'value': ''}, {'label': '就绪', 'value': 'enabled'},
                {'label': '已禁用', 'value': 'paused'}], style={'width': 140}),
            fac.AntdButton('刷新快照', id='tc-windows-refresh', type='primary',
                           icon=fac.AntdIcon(icon='antd-reload')),
            fac.AntdButton('认领历史任务', id='tc-windows-claim-open',
                           icon=fac.AntdIcon(icon='antd-user-add')),
            fac.AntdButton('导出', id='tc-windows-export', icon=fac.AntdIcon(icon='antd-download')),
        ], className='tc-toolbar'),
        html.Div(id='tc-windows-summary', className='tc-summary-strip'),
        fac.AntdAlert(
            message='服务器本机 · Windows 原生执行 · 命令受理不代表运行完成',
            type='info', showIcon=True, className='tc-inline-note'),
        fac.AntdTable(id='tc-windows-table', columns=[
            {'title': '完整任务路径', 'dataIndex': 'name', 'width': 270},
            {'title': '状态 / LastTaskResult', 'dataIndex': 'state', 'width': 180},
            {'title': '下次触发', 'dataIndex': 'next_run', 'width': 185},
            {'title': '服务器账户', 'dataIndex': 'account', 'width': 180},
            {'title': '团队', 'dataIndex': 'group_name', 'width': 130},
            {'title': '原生并发策略', 'dataIndex': 'policy', 'width': 150},
            {'title': '操作', 'dataIndex': 'actions', 'width': 250,
             'renderOptions': {'renderType': 'button'}},
        ], data=[], size='small', tableLayout='fixed', pagination=False, className='tc-table'),
        fac.AntdPagination(id='tc-windows-pagination', current=1, pageSize=20, total=0,
                           showSizeChanger=True, pageSizeOptions=[10, 20, 50, 100]),
        fac.AntdModal(id='tc-windows-confirm', title='确认 Windows 操作', visible=False,
                      renderFooter=True, okClickClose=False, okText='确认', cancelText='返回'),
        fac.AntdModal(id='tc-windows-claim', title='认领 Windows 任务', visible=False,
                      renderFooter=True, okClickClose=False, okText='认领', cancelText='取消'),
    ], className='tc-page tc-windows-page')
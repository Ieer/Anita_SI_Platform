from common.utilities.util_menu_access import MenuAccess
import feffery_antd_components as fac
from dash import dcc, html
from common import task_center
import dash_callback.application.task_.task_mgmt_c  # noqa: F401
from dash_callback.application.task_.i18n import task_label as t_task


# 二级菜单的标题、图标和显示顺序
title = '平台任务'
icon = 'antd-table'
order = 2
access_metas = ('任务管理-页面',)


def render_content(menu_access: MenuAccess, **kwargs):
    return html.Div([
        html.Div([html.Div([html.Span('TASK CENTER', className='tc-eyebrow'), html.H2(t_task('平台任务'))]),
              html.Span('APScheduler', className='tc-engine-label')], className='tc-page-heading'),
        dcc.Store(id='tc-platform-query', data={'page': 1, 'page_size': 20, 'keyword': '', 'status': '',
                               'task_id': kwargs.get('task_id')}),
        dcc.Store(id='tc-platform-selection'),
        dcc.Store(id='tc-platform-action'),
        dcc.Store(id='tc-platform-edit-id'),
        dcc.Store(id='tc-platform-request-key'),
        dcc.Interval(id='tc-platform-init', interval=100, max_intervals=1),
        html.Div(id='tc-platform-notice'),
        html.Div(id='tc-platform-service-notice'),
        html.Div([
            fac.AntdInput(id='tc-platform-search', placeholder=t_task('搜索任务名称'), allowClear=True,
                          prefix=fac.AntdIcon(icon='antd-search'), style={'width': 'min(260px, 100%)'}),
            fac.AntdSelect(id='tc-platform-status', value='', options=[
                {'label': t_task('全部状态'), 'value': ''}, {'label': t_task('启用'), 'value': 'enabled'},
                {'label': t_task('已暂停'), 'value': 'paused'}, {'label': t_task('状态未知'), 'value': 'unknown'}],
                style={'width': 140}),
            fac.AntdSelect(id='tc-platform-team', placeholder=t_task('团队筛选'), allowClear=True,
                           style={'width': 180}),
            fac.AntdSelect(id='tc-platform-type', placeholder=t_task('执行类型'), allowClear=True,
                           options=[{'label': t_task('本机'), 'value': 'local'}, {'label': 'SSH', 'value': 'ssh'}],
                           style={'width': 130}),
            fac.AntdButton(t_task('新建任务'), id='tc-platform-create', type='primary',
                           icon=fac.AntdIcon(icon='antd-plus')),
            fac.AntdButton(t_task('认领历史任务'), id='tc-platform-claim-open', icon=fac.AntdIcon(icon='antd-user-add')),
                        *([fac.AntdButton(t_task('团队授权'), id='tc-platform-grant-open',
                                                            icon=fac.AntdIcon(icon='antd-team'))]
                            if 'admin' in getattr(getattr(menu_access, 'user_info', None), 'user_roles', [])
                            or task_center.can_manage_grants(menu_access) else []),
            fac.AntdButton(t_task('刷新'), id='tc-platform-refresh', title=t_task('刷新任务'),
                           icon=fac.AntdIcon(icon='antd-reload')),
        ], className='tc-toolbar'),
        html.Div(id='tc-platform-summary', className='tc-summary-strip'),
        fac.AntdTable(id='tc-platform-table', columns=[
            {'title': t_task('任务'), 'dataIndex': 'name', 'width': 220},
            {'title': t_task('团队'), 'dataIndex': 'group_name', 'width': 130},
            {'title': t_task('触发计划'), 'dataIndex': 'plan_label', 'width': 210},
            {'title': t_task('下次运行'), 'dataIndex': 'next_run', 'width': 190},
            {'title': t_task('状态'), 'dataIndex': 'state', 'width': 120},
            {'title': t_task('操作'), 'dataIndex': 'actions', 'width': 300,
             'renderOptions': {'renderType': 'button'}},
        ], data=[], size='small', tableLayout='fixed',
            pagination=False, className='tc-table'),
        fac.AntdPagination(id='tc-platform-pagination', current=1, pageSize=20, total=0,
                           showSizeChanger=True, pageSizeOptions=[10, 20, 50, 100], showQuickJumper=True),
        fac.AntdModal(id='tc-platform-editor', title=t_task('任务定义'), visible=False, width='min(900px, 96vw)',
                      maskClosable=False, renderFooter=True, okClickClose=False, okText=t_task('保存'), cancelText=t_task('取消'),
                      className='tc-modal'),
        fac.AntdModal([
            html.P(id='tc-platform-confirm-summary'),
            fac.AntdSelect(id='tc-platform-transfer-team', value=None, options=[],
                           placeholder=t_task('目标团队'), style={'display': 'none'}),
            fac.AntdInput(id='tc-platform-resolve-reason', value='', mode='text-area',
                          placeholder=t_task('人工核实依据'), style={'display': 'none'}),
        ], id='tc-platform-confirm', title=t_task('确认操作'), visible=False, forceRender=True,
                      renderFooter=True, okClickClose=False, okText=t_task('确认'), cancelText=t_task('返回')),
        fac.AntdModal(id='tc-platform-claim', title=t_task('认领历史任务'), visible=False,
                      renderFooter=True, okClickClose=False, okText=t_task('认领并暂停'), cancelText=t_task('取消')),
        fac.AntdModal(id='tc-platform-grant', title=t_task('团队操作授权'), visible=False,
                      renderFooter=True, okClickClose=False, okText=t_task('保存授权'), cancelText=t_task('取消')),
        dcc.Download(id='tc-platform-download'),
    ], className='tc-page tc-platform-page')

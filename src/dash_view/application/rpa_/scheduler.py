from dash import dcc
from common.utilities.util_menu_access import MenuAccess

title = 'RPA调度中心'
icon = 'antd-schedule'
order = 1
access_metas = ('rpa_scheduler',)


def render_content(menu_access: MenuAccess, **kwargs):
    return dcc.Location(id='legacy-rpa-scheduler-redirect', href='/task_/overview', refresh=True)# -*- coding: utf-8 -*-
"""
# src/dash_view/application/rpa_/scheduler.py
RPA调度中心页面
管理RPA任务调度，提供查询、控制和管理功能。
"""

from dash import html, dcc, Output, Input, callback, no_update
import feffery_antd_components as fac
import datetime as dt
from i18n import t__access, t__default
from common.utilities.util_logger import Log
from common.utilities.util_apscheduler import get_apscheduler_all_jobs
from database.sql_db.repositories.apscheduler_repository import select_extract_values_between
from database.sql_db.repositories.apscheduler_repository import select_latest_results
from common.utilities.util_apscheduler import add_local_date_job
from typing import Any
from dash_components.create_scheduler import create_scheduler
from common.utilities.util_menu_access import get_menu_access

title = 'RPA调度中心'
icon = 'antd-schedule'
order = 1

# 引入自定義 Tooltip 樣式
external_stylesheets = ['/assets/scheduler_tooltip.css']
external_scripts = []
logger = Log.get_logger(__name__)

# 確保回調被註冊（填充表格/摘要等），以 side-effect 方式導入 callback 模組
try:
    from dash_callback.application.rpa_ import scheduler_c  # noqa: F401
except Exception:
    # 若回調模組不可用，不影響本頁其他功能（如甘特圖）；僅記錄日誌
    logger.warning('未能載入 scheduler_c 回調模組，任務列表可能不更新')

# 元素 ID 定义
TABLE_STORE_ID = 'rpa-scheduler-store'                        # 存储任务数据
REFRESH_INTERVAL_ID = 'rpa-scheduler-refresh-interval'        # 刷新间隔
BASE_DATE_ID = 'rpa-scheduler-base-date'                      # 基础日期选择
VIEW_RANGE_ID = 'rpa-scheduler-view-range'                    # 视图范围选择
STATUS_FILTER_ID = 'rpa-scheduler-status-filter'              # 状态过滤器
SEARCH_ID = 'rpa-scheduler-search'                            # 搜索框
DETAIL_STORE_ID = 'rpa-scheduler-detail-store'                # 详情存储
VIEW_TABS_ID = 'rpa-scheduler-view-tabs'                      # 视图切换
TABLE_ID = 'rpa-scheduler-table'                              # 任务列表表格
STATUS_CHART_ID = 'rpa-scheduler-status-chart'                # 狀態圖表容器
STATUS_WRAP_ID = 'rpa-scheduler-status-wrap'                  # 狀態區塊包裹層（便於顯示/隱藏）
STATUS_TOGGLE_ID = 'rpa-scheduler-toggle-status'              # 狀態總覽顯示開關
LEGEND_EXTRA_ID = 'rpa-scheduler-legend-extra'                # 右上角圖例（單排，含比例提示）

def _build_table_columns():
    """延迟构造表格列，避免在無請求上下文時調用 i18n。"""
    return [
        {'title': 'Job ID', 'dataIndex': 'job_id', 'width': 200},
        {'title': t__default('触发器'), 'dataIndex': 'trigger', 'width': 120},
        {'title': t__default('下次执行'), 'dataIndex': 'next_run', 'width': 180},
        {'title': t__default('状态'), 'dataIndex': 'status', 'width': 120},
        {'title': t__default('下一步动作'), 'dataIndex': 'next_action', 'width': 120},
        {'title': t__default('运行类型'), 'dataIndex': 'type', 'width': 120},
        {'title': t__default('脚本类型'), 'dataIndex': 'script_type', 'width': 120},
        {'title': t__default('超时秒数'), 'dataIndex': 'timeout', 'width': 120},
        {'title': t__default('操作'), 'dataIndex': 'action', 'renderOptions': {'renderType': 'button'}, 'width': 160},
    ]

# 任务类型与颜色对应
JOB_TYPE_COLORS = {
    'interval': '#1890ff',                                  # 间隔任务
    'date': '#13c2c2',                                      # 日期任务
    'listen': '#fa8c16',                                    # 监听任务
    'windows': '#fa541c',                                   # Windows任务
    'cron': '#722ed1',                                      # Cron任务
    'daily': '#d48806',                                     # 日任务
    'weekly': '#0088cc',                                    # 周任务
    'monthly': '#34137d',                                   # 月任务
    'disabled': '#757474'                                   # 禁用状态
}

# 任务状态与颜色对应
STATUS_COLORS = {
    'enabled': '#52c41a',                                   # 启用状态
    'disabled': '#ff4d4f',                                  # 禁用状态
    'running': '#1890ff',                                   # 运行中状态
    'pending': '#faad14',                                   # 待处理状态
    'success': '#52c41a',                                   # 成功状态
    'failed': '#ff4d4f'                                     # 失败状态
}

# 权限标签，供菜单系统自动识别
access_metas = ('rpa_scheduler',)

def layout(menu_access=None):
    today_str = dt.date.today().strftime('%Y-%m-%d')
    status_options = [{'label': '启用', 'value': 'enabled'}, {'label': '暂停', 'value': 'disabled'}]
    view_options: Any = [
        {'label': t__default('日视图'), 'value': 'day'},
        {'label': t__default('周视图'), 'value': 'week'},
        {'label': t__default('月视图'), 'value': 'month'},
    ]
    # 右上角圖例改為動態單排（含顯示比例提示），由 callback 生成
    legend_extra = fac.AntdSpace(id=LEGEND_EXTRA_ID, size='small', wrap=True, className='rpa-scheduler-legend-row')
    toolbar = html.Div(
        [
            fac.AntdSpace(
                [
                    fac.AntdDatePicker(
                        id=BASE_DATE_ID,
                        value=today_str,
                        format='YYYY-MM-DD',
                        style={'width': 140},
                        size='small',
                    ),
                    fac.AntdSpace(
                        [
                            fac.AntdButton(t__default('上月'), id='rpa-scheduler-prev-month', size='small'),
                            fac.AntdButton('◀', id='rpa-scheduler-prev', size='small'),
                            fac.AntdButton(t__default('今天'), id='rpa-scheduler-today', size='small'),
                            fac.AntdButton('▶', id='rpa-scheduler-next', size='small'),
                            fac.AntdButton(t__default('下月'), id='rpa-scheduler-next-month', size='small'),
                        ],
                        size='small',
                        className='date-nav-buttons',
                    ),
                    fac.AntdSegmented(
                        id=VIEW_RANGE_ID,
                        value='week',
                        options=view_options,  # type: ignore
                        size='small',
                    ),
                    fac.AntdSelect(
                        id=STATUS_FILTER_ID,
                        allowClear=True,
                        placeholder=t__default('状态'),
                        style={'width': 140},
                        options=status_options,  # type: ignore
                        size='small',
                    ),
                    fac.AntdInput(
                        id=SEARCH_ID,
                        placeholder=t__default('任务名称'),
                        allowClear=True,
                        style={'width': 200},
                        prefix=fac.AntdIcon(icon='antd-search'),
                        size='small',
                    ),
                ],
                wrap=True,
                size='small',
                className='rpa-scheduler-filter-group',
            ),
            fac.AntdSpace(
                [
                    fac.AntdButton(
                        t__default('新建任务'),
                        id='rpa-scheduler-btn-create',
                        type='primary',
                        icon=fac.AntdIcon(icon='antd-plus'),
                        size='small',
                    ),
                    fac.AntdButton(
                        t__default('立即刷新'),
                        id='rpa-scheduler-btn-refresh',
                        icon=fac.AntdIcon(icon='antd-sync'),
                        size='small',
                    ),
                    fac.AntdButton(
                        t__default('自动重跑'),
                        id='rpa-scheduler-btn-auto-retry',
                        type='primary',
                        ghost=True,
                        icon=fac.AntdIcon(icon='antd-robot'),
                        size='small',
                    ),
                    fac.AntdSpace([
                        fac.AntdText(t__default('状态总览'), style={'fontSize': 12}),
                        fac.AntdSwitch(id=STATUS_TOGGLE_ID, checked=False, size='small')
                    ], size='small')
                ],
                wrap=True,
                size='small',
                className='rpa-scheduler-action-group',
            ),
        ],
        className='rpa-scheduler-toolbar',
        style={
            'display': 'flex',
            'flexWrap': 'wrap',
            'gap': '12px',
            'alignItems': 'center',
            'justifyContent': 'space-between',
        },
    )
    return html.Div(
        [
            fac.AntdPageHeader(title=t__access('RPA调度中心'), ghost=False),
            dcc.Interval(id=REFRESH_INTERVAL_ID, interval=15 * 1000, n_intervals=0),
            dcc.Store(id=TABLE_STORE_ID),
            dcc.Store(id=DETAIL_STORE_ID),
            fac.AntdAlert(id='rpa-load-error', message=t__default('任务加载失败'), type='error', showIcon=True, style={'display': 'none'}),
            fac.AntdCard(
                toolbar,
                size='small',
                style={'marginBottom': 12},
                styles={'body': {'padding': '8px'}},
            ),
            # 狀態總覽支援顯示/隱藏（預設隱藏以釋放垂直空間）
            html.Div(
                fac.AntdCard(
                    fac.AntdRow(id=STATUS_CHART_ID, gutter=4, wrap=True, justify='start'),
                    size='small',
                    styles={'body': {'padding': '2px'}},
                ),
                id=STATUS_WRAP_ID,
                style={'display': 'none', 'marginBottom': 4},
            ),
            fac.AntdCard(
                [
                    fac.AntdAlert(
                        id='rpa-manual-run-result',
                        message='',
                        description='',
                        type='success',
                        showIcon=True,
                        style={'display': 'none', 'marginBottom': 12},
                    ),
                    fac.AntdAlert(
                        id='rpa-auto-retry-result',
                        message='',
                        description='',
                        type='success',
                        showIcon=True,
                        style={'display': 'none', 'marginBottom': 12},
                    ),
                    fac.AntdTabs(
                        id=VIEW_TABS_ID,
                        type='card',
                        items=[
                            {
                                'key': 'gantt',
                                'label': t__default('甘特图'),
                                'children': html.Div(
                                    id='rpa-scheduler-gantt-container',
                                    className='rpa-scheduler-gantt-container',
                                    style={'minHeight': 'calc(100vh - 260px)', 'overflow': 'auto'},
                                ),
                            },
                            {
                                'key': 'table',
                                'label': t__default('任务列表'),
                                'children': fac.AntdTable(
                                    id=TABLE_ID,
                                    columns=_build_table_columns(),  # type: ignore
                                    data=[],
                                    size='small',
                                    pagination={'pageSize': 12, 'showSizeChanger': False},
                                    maxHeight=460,
                                    tableLayout='fixed',
                                ),
                            },
                        ],
                    ),
                ],
                size='small',
                title=t__default('任务视图'),
                extra=legend_extra,
                styles={
                    'header': {'padding': '0 12px', 'alignItems': 'flex-start'},
                    'body': {
                        'padding': '8px',
                        # 避免固定高度導致內容塌陷，改用最小高度
                        'minHeight': 'calc(100vh - 240px)'
                    }
                },
            ),
            fac.AntdModal(
                id='rpa-scheduler-create-modal',
                title=t__default('新建任务'),
                visible=False,
                maskClosable=False,
                okText=t__default('保存'),
                cancelText=t__default('取消'),
                children=[
                    fac.AntdAlert(
                        id='rpa-create-error',
                        message='',
                        type='error',
                        showIcon=True,
                        style={'display': 'none', 'marginBottom': 8},
                    ),
                    fac.AntdForm(
                        [
                            fac.AntdFormItem(
                                fac.AntdInput(
                                    id='rpa-create-job-id',
                                    placeholder=t__default('job_id（可留空自动生成）'),
                                ),
                                label='Job ID',
                            ),
                            fac.AntdFormItem(
                                fac.AntdSelect(
                                    id='rpa-create-trigger',
                                    options=['interval', 'cron', 'date'],
                                    value='interval',
                                ),
                                label=t__default('触发器'),
                            ),
                            fac.AntdFormItem(
                                fac.AntdSegmented(
                                    id='rpa-create-script-type',
                                    options=['python', 'shell'],
                                    value='python',
                                ),
                                label=t__default('脚本类型'),
                            ),
                            fac.AntdFormItem(
                                fac.AntdSegmented(
                                    id='rpa-create-run-type',
                                    options=['local', 'ssh'],
                                    value='local',
                                ),
                                label='run_type',
                            ),
                            fac.AntdFormItem(
                                fac.AntdInput(
                                    id='rpa-create-script-text',
                                    mode='text-area',
                                    autoSize={'minRows': 6},
                                    placeholder=t__default('在此输入脚本内容'),
                                ),
                                label=t__default('脚本内容'),
                            ),
                            html.Div(
                                [
                                    fac.AntdFormItem(
                                        fac.AntdInput(
                                            id='rpa-create-ssh-host',
                                            placeholder='host',
                                        ),
                                        label='SSH Host',
                                    ),
                                    fac.AntdFormItem(
                                        fac.AntdInputNumber(
                                            id='rpa-create-ssh-port',
                                            value=22,
                                            min=1,
                                            max=65535,
                                        ),
                                        label='SSH Port',
                                    ),
                                    fac.AntdFormItem(
                                        fac.AntdInput(
                                            id='rpa-create-ssh-username',
                                            placeholder='username',
                                        ),
                                        label='SSH User',
                                    ),
                                    fac.AntdFormItem(
                                        fac.AntdInput(
                                            id='rpa-create-ssh-password',
                                            mode='password',
                                            placeholder='password',
                                        ),
                                        label='SSH Password',
                                    ),
                                ],
                                id='rpa-create-ssh-fields',
                                style={'display': 'none'},
                            ),
                            fac.AntdFormItem(
                                fac.AntdInputNumber(
                                    id='rpa-create-interval-seconds',
                                    min=5,
                                    value=60,
                                ),
                                label='interval seconds',
                                help='仅 interval 使用',
                            ),
                            fac.AntdFormItem(
                                fac.AntdInput(
                                    id='rpa-create-cron-expr',
                                    placeholder='sec min hour day month dow year week',
                                ),
                                label='cron expr',
                                help='以空格分隔；仅 cron 使用',
                            ),
                            fac.AntdFormItem(
                                fac.AntdInputNumber(
                                    id='rpa-create-timeout',
                                    min=5,
                                    value=300,
                                ),
                                label=t__default('超时秒数'),
                            ),
                            fac.AntdFormItem(
                                fac.AntdCheckboxGroup(
                                    id='rpa-create-notify-channels',
                                    options=[
                                        {'label': 'server酱', 'value': 'server_jiang'},
                                        {'label': '企业微信', 'value': 'wechat'},
                                    ],
                                ),
                                label=t__default('通知渠道'),
                            ),
                            fac.AntdFormItem(
                                fac.AntdInput(
                                    id='rpa-create-extract-names',
                                    placeholder=t__default('变量抽取名称，逗号分隔'),
                                ),
                                label='extract_names',
                            ),
                            fac.AntdFormItem(
                                fac.AntdInput(
                                    id='rpa-create-env-vars',
                                    mode='text-area',
                                    autoSize={'minRows': 2},
                                    placeholder='{"KEY":"VALUE"} JSON',
                                ),
                                label='env_vars',
                            ),
                        ],
                        layout='vertical',
                    ),
                ],
            ),
        ],
        style={'padding': 16},
        className='rpa-scheduler-root',
    )

@callback(
    Output('rpa-scheduler-gantt-container', 'children'),
    [Input(REFRESH_INTERVAL_ID, 'n_intervals'),
     Input(VIEW_RANGE_ID, 'value'),
     Input(BASE_DATE_ID, 'value'),
     Input(STATUS_FILTER_ID, 'value'),
     Input(SEARCH_ID, 'value')]
)
def update_gantt(n_intervals, view_range, base_date, status_filter, keyword):
    if not get_menu_access().has_access('rpa_scheduler'):
        return no_update
    try:
        # 小工具：安全解析各式時間字串
        def _parse_dt(val: Any) -> Any:
            if isinstance(val, dt.datetime):
                return val
            if not val:
                return None
            s = str(val).strip()
            # 忽略占位符
            if s in ('-', 'None', 'null', 'NULL'):
                return None
            try:
                # 常見 ISO 格式
                return dt.datetime.fromisoformat(s.replace('Z', '').replace('T', ' '))
            except Exception:
                # 常見格式回退
                for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%Y/%m/%d %H:%M:%S'):
                    try:
                        return dt.datetime.strptime(s, fmt)
                    except Exception:
                        continue
            return None

        # 視窗日期計算
        if base_date:
            try:
                base_date_obj = dt.datetime.strptime(base_date, '%Y-%m-%d').date()
            except Exception:
                base_date_obj = dt.date.today()
        else:
            base_date_obj = dt.date.today()

        if (view_range or 'week') == 'day':
            start_window = dt.datetime.combine(base_date_obj, dt.time.min)
            end_window = dt.datetime.combine(base_date_obj, dt.time.max)
        elif view_range == 'week':
            monday = base_date_obj - dt.timedelta(days=base_date_obj.weekday())
            start_window = dt.datetime.combine(monday, dt.time.min)
            end_window = start_window + dt.timedelta(days=7, seconds=-1)
        else:  # month
            first = base_date_obj.replace(day=1)
            if first.month == 12:
                next_first = first.replace(year=first.year + 1, month=1, day=1)
            else:
                next_first = first.replace(month=first.month + 1, day=1)
            start_window = dt.datetime.combine(first, dt.time.min)
            end_window = dt.datetime.combine(next_first, dt.time.min) - dt.timedelta(seconds=1)

        # 讀取提取值資料，作為甘特圖的「一次執行片段」
        extracts = select_extract_values_between(start_window, end_window)
        # 讀取作業基本資料，用於補全 owner/type/status
        jobs = {j.job_id: j for j in get_apscheduler_all_jobs()}

        tasks = []
        represented_jobs = set()  # 已出現於視窗內的 job_id（避免重複補片段）
        for rec in extracts:
            job_id_val = rec.get('job_id')
            job_id = str(job_id_val) if job_id_val is not None else ''
            job = jobs.get(job_id)
            name = job_id or '-'
            status = 'enabled' if (getattr(job, 'status', True) if job else True) else 'disabled'
            # 使用觸發器類型作為甘特圖顏色分類（interval/cron/date/listen），避免使用 run_type（local/ssh）導致樣式缺失
            job_type = getattr(job, 'trigger', 'interval') if job else 'interval'
            owner = (getattr(job, 'create_by', '') or getattr(job, 'update_by', '')) if job else ''
            machine = job_id or ''  # 無專門欄位，暫以 job_id 代替
            start_dt = rec.get('start_datetime')
            if not isinstance(start_dt, dt.datetime):
                # 忽略無效資料
                continue
            # 以提取完成時間或 +1h 作為片段結束
            finish_dt = rec.get('finish_datetime')
            end_dt = finish_dt if isinstance(finish_dt, dt.datetime) else (start_dt + dt.timedelta(hours=1))
            duration = max(1, int((end_dt - start_dt).total_seconds() // 60))
            tasks.append({
                'name': name,
                'start': start_dt.strftime('%Y-%m-%d %H:%M:%S'),
                'end': end_dt.strftime('%Y-%m-%d %H:%M:%S'),
                'job_type': job_type,
                'status': status,
                'owner': owner,
                'machine': machine,
                'duration': duration,
            })
            if job_id:
                represented_jobs.add(job_id)

        # 2) 以最近結果作為後援，補充沒有提取值且時間落在視窗內的執行片段
        try:
            latest_results = select_latest_results(limit=1000)
        except Exception:
            latest_results = []
        for row in latest_results:
            job_id = str(row.get('job_id') or '')
            if not job_id:
                continue
            if job_id in represented_jobs:
                continue
            start_dt = row.get('start_datetime')
            finish_dt = row.get('finish_datetime')
            if not isinstance(start_dt, dt.datetime):
                continue
            end_dt = finish_dt if isinstance(finish_dt, dt.datetime) else (start_dt + dt.timedelta(hours=1))
            if end_dt < start_window or start_dt > end_window:
                continue
            job = jobs.get(job_id)
            status = 'enabled' if (getattr(job, 'status', True) if job else True) else 'disabled'
            job_type = getattr(job, 'trigger', 'interval') if job else 'interval'
            owner = (getattr(job, 'create_by', '') or getattr(job, 'update_by', '')) if job else ''
            machine = job_id
            duration = max(1, int((end_dt - start_dt).total_seconds() // 60))
            tasks.append({
                'name': job_id,
                'start': start_dt.strftime('%Y-%m-%d %H:%M:%S'),
                'end': end_dt.strftime('%Y-%m-%d %H:%M:%S'),
                'job_type': job_type,
                'status': status,
                'owner': owner,
                'machine': machine,
                'duration': duration,
            })
            represented_jobs.add(job_id)

        # 3) 對仍未出現的 job，使用「下一次執行時間」或類型特性補一個視覺片段，確保「所有任務可見」
        PLANNED_MINUTES = 30
        for job_id, job in jobs.items():
            if job_id in represented_jobs:
                continue
            status = 'enabled' if getattr(job, 'status', True) else 'disabled'
            job_type = getattr(job, 'trigger', 'interval')
            owner = getattr(job, 'create_by', '') or getattr(job, 'update_by', '')
            machine = job_id
            # listen 類型：視為全天可用，為每個日視窗加一段長條
            if job_type == 'listen':
                cur = start_window.date()
                end_date = end_window.date()
                while cur <= end_date:
                    day_start = dt.datetime.combine(cur, dt.time.min)
                    day_end = dt.datetime.combine(cur, dt.time.max)
                    tasks.append({
                        'name': job_id,
                        'start': day_start.strftime('%Y-%m-%d %H:%M:%S'),
                        'end': day_end.strftime('%Y-%m-%d %H:%M:%S'),
                        'job_type': job_type,
                        'status': status,
                        'owner': owner,
                        'machine': machine,
                        'duration': 24 * 60,
                    })
                    cur += dt.timedelta(days=1)
                represented_jobs.add(job_id)
                continue

            # 其他類型：用 next_run_time 在視窗內補一段預估片段
            nrt = _parse_dt(getattr(job, 'job_next_run_time', None))
            if nrt and (start_window <= nrt <= end_window):
                end_dt = nrt + dt.timedelta(minutes=PLANNED_MINUTES)
                tasks.append({
                    'name': job_id,
                    'start': nrt.strftime('%Y-%m-%d %H:%M:%S'),
                    'end': end_dt.strftime('%Y-%m-%d %H:%M:%S'),
                    'job_type': job_type,
                    'status': status,
                    'owner': owner,
                    'machine': machine,
                    'duration': PLANNED_MINUTES,
                })
                represented_jobs.add(job_id)

        # 狀態過濾（若指定）
        if status_filter in ('enabled', 'disabled'):
            tasks = [t for t in tasks if t['status'] == status_filter]

        # 關鍵字過濾（job_id/name/machine）
        if keyword:
            kw = str(keyword).lower()
            tasks = [t for t in tasks if kw in str(t['name']).lower() or kw in str(t['machine']).lower()]

        return create_scheduler(tasks, view_range=view_range or 'week', base_date=base_date_obj)
    except Exception as e:
        logger.error(f'甘特圖更新失敗: {e}')
        # 出錯時回傳空的甘特圖，以避免整塊不顯示
        safe_base = dt.date.today()
        return create_scheduler([], view_range=view_range or 'week', base_date=safe_base)


@callback(
    Output(TABLE_ID, 'data'),
    Input(TABLE_STORE_ID, 'data'),
    prevent_initial_call=False,
)
def sync_table_data(store_data):
    """同步任務列表數據至表格顯示。"""
    if not store_data:
        return []
    # 注入穩定的 key 欄位，優先使用 job_id
    result = []
    for idx, row in enumerate(store_data):
        try:
            # 避免直接修改原物件，淺拷貝一份
            r = dict(row)
            r['key'] = r.get('job_id') or f"row_{idx}"
            result.append(r)
        except Exception:
            # 兜底：若某行不是字典，包裝為字典
            result.append({'key': f"row_{idx}", 'value': row})
    return result


def render_content(menu_access, **kwargs):
    """供菜单系统调用的统一入口"""
    return layout(menu_access=menu_access)


@callback(
    Output('main-dcc-url', 'pathname', allow_duplicate=True),
    Input('rpa-scheduler-btn-create', 'nClicks'),
    prevent_initial_call=True,
)
def goto_task_mgmt(n):
    # 將「新建任務」行為改為導航至 任務管理 頁面，以進行任務新增與調整
    if n:
        return '/task_/task_mgmt'
    return no_update


@callback(
    Output(STATUS_CHART_ID, 'children'),
    Input(REFRESH_INTERVAL_ID, 'n_intervals'),
    prevent_initial_call=False,
)
def render_status_charts(_):
    """將每個狀態以小型環形圖單排顯示，節省垂直空間。"""
    if not get_menu_access().has_access('rpa_scheduler'):
        return no_update
    try:
        # 1) 任務啟停
        jobs = get_apscheduler_all_jobs()
        enabled = sum(1 for j in jobs if getattr(j, 'status', True))
        disabled = sum(1 for j in jobs if not getattr(j, 'status', True))

        # 2) 近期結果狀態（success/failed/running/pending）
        raw = []
        try:
            raw = select_latest_results(limit=300)
        except Exception:
            raw = []

        def _norm(s: Any) -> str:
            s = (str(s or '')).lower()
            if s in ('success', 'ok', 'done', 'pass'):
                return 'success'
            if s in ('error', 'fail', 'failed', 'timeout'):
                return 'failed'
            if s in ('running', 'in_progress', 'processing'):
                return 'running'
            if s in ('pending', 'queued', 'waiting', ''):
                return 'pending'
            return 'pending'

        dist_keys = ['success', 'failed', 'running', 'pending']
        dist_counts = {k: 0 for k in dist_keys}
        for r in raw:
            dist_counts[_norm(r.get('status'))] += 1

        # 小環形圖產生器
        def ring(title: str, percent: float, color: str, subtitle: str = ''):
            return fac.AntdCard(
                fac.AntdSpace(
                    [
                        fac.AntdProgress(
                            percent=round(max(0.0, min(1.0, percent)) * 100, 1),
                            type='circle',
                            size=50,
                            strokeColor=color,
                            showInfo=False,
                            strokeWidth=12,
                        ),
                        fac.AntdStatistic(
                            title=title,
                            value=subtitle,
                            valueStyle={'fontSize': '16px', 'fontWeight': 'bold', 'color': color},
                            style={'fontSize': '12px', 'marginTop': '4px'}
                        )
                    ],
                    direction='vertical',
                    align='center',
                    size='small',
                    style={'width': '100%'}
                ),
                size='small',
                style={'width': 140},
                styles={'body': {'padding': '12px 8px'}}
            )

        total_jobs = max(1, enabled + disabled)
        total_results = max(1, sum(dist_counts.values()))

        items = [
            fac.AntdCol(
                ring(t__default('启用'), enabled / total_jobs, STATUS_COLORS['enabled'], str(enabled)),
                xs=8, sm=6, md=4, lg=4, xl=4, span=4,
            ),
            fac.AntdCol(
                ring(t__default('禁用'), disabled / total_jobs, STATUS_COLORS['disabled'], str(disabled)),
                xs=8, sm=6, md=4, lg=4, xl=4, span=4,
            ),
            fac.AntdCol(
                ring(t__default('成功'), dist_counts['success'] / total_results, STATUS_COLORS['success'], str(dist_counts['success'])),
                xs=8, sm=6, md=4, lg=4, xl=4, span=4,
            ),
            fac.AntdCol(
                ring(t__default('失败'), dist_counts['failed'] / total_results, STATUS_COLORS['failed'], str(dist_counts['failed'])),
                xs=8, sm=6, md=4, lg=4, xl=4, span=4,
            ),
            fac.AntdCol(
                ring(t__default('运行中'), dist_counts['running'] / total_results, STATUS_COLORS['running'], str(dist_counts['running'])),
                xs=8, sm=6, md=4, lg=4, xl=4, span=4,
            ),
            fac.AntdCol(
                ring(t__default('待处理'), dist_counts['pending'] / total_results, STATUS_COLORS['pending'], str(dist_counts['pending'])),
                xs=8, sm=6, md=4, lg=4, xl=4, span=4,
            ),
        ]
        return items
    except Exception as e:
        logger.warning(f'狀態圖表渲染失敗: {e}')
        return []


@callback(
    Output(STATUS_WRAP_ID, 'style'),
    Input(STATUS_TOGGLE_ID, 'checked'),
    prevent_initial_call=False,
)
def toggle_status_block(checked):
    """顯示/隱藏狀態總覽區塊。預設隱藏以最大化甘特圖空間。"""
    try:
        if checked:
            return {'display': 'block', 'marginBottom': 4}
        return {'display': 'none', 'marginBottom': 4}
    except Exception:
        return {'display': 'none', 'marginBottom': 4}


@callback(
    Output(LEGEND_EXTRA_ID, 'children'),
    [Input(VIEW_RANGE_ID, 'value'), Input(BASE_DATE_ID, 'value')],
    prevent_initial_call=False,
)
def render_legend_row(view_range, base_date):
    """單排圖例：任務類型 + 任務狀態 + 顯示比例/範圍提示。"""
    try:
        # 計算視窗範圍與比例提示
        if base_date:
            try:
                base_date_obj = dt.datetime.strptime(base_date, '%Y-%m-%d').date()
            except Exception:
                base_date_obj = dt.date.today()
        else:
            base_date_obj = dt.date.today()

        vr = (view_range or 'week')
        if vr == 'day':
            start_window = dt.datetime.combine(base_date_obj, dt.time.min)
            end_window = dt.datetime.combine(base_date_obj, dt.time.max)
            vr_label = t__default('日视图')
        elif vr == 'week':
            monday = base_date_obj - dt.timedelta(days=base_date_obj.weekday())
            start_window = dt.datetime.combine(monday, dt.time.min)
            end_window = start_window + dt.timedelta(days=7, seconds=-1)
            vr_label = t__default('周视图')
        else:
            first = base_date_obj.replace(day=1)
            if first.month == 12:
                next_first = first.replace(year=first.year + 1, month=1, day=1)
            else:
                next_first = first.replace(month=first.month + 1, day=1)
            start_window = dt.datetime.combine(first, dt.time.min)
            end_window = dt.datetime.combine(next_first, dt.time.min) - dt.timedelta(seconds=1)
            vr_label = t__default('月视图')

        delta = end_window - start_window
        total_hours = int(delta.total_seconds() // 3600)
        total_days = max(1, int(round(delta.total_seconds() / 86400)))
        range_text = f"{start_window.strftime('%Y-%m-%d')} ~ {end_window.strftime('%Y-%m-%d')}"
        scale_text = f"{t__default('视图')}: {vr_label} · {t__default('范围')}: {range_text} · {t__default('时长')}: {total_days}d/{total_hours}h"

        # 單排圖例標籤 - 優化為 Badge 樣式
        job_type_badges = [
            fac.AntdBadge(text=label, color=color, style={'marginRight': 8})
            for label, color in JOB_TYPE_COLORS.items()
        ]
        status_badges = [
            fac.AntdBadge(text=label, color=color, style={'marginRight': 8})
            for label, color in STATUS_COLORS.items()
        ]

        children = [
            # 任務類型組
            fac.AntdSpace(
                [
                    fac.AntdText(t__default('任务类型'), type='secondary', style={'fontSize': 12}),
                    *job_type_badges
                ],
                size=4,
                wrap=False,
                align='center'
            ),
            
            fac.AntdDivider(direction='vertical', style={'height': '1.2em', 'margin': '0 8px'}),
            
            # 任務狀態組
            fac.AntdSpace(
                [
                    fac.AntdText(t__default('任务状态'), type='secondary', style={'fontSize': 12}),
                    *status_badges
                ],
                size=4,
                wrap=False,
                align='center'
            ),

            fac.AntdDivider(direction='vertical', style={'height': '1.2em', 'margin': '0 8px'}),

            # 視圖資訊
            fac.AntdText(
                scale_text, 
                type='secondary',
                style={'fontSize': 12}
            ),
        ]
        return children
    except Exception as e:
        logger.warning(f'圖例渲染失敗: {e}')
        return []


@callback(
    Output('rpa-auto-retry-result', 'message', allow_duplicate=True),
    Output('rpa-auto-retry-result', 'description', allow_duplicate=True),
    Output('rpa-auto-retry-result', 'type', allow_duplicate=True),
    Output('rpa-auto-retry-result', 'style', allow_duplicate=True),
    Input('rpa-scheduler-btn-auto-retry', 'nClicks'),
    prevent_initial_call=True,
)
def auto_retry(n):
    if not n:
        return (no_update,) * 4
    try:
        if not get_menu_access().has_access('任务管理-页面'):
            raise PermissionError('没有任务管理权限')
        failed_status = {'error', 'timeout'}
        latest = select_latest_results(limit=500)
        jobs = {j.job_id: j for j in get_apscheduler_all_jobs()}
        now = dt.datetime.now()
        scheduled = 0
        details = []
        for row in latest:
            if row['status'] not in failed_status:
                continue
            job = jobs.get(row['job_id'])
            if not job:
                continue
            # 退避策略：距今 <10m -> +5m；<60m -> +15m；其他 -> +60m
            diff_min = 0
            try:
                if row['finish_datetime']:
                    diff_min = max(0, int((now - row['finish_datetime']).total_seconds() // 60))
            except Exception:
                diff_min = 0
            if diff_min < 10:
                delta = dt.timedelta(minutes=5)
            elif diff_min < 60:
                delta = dt.timedelta(minutes=15)
            else:
                delta = dt.timedelta(hours=1)
            run_at = now + delta
            temp_job_id = f"retry_{job.job_id}_{run_at.strftime('%H%M%S')}"
            try:
                if job.type == 'local':
                    add_local_date_job(
                        script_text=job.script_text,
                        script_type=job.script_type,
                        timeout=job.timeout,
                        job_id=temp_job_id,
                        extract_names=job.extract_names,
                        notify_channels=job.notify_channels,
                        env_vars=None,
                        run_date=run_at,
                    )
                    scheduled += 1
                    details.append(f"{job.job_id} → {run_at.strftime('%Y-%m-%d %H:%M:%S')}")
                else:
                    # 目前不對 ssh 類型自動重跑（避免憑證處理風險）
                    continue
            except Exception:
                continue
        if scheduled == 0:
            return (
                '沒有可重跑的任務',
                '',
                'info',
                {'display': 'block', 'marginBottom': 12},
            )
        return (
            f"已安排 {scheduled} 個任務臨時重跑",
            '；'.join(details),
            'success',
            {'display': 'block', 'marginBottom': 12},
        )
    except Exception as e:
        return (
            '自動重跑失敗',
            str(e),
            'error',
            {'display': 'block', 'marginBottom': 12},
        )
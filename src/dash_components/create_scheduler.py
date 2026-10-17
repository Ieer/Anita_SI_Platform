# src/dash_components/create_scheduler.py
"""
任务调度甘特图组件
原始 Enterprise RPA Empowerment System 页面风格的任务调度甘特图组件。
直接读取app.db相关任务信息，转化为甘特图数据。
使用 CSS Grid 和绝对定位来实现更接近原始页面的效果。
"""

from dash import html
import datetime as dt
from dash.development.base_component import Component
from typing import List, Dict, Any, Optional, TypedDict, Literal

# 統一的日期時間格式
DATETIME_FMT = '%Y-%m-%d %H:%M:%S'


class SchedulerTask(TypedDict, total=False):
    """甘特圖任務資料結構（前端渲染所需）"""

    key: str
    name: str
    owner: str
    machine: str
    status: str
    job_type: str
    start: str
    end: str
    duration: int
    display: str

def fetch_tasks_from_db(
    view_range: Literal['day', 'week', 'month'] = 'day',
    base_date: Optional[dt.date] = None,
    limit_latest_results: int = 1000,
) -> List[SchedulerTask]:
    """從資料庫聚合生成甘特圖所需任務清單。

    資料來源優先順序：
    1) sys_apscheduler_extract_value（以 start~finish 畫執行片段）
    2) sys_apscheduler_results（若視窗中沒有抽取值紀錄，補上一段）

    備註：owner/machine/job_type 資訊若資料庫無法提供，將以 '-' 或 'cron' 作為缺省值。
    """
    today = dt.date.today() if base_date is None else base_date

    # 計算視窗（與 create_scheduler 保持一致）
    if view_range == 'day':
        start_date = today
        days = 1
    elif view_range == 'week':
        monday = today - dt.timedelta(days=today.weekday())
        start_date = monday
        days = 7
    else:  # month
        start_date = today.replace(day=1)
        if start_date.month == 12:
            next_month = dt.date(start_date.year + 1, 1, 1)
        else:
            next_month = dt.date(start_date.year, start_date.month + 1, 1)
        days = (next_month - start_date).days

    window_start = dt.datetime.combine(start_date, dt.time.min)
    window_end = dt.datetime.combine(start_date + dt.timedelta(days=days - 1), dt.time.max)

    tasks: List[SchedulerTask] = []

    # 延遲導入，避免在無資料庫環境下導致匯入失敗
    try:
        from database.sql_db.repositories.apscheduler_repository import (
            select_extract_values_between,
            select_latest_results,
        )
    except Exception:
        # 資料層不可用時回傳空清單（前端會顯示空狀態）
        return tasks

    # 1) 以抽取值表產生執行片段
    try:
        extract_rows = select_extract_values_between(window_start, window_end)
    except Exception:
        extract_rows = []

    seen_keys = set()
    for r in extract_rows:
        job_id = str(r.get('job_id', ''))
        start_dt = r.get('start_datetime')
        finish_dt = r.get('finish_datetime')

        if not isinstance(start_dt, dt.datetime):
            continue
        if not isinstance(finish_dt, dt.datetime):
            # 無 finish 時以 1 小時補齊，避免 0 高度
            finish_dt = start_dt + dt.timedelta(hours=1)

        start_str = start_dt.strftime(DATETIME_FMT)
        end_str = finish_dt.strftime(DATETIME_FMT)
        duration = int((finish_dt - start_dt).total_seconds())

        key = f"{job_id}|{start_str}"
        seen_keys.add(key)

        tasks.append(
            SchedulerTask(
                key=key,
                name=job_id,
                owner='-',
                machine='-',
                status='success',  # 抽取值通常代表一次完整執行
                job_type='cron',   # 缺省類型（資料層未提供）
                start=start_str,
                end=end_str,
                duration=duration,
                display=f"{job_id}",
            )
        )

    # 2) 從最近結果表補片段（若視窗內尚無該 job 的抽取片段）
    try:
        latest_rows = select_latest_results(limit=limit_latest_results)
    except Exception:
        latest_rows = []

    for r in latest_rows:
        job_id = str(r.get('job_id', ''))
        status = str(r.get('status', '') or '').lower() or 'unknown'
        s_dt = r.get('start_datetime')
        f_dt = r.get('finish_datetime')
        if not isinstance(s_dt, dt.datetime) or not isinstance(f_dt, dt.datetime):
            continue

        # 僅補與視窗有交集的片段
        if f_dt < window_start or s_dt > window_end:
            continue

        # 修剪到視窗內
        s_dt_clip = max(s_dt, window_start)
        f_dt_clip = min(f_dt, window_end)
        if f_dt_clip <= s_dt_clip:
            f_dt_clip = s_dt_clip + dt.timedelta(minutes=1)

        start_str = s_dt_clip.strftime(DATETIME_FMT)
        end_str = f_dt_clip.strftime(DATETIME_FMT)
        key = f"{job_id}|{start_str}"

        # 若抽取值已覆蓋同一 job 的此時段，跳過
        if key in seen_keys:
            continue

        duration = int((f_dt_clip - s_dt_clip).total_seconds())

        tasks.append(
            SchedulerTask(
                key=key,
                name=job_id,
                owner='-',
                machine='-',
                status=status,  # e.g., success/failed
                job_type='cron',
                start=start_str,
                end=end_str,
                duration=duration,
                display=f"{job_id}",
            )
        )

    # 3) 計畫片段：若仍未出現，且 next_run_time 落在視窗內，補繪 30 分鐘
    try:
        from common.utilities.util_apscheduler import get_apscheduler_all_jobs  # rpyc RPC 取 APScheduler job 列表
        jobs = get_apscheduler_all_jobs()
    except Exception:
        jobs = []

    present_job_ids = {t.get('name', '') for t in tasks if isinstance(t, dict)}

    def _parse_iso(dt_str: Optional[str]) -> Optional[dt.datetime]:
        if not dt_str or dt_str == '-':
            return None
        try:
            return dt.datetime.strptime(str(dt_str), '%Y-%m-%dT%H:%M:%S')
        except Exception:
            return None

    for job in jobs or []:
        try:
            job_id = str(getattr(job, 'job_id', ''))
            if not job_id:
                continue
            if job_id in present_job_ids:
                # 視窗內已有片段（抽取/結果），不再補計畫
                continue
            nrt = _parse_iso(getattr(job, 'job_next_run_time', None))
            if not isinstance(nrt, dt.datetime):
                continue
            if not (window_start <= nrt <= window_end):
                continue

            s_dt = nrt
            f_dt = min(nrt + dt.timedelta(minutes=30), window_end)
            if f_dt <= s_dt:
                f_dt = s_dt + dt.timedelta(minutes=1)

            start_str = s_dt.strftime(DATETIME_FMT)
            end_str = f_dt.strftime(DATETIME_FMT)
            key = f"{job_id}|{start_str}"
            if key in seen_keys:
                continue

            # 取 trigger/type 來決定 job_type 樣式
            job_type = str(getattr(job, 'trigger', '') or getattr(job, 'type', '') or 'cron').lower()
            duration = int((f_dt - s_dt).total_seconds())

            tasks.append(
                SchedulerTask(
                    key=key,
                    name=job_id,
                    owner='-',
                    machine='-',
                    status='pending',
                    job_type=job_type,
                    start=start_str,
                    end=end_str,
                    duration=duration,
                    display=f"{job_id}",
                )
            )
        except Exception:
            # 單筆 job 解析失敗時略過，避免影響整體渲染
            continue

    return tasks

def create_scheduler(
    tasks: List[Dict[str, Any]], 
    view_range: str = 'day',
    base_date: Optional[dt.date] = None
) -> Component:
    """
    創建符合原始 Enterprise RPA Empowerment System 頁面風格的任務調度甘特圖。
    使用 CSS Grid 和絕對定位來實現更接近原始頁面的效果。
    
    特點:
    1. 顯示橫向日期（日/週/月對應天數）
    2. 縱向24小時，15分鐘刻度
    3. 週末和今日高亮顯示
    4. 任務依據類型顏色區分，按狀態加邊框
    5. 任務提示包含負責人、名稱、類型、機器等信息
    
    Args:
        tasks: 任務列表
        view_range: 'day' | 'week' | 'month'
        base_date: 參考日期，若為 None 則使用今天
    
    Returns:
        Component: HTML 組件
    """
    today = dt.date.today() if base_date is None else base_date
    
    # 計算視窗
    if view_range == 'day':
        start_date = today
        days = 1
    elif view_range == 'week':
        monday = today - dt.timedelta(days=today.weekday())
        start_date = monday
        days = 7
    else:  # month
        start_date = today.replace(day=1)
        if start_date.month == 12:
            next_month = dt.date(start_date.year + 1, 1, 1)
        else:
            next_month = dt.date(start_date.year, start_date.month + 1, 1)
        days = (next_month - start_date).days
    
    # 生成日期列表
    dates = [start_date + dt.timedelta(days=d) for d in range(days)]
    
    # 解析任務
    parsed_tasks = []
    for task in tasks:
        # 安全解析時間
        start_raw = task.get('start') if isinstance(task, dict) else None
        end_raw = task.get('end') if isinstance(task, dict) else None
        try:
            start_time = dt.datetime.strptime(str(start_raw), DATETIME_FMT)
        except Exception:
            # 跳過無效任務
            continue
        try:
            end_time = dt.datetime.strptime(str(end_raw), DATETIME_FMT)
        except Exception:
            end_time = start_time + dt.timedelta(minutes=1)
        
        # 僅處理視窗內的任務
        window_start = dt.datetime.combine(start_date, dt.time.min)
        window_end = dt.datetime.combine(dates[-1], dt.time.max)
        
        if end_time < window_start or start_time > window_end:
            continue
            
        # 修剪超出範圍
        if start_time < window_start:
            start_time = window_start
        if end_time > window_end:
            end_time = window_end
            
        # 拆分跨天任務
        current_date = start_time.date()
        while current_date <= end_time.date():
            day_start = max(start_time, dt.datetime.combine(current_date, dt.time.min))
            day_end = min(end_time, dt.datetime.combine(current_date, dt.time.max))
            
            # 計算任務在這一天的起止時間（分鐘）
            start_minute = (day_start.hour * 60) + day_start.minute
            end_minute = (day_end.hour * 60) + day_end.minute
            
            # 確保結束時間至少比開始晚1分鐘
            if end_minute <= start_minute:
                end_minute = start_minute + 1
                
            date_index = (current_date - start_date).days
            if 0 <= date_index < len(dates):
                parsed_tasks.append({
                    'date_index': date_index,
                    'start_minute': start_minute,
                    'end_minute': end_minute,
                    'task': task
                })
            
            current_date += dt.timedelta(days=1)
    
    # 處理任務重疊（同一日，分配 lane）
    by_date = {}
    for t in parsed_tasks:
        date_idx = t['date_index']
        if date_idx not in by_date:
            by_date[date_idx] = []
        by_date[date_idx].append(t)
    
    # 對每一天的任務進行lane分配
    for date_idx, day_tasks in by_date.items():
        day_tasks.sort(key=lambda x: x['start_minute'])
        lanes = []  # 每個lane記錄當前的結束時間
        
        for t in day_tasks:
            # 查找可用的lane
            lane_found = False
            for i, end_time in enumerate(lanes):
                if end_time <= t['start_minute']:
                    lanes[i] = t['end_minute']
                    t['lane'] = i
                    lane_found = True
                    break
            
            # 如果沒有可用lane，創建新lane
            if not lane_found:
                t['lane'] = len(lanes)
                lanes.append(t['end_minute'])
    
    # 版面常數
    DAY_WIDTH = 160      # 單日網格寬度（可視需要調整）
    HOUR_HEIGHT = 26     # 每小時行高(px)，需與 CSS 保持一致
    HEADER_HEIGHT = 34   # 標頭高度（px），需與 CSS scheduler-header 對齊

    # 構建HTML元素
    hours = list(range(24))
    
    # 建立頭部日期欄（更貼近原始頁面格式）
    header_cells = [html.Div("時間", className="scheduler-time-label")]
    
    # 移除頂部額外的視圖類型覆蓋標籤，避免影響垂直對齊
    
    for date in dates:
        is_weekend = date.weekday() >= 5
        is_today = date == dt.date.today()
        
        # 更詳細的日期格式
        date_format = '%m-%d'
        if view_range == 'day':
            # 日視圖增加詳細時間
            date_str = date.strftime('%Y-%m-%d')
            weekday = ['一', '二', '三', '四', '五', '六', '日'][date.weekday()]
            date_display = f"{date_str} (週{weekday})"
        elif view_range == 'week':
            # 週視圖顯示星期
            weekday = ['一', '二', '三', '四', '五', '六', '日'][date.weekday()]
            date_display = f"{date.strftime(date_format)} (週{weekday})"
        else:
            # 月視圖只顯示日期
            date_display = date.strftime(date_format)
            if date.day == 1:
                date_display = date.strftime('%Y-%m') + '-01'
        
        header_cells.append(html.Div(
            date_display,
            className=f"scheduler-header-cell{' weekend' if is_weekend else ''}{' today' if is_today else ''}"
        ))
    
    # Header 加入固定寬度 style（總寬 = 所有天 * DAY_WIDTH + 左側時間欄 60px）
    # 注意：CSS 中 .scheduler-time-label 和 .scheduler-hour 已改為 60px 且 box-sizing: border-box
    LEFT_LABEL_WIDTH = 60
    total_width_px = LEFT_LABEL_WIDTH + len(dates) * DAY_WIDTH
    header = html.Div(header_cells, className="scheduler-header", style={"width": f"{total_width_px}px"})
    
    # 每小時行
    rows = []
    for hour in hours:
        # 每小時網格
        grid_columns = []
        for date_idx, date in enumerate(dates):
            is_weekend = date.weekday() >= 5
            is_today = date == dt.date.today()
            
            # 優化：使用 CSS 漸變繪製刻度線，減少 DOM 節點
            # 移除內部的 quarter-line div，改用 CSS class 處理
            grid_column = html.Div(
                className=f"scheduler-grid-column{' weekend-column' if is_weekend else ''}{' today-column' if is_today else ''} grid-lines-gradient"
            )
            
            grid_columns.append(grid_column)
        
        # 這一行的時間標籤（統一使用 24 小時制，去除 上午/下午 標識）
        hour_label = f"{hour:02d}:00"

        # 構建一小時行
        row = html.Div([
            html.Div(hour_label, className="scheduler-hour"),
            html.Div(grid_columns, className="scheduler-grid", style={"width": f"{len(dates)*DAY_WIDTH}px"})
        ], className="scheduler-row", style={"width": f"{total_width_px}px", "height": f"{HOUR_HEIGHT}px"})

        rows.append(row)
    
    # 任務塊
    task_elements = []
    for t in parsed_tasks:
        task = t['task']
        job_type = str(task.get('job_type', 'cron')).lower() if isinstance(task, dict) else 'cron'
        status = str(task.get('status', 'unknown')).lower() if isinstance(task, dict) else 'unknown'
        day_idx = t['date_index']
        lane = t.get('lane', 0)
        max_lanes = max(4, max([ti.get('lane', 0) for ti in by_date.get(day_idx, [])]) + 1)
        lane_width_px = DAY_WIDTH / max_lanes
        start_hour = t['start_minute'] / 60.0
        end_hour = t['end_minute'] / 60.0
        left_px = day_idx * DAY_WIDTH + lane * lane_width_px
        width_px = lane_width_px * 0.95
        try:
            start_time_obj = dt.datetime.strptime(str(task.get('start', '')), DATETIME_FMT)
            end_time_obj = dt.datetime.strptime(str(task.get('end', '')), DATETIME_FMT)
        except Exception:
            start_time_obj = dt.datetime.now()
            end_time_obj = start_time_obj + dt.timedelta(minutes=1)
        time_str = f"{start_time_obj.strftime('%H:%M')} ~ {end_time_obj.strftime('%H:%M')}"
        task_duration_minutes = max(1, t['end_minute'] - t['start_minute'])
        display_content = [str(task.get('name', ''))] if task_duration_minutes >= 15 else []
        data_attrs: Dict[str, Any] = {
            'data-task': str(task.get('name', '')),
            'data-start': str(task.get('start', '')),
            'data-end': str(task.get('end', '')),
            'data-owner': str(task.get('owner', '-')),
            'data-status': status,
            'data-type': job_type,
            'data-machine': str(task.get('machine', '-')),
            'data-duration': str(task.get('duration', 0)),
        }

        task_element = html.Div(
            [
                # 內容容器：負責處理文本溢出，避免影響 Tooltip 顯示
                html.Div(display_content, className="task-content"),
                
                # 自定義 Tooltip 結構
                html.Div(
                    [
                        html.Div(str(task.get('name', '')), className="tooltip-title"),
                        html.Div(f"时间: {time_str}", className="tooltip-row"),
                        html.Div(f"状态: {status}", className="tooltip-row"),
                        html.Div(f"负责人: {str(task.get('owner', '-'))}", className="tooltip-row"),
                        html.Div(f"类型: {job_type}", className="tooltip-row"),
                        html.Div(f"机器: {str(task.get('machine', '-'))}", className="tooltip-row"),
                    ],
                    className="scheduler-tooltip"
                )
            ],
            className=f"scheduler-task task-{job_type} status-{status}",
            style={
                "left": f"{LEFT_LABEL_WIDTH + left_px:.2f}px",
                "width": f"{width_px - 2:.2f}px",
                "top": f"{(HEADER_HEIGHT + start_hour * HOUR_HEIGHT):.2f}px",
                "height": f"{((end_hour - start_hour) * HOUR_HEIGHT):.2f}px"
            },
            # 移除原生 title 屬性，避免雙重顯示
            # title=f"{str(task.get('name',''))}\n{time_str}\n狀態: {status}\n負責人: {str(task.get('owner','-'))}\n類型: {job_type}\n機器: {str(task.get('machine','-'))}",
            **data_attrs,
        )
        task_elements.append(task_element)

    # 現在時間垂直指示線（以當前日期在視圖中位置顯示）
    # 若當前日期不在視圖範圍則不顯示
    current_date = dt.date.today()
    if dates and dates[0] <= current_date <= dates[-1]:
        current_index = (current_date - dates[0]).days
        left_px = current_index * DAY_WIDTH + DAY_WIDTH / 2.0
        current_time_indicator = html.Div(
            className="current-time-line",
            style={
                "left": f"{LEFT_LABEL_WIDTH + left_px:.2f}px",
                "top": f"{HEADER_HEIGHT}px",
                "height": f"{24 * HOUR_HEIGHT}px"
            }
        )
        task_elements.append(current_time_indicator)
    
    # 組裝最終組件
    # 外層容器包裹，允許水平滾動
    scheduler_inner = html.Div([header] + rows + task_elements, className="scheduler-inner", style={"width": f"{total_width_px}px"})
    # 空狀態提示：若沒有任何任務塊，顯示輕量提示，避免誤以為渲染失敗
    empty_overlay = None
    if len(parsed_tasks) == 0:
        empty_overlay = html.Div(
            t := "暫無任務數據，請確認時間範圍或任務是否有輸出提取值（extract）",
            className="scheduler-empty",
            style={
                "position": "absolute",
                "left": "60px",
                "top": "48px",
                "right": "12px",
                "padding": "8px 12px",
                "background": "#fffbe6",
                "border": "1px solid #ffe58f",
                "color": "#614700",
                "borderRadius": "6px",
                "zIndex": 30,
            },
            title=t,
        )

    scheduler = html.Div(
        [scheduler_inner] + ([empty_overlay] if empty_overlay else []),
        className="scheduler-container",
        id="scheduler-component"
    )

    return scheduler

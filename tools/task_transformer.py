"""Task Transformer
=================================
提供 Windows Scheduler (schtasks) 原始欄位資料到前端甘特圖資料結構以及APScheduler后台写入数据库
本模組避免直接依賴資料庫層，僅做純轉換。
python -m tools.task_transformer --register-aps
"""
from __future__ import annotations
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import pandas as pd
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import hashlib
import re
from src.database.sql_db.conn import db, create_rds_table
from src.database.sql_db.entity.table_apscheduler import SysApschedulerExtractValue
from src.dash_components.windows_tasks_manager import WindowsTasksManager  # type: ignore
from src.common.utilities.util_apscheduler import add_local_cron_job

class TaskScheduler:
    def __init__(self):
        # 消除 pandas 警告
        pd.options.mode.chained_assignment = None  # default='warn'

        # 初始化 WindowsTasksManager
        self.windows_tasks_manager = WindowsTasksManager()

        # 初始化任务 DataFrame
        self.scheduled_tasks_df = self._load_tasks()

        # 映射任务计划程序返回代码
        self.last_return_code = {
            '0': 'Success',
            "1": "Incorrect function.",
            "267011": "The task is already running.",
            "-1073741510": "The task is not found.",
            "-2147023829": "The task is disabled.",
        }

    def _load_tasks(self):
        """获取任务清单并初始化 DataFrame。"""
        scheduled_tasks = self.windows_tasks_manager.get_scheduled_tasks()
        scheduled_tasks_df = pd.DataFrame(scheduled_tasks)
        return scheduled_tasks_df

    @staticmethod
    def _build_job_id(task_name: str) -> str:
        canonical_name = str(task_name).strip('\\').casefold()
        slug = re.sub(r'[^A-Za-z0-9_-]+', '_', canonical_name).strip('_').lower() or 'task'
        digest = hashlib.sha256(canonical_name.encode('utf-8')).hexdigest()[:12]
        return f'win_{slug[:170]}_{digest}'

    @staticmethod
    def convert_to_24_hour_format(time_str):
        """
        将12小时制时间字符串转换为24小时制。
        """
        try:
            return pd.to_datetime(time_str, format='%I:%M:%S %p').strftime('%H:%M:%S')
        except ValueError:
            return time_str  # 如果格式不匹配，返回原始字符串

    @staticmethod
    def next_run_time(row):
        """
        根据任务 Last Run Time 和 Execution Time 计算下次运行时间。
        """
        try:
            last_run_time = row['Last Run Time']
            schedule_type = row['Schedule Type']
            last_run_dt = datetime.strptime(last_run_time, '%m/%d/%Y %I:%M:%S %p')
            current_dt = datetime.now()
            try:
                last_result = int(row['Last Result'])
            except (TypeError, ValueError):
                last_result = None
            next_run_dt = None  # 默认值

            if schedule_type == 'Daily':
                target_time = last_run_dt.time()
                current_time = current_dt.time()
                if last_result == 0:  # 运行成功
                    next_run_dt = last_run_dt + timedelta(days=1)
                else:
                    next_run_dt = datetime.combine(current_dt.date(), target_time)
                    if current_time >= target_time:
                        next_run_dt += timedelta(days=1)

            elif schedule_type == 'Weekly':
                target_time = last_run_dt.time()
                current_time = current_dt.time()
                if last_result == 0:
                    next_run_dt = last_run_dt + timedelta(weeks=1)
                else:
                    next_run_dt = datetime.combine(current_dt.date(), target_time)
                    if current_time >= target_time:
                        next_run_dt += timedelta(weeks=1)

            elif schedule_type == 'Monthly':
                if last_result == 0:
                    next_run_dt = last_run_dt + relativedelta(months=1)
                else:
                    target_time = last_run_dt.time()
                    current_time = current_dt.time()
                    next_run_dt = datetime.combine(current_dt.date(), target_time)
                    if current_time >= target_time:
                        next_run_dt += relativedelta(months=1)

            else:
                next_run_dt = "N/A"

            return next_run_dt.strftime('%Y/%m/%d %I:%M:%S %p') if isinstance(next_run_dt, datetime) else next_run_dt

        except Exception:
            return "N/A"

    @staticmethod
    def generate_cron_expression(task):
        """
        根据 Schedule Type 和 Start Time 生成 Cron 表达式。
        """
        try:
            if 'Schedule Type' not in task or 'Start Time' not in task:
                raise ValueError("Missing required fields: 'Schedule Type' or 'Start Time'")

            # Daily 处理
            if "Daily" in task['Schedule Type']:
                start_time_parts = task['Start Time'].split(':')
                hour, minute = start_time_parts[:2]
                return f"{minute} {hour} * * *"

            # Weekly 处理
            elif "Weekly" in task['Schedule Type']:
                if 'Days' not in task or not task['Days']:
                    raise ValueError("Missing or invalid 'Days' for Weekly schedule.")
                days_of_week = task['Days'].split(",")
                days_of_week = [d.strip().upper() for d in days_of_week]
                mapping = {'MON': 1, 'TUE': 2, 'WED': 3, 'THU': 4, 'FRI': 5, 'SAT': 6, 'SUN': 0}
                day_of_week = [mapping[d] for d in days_of_week if d in mapping]
                return f"{task['Start Time'].split(':')[1]} {task['Start Time'].split(':')[0]} * * {','.join(map(str, sorted(day_of_week)))}"

            # Monthly 处理
            elif "Monthly" in task['Schedule Type']:
                if 'Months' not in task or 'Days' not in task:
                    raise ValueError("Missing 'Months' or 'Days' fields for Monthly schedule.")
                
                month_mapping = {
                    'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
                    'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12
                }
                months = task['Months'].split(",")
                month_list = {month_mapping[m.strip().upper()] for m in months if m.strip().upper() in month_mapping}
                days = task['Days'].split(",")
                day_list = {int(d.strip()) for d in days if d.strip().isdigit() and 1 <= int(d.strip()) <= 31}
                return f"{task['Start Time'].split(':')[1]} {task['Start Time'].split(':')[0]} {','.join(map(str, sorted(day_list)))} {','.join(map(str, sorted(month_list)))} *"

            else:
                raise ValueError(f"Unknown Schedule Type: {task['Schedule Type']}")

        except Exception as e:
            print(f"Error generating Cron expression: {str(e)}")
            return None

    def process_tasks(self):
        """处理任务，并添加转换字段。"""
        required_columns = {
            'Schedule Type', 'Start Time', 'Start Date', 'End Date', 'Days', 'Months',
            'Last Run Time', 'Last Result', 'TaskName', 'Task To Run', 'Start In',
        }
        missing_columns = sorted(required_columns - set(self.scheduled_tasks_df.columns))
        if missing_columns:
            raise ValueError(f'Windows task data is missing required columns: {", ".join(missing_columns)}')

        # 移除不必要的列
        drop_col = [
            # 'Task To Run',  # 保留以生成 APScheduler 執行腳本
            # 'Start In',     # 保留以切換工作目錄
            'Logon Mode', 'Comment',
            'Idle Time', 'Power Management', 'Run As User', 'Delete Task If Not Rescheduled',
            'Schedule', 'Folder', "Repeat"
        ]
        self.scheduled_tasks_df.drop(columns=drop_col, inplace=True, errors='ignore')
        self.scheduled_tasks_df.rename(columns={'Stop Task If Runs X Hours and X Mins': 'Timeout'}, inplace=True)

        # 过滤 Scheduler 类型
        scheduler_type_filter = ["Daily", "Weekly", "Monthly"]
        supported = self.scheduled_tasks_df['Schedule Type'].isin(scheduler_type_filter)
        if 'Import Supported' in self.scheduled_tasks_df:
            supported &= self.scheduled_tasks_df['Import Supported'].fillna(False).astype(bool)
        self.unsupported_tasks = self.scheduled_tasks_df.loc[~supported].copy()
        self.scheduled_tasks_df = self.scheduled_tasks_df.loc[supported].copy()

        # 数据处理，开始字段转换与计算
        self.scheduled_tasks_df['Start Time'] = self.scheduled_tasks_df['Start Time'].apply(self.convert_to_24_hour_format)
        self.scheduled_tasks_df['End Date'] = self.scheduled_tasks_df['End Date'].replace('N/A', '2099/12/31')
        if 'Execution Time' not in self.scheduled_tasks_df:
            self.scheduled_tasks_df['Execution Time'] = 300
        else:
            self.scheduled_tasks_df['Execution Time'] = self.scheduled_tasks_df['Execution Time'].fillna(300)
        self.scheduled_tasks_df['Next Run Time'] = self.scheduled_tasks_df.apply(self.next_run_time, axis=1)
        self.scheduled_tasks_df['Cron'] = self.scheduled_tasks_df.apply(self.generate_cron_expression, axis=1) # type: ignore

    @staticmethod
    def _map_weekdays_for_aps(days_str: str) -> str:
        """將 MON,TUE 之類的字串轉成 apscheduler 可接受的 day_of_week 表示，如 mon,tue。"""
        if not days_str:
            return '*'
        mapping = {
            'MON': 'mon', 'TUE': 'tue', 'WED': 'wed', 'THU': 'thu', 'FRI': 'fri', 'SAT': 'sat', 'SUN': 'sun'
        }
        parts = [p.strip().upper() for p in str(days_str).split(',') if p.strip()]
        mapped = [mapping[p] for p in parts if p in mapping]
        return ','.join(mapped) if mapped else '*'

    @staticmethod
    def _map_months_for_aps(months_str: str) -> str:
        """將 JAN,FEB -> 1,2 或者直接使用英文簡寫也可。此處用數字，避免語系差異。"""
        if not months_str:
            return '*'
        month_mapping = {
            'JAN': '1', 'FEB': '2', 'MAR': '3', 'APR': '4', 'MAY': '5', 'JUN': '6',
            'JUL': '7', 'AUG': '8', 'SEP': '9', 'OCT': '10', 'NOV': '11', 'DEC': '12'
        }
        parts = [p.strip().upper() for p in str(months_str).split(',') if p.strip()]
        mapped = [month_mapping[p] for p in parts if p in month_mapping]
        return ','.join(mapped) if mapped else '*'

    def generate_cron_list(self, task_row) -> list[str | None]:
        """產生 APScheduler 所需的 cron_list: [second, minute, hour, day, month, day_of_week, year, week]"""
        start_time = str(task_row.get('Start Time', '00:00:00'))
        hh, mm, *_ = start_time.split(':')
        schedule_type = str(task_row.get('Schedule Type', 'Daily'))

        # 預設通配符
        second = '0'
        minute = mm
        hour = hh
        day = '*'
        month = '*'
        day_of_week = '*'
        year = '*'
        week = None

        if 'Weekly' in schedule_type:
            day_of_week = self._map_weekdays_for_aps(task_row.get('Days', ''))
        elif 'Monthly' in schedule_type:
            # Days: "1,15" -> day 欄位；Months: "JAN,MAR" -> month 欄位
            days = str(task_row.get('Days', '')).strip()
            if days:
                day = ','.join([d.strip() for d in days.split(',') if d.strip().isdigit()]) or '*'
            month = self._map_months_for_aps(str(task_row.get('Months', '')))

        return [second, minute, hour, day, month, day_of_week, year, week]

    @staticmethod
    def _build_bat_script(task_to_run: str, start_in: str | None) -> str:
        lines = [
            '@echo off',
        ]
        if start_in and start_in not in ('', 'N/A'):
            lines.append(f'cd /d "{start_in}"')
        # 使用 call 可讓批次腳本繼續並捕捉返回碼
        lines.append(f'call {task_to_run}')
        lines.append('set CODE=%ERRORLEVEL%')
        lines.append('echo ExitCode=%CODE%')
        lines.append('exit /b %CODE%')
        return '\n'.join(lines)

    @staticmethod
    def _parse_timeout_seconds(execution_time: str | int | float | None) -> int:
        """將 "HH:MM:SS" 或 "HH:MM" 轉為秒，預設 300 秒"""
        if not execution_time:
            return 300
        if isinstance(execution_time, (int, float)):
            return max(1, int(execution_time))
        try:
            value = str(execution_time).strip()
            if value.isdigit():
                return max(1, int(value))
            duration_match = re.fullmatch(r'(?:(\d+)\.)?(\d{1,2}):(\d{2}):(\d{2})', value)
            if duration_match:
                days, hours, minutes, seconds = (int(part or 0) for part in duration_match.groups())
                return max(1, days * 86400 + hours * 3600 + minutes * 60 + seconds)
            parts = [int(p) for p in value.split(':')]
            if len(parts) == 3:
                h, m, s = parts
            elif len(parts) == 2:
                h, m = parts
                s = 0
            else:
                return 300
            return h * 3600 + m * 60 + s
        except Exception:
            return 300

    def register_jobs_to_apscheduler(self, is_pause: bool = True):
        """依照轉換結果，直接建立 APScheduler 的 cron 任務。

        注意：需要 app_apscheduler 的 RPC 服務已啟動（util_apscheduler 經由 rpyc 連線）。
        """
        count_ok, count_fail = 0, 0
        for _, row in self.scheduled_tasks_df.iterrows():
            cron_list = self.generate_cron_list(row)

            task_name = str(row.get('TaskName', ''))
            job_id = self._build_job_id(task_name)

            # 組合批次腳本（優先使用 Task To Run 與 Start In）
            task_to_run = row.get('Task To Run') or ''
            start_in = row.get('Start In') or ''
            script_text = self._build_bat_script(task_to_run, start_in)
            script_type = 'Bat'
            timeout = self._parse_timeout_seconds(row.get('Execution Time'))
            start_date = row.get('Start DateTime') or None
            end_date = row.get('End DateTime') or None
            task_is_pause = is_pause or str(row.get('Status', '')).casefold() == 'disabled'

            try:
                add_local_cron_job(
                    script_text=script_text,
                    script_type=script_type,
                    cron_list=cron_list,
                    timeout=timeout,
                    job_id=job_id,
                    update_by='windows-import',
                    update_datetime=datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
                    create_by='windows-import',
                    create_datetime=datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
                    extract_names=None,
                    notify_channels=None,
                    is_pause=task_is_pause,
                    start_date=start_date,
                    end_date=end_date,
                )
                count_ok += 1
            except Exception as e:
                print(f'建立 APScheduler 任務失敗: job_id={job_id}, err={e}')
                count_fail += 1

        print(f'APScheduler 任務建立完成：成功 {count_ok}，失敗 {count_fail}')

    def generate_apscheduler_table(self):
        """
        生成 APScheduler 数据表，返回一个 DataFrame。
        当前只包含周期性任务：日，周，月生成。
        开始日期为空默认为当前日期，结束日期为空默认为2099/12/31。
        """
        apscheduler_data = []

        for idx, task in self.scheduled_tasks_df.iterrows():
            # 處理開始時間
            start_date = task.get('Start Date', datetime.now().strftime('%Y-%m-%d'))
            start_time = task.get('Start Time', '00:00:00')
            try:
                start_dt = pd.to_datetime(f"{start_date} {start_time}")
            except Exception:
                start_dt = pd.to_datetime(datetime.now())
            start_dt_str = start_dt.strftime('%Y-%m-%d %H:%M:%S')

            # 處理結束時間（補齊時間）
            end_date = task.get('End Date', "2099-12-31")
            try:
                end_dt = pd.to_datetime(f"{end_date} {start_time}")
            except Exception:
                end_dt = pd.to_datetime("2099-12-31 23:59:59")
            end_dt_str = end_dt.strftime('%Y-%m-%d %H:%M:%S')

            # Use the same path-based ID as APScheduler registration.
            extract_name_raw = str(task.get('TaskName', "")).split("\\")[-1]
            job_id_stable = self._build_job_id(str(task.get('TaskName', '')))

            apscheduler_data.append({
                'job_id': job_id_stable,                              # 穩定 job_id，避免重複導入
                'extract_name': extract_name_raw,                      # 抽取名稱：TaskName 最後一段
                'value_type': "string",                               # 預設 string
                'value': task.get('Cron', "") or "",                # Cron 表達式
                'start_datetime': start_dt_str,                        # 標準 datetime 格式
                'finish_datetime': end_dt_str                          # 標準 datetime 格式
            })

        # 返回包含表结构的 DataFrame
        return pd.DataFrame(apscheduler_data)
    
    
    def generate_gantt_data(self):
        """
        生成甘特图上传数据表
        """
        gatt_list = []
        for index, row in self.scheduled_tasks_df.iterrows():
            name = row['TaskName']
            machine = row['HostName']
            owner = row['Author']
            job_type = row['Schedule Type']
            
            start_dt = pd.to_datetime(row['Start Date'] + ' ' + row['Start Time'])
            end_dt = pd.to_datetime(row['End Date'] + ' ' + row['Start Time']) + pd.to_timedelta(row['Execution Time'])
            
            duration = row['Execution Time']
            
            # 将状态转换为预定义的状态
            status_mapping = {
                '0': 'success',
                '1': 'failed',
                '267011': 'running',
                '-1073741510': 'pending',
                '-2147023829': 'disabled'
            }
            status = row['Last Result']
            status = status_mapping.get(status, 'unknown')

            row_data = [name, start_dt, end_dt, job_type, status, owner, machine, duration]
            gatt_list.append(row_data)
        
        gant_report = pd.DataFrame(gatt_list, columns=['name', 'start', 'end', 'job_type', 'status', 'owner', 'machine', 'duration'])
        return gant_report


    def get_processed_tasks(self):
        """
        返回处理后的计划任务 DataFrame。
        """
        return self.scheduled_tasks_df
    
    
    # 添加写入数据库：整合 Peewee ORM，避免手寫 SQL，並利用唯一索引做 upsert
    def write_to_database(self):
        """
        解析並將 Windows 任務導入 sys_apscheduler_extract_value。
        使用既有 Peewee 連線與模型，按唯一索引 (extract_name, job_id, start_datetime) upsert。
        """
        # 確保資料表已建立
        create_rds_table()

        # 生成 DataFrame
        apscheduler_df = self.generate_apscheduler_table()

        # 開始導入
        database = db()
        inserted, updated = 0, 0
        with database.atomic():
            for _, row in apscheduler_df.iterrows():
                # 轉換時間字串為 datetime
                try:
                    start_dt = datetime.strptime(row['start_datetime'], '%Y-%m-%d %H:%M:%S')
                except Exception:
                    start_dt = datetime.now()
                try:
                    finish_dt = datetime.strptime(row['finish_datetime'], '%Y-%m-%d %H:%M:%S')
                except Exception:
                    finish_dt = datetime.now()

                obj, created = SysApschedulerExtractValue.get_or_create(
                    job_id=row['job_id'],
                    extract_name=row['extract_name'],
                    start_datetime=start_dt,
                    defaults={
                        'value_type': row['value_type'],
                        'value': row['value'],
                        'finish_datetime': finish_dt,
                    },
                )
                if created:
                    inserted += 1
                else:
                    # 更新最新值（例如 Cron 變動或結束時間調整）
                    obj.value_type = row['value_type']
                    obj.value = row['value']
                    obj.finish_datetime = finish_dt
                    obj.save()
                    updated += 1

        print(f"已導入 sys_apscheduler_extract_value：新增 {inserted} 筆，更新 {updated} 筆。")
    

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Import Windows Scheduler tasks into APScheduler and/or database')
    parser.add_argument('--dry-run', action='store_true', help='只预览；这是默认行为，与写入选项互斥')
    parser.add_argument('--write-db', action='store_true', help='显式写入 sys_apscheduler_extract_value 计划数据')
    parser.add_argument('--no-db', action='store_true', help='兼容旧参数：不写入计划数据')
    parser.add_argument('--register-aps', action='store_true', help='將任務註冊到 APScheduler（需 RPC 服務啟動）')
    activation = parser.add_mutually_exclusive_group()
    activation.add_argument('--activate', action='store_true', help='立即启用任务；确认已处理对应 Windows 原生任务后再使用')
    activation.add_argument('--pause', action='store_true', help='以暂停状态建立 APScheduler 任务（默认）')
    args = parser.parse_args()
    if args.write_db and args.no_db:
        parser.error('--write-db and --no-db cannot be used together')
    if args.dry_run and (args.write_db or args.register_aps or args.activate):
        parser.error('--dry-run cannot be combined with write or registration options')
    if args.activate and not args.register_aps:
        parser.error('--activate requires --register-aps')

    scheduler = TaskScheduler()
    scheduler.process_tasks()  # 处理任务数据
    if not scheduler.unsupported_tasks.empty:
        print(f'跳过 {len(scheduler.unsupported_tasks)} 个不支持的 Windows 任务：')
        for _, task in scheduler.unsupported_tasks.iterrows():
            reason = task.get('Import Issue', task.get('Schedule Type', 'unsupported'))
            print(f"- {task.get('TaskName', '<unknown>')}: {reason}")
    apscheduler_table = scheduler.generate_apscheduler_table()
    print(apscheduler_table)

    preview_only = args.dry_run or not (args.write_db or args.register_aps)
    if preview_only:
        print('预览模式：没有写入数据库，也没有注册 APScheduler 任务。')

    if args.write_db:
        try:
            scheduler.write_to_database()
        except Exception as e:
            print(f"寫入資料庫失敗: {e}")

    if args.register_aps:
        try:
            scheduler.register_jobs_to_apscheduler(is_pause=not args.activate)
        except Exception as e:
            print(f"註冊 APScheduler 失敗（請確認 app_apscheduler RPC 服務是否啟動）: {e}")
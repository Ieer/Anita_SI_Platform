from database.sql_db.conn import db
from peewee import DoesNotExist, IntegrityError
from common.utilities.util_logger import Log
from ..entity.table_apscheduler import SysApschedulerResults, SysApschedulerExtractValue, SysApschedulerRunning
from datetime import datetime, timedelta
from common.notify import send_text_notify
import re
from typing import List, Dict, Optional

logger = Log.get_logger(__name__)


def recover_stale_runs():
    database = db()
    with database.connection_context(), database.atomic():
        from ..entity.table_task_center import TaskRun

        task_runs_available = database.table_exists(TaskRun._meta.table_name)
        grouped_logs = {}
        for row in SysApschedulerRunning.select().order_by(
            SysApschedulerRunning.job_id,
            SysApschedulerRunning.start_datetime,
            SysApschedulerRunning.order,
        ):
            key = (row.job_id, row.start_datetime)
            grouped_logs.setdefault(key, []).append(row.log)
        for (job_id, start_datetime), log_parts in grouped_logs.items():
            recovered_task_run = None
            if task_runs_available:
                recovered_task_run = (TaskRun.select().where(
                    (TaskRun.job_id == job_id)
                    & (TaskRun.started_at >= start_datetime - timedelta(seconds=5))
                    & (TaskRun.started_at <= start_datetime + timedelta(seconds=5))
                ).first())
            if recovered_task_run:
                if recovered_task_run.status in {'requested', 'submitted', 'running'}:
                    from . import task_center_repository

                    task_center_repository.finish_run(
                        recovered_task_run.run_id, 'unknown',
                        'scheduler recovery could not confirm process termination',
                    )
                status = 'unknown'
                note = '\n[UNKNOWN] Scheduler process stopped before termination was confirmed.'
            else:
                status = 'error'
                note = '\n[ERROR] Scheduler process stopped before the task completed.'
            SysApschedulerResults.get_or_create(
                job_id=job_id,
                start_datetime=start_datetime,
                defaults={
                    'status': status,
                    'log': ''.join(log_parts) + note,
                    'finish_datetime': datetime.now(),
                },
            )
        SysApschedulerRunning.delete().execute()


def has_job_result(job_id: str) -> bool:
    database = db()
    with database.connection_context():
        return SysApschedulerResults.select().where(SysApschedulerResults.job_id == job_id).exists()


def get_log_job_ids():
    with db().connection_context():
        completed = SysApschedulerResults.select(SysApschedulerResults.job_id).distinct().tuples()
        running = SysApschedulerRunning.select(SysApschedulerRunning.job_id).distinct().tuples()
        return sorted({row[0] for row in completed} | {row[0] for row in running}, reverse=True)


def get_latest_completed_log(job_id):
    with db().connection_context():
        result = (SysApschedulerResults.select()
                  .where(SysApschedulerResults.job_id == job_id)
                  .order_by(SysApschedulerResults.start_datetime.desc())
                  .first())
        if result is None:
            return None
        return {
            'status': 'ok', 'job_id': result.job_id, 'run_status': result.status,
            'start_datetime': result.start_datetime.isoformat(),
            'finish_datetime': result.finish_datetime.isoformat(), 'log': result.log,
        }


def select_last_log_from_job_id(job_id: str, accept_timedelta: timedelta) -> str:
    """查询指定job_id的最新的日志log"""
    try:
        result = (
            SysApschedulerResults.select(SysApschedulerResults.log)
            .where((SysApschedulerResults.job_id == job_id) & (SysApschedulerResults.finish_datetime > datetime.now() - accept_timedelta))
            .order_by(SysApschedulerResults.finish_datetime.desc())
            .get()
        )
        return result[0]
    except DoesNotExist as e:
        raise Exception('Job log not found') from e


def get_apscheduler_start_finish_datetime_with_status_by_job_id(job_id: str):
    """查询指定job_id的开始时间"""
    try:
        result_running = (
            SysApschedulerRunning.select(SysApschedulerRunning.start_datetime)
            .where(SysApschedulerRunning.job_id == job_id)
            .distinct()
            .order_by(SysApschedulerRunning.start_datetime.desc())
        )
        result_done = (
            SysApschedulerResults.select(SysApschedulerResults.start_datetime, SysApschedulerResults.finish_datetime, SysApschedulerResults.status)
            .where(SysApschedulerResults.job_id == job_id)
            .order_by(SysApschedulerResults.start_datetime.desc())
        )
        start_datetimes_running = [(i.start_datetime, '...', 'running') for i in result_running]
        start_datetimes_done = [(i.start_datetime, i.finish_datetime, i.status) for i in result_done]
        rt = [*start_datetimes_running, *start_datetimes_done]
        rt.sort(key=lambda x: x[0], reverse=True)
        return rt
    except DoesNotExist as e:
        raise Exception('Job start datetime not found') from e


def get_running_log(job_id: str, start_datetime: datetime, order: Optional[int] = None) -> Optional[str]:
    """查询指定job_id的日志log"""
    try:
        return (
            SysApschedulerRunning.select(SysApschedulerRunning.log)
            .where((SysApschedulerRunning.job_id == job_id) & (SysApschedulerRunning.start_datetime == start_datetime) & (SysApschedulerRunning.order == order))
            .dicts()
            .get()['log']
        )
    except DoesNotExist:
        return None
    except Exception as e:
        raise e


def get_done_log(job_id: str, start_datetime: datetime) -> Optional[str]:
    """查询指定job_id的日志log"""
    try:
        return (
            SysApschedulerResults.select(SysApschedulerResults.log)
            .where((SysApschedulerResults.job_id == job_id) & (SysApschedulerResults.start_datetime == start_datetime))
            .dicts()
            .get()['log']
        )
    except DoesNotExist:
        return None
    except Exception as e:
        raise e


def insert_apscheduler_running(job_id, log, order, start_datetime):
    """插入实时日志到数据库"""
    database = db()
    try:
        with database.atomic():
            SysApschedulerRunning.create(job_id=job_id, log=log, order=order, start_datetime=start_datetime)
    except IntegrityError as e:
        logger.error(f'插入实时日志时发生数据库完整性错误: {e}')
        raise Exception('Failed to insert apscheduler running log due to integrity error') from e
    except Exception as e:
        logger.error(f'插入实时日志时发生未知错误: {e}')
        raise Exception('Failed to insert apscheduler running log due to an unknown error') from e


def select_apscheduler_running_log(job_id, start_datetime, order=None):
    """查询指定job_id的实时日志log"""
    try:
        if order is None:
            results = (
                SysApschedulerRunning.select(SysApschedulerRunning.log)
                .where((SysApschedulerRunning.job_id == job_id) & (SysApschedulerRunning.start_datetime == start_datetime))
                .order_by(SysApschedulerRunning.order.asc())
            )
        else:
            results = SysApschedulerRunning.select(SysApschedulerRunning.log).where(
                (SysApschedulerRunning.job_id == job_id) & (SysApschedulerRunning.start_datetime == start_datetime) & (SysApschedulerRunning.order == order)
            )
        result = [result.log for result in results]
        return ''.join(result)
    except DoesNotExist as e:
        raise Exception('Job log not found') from e


def delete_apscheduler_running(job_id, start_datetime):
    """删除指定job_id的实时日志log"""
    database = db()
    try:
        with database.atomic():
            SysApschedulerRunning.delete().where((SysApschedulerRunning.job_id == job_id) & (SysApschedulerRunning.start_datetime == start_datetime)).execute()
    except IntegrityError as e:
        logger.error(f'删除实时日志时发生数据库完整性错误: {e}')
        raise Exception('Failed to delete apscheduler running log due to integrity error') from e
    except Exception as e:
        logger.error(f'删除实时日志时发生未知错误: {e}')
        raise Exception('Failed to delete apscheduler running log due to an unknown error') from e


def truncate_apscheduler_running():
    database = db()
    try:
        with database.atomic():
            SysApschedulerRunning.delete().execute()
    except IntegrityError as e:
        logger.error(f'清空实时日志时发生数据库完整性错误: {e}')


def insert_apscheduler_extract_value(job_id, log, start_datetime, extract_names, notify_channels):
    database = db()
    if not extract_names:
        return
    with database.atomic():
        for extract_name in extract_names:
            type_ = extract_name['type']
            name = extract_name['name']
            re_search = re.search(r'<SOPS_VAR>%s:(.+?)</SOPS_VAR>' % re.escape(name), log, flags=re.DOTALL)
            if re_search:
                value = re_search.group(1)
                if type_ == 'string':
                    try:
                        value = str(value)
                    except Exception as e:
                        logger.warning(f'提取数据类型为string，但无法转换为字符串: {value}; error={e}')
                        continue
                elif type_ == 'number':
                    try:
                        value = float(value)
                    except Exception as e:
                        logger.warning(f'提取数据类型为number，但无法转换为数值: {value}; error={e}')
                        continue
                elif type_ == 'notify':
                    send_text_notify(
                        title=name,
                        short=value,
                        desp=value + f'【The message from Job {job_id}】',
                        notify_channels=notify_channels,
                    )
                else:
                    raise ValueError('不支持的提取数据类型')
                SysApschedulerExtractValue.create(
                    job_id=job_id,
                    extract_name=name,
                    value_type=type_,
                    value=value,
                    start_datetime=start_datetime,
                    finish_datetime=datetime.now(),
                )


def insert_apscheduler_result(job_id, status, log, start_datetime):
    database = db()
    try:
        with database.atomic():
            SysApschedulerResults.create(job_id=job_id, status=status, log=log, start_datetime=start_datetime, finish_datetime=datetime.now())
    except IntegrityError as e:
        logger.error(f'插入任务结果时发生数据库完整性错误: {e}')
        raise Exception('Failed to insert apscheduler result due to integrity error') from e
    except Exception as e:
        logger.error(f'插入任务结果时发生未知错误: {e}')
        raise Exception('Failed to insert apscheduler result due to an unknown error') from e


def delete_expire_data(day):
    # 删除SysApschedulerResults和SysApschedulerExtractValue超时的数据
    try:
        database = db()
        with database.atomic():
            expire_time = datetime.now() - timedelta(days=day)
            SysApschedulerResults.delete().where(SysApschedulerResults.start_datetime < expire_time).execute()
            SysApschedulerExtractValue.delete().where(SysApschedulerExtractValue.start_datetime < expire_time).execute()
    except IntegrityError as e:
        logger.error(f'删除超时数据时发生数据库完整性错误: {e}')
        raise Exception('Failed to delete expired data due to integrity error') from e
    except Exception as e:
        logger.error(f'删除超时数据时发生未知错误: {e}')
        raise Exception('Failed to delete expired data due to an unknown error') from e


def select_extract_values_between(start_datetime: datetime, end_datetime: datetime) -> List[Dict]:
    """查詢在時間窗內的 sys_apscheduler_extract_value 紀錄。

    回傳欄位：job_id, extract_name, value_type, value, start_datetime, finish_datetime
    """
    try:
        results = (
            SysApschedulerExtractValue.select(
                SysApschedulerExtractValue.job_id,
                SysApschedulerExtractValue.extract_name,
                SysApschedulerExtractValue.value_type,
                SysApschedulerExtractValue.value,
                SysApschedulerExtractValue.start_datetime,
                SysApschedulerExtractValue.finish_datetime,
            )
            .where(
                (SysApschedulerExtractValue.start_datetime >= start_datetime)
                & (SysApschedulerExtractValue.start_datetime <= end_datetime)
            )
            .order_by(SysApschedulerExtractValue.start_datetime.asc())
        )
        return [
            {
                'job_id': r.job_id,
                'extract_name': r.extract_name,
                'value_type': r.value_type,
                'value': r.value,
                'start_datetime': r.start_datetime,
                'finish_datetime': r.finish_datetime,
            }
            for r in results
        ]
    except Exception as e:
        logger.error(f'查詢提取值資料失敗: {e}')
        raise


def select_latest_results(limit: int = 1000) -> List[Dict]:
    """取得最近的任務結果（按 finish_datetime 由新到舊），每個 job_id 僅保留最新一筆。

    回傳：[{job_id, status, start_datetime, finish_datetime}]
    """
    try:
        rows = (
            SysApschedulerResults.select(
                SysApschedulerResults.job_id,
                SysApschedulerResults.status,
                SysApschedulerResults.start_datetime,
                SysApschedulerResults.finish_datetime,
            )
            .order_by(SysApschedulerResults.finish_datetime.desc())
            .limit(limit)
        )
        seen = set()
        result: List[Dict] = []
        for r in rows:
            if r.job_id in seen:
                continue
            seen.add(r.job_id)
            result.append(
                {
                    'job_id': r.job_id,
                    'status': r.status,
                    'start_datetime': r.start_datetime,
                    'finish_datetime': r.finish_datetime,
                }
            )
        return result
    except Exception as e:
        logger.error(f'查詢最新任務結果失敗: {e}')
        raise

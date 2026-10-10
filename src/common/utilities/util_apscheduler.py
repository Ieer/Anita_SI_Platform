import json
from datetime import date, datetime
from uuid import uuid4
from dataclasses import dataclass
from typing import Optional, Dict, List, cast
from config.dashgo_conf import ApSchedulerConf
from database.sql_db.dao import dao_listen_task
from common.utilities.util_scheduler_rpc import connect_scheduler_rpc


def get_connect(timeout=5):
    return connect_scheduler_rpc(ApSchedulerConf.HOST, ApSchedulerConf.PORT, timeout=timeout)


def _json_safe(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _decode_json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _listen_run_metadata(job_id, job_kwargs):
    if not job_id.startswith('listen_'):
        return {}
    from database.sql_db.dao import dao_task_center, dao_listen_task

    fields = ('type', 'script_text', 'script_type', 'timeout', 'host', 'port',
              'username', 'password', 'extract_names', 'notify_channels')
    defaults = {'host': '', 'port': 22, 'username': '', 'password': ''}
    submitted = {name: _decode_json(job_kwargs.get(name, defaults.get(name))) for name in fields}
    submitted['extract_names'] = submitted['extract_names'] or None
    submitted['notify_channels'] = submitted['notify_channels'] or None
    matches = []
    for listener in dao_listen_task.get_activa_listen_job():
        if not listener.status:
            continue
        candidate = {
            'type': listener.type,
            'script_text': listener.script_text,
            'script_type': listener.script_type,
            'timeout': listener.timeout,
            'host': listener.host,
            'port': listener.port,
            'username': listener.username,
            'password': listener.password,
            'extract_names': _decode_json(listener.extract_names),
            'notify_channels': _decode_json(listener.notify_channels),
        }
        candidate['extract_names'] = candidate['extract_names'] or None
        candidate['notify_channels'] = candidate['notify_channels'] or None
        if candidate != submitted:
            continue
        task = dao_task_center.find_task('platform', listener.job_id)
        if task:
            matches.append((listener, task))
    if len(matches) != 1:
        return {}
    listener, task = matches[0]
    return {
        'task_id': task.task_id,
        'run_id': uuid4().hex,
        'source': 'listen',
        'source_job_id': listener.job_id,
    }


def _add_job(trigger, trigger_args, job_kwargs, job_id, is_pause=False, replace_existing=False):
    conn = None
    try:
        conn = get_connect()
        safe_job_kwargs = dict(job_kwargs)
        if not any(name in safe_job_kwargs for name in ('task_id', 'run_id', 'source', 'source_job_id')):
            safe_job_kwargs.update(_listen_run_metadata(job_id, safe_job_kwargs))
        return conn.root.add_job(
            trigger,
            _json_safe(trigger_args),
            _json_safe(safe_job_kwargs),
            job_id,
            is_pause,
            replace_existing,
        )
    finally:
        if conn:
            conn.close()


def add_ssh_date_job(
    host,
    port,
    username,
    password,
    script_text,
    script_type,
    timeout,
    job_id,
    extract_names,
    notify_channels,
    env_vars,
    run_date: Optional[object] = None,
    timezone=None,
):
    if not extract_names:
        extract_names = None
    return _add_job(
        'date', {key: value for key, value in {'run_date': run_date, 'timezone': timezone}.items() if value is not None},
        {
            'type': 'ssh', 'script_text': script_text, 'script_type': script_type,
            'timeout': timeout, 'host': host, 'port': port, 'username': username,
            'password': password, 'extract_names': extract_names,
            'notify_channels': notify_channels, 'env_vars': env_vars,
        },
        job_id,
    )


def add_ssh_interval_job(
    host,
    port,
    username,
    password,
    script_text,
    script_type,
    interval,
    timeout,
    job_id,
    update_by,
    update_datetime,
    create_by,
    create_datetime,
    extract_names,
    notify_channels,
    is_pause,
    replace_existing=False,
    start_date=None,
    end_date=None,
    timezone=None,
):
    if not extract_names:
        extract_names = None
    return _add_job(
        'interval', {key: value for key, value in {
            'seconds': interval, 'start_date': start_date, 'end_date': end_date, 'timezone': timezone,
        }.items() if value is not None},
        {
            'type': 'ssh', 'script_text': script_text, 'script_type': script_type,
            'timeout': timeout, 'host': host, 'port': port, 'username': username,
            'password': password, 'extract_names': extract_names,
            'notify_channels': notify_channels, 'update_by': update_by,
            'update_datetime': update_datetime, 'create_by': create_by,
            'create_datetime': create_datetime,
        },
        job_id, is_pause, replace_existing,
    )


def add_ssh_cron_job(
    host,
    port,
    username,
    password,
    script_text,
    script_type,
    cron_list,
    timeout,
    job_id,
    update_by,
    update_datetime,
    create_by,
    create_datetime,
    extract_names,
    notify_channels,
    is_pause,
    replace_existing=False,
    start_date=None,
    end_date=None,
    timezone=None,
):
    """https://apscheduler.readthedocs.io/en/master/api.html#apscheduler.triggers.cron.CronTrigger"""
    if not extract_names:
        extract_names = None
    second, minute, hour, day, month, day_of_week, year, week = cron_list
    return _add_job(
        'cron', {
            'year': year, 'week': week, 'second': second, 'minute': minute,
            'hour': hour, 'day': day, 'month': month, 'day_of_week': day_of_week,
            'start_date': start_date, 'end_date': end_date, 'timezone': timezone,
        },
        {
            'type': 'ssh', 'script_text': script_text, 'script_type': script_type,
            'timeout': timeout, 'host': host, 'port': port, 'username': username,
            'password': password, 'extract_names': extract_names,
            'notify_channels': notify_channels, 'update_by': update_by,
            'update_datetime': update_datetime, 'create_by': create_by,
            'create_datetime': create_datetime,
        },
        job_id, is_pause, replace_existing,
    )


def add_local_date_job(
    script_text,
    script_type,
    timeout,
    job_id,
    extract_names,
    notify_channels,
    env_vars,
    run_date: Optional[object] = None,
    timezone=None,
):
    if not extract_names:
        extract_names = None
    return _add_job(
        'date', {key: value for key, value in {'run_date': run_date, 'timezone': timezone}.items() if value is not None},
        {
            'type': 'local', 'script_text': script_text, 'script_type': script_type,
            'timeout': timeout, 'extract_names': extract_names,
            'notify_channels': notify_channels, 'env_vars': env_vars,
        },
        job_id,
    )


def add_local_interval_job(
    script_text, script_type, interval, timeout, job_id, update_by, update_datetime,
    create_by, create_datetime, extract_names, notify_channels, is_pause,
    replace_existing=False,
    start_date=None, end_date=None, timezone=None,
):
    if not extract_names:
        extract_names = None
    return _add_job(
        'interval', {key: value for key, value in {
            'seconds': interval, 'start_date': start_date, 'end_date': end_date, 'timezone': timezone,
        }.items() if value is not None},
        {
            'type': 'local', 'script_text': script_text, 'script_type': script_type,
            'timeout': timeout, 'extract_names': extract_names,
            'notify_channels': notify_channels, 'update_by': update_by,
            'update_datetime': update_datetime, 'create_by': create_by,
            'create_datetime': create_datetime,
        },
        job_id, is_pause, replace_existing,
    )


def add_local_cron_job(
    script_text,
    script_type,
    cron_list,
    timeout,
    job_id,
    update_by,
    update_datetime,
    create_by,
    create_datetime,
    extract_names,
    notify_channels,
    is_pause,
    start_date=None,
    end_date=None,
    replace_existing=False,
    timezone=None,
):
    if not extract_names:
        extract_names = None
    second, minute, hour, day, month, day_of_week, year, week = cron_list
    return _add_job(
        'cron', {
            'year': year, 'week': week, 'second': second, 'minute': minute,
            'hour': hour, 'day': day, 'month': month, 'day_of_week': day_of_week,
            'start_date': start_date, 'end_date': end_date, 'timezone': timezone,
        },
        {
            'type': 'local', 'script_text': script_text, 'script_type': script_type,
            'timeout': timeout, 'extract_names': extract_names,
            'notify_channels': notify_channels, 'update_by': update_by,
            'update_datetime': update_datetime, 'create_by': create_by,
            'create_datetime': create_datetime,
        },
        job_id, is_pause, replace_existing,
    )


@dataclass
class JobInfo:
    job_id: str
    status: bool
    job_next_run_time: str
    trigger: str
    plan: Dict
    type: str
    script_text: str
    script_type: str
    timeout: int
    extract_names: Optional[List]
    notify_channels: Optional[List]
    update_by: str
    update_datetime: str
    create_by: str
    create_datetime: str


def get_apscheduler_all_jobs():
    conn = None
    try:
        conn = get_connect()
        job_jsons = json.loads(conn.root.get_jobs())
        aps_job = []
        for job_json in job_jsons:
            job_kwargs = job_json.get('kwargs') or {}
            plan = job_json.get('plan') or {}
            aps_job.append(JobInfo(
                job_id=job_json.get('id', ''),
                status=bool(job_json.get('status', job_json.get('next_run_time') not in (None, '-'))),
                job_next_run_time=job_json.get('next_run_time') or '-',
                trigger=job_json.get('trigger') or '',
                plan=plan,
                type=job_kwargs.get('type', ''),
                script_text=job_kwargs.get('script_text', ''),
                script_type=job_kwargs.get('script_type', ''),
                timeout=job_kwargs.get('timeout', 0),
                extract_names=job_kwargs.get('extract_names'),
                notify_channels=job_kwargs.get('notify_channels'),
                update_by=job_kwargs.get('update_by', ''),
                update_datetime=job_kwargs.get('update_datetime', ''),
                create_by=job_kwargs.get('create_by', ''),
                create_datetime=job_kwargs.get('create_datetime', ''),
            ))
        active_listen_jobs = [
            JobInfo(
                job_id=str(job.job_id),
                status=bool(job.status),
                job_next_run_time='-',
                trigger='listen',
                plan={},
                type=str(job.type),
                script_text=str(job.script_text),
                script_type=str(job.script_type),
                timeout=int(cast(int, job.timeout)) if getattr(job, 'timeout', None) is not None else 0,
                extract_names=None,
                notify_channels=None,
                update_by=str(job.update_by),
                update_datetime=f'{job.update_datetime:%Y-%m-%dT%H:%M:%S}',
                create_by=str(job.create_by),
                create_datetime=f'{job.create_datetime:%Y-%m-%dT%H:%M:%S}',
            )
            for job in dao_listen_task.get_activa_listen_job()
        ]
        return aps_job + active_listen_jobs
    except Exception as e:
        raise e
    finally:
        if conn:
            conn.close()


def start_stop_job(job_id, is_start: bool, type_task: str):
    if type_task == 'listen':
        dao_listen_task.enable_job(job_id=job_id, enable=is_start)
    else:
        conn = None
        try:
            conn = get_connect()
            if is_start:
                conn.root.resume_job(job_id=job_id)
            else:
                conn.root.pause_job(job_id=job_id)
        except Exception as e:
            raise e
        finally:
            if conn:
                conn.close()


def remove_job(job_id, task_tye):
    if task_tye == 'listen':
        dao_listen_task.remove_activa_listen_job(job_id=job_id)
    else:
        conn = None
        try:
            conn = get_connect()
            conn.root.remove_job(job_id=job_id)
        except Exception as e:
            raise e
        finally:
            if conn:
                conn.close()


def modify_job(job_id, **kwargs):
    conn = None
    try:
        conn = get_connect()
        conn.root.modify_job(job_id=job_id, **kwargs)
    except Exception as e:
        raise e
    finally:
        if conn:
            conn.close()


def reschedule_job_cron(
    job_id, second, minute, hour, day, month, day_of_week, year=None, week=None,
    start_date=None, end_date=None, timezone=None,
):
    conn = None
    try:
        conn = get_connect()
        conn.root.reschedule_job(
            job_id,
            'cron',
            year=year,
            week=week,
            second=second,
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
            start_date=start_date,
            end_date=end_date,
            timezone=timezone,
        )
    except Exception as e:
        raise e
    finally:
        if conn:
            conn.close()


def reschedule_job_interval(job_id, seconds):
    conn = None
    try:
        conn = get_connect()
        conn.root.reschedule_job(
            job_id,
            'interval',
            seconds=seconds,
        )
    except Exception as e:
        raise e
    finally:
        if conn:
            conn.close()


def get_platform():
    conn = None
    try:
        conn = get_connect()
        return conn.root.get_platform()
    except Exception as e:
        raise e
    finally:
        if conn:
            conn.close()


def get_scheduler_status(timeout=5):
    conn = None
    try:
        conn = get_connect(timeout=timeout)
        return json.loads(conn.root.get_status())
    finally:
        if conn:
            conn.close()


def get_job(job_id):
    conn = None
    try:
        conn = get_connect()
        job_json = conn.root.get_job(job_id)
        if job_json:
            return json.loads(job_json)
        else:
            return None
    except Exception as e:
        raise e
    finally:
        if conn:
            conn.close()

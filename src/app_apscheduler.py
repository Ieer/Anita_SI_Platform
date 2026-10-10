import argparse
import json
import os
import platform
import re
import shlex
import signal
import socket
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from queue import Empty, Queue
from uuid import uuid4

import rpyc
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import STATE_PAUSED
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from rpyc.utils.server import ThreadedServer
from sqlalchemy.engine import URL

from common.listen import active_listen
from common.utilities import util_ssh
from common.utilities.util_process_lock import ProcessFileLock
from common.utilities.util_scheduler_rpc import SchedulerRPCAuthenticator
from config.dashgo_conf import ApSchedulerConf, ListenTaskConf, PathProj, SqlDbConf
from database.sql_db.dao.dao_apscheduler import (
    delete_apscheduler_running,
    delete_expire_data,
    insert_apscheduler_extract_value,
    insert_apscheduler_result,
    insert_apscheduler_running,
    recover_stale_runs,
    select_apscheduler_running_log,
)
from database.sql_db.entity.table_user import SysGroup

# https://github.com/agronholm/apscheduler/blob/3.x/examples/rpc/server.py


SUFFIX = {'Bat': '.bat', 'Shell': '.sh', 'Python': '.py'}
RUN_CMD = {'Bat': ['cmd', '/c'], 'Shell': ['sh'], 'Python': ['python']}
NO_CATCHUP_MISFIRE_GRACE_SECONDS = 1


def _stop_local_process(process):
    if process.poll() is not None:
        return True
    if os.name == 'nt':
        try:
            subprocess.run(
                ['taskkill.exe', '/F', '/T', '/PID', str(process.pid)],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        if os.name != 'nt':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait(timeout=5)
    return process.poll() is not None


def _drain_queue(output_queue):
    chunks = []
    while True:
        try:
            chunks.append(output_queue.get_nowait())
        except Empty:
            return ''.join(chunks)


def _execute_script(
    type,
    script_text,
    script_type,
    job_id,
    notify_channels,
    extract_names=None,
    timeout=20,
    host=None,
    port=22,
    username=None,
    password=None,
    update_by=None,
    update_datetime=None,
    create_by=None,
    create_datetime=None,
    env_vars=None,
    _task_run_id=None,
):
    """
    根据类型执行脚本，支持本地和远程执行。

    参数:
    type (str): run-type执行类型，'local' 表示本地执行，'ssh' 表示远程执行。
    script_text (str): 要运行的脚本内容或命令。
    host (str): 远程服务器的 host 地址（仅在 'ssh' 类型时需要）。
    password (str): SSH 登录密码（仅在 'ssh' 类型时需要）。
    timeout (int): 命令执行的超时时间，单位为秒。
    """
    start_datetime = datetime.now()

    def _parse_json_if_str(v):
        if v is None:
            return None
        if isinstance(v, (str, bytes, bytearray)):
            try:
                return json.loads(v)
            except Exception:
                return v
        return v

    extract_names = _parse_json_if_str(extract_names)
    notify_channels = _parse_json_if_str(notify_channels)
    env_vars = _parse_json_if_str(env_vars) or {}
    if not isinstance(env_vars, dict):
        env_vars = {}
    output_full = ''

    def persist_task_log(output):
        if output and _task_run_id:
            from database.sql_db.dao import dao_task_center

            dao_task_center.append_log(_task_run_id, output)

    def pop_from_stdout(stdout, event: threading.Event, queue_stdout: Queue, encoding='utf8', stop_on_eof=False):
        while not event.is_set():
            try:  # ugly fix: 避免ssh命令超时后，这里疯狂报错
                line = stdout.readline()
                if not line:
                    if stop_on_eof:
                        break
                    time.sleep(0.2)
                    continue
                if isinstance(line, bytes):
                    queue_stdout.put(line.decode(encoding, errors='ignore'))
                else:
                    queue_stdout.put(line)
            except Exception:
                ...

    def pop_from_stderr(stderr, event: threading.Event, queue_stderr: Queue, encoding='utf8', stop_on_eof=False):
        while not event.is_set():
            try:  # ugly fix: 避免ssh命令超时后，这里疯狂报错
                line = stderr.readline()
                if not line:
                    if stop_on_eof:
                        break
                    time.sleep(0.2)
                    continue
                if isinstance(line, bytes):
                    queue_stderr.put(line.decode(encoding, errors='ignore'))
                else:
                    queue_stderr.put(line)
            except Exception:
                ...

    suffix = SUFFIX[script_type]
    run_cmd = RUN_CMD[script_type]
    if type == 'local':
        encoding = 'gbk' if suffix == '.bat' else 'utf-8'
        script_filepath = None
        process = None
        queue_stdout = Queue()
        queue_stderr = Queue()
        event = threading.Event()
        reader_threads = []
        order = 0
        result_recorded = False

        def persist_output(output):
            nonlocal output_full, order
            if not output:
                return
            insert_apscheduler_running(
                job_id=job_id,
                log=output,
                order=order,
                start_datetime=start_datetime,
            )
            persist_task_log(output)
            output_full += output
            if extract_names and (search_sops_var := re.search(r'<SOPS_VAR>.+</SOPS_VAR>', output_full, flags=re.DOTALL)):
                insert_apscheduler_extract_value(
                    job_id=job_id,
                    log=output,
                    start_datetime=start_datetime,
                    extract_names=extract_names,
                    notify_channels=notify_channels,
                )
                output_full = output_full[search_sops_var.end():]
            order += 1

        try:
            with tempfile.NamedTemporaryFile(
                delete=False,
                mode='w',
                dir=tempfile.gettempdir(),
                prefix='dashgo_',
                suffix=suffix,
                encoding=encoding,
            ) as script_file:
                script_file.write(script_text)
                script_filepath = script_file.name

            popen_options = {}
            if os.name == 'nt':
                popen_options['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                popen_options['start_new_session'] = True
            process = subprocess.Popen(
                [*run_cmd, script_filepath],
                shell=False,
                env={**os.environ.copy(), **env_vars} if env_vars else os.environ.copy(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=False,
                **popen_options,
            )
            reader_threads = [
                threading.Thread(target=pop_from_stdout, args=(process.stdout, event, queue_stdout, encoding, True), daemon=True),
                threading.Thread(target=pop_from_stderr, args=(process.stderr, event, queue_stderr, encoding, True), daemon=True),
            ]
            for reader_thread in reader_threads:
                reader_thread.start()

            deadline = time.monotonic() + max(1, int(timeout))
            is_timeout = False
            while process.poll() is None:
                persist_output(_drain_queue(queue_stdout) + _drain_queue(queue_stderr))
                if time.monotonic() >= deadline:
                    is_timeout = True
                    if not _stop_local_process(process):
                        raise RuntimeError('Local process termination could not be confirmed')
                    break
                time.sleep(0.1)

            return_code = process.wait(timeout=5)
            event.set()
            for reader_thread in reader_threads:
                reader_thread.join(timeout=2)
            persist_output(_drain_queue(queue_stdout) + _drain_queue(queue_stderr))
            log = select_apscheduler_running_log(job_id=job_id, start_datetime=start_datetime)
            status = 'timeout' if is_timeout else ('success' if return_code == 0 else 'error')
            insert_apscheduler_result(job_id, status=status, log=log, start_datetime=start_datetime)
            result_recorded = True

            if status != 'success':
                err_name = f'[ERROR] {job_id} Local Script Execute {status.title() if is_timeout else "Failed"}'
                err_log = f'<SOPS_VAR>{err_name}: {log}</SOPS_VAR>'
                insert_apscheduler_extract_value(
                    job_id=job_id,
                    log=err_log,
                    start_datetime=start_datetime,
                    extract_names=[{'type': 'notify', 'name': err_name}],
                    notify_channels=notify_channels,
                )
            return status
        except Exception as error:
            if process and process.poll() is None:
                _stop_local_process(process)
            if not result_recorded:
                try:
                    log = select_apscheduler_running_log(job_id=job_id, start_datetime=start_datetime)
                    insert_apscheduler_result(
                        job_id,
                        status='unknown' if process and process.poll() is None else 'error',
                        log=f'{log}\n[ERROR] {error.__class__.__name__}: {error}',
                        start_datetime=start_datetime,
                    )
                    result_recorded = True
                except Exception:
                    pass
            raise
        finally:
            event.set()
            if process:
                if process.poll() is None:
                    _stop_local_process(process)
                for stream in (process.stdout, process.stderr):
                    if stream:
                        stream.close()
            for reader_thread in reader_threads:
                reader_thread.join(timeout=2)
            if result_recorded:
                delete_apscheduler_running(job_id=job_id, start_datetime=start_datetime)
            if script_filepath and os.path.exists(script_filepath):
                os.remove(script_filepath)
    elif type == 'ssh':
        tempdir = tempfile.gettempdir()
        sshscript_cache = os.path.join(tempdir, 'dashgo_sshscript_cache')
        if not os.path.exists(sshscript_cache):
            os.makedirs(sshscript_cache)
        # ssh默认都认为是linux系统
        with tempfile.NamedTemporaryFile(
            delete=False,
            mode='w',
            newline='\n',
            dir=sshscript_cache,
            prefix=f'dashgo_{datetime.now().timestamp()}',
            suffix=suffix,
            encoding='utf-8',
        ) as f:
            if script_type == 'Shell':
                f.write('\n'.join(['export ' + key + "='" + value + "'" for key, value in env_vars.items()]) + '\n')
            elif script_type == 'Python':
                f.write('import os\n')
                for k, v in env_vars.items():
                    f.write(f"os.environ['{k}'] = '''{v}'''\n")
            f.write(script_text)
            f.flush()
            script_filepath = f.name
        ssh = None
        remote_script_path = f'/tmp/{os.path.basename(script_filepath)}'
        event = threading.Event()
        reader_threads = []
        stdout = None
        result_recorded = False
        remote_started = False
        deadline = time.monotonic() + max(1, int(timeout))
        try:
            ssh = util_ssh.connect_ssh(
                hostname=host,
                port=port,
                username=username,
                password=password,
                timeout=max(1, int(timeout)),
            )
            util_ssh.ftp_ssh(ssh, script_filepath, '/tmp')  # 上传脚本到远程/tmp目录
            try:
                stdin, stdout, stderr = ssh.exec_command(
                    f'{" ".join(run_cmd)} {shlex.quote(remote_script_path)}',
                    get_pty=True,
                    timeout=max(1, int(timeout)),
                )
                remote_started = True
                queue_stdout = Queue()
                queue_stderr = Queue()
                reader_threads = [
                    threading.Thread(target=pop_from_stdout, args=(stdout, event, queue_stdout), daemon=True),
                    threading.Thread(target=pop_from_stderr, args=(stderr, event, queue_stderr), daemon=True),
                ]
                for reader_thread in reader_threads:
                    reader_thread.start()
                order = 0
                time.sleep(1)
                output = ''
                iteration = 0
                while True:
                    if time.monotonic() >= deadline:
                        raise socket.timeout('SSH task execution timed out')
                    if iteration % 4 == 0:  # 等待2秒钟读取一次日志
                        output_list = []
                        output_list.extend(queue_stdout.get() for _ in range(queue_stdout.qsize()))
                        output_list.extend(queue_stderr.get() for _ in range(queue_stderr.qsize()))
                        if output := ''.join(output_list):
                            insert_apscheduler_running(
                                job_id=job_id,
                                log=output,
                                order=order,
                                start_datetime=start_datetime,
                            )
                            persist_task_log(output)
                            output_full += output
                            if extract_names and (search_sops_var := re.search(r'<SOPS_VAR>.+</SOPS_VAR>', output_full, flags=re.DOTALL)):
                                # 发现了<SOPS_VAR>标签，进行提取
                                insert_apscheduler_extract_value(
                                    job_id=job_id,
                                    log=output,
                                    start_datetime=start_datetime,
                                    extract_names=extract_names,
                                    notify_channels=notify_channels,
                                )
                                output_full = output_full[search_sops_var.end() :]  # 清除已经提取的内容
                            order += 1
                    if stdout.channel.exit_status_ready() and output == '':
                        break
                    time.sleep(0.5)
                    iteration += 1
                time.sleep(0.5)  # 多等待0.5秒，保证多线程的日志读取完成
                event.set()
                output_list = []
                output_list.extend(queue_stdout.get() for _ in range(queue_stdout.qsize()))
                output_list.extend(queue_stderr.get() for _ in range(queue_stderr.qsize()))
                if output := ''.join(output_list):
                    insert_apscheduler_running(
                        job_id=job_id,
                        log=output,
                        order=order,
                        start_datetime=start_datetime,
                    )
                    persist_task_log(output)
                    output_full += output
                    if extract_names and (search_sops_var := re.search(r'<SOPS_VAR>.+</SOPS_VAR>', output_full, flags=re.DOTALL)):
                        # 发现了<SOPS_VAR>标签，进行提取
                        insert_apscheduler_extract_value(
                            job_id=job_id,
                            log=output,
                            start_datetime=start_datetime,
                            extract_names=extract_names,
                            notify_channels=notify_channels,
                        )
            except socket.timeout:
                if stdout:
                    stdout.channel.close()
                log = select_apscheduler_running_log(job_id=job_id, start_datetime=start_datetime)
                insert_apscheduler_result(
                    job_id,
                    status='unknown',
                    log=log,
                    start_datetime=start_datetime,
                )
                result_recorded = True
                return 'unknown'
            return_code = stdout.channel.recv_exit_status()
            # 执行成功 or 失败
            log = select_apscheduler_running_log(job_id=job_id, start_datetime=start_datetime)
            insert_apscheduler_result(
                job_id,
                status='success' if return_code == 0 else 'error',
                log=log,
                start_datetime=start_datetime,
            )
            result_recorded = True
            if return_code != 0:
                err_name = f'[ERROR] {job_id} Ssh Script Execute Fail'
                err_log = f'<SOPS_VAR>{err_name}: {log}</SOPS_VAR>'
                insert_apscheduler_extract_value(
                    job_id=job_id,
                    log=err_log,
                    start_datetime=start_datetime,
                    extract_names=[{'type': 'notify', 'name': err_name}],
                    notify_channels=notify_channels,
                )
            return 'success' if return_code == 0 else 'error'
        except TimeoutError:
            err_name = f'[ERROR] SSH connect error from {job_id}'
            err_log = f'<SOPS_VAR>{err_name}: Cannot connect to the host {host}</SOPS_VAR>'
            insert_apscheduler_result(
                job_id,
                status='error',
                log=err_log,
                start_datetime=start_datetime,
            )
            result_recorded = True
            insert_apscheduler_extract_value(
                job_id=job_id,
                log=err_log,
                start_datetime=start_datetime,
                extract_names=[{'type': 'notify', 'name': err_name}],
                notify_channels=notify_channels,
            )
            return 'error'
        except Exception as error:
            if not result_recorded:
                try:
                    log = select_apscheduler_running_log(job_id=job_id, start_datetime=start_datetime)
                    insert_apscheduler_result(
                        job_id,
                        status='unknown' if remote_started else 'error',
                        log=f'{log}\n[ERROR] {error.__class__.__name__}: {error}',
                        start_datetime=start_datetime,
                    )
                    result_recorded = True
                except Exception:
                    pass
            raise
        finally:
            event.set()
            if stdout:
                try:
                    stdout.channel.close()
                except Exception:
                    pass
            for reader_thread in reader_threads:
                reader_thread.join(timeout=2)
            if ssh:
                try:
                    ssh.exec_command(f'rm -f -- {shlex.quote(remote_script_path)}', timeout=5)
                except Exception:
                    pass
                ssh.close()
            if os.path.exists(script_filepath):
                os.remove(script_filepath)
            if result_recorded:
                delete_apscheduler_running(job_id=job_id, start_datetime=start_datetime)


def _execution_target(task_id, source_job_id):
    from database.sql_db.dao import dao_task_center

    task = (dao_task_center.get_task(task_id) if task_id
            else dao_task_center.find_task('platform', source_job_id))
    if (task is None or task.engine != 'platform' or task.backend_ref != source_job_id
            or not task.claimed or not task.enabled or task.deleted):
        return None
    with SysGroup._meta.database.connection_context():
        if not SysGroup.select().where(
                (SysGroup.group_name == task.group_name) & (SysGroup.group_status == True)).exists():
            return None
    return task


def run_script(
    type,
    script_text,
    script_type,
    job_id,
    notify_channels,
    extract_names=None,
    timeout=20,
    host=None,
    port=22,
    username=None,
    password=None,
    update_by=None,
    update_datetime=None,
    create_by=None,
    create_datetime=None,
    env_vars=None,
    task_id=None,
    run_id=None,
    source='schedule',
    source_job_id=None,
):
    """Compatibility entry point for persisted APScheduler callables and guarded runs."""
    from database.sql_db.dao import dao_task_center

    backend_job_id = source_job_id or job_id
    if source not in {'schedule', 'manual', 'agent', 'retry', 'listen'}:
        return None
    task = _execution_target(task_id, backend_job_id)
    if task is None:
        if run_id:
            dao_task_center.finish_run(run_id, 'skipped', 'task or team disabled before execution')
        return None
    run_id = run_id or uuid4().hex
    from common import task_center
    payload = dict(type=type, script_text=script_text, script_type=script_type, timeout=timeout,
                   extract_names=extract_names, notify_channels=notify_channels,
                   env_vars=None if source == 'listen' else env_vars)
    if type == 'ssh':
        payload.update(host=host, port=port, username=username, password=password)
    try:
        if not task.digest or task.digest != task_center._digest(payload):
            raise ValueError('execution payload does not match claimed definition')
        if source in {'manual', 'agent'}:
            from types import SimpleNamespace
            from database.sql_db.dao.dao_user import get_user_access_meta
            request = dao_task_center.get_run(run_id)
            if request is None or request.task_id != task.task_id or request.source != source or request.digest != task.digest:
                raise ValueError('run metadata mismatch')
            permissions = get_user_access_meta(request.actor)
            task_center._task_access(SimpleNamespace(user_name=request.actor,
                has_access=lambda permission: permission in permissions), task, 'run')
    except Exception:
        dao_task_center.finish_run(run_id, 'skipped', 'definition or actor authorization changed before execution')
        return None
    try:
        run = dao_task_center.begin_execution(
            backend_job_id,
            task_id=task.task_id,
            run_id=run_id,
            source=source,
            actor='scheduler' if source in {'schedule', 'listen', 'retry'} else '',
        )
    except Exception:
        try:
            dao_task_center.finish_run(run_id, 'unknown', 'execution guard outcome was ambiguous')
        except Exception:
            pass
        return None
    if not run or run.get('status') != 'running':
        return None
    if _execution_target(task.task_id, backend_job_id) is None:
        dao_task_center.finish_run(run_id, 'skipped', 'task or team was disabled before execution')
        return None

    try:
        outcome = _execute_script(
            type=type,
            script_text=script_text,
            script_type=script_type,
            job_id=backend_job_id,
            notify_channels=notify_channels,
            extract_names=extract_names,
            timeout=timeout,
            host=host,
            port=port,
            username=username,
            password=password,
            update_by=update_by,
            update_datetime=update_datetime,
            create_by=create_by,
            create_datetime=create_datetime,
            env_vars=env_vars,
            _task_run_id=run_id,
        )
        terminal = {
            'success': 'succeeded', 'error': 'failed', 'timeout': 'timeout',
            'unknown': 'unknown',
        }.get(outcome, 'unknown')
        if not dao_task_center.finish_run(run_id, terminal, 'executor outcome: ' + str(outcome)):
            current = dao_task_center.get_run(run_id)
            if current and current.status in {'requested', 'submitted', 'running'}:
                dao_task_center.finish_run(run_id, 'unknown', 'terminal state write was ambiguous')
        return outcome
    except Exception as error:
        try:
            dao_task_center.finish_run(
                run_id, 'unknown', f'execution outcome is uncertain: {error.__class__.__name__}')
        except Exception:
            pass
        raise


CLEAR_JOB_ID = 'sys_delete_expire_data_for_cron'
LISTEN_JOB_ID = 'sys_listen_interval_job'
SCHEDULER_STATUS = {
    'started_at': None,
    'state': 'starting',
    'last_job_finished_at': None,
    'last_job_id': None,
    'last_job_error': None,
}
SCHEDULER_STATUS_LOCK = threading.Lock()


def _record_scheduler_event(event):
    with SCHEDULER_STATUS_LOCK:
        SCHEDULER_STATUS['last_job_finished_at'] = datetime.now().astimezone().isoformat()
        SCHEDULER_STATUS['last_job_id'] = getattr(event, 'job_id', None)
        exception = getattr(event, 'exception', None)
        SCHEDULER_STATUS['last_job_error'] = type(exception).__name__ if exception else None


class SchedulerService(rpyc.Service):
    trigger_arg_names = {
        'date': {'run_date', 'timezone'},
        'interval': {'weeks', 'days', 'hours', 'minutes', 'seconds', 'start_date', 'end_date', 'timezone'},
        'cron': {'year', 'month', 'day', 'week', 'day_of_week', 'hour', 'minute', 'second', 'start_date', 'end_date', 'timezone'},
    }

    @staticmethod
    def _validate_jobstore(jobstore):
        if jobstore not in (None, 'default'):
            raise ValueError('Unsupported job store')

    def exposed_add_job(self, trigger, trigger_args, job_kwargs, job_id, is_pause=False, replace_existing=False):
        job_kwarg_names = {
            'type', 'script_text', 'script_type', 'timeout', 'extract_names', 'notify_channels',
            'env_vars', 'host', 'port', 'username', 'password', 'update_by', 'update_datetime',
            'create_by', 'create_datetime', 'task_id', 'run_id', 'source', 'source_job_id',
        }
        if trigger not in self.trigger_arg_names:
            raise ValueError('Unsupported scheduler trigger')
        if not isinstance(trigger_args, dict) or set(trigger_args) - self.trigger_arg_names[trigger]:
            raise ValueError('Invalid trigger arguments')
        if not isinstance(job_kwargs, dict) or set(job_kwargs) - job_kwarg_names:
            raise ValueError('Invalid job arguments')
        if not isinstance(job_id, str) or not job_id or len(job_id) > 191:
            raise ValueError('Invalid job ID')
        if job_kwargs.get('type') not in {'local', 'ssh'}:
            raise ValueError('Unsupported script execution type')
        if job_kwargs.get('script_type') not in SUFFIX:
            raise ValueError('Unsupported script type')
        if not isinstance(job_kwargs.get('script_text'), str) or len(job_kwargs['script_text']) > 1_000_000:
            raise ValueError('Invalid script content')
        timeout = job_kwargs.get('timeout')
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 86400:
            raise ValueError('Invalid execution timeout')
        if not isinstance(is_pause, bool):
            raise ValueError('Invalid pause state')
        if not isinstance(replace_existing, bool):
            raise ValueError('Invalid replacement setting')
        metadata = {name: job_kwargs.get(name) for name in ('task_id', 'run_id', 'source', 'source_job_id')}
        if any(value is not None for value in metadata.values()):
            if (not isinstance(metadata['task_id'], str) or not metadata['task_id'] or len(metadata['task_id']) > 32
                    or not isinstance(metadata['run_id'], str) or not metadata['run_id'] or len(metadata['run_id']) > 32
                    or metadata['source'] not in {'manual', 'agent', 'retry', 'listen'}
                    or not isinstance(metadata['source_job_id'], str) or not metadata['source_job_id']
                    or len(metadata['source_job_id']) > 191):
                raise ValueError('Invalid task run metadata')
        if job_kwargs['type'] == 'ssh':
            if not isinstance(job_kwargs.get('host'), str) or not job_kwargs['host']:
                raise ValueError('SSH host is required')
            if not isinstance(job_kwargs.get('username'), str) or not job_kwargs['username']:
                raise ValueError('SSH username is required')
            port = job_kwargs.get('port', 22)
            if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
                raise ValueError('Invalid SSH port')
        json.dumps(trigger_args, ensure_ascii=False, allow_nan=False)
        json.dumps(job_kwargs, ensure_ascii=False, allow_nan=False)

        safe_job_kwargs = dict(job_kwargs, job_id=job_id)
        job_options = {
            'max_instances': 1,
            'coalesce': True,
            'misfire_grace_time': (
                ApSchedulerConf.MISFIRE_GRACE_TIME_SECONDS
                if job_kwargs.get('source') in {'manual', 'agent'}
                else NO_CATCHUP_MISFIRE_GRACE_SECONDS
            ),
        }
        if is_pause:
            job_options['next_run_time'] = None
        job = scheduler.add_job(
            run_script,
            trigger,
            trigger_args=trigger_args,
            kwargs=safe_job_kwargs,
            id=job_id,
            replace_existing=replace_existing,
            **job_options,
        )
        return job.id

    def exposed_modify_job(self, job_id, jobstore=None, **changes):
        self._validate_jobstore(jobstore)
        allowed_changes = {'misfire_grace_time', 'coalesce', 'max_instances'}
        if not changes or set(changes) - allowed_changes:
            raise ValueError('Unsupported job modification')
        if 'misfire_grace_time' in changes and (
            isinstance(changes['misfire_grace_time'], bool)
            or not isinstance(changes['misfire_grace_time'], int)
            or changes['misfire_grace_time'] < 1
        ):
            raise ValueError('Invalid misfire grace time')
        if 'coalesce' in changes and not isinstance(changes['coalesce'], bool):
            raise ValueError('Invalid coalesce setting')
        if 'max_instances' in changes and (
            isinstance(changes['max_instances'], bool)
            or not isinstance(changes['max_instances'], int)
            or changes['max_instances'] < 1
        ):
            raise ValueError('Invalid maximum instances')
        scheduler.modify_job(job_id, jobstore, **changes)

    def exposed_reschedule_job(self, job_id, jobstore=None, trigger=None, **trigger_args):
        self._validate_jobstore(jobstore)
        if trigger not in self.trigger_arg_names:
            raise ValueError('Unsupported scheduler trigger')
        if set(trigger_args) - self.trigger_arg_names[trigger]:
            raise ValueError('Invalid trigger arguments')
        json.dumps(trigger_args, ensure_ascii=False, allow_nan=False)
        scheduler.reschedule_job(job_id, jobstore, trigger, **trigger_args)

    def exposed_pause_job(self, job_id, jobstore=None):
        self._validate_jobstore(jobstore)
        scheduler.pause_job(job_id, jobstore)

    def exposed_resume_job(self, job_id, jobstore=None):
        self._validate_jobstore(jobstore)
        scheduler.resume_job(job_id, jobstore)

    def exposed_remove_job(self, job_id, jobstore=None):
        self._validate_jobstore(jobstore)
        scheduler.remove_job(job_id, jobstore)

    def exposed_get_job(self, job_id):
        job = scheduler.get_job(job_id)
        if job:
            return json.dumps(self.get_job_dict(job))
        else:
            return None

    def exposed_get_platform(self):
        return platform.system()

    def exposed_get_status(self):
        active_scheduler = globals().get('scheduler')
        with SCHEDULER_STATUS_LOCK:
            result = dict(SCHEDULER_STATUS)
        scheduler_running = bool(active_scheduler and active_scheduler.running)
        if scheduler_running:
            result['state'] = 'paused' if getattr(active_scheduler, 'state', None) == STATE_PAUSED else 'running'
        user_jobs = (
            [job for job in active_scheduler.get_jobs() if job.id not in (CLEAR_JOB_ID, LISTEN_JOB_ID)]
            if scheduler_running
            else []
        )
        result.update({
            'service': 'si-platform-scheduler',
            'ready': scheduler_running,
            'running': scheduler_running,
            'job_count': len(user_jobs),
            'process_id': os.getpid(),
        })
        return json.dumps(result, ensure_ascii=False)

    def exposed_get_jobs(self, jobstore=None):
        self._validate_jobstore(jobstore)
        jobs = scheduler.get_jobs(jobstore)
        result = []
        for job in jobs:
            if job.id in (CLEAR_JOB_ID, LISTEN_JOB_ID):
                continue
            result.append(self.get_job_dict(job))
        return json.dumps(result, ensure_ascii=False)

    @staticmethod
    def get_job_dict(job):
        def iso(value):
            return value.isoformat() if value is not None else None

        def timezone_name(value):
            return getattr(value, 'key', str(value)) if value is not None else None

        if isinstance(job.trigger, IntervalTrigger):
            total_seconds = job.trigger.interval.total_seconds()
            whole_seconds = int(total_seconds)
            days, remainder = divmod(whole_seconds, 86400)
            weeks, days = divmod(days, 7)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            plan = {
                'weeks': weeks,
                'days': days,
                'hours': hours,
                'minutes': minutes,
                'seconds': seconds + (total_seconds - whole_seconds),
                'interval_seconds': total_seconds,
                'start_date': iso(job.trigger.start_date),
                'end_date': iso(job.trigger.end_date),
                'timezone': timezone_name(job.trigger.timezone),
            }
            trigger = 'interval'
        elif isinstance(job.trigger, CronTrigger):
            plan = {field.name: str(field) for field in job.trigger.fields}
            plan.update({
                'start_date': iso(job.trigger.start_date),
                'end_date': iso(job.trigger.end_date),
                'timezone': timezone_name(job.trigger.timezone),
            })
            trigger = 'cron'
        elif isinstance(job.trigger, DateTrigger):
            plan = {
                'run_date': iso(job.trigger.run_date),
                'timezone': timezone_name(job.trigger.run_date.tzinfo),
            }
            trigger = 'date'
        else:
            raise Exception('不支持的触发器类型')
        return {
            'id': job.id,
            'status': job.next_run_time is not None,
            'next_run_time': iso(job.next_run_time) if job.next_run_time else '-',
            'kwargs': job.kwargs,
            'trigger': trigger,
            'plan': plan,
        }


def delete_expire_data_for_cron(day):
    delete_expire_data(day)
    from database.sql_db.dao.dao_task_center import prune_logs
    prune_logs(day)


def add_clear_job(scheduler):
    # 添加清理作业
    try:
        scheduler.remove_job(CLEAR_JOB_ID)
        print('清理作业删除成功')
    except Exception:
        pass
    scheduler.add_job(
        'app_apscheduler:delete_expire_data_for_cron',
        'cron',
        kwargs=[
            ('day', ApSchedulerConf.DATA_EXPIRE_DAY),
        ],
        year='*',
        week='*',
        second=0,
        minute=0,
        hour=1,
        day='*',
        month='*',
        day_of_week='*',
        id=CLEAR_JOB_ID,
    )
    print(f'清理作业添加成功，保留天数为{ApSchedulerConf.DATA_EXPIRE_DAY}')


def listen_interval():
    active_listen()


def add_listen_job(scheduler):
    # 添加监听作业
    try:
        scheduler.remove_job(LISTEN_JOB_ID)
        print('主动监听作业删除成功')
    except Exception:
        pass
    interval = ListenTaskConf.PERIOD_MINTUES * 60
    scheduler.add_job(
        'app_apscheduler:listen_interval',
        'interval',
        seconds=interval,
        jobstore='memory',
        id=LISTEN_JOB_ID,
    )
    print(f'主动监听作业添加成功，扫描周期为{interval}秒')


def _allow_task_center_recovery(schema_version, start_paused):
    if schema_version == 1:
        if not start_paused:
            raise RuntimeError(
                'Task-center schema version 1 requires an explicit maintenance migration; '
                'start the scheduler paused for migration.'
            )
        return False
    return True


def _task_center_schema_version():
    from database.sql_db.conn import db
    from database.sql_db.entity.table_task_center import TaskSchemaVersion

    database = db()
    with database.connection_context():
        if not database.table_exists(TaskSchemaVersion._meta.table_name):
            return None
        version = TaskSchemaVersion.get_or_none(TaskSchemaVersion.component == 'task_center')
        return version.version if version else None


def run_scheduler_service(start_paused=False):
    import ipaddress

    if ApSchedulerConf.HOST != 'localhost' and not ipaddress.ip_address(ApSchedulerConf.HOST).is_loopback:
        raise RuntimeError('Scheduler RPC must only listen on a loopback address')
    authenticator = SchedulerRPCAuthenticator()
    scheduler_instance = None
    server_instance = None
    try:
        from database.sql_db.dao.dao_listen_task import ensure_listen_checkpoint_table

        ensure_listen_checkpoint_table()
        if SqlDbConf.RDB_TYPE == 'sqlite':
            jobstores = {
                'default': SQLAlchemyJobStore(
                    url=URL.create(
                        'sqlite',
                        database=PathProj.resolve_sqlite_db_path(SqlDbConf.SQLITE_DB_PATH).as_posix(),
                    ),
                    engine_options={'connect_args': {'timeout': 20}},
                ),
                'memory': MemoryJobStore(),
            }
        elif SqlDbConf.RDB_TYPE == 'mysql':
            jobstores = {
                'default': SQLAlchemyJobStore(url=f'mysql+pymysql://{SqlDbConf.USER}:{SqlDbConf.PASSWORD}@{SqlDbConf.HOST}:{SqlDbConf.PORT}/{SqlDbConf.DATABASE}'),
                'memory': MemoryJobStore(),
            }
        else:
            raise RuntimeError(f'Unsupported scheduler database type: {SqlDbConf.RDB_TYPE}')
        schema_version = _task_center_schema_version()
        recover_task_center_runs = _allow_task_center_recovery(schema_version, start_paused)
        if recover_task_center_runs:
            recover_stale_runs()
        executors = {'default': ThreadPoolExecutor(64)}
        job_defaults = {
            'coalesce': True,
            'max_instances': 1,
            'misfire_grace_time': NO_CATCHUP_MISFIRE_GRACE_SECONDS,
        }
        scheduler_instance = BackgroundScheduler(jobstores=jobstores, executors=executors, job_defaults=job_defaults)
        globals()['scheduler'] = scheduler_instance
        scheduler_instance.add_listener(_record_scheduler_event, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
        with SCHEDULER_STATUS_LOCK:
            SCHEDULER_STATUS.update({
                'started_at': datetime.now().astimezone().isoformat(),
                'state': 'paused' if start_paused else 'running',
                'last_job_finished_at': None,
                'last_job_id': None,
                'last_job_error': None,
            })
        scheduler_instance.start(paused=True)
        from database.sql_db.dao import dao_task_center

        if recover_task_center_runs:
            dao_task_center.recover_runs()
        for restored_job in scheduler_instance.get_jobs():
            if restored_job.id not in (CLEAR_JOB_ID, LISTEN_JOB_ID):
                scheduler_instance.modify_job(
                    restored_job.id,
                    coalesce=True,
                    max_instances=1,
                    misfire_grace_time=NO_CATCHUP_MISFIRE_GRACE_SECONDS,
                )
        protocol_config = {'allow_public_attrs': False, 'allow_pickle': False}
        server_instance = ThreadedServer(
            SchedulerService,
            hostname=ApSchedulerConf.HOST,
            port=ApSchedulerConf.PORT,
            authenticator=authenticator,
            protocol_config=protocol_config,
        )
        add_clear_job(scheduler_instance)
        add_listen_job(scheduler_instance)
        if not start_paused:
            scheduler_instance.resume()
        server_instance.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        if server_instance:
            server_instance.close()
        if scheduler_instance and scheduler_instance.running:
            scheduler_instance.shutdown(wait=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Start the platform scheduler RPC service')
    parser.add_argument('--start-paused', action='store_true', help='Load jobs without processing scheduled runs')
    args = parser.parse_args()
    scheduler_lock_path = PathProj.ROOT_PATH.parent / 'user_data' / 'cache' / 'apscheduler.lock'
    with ProcessFileLock(scheduler_lock_path):
        run_scheduler_service(start_paused=args.start_paused)

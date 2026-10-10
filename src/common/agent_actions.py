import hashlib
import json
import os
from datetime import datetime

from common.agent import AgentError
from common.utilities import util_apscheduler


def run_allowlist():
    try:
        value = json.loads(os.environ.get('SMART_AGENT_RUN_ALLOWLIST', '[]'))
    except (TypeError, ValueError):
        return set()
    if not isinstance(value, list) or len(value) > 100 or any(
            not isinstance(item, str) or not item.strip() or len(item) > 191 for item in value):
        return set()
    return set(value)


def can_propose(menu_access):
    return (menu_access.has_access('工作台-页面') and menu_access.has_access('任务管理-页面')
            and bool(run_allowlist()))


def _task(job_id, menu_access):
    if not can_propose(menu_access) or job_id not in run_allowlist():
        raise AgentError('任务未获运行授权或不在运行白名单中。')
    try:
        job = next((item for item in util_apscheduler.get_apscheduler_all_jobs() if item.job_id == job_id), None)
    except Exception as error:
        raise AgentError('调度服务不可用，未提交运行请求。') from error
    if job is None or job.type != 'local' or job.status is not True:
        raise AgentError('任务不存在、已暂停或不是本地任务。')
    from common import task_center
    from database.sql_db.dao import dao_task_center
    try:
        task_center._task_access(menu_access, dao_task_center.find_task('platform', job_id), 'run')
    except Exception as error:
        raise AgentError('任务未认领或团队未授予运行权限。') from error
    payload = {'script_text': job.script_text, 'script_type': job.script_type, 'timeout': job.timeout,
               'extract_names': job.extract_names, 'notify_channels': job.notify_channels, 'env_vars': None}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
    return payload, digest


def propose_task(job_id, menu_access):
    payload, digest = _task(job_id, menu_access)
    return {'job_id': job_id, 'task_digest': digest,
            'summary': f"运行已有本地任务 {job_id}；类型 {payload['script_type']}；超时 {payload['timeout']} 秒。"}


def decide_task(owner, conversation_id, action_id, approve, access_provider):
    from database.sql_db.dao import dao_agent

    access = access_provider()
    if access.user_name != owner or not access.has_access('工作台-页面'):
        raise AgentError('访问权限已变更，请重新登录。')
    if approve and not can_propose(access):
        raise AgentError('没有任务运行授权，未提交运行请求。')
    action = dao_agent.decide_action(owner, conversation_id, action_id, approve)
    if not approve:
        return {'status': 'rejected', 'message': '已拒绝，未提交任务。'}
    try:
        payload, digest = _task(action['job_id'], access)
        if digest != action['task_digest']:
            raise AgentError('任务内容已变更，请重新生成提议并核对。')
        access = access_provider()
        if access.user_name != owner or not can_propose(access) or action['job_id'] not in run_allowlist():
            raise AgentError('访问权限或运行白名单已变更，未提交运行请求。')
    except AgentError as error:
        dao_agent.finish_action(owner, conversation_id, action_id, 'blocked', str(error))
        return {'status': 'blocked', 'message': str(error)}
    except Exception:
        message = '任务校验失败，未提交运行请求。'
        dao_agent.finish_action(owner, conversation_id, action_id, 'blocked', message)
        return {'status': 'blocked', 'message': message}
    canonical_run_id = hashlib.sha256(action['run_id'].encode('utf-8')).hexdigest()[:32]
    try:
        from common import task_center
        from database.sql_db.dao import dao_task_center
        task = dao_task_center.find_task('platform', action['job_id'])
        result = task_center.request_run(access, task.task_id, action['run_id'], source='agent',
                                          run_id=canonical_run_id)
        if result.get('run_id') != canonical_run_id:
            raise AgentError('运行回执不匹配。')
    except Exception:
        status = 'unknown'
        message = f"提交结果未知，禁止自动重试。请在任务日志中核对运行 ID：{canonical_run_id}"
    else:
        status = 'submitted' if result['status'] in {
            'submitted', 'running', 'succeeded', 'failed', 'timeout', 'cancelled'} else 'unknown'
        message = f"{result['message']} 运行 ID：{result['run_id']}"
    dao_agent.finish_action(owner, conversation_id, action_id, status, message)
    return {'status': status, 'message': message}
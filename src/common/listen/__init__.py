from . import email_pop3
from database.sql_db.repositories.listen_repository import get_listen_api_by_name
from database.sql_db.repositories.listen_task_repository import get_activa_listen_job
from common.utilities.util_logger import Log
from typing import List, Dict
import json
import hashlib
from datetime import datetime, timedelta
from config.anita_go_conf import ListenTaskConf
from common.utilities.util_apscheduler import add_ssh_date_job, add_local_date_job, get_job
from database.sql_db.repositories import apscheduler_repository, listen_task_repository

logger = Log.get_logger('active_listen_task')


def _listen_run_job_id(channel_name, source_job_id, message_uidl):
    identity = f'{channel_name}\0{source_job_id}\0{message_uidl}'.encode('utf-8')
    return f'listen_{hashlib.sha256(identity).hexdigest()[:40]}'


def email_to_run_date_job(email, jobs, channel_name):
    title = email['subject']
    datetime_ = email['datetime'].astimezone().strftime('%Y-%m-%dT%H:%M:%S')
    from_ = email['from']
    desp = email['context']
    env_vars = {'__title__': title, '__from__': from_, '__desp__': desp, '__datetime__': datetime_}
    for job in jobs:
        if job['listen_keyword'] in title:
            run_job_id = _listen_run_job_id(channel_name, job['job_id'], email['uidl'])
            if apscheduler_repository.has_job_result(run_job_id) or get_job(run_job_id):
                logger.info(f'邮件"{email["uidl"]}"对应任务"{job["job_id"]}"已派发，跳过重复触发')
                continue
            logger.info(f'邮件主题"{email["subject"]}"符合"{job["job_id"]}"的关键词检测正在发起任务')
            if job['type'] not in {'local', 'ssh'}:
                logger.error(f'不支持的类型运行类型: {job["type"]}')
                continue
            from common.task_center import dispatch_listen
            dispatch_listen(job['job_id'], run_job_id, env_vars)
            logger.info(f'"{job["job_id"]}"任务已发起')


def active_listen():
    end_datetime = datetime.now().astimezone() - timedelta(seconds=15)
    mapping_listen_job: Dict[str, List[Dict]] = {}
    for activa_listen_job in get_activa_listen_job(job_id=None):
        if not activa_listen_job.status:
            continue
        listen_channels = json.loads(activa_listen_job.listen_channels)
        for listen_channel in listen_channels:
            dict_job = dict(
                job_id=activa_listen_job.job_id,
                listen_keyword=activa_listen_job.listen_keyword,
                type=activa_listen_job.type,
                script_text=activa_listen_job.script_text,
                script_type=activa_listen_job.script_type,
                notify_channels=activa_listen_job.notify_channels,
                extract_names=activa_listen_job.extract_names,
                timeout=activa_listen_job.timeout,
                host=activa_listen_job.host,
                port=activa_listen_job.port,
                username=activa_listen_job.username,
                password=activa_listen_job.password,
            )
            if listen_channel not in mapping_listen_job:
                mapping_listen_job[listen_channel] = [dict_job]
            else:
                mapping_listen_job[listen_channel].append(dict_job)
    for listen_api in get_listen_api_by_name(api_name=None):
        api_name = listen_api.api_name
        api_type = listen_api.api_type
        if not listen_api.enable:
            logger.info(f'"{api_name}"接口监听未启用，跳过')
            continue
        logger.info(f'准备扫描"{api_name}"接口监听消息')
        if api_type == '邮件POP3协议':
            if mapping_listen_job.get(api_name, None) is None:  # 都不需要检测这个通道
                logger.info(f'没有任务配置"{api_name}"接口监听，跳过')
                continue
            if not listen_api.params_json:
                logger.error(f'"{api_name}"的接口未配置')
                continue
            params_json = json.loads(listen_api.params_json)
            last_datetime = listen_task_repository.get_listen_checkpoint(api_name)
            if last_datetime is None:
                last_datetime = datetime.now().astimezone() - timedelta(minutes=ListenTaskConf.PERIOD_MINTUES)
            logger.info(f'监听渠道"{api_name}"的扫描区间为 {last_datetime} 至 {end_datetime}')
            pop3_server = params_json['pop3_server']
            port = params_json['port']
            email_account = params_json['email_account']
            password = params_json['password']
            emails = email_pop3.get_email_context_from_subject_during(
                pop3_server=pop3_server,
                port=int(port),
                emal_account=email_account,
                password=password,
                since_time=last_datetime,
                before_time=end_datetime,
            )
            if emails:
                logger.info(f'接口"{api_name}"发现新增邮件，主题为: {",".join([email["subject"] for email in emails])}')
            else:
                logger.info(f'接口"{api_name}"没有发现新增邮件')
            for email in emails:
                email_to_run_date_job(email, mapping_listen_job[api_name], api_name)
            listen_task_repository.set_listen_checkpoint(api_name, end_datetime)

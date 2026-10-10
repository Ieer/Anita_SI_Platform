"""同步 Windows 排程任務到 APScheduler

Usage (PowerShell):
  python scripts/sync_windows_tasks.py --host 127.0.0.1 --port 18861 --dry-run
  python scripts/sync_windows_tasks.py --host 127.0.0.1 --port 18861

流程:
  1. 讀取 Windows Scheduler (schtasks) 原始資料
  2. 轉換為甘特圖資料 / APScheduler job 結構 (僅結構推導)
  3. 從 APScheduler 取現有 job 列表
  4. 比對: 不存在則新增；存在而 trigger/plan 不同則更新 (reschedule)

限制:
  - 僅處理 Daily/Weekly/Monthly 對應的 cron/interval (簡化規則) ，與 task_transformer 對齊
  - 目前新增的作業統一執行函式使用 placeholder: 'app_apscheduler:run_script'
    未提供實際 script/kwargs → 可後續擴展 (例如把 Windows 任務執行命令塞入腳本欄位)
  - 若需要可在後續調整 run_script 參數策略
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, Any
from pathlib import Path

try:
    import rpyc  # type: ignore
except ImportError:  # pragma: no cover
    rpyc = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dash_components.windows_tasks_manager import windows_tasks_manager  # type: ignore  # noqa: E402
from tools.task_transformer import windows_tasks_to_apscheduler_jobs  # type: ignore  # noqa: E402


def fetch_aps_jobs(conn) -> Dict[str, Dict[str, Any]]:
    raw = conn.root.get_jobs()
    jobs = json.loads(raw) if isinstance(raw, str) else raw
    return {j['id']: j for j in jobs}


def job_diff(local_job: Dict[str, Any], remote_job: Dict[str, Any]) -> bool:
    """判斷是否需要更新 (trigger 或 plan 有差異)。"""
    if local_job['trigger'] != remote_job['trigger']:
        return True
    # plan 內部鍵值轉為字串比對
    a = {k: str(v) for k, v in local_job['plan'].items()}
    b = {k: str(v) for k, v in remote_job['plan'].items()}
    return a != b


def build_add_job_kwargs(job: Dict[str, Any]):
    trigger = job['trigger']
    plan = job['plan']
    script_text = job.get('script_text') or '# auto-synced placeholder'
    # 根據命令推斷 script_type
    lower_cmd = script_text.lower()
    if lower_cmd.endswith('.bat') or lower_cmd.endswith('.cmd'):
        script_type = 'Bat'
    elif lower_cmd.endswith('.py'):
        script_type = 'Python'
    else:
        # Windows 其實多為可執行或含參數命令，這裡統一放入 .bat 腳本方式執行更安全
        script_type = 'Bat'
    kwargs_list = [
        ('type', 'local'),
        ('script_text', script_text),
        ('script_type', script_type),
        ('notify_channels', '[]'),
        ('extract_names', '[]'),
        ('timeout', 300),
        ('env_vars', '{}'),
    ]
    base = {
        'func': 'app_apscheduler:run_script',
        'id': job['id'],
        'kwargs': kwargs_list,
    }
    if trigger == 'interval':
        return {**base, 'trigger': 'interval', 'seconds': plan['seconds']}
    elif trigger == 'cron':
        return {
            **base,
            'trigger': 'cron',
            'minute': plan.get('minute', '*'),
            'hour': plan.get('hour', '*'),
            'day': plan.get('day', '*'),
            'month': plan.get('month', '*'),
            'day_of_week': plan.get('day_of_week', '*'),
            'second': 0,
        }
    else:
        raise ValueError(f'Unsupported trigger: {trigger}')


def build_reschedule_args(job: Dict[str, Any]):
    trigger = job['trigger']
    plan = job['plan']
    if trigger == 'interval':
        return ('interval', {'seconds': plan['seconds']})
    elif trigger == 'cron':
        return ('cron', {
            'minute': plan.get('minute', '*'),
            'hour': plan.get('hour', '*'),
            'day': plan.get('day', '*'),
            'month': plan.get('month', '*'),
            'day_of_week': plan.get('day_of_week', '*'),
            'second': 0,
        })
    else:
        raise ValueError(f'Unsupported trigger: {trigger}')


def sync(host: str, port: int, dry_run: bool = False, verbose: bool = False):
    if rpyc is None:
        raise RuntimeError('rpyc not installed; please add to environment')
    conn = rpyc.connect(host, port)  # type: ignore
    windows_tasks = windows_tasks_manager.get_scheduled_tasks() or []
    local_jobs = windows_tasks_to_apscheduler_jobs(windows_tasks)
    local_jobs_map = {j['id']: j for j in local_jobs}
    remote_jobs_map = fetch_aps_jobs(conn)

    to_add = []
    to_update = []
    for job_id, lj in local_jobs_map.items():
        rj = remote_jobs_map.get(job_id)
        if not rj:
            to_add.append(lj)
        else:
            if job_diff(lj, rj):
                to_update.append(lj)

    if verbose:
        print(f'[INFO] Windows Tasks: {len(windows_tasks)} -> Candidate Jobs: {len(local_jobs)}')
        print(f'[INFO] APScheduler Existing: {len(remote_jobs_map)}')
        print(f'[PLAN] Add: {len(to_add)}, Update: {len(to_update)}')

    if dry_run:
        print('--- DRY RUN RESULT ---')
        print('Add IDs:', [j['id'] for j in to_add])
        print('Update IDs:', [j['id'] for j in to_update])
        return {'add': to_add, 'update': to_update}

    # 執行新增
    for j in to_add:
        add_kwargs = build_add_job_kwargs(j)
        if verbose:
            print('[ADD]', add_kwargs)
        conn.root.add_job(**add_kwargs)
    # 執行更新 (使用 reschedule)
    for j in to_update:
        trigger, plan_args = build_reschedule_args(j)
        if verbose:
            print('[UPDATE]', j['id'], trigger, plan_args)
        conn.root.reschedule_job(j['id'], trigger=trigger, **plan_args)

    if verbose:
        print('[DONE] Sync complete')
    return {'add': to_add, 'update': to_update}


def main():  # pragma: no cover - CLI 主入口
    parser = argparse.ArgumentParser(description='Sync Windows Scheduler tasks into APScheduler')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=18861)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()
    sync(args.host, args.port, dry_run=args.dry_run, verbose=args.verbose)


if __name__ == '__main__':  # pragma: no cover
    main()

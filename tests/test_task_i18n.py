import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from common import task_center
from dash_callback.application.task_ import task_mgmt_c
from dash_callback.application.task_.i18n import task_label, task_message, task_status_label
from server import app


def test_platform_table_translates_presentation_but_preserves_task_data():
    record = {
        'task_id': 'task-1',
        'job_id': 'ui_job',
        'name': '用户定义任务',
        'group_name': '用户团队',
        'claimed': True,
        'enabled': True,
        'trigger': 'interval',
        'plan': {'seconds': 60},
        'next_run': None,
    }
    with app.server.test_request_context('/', headers={'Cookie': 'dash-i18n=en-us'}), \
            patch.object(task_center, 'list_tasks', return_value={
                'rows': [record], 'total': 1, 'online': True,
            }), \
            patch.object(task_center, 'task_capabilities', return_value={'edit', 'run', 'pause', 'logs'}):
        rows, _, _ = task_mgmt_c.load_platform(object())

    assert rows[0]['plan_label'] == 'Every 60s'
    assert rows[0]['next_run'] == 'Not scheduled'
    assert rows[0]['name'] == '用户定义任务'
    assert rows[0]['group_name'] == '用户团队'
    assert [action['content'] for action in rows[0]['actions']] == ['Edit', 'Run', 'Pause', 'Logs']


def test_task_messages_and_status_labels_translate_only_known_application_text():
    with app.server.test_request_context('/', headers={'Cookie': 'dash-i18n=en-us'}):
        assert task_label('新建平台任务') == 'Create platform task'
        assert task_message('调度器已接收请求，不代表运行成功。') == (
            'The scheduler accepted the request; this does not mean the run succeeded.'
        )
        assert task_status_label('failed') == 'Failed'
        raw_log = '用户日志：任务失败，凭据=do-not-translate'
        assert task_message(raw_log) == raw_log

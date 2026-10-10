"""Compatibility helpers for callers of the retired RPA scheduler page.

This module intentionally registers no Dash callbacks and exposes no execution path.
"""

from common import task_center
from common.utilities.util_menu_access import get_menu_access

TABLE_STORE_ID = 'rpa-scheduler-store'
REFRESH_INTERVAL_ID = 'rpa-scheduler-refresh-interval'


class _LegacyAccess:
    def __init__(self, access):
        self.user_name = access.user_name
        self._access = access

    def has_access(self, permission):
        if permission == task_center.PAGE_PERMISSION:
            return self._access.has_access('rpa_scheduler') or self._access.has_access('任务管理-页面')
        return self._access.has_access(permission)


def load_jobs(*_args, **_kwargs):
    """Return safe task rows for legacy integrations; scripts and credentials are excluded."""
    result = task_center.list_tasks(_LegacyAccess(get_menu_access()), engine='platform', page=1, page_size=100)
    return [{
        'job_id': row['job_id'], 'task_id': row.get('task_id'), 'name': row['name'],
        'trigger': row.get('trigger'), 'plan': row.get('plan'), 'next_run': row.get('next_run'),
        'enabled': row.get('enabled'), 'claimed': row.get('claimed'), 'type': row.get('type'),
    } for row in result['rows']]

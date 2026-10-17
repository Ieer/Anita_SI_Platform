import json
from datetime import datetime
from uuid import uuid4

import dash
from dash import Input, Output, State, dcc, html
import feffery_antd_components as fac
from common import task_center
from common.utilities.util_menu_access import get_menu_access
from server import app
from .i18n import task_label as t_task, task_message, task_operation_label, task_timestamp


def _access():
	return get_menu_access()


def _time_label(value):
	return task_timestamp(value) if value else t_task('未计划')


def _plan_label(row):
	plan = row.get('plan') or {}
	if row.get('trigger') == 'cron':
		return ' '.join(str(plan.get(key, '*')) for key in
						('second', 'minute', 'hour', 'day', 'month', 'day_of_week'))
	if row.get('trigger') == 'interval':
		unit_labels = {
			'weeks': '周期单位-周', 'days': '周期单位-天', 'hours': '周期单位-小时',
			'minutes': '周期单位-分钟', 'seconds': '周期单位-秒',
		}
		return t_task('每') + ' ' + ' '.join(
			f"{value}{t_task(unit_labels[key])}" for key, value in plan.items()
			if key in unit_labels and value)
	return str(plan.get('run_date') or row.get('trigger') or '—')


def load_platform(access, keyword='', status='', team='', task_type='', page=1, page_size=20, task_id=None):
	result = task_center.list_tasks(access, engine='platform', keyword=keyword or '',
									status=status or '', page=page, page_size=page_size,
									team=team or '', task_type=task_type or '', task_id=task_id)
	rows = []
	for record in result['rows']:
		task_id = record.get('task_id')
		actions = []
		capabilities = set()
		if task_id and record.get('claimed'):
			capabilities = task_center.task_capabilities(access, task_id)
			actions = [{'content': label, 'type': 'link', 'custom': f'{operation}:{task_id}',
						'danger': operation == 'delete'} for label, operation in [
							(t_task('编辑'), 'edit'), (t_task('运行'), 'run'),
							(t_task('暂停') if record['enabled'] else t_task('恢复'),
								 'pause' if record['enabled'] else 'resume'), (t_task('日志'), 'logs'),
							(t_task('删除'), 'delete')]
						 if operation in capabilities]
		rows.append(dict(record, key=task_id or record['job_id'], actions=actions,
						 group_name=record.get('group_name') or t_task('未认领'), plan_label=_plan_label(record),
						 next_run=_time_label(record.get('next_run')),
						 state=t_task('状态未知') if record.get('guard_state') == 'unknown' else
						 t_task('运行中') if record.get('active_run_id') else
						 t_task('启用') if record['enabled'] else t_task('已暂停')))
		if task_id and 'resolve' in capabilities:
			actions.append({'content': t_task('核实定义'), 'type': 'link', 'custom': f'resolve_definition:{task_id}'})
		if task_id and 'transfer' in capabilities:
			actions.append({'content': t_task('转移团队'), 'type': 'link', 'custom': f'transfer:{task_id}'})
	return rows, result['total'], result


@app.callback(
	Output('tc-platform-table', 'data'), Output('tc-platform-pagination', 'total'),
	Output('tc-platform-summary', 'children'), Output('tc-platform-service-notice', 'children'),
	Input('tc-platform-init', 'n_intervals'), Input('tc-platform-refresh', 'nClicks'),
	Input('tc-platform-search', 'value'), Input('tc-platform-status', 'value'),
	Input('tc-platform-team', 'value'), Input('tc-platform-type', 'value'),
	Input('tc-platform-pagination', 'current'), Input('tc-platform-pagination', 'pageSize'),
	State('tc-platform-query', 'data'),
)
def refresh_platform(_init, _refresh, keyword, status, team, task_type, page, page_size, query):
	try:
		access = _access()
		try:
			if dash.ctx.triggered_id in {'tc-platform-search', 'tc-platform-status', 'tc-platform-team', 'tc-platform-type'}:
				page = 1
				dash.set_props('tc-platform-pagination', {'current': 1})
		except dash.exceptions.MissingCallbackContextException:
			pass
		rows, total, result = load_platform(access, keyword, status, team, task_type, page or 1, page_size or 20,
			(query or {}).get('task_id'))
		dash.set_props('tc-platform-team', {'options': [{'label': t_task('全部团队'), 'value': ''},
													   *task_center.list_teams(access)]})
		notice = None if result['online'] else fac.AntdAlert(
			message=t_task('调度服务未连接，显示已认领任务。'), type='warning', showIcon=True)
		return rows, total, html.Span(f'{total} {t_task("个匹配任务")}'), notice
	except Exception as exc:
		return dash.no_update, dash.no_update, dash.no_update, fac.AntdAlert(
			message=task_message(str(exc)), type='error', showIcon=True)


def _editor(values=None, teams=None, task_id=None):
	values = values or {}
	plan = values.get('plan') or {}
	fields = [
		(t_task('任务名称'), fac.AntdInput(id='tc-edit-job-id', value=values.get('job_id', ''), disabled=bool(task_id))),
		(t_task('团队'), fac.AntdSelect(id='tc-edit-team', value=values.get('group_name'), options=teams or [])),
		(t_task('触发方式'), fac.AntdSelect(id='tc-edit-trigger', value=values.get('trigger', 'interval'),
			options=[{'label': label, 'value': value} for label, value in
					 [(t_task('周期'), 'interval'), ('Cron', 'cron'), (t_task('单次'), 'date'), (t_task('监听'), 'listen')]])),
		(t_task('时区'), fac.AntdInput(id='tc-edit-timezone', value=values.get('timezone', 'Asia/Shanghai'))),
		(t_task('周期秒数'), fac.AntdInputNumber(id='tc-edit-interval-seconds', min=1,
			value=sum(plan.get(key, 0) * factor for key, factor in
					  [('weeks', 604800), ('days', 86400), ('hours', 3600), ('minutes', 60), ('seconds', 1)]) or 60)),
		(t_task('单次时间'), fac.AntdInput(id='tc-edit-run-date', value=str(plan.get('run_date', '')))),
		(t_task('执行位置'), fac.AntdSelect(id='tc-edit-type', value=values.get('type', 'local'),
			options=[{'label': t_task('本机'), 'value': 'local'}, {'label': 'SSH', 'value': 'ssh'}])),
		(t_task('SSH 主机'), fac.AntdInput(id='tc-edit-ssh-host', value=values.get('host', ''))),
		(t_task('SSH 端口'), fac.AntdInputNumber(id='tc-edit-ssh-port', min=1, max=65535, value=values.get('port', 22))),
		(t_task('SSH 用户'), fac.AntdInput(id='tc-edit-ssh-user', value=values.get('username', ''))),
		(t_task('替换密码'), fac.AntdInput(id='tc-edit-ssh-password', mode='password', value='')),
		(t_task('超时秒数'), fac.AntdInputNumber(id='tc-edit-timeout', min=1, max=86400, value=values.get('timeout', 300))),
		(t_task('脚本类型'), fac.AntdSelect(id='tc-edit-script-type', value=values.get('script_type', 'Python'),
			options=[{'label': value, 'value': value} for value in ['Python', 'Bat', 'Shell']])),
	]
	children = [fac.AntdFormItem(component, label=label, id=component.id + '-field') for label, component in fields]
	children += [fac.AntdFormItem(fac.AntdInput(id=f'tc-edit-cron-{suffix}',
				 value=str(plan.get(key, default))), label=t_task(label), id=f'tc-edit-cron-{suffix}-field') for suffix, key, label, default in [
					 ('second', 'second', 'Cron 秒', '0'), ('minute', 'minute', 'Cron 分', '0'),
					 ('hour', 'hour', 'Cron 时', '*'), ('day', 'day', 'Cron 日', '*'),
					 ('month', 'month', 'Cron 月', '*'), ('weekday', 'day_of_week', 'Cron 周', '*')]]
	children += [fac.AntdFormItem(fac.AntdInput(id='tc-edit-script', mode='text-area',
					value=values.get('script_text', ''), autoSize={'minRows': 6, 'maxRows': 12}),
					label=t_task('脚本'), className='tc-form-wide'),
				 fac.AntdFormItem(fac.AntdInput(id='tc-edit-advanced', mode='text-area',
					value=json.dumps({key: values.get(key) for key in
						('extract_names', 'notify_channels', 'env_vars')}, ensure_ascii=False, indent=2),
					autoSize={'minRows': 3, 'maxRows': 8}), label=t_task('提取 / 通知 / 环境变量 (JSON)'), className='tc-form-wide'),
				 fac.AntdFormItem(fac.AntdInput(id='tc-edit-plan-extra', mode='text-area',
					value=json.dumps({key: value for key, value in plan.items() if key in
						{'year', 'week', 'start_date', 'end_date', 'listen_channels', 'listen_keyword'}}, ensure_ascii=False, indent=2),
						autoSize={'minRows': 2, 'maxRows': 5}), label=t_task('计划边界 / 监听渠道与关键词 (JSON)'), className='tc-form-wide'),
				 fac.AntdButton(t_task('预览未来 5 次'), id='tc-edit-preview'),
				 html.Div(id='tc-edit-preview-result', className='tc-form-wide'),
				 dcc.Store(id='tc-edit-expected-digest', data=values.get('expected_digest')),
				 dcc.Store(id='tc-edit-expected-version', data=values.get('expected_version'))]
	return html.Div(children, className='tc-form-grid')


@app.callback(*[Output(f'tc-edit-{name}-field', 'style') for name in [
	'interval-seconds', 'run-date', 'cron-second', 'cron-minute', 'cron-hour', 'cron-day',
	'cron-month', 'cron-weekday', 'ssh-host', 'ssh-port', 'ssh-user', 'ssh-password']],
	Input('tc-edit-trigger', 'value'), Input('tc-edit-type', 'value'))
def editor_field_visibility(trigger, run_type):
	visible = [trigger == 'interval', trigger == 'date', *([trigger == 'cron'] * 6), *([run_type == 'ssh'] * 4)]
	return [{} if show else {'display': 'none'} for show in visible]


@app.callback(Output('tc-platform-editor', 'visible'), Output('tc-platform-editor', 'children'),
			  Output('tc-platform-editor', 'title'), Output('tc-platform-edit-id', 'data'),
			  Input('tc-platform-create', 'nClicks'), Input('tc-platform-table', 'nClicksButton'),
			  State('tc-platform-table', 'clickedCustom'), State('tc-platform-table', 'recentlyButtonClickedRow'),
			  prevent_initial_call=True)
def open_editor(_create, _button, custom, row):
	access = _access()
	if dash.ctx.triggered_id == 'tc-platform-create':
		teams = task_center.list_teams(access)
		return True, _editor({'group_name': teams[0]['value']} if teams else {}, teams), t_task('新建平台任务'), None
	if not custom or ':' not in custom:
		return (dash.no_update,) * 4
	operation, task_id = custom.split(':', 1)
	if operation == 'edit':
		return True, _editor(task_center.get_edit_values(access, task_id),
							 task_center.list_teams(access), task_id), t_task('编辑任务'), task_id
	if operation == 'logs':
		dash.set_props('main-dcc-url', {'pathname': '/task_/task_log', 'search': f'?task_id={task_id}'})
	elif operation in {'run', 'pause', 'resume', 'delete', 'resolve_definition', 'transfer'}:
		dash.set_props('tc-platform-request-key', {'data': {
			'operation': operation, 'task_id': task_id, 'request_key': uuid4().hex}})
		dash.set_props('tc-platform-confirm-summary', {'children':
			f"{t_task('确认操作：')}{task_operation_label(operation)} / {(row or {}).get('name', task_id)}"})
		dash.set_props('tc-platform-transfer-team', {'value': None, 'options': task_center.list_teams(access),
			'style': {'width': '100%'} if operation == 'transfer' else {'display': 'none'}})
		dash.set_props('tc-platform-resolve-reason', {'value': '',
			'style': {} if operation in {'resolve_definition', 'transfer'} else {'display': 'none'}})
		dash.set_props('tc-platform-confirm', {'visible': True})
	return (dash.no_update,) * 4


def _plan(trigger, seconds, second, minute, hour, day, month, weekday, run_date):
	return ({'seconds': seconds} if trigger == 'interval' else {'run_date': run_date}
			if trigger == 'date' else {'second': second, 'minute': minute, 'hour': hour,
									 'day': day, 'month': month, 'day_of_week': weekday})


_PLAN_STATES = [State(f'tc-edit-{name}', 'value') for name in [
	'trigger', 'timezone', 'interval-seconds', 'cron-second', 'cron-minute', 'cron-hour',
	'cron-day', 'cron-month', 'cron-weekday', 'run-date']]


@app.callback(Output('tc-edit-preview-result', 'children'), Input('tc-edit-preview', 'nClicks'),
			  *_PLAN_STATES, State('tc-edit-plan-extra', 'value'), prevent_initial_call=True)
def preview_editor(_click, trigger, timezone, seconds, second, minute, hour, day, month, weekday, run_date, plan_extra):
	try:
		if trigger == 'listen':
			return fac.AntdText(t_task('监听事件触发，没有未来时间点。'))
		plan = _plan(trigger, seconds, second, minute, hour, day, month, weekday, run_date)
		plan.update(json.loads(plan_extra or '{}'))
		return fac.AntdText(' · '.join(task_timestamp(point, timezone)
			for point in task_center.preview_schedule(trigger, plan, timezone)))
	except Exception as exc:
		return fac.AntdText(task_message(str(exc)), type='danger')


@app.callback(Output('tc-platform-editor', 'visible', allow_duplicate=True),
			  Output('tc-platform-notice', 'children', allow_duplicate=True),
			  Input('tc-platform-editor', 'okCounts'), State('tc-platform-edit-id', 'data'),
			  State('tc-edit-job-id', 'value'), State('tc-edit-team', 'value'), *_PLAN_STATES,
			  *[State(f'tc-edit-{name}', 'value') for name in [
				  'type', 'ssh-host', 'ssh-port', 'ssh-user', 'ssh-password', 'timeout', 'script-type', 'script']],
			  State('tc-edit-expected-digest', 'data'), State('tc-edit-advanced', 'value'),
			  State('tc-edit-plan-extra', 'value'), State('tc-edit-expected-version', 'data'), prevent_initial_call=True)
def save_editor(_ok, task_id, job_id, group_name, trigger, timezone, seconds, second, minute,
				hour, day, month, weekday, run_date, run_type, host, port, username, password,
				timeout, script_type, script_text, digest, advanced, plan_extra, expected_version=None):
	values = dict(job_id=job_id, engine='platform', group_name=group_name, trigger=trigger,
				  timezone=timezone, plan=_plan(trigger, seconds, second, minute, hour, day, month, weekday, run_date),
				  type=run_type, host=host, port=port, username=username, password=password or '',
				  timeout=timeout, script_type=script_type, script_text=script_text or '', expected_digest=digest)
	try:
		if expected_version is not None:
			values['expected_version'] = expected_version
		configuration = json.loads(advanced or '{}')
		extra = json.loads(plan_extra or '{}')
		if not isinstance(configuration, dict) or set(configuration) - {'extract_names', 'notify_channels', 'env_vars'}:
			raise ValueError('高级配置必须为提取、通知、环境变量组成的JSON对象。')
		if not isinstance(extra, dict) or set(extra) - {'year', 'week', 'start_date', 'end_date', 'listen_channels', 'listen_keyword'}:
			raise ValueError('计划边界配置无效。')
		values.update(configuration)
		values['plan'] = extra if trigger == 'listen' else {**values['plan'], **extra}
		task_center.save_task(_access(), values, task_id)
		dash.set_props('tc-platform-refresh', {'nClicks': int(datetime.now().timestamp() * 1_000_000)})
		return False, fac.AntdAlert(message=t_task('已保存并暂停。核对后请显式恢复。'), type='success', showIcon=True)
	except Exception as exc:
		return True, fac.AntdAlert(message=task_message(str(exc)), type='error', showIcon=True)


@app.callback(Output('tc-platform-confirm', 'visible', allow_duplicate=True),
			  Output('tc-platform-notice', 'children', allow_duplicate=True),
			  Input('tc-platform-confirm', 'okCounts'), State('tc-platform-request-key', 'data'),
			  State('tc-platform-resolve-reason', 'value'),
			  State('tc-platform-transfer-team', 'value'),
			  running=[(Output('tc-platform-confirm', 'confirmLoading'), True, False)], prevent_initial_call=True)
def confirm_platform_action(_ok, action, reason=None, destination=None):
	try:
		if not action:
			return (dash.no_update,) * 2
		if action['operation'] == 'run':
			result = task_center.request_run(_access(), action['task_id'], action['request_key'])
		elif action['operation'] == 'resolve_definition':
			result = task_center.resolve_task_definition(_access(), action['task_id'], reason)
		elif action['operation'] == 'transfer':
			result = task_center.transfer_task(_access(), action['task_id'], destination, reason)
		else:
			result = task_center.operate_task(_access(), action['task_id'], action['operation'],
											  request_key=action['request_key'])
		dash.set_props('tc-platform-refresh', {'nClicks': int(datetime.now().timestamp() * 1_000_000)})
		return False, fac.AntdAlert(message=task_message(result.get('message') or '已提交'),
									type='warning' if result.get('status') == 'unknown' else 'success', showIcon=True)
	except Exception as exc:
		return False, fac.AntdAlert(message=task_message(str(exc)), type='error', showIcon=True)


@app.callback(Output('tc-platform-claim', 'visible'), Output('tc-platform-claim', 'children'),
			  Input('tc-platform-claim-open', 'nClicks'), prevent_initial_call=True)
def open_claim(_click):
	access = _access()
	rows = task_center.list_tasks(access, page_size=100)['rows']
	return True, html.Div([
		fac.AntdSelect(id='tc-claim-job', options=[{'label': row['name'], 'value': row['job_id']}
					   for row in rows if not row.get('claimed')], placeholder=t_task('选择旧任务')),
		fac.AntdSelect(id='tc-claim-team', options=task_center.list_teams(access), placeholder=t_task('归属团队'))])


@app.callback(Output('tc-platform-claim', 'visible', allow_duplicate=True),
			  Output('tc-platform-notice', 'children', allow_duplicate=True),
			  Input('tc-platform-claim', 'okCounts'), State('tc-claim-job', 'value'),
			  State('tc-claim-team', 'value'), prevent_initial_call=True)
def claim_platform(_ok, job_id, group_name):
	try:
		task_center.claim_task(_access(), job_id, group_name)
		dash.set_props('tc-platform-refresh', {'nClicks': int(datetime.now().timestamp() * 1_000_000)})
		return False, fac.AntdAlert(message=t_task('已认领并暂停。'), type='success', showIcon=True)
	except Exception as exc:
		return True, fac.AntdAlert(message=task_message(str(exc)), type='error', showIcon=True)


@app.callback(Output('tc-platform-grant', 'visible'), Output('tc-platform-grant', 'children'),
			  Input('tc-platform-grant-open', 'nClicks'), prevent_initial_call=True)
def open_grant(_click):
	return True, html.Div([
		fac.AntdSelect(id='tc-grant-team', options=task_center.list_teams(_access()), placeholder=t_task('团队')),
		fac.AntdInput(id='tc-grant-user', placeholder=t_task('团队成员用户名')),
		fac.AntdCheckboxGroup(id='tc-grant-operations', value=['view'], options=[
			{'label': name, 'value': name} for name in sorted(task_center.ALLOWED_OPERATIONS)])])


@app.callback(Output('tc-platform-grant', 'visible', allow_duplicate=True),
			  Output('tc-platform-notice', 'children', allow_duplicate=True),
			  Input('tc-platform-grant', 'okCounts'), State('tc-grant-team', 'value'),
			  State('tc-grant-user', 'value'), State('tc-grant-operations', 'value'), prevent_initial_call=True)
def save_grant(_ok, group_name, user_name, operations):
	try:
		task_center.grant_team(_access(), group_name, user_name, operations or [])
		return False, fac.AntdAlert(message=t_task('团队授权已更新。'), type='success', showIcon=True)
	except Exception as exc:
		return True, fac.AntdAlert(message=task_message(str(exc)), type='error', showIcon=True)

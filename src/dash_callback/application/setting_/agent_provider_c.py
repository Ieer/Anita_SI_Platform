import re

from dash import Input, Output, State, ctx, no_update, set_props
from dash.exceptions import PreventUpdate

from common.agent import AgentError, model_status, test_provider_connection
from common.agent_providers import registered_provider_ids
from common.utilities.util_logger import Log
from common.utilities.util_menu_access import get_menu_access
from database.sql_db.repositories import agent_provider_repository
from server import app
from i18n import t__setting


logger = Log.get_logger(__name__)


def translate_provider_message(message):
    patterns = (
        (r'最多保存 (\d+) 个 Agent 配置。', '最多保存 {value} 个 Agent 配置。'),
        (r'Provider“(.+)”未在服务端注册。', 'Provider“{value}”未在服务端注册。'),
        (r'未注册的 Agent Provider：(.+)', '未注册的 Agent Provider：{value}'),
        (r'Columbus (Agent ID|Tenant ID|Client ID) 必须是有效 UUID。', 'Columbus {value} 必须是有效 UUID。'),
        (r'请填写有效的 Columbus (Client Secret|Scope)。', '请填写有效的 Columbus {value}。'),
        (r'Columbus (Token 获取|Agent 请求)认证或授权失败，请检查凭证、订阅和 Scope。',
         'Columbus {value}认证或授权失败，请检查凭证、订阅和 Scope。'),
    )
    for pattern, template in patterns:
        match = re.fullmatch(pattern, message)
        if match:
            return t__setting(template).format(value=t__setting(match[1]))
    match = re.fullmatch(r'Columbus (Token 获取|Agent 请求)失败（HTTP (\d+)），请联系管理员。', message)
    if match:
        return t__setting('Columbus {stage}失败（HTTP {code}），请联系管理员。').format(
            stage=t__setting(match[1]), code=match[2])
    return t__setting(message)


def _require_admin():
    access = get_menu_access()
    if not access.has_access('Agent配置-页面'):
        raise AgentError('只有系统管理员可以管理 Agent 配置。')
    return access


def _load_columbus_fields(options):
    for key in ('agent_id', 'tenant_id', 'client_id', 'scope', 'client_secret'):
        set_props('agent-columbus-' + key, {
            'value': '' if key == 'client_secret' else options.get(key, ''),
        })


@app.callback(
    Output('agent-columbus-fields', 'hidden'),
    Output('agent-provider-model-field', 'hidden'),
    Input('agent-provider-kind', 'value'),
)
def show_provider_fields(provider_id):
    _require_admin()
    return provider_id != 'columbus_agent', provider_id == 'columbus_agent'


def _refresh_list(selected=None):
    profiles = agent_provider_repository.list_profiles()
    options = [{'label': row['name'], 'value': row['profile_id']} for row in profiles]
    chosen = selected if any(row['profile_id'] == selected for row in profiles) else (
        profiles[0]['profile_id'] if profiles else None)
    set_props('agent-provider-profile-list', {'options': options, 'value': chosen})
    set_props('agent-provider-profile', {'options': agent_provider_repository.profile_options()})
    set_props('agent-provider-count', {'children': t__setting('{count} / 12 个配置档').format(count=len(profiles))})
    status = model_status(agent_provider_repository.default_profile_id())
    set_props('agent-config-live-status', {'children': status['model'] or t__setting('尚未连接')})
    set_props('agent-config-live-detail', {'children': translate_provider_message(status['message'])})


@app.callback(
    Output('agent-provider-profile-list', 'options'),
    Output('agent-provider-profile-list', 'value'),
    Input('agent-provider-init', 'n_intervals'),
    Input('agent-provider-refresh', 'data'),
    State('agent-provider-profile-list', 'value'),
    prevent_initial_call=True,
)
def initialize_provider_profiles(_interval, _refresh, selected):
    try:
        _require_admin()
        profiles = agent_provider_repository.list_profiles()
        options = [{'label': row['name'], 'value': row['profile_id']} for row in profiles]
        if ctx.triggered_id == 'agent-provider-refresh' and selected in [row['profile_id'] for row in profiles]:
            return options, selected
        return options, profiles[0]['profile_id'] if profiles else None
    except AgentError as error:
        set_props('agent-provider-status', {'children': translate_provider_message(str(error))})
        return [], None
    except Exception:
        logger.exception('Could not load Agent provider profile options.')
        set_props('agent-provider-status', {'children': t__setting('配置列表暂时不可用，请刷新页面或联系管理员。')})
        return [], None


@app.callback(
    Output('agent-provider-edit-id', 'data'),
    Output('agent-provider-name', 'value'),
    Output('agent-provider-kind', 'value'),
    Output('agent-provider-url', 'value'),
    Output('agent-provider-model', 'value'),
    Output('agent-provider-key', 'value'),
    Output('agent-provider-default', 'checked'),
    Output('agent-provider-editor-status', 'children'),
    Input('agent-provider-profile-list', 'value'),
    Input('agent-provider-add', 'nClicks'),
    prevent_initial_call=True,
)
def load_provider_profile(profile_id, add_clicks):
    try:
        _require_admin()
        if ctx.triggered_id == 'agent-provider-add':
            if not add_clicks:
                raise PreventUpdate
            providers = registered_provider_ids()
            provider = 'openai_compatible' if 'openai_compatible' in providers else (
                providers[0] if providers else '')
            _load_columbus_fields({})
            return None, '', provider or None, '', '', '', False, t__setting('新建配置：保存前请先测试连接。')
        if not profile_id:
            raise PreventUpdate
        profile = agent_provider_repository.get_profile(profile_id)
        _load_columbus_fields(profile.get('provider_options', {}))
        return (
            profile['profile_id'], profile['name'], profile['provider_id'],
            profile['base_url'], profile['model'], '', profile['is_default'],
            t__setting('凭证已安全保存；更新凭证时填写新值，留空将保留现有凭证。'),
        )
    except PreventUpdate:
        raise
    except AgentError as error:
        return no_update, no_update, no_update, no_update, no_update, '', no_update, translate_provider_message(str(error))
    except Exception:
        logger.exception('Could not load selected Agent provider profile.')
        return no_update, no_update, no_update, no_update, no_update, '', no_update, t__setting('配置详情暂时不可用。')


@app.callback(
    Output('agent-provider-status', 'children'),
    Output('agent-provider-refresh', 'data'),
    Input('agent-provider-test', 'nClicks'),
    Input('agent-provider-save', 'nClicks'),
    Input('agent-provider-make-default', 'nClicks'),
    Input('agent-provider-delete-confirm', 'confirmCounts'),
    State('agent-provider-edit-id', 'data'),
    State('agent-provider-name', 'value'),
    State('agent-provider-kind', 'value'),
    State('agent-provider-url', 'value'),
    State('agent-provider-model', 'value'),
    State('agent-provider-key', 'value'),
    State('agent-provider-default', 'checked'),
    State('agent-provider-refresh', 'data'),
    State('agent-columbus-agent_id', 'value'),
    State('agent-columbus-tenant_id', 'value'),
    State('agent-columbus-client_id', 'value'),
    State('agent-columbus-scope', 'value'),
    State('agent-columbus-client_secret', 'value'),
    prevent_initial_call=True,
)
def manage_provider_profile(
    test_clicks, save_clicks, default_clicks, delete_clicks,
    profile_id, name, provider_id, base_url, model, api_key, make_default, refresh,
    agent_id=None, tenant_id=None, client_id=None, scope=None, client_secret=None,
):
    try:
        _require_admin()
        options = {
            key: (value or '').strip()
            for key, value in (
                ('agent_id', agent_id), ('tenant_id', tenant_id), ('client_id', client_id),
                ('scope', scope), ('client_secret', client_secret),
            )
        } if provider_id == 'columbus_agent' else {}
        if ctx.triggered_id == 'agent-provider-test':
            if not test_clicks:
                raise PreventUpdate
            settings = agent_provider_repository.prepare_settings(
                profile_id, provider_id, base_url or '', model or '', api_key, options,
            )
            settings.validate()
            result = test_provider_connection(settings)
            return t__setting('{message}（{model}）').format(
                message=translate_provider_message(result['message']), model=result['model']), no_update

        if ctx.triggered_id == 'agent-provider-save':
            if not save_clicks:
                raise PreventUpdate
            saved_id = agent_provider_repository.save_profile(
                profile_id, name, provider_id, base_url, model or '', api_key, make_default is True,
                provider_options=options,
            )
            _refresh_list(saved_id)
            set_props('agent-provider-edit-id', {'data': saved_id})
            set_props('agent-provider-key', {'value': ''})
            set_props('agent-columbus-client_secret', {'value': ''})
            set_props('agent-provider-default', {
                'checked': agent_provider_repository.get_profile(saved_id)['is_default'],
            })
            return t__setting('配置已加密保存。建议测试连接后再用于对话。'), (refresh or 0) + 1

        if ctx.triggered_id == 'agent-provider-make-default':
            if not default_clicks:
                raise PreventUpdate
            if not profile_id:
                raise AgentError('请先选择一个已保存的配置。')
            agent_provider_repository.set_default_profile(profile_id)
            _refresh_list(profile_id)
            set_props('agent-provider-default', {'checked': True})
            set_props('agent-config-live-detail', {'children': t__setting('新建对话将默认使用此服务。')})
            return t__setting('已更新新对话的默认配置。'), (refresh or 0) + 1

        if ctx.triggered_id == 'agent-provider-delete-confirm':
            if not delete_clicks:
                raise PreventUpdate
            agent_provider_repository.delete_profile(profile_id)
            _refresh_list()
            _load_columbus_fields({})
            set_props('agent-provider-edit-id', {'data': None})
            set_props('agent-provider-name', {'value': ''})
            set_props('agent-provider-key', {'value': ''})
            set_props('agent-provider-url', {'value': ''})
            set_props('agent-provider-model', {'value': ''})
            set_props('agent-provider-default', {'checked': False})
            set_props('agent-provider-kind', {'value': 'openai_compatible'})
            return t__setting('配置已删除。'), (refresh or 0) + 1
        raise PreventUpdate
    except PreventUpdate:
        raise
    except AgentError as error:
        return translate_provider_message(str(error)), no_update
    except Exception:
        logger.exception('Agent provider profile operation failed.')
        return t__setting('操作未完成；请检查服务状态后重试。'), no_update

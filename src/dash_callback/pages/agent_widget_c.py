from dash import ALL, Input, Output, State, ctx, no_update, set_props
from dash.exceptions import PreventUpdate

from common.agent import AgentError, ModelSettings, model_status, run_turn
from common.agent_actions import can_propose, decide_task
from common.utilities.util_logger import Log
from common.utilities.util_menu_access import get_menu_access
from database.sql_db.repositories import agent_provider_repository, agent_repository
from dash_view.framework.agent_widget_i18n import translate_agent_message
from dash_view.framework.page_assistant import page_agent_url, validate_page_agent_url
from i18n import t__dashboard
from server import app


logger = Log.get_logger(__name__)


@app.callback(
    Output('page-agent-script', 'jsUrl'),
    Output('page-agent-status', 'children'),
    Output('page-agent-status', 'className'),
    Output('page-agent-launcher', 'disabled'),
    Input('page-agent-launcher', 'n_clicks'),
    prevent_initial_call=True,
)
def load_page_agent(clicks):
    if not clicks:
        raise PreventUpdate
    url = page_agent_url()
    if not validate_page_agent_url(url):
        message = t__dashboard('页面助手地址无效，请联系管理员配置 HTTPS 地址。')
        return no_update, message, 'page-agent-status page-agent-status-error', False
    return (
        f'{url}?autoInit=false',
        t__dashboard('页面助手已加载：可定位、填写和筛选；执行任务仍需平台审批。'),
        'page-agent-status page-agent-status-ready',
        True,
    )


@app.callback(
    Output('agent-conversation-select', 'options'),
    Output('agent-conversation-select', 'value'),
    Output('agent-provider-profile', 'options'),
    Output('agent-provider-profile', 'value'),
    Input('agent-init', 'n_intervals'),
    Input('agent-new-conversation', 'nClicks'),
    prevent_initial_call=True,
)
def initialize_conversations(_intervals, new_clicks):
    access = get_menu_access()
    if not access.has_access('工作台-页面'):
        raise PreventUpdate
    try:
        conversations = agent_repository.list_conversations(access.user_name)
        if not conversations or (ctx.triggered_id == 'agent-new-conversation' and new_clicks):
            selected = agent_repository.create_conversation(access.user_name)
            conversations = agent_repository.list_conversations(access.user_name)
        else:
            selected = conversations[0]['conversation_id']
        profile_options = agent_provider_repository.profile_options()
        profile_id = agent_provider_repository.get_conversation_profile(access.user_name, selected)
        set_props('agent-conversation-title', {'data-profile-id': profile_id or ''})
        return ([{'label': row['title'], 'value': row['conversation_id']} for row in conversations],
                selected, profile_options, profile_id)
    except Exception:
        logger.exception('Could not initialize Agent conversations.')
        set_props('agent-status', {'children': t__dashboard('会话数据库不可用，请联系管理员。')})
        return [], None, [], None


@app.callback(
    Output('agent-messages', 'children'),
    Output('agent-active-conversation', 'data'),
    Output('agent-conversation-title', 'children'),
    Output('agent-prompt', 'value'),
    Output('agent-conversation-title', 'data-conversation-id'),
    Output('agent-status', 'children'),
    Output('agent-provider-profile', 'value', allow_duplicate=True),
    Output('agent-conversation-profile-feedback', 'children'),
    Input('agent-conversation-select', 'value'),
    Input('agent-send', 'nClicks'),
    Input('agent-stream-refresh', 'data'),
    State('agent-active-conversation', 'data'),
    State('agent-prompt', 'value'),
    State('agent-log-consent', 'checked'),
    State('agent-provider-profile', 'value'),
    prevent_initial_call=True,
    running=[
        (Output('agent-send', 'loading'), True, False),
        (Output('agent-prompt', 'disabled'), True, False),
        (Output('agent-conversation-select', 'disabled'), True, False),
        (Output('agent-new-conversation', 'disabled'), True, False),
        (Output('agent-composer', 'style'), {'pointerEvents': 'none'}, {}),
    ],
)
def handle_conversation(selected, clicks, _stream_refresh, active, prompt, consent, profile_id=None):
    from dash_view.framework.agent_widget import render_messages

    access = get_menu_access()
    if not access.has_access('工作台-页面'):
        raise PreventUpdate
    if not selected:
        return (render_messages([]), None, t__dashboard('当前会话'), no_update, None,
                t__dashboard('请新建或选择会话。'), None, '')
    turn_id = None
    try:
        if ctx.triggered_id != 'agent-send':
            messages = agent_repository.read_messages(access.user_name, selected)
            selected_profile = agent_provider_repository.get_conversation_profile(access.user_name, selected)
            set_props('agent-conversation-title', {'data-profile-id': selected_profile or ''})
            status = model_status(selected_profile)
            set_props('agent-model-status', {
                'children': status['model'] or t__dashboard('配置不可用'),
                'className': 'agent-model-ready' if status['ready'] else 'agent-model-offline',
            })
            set_props('agent-setup-hint', {
                'children': [translate_agent_message(status['message'])],
                'hidden': status['ready'],
                'className': 'agent-setup-hint' if not status['ready'] else '',
            })
            status_message = translate_agent_message(status['message'])
            return (render_messages(messages, access.user_info), selected, t__dashboard('当前会话'), '',
                    selected, '' if status['ready'] else status_message, selected_profile,
                    t__dashboard('此对话使用：{model}。切换服务后会重新建立远端 Agent 会话。').format(
                        model=status['model']) if status['ready'] else status_message)
        if not clicks:
            raise PreventUpdate
        if active != selected:
            raise AgentError('会话正在切换，请稍后再发送。')
        set_props('agent-progress', {'children': t__dashboard('正在处理请求…')})
        selected_profile = profile_id or agent_provider_repository.get_conversation_profile(
            access.user_name, selected)
        settings = (agent_provider_repository.resolve_profile_settings(selected_profile)
                    if selected_profile and selected_profile != 'environment'
                    else ModelSettings.from_environment())
        turn_id, history = agent_repository.begin_turn(access.user_name, selected, prompt)
        provider_state = agent_repository.get_provider_state(
            access.user_name, selected, settings.remote_session_key)
        remote_id = agent_repository.get_remote_session(
            access.user_name, selected, settings.remote_session_key)
        result = run_turn(prompt, access, history, allow_log_export=consent is True,
                          access_provider=get_menu_access, remote_conversation_id=remote_id,
                          profile_id=selected_profile, provider_state=provider_state)
        current_access = get_menu_access()
        if not current_access.has_access('工作台-页面') or current_access.user_name != access.user_name:
            raise AgentError('访问权限已变更，请重新登录。')
        agent_repository.finish_turn(access.user_name, selected, turn_id, result['answer'],
                              result['trace'], result['model'], proposals=result.get('proposals', ()),
                              provider_id=settings.remote_session_key,
                              provider_state=result.get('provider_state'))
        agent_repository.set_remote_session(
            access.user_name, selected, settings.remote_session_key,
            result.get('remote_conversation_id'))
        messages = agent_repository.read_messages(access.user_name, selected)
        conversations = agent_repository.list_conversations(access.user_name)
        set_props('agent-conversation-select', {'options': [
            {'label': row['title'], 'value': row['conversation_id']} for row in conversations]})
        set_props('agent-model-status', {'children': result['model'], 'className': 'agent-model-ready'})
        set_props('agent-response-event', {'children': result['answer'], 'data-turn-id': turn_id})
        return (render_messages(messages, current_access.user_info), selected, t__dashboard('当前会话'), '', selected, '',
                no_update, no_update)
    except AgentError as error:
        message = translate_agent_message(str(error))
    except Exception:
        logger.exception('Agent conversation turn failed.')
        message = t__dashboard('请求未完成，请检查服务连接后重试。')
    if turn_id:
        try:
            agent_repository.finish_turn(access.user_name, selected, turn_id, message, failed=True)
            current_access = get_menu_access()
            if current_access.has_access('工作台-页面') and current_access.user_name == access.user_name:
                messages = agent_repository.read_messages(access.user_name, selected)
                return (render_messages(messages, current_access.user_info), selected,
                        t__dashboard('当前会话'), no_update,
                        selected, message, no_update, message)
        except Exception:
            pass
    return no_update, no_update, no_update, no_update, no_update, message, no_update, message


@app.callback(
    Output('agent-conversation-profile-feedback', 'children', allow_duplicate=True),
    Input('agent-provider-profile', 'value'),
    State('agent-conversation-select', 'value'),
    prevent_initial_call=True,
)
def select_conversation_provider(profile_id, conversation_id):
    if not conversation_id or ctx.triggered_id != 'agent-provider-profile':
        raise PreventUpdate
    try:
        access = get_menu_access()
        if not access.has_access('工作台-页面'):
            raise AgentError('没有工作台权限。')
        agent_provider_repository.set_conversation_profile(access.user_name, conversation_id, profile_id)
        status = model_status(profile_id)

        set_props('agent-conversation-title', {'data-profile-id': profile_id or ''})
        set_props('agent-model-status', {
            'children': status['model'] or t__dashboard('配置不可用'),
            'className': 'agent-model-ready' if status['ready'] else 'agent-model-offline',
        })
        set_props('agent-setup-hint', {
            'children': [translate_agent_message(status['message'])],
            'hidden': status['ready'],
            'className': 'agent-setup-hint' if not status['ready'] else '',
        })
        if not status['ready']:
            return translate_agent_message(status['message'])
        return t__dashboard('此对话使用：{model}。切换服务后会重新建立远端 Agent 会话。').format(
            model=status['model'])
    except AgentError as error:
        current_profile = agent_provider_repository.get_conversation_profile(
            get_menu_access().user_name, conversation_id)
        set_props('agent-provider-profile', {'value': current_profile})
        return translate_agent_message(str(error))
    except Exception:
        return t__dashboard('配置切换未完成，请刷新会话后重试。')


@app.callback(
    Output('agent-send', 'disabled'),
    Input('agent-active-conversation', 'data'),
    Input('agent-provider-profile', 'value'),
    prevent_initial_call=True,
)
def update_send_state(conversation_id, profile_id):
    return not conversation_id or not model_status(profile_id)['ready']


@app.callback(
    Output('agent-approvals', 'children'),
    Input('agent-active-conversation', 'data'),
    Input('agent-messages', 'children'),
    prevent_initial_call=True,
)
def refresh_approvals(conversation_id, _messages):
    from dash_view.framework.agent_widget import render_actions

    access = get_menu_access()
    if not access.has_access('工作台-页面') or not conversation_id:
        return []
    try:
        return render_actions(agent_repository.read_actions(access.user_name, conversation_id), can_propose(access))
    except Exception:
        return t__dashboard('审批记录不可用，请刷新会话。')


@app.callback(
    Output('agent-approvals', 'children', allow_duplicate=True),
    Output('agent-approval-status', 'children'),
    Input({'type': 'agent-action-decision', 'action_id': ALL, 'decision': ALL}, 'n_clicks'),
    State({'type': 'agent-action-consent', 'action_id': ALL}, 'checked'),
    State({'type': 'agent-action-consent', 'action_id': ALL}, 'id'),
    State('agent-active-conversation', 'data'),
    State('agent-conversation-select', 'value'),
    prevent_initial_call=True,
    running=[(Output('agent-approvals', 'style'), {'pointerEvents': 'none'}, {})],
)
def handle_action(clicks, consents, consent_ids, active, selected):
    from dash_view.framework.agent_widget import render_actions

    triggered = ctx.triggered_id
    if not any(clicks or []) or not isinstance(triggered, dict):
        raise PreventUpdate
    access = get_menu_access()
    if not access.has_access('工作台-页面'):
        raise PreventUpdate
    try:
        if not active or active != selected:
            raise AgentError('会话正在切换，请稍后再审批。')
        action_id = triggered.get('action_id')
        decision = triggered.get('decision')
        if decision not in {'approve', 'reject'}:
            raise AgentError('审批决定无效。')
        if decision == 'approve' and not any(
                identifier.get('action_id') == action_id and checked is True
                for identifier, checked in zip(consent_ids or [], consents or [])):
            raise AgentError('请先核对任务脚本及影响。')
        result = decide_task(access.user_name, active, action_id, decision == 'approve', get_menu_access)
        current_access = get_menu_access()
        if current_access.user_name != access.user_name or not current_access.has_access('工作台-页面'):
            raise AgentError('访问权限已变更，请重新登录。')
        return (render_actions(agent_repository.read_actions(access.user_name, active), can_propose(current_access)),
                translate_agent_message(result['message']))
    except AgentError as error:
        return no_update, translate_agent_message(str(error))
    except Exception:
        return no_update, t__dashboard('审批处理未完成。请刷新并核对运行 ID，不要重复提交。')
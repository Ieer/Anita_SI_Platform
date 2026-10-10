from dash import ALL, Input, Output, State, ctx, no_update
from dash.exceptions import PreventUpdate

from common.agent import AgentError, ModelSettings, model_status, run_turn
from common.agent_actions import can_propose, decide_task
from common.utilities.util_menu_access import get_menu_access
from database.sql_db.repositories import agent_repository
from server import app


@app.callback(
    Output('agent-conversation-select', 'options'),
    Output('agent-conversation-select', 'value'),
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
        return [{'label': row['title'], 'value': row['conversation_id']} for row in conversations], selected
    except Exception:
        from dash import set_props

        set_props('agent-status', {'children': '会话数据库不可用，请联系管理员。'})
        return [], None


@app.callback(
    Output('agent-messages', 'children'),
    Output('agent-active-conversation', 'data'),
    Output('agent-conversation-title', 'children'),
    Output('agent-prompt', 'value'),
    Output('agent-status', 'children'),
    Input('agent-conversation-select', 'value'),
    Input('agent-send', 'nClicks'),
    State('agent-active-conversation', 'data'),
    State('agent-prompt', 'value'),
    State('agent-log-consent', 'checked'),
    prevent_initial_call=True,
    running=[
        (Output('agent-send', 'loading'), True, False),
        (Output('agent-prompt', 'disabled'), True, False),
        (Output('agent-conversation-select', 'disabled'), True, False),
        (Output('agent-new-conversation', 'disabled'), True, False),
        (Output('agent-composer', 'style'), {'pointerEvents': 'none'}, {}),
        (Output('agent-progress', 'children'), '正在处理请求…', ''),
    ],
)
def handle_conversation(selected, clicks, active, prompt, consent):
    from dash_view.framework.agent_widget import render_messages

    access = get_menu_access()
    if not access.has_access('工作台-页面'):
        raise PreventUpdate
    if not selected:
        return render_messages([]), None, '当前会话', no_update, '请新建或选择会话。'
    turn_id = None
    try:
        if ctx.triggered_id != 'agent-send':
            messages = agent_repository.read_messages(access.user_name, selected)
            return render_messages(messages, access.user_info), selected, '当前会话', '', ''
        if not clicks:
            raise PreventUpdate
        if active != selected:
            raise AgentError('会话正在切换，请稍后再发送。')
        ModelSettings.from_environment()
        turn_id, history = agent_repository.begin_turn(access.user_name, selected, prompt)
        result = run_turn(prompt, access, history, allow_log_export=consent is True,
                          access_provider=get_menu_access)
        current_access = get_menu_access()
        if not current_access.has_access('工作台-页面') or current_access.user_name != access.user_name:
            raise AgentError('访问权限已变更，请重新登录。')
        agent_repository.finish_turn(access.user_name, selected, turn_id, result['answer'],
                              result['trace'], result['model'], proposals=result.get('proposals', ()))
        messages = agent_repository.read_messages(access.user_name, selected)
        from dash import set_props

        conversations = agent_repository.list_conversations(access.user_name)
        set_props('agent-conversation-select', {'options': [
            {'label': row['title'], 'value': row['conversation_id']} for row in conversations]})
        set_props('agent-model-status', {'children': result['model'], 'className': 'agent-model-ready'})
        set_props('agent-response-event', {'children': result['answer'], 'data-turn-id': turn_id})
        return render_messages(messages, current_access.user_info), selected, '当前会话', '', ''
    except AgentError as error:
        message = str(error)
    except Exception:
        message = '请求未完成，请检查服务连接后重试。'
    if turn_id:
        try:
            agent_repository.finish_turn(access.user_name, selected, turn_id, message, failed=True)
            current_access = get_menu_access()
            if current_access.has_access('工作台-页面') and current_access.user_name == access.user_name:
                messages = agent_repository.read_messages(access.user_name, selected)
                return render_messages(messages, current_access.user_info), selected, '当前会话', no_update, message
        except Exception:
            pass
    return no_update, no_update, no_update, no_update, message


@app.callback(
    Output('agent-send', 'disabled'),
    Input('agent-active-conversation', 'data'),
    prevent_initial_call=True,
)
def update_send_state(conversation_id):
    return not conversation_id or not model_status()['ready']


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
        return '审批记录不可用，请刷新会话。'


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
        return render_actions(agent_repository.read_actions(access.user_name, active), can_propose(current_access)), result['message']
    except AgentError as error:
        return no_update, str(error)
    except Exception:
        return no_update, '审批处理未完成。请刷新并核对运行 ID，不要重复提交。'
import json
import re

from i18n import t__dashboard


_AGENT_MESSAGES = (
    'Enterprise Models 仅限绑定的系统管理员使用。',
    'Enterprise Models 登录文件不可用或已过期，请重新完成企业 AI 登录。',
    'Enterprise Models 登录已失效，请重新完成企业 AI 登录。',
    'Enterprise Models 登录凭证已变更，请新建会话后重试。',
    'Enterprise Models 上轮状态不确定，请新建会话后重试。',
    'Enterprise Models 模型必须属于有效且不重复的候选列表。',
    'Enterprise Models 服务地址必须与服务端配置一致。',
    'Enterprise Models 服务地址未配置或无效。',
    'Enterprise Models 对话状态无效。',
    'Enterprise Models 上游拒绝授权，请检查账号权限。',
    'Enterprise Models 服务请求失败，请稍后检查连接。',
    'Enterprise Models 未返回有效事件流。',
    'Enterprise Models 响应过大。',
    'Enterprise Models 事件过大。',
    'Enterprise Models 回答过长。',
    'Enterprise Models 响应无效或连接中断，请检查企业 AI 登录。',
    'Enterprise Models 响应不完整，请新建会话后重试。',
    'Agent 未配置，请在服务端设置 Provider 和对应访问凭证。',
    '模型地址格式无效。',
    '模型地址必须使用 HTTPS；仅本机服务允许 HTTP。',
    'Internal Agent API APIM Subscription Key 格式无效。',
    '没有工作台权限。',
    '模型请求了未授权或不支持的工具。',
    '工具参数必须是对象。',
    '任务数量必须是 1 到 20 的整数。',
    '任务 ID 无效。',
    '请输入 1 到 4000 字的消息。',
    '连接测试不允许执行工具。',
    '服务已响应，但没有返回可显示的文本。',
    'Agent 已配置，可进行连接测试',
    '配置不可用',
    '会话数据库不可用，请联系管理员。',
    '会话正在切换，请稍后再发送。',
    '访问权限已变更，请重新登录。',
    '请求未完成，请检查服务连接后重试。',
    '请新建或选择会话。',
    '配置切换未完成，请刷新会话后重试。',
    '审批记录不可用，请刷新会话。',
    '会话正在切换，请稍后再审批。',
    '审批决定无效。',
    '请先核对任务脚本及影响。',
    '审批处理未完成。请刷新并核对运行 ID，不要重复提交。',
    '已拒绝，未提交任务。',
    '任务未获运行授权或不在运行白名单中。',
    '调度服务不可用，未提交运行请求。',
    '任务不存在、已暂停或不是本地任务。',
    '任务未认领或团队未授予运行权限。',
    '没有任务运行授权，未提交运行请求。',
    '任务内容已变更，请重新生成提议并核对。',
    '访问权限或运行白名单已变更，未提交运行请求。',
    '运行回执不匹配。',
    '调度服务不可用，无法读取当前任务。',
    '仅生成提议，尚未运行；需用户在审批区批准。',
    '日志数据库不可用。',
    '未找到已完成的运行日志。',
    'Internal Agent API 配置必须是对象。',
    'Internal Agent API 扩展配置过大。',
    'Internal Agent API 请求受到限流，请稍后确认状态再重试。',
    'Internal Agent API 响应过大，无法安全处理。',
    'Internal Agent API 返回的不是有效 JSON；当前 Provider 尚不支持上游 SSE。',
    'Internal Agent API 响应格式无效。',
    'Internal Agent API Token 响应缺少有效凭证或有效期。',
    'Internal Agent API 请求超时；远端任务可能仍在执行，请确认状态后再重试，勿重复提交。',
    'Internal Agent API 连接中断；远端执行状态未知，请确认后再重试。',
    'Internal Agent API 上轮未返回原生 messages，无法可靠续接。请新建对话，并向平台确认 history 协议。',
    'Internal Agent API 已保存的原生历史无效，请新建对话。',
    'Internal Agent API 对话历史过大，请新建对话。',
    'Internal Agent API 返回的对话历史过大，请新建对话。',
    '当前 Internal Agent API Provider 尚不支持上游 SSE；请确认完整结果模式，勿重复提交任务。',
    'Internal Agent API 响应缺少有效 data。',
    'Internal Agent API 任务结果格式无效。',
    'Internal Agent API 返回业务错误，请检查限流、上下文长度或 Agent 插件配置。',
    'Internal Agent API 回答格式无效。',
    'Internal Agent API 任务失败或已取消，请联系管理员确认远端状态。',
    'Internal Agent API 仅返回异步任务，查询协议尚未提供；请在远端确认任务状态，勿重复提交。',
    'Internal Agent API 未返回明确的成功状态，无法确认回答。',
    'Internal Agent API 任务与回答状态不一致，无法确认回答。',
    'Internal Agent API 响应缺少 output。',
    'Internal Agent API 消息内容格式无效。',
    'Internal Agent API 没有返回可显示的回答。',
    'Internal Agent API 返回的原生 messages 格式无效。',
    '模型请求失败或超时，请检查服务端模型配置后重试。',
    '模型没有返回可显示的回答。',
    '已达到工具调用上限，请缩小查询范围后重试。',
    '模型工具调用 ID 无效。',
    '模型工具调用 ID 重复。',
    '模型工具参数过长或格式错误。',
    '模型工具参数不是有效 JSON。',
    '每轮只能提议运行一个任务。',
    '已达到模型调用上限。',
    '外部 Agent Provider 加载失败，请检查服务端模块配置。',
    '正在处理授权工具请求…',
    '工具处理完成，正在整理答复…',
    '正在连接 Internal Agent API；当前使用完整结果模式…',
    '上游未返回 messages；本次回答可显示，但下一轮无法可靠续接。',
)


def translate_agent_message(message):
    if message in _AGENT_MESSAGES:
        return t__dashboard(message)
    match = re.fullmatch(r'Provider“([^”]+)”未在服务端注册。', message)
    if match:
        return t__dashboard('Provider“{provider}”未在服务端注册。').format(provider=match.group(1))
    match = re.fullmatch(r'未注册的 Agent Provider：(.+)', message)
    if match:
        return t__dashboard('未注册的 Agent Provider：{provider}').format(provider=match.group(1))
    match = re.fullmatch(r'Internal Agent API (.+) 必须是有效 UUID。', message)
    if match:
        return t__dashboard('Internal Agent API {label} 必须是有效 UUID。').format(
            label=_translate_internal_agent_label(match.group(1)))
    match = re.fullmatch(r'请填写有效的 Internal Agent API (.+)。', message)
    if match:
        return t__dashboard('请填写有效的 Internal Agent API {label}。').format(
            label=_translate_internal_agent_label(match.group(1)))
    match = re.fullmatch(r'Internal Agent API (.+)认证或授权失败，请检查凭证、订阅和 Scope。', message)
    if match:
        return t__dashboard('Internal Agent API {stage}认证或授权失败，请检查凭证、订阅和 Scope。').format(
            stage=_translate_internal_agent_stage(match.group(1)))
    match = re.fullmatch(r'Internal Agent API (.+)失败（HTTP (\d+)），请联系管理员。', message)
    if match:
        return t__dashboard('Internal Agent API {stage}失败（HTTP {status}），请联系管理员。').format(
            stage=_translate_internal_agent_stage(match.group(1)), status=match.group(2))
    return message


def _translate_internal_agent_label(label):
    return label


def _translate_internal_agent_stage(stage):
    return {
        'Token 获取': t__dashboard('Token 获取'),
        'Agent 请求': t__dashboard('Agent 请求'),
    }.get(stage, stage)


def agent_widget_i18n_data():
    return json.dumps({
        'labels': {
            'openChat': t__dashboard('打开 Agent 对话'),
            'closeChat': t__dashboard('收起 Agent 对话'),
            'expandChat': t__dashboard('放大对话'),
            'restoreChat': t__dashboard('恢复浮窗'),
            'controlsUnavailable': t__dashboard('Agent 对话控件尚未就绪。'),
            'invalidStreamEvent': t__dashboard('Agent 返回了无效的流事件。'),
            'unsupportedStream': t__dashboard('浏览器不支持 Agent 流式响应。'),
            'streamInterrupted': t__dashboard('对话连接中断，请刷新会话后确认消息状态。'),
            'requestFailed': t__dashboard('请求未完成，请稍后重试。'),
            'youPrefix': t__dashboard('你：'),
            'connecting': t__dashboard('正在连接 Agent…'),
            'processing': t__dashboard('正在处理…'),
        },
        'messages': {message: t__dashboard(message) for message in _AGENT_MESSAGES},
        'patterns': [
            {
                'pattern': r'^Provider“([^”]+)”未在服务端注册。$',
                'template': t__dashboard('Provider“{provider}”未在服务端注册。'),
                'groups': ['provider'],
            },
            {
                'pattern': r'^未注册的 Agent Provider：(.+)$',
                'template': t__dashboard('未注册的 Agent Provider：{provider}'),
                'groups': ['provider'],
            },
            {
                'pattern': r'^Internal Agent API (.+) 必须是有效 UUID。$',
                'template': t__dashboard('Internal Agent API {label} 必须是有效 UUID。'),
                'groups': ['label'],
            },
            {
                'pattern': r'^请填写有效的 Internal Agent API (.+)。$',
                'template': t__dashboard('请填写有效的 Internal Agent API {label}。'),
                'groups': ['label'],
            },
            {
                'pattern': r'^Internal Agent API (.+)认证或授权失败，请检查凭证、订阅和 Scope。$',
                'template': t__dashboard('Internal Agent API {stage}认证或授权失败，请检查凭证、订阅和 Scope。'),
                'groups': ['stage'],
                'translations': {'stage': {
                    'Token 获取': t__dashboard('Token 获取'),
                    'Agent 请求': t__dashboard('Agent 请求'),
                }},
            },
            {
                'pattern': r'^Internal Agent API (.+)失败（HTTP (\d+)），请联系管理员。$',
                'template': t__dashboard('Internal Agent API {stage}失败（HTTP {status}），请联系管理员。'),
                'groups': ['stage', 'status'],
                'translations': {'stage': {
                    'Token 获取': t__dashboard('Token 获取'),
                    'Agent 请求': t__dashboard('Agent 请求'),
                }},
            },
        ],
    }, ensure_ascii=False)

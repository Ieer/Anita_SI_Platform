# Agent Provider 接入模板

Agent 对话入口使用 Provider 适配层。页面、用户会话、日志授权和本地任务审批由 SI-Platform 管理；Provider 只负责把一轮问答转换为目标模型或 Agent 平台的请求与响应。

## 已有 Provider

- `openai_compatible` 是默认 Provider，使用 Chat Completions 格式和 `SMART_AGENT_BASE_URL`、`SMART_AGENT_MODEL`、`SMART_AGENT_API_KEY`。支持工具调用和流式文本。
- `enterprise_models` 显示为 **Enterprise Models / 企业模型**，使用同机 Enterprise AI 登录文件和专用 SSE 协议，仅绑定的系统管理员可使用；不注册厂商品牌名称作为 Provider ID。
- `internal_agent` 为内置 Internal Agent API 专用 Provider，使用 OAuth2 client credentials + APIM 订阅凭证，仅支持 JSON 文本完整结果模式。它不使用 OpenAI SDK，也不会执行本地工具。
- 外部 Agent 平台通常有自己的鉴权、请求字段、会话 ID 和流事件。仅在其明确兼容 Chat Completions 时才能直接使用默认 Provider；否则应实现独立适配器。

黑客松开始前，先从目标平台的官方 API 文档确认：API 类型（模型还是 Agent 应用）、认证方式、端点、会话续接字段、流传输格式、结束/错误事件及限流。不要根据平台名称猜测其协议。

## Enterprise Models

本 Provider 使用企业模型专用 SSE 协议，不是通用 OpenAI 兼容接口，也不是下文的 Internal Agent API 应用接口。端点由部署管理员在服务端配置；仓库不包含实际服务地址。必须先获准使用目标账号、服务和模型，并确认服务实现了本适配器要求的协议。

企业 AI 登录客户端与本平台运行在同一台 Windows。管理员在客户端手动登录并完成 MFA，客户端写入登录文件；平台以只读方式读取，不启动登录浏览器、不上传文件，也不把 Token 复制进数据库。文件路径可配置，不绑定安装目录。通过部署环境显式设置以下四项：

```powershell
$env:SMART_AGENT_ENTERPRISE_MODELS_ENDPOINT = 'https://enterprise-api.example.com/v1/chat/stream'
$env:SMART_AGENT_ENTERPRISE_MODELS_TOKEN_FILE = 'C:\ProgramData\EnterpriseAI\Data\token.dat'
$env:SMART_AGENT_ENTERPRISE_MODELS_OWNER = '<对应的平台管理员用户名>'
$env:SMART_AGENT_ENTERPRISE_MODELS_TOKEN_TIMEZONE = '<实际来源的 IANA 时区>'
```

以上域名和路径仅作参考，示例域名会被校验器拒绝，必须替换为获准的实际 HTTPS 完整流端点及登录文件绝对路径。地址不能含账号密码、查询串或片段；不跟随重定向。页面只读显示服务端端点，不能通过提交表单覆盖；文件路径也不能由网页指定。配置缺失时禁用调用。文件路径仅保存在进程环境，不写入配置档；真实端点会作为连接参数保存在本地数据库中，不应提交数据库。

应用不自动加载 `.env`。通过进程环境、服务管理器或密钥管理系统注入真实值；修改配置后重启全部 Web 进程，并重新检查登录及测试连接。Git 忽略本机 `.env.*`、`token.dat` 及其备份，但忽略规则不会移除已经被跟踪的文件。公开模板之前，还需检查历史提交、附件、截图和远端仓库；本次源码清理不改写 Git 历史。

例如来源电脑确实使用中国标准时间时，时区可填写 `Asia/Shanghai`；不得猜测。无时区的 `token_expiry` 按该时区转换为 UTC，已有偏移的值按自身偏移解释。源程序已经提前处理期限，不再次扣五分钟。平台不使用 `last_refresh` 延长有效期。

在 Agent Connections 新建“企业模型”配置，维护候选列表并选择模型；不填写 API Key。内置 `gpt-5-mini`、`claude-sonnet-4-6`、`gemini-3.1-pro-preview` 仅是源码候选，非授权或可用性承诺。候选列表最多 50 项，每项最多 191 字符。保存配置不读取 Token，不等于登录或网络验证成功。“检查企业 AI 登录”仅检查本机文件；“测试连接”会实际发送一次模型请求，可能产生用量。

权限要求同时满足有效 `admin` 角色和指定 OWNER。其他管理员、团队管理员及普通用户不能查看或调用该连接。默认项不可访问时明确拒绝，不自动把历史发送给其他服务；可手动选择其他合法连接。匿名/未授权请求不读取凭证文件。环境配置也受同样限制；部署为 `SMART_AGENT_PROVIDER=enterprise_models` 时，使用同一组部署配置并设置 `SMART_AGENT_MODEL`，专用 ENDPOINT 优先于通用 `SMART_AGENT_BASE_URL`，不使用 `SMART_AGENT_API_KEY` 作为备用。

读取上限 64 KiB，只提取 `access_token` 与 `token_expiry`，不保存/使用 `refresh_token`。缺失、无权限、空 Token、初始化时间、畸形或过期文件均拒绝调用，不回退 `.bak`、旧缓存或其他密钥。401 会记录该凭证版本失效，不自动刷新或重发；403 单独报告授权失败。重新登录产生新 Token 后重新检查状态。请求发送但未完整结束时远端状态不确定，应新建对话，不能盲目继续；凭证轮换建立新的原生上下文，本地消息仍保留但不自动导入新账号。只支持文本问答，不执行本地工具、不支持图片或页面助手的 OpenAI 代理。

**源端限制**：登录客户端可能明文保存 Refresh Token、自动刷新并创建 `.bak`，请按实际客户端确认。平台只读且不刷新，不等于源端也停止刷新；严格“到期只能人工重登”需要另行调整客户端，本实现不修改它。保护整个目录 ACL（含临时/备份文件），客户端身份写、平台专用身份只读；同 Windows 用户运行时不能声称系统级读写隔离。不要放进 Git、静态目录、云同步或普通日志采集。平台不删除源文件，也不能替代远端撤销授权。

自动化使用临时文件、临时数据库和模拟 HTTP；并不代表真实账号、企业条件访问、模型目录或 SSE 格式已完成线上认证。首次真实测试需显式授权。

## Internal Agent API（企业内部 Agent 接口）

此接口调用企业内部已发布的 Agent 应用，使用应用凭证认证，不代表用户交互式登录。界面中文为“企业内部 Agent 接口”，英文为“Internal Agent API”；Provider ID 为 `internal_agent`，实现类为 `InternalAgentProvider`，模块为 `common.internal_agent_provider`。Enterprise Models 的“企业 AI 登录”文件流程与本接口独立。

### 配置

在“系统设置 → Agent 接入”选择 **企业内部 Agent 接口 / Internal Agent API**，填写：

| 字段 | 用途 |
|---|---|
| 服务地址 | 平台提供的服务根地址（包含部署所需协议前缀），适配器追加 `/platform/agent/message` |
| API 凭证 / APIM Subscription Key | 每次 Agent 请求使用的订阅凭证 |
| Agent ID | 已发布 Agent 的 UUID |
| Tenant ID / Client ID | Azure AD 租户及客户端 UUID |
| Scope | 目标环境的资源 Scope，QAS 和 PRD 分开建配置档 |
| Client Secret | OAuth2 客户端密钥，首次必填，编辑留空保留 |

密钥及扩展配置通过 AES-GCM 加密落库。浏览器只收到非密钥配置；Subscription Key 和 Client Secret 不回显。Token 仅在 Web 进程内缓存，按租户、客户端、Scope、Client Secret 隔离，并在过期前刷新；上游 401 使缓存失效，但不会自动重放 Agent 提交。

也支持环境变量：`SMART_AGENT_PROVIDER=internal_agent`、`SMART_AGENT_BASE_URL`、`SMART_AGENT_API_KEY`（订阅凭证），以及 `SMART_AGENT_INTERNAL_AGENT_AGENT_ID`、`SMART_AGENT_INTERNAL_AGENT_TENANT_ID`、`SMART_AGENT_INTERNAL_AGENT_CLIENT_ID`、`SMART_AGENT_INTERNAL_AGENT_CLIENT_SECRET`、`SMART_AGENT_INTERNAL_AGENT_SCOPE`。不需要设置模型名或扩展模块。

### 旧部署迁移

专用 Agent 的中性标识现在是 `internal_agent`，模块为 `common.internal_agent_provider`，凭证文件模式仍是独立的 `enterprise_models`。之前的专用 Provider 标识及环境变量不再注册，不会静默将旧会话发送到新服务。部署前备份本地数据库，使用新标识重新登记连接，将服务根地址设为追加 `/platform/agent/message` 后能得到正确端点的前缀，将应用凭证环境变量改用 `SMART_AGENT_INTERNAL_AGENT_` 前缀，再验证两轮问答。历史本地消息不删除；未完成迁移前不要将旧连接设为默认项。

### 问答与历史

- 使用 `background: false` 提交文本，Token 请求超时 20 秒，Agent 请求超时 90 秒；没有自动任务重试。
- 解析直接 `response.output` 或 `data.taskResult.response.output` 中的 assistant `output_text`；同时检查 HTTP 状态、外层业务错误、任务状态、回答错误和 `detail` 业务错误。`SUCCESS` 不等于忽略内层错误。
- 浏览器仍使用本项目 SSE，但仅显示等待状态和一段最终回答；这不是 Internal Agent API 原生逐字流式。
- 若响应包含原生 `messages`（结果、data、根响应或 response 内），将其加密存储，下一轮原样作为 `history` 发送，不将浏览器聊天历史强行转换为该协议。
- 响应没有 `messages` 时，首轮回答仍可显示，但会记录“不能可靠续接”的状态，下一轮在发请求前明确拒绝。须由平台提供真实连续两轮样例来确认历史字段。
- Internal Agent API 原生历史不回传到浏览器，最多 32 KiB（加密编码后适配 SQLite / MySQL TEXT 存储）。成功回答与历史更新同事务落库；失败不推进历史。按本地会话和配置档隔离，切换、删除或修改连接参数会清除相关原生历史；修改或删除正在处理的配置会被拒绝。
- 新建或切换到 Internal Agent API 时使用新的原生上下文，不自动导入其他 Provider 的本地聊天记录；原有本地记录保留可见。

### 明确未支持的能力

仅返回 `taskId` 或运行中状态时会明确报错，不猜测查询端点，不把 `logTracing` 当结果查询，也不自动再次提交。上游任务可能仍在执行，须在远端确认。当前未实现异步任务续查/取消、原生 SSE、图片上传、远端工具到本地 RPA 的桥接。

连接测试同样调用真实 Agent，可能产生费用或插件动作；优先配置只读 Agent。Internal Agent API 远端插件不受本项目本地审批器约束。

新增表自动创建，不修改旧配置表列：扩展配置表与原生会话状态表复用服务端加密密钥。切勿将内网 API 文档、真实凭证、带敏感数据的原生响应或 Blob 签名 URL 提交进仓库。本实现的自动化验收使用模拟 HTTP 上游，不代表目标 QAS/PRD 已完成真实联调。

## Provider 接口

扩展模块可从 `common.agent_providers` 导入以下类型：

- `AgentProvider.stream_turn(request, tools, execute_tool, streaming=True)`：产生 `AgentStreamEvent`，并以 `return AgentTurnResult(...)` 结束。
- `AgentTurnRequest`：包含当前消息、本地已保存历史、该本地会话对应的远端会话 ID，以及可选的 `provider_state` 原生状态。
- `AgentStreamEvent`：事件类型为 `delta` 或 `status`，可携带文本。
- `AgentTurnResult`：包含最终回答、工具轨迹、展示模型名、审批提议、远端会话 ID，以及可选的 `provider_state`。原生状态由本地会话层加密持久化，不放入浏览器完成事件。
- `register_provider(provider_id, factory)`：注册唯一的 Provider ID。

简化的适配器结构：

```python
from common.agent_providers import AgentStreamEvent, AgentTurnResult, register_provider


class VendorAgentProvider:
    def __init__(self, settings, client=None):
        self.settings = settings

    def stream_turn(self, request, tools, execute_tool, streaming=True):
        # 在这里实现目标平台的认证、HTTP 请求、事件解析和错误转换。
        # 使用 request.remote_conversation_id 续接已有远端会话。
        # 只向界面 yield 文本/状态事件；远端工具必须经本地授权执行器校验。
        answer = "由目标平台返回的最终文本"
        yield AgentStreamEvent('delta', answer)
        return AgentTurnResult(
            answer=answer,
            trace=[],
            model='目标 Agent 名称',
            proposals=[],
            remote_conversation_id='目标平台返回的会话 ID',
        )


register_provider('vendor_agent', VendorAgentProvider)
```

这是接口形状示例，不包含任一厂商的伪造端点或协议实现。生产适配器必须处理超时、非成功 HTTP 状态、畸形响应、断流和平台错误事件，并且不得把密钥或原始认证头放进轨迹、异常文本或用户可见消息。

## 注册和配置

把实现放在服务端可导入的 Python 模块中，再由受信任管理员配置：

```powershell
$env:SMART_AGENT_PROVIDER = 'vendor_agent'
$env:SMART_AGENT_PROVIDER_MODULE = 'your_package.vendor_agent_provider'
$env:SMART_AGENT_API_KEY = '由管理员安全注入的凭证'
$env:SMART_AGENT_BASE_URL = 'https://目标平台提供的 HTTPS API 地址'
```

若目标 Agent 不使用模型名，`SMART_AGENT_MODEL` 可留空；适配器可从目标平台专属服务端环境变量读取 Agent/App ID。HTTP 仅允许回环地址；远程地址必须使用 HTTPS。凭证仅经管理员配置流程或服务端环境注入，不能存进仓库或由聊天提示控制。

Provider 模块只在当前配置的 Provider ID 尚未注册时加载。扩展使用唯一 ID，不应覆盖内置 `openai_compatible` Provider。修改 Provider 配置后重启 Web 进程。

重启并注册 Provider 后，系统管理员可在“系统设置 → Agent 接入”创建配置档、测试服务并设为新对话默认项。用户可以在聊天窗口按会话切换已登记的配置。管理 UI 不允许输入或动态导入 Python 模块名；非兼容协议仍需由部署管理员通过受信任的服务端模块注册。

UI 保存的 API 凭证使用服务端 `CommonConf.ENCRYPT_KEY` 进行 AES-GCM 加密，数据库仅保存密文。该密钥必须安全备份；轮换前须迁移凭证，否则旧密文无法解密。凭证不会从管理 API 返回，编辑时留空表示保留旧值，输入新值表示轮换凭证。部署环境变量配置仍兼容且可在聊天配置选择器中使用。

### 比赛现场切换流程

1. 管理员提前创建主、备平台配置，分别点击“测试连接”，确认当前参数可以返回回答；测试不会开放本地任务工具。
2. 保存配置，按需设为新对话默认服务。保存不等于连接测试通过，也不保证服务持续可用；修改参数后应重新测试。
3. 在聊天窗口的服务选择器切换当前对话使用的配置。选择会持久化，刷新后保留；OpenAI 兼容服务会接收已有本地消息，Internal Agent API 则建立新的原生上下文。敏感信息不要跨平台发送。
4. 切换配置将清除该对话旧的远端会话映射，避免不同平台或同协议的不同配置串用远端会话。处理中不能切换配置。
5. 删除配置须二次确认。受影响的对话迁移到剩余默认服务（没有保存型配置时尝试环境配置），保留本地聊天记录并清除远端会话映射；没有可用配置时需重新配置。

配置管理页面及其回调仅对系统管理员开放；普通用户可以使用获准的已保存配置，但不能读取凭证或修改服务参数。Enterprise Models 额外限制为绑定管理员，不能按普通共享配置处理。

配置页沿用右上角的中英语言切换，覆盖菜单/页签标题、表单、状态、操作反馈和内置 Provider 的校验及连接错误。切换会刷新页面：已保存的配置、默认项和凭证不变，未保存的表单输入不会保留；配置名称、模型 ID、服务地址和第三方 Provider 自定义错误保持原值，不作机器翻译。

### 现场展示建议

- 建议使用 1440×900 或 1366×768 分辨率，浏览器缩放保持 100%，提前选择统一的语言和浅色/深色主题。投影环境下可开启高对比度。
- 页面不再重复展示介绍性大标题，当前默认服务整合到配置档卡片；分区标题、标签和输入使用 14px，辅助说明使用 12px，与其他系统设置页保持一致。
- 配置页在桌面并排展示配置档与连接参数，较窄屏幕改为上下排列；Internal Agent API 参数在宽屏双列、手机单列显示。进入编辑区后，测试/保存按钮与操作反馈吸底显示，避免长表单反复滚动。
- 主演示先展示任务概览，再打开放大的 Agent 对话完成连续问答，最后展示主备配置切换；配置步骤作为补充，不要现场输入或展示真实凭证。
- 浏览器验收使用隔离数据库与模拟上游，只证明界面和交互链路正常。比赛前仍须以目标平台测试账号完成真实网络、鉴权及多轮问答联调。

## 会话和流事件

本地会话 ID 仍由 SI-Platform 创建，并绑定到登录用户。目标平台若有自己的会话 ID，适配器从 `request.remote_conversation_id` 读取它，并在最终 `AgentTurnResult.remote_conversation_id` 返回新值。服务端将远端 ID 按“本地会话 + Provider”单独保存；新建本地会话不得复用旧会话的远端 ID。

浏览器通过同源、登录保护的 `POST /agent_stream` 请求发消息。服务端将 Provider 事件转为 SSE：

```text
event: delta
data: {"text":"增量文本"}

event: status
data: {"text":"正在处理…"}

event: completed
data: {"conversation_id":"本地会话 ID","remote_conversation_id":"远端会话 ID"}
```

失败时使用 `event: error` 和不含密钥的用户可读消息。Provider 不支持流式时可以只 yield 一个最终 `delta`；UI 与本地会话流程不需要改动。断流/失败会持久化错误消息并释放会话处理锁。

## 权限边界

- 本地 `list_tasks`、`read_latest_log` 和 `propose_run_task` 工具仍由 SI-Platform 生成并执行服务端授权检查。
- 不把 SSH 凭证、任务脚本或整份任务数据库发送给外部 Agent。
- 发送日志仍要求用户明确同意；同意不代表所有字段都已脱敏。
- 外部 Agent 的“执行成功”文本不是本地任务执行回执。运行本地任务必须经过现有白名单、任务版本核验和人工审批。
- Provider 的远端会话 ID 是外部系统提供的数据，仅可作为不透明标识使用，不得作为本地用户身份或权限依据。

## 联调与复评清单

新增 Provider 至少提供适配器契约测试及一次使用目标平台测试凭证的端到端验证：

1. 正常认证、错误认证、超时、限流、服务器错误、畸形事件和断流。
2. 多轮问答沿用同一个远端会话；不同本地会话、用户和 Provider 之间不串话。
3. 每个 `delta` 按顺序显示，最终消息完整落库；非流式服务也可完成。
4. 上游错误不会被保存或显示成成功回答；密钥不会出现在异常、轨迹和日志。
5. 用户权限撤销后，本地工具调用被拒绝；未同意时日志不会发送。
6. 远端 Agent 请求运行本地任务时，不会绕过本地审批、白名单和任务内容校验。

架构改造只提供通用接入点，不代表任何特定外部 Agent 平台已经认证或联调。每次新 Provider 落地后，应据以上清单复评具体协议、错误语义和安全边界。

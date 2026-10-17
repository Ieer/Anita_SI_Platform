# 统一任务中心

本文是任务调度、Windows 原生任务、运行记录和维护切换的权威操作说明。测试演示数据与生产任务完全隔离；本指南不授权恢复、迁移或运行真实业务任务。

## 页面与数据边界

| 页面 | 路径 | 管理对象 |
| --- | --- | --- |
| 任务概览 | `/task_/overview` | 已授权的平台任务、计划和运行时间线 |
| 平台任务 | `/task_/task_mgmt` | APScheduler 本机/SSH 任务及 POP3 监听任务 |
| Windows 任务 | `/task_/windows_scheduler` | 当前服务器服务账号可见的原生任务 |
| 运行日志 | `/task_/task_log` | 平台与 Windows 的运行映射、状态和日志 |

旧 `/rpa_/scheduler`、`/rpa_/windows_scheduler` 地址兼容跳转；旧 RPA 页面说明见[历史入口索引](rpa_scheduler_usage.md)。

APScheduler 的持久 Job Store 和 Windows Task Scheduler 各自是任务定义的事实来源。任务中心数据库保存稳定映射、团队归属、运行记录、串行护栏和审计信息，不是可独立修改的第二份调度定义。Windows 任务不会自动导入 APScheduler，不做双向同步，也不会在认领或迁移时默认停用原生任务。

## 权限与团队归属

页面权限只是第一层检查。服务端对用户、角色、团队成员关系和任务动作再次授权；没有明确授权时默认拒绝。全局管理员可跨启用团队管理。团队管理员只能委派自己已有的操作，不能继续委派 `grant`，不能通过授权提升自身权限。

操作范围包括查看、编辑、运行、暂停、恢复、删除、查看/导出日志、认领、核实、授权和转移。日志导出需要独立授权；Agent 将日志发送到模型还需要用户明确同意。页面隐藏按钮不构成服务端访问控制。

历史平台任务须由管理员明确认领并指定团队。未认领任务保持暂停，认领不会自动恢复。任务转移要求暂停、无在途或未决运行以及源/目标团队对应的操作权限；转移原因会记录，历史运行的团队快照不改写。仍被任务或运行记录引用的团队不能硬删除。

## Web 与调度器启动

Web 和 APScheduler 应作为独立进程运行。不要在 Dash 导入、开发热重载或多个 Web worker 中创建 scheduler；APScheduler 3.x 不支持多个实例共享同一个持久 Job Store。

首次初始化前，请复制 [INI 模板](../src/config/anita_go.ini.example) 为 `src/config/anita_go.ini`，填写独立随机的加密密钥、Cookie 密钥和 JWT 密钥。旧 `dashgo.ini` 会在新文件不存在时兼容读取。初始化数据库会创建默认账号 `admin`，初始密码 `admin123`；首次登录后立即修改。该默认密码和旧 SHA-256 密码存储不适用于公网生产环境。

`scripts/start_ap.py` 显式控制调度启动策略：

| 命令参数 | 行为与风险 |
| --- | --- |
| `--scheduler-mode paired_paused` | 启动 Web 和已认证 scheduler RPC，但不恢复调度执行；适合首次检查既有任务 |
| `--scheduler-mode paired_running` | 启动后恢复计划与监听任务；已有到期任务可能产生真实副作用 |
| `--scheduler-mode web_only` | 只启动 Web；连接外部 scheduler 时仍须配置相同 RPC 密钥 |
| `--only-init` | 只初始化数据库，不启动 Web 或 scheduler |
| `--init-db` | 启动服务前显式初始化数据库 |

先为 Web 和 scheduler 配置同一个 `SMART_AGENT_SCHEDULER_RPC_SECRET`，至少 32 个 UTF-8 字节。RPC 默认用于受控的 loopback 通信；HMAC 提供认证，不提供传输加密。不要把端口暴露到公网。启动器不会接管未知进程；发生端口冲突或认证失败时应先查明服务归属。

开发热重载仅用于 debug + `web_only`。联启 scheduler 时不要使用 reloader。由 `--scheduler-mode` 指定的策略优先于 Web 中保存的下次启动策略。Web 中保存启动策略不会热切换当前进程；从暂停切到运行前必须再次核对目标数据库中的任务。

任务概览中的“保持设备唤醒”开关仅对系统管理员显示，并与启动策略使用同一份带版本号的配置。保存后，管理员当前浏览器会申请 Screen Wake Lock，页面切换到后台后会在浏览器允许时重新申请；该能力不绕过操作系统或域策略强制锁屏，也不影响其他浏览器会话。浏览器不支持 Wake Lock 时，页面会显示明确提示。

新建/编辑任务保存后保持暂停。显式恢复之前，核对脚本、执行账号、通知渠道、时区和下次触发点。详细表单示例见[任务配置说明](task.md)。

## 运行状态与未知结果

- 平台运行按任务串行。持久请求键保证相同请求不会重复提交；未决运行期间，不同请求会被拒绝。
- `submitted` 只表示 scheduler 返回了匹配的运行回执；`running` 表示已开始。最终结果以执行器记录为准，不因提交成功而宣称脚本成功。
- Windows `accepted` 只表示原生命令已受理，不代表任务完成。Windows 的 `LastTaskResult` 是原生快照，不是由平台执行器生成的完整日志。
- 如果提交回执不确定、SSH 断链无法确认远端是否结束、或进程恢复无法判断执行结果，运行会标记为 `unknown` 并保持护栏锁定。
- `unknown` 不会自动重试、自动释放或因超时推断为未执行。先到脚本目标系统、Windows 历史或业务系统核对副作用，再由具备核实权限的人员提交非空依据。
- 暂停只阻止未来触发，不会终止已运行脚本。平台串行护栏不拦截 Windows 自身调度或平台外启动。

应用审计用于操作追踪，不是防数据库管理员篡改的不可变审计系统。平台不承诺任意脚本对外部业务副作用的 exactly-once。

## Windows 原生任务

Windows 任务页只管理 Web 所在服务器及服务账户能枚举的原生完整任务路径。暂停、恢复、启动或删除均会影响真实 Windows 任务；测试和演示请使用隔离虚拟机或临时任务。平台不会修改任务 Principal、触发器或 `MultipleInstancesPolicy`。

平台通过原生命令启动任务后记录 `accepted`，不会伪造 stdout 或完成结果。若平台映射处于未知状态，应先在原生 Task Scheduler 核验；未确认原生任务已禁用前，不要另建一个等价 APScheduler 任务。Windows 任务页与原生触发器字段说明见[Windows 任务说明](windows_scheduler.md)。

## 概览图表

概览默认显示未来 24 小时，可选未来 7 天或 30 天；实际运行视图展示对应的过去时间窗口。时间按 `Asia/Shanghai` 显示。统计区会区分平台任务总数与当前页数量。

计划图仅显示权威触发时间点；每个任务在选定窗口最多预览 50 个点，图中聚合计数不等于全部计划明细。实际运行图按 Run ID 独立分行，只绘制有效开始/结束时间对应的真实区间；缺失时间只显示已有标记，不会补造一小时、30 分钟或监听全天区间。缩放还原返回当前选定的窗口。

移动端图表保持局部横向滚动，不扩大整页宽度。没有可绘制运行区间时显示空态，不根据任务数量虚构成功率或持续时间。

## 日志、导出与容量限制

任务日志按服务端 cursor 分批读取；浏览器详情最多保留最近 200000 个字符用于查看。原文导出最多 1000000 个字符，超过上限会截断并明确提示，且需要独立导出权限。Windows CSV 最多 10000 行，导出时重新从服务端读取并校验权限。

SSE 使用有界连接和时长，断开后需续连并重新检查权限；任务日志详情采用有界轮询。反向代理需设置适当超时，部署容量不能据此视为已认证。原文日志可能包含业务敏感信息，导出前应确认访问范围。

## Agent 审批边界

Agent 可选，默认 Provider 使用 OpenAI 兼容 Chat Completions；其他 Agent 平台须按其协议实现并注册服务端 Provider，见 [Agent Provider 接入模板](agent_provider_integration.md)。本地会话历史由平台保管；目标平台若维护远端会话，远端会话 ID 按本地会话和 Provider 隔离保存。Agent 对话使用独立的同源 SSE 流；任务日志 SSE 不用于聊天。

任务查询只提供受权限控制的元数据，不向模型提供 SSH 凭据或任务脚本。日志分析必须有任务日志权限并由用户勾选同意；只读取已完成运行的有限日志片段，脱敏为尽力处理，不保证移除所有敏感内容。会话历史会包含先前的用户和助手文本；不要在对话中加入不应发送给配置模型/Agent 服务的敏感信息。

Agent 执行已有任务默认关闭。显式配置 `SMART_AGENT_RUN_ALLOWLIST` 的 JSON 任务 ID 列表后，Agent 也只能提出已存在、启用中的本机任务审批；不接受通配符，不创建任务、不运行模型生成脚本、不支持 SSH 或暂停任务。用户须再次核对任务与版本、确认脚本及外部影响并批准。审批和调度回执未知时不得重试；管理员应先按固定 Run ID 核验任务日志及业务副作用。

## 隔离演示与浏览器验收

从项目根目录启动：

```powershell
python scripts/demo_task_center.py --port 8097
```

访问 `http://127.0.0.1:8097/demo`。演示使用临时 SQLite、演示管理员、固定合成主机指标、模拟平台/Windows 任务和模拟成功、失败、未知及运行中记录；没有本机指标采样。Anita 使用确定性固定剧本，不创建 OpenAI 客户端；读取模拟日志仍需页面权限和“仅本地”明确同意，任务执行请求只会被拒绝，不生成审批。脚本、scheduler、SSH 与 Windows 操作都不会真实执行。演示自动登录路由仅由此脚本注册，不可用于生产部署。进程退出后临时数据清理，真实数据库和 scheduler 不变。

浏览器回归需要安装 Playwright 与 Chromium，并显式启用：

```powershell
$env:PLATFORM_TASK_UI_PROBE = '1'
$env:PLATFORM_TASK_DEMO_URL = 'http://127.0.0.1:8097'
python -m pytest tests/test_task_center_ui.py -q
Remove-Item Env:\PLATFORM_TASK_UI_PROBE
Remove-Item Env:\PLATFORM_TASK_DEMO_URL
```

覆盖范围见测试函数；完整安全回归可运行 `python -m pytest tests -q`。运行任何测试前应检查它是否会启动隔离的本地脚本或要求显式授权的 Windows 实机测试。测试结果不证明真实 SSH、通知供应商、模型服务或 MySQL 生产迁移已经认证。

### 页面助手演示边界

监控、任务概览和运行日志页面带有页面助手上下文，供 page-agent 了解当前页面、允许的辅助能力和禁止的动作。页面助手需要用户在 Anita 面板中主动点击“加载页面助手”才会加载脚本。

页面助手只负责页面级辅助：

- 允许定位页面元素、填写筛选条件、打开详情和页面跳转；
- 禁止直接调用 scheduler、执行任务、修改权限、读取凭证或绕过审批；
- 任务执行仍由 Anita 生成提议，由平台服务端重新检查页面权限、团队归属、任务版本和运行白名单；
- 默认使用仓库内置的 `page-agent 1.12.4` 文件，由 Dash 静态资源服务提供，断网内网环境不访问 CDN；
- `SMART_AGENT_PAGE_AGENT_URL` 仅用于覆盖为企业审核后的固定 HTTPS 地址，不接受查询参数、内嵌凭证或明文 HTTP。
- page-agent 以 `autoInit=false` 加载，不调用官方公网测试模型；浏览器只访问 SI-Platform 同源模型代理，API Key 不下发到浏览器；
- 同源代理复用平台默认的 `openai_compatible` Provider。内网模型地址可使用 HTTPS，或按现有模型配置规则使用本机回环 HTTP；Columbus 等非 OpenAI 兼容 Provider 不用于页面助手。

完整黑客松链路为“告警分析 → 页面辅助 → 脱敏日志 → 任务提议 → 用户审批 → 运行回执”。页面助手加载失败时，Anita 的服务端 Agent 和现有任务中心仍可独立使用。

## SQLite 维护迁移

`python scripts/migrate_task_center.py` 默认只读检查，不启动 scheduler；SQLite 数据库必须已存在。迁移应用会写数据库并暂停未认领任务，只能在批准的维护窗口执行。迁移前应停止新的外部任务提交、核实所有 running/unknown 运行，并备份数据库、持久 Job Store、配置和凭据。

schema 2 新表时间以 UTC 保存，界面按 `Asia/Shanghai` 显示。旧日志中的无时区时间保留原始本地语义。schema 1 转换必须显式指定旧数据时区，不得假定为 UTC；跨夏令时歧义需先核对。脚本生成备份和暂停清单，首次清单是原启停状态的恢复依据；重复运行不要用后续清单替代首次清单。迁移后保持暂停，审核任务归属、授权、触发时间和日志，再逐项决定是否恢复。回退前仍须核实未决任务，不能绕过护栏直接启动旧版本。

示例命令仅供经过审批的维护人员在维护窗口执行：

```powershell
python scripts/migrate_task_center.py
python scripts/migrate_task_center.py --apply --confirm-maintenance --actor admin --backup-dir E:/Backups/task-center --legacy-timezone Asia/Shanghai
```

`--legacy-timezone` 仅在 schema 1 转换时使用，并应填写旧服务实际采用的时区。迁移应用会产生真实数据库写入；MySQL 应用迁移仍需单独评估和认证，本指南不表示其已验证。

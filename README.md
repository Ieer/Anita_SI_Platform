<p align="center">
  <img src="src/assets/imgs/digital-human-mark.svg" alt="Anita 数字人头像" width="80" height="80" align="middle">
  &nbsp;&nbsp;
  <img src="src/assets/imgs/anita-wordmark.svg" alt="Anita" width="152" height="48" align="middle">
</p>

<h1 align="center">SI-Platform</h1>

<p align="center">基于 Dash 的 Python 运维与任务自动化平台</p>

<p align="center">
  <a href="LICENSE">MIT License</a> · Windows 优先 · Python
</p>

SI-Platform 提供后台管理、权限控制、主机监控和统一任务中心。任务调度、Windows 原生计划任务和可选 AI Agent 有明确的运行边界；演示与测试使用隔离数据，不代表生产系统已完成安全或外部服务认证。

## 三分钟隔离演示

无需模型密钥或外部服务。从项目根目录运行：

```powershell
python scripts/demo_task_center.py --port 8097
```

打开 `http://127.0.0.1:8097/demo`。这是仅监听本机的临时演示服务，使用临时 SQLite；进程退出后数据清理，不启动真实 scheduler，也不读取本机系统指标。

1. 在系统监控中查看预置合成告警：CPU 93%、内存 86%、磁盘 92%。
2. 打开 Anita，询问“分析当前告警”；页面明确标注固定剧本，不是模型生成。
3. 询问“查看票据归档的最近运行日志”。首次请求会拒绝读取；勾选“允许读取脱敏演示日志（仅本地）”后重发，展示权限保护下的本地合成日志和工具记录。
4. 询问“运行任务票据归档”；演示只读分析，不生成审批或运行任务。平台任务、Windows 任务及回执全部为模拟。

[播放 MVP 演示视频（WebM，约 15 秒）](screenshots/si-platform-mvp-demo.webm)。视频由 Playwright 直接录制，展示告警分析、日志授权前后的行为和拒绝执行边界。

重新录制需安装 Playwright 和 Chromium，并先启动上面的隔离演示服务：

```powershell
$env:PLATFORM_TASK_DEMO_URL = 'http://127.0.0.1:8097'
python scripts/record_demo_video.py
```

演示入口只由该脚本注册，不可用于生产部署。它不调用外部模型、scheduler、Windows 任务、SSH 或通知服务；要展示真实集成，需另行配置和认证。

## 功能

- 登录、动态码、OAuth2 Provider，以及用户、角色、团队和页面权限管理。
- 多页面后台、中文/英文界面、个人资料、主题偏好和系统资源监控。
- 普通深色模式采用中性炭黑分层底色与薄荷绿强调色，页面下部带中性灰静态渐隐透视网格；表格、图表和浮层保留实色背景，登录页保留品牌炭黑绿，浅色、高对比和系统强制颜色模式不显示网格。
- 统一任务中心：任务概览、本机或 SSH 平台任务、运行日志、数据提取和通知。
- 周期、Cron、单次和邮件 POP3 监听触发；任务保存后默认暂停，需显式恢复。
- Windows 任务计划程序独立管理。不会自动导入、停用、删除或双向同步 Windows 任务。
- 可选 Agent：默认支持 OpenAI 兼容接口，并提供可扩展 Provider 适配层、连续会话与流式问答；本地工具权限、日志授权和任务执行审批由平台服务端控制。外部 Agent 平台需按其官方协议实现 Provider，未配置时不可用。
- 内置 Internal Agent API 专用 Provider：OAuth2 + APIM 双重鉴权、文本完整结果问答及原生历史续接；与 OpenAI 配置档并存。上游异步任务查询、SSE 和图片协议尚未接入。
- 页面助手：在监控、任务概览和运行日志页面显式加载 page-agent，辅助定位、填写和筛选；任务执行仍必须回到 Anita 审批区，不能由浏览器脚本绕过服务端权限。

## 黑客松智能运维演示

在隔离演示模式中，Anita 负责分析告警、读取脱敏日志和生成任务运行提议；页面助手负责打开页面、定位任务和填写筛选条件；平台继续负责权限校验、审批、任务提交和运行回执。

页面助手默认只有用户点击“加载页面助手”后才加载。项目已内置经过完整性校验的 `page-agent 1.12.4` IIFE 构建，默认从当前 Dash 服务的 `/assets/vendor/page-agent/1.12.4/page-agent.demo.js` 加载，不需要访问公网 CDN，适合断网内网展示。

如需使用企业审核后的其他构建，可显式覆盖地址：

```powershell
$env:SMART_AGENT_PAGE_AGENT_URL = 'https://<approved-intranet-host>/page-agent.<version>.js'
$env:SMART_AGENT_DEMO_MODE = '1'
python scripts/demo_task_center.py --port 8097
```

未配置 `SMART_AGENT_PAGE_AGENT_URL` 时使用仓库内置文件，不进行 CDN 回退。远程覆盖地址必须是无查询参数、无内嵌凭证的 HTTPS 地址。页面助手仅开放定位、填写、筛选和页面跳转能力，不能执行任务、修改权限或读取凭证。

页面助手不会使用 page-agent 的公网测试模型。浏览器通过同源 `/page_agent_llm/v1/chat/completions` 访问平台代理，真实 API Key 只保留在服务端；代理强制使用平台默认的 `openai_compatible` Provider 和服务端模型名，并校验登录、工作台权限、同源请求、请求大小和并发上限。内网展示时，Web 服务器只需能够访问配置的内网 OpenAI 兼容模型。若默认 Provider 是 Internal Agent API 或其他非 OpenAI 兼容协议，Anita 仍可使用，但页面助手会明确提示模型配置不兼容。

推荐展示链路：打开系统监控 → 让 Anita 分析告警 → 加载页面助手进入任务概览 → 打开运行日志并筛选 → 回到 Anita 查看脱敏日志 → 生成任务提议 → 用户核对任务版本并审批 → 查看运行回执。

## Dash 页面模板

页面脚手架生成主题适配的指标、示例记录、搜索/状态筛选和表格，并在筛选回调中校验页面权限。先预览，再生成：

```powershell
python scripts/scaffold_page.py --module analytics --title 分析中心 --subpage 总览:overview:1 --dry-run
python scripts/scaffold_page.py --module analytics --title 分析中心 --subpage 总览:overview:1
```

新人推荐使用完整模式，一次生成页面类型标记、模块级中英文词条和页面契约测试：

```powershell
python scripts/scaffold_page.py `
  --module analytics `
  --title 分析中心 `
  --page-type list `
  --with-i18n `
  --with-tests `
  --subpage 总览:overview:1
python scripts/check_pages.py
```

`--page-type` 当前支持 `list`、`dashboard` 和 `form`，用于标记页面意图并帮助代码评审；它不会替业务页面自动猜测数据模型。`--with-i18n` 会生成 `src/translations/topic_locales/<module>.json`，`--with-tests` 会生成页面渲染契约测试。页面接入真实数据后，应把生成的示例测试扩展为权限、空数据和关键交互测试。

生成的记录明确标为示例数据；业务接入时应替换 fixture 并为写操作实现独立的服务端授权。更多页面约定见[新增页面指南](docs/add_page_guide_full.md)。

## 快速启动

当前提供 Windows 依赖清单。建议使用 Python 3.11 和独立虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements_win.txt
Copy-Item src\config\anita_go.ini.example src\config\anita_go.ini
```

已有 `src/config/dashgo.ini` 配置会在新配置文件不存在时自动兼容读取；新部署请使用 `anita_go.ini`。

首次启动前编辑 `src/config/anita_go.ini`，为 `CommonConf.ENCRYPT_KEY`、`FlaskConf.COOKIE_SESSION_SECRET_KEY` 和 `JwtConf.JWT_SECRET_KEY` 分别生成并填写独立随机值。加密密钥应为 32 个随机字节的 64 位十六进制文本；不要使用模板占位符，也不要把实际配置提交到 Git。

可在 PowerShell 中生成随机值：

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

首次完成依赖安装及密钥配置后，初始化数据库并启动 Web 和独立 scheduler：

```powershell
python scripts/start_ap.py --only-init
.\start.bat
```

首次初始化新库时，终端会隐藏输入并确认唯一的 `admin` 密码（16 到 1024 个字符），不再提供默认密码。无人值守初始化须由部署密钥系统注入 `SMART_AGENT_INITIAL_ADMIN_PASSWORD`；不要把口令写入命令行、配置模板或版本库，初始化后从进程环境清除。已有管理员的密码、状态和角色不会因重复初始化被重置。

`start.bat` 使用项目 `.venv` 中的 Python，显式以 `paired_paused` 模式启动；若当前环境未设置 `SMART_AGENT_SCHEDULER_RPC_SECRET`，会为本次启动临时生成，不写入文件。双击 `start.bat` 也可启动。该脚本不会安装依赖或初始化数据库。Web 默认访问地址为 `http://127.0.0.1:8090`。`paired_paused` 不恢复已有调度任务。确认目标数据库中的任务、脚本和外部影响后，再按[任务中心指南](docs/task_center.md)决定是否恢复。

新密码使用带随机盐的 scrypt 保存；旧 SHA-256 存储在成功密码验证时自动升级，无需改表或重置现有账号。长期不登录的账号应安排密码重置；历史默认口令仍须由管理员主动更改。浏览器登录摘要仍属于凭据，远程部署必须使用 HTTPS。详见[密码迁移说明](docs/security_audit.md#密码存储与迁移)。

只运行 Web、不启动 scheduler：

```powershell
python scripts/start_ap.py --scheduler-mode web_only
```

如果 Web 要连接另一个 scheduler，Web 与 scheduler 必须配置相同的 `SMART_AGENT_SCHEDULER_RPC_SECRET`（至少 32 个 UTF-8 字节）。不要将 RPC 暴露到公网。启动策略、维护迁移和停止行为见[任务中心指南](docs/task_center.md)。

`src/config/anita_go.ini.example` 是配置模板；实际配置文件已被 Git 忽略。`.env.example` 只列出可选环境变量，程序不会自动加载 dotenv 文件。Agent 配置示例和审批限制见下文及[安全说明](docs/security_audit.md)。

## Dash 辅助脚本

从项目根目录运行菜单，也可指定操作用于命令行调用：

```powershell
python tools/dash_helper.py
python tools/dash_helper.py web --port 8090
python tools/dash_helper.py paired-paused
python tools/dash_helper.py demo --port 8097
python tools/dash_helper.py check-pages
python tools/dash_helper.py check-deps
python tools/dash_helper.py test
python tools/dash_helper.py init-db --dry-run
python tools/dash_helper.py init-db --yes
```

脚本只依赖 Python 标准库，优先调用项目 `.venv` 中的解释器，不存在时使用当前解释器。所有操作支持 `--dry-run`，仅显示命令、不执行。无参数且没有交互终端时显示提示并退出，不会挂起等待输入。命令执行保留子脚本退出码。

`web` 显式选择 `web_only`，不会因已保存的启动策略而启动 scheduler；Web 默认使用 Waitress 的 `prod` 模式，可通过 `--mode debug` 切换。`paired-paused` 复用现有启动器，仅启动暂停状态的 scheduler；缺少 RPC 密钥时为子进程临时生成，不显示或写入文件。已有密钥保持不变；连接其他服务仍须配置一致密钥。服务在前台运行，使用 `Ctrl+C` 交由原启动器清理本次自有进程；端口冲突会报错，不接管其他进程。

`init-db` 是写操作，菜单要求输入 `INIT`，命令行要求显式 `--yes`；它调用已有幂等初始化入口，不重置现有管理员密码，也不执行任务中心维护迁移。操作前仍应确认目标数据库并备份。`test` 复用完整测试目录，真实集成测试仍由各自 opt-in 环境变量控制；请勿在生产环境开启这些变量。

参考 Dash 辅助脚本的评估与取舍：

- 保留交互菜单、依赖检查、测试和启动入口聚合，直接复用本项目脚本。
- 不迁入 Conda、Poetry、Bash 或 Gunicorn 管理，适配现有 Windows / `.venv` / Waitress 方案，无新增依赖。
- 不迁入启动前自动清日志、删除并重建管理员、按进程名称批量终止或强杀等操作，避免丢失审计信息和干扰其他实例。
- 不自动安装依赖、修改配置或运行数据库迁移；环境安装沿用上方快速启动步骤，维护迁移沿用任务中心指南。

## 通知与监听通道

在“系统设置 → 通知接口”维护 Server酱、Gewechat、企业微信群机器人和 SMTP 邮件通道；“监听接口”用于配置 POP3 邮箱输入。两个页面使用一致的新增、激活、参数配置布局，支持中英切换及浅色/深色主题。

通知接口、监听接口、Agent 接入与任务中心统一使用公共页签的水平内容边界，与权限管理对齐；页面外层不再叠加水平留白或居中限宽，卡片内部间距保持不变。

### 中英国际化与回归

顶部语言按钮在中文和英文间切换，并通过语言 Cookie 保留选择。系统页面、导航、表单、操作反馈及机器人交互文案按当前请求语言显示；用户名、团队/角色名称、任务名称、聊天内容、外部错误详情和原始日志保留原文。权限键、状态码、接口字段及数据库枚举值不随语言改变。

新增页面或动态反馈应使用对应主题的 `t__*` 函数；含参数的文案先翻译模板，再调用 `format`，不要在模块导入或回调注册时计算翻译。前端脚本使用服务端提供的本地化标签。

- 词条完整性、重复键、占位符、菜单与静态文案检查：`.\.venv\Scripts\python.exe -m pytest tests\test_i18n.py -q`
- 全站浏览器验收（隔离数据库，无真实通知发送）：在 PowerShell 设置 `$env:PLATFORM_AGENT_UI_PROBE='1'` 后，运行 `.\.venv\Scripts\python.exe -m pytest tests\test_agent_ui.py -k all_registered_pages_switch_language -q`。

- 输入通道名称，选择类型；新建通道默认停用。保存参数并测试后，再在“激活通道”勾选启用，取消勾选即停用。
- 邮箱使用密码或服务商授权码。POP3 SSL 新建配置默认端口为 **995**；已有自定义端口不变。监听按现有两分钟周期轮询，任务触发仍需在任务中心绑定相应监听通道。
- 消息测试会实际发送通知，监听测试会实际连接邮箱，现场演示请使用测试接收人和测试账号。
- 密钥输入默认遮罩仅防止屏幕直视，不等同于 Agent 配置的加密存储和不回传机制；现有通道配置仍会加载已保存的凭证到编辑表单。共享屏幕时不要展开凭证明文。
- 界面验收使用隔离数据库与模拟通知/邮箱调用，不代表真实厂商服务已联调通过。

## Agent 配置

Agent 默认不可用，未配置服务时不会生成模拟回答。系统管理员登录后，可在“系统设置 → Agent 接入”添加多个服务配置，先用“测试连接”验证，再设置新对话默认项；用户可在每个对话中独立切换配置。配置档共享于平台，但只有管理员能查看和编辑连接参数，API 凭证通过服务端 AES-GCM 加密保存，不会回传给浏览器。切换对话所用服务会重置该对话的远端会话映射，本地聊天记录与权限边界保持不变。

也可继续使用服务端环境变量方式（例如容器密钥或部署平台的 Secret），该配置显示为兼容选项：

```powershell
$env:SMART_AGENT_BASE_URL = 'https://api.openai.com/v1'
$env:SMART_AGENT_MODEL = '服务商提供的模型名'
$env:SMART_AGENT_API_KEY = '本机环境中的真实密钥'
```

真实密钥不要写入仓库、配置模板或提交记录。UI 配置加密依赖 `CommonConf.ENCRYPT_KEY`，部署时必须设置强随机密钥并做好安全备份；轮换加密密钥前应先迁移已保存的 Agent 凭证。Agent 运行已有任务的功能默认关闭；启用需要审查环境内的任务后，通过服务端 `SMART_AGENT_RUN_ALLOWLIST` 配置明确的任务 ID 列表，并在界面逐次审批。提交结果未知时系统不会自动重试。详见[任务中心指南](docs/task_center.md#agent-审批边界)与[安全说明](docs/security_audit.md)。

需要接入 Dify、LangGraph Server 或其他外部 Agent 平台时，先确认目标平台的 API 类型和会话/流协议；仅当明确兼容 Chat Completions 时才能直接使用默认 Provider。否则按[Agent Provider 接入模板](docs/agent_provider_integration.md)实现服务端适配器、通过 `SMART_AGENT_PROVIDER_MODULE` 注册后重启 Web 服务，再由管理员在 Agent 接入页面创建对应配置档。该模板不代表特定平台已完成联调。

Internal Agent API 已有内置 `internal_agent`，无需扩展模块。管理员选择“Internal Agent API”，填写 v5 服务根地址、APIM Subscription Key、Agent ID、Tenant ID、Client ID、Client Secret 和 Scope。两个凭证加密保存、留空保留且不会回显；Access Token 自动获取和缓存。多轮要求服务返回原生 `messages`：缺少时可显示首轮回答，但下一轮会明确拒绝不可靠的续接。连接测试会真实调用远端 Agent，请优先使用只读 Agent。详见[Internal Agent API 接入边界](docs/agent_provider_integration.md#internal_agent-专用-provider)。

## 测试

```powershell
python -m pytest tests -q
```

浏览器验收为显式 opt-in，需要 Playwright 和 Chromium；具体环境变量和测试范围见[任务中心指南](docs/task_center.md#隔离演示与浏览器验收)。测试结果不代表真实 SSH、通知供应商、第三方 OAuth2、MySQL 生产迁移或 Windows 任务生命周期已联调。

### 可复现交付检查

交付基线为 Windows / Python 3.11。`requirements_win.txt` 和 `requirements_dev.txt` 是人工维护的直接依赖，配套 `.lock` 文件固定完整依赖及下载哈希。锁定不等于安全认证，不支持直接用于其他操作系统或 Python 版本。

建议在单独的验证环境运行，避免修改正在使用的服务环境：

```powershell
python -m venv cache/verify-venv
.\cache\verify-venv\Scripts\python.exe -m pip install --require-hashes -r requirements_dev.lock
.\cache\verify-venv\Scripts\python.exe scripts/check_ci.py
.\cache\verify-venv\Scripts\python.exe -m playwright install --only-shell chromium
.\cache\verify-venv\Scripts\python.exe scripts/check_ci.py --browser-smoke
```

`check_ci.py` 使用临时配置、随机密钥和 SQLite，清除继承的模型、运行白名单与真实探测开关，不读取真实 INI 或启动 scheduler。默认执行依赖一致性、聚焦静态规则、自定义页面契约和完整默认回归；浏览器模式只执行隔离冒烟用例，不代表全站浏览器验收。报告位于 `cache/report/ci/`。

GitHub 工作流分别执行回归和依赖审计，并上传报告。审计发现漏洞或无法完成查询时会失败，不会通过 `continue-on-error` 或忽略漏洞来制造绿灯；启用分支保护时，应同时将两个 job 设为必需检查。

更新依赖时，先审查直接版本与兼容性，再重新生成锁文件并在全新环境运行上述检查：

```powershell
python -m piptools compile --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url --no-emit-trusted-host --output-file=requirements_win.lock requirements_win.txt
python -m piptools compile --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url --no-emit-trusted-host --output-file=requirements_dev.lock requirements_dev.txt
python -m pip_audit -r requirements_win.lock --no-deps --disable-pip --strict --require-hashes
```

## 文档

- [任务中心：页面、权限、启动策略、运行状态、隔离演示与维护迁移](docs/task_center.md)
- [任务配置：周期、Cron、监听、脚本、SSH、通知和日志](docs/task.md)
- [Windows 原生任务行为与安全边界](docs/windows_scheduler.md)
- [新增应用与页面脚手架](docs/add_page_guide_full.md)
- [安全说明与审计状态](docs/security_audit.md)
- [项目结构与开发入口](docs/project_inventory.md)
- [Agent Provider 接入模板、会话映射与联调清单](docs/agent_provider_integration.md)
- [旧 RPA 文档索引](docs/rpa_scheduler_usage.md)

## 界面截图

| 登录 | Agent 浮窗 |
| --- | --- |
| ![SI-Platform 登录页](screenshots/login.png) | ![Anita Agent 演示对话](screenshots/workbench.png) |

| 系统监控 | 任务中心 |
| --- | --- |
| ![系统监控](screenshots/moniter.png) | ![任务中心概览](screenshots/task_overview.png) |

| 任务日志 | 角色管理 |
| --- | --- |
| ![任务运行日志](screenshots/task_log.png) | ![角色管理](screenshots/role.png) |

仓库截图仅为界面样例，不代表实时状态。隔离演示中的监控指标、任务和回执均为合成数据；截图和演示都不构成真实模型、SSH、通知或 Windows 生命周期认证。

## 项目结构

```text
scripts/                 启动、页面脚手架、迁移和隔离演示工具
src/
  app.py                 Web 入口
  app_apscheduler.py     独立 scheduler 服务
  common/                Agent、任务中心及通用服务
  config/                页面注册和本地配置模板
  dash_callback/         Dash 回调
  dash_view/             页面视图
  database/              Peewee 实体与 Repository
  assets/                CSS、JavaScript、图像和前端依赖
  translations/          国际化词条
tests/                   单元、集成和 opt-in 浏览器测试
docs/                    操作、安全和开发文档
screenshots/             隔离演示界面截图
```

## 安全与部署提示

本项目可执行本机或 SSH 脚本，应仅在受控网络和最小权限服务账户下运行。HMAC 调度 RPC 提供认证但不加密传输；需要通过可信网络隔离，不能直接公开端口。默认管理员密码、旧 SHA-256 密码散列及密钥轮换策略都需要部署方处理。当前仓库不声明通过全面安全认证；部署前请阅读[安全说明](docs/security_audit.md)，重新执行依赖与代码扫描并完成环境专项评估。

## 许可证

项目以 MIT License 发布，保留原始许可声明。Three.js 和机器人模型等第三方资源另有各自许可说明，详见 README 中的资源文件及其随附许可证。

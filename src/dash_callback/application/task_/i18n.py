from common.task_center import RUN_STATUS_LABELS, SOURCE_LABELS, display_timestamp
from i18n import t__task


_TASK_FEEDBACK = frozenset({
    '已取消，配置未更改。', '已保存；下次通过统一启动器启动时生效。', '保存失败',
    '高级配置必须为提取、通知、环境变量组成的JSON对象。', '计划边界配置无效。',
    '已保存并暂停。核对后请显式恢复。', '已提交', '已认领并暂停。', '团队授权已更新。',
    '运行已核实', 'Windows 任务已认领。', '操作已受理',
    '登录身份无效。', '账户不存在或已停用。', '没有任务中心页面权限。',
    '没有任务操作页面权限。', '团队不存在或已停用。', '团队不存在或无权访问。',
    '团队未授予此操作。', '任务不存在或无权访问。', '运行不存在或无权访问。',
    '没有任务日志页面权限。', '运行不存在或无权访问。', '页码无效。',
    '任务引擎不匹配。', '不支持的任务引擎。', '任务状态无效。',
    'Windows服务不可用，未生成导出。', '导出超过10000条，请先缩小筛选范围。',
    '任务不存在。', '调度服务不可用，未认领任务。', 'Windows任务不存在。',
    '任务操作无效。', '后端任务已不存在。', '任务详情暂不可用。',
    'Windows任务定义由原生计划程序维护。', '调度服务不可用，不能编辑任务。',
    '后端定义已变化，请管理员核实后重新认领。', '运行请求键无效。', '运行ID无效。',
    '不支持此运行来源。', 'Agent不能提交Windows原生任务。',
    'Windows任务不存在或已禁用。', '调度服务不可用，未提交运行。',
    '任务不存在或已暂停。', '任务定义已变化，请刷新后重试。',
    '调度器提交回执不匹配。', '运行状态在调度提交期间发生变化。',
    '监听任务未认领、已暂停或团队已停用。', '监听定义不可用。', '监听定义已变化。',
    '监听提交结果未知，禁止自动重试。', '不支持的任务操作。',
    '任务正在运行或状态未知，不能删除。', '任务正在运行或状态未知，不能恢复。',
    '操作请求键无效。', '任务状态已变化，请刷新后重试。', '后端任务不存在。',
    '任务操作失败。', '必须提供人工核实依据。', '请先核实未决运行。',
    '请先在原生计划程序确认任务已禁用，再核实解锁；平台不会自动停用它。',
    '后端定义状态已变化，请刷新。', '转移必须提供原因。', '日志游标无效。',
    '团队操作授权列表无效。', '团队授权管理员只能委派自身已有的操作，不能提升权限。',
    '用户不存在或已停用。', '用户不是该团队成员。', '任务定义无效。',
    'Windows任务定义由Windows任务计划程序维护。', '任务ID无效。',
    '编辑不能更改稳定任务ID。', '后端定义已变化，请刷新后重试。',
    '脚本配置无效。', '不支持的脚本类型。', '超时秒数必须是1至86400的整数。',
    '提取配置必须是字符串或对象组成的列表。', '通知渠道必须是字符串列表。',
    '环境变量必须是字符串键和标量值组成的对象。',
    '监听任务环境变量由监听事件生成，不能配置静态环境变量。',
    '监听渠道必须是非空字符串列表。', '监听关键词不能为空。',
    '编辑任务不能同时转移团队。', '任务计划版本已变化，请刷新后重试。',
    '监听与时间调度不能直接互换，请新建任务。',
    '任务正在运行或定义已变化，请刷新后重试。', '调度器保存回执不匹配。',
    '任务保存结果未知；任务保持暂停，请检查后再操作。', '任务计划无效。',
    'Cron计划包含不支持的字段。', 'Interval计划包含不支持的字段。',
    '周期必须为正数。', 'Date计划包含不支持的字段。',
    'Date计划必须指定run_date。', '触发计划无效。',
    '仅支持 cron、interval 和 date 触发器。', '预览数量无效。',
    '需要系统管理员权限才能修改全局启动策略', '无效的调度启动策略',
    '配置已被其他管理员修改，请刷新后重试', '确认已过期，请重新发起保存。',
    '调度服务未连接，显示已认领任务。', '监听事件触发，没有未来时间点。',
    '结果未知。外部核实前不要重跑；提交核实将解除任务护栏。',
    'Windows 任务服务不可用：请稍后刷新',
    '平台请求只代表服务已受理，不代表原生任务已完成。',
    '已返回原运行；同一请求不会重复提交。', '同一请求不会重复提交。',
    '原生提交结果未知，请人工核实；不会自动重试。',
    'Windows已受理启动命令，不代表执行完成。',
    '已返回相同幂等键对应的运行，不会重复提交。',
    '提交结果未知，已锁定任务且不会自动重试。',
    '调度器已接收请求，不代表运行成功。',
    '原生任务已不存在，平台映射已审计关闭。',
    '已核实原生禁用状态，平台映射保持暂停。',
    '已核实后端定义不存在，平台映射已审计关闭。',
    '已核实定义并保持暂停，请核对计划后显式恢复。',
    '任务归属已转移，历史运行团队快照不变，任务保持暂停。',
    '运行已人工核实，审计记录已保存。',
})


def task_label(message):
    return t__task(message)


def task_message(message):
    if not isinstance(message, str):
        return message
    if message in _TASK_FEEDBACK:
        return t__task(message)
    if message.startswith('每页数量必须在1至') and message.endswith('之间。'):
        maximum = message[len('每页数量必须在1至'):-len('之间。')]
        if maximum.isdecimal():
            return t__task('每页数量必须在1至{max}之间。').format(max=maximum)
    if message.startswith('日志数量必须在1至') and message.endswith('之间。'):
        maximum = message[len('日志数量必须在1至'):-len('之间。')]
        if maximum.isdecimal():
            return t__task('日志数量必须在1至{max}之间。').format(max=maximum)
    return message


def task_status_label(status):
    label = RUN_STATUS_LABELS.get(status)
    return task_label(label) if label else task_label('未知') if status is None else status


def task_source_label(source):
    label = SOURCE_LABELS.get(source)
    return task_label(label) if label else source or '—'


def task_timestamp(value, timezone='Asia/Shanghai'):
    rendered = display_timestamp(value, timezone)
    return task_label('未记录') if rendered == '未记录' else rendered


def task_operation_label(operation):
    labels = {
        'run': '运行', 'pause': '暂停', 'resume': '恢复', 'delete': '删除',
        'resolve_definition': '核实定义', 'transfer': '转移团队',
    }
    label = labels.get(operation)
    return task_label(label) if label else operation

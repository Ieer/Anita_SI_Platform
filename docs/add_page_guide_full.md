# 新增应用页面

自定义应用通过脚手架生成并注册到 `src/config/applications.json`。菜单、页面路由和权限沿用 SI-Platform 现有模型；新增页面不应绕过授权层或直接修改受保护的内置菜单。

## 推荐：使用页面脚手架

在项目根目录先预览，再生成：

```powershell
python scripts/scaffold_page.py --module analytics --title 分析中心 --subpage 总览:overview:1 --subpage 明细:details:2 --dry-run
python scripts/scaffold_page.py --module analytics --title 分析中心 --subpage 总览:overview:1 --subpage 明细:details:2
```

模块名和页面名需为 ASCII Python 标识符；应用模块名不要以 `_` 结尾，该后缀用于内置应用。子页面格式为 `标题:页面名[:排序[:图标]]`。脚手架不会覆盖已存在模块。生成后检查变更、完成业务视图与回调，再重启 Web；进入角色管理，将新页面权限显式授予需要的角色。

页面 URL 采用注册模块名和页面名组成的路径，例如 `/analytics/overview`。未知页面返回 404；没有页面权限时返回无权限页面。移除模块后，旧数据库授权只会被忽略/告警，不会自动删除角色数据。

## 页面契约

应用页面模块导出：

- `title`：导航标题。
- `icon`：FAC 图标名或 `None`。
- `order`：同级页面排序。
- `access_metas`：非空且在数据库长度限制内的权限名元组。
- `render_content(menu_access, **kwargs)`：返回 Dash 组件；URL 查询参数会传入 `kwargs`。

视图文件负责布局，并导入同模块回调文件以注册 Dash callbacks。业务逻辑放在 `src/common/` 或 DAO/service 层，避免把数据操作塞进布局函数。页面和回调的具体文件位置以脚手架生成结果及现有邻近模块为准。

## 服务端权限

不要把“按钮隐藏”当成权限检查。每个回调都需在服务端校验当前用户和所操作的对象：

```python
from common.utilities.util_menu_access import get_menu_access

access = get_menu_access()
if not access.has_access('分析中心-导出'):
    return no_update
```

上例中的 `no_update` 需按回调实际输出设计替换为合适的拒绝响应。对读取、导出、创建、编辑、删除、触发任务等操作，均应检查权限；团队资源还需验证对象所属团队和动作授权。不要信任浏览器提交的任务脚本、团队 ID 或行数据。

新增权限需列在页面的 `access_metas` 中并给出唯一名称。页面访问权限和对象/动作权限是不同层次；调用任务中心等共享服务时应沿用该服务的授权接口。

## 国际化与主题

国际化词条按项目现有 `src/translations/` 主题组织方式添加，并从 `src/i18n.py` 使用对应翻译函数。不要在 README 或页面中写不存在的英文版文件链接。

后台页面处于全局主题 Provider 下。使用已有语义 CSS 变量，例如 `--platform-surface`、`--platform-ink`、`--platform-muted`、`--platform-border`、`--platform-accent`、`--platform-success`、`--platform-warning` 和 `--platform-danger`；主题算法集中在 `src/common/utilities/util_theme.py`。避免另建固定浅色 Provider 或只为一页硬编码完整色板。

Plotly 图表需按页面现有模式应用主题样式，并保留 `uirevision`、查询条件和缩放状态。主题切换不应触发任务执行、数据刷新或外部服务请求。

## 验收建议

1. 页面只通过已注册模块访问；无权限与未知页面行为符合预期。
2. 任何有副作用的回调都有服务端权限与对象归属检查，错误状态不会丢失已有页面数据。
3. 检查日间/夜间主题、状态/禁用/焦点样式及长文本布局。
4. 在 1440、1366、390、320 像素视口验证页面宽度和弹窗可达性；宽表格只在自身容器滚动。
5. 运行相关测试；涉及真实任务、外部通知或 Windows 原生操作的测试必须使用隔离目标或明确授权。

基础注册和平台回归：

```powershell
python -m pytest tests/test_platform.py -q
```

当前项目结构和测试安全边界见[项目盘点](project_inventory.md)。

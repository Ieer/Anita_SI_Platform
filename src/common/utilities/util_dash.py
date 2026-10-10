import re
import dash
from functools import wraps
import json
from common.utilities.util_theme import browser_config


CALLBACK_PERMISSIONS = {
    'access_.user_mgmt_c': '用户管理-页面',
    'access_.role_mgmt_c': '角色管理-页面',
    'access_.group_mgmt_c': '团队管理-页面',
    'access_.group_auth_c': '团队授权-页面',
    'dashboard_.monitor_c': '监控页-页面',
    'dash_callback.pages.agent_widget_c': '工作台-页面',
    'message_.announcement_c': '公告管理-页面',
    'person_.personal_info_c': '个人信息-页面',
    'task_.task_mgmt_c': '任务管理-页面',
    'task_.task_log_c': '任务日志-页面',
    'setting_.notify_api_c': '通知接口-页面',
    'setting_.listen_api_c': '监听接口-页面',
}


def guard_callback(function):
    permission = CALLBACK_PERMISSIONS.get(function.__module__.removeprefix('dash_callback.application.'))
    if permission is None:
        return function

    @wraps(function)
    def guarded(*args, **kwargs):
        from common.utilities.util_menu_access import get_menu_access
        from dash.exceptions import PreventUpdate

        if not get_menu_access().has_access(permission):
            raise PreventUpdate
        return function(*args, **kwargs)

    return guarded

cdn_modules = [
    'DashRenderer',
    'dash_html_components',
    'dash_core_components',
    'feffery_antd_components',
    'feffery_utils_components',
    'feffery_markdown_components',
]


class CustomDash(dash.Dash):
    """
    自定义Dash实例，用于改造默认的CDN访问行为
    """

    def callback(self, *args, **kwargs):
        register = super().callback(*args, **kwargs)

        def decorate(function):
            return register(guard_callback(function))

        return decorate

    def interpolate_index(self, **kwargs):
        configuration = json.dumps(browser_config(), separators=(',', ':')).replace('<', '\\u003c')
        kwargs['metas'] = kwargs.get('metas', '') + (
            '<script>window.platformThemeConfig=' + configuration + ';</script>'
            '<script src="/assets/js/theme.js"></script>'
        )
        kwargs['app_entry'] = '''<div id="react-entry-point">
<div class="_dash-loading" role="status" aria-live="polite" aria-busy="true">
<span class="platform-loader" aria-hidden="true"></span>
<strong>正在加载平台</strong>
<span class="platform-load-help">等待时间较长？<a href="">刷新重试</a></span>
<noscript>请启用 JavaScript 后重新加载。</noscript>
</div></div>'''
        scripts = kwargs.pop('scripts')
        scripts = '''<script type="importmap">{"imports":{"three":"/assets/vendor/three/three.module.min.js"}}</script>''' + scripts

        # 提取scripts部分符合条件的外部js资源
        external_scripts = re.findall('(<script src="http.*?"></script>)', scripts)

        # 将原有的script标签内容替换为带备用地址错误切换的版本
        for external_script in external_scripts:
            # 提取当前资源地址
            origin_script_src = re.findall('"(.*?)"', external_script)[0]
            # 抽取关键信息
            library_name, library_version, library_file = re.findall(
                'com/(.+)@(.+?)/(.+?)$', origin_script_src
            )[0]
            # 基于阿里cdn构建新的资源地址
            new_library_src = (
                f'https://registry.npmmirror.com/{library_name}/{library_version}/files/{library_file}'
            )

            scripts = scripts.replace(
                external_script,
                """<script src="{}" onerror='this.remove(); let fallbackScript = document.createElement("script"); fallbackScript.src = "{}"; document.querySelector("head").prepend(fallbackScript);'></script>""".format(
                    re.findall('"(.*?)"', external_script)[0].replace(
                        origin_script_src,
                        new_library_src,
                    ),
                    re.findall('"(.*?)"', external_script)[0],
                ),
            )

        scripts = (
            """
<script>
const requiredModules = {};
{}
</script>
""".format(
                str(cdn_modules),
                """// 自定义延时
const delay = (ms) => {
    return new Promise(resolve => setTimeout(resolve, ms));
}

// 判断指定的多个模块是否均已加载
const areModulesLoaded = (modules) => {
    return modules.every(module => window[module]);
}

const waitForModules = async () => {
    while (!areModulesDefined(requiredModules)) {
        await delay(100); // 延迟100毫秒
    }

    // 若相关模块均已加载完成，手动初始化DashRenderer
    var renderer = new DashRenderer();
}

const customOnError = async (message, source, lineno, colno, error) => {
    if (message.includes('is not defined') !== -1) {
        await waitForModules();
    }
}

window.onerror = customOnError""",
            )
            + scripts
        )

        return super(CustomDash, self).interpolate_index(scripts=scripts, **kwargs)

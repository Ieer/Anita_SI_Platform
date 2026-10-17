import feffery_antd_components as fac
import feffery_utils_components as fuc
from i18n import translator


def render_lang_content(color='var(--platform-muted)'):
    locale = translator.get_current_locale()
    next_locale = 'zh-cn' if locale == 'en-us' else 'en-us'
    return fac.AntdTooltip(
        fac.AntdButton(
            fac.Fragment(
                # 国际化cookie
                fuc.FefferyCookie(id='global-locale', expires=3600 * 24 * 365, cookieKey=translator.cookie_name)
            ),
            id='global-locale-switch',
            title='切换到中文' if next_locale == 'zh-cn' else 'Switch to English',
            **{'aria-label': '切换到中文' if next_locale == 'zh-cn' else 'Switch to English'},
            icon=fac.AntdIcon(
                icon='md-translate',
                style={
                    'fontSize': '1.2em',
                    'verticalAlign': '-0.4em',
                    'color': color,
                },
            ),
            type='text',
            clickExecuteJsString=f"""
                window.dash_clientside.set_props('global-locale', {{ value: '{next_locale}' }});
                document.cookie = '{translator.cookie_name}={next_locale}; path=/; max-age={3600 * 24 * 365}; SameSite=Lax';
                window.location.reload();
            """,
        ),
        placement='bottom',
        title='English / 中文',
    )

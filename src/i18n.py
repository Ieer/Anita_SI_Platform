import os
from functools import partial

from feffery_dash_utils.i18n_utils import Translator
from flask import request

# 动态路径解决  2025/09/24
os.chdir(os.path.dirname(os.path.abspath(__file__)))

LANGUAGE_COOKIE_NAME = 'dash-i18n'


def get_current_locale():
    locale = request.cookies.get(LANGUAGE_COOKIE_NAME)
    if locale in translator.available_locales:
        return locale
    return translator.root_locale


translator = Translator(
    translations=[
        # 全局无主题文案
        './translations/locales.json',
        # 各组件文档主题文案
        *[os.path.join('./translations/topic_locales', path) for path in os.listdir('./translations/topic_locales')],
    ],
    cookie_name=LANGUAGE_COOKIE_NAME,
    get_current_locale=get_current_locale,
    force_check_content_translator=False,
)

t__default = partial(translator.t)
t__access = partial(translator.t, locale_topic='access')
t__dashboard = partial(translator.t, locale_topic='dashboard')
t__person = partial(translator.t, locale_topic='person')
t__notification = partial(translator.t, locale_topic='notification')
t__task = partial(translator.t, locale_topic='task')
t__setting = partial(translator.t, locale_topic='setting')
t__other = partial(translator.t, locale_topic='other')
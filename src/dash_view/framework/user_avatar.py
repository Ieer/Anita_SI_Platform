import unicodedata

import feffery_antd_components as fac
from config.dashgo_conf import PathProj


def get_avatar_initials(user_info):
    full_name = str(getattr(user_info, 'user_full_name', '') or '').strip()
    user_name = str(getattr(user_info, 'user_name', '') or '').strip()
    display_name = full_name or user_name
    if not display_name:
        return '?'

    first_character = display_name[0]
    character_name = unicodedata.name(first_character, '')
    if character_name.startswith(('CJK ', 'HANGUL ', 'HIRAGANA ', 'KATAKANA ')):
        return first_character

    name_parts = display_name.split()
    initials = name_parts[0][0] if len(name_parts) == 1 else name_parts[0][0] + name_parts[-1][0]
    return initials.upper()


def avatar_file_exists(user_info):
    user_name = str(getattr(user_info, 'user_name', '') or '').strip()
    if not user_name or '..' in user_name or any(character in user_name for character in '/\\:'):
        return False
    return (PathProj.AVATAR_DIR_PATH / f'{user_name}.jpg').is_file()


def render_user_avatar(user_info, size=36, component_id=None, class_name=None, avatar_exists=None):
    user_name = str(getattr(user_info, 'user_name', '') or '').strip()
    display_name = str(getattr(user_info, 'user_full_name', '') or '').strip() or user_name or '未知用户'
    initials = get_avatar_initials(user_info)
    if avatar_exists is None:
        avatar_exists = avatar_file_exists(user_info)
    image_mode = bool(user_name and avatar_exists)
    return fac.AntdAvatar(
        **({'id': component_id} if component_id is not None else {}),
        mode='image' if image_mode else 'text',
        src=f'/avatar/{user_name}' if image_mode else None,
        alt=initials,
        text=initials if not image_mode else None,
        size=size,
        shape='circle',
        className=class_name,
        **{'aria-label': display_name},
    )
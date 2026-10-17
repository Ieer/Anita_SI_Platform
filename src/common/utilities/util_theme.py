from copy import deepcopy


DEFAULT_PREFERENCE = {'mode': 'system', 'palette': 'teal', 'contrast': 'normal'}
STORAGE_KEY = 'si-platform.appearance.v1'
PALETTES = {
    'teal': ('#08776f', '#72e6c9', '#00554d'),
    'blue': ('#245ab5', '#99bbff', '#143a85'),
    'rose': ('#a32d62', '#ffadd0', '#771540'),
}


def normalize_preference(value):
    value = value if isinstance(value, dict) else {}
    allowed = {'mode': ('light', 'dark', 'system'), 'palette': tuple(PALETTES),
               'contrast': ('normal', 'high')}
    return {key: value.get(key) if value.get(key) in choices else DEFAULT_PREFERENCE[key]
            for key, choices in allowed.items()}


def theme_spec(value=None, system_dark=False, login=False):
    preference = normalize_preference(value)
    dark = login or preference['mode'] == 'dark' or (preference['mode'] == 'system' and system_dark)
    high = preference['contrast'] == 'high'
    palette = 'teal' if login else preference['palette']
    accent = PALETTES[palette][1 if dark else 2 if high else 0]
    colors = {
        'ink': '#f5f7fa' if dark else '#19282f',
        'muted': ('#e3e9ee' if high else '#b4bdc4') if dark else ('#263238' if high else '#526369'),
        'canvas': ('#000000' if high else '#11191a' if login else '#151819') if dark else ('#ffffff' if high else '#f2f4f5'),
        'surface': ('#080808' if high else '#1b2024' if login else '#1d2224') if dark else '#ffffff',
        'raised': ('#151515' if high else '#272e34' if login else '#272d30') if dark else ('#ffffff' if high else '#f1f4f6'),
        'border': ('#bac8d2' if high else '#7f9098') if dark else ('#465660' if high else '#7f9098'),
        'divider': ('#bac8d2' if high else '#7f9098' if login else '#394346') if dark else ('#465660' if high else '#7f9098'),
        'accent': accent,
        'accent-soft': '#263c42' if dark else '#e8eef2',
        'on-accent': '#081216' if dark else '#ffffff',
        'success': '#70e0a0' if dark else '#124c32' if high else '#176b45',
        'warning': '#ffd27a' if dark else '#603700' if high else '#854b00',
        'danger': '#ffaaaa' if dark else '#801b28' if high else '#aa2534',
        'info': '#99bbff' if dark else '#143a85' if high else '#245ab5',
        'secondary': '#dab5f4' if dark else '#5d306f' if high else '#754590',
        'disabled': '#a2adb5' if dark else '#62717a',
        'code': '#101619' if dark else '#f1f4f6',
    }
    token = {
        'colorPrimary': accent, 'colorLink': accent, 'colorText': colors['ink'],
        'colorTextSecondary': colors['muted'], 'colorTextPlaceholder': colors['muted'],
        'colorTextDisabled': colors['disabled'], 'colorBgLayout': colors['canvas'],
        'colorBgContainer': colors['surface'], 'colorBgElevated': colors['raised'] if dark and not high and not login else colors['surface'],
        'colorFillAlter': colors['raised'], 'colorBorder': colors['border'],
        'colorBorderSecondary': colors['divider'], 'colorSuccess': colors['success'],
        'colorWarning': colors['warning'], 'colorError': colors['danger'],
        'colorInfo': colors['info'], 'colorTextLightSolid': colors['on-accent'],
        'controlOutline': accent, 'borderRadius': 6,
    }
    menu = {'itemColor': colors['ink'], 'itemBg': colors['surface'],
            'itemSelectedColor': accent, 'itemSelectedBg': colors['accent-soft'],
            'darkItemColor': colors['ink'], 'darkItemBg': colors['surface'],
            'darkSubMenuItemBg': colors['surface'], 'darkItemHoverColor': colors['ink'],
            'darkItemSelectedColor': accent, 'darkItemSelectedBg': colors['accent-soft'],
            'darkItemDisabledColor': colors['disabled'], 'darkGroupTitleColor': colors['muted']}
    return {'mode': 'dark' if dark else 'light', 'colors': colors,
            'algorithm': 'dark' if dark else 'default', 'token': token,
            'componentsToken': {'Menu': menu}}


def browser_config():
    return {'storageKey': STORAGE_KEY, 'defaults': DEFAULT_PREFERENCE, 'loginThemes': {
        contrast: theme_spec({'contrast': contrast}, login=True)
        for contrast in ('normal', 'high')
    }, 'themes': {
        f'{mode}:{palette}:{contrast}': theme_spec(
            {'mode': mode, 'palette': palette, 'contrast': contrast})
        for mode in ('light', 'dark') for palette in PALETTES for contrast in ('normal', 'high')
    }}


def restyle_figure(figure, state=None):
    state = state if isinstance(state, dict) else {}
    spec = theme_spec(state, system_dark=state.get('resolvedMode') == 'dark')
    colors = spec['colors']
    result = deepcopy(figure or {'data': [], 'layout': {}})
    layout = result.setdefault('layout', {})
    layout.update(paper_bgcolor=colors['surface'], plot_bgcolor=colors['surface'])
    layout.setdefault('font', {})['color'] = colors['muted']
    layout['hoverlabel'] = {'bgcolor': colors['surface'], 'bordercolor': colors['border'],
                            'font': {'color': colors['ink']}}
    for axis in ('xaxis', 'yaxis'):
        layout.setdefault(axis, {}).update(gridcolor=colors['border'], zerolinecolor=colors['border'])
    for annotation in layout.get('annotations', []):
        annotation.setdefault('font', {})['color'] = colors['ink']
    roles = {'#087f77': 'accent', '#087f78': 'success', '#5272b5': 'secondary',
             '#a6610b': 'warning', '#bd4141': 'danger', '#286bb0': 'info', '#78898c': 'muted'}
    meta = layout.setdefault('meta', {})
    previous_roles = meta.get('platformThemeRoles', [])
    trace_roles = []
    for index, trace in enumerate(result.get('data', [])):
        saved = previous_roles[index] if index < len(previous_roles) else {}
        current = {}
        for part in ('line', 'marker'):
            style = trace.get(part, {})
            color = style.get('color')
            role = saved.get(part)
            if isinstance(color, str):
                role = role or roles.get(color)
                if role:
                    style['color'] = colors[role]
            elif isinstance(color, list):
                role = role or [roles.get(item) for item in color]
                style['color'] = [colors[item_role] if item_role else item
                                  for item, item_role in zip(color, role)]
            current[part] = role
        if trace.get('textfont'):
            trace['textfont']['color'] = colors['on-accent']
        if trace.get('marker', {}).get('line', {}).get('color'):
            trace['marker']['line']['color'] = colors['surface']
        trace_roles.append(current)
    meta['platformThemeRoles'] = trace_roles
    return result
import feffery_antd_components as fac
from dash import html


class Card(fac.AntdCard):
    def __init__(self, *args, **kwargs):
        icon = kwargs.pop('icon', None)
        base_style = kwargs.get('style') or {}
        kwargs['style'] = {
            **base_style,
            'boxShadow': 'rgba(99, 99, 99, 0.2) 0px 2px 8px 0px',
            'boxSizing': 'border-box',
        }
        kwargs.setdefault('size', 'small')

        # Remove unsupported parameters for feffery_antd_components 0.4.2
        kwargs.pop('headStyle', None)

        if kwargs.get('title') is not None:
            title_children = []
            if icon is not None:
                title_children.append(fac.AntdIcon(icon=icon))
            title_text_style = {'marginLeft': '10px'} if icon is not None else {}
            title_value = kwargs['title']
            title_children.append(
                fac.AntdText(title_value, style=title_text_style)
            )
            kwargs['title'] = html.Div(title_children)
        else:
            styles = kwargs.setdefault('styles', {})
            header_style = styles.get('header', {})
            styles['header'] = {
                **header_style,
                'display': 'none'
            }

        super().__init__(*args, **kwargs)

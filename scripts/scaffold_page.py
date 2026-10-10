"""Generate registered Dash applications without overwriting existing files."""

import argparse
import json
import keyword
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate_identifier(value):
    if not value.isascii() or not value.isidentifier() or keyword.iskeyword(value) or value.startswith('_'):
        raise ValueError(f'Invalid module identifier: {value!r}')
    return value


def parse_subpages(sp_list):
    result = []
    for raw in sp_list:
        parts = raw.split(':')
        if not 2 <= len(parts) <= 4 or not parts[0].strip():
            raise ValueError(f'Expected title:page[:order[:icon]], got {raw!r}')
        name = validate_identifier(parts[1])
        order = int(parts[2]) if len(parts) >= 3 else 1
        title = parts[0].strip()
        if len(title + '-页面') > 32:
            raise ValueError('Page permission names must not exceed 32 characters')
        result.append(dict(title=title, route=name, order=order,
                           icon=parts[3] if len(parts) == 4 else 'antd-file', filename=f'{name}.py'))
    if not result:
        raise ValueError('At least one --subpage is required')
    if len({page['route'] for page in result}) != len(result) or len({page['title'] for page in result}) != len(result):
        raise ValueError('Duplicate page names or titles')
    return result


def scaffold(root, module, title, icon, order, subpages, dry_run=False):
    module = validate_identifier(module)
    if module.endswith('_'):
        raise ValueError('Names ending in _ are reserved for built-in applications')
    if not title.strip():
        raise ValueError('Application title must not be empty')
    pages = parse_subpages(subpages)
    root = Path(root)
    view_dir = root / 'src' / 'dash_view' / 'application' / module
    callback_dir = root / 'src' / 'dash_callback' / 'application' / module
    if view_dir.exists() or callback_dir.exists():
        raise ValueError(f'Application already exists: {module}')
    registry_path = root / 'src' / 'config' / 'applications.json'
    registry = json.loads(registry_path.read_text(encoding='utf-8')) if registry_path.exists() else []
    if not isinstance(registry, list) or not all(isinstance(entry, str) for entry in registry):
        raise ValueError('applications.json must be a list of module paths')
    paths = [f'{module}.{page["route"]}' for page in pages]
    if set(paths) & set(registry):
        raise ValueError('Application pages are already registered')
    files = {
        view_dir / '__init__.py': f'title = {title!r}\nicon = {icon!r}\norder = {order}\n',
        callback_dir / '__init__.py': '',
    }
    for page in pages:
        name = page['route']
        files[view_dir / page['filename']] = (
            'from dash import html\n'
            'from common.utilities.util_menu_access import MenuAccess\n'
            f'from dash_callback.application.{module} import {name}_c\n\n'
            f'title = {page["title"]!r}\nicon = {page["icon"]!r}\norder = {page["order"]}\n'
            f'access_metas = ({page["title"] + "-页面"!r},)\n\n\n'
            'def render_content(menu_access: MenuAccess, **kwargs):\n'
            f'    return html.Div(id={module + "-" + name + "-content"!r})\n'
        )
        files[callback_dir / f'{name}_c.py'] = '"""Register page callbacks here; check permissions before data access."""\n'
    files[registry_path] = json.dumps(registry + paths, ensure_ascii=False, indent=2) + '\n'
    for path, content in files.items():
        if path.suffix == '.py':
            compile(content, str(path), 'exec')
    if not dry_run:
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
    return list(files)


def main():
    parser = argparse.ArgumentParser(description='Create Dash pages, callbacks and application registration')
    parser.add_argument('--module', required=True)
    parser.add_argument('--title', required=True)
    parser.add_argument('--icon', default='antd-appstore')
    parser.add_argument('--order', type=int, default=10)
    parser.add_argument('--subpage', action='append', required=True, help='title:page[:order[:icon]]')
    parser.add_argument('--dry-run', action='store_true', help='Preview without writing files')
    args = parser.parse_args()
    try:
        files = scaffold(ROOT, args.module, args.title, args.icon, args.order, args.subpage, args.dry_run)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    for path in files:
        print(('PLAN ' if args.dry_run else 'CREATED ') + str(path.relative_to(ROOT)))
    print('Restart the web process and grant page permissions to the intended roles.')


if __name__ == '__main__':
    main()
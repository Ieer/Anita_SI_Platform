"""Validate registered custom pages before restarting the web process."""

import ast
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'


def _literal(module, name):
    tree = ast.parse(module.read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                value = node.value
                return ast.literal_eval(value)
    raise ValueError(f'{module}: missing literal {name}')


def validate(registry_path=None):
    path = Path(registry_path) if registry_path else SRC / 'config' / 'applications.json'
    source_root = path.parents[1] if registry_path else SRC
    entries = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(entries, list) or len(entries) != len(set(entries)):
        raise ValueError('applications.json must be a list without duplicate entries')

    permissions = set()
    checked = []
    for entry in entries:
        parts = entry.split('.')
        if len(parts) != 2 or any(not part.isidentifier() or part.startswith('_') for part in parts):
            raise ValueError(f'invalid custom page path: {entry!r}')
        module, route = parts
        view = source_root / 'dash_view' / 'application' / module / f'{route}.py'
        callback = source_root / 'dash_callback' / 'application' / module / f'{route}_c.py'
        if not view.exists() or not callback.exists():
            raise ValueError(f'{entry}: view and callback files are required')
        title = _literal(view, 'title')
        access_metas = _literal(view, 'access_metas')
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f'{entry}: title must be a non-empty string')
        if not isinstance(access_metas, (tuple, list)) or not access_metas:
            raise ValueError(f'{entry}: access_metas must be non-empty')
        if not any(isinstance(node, ast.FunctionDef) and node.name == 'render_content'
                   for node in ast.walk(ast.parse(view.read_text(encoding='utf-8-sig')))):
            raise ValueError(f'{entry}: render_content is required')
        for permission in access_metas:
            if not isinstance(permission, str) or not permission.strip() or len(permission) > 32:
                raise ValueError(f'{entry}: invalid permission {permission!r}')
            if permission in permissions:
                raise ValueError(f'duplicate permission {permission!r}')
            permissions.add(permission)
        checked.append(entry)
    return checked


def main():
    try:
        checked = validate()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f'PAGE CHECK FAILED: {exc}', file=sys.stderr)
        return 1
    print(f'PAGE CHECK OK: {len(checked)} custom page(s), {len(checked)} module path(s)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

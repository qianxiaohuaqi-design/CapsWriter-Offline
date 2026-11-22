"""Small, testable data-protection primitives used by control-center editors."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import threading
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DATA_LOCK = threading.RLock()
SECRET_KEYS = {'api_key', 'apikey', 'access_token', 'refresh_token', 'authorization', 'password', 'secret', 'token'}


def fingerprint(path: Path) -> str:
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else 'missing'


def atomic_write(path: Path, content: bytes) -> None:
    path = Path(path)
    if path.is_symlink():
        raise ValueError('不写入链接文件。')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def redact(value):
    if isinstance(value, dict):
        return {str(key): redact(item) for key, item in value.items() if str(key).lower() not in SECRET_KEYS}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, str) and value.startswith(('https://', 'http://')):
        parsed = urlsplit(value)
        host = parsed.netloc.rsplit('@', 1)[-1]
        query = [(key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True) if key.lower() not in SECRET_KEYS]
        return urlunsplit((parsed.scheme, host, parsed.path, urlencode(query), parsed.fragment))
    return value


def declarative_value(node):
    """Literal data plus bounded numeric arithmetic used by existing role files."""
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        pass
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv)):
        left, right = declarative_value(node.left), declarative_value(node.right)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or abs(v) > 10**12 for v in (left, right)):
            raise ValueError('角色数值表达式超出支持范围。')
        import operator
        operation = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                     ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv}[type(node.op)]
        result = operation(left, right)
        import math
        if not math.isfinite(result) or abs(result) > 10**12:
            raise ValueError('角色数值表达式超出支持范围。')
        return result
    raise ValueError('角色字段只支持配置字面值和简单数值计算，不执行 Python 代码。')


def safe_role(content: str) -> str:
    """Role import is declarative data; never introduce executable Python."""
    tree = ast.parse(content)
    result = []
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            result.append(repr(node.value.value))
            continue
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            value = declarative_value(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            value = declarative_value(node.value)
        else:
            raise ValueError('角色备份只允许文字、数字、列表等配置赋值，不允许导入或执行 Python 代码。')
        if name.startswith('__'):
            raise ValueError('角色配置包含保留字段。')
        value = '' if name.lower() in SECRET_KEYS else redact(value)
        result.append(f'{name} = {value!r}')
    return '\n'.join(result) + '\n'


def split_rule_line(line: str) -> tuple[str, str]:
    # The documented delimiter has surrounding whitespace. This keeps (?=...) and
    # (?<=...) inside a regex intact; retain simple legacy a=b rules as fallback.
    parts = re.split(r'\s+=\s*', line.strip(), maxsplit=1)
    if len(parts) != 2:
        parts = line.strip().split('=', 1)
    if len(parts) != 2 or not parts[0].strip():
        raise ValueError('规则需要“正则 = 替换文本”。')
    return parts[0].strip(), parts[1].strip()


def validate_rules(text: str) -> None:
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        try:
            pattern_text, replacement_text = split_rule_line(line)
        except ValueError as error:
            raise ValueError(f'第 {number} 行：{error}') from error
        replacement = re.sub(r'\$(\d+)', r'\\\1', replacement_text.replace(r'\s', ' '))
        try:
            pattern = re.compile(pattern_text)
            pattern.sub(replacement, '')  # validates replacement group references too
        except re.error as error:
            raise ValueError(f'第 {number} 行正则或替换引用无效：{error}') from error


def replace_assignment(content: str, name: str, value, class_name: str | None = None) -> str:
    tree = ast.parse(content)
    scope = tree
    if class_name:
        scope = next((node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name), None)
        if scope is None:
            raise ValueError(f'配置缺少 {class_name}，未写入。')
    nodes = scope.body if class_name else ast.walk(tree)
    assignment = next((node for node in nodes if isinstance(node, (ast.Assign, ast.AnnAssign)) and any(
        isinstance(target, ast.Name) and target.id == name
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target]))), None)
    lines = content.splitlines(keepends=True)
    if assignment:
        # AST columns are UTF-8 byte offsets, not Python character indexes.
        encoded = content.encode('utf-8')
        encoded_lines = encoded.splitlines(keepends=True)
        start = sum(map(len, encoded_lines[:assignment.lineno - 1])) + assignment.col_offset
        end = sum(map(len, encoded_lines[:assignment.end_lineno - 1])) + assignment.end_col_offset
        result = (encoded[:start] + f'{name} = {value!r}'.encode('utf-8') + encoded[end:]).decode('utf-8')
    elif class_name:
        index = scope.end_lineno
        lines.insert(index, f'    {name} = {value!r}\n')
        result = ''.join(lines)
    else:
        result = content + f'\n{name} = {value!r}\n'
    ast.parse(result)
    return result


def transaction_token(staged: dict[Path, str], fingerprints: dict[str, str]) -> str:
    payload = {'new': {str(path): hashlib.sha256(text.encode()).hexdigest() for path, text in staged.items()},
               'old': fingerprints}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def commit_transaction(staged: dict[Path, str], backup_root: Path, expected: dict[str, str],
                       writer=atomic_write) -> Path:
    """Journal old bytes before first replacement; read back or restore every file.

    Multi-file imports are recoverable, not filesystem-wide atomic. A journal left
    after power loss is detected and recovered before a subsequent import.
    """
    with DATA_LOCK:
        if any(fingerprint(path) != expected.get(str(path)) for path in staged):
            raise ValueError('配置已被其他窗口或程序修改，请重新预览后确认。')
        for path in staged:
            if path.is_symlink():
                raise ValueError('配置路径包含链接，已阻止导入。')
        journal = Path(backup_root) / f'import-{uuid.uuid4().hex}'
        journal.mkdir(parents=True, exist_ok=False)
        manifest = {'state': 'prepared', 'files': []}
        for index, (path, text) in enumerate(staged.items()):
            old = path.read_bytes() if path.exists() else None
            name = f'{index}.original'
            if old is not None:
                atomic_write(journal / name, old)
            manifest['files'].append({'path': str(path), 'backup': name, 'existed': old is not None,
                                      'old_sha256': hashlib.sha256(old).hexdigest() if old is not None else None,
                                      'new_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()})
        atomic_write(journal / 'manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2).encode())
        try:
            for path, text in staged.items():
                if fingerprint(path) != expected[str(path)]:
                    raise ValueError('配置在导入期间被外部修改，停止写入。')
                writer(path, text.encode('utf-8'))
                if path.read_bytes() != text.encode('utf-8'):
                    raise OSError('配置写入后读回校验失败。')
            manifest['state'] = 'committed'
            atomic_write(journal / 'manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2).encode())
            return journal
        except Exception as error:
            failures = []
            for item in manifest['files']:
                path = Path(item['path'])
                try:
                    current = fingerprint(path)
                    if current not in {item['old_sha256'] or 'missing', item['new_sha256']}:
                        raise OSError('目标已被外部修改，保留现场。')
                    if item['existed']:
                        old = (journal / item['backup']).read_bytes()
                        atomic_write(path, old)
                        if fingerprint(path) != item['old_sha256']:
                            raise OSError('恢复读回不一致')
                    else:
                        # Only remove exactly the new bytes from this transaction.
                        if path.exists() and fingerprint(path) == item['new_sha256']:
                            path.unlink()
                except Exception:
                    failures.append(str(path))
            manifest['state'] = 'rollback_failed' if failures else 'rolled_back'
            manifest['recovery_required'] = failures
            atomic_write(journal / 'manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2).encode())
            if failures:
                raise OSError(f'导入未完成，部分恢复需要人工处理；原字节保存在 {journal}。') from error
            raise ValueError('导入失败，全部原配置已恢复。') from error


def pending_imports(backup_root: Path) -> list[dict]:
    result = []
    for manifest_path in Path(backup_root).glob('import-*/manifest.json'):
        try:
            if manifest_path.is_symlink() or manifest_path.parent.is_symlink():
                raise ValueError('恢复记录不能是链接。')
            item = json.loads(manifest_path.read_text(encoding='utf-8'))
            if not isinstance(item, dict) or item.get('state') not in {
                    'prepared', 'rollback_failed', 'committed', 'rolled_back'}:
                raise ValueError('恢复记录状态无法确认。')
        except (OSError, ValueError):
            result.append({'journal': str(manifest_path.parent), 'state': 'unreadable'})
            continue
        if item.get('state') in {'prepared', 'rollback_failed'}:
            result.append({'journal': str(manifest_path.parent), 'state': item['state']})
    return result


def recover_transaction(journal: Path, backup_root: Path, allowed_paths: set[Path]) -> None:
    """Restore a prepared journal after an explicit recovery request."""
    journal = Path(journal)
    if journal.is_symlink() or journal.resolve().parent != Path(backup_root).resolve():
        raise ValueError('恢复目录不属于本应用。')
    manifest_path = journal / 'manifest.json'
    if manifest_path.is_symlink():
        raise ValueError('恢复记录不能是链接。')
    with DATA_LOCK:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if not isinstance(manifest, dict) or manifest.get('state') not in {'prepared', 'rollback_failed'}:
            raise ValueError('该记录不需要中断恢复。')
        allowed = {path.resolve() for path in allowed_paths}
        changes = []
        for item in manifest['files']:
            path = Path(item['path'])
            if path.is_symlink() or path.resolve() not in allowed:
                raise ValueError('恢复目标不属于受管理配置。')
            current = fingerprint(path)
            if current not in {item['old_sha256'] or 'missing', item['new_sha256']}:
                raise ValueError('配置在中断后被其他程序修改；原备份保留，需手动比较。')
            old = None
            if item['existed']:
                original = journal / item['backup']
                if original.is_symlink() or original.resolve().parent != journal.resolve():
                    raise ValueError('原字节备份路径无效。')
                old = original.read_bytes()
                if hashlib.sha256(old).hexdigest() != item['old_sha256']:
                    raise ValueError('原字节备份校验失败，停止恢复。')
            changes.append((path, old, item))
        for path, old, item in changes:
            if old is None:
                if path.exists() and fingerprint(path) == item['new_sha256']:
                    path.unlink()
            else:
                atomic_write(path, old)
                if fingerprint(path) != item['old_sha256']:
                    raise OSError('恢复读回校验失败，保留恢复记录。')
        manifest['state'] = 'rolled_back'
        atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2).encode())

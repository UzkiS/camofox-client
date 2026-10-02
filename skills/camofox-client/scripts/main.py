"""用途：CLI 适配层，只负责参数、文件与输出；业务入口为 Client.call 与组合动作。"""
import argparse
from dataclasses import fields
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from actions import ACTIONS, COMPOSITE
from client import Client
from config import Config, build_config
from doctor import diagnose
from errors import describe
from observe import click_and_observe
from transport import Response


COMPOSITE_HANDLERS = {
    'click-and-observe': click_and_observe,
    'doctor': diagnose,
}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    try:
        options = vars(build_parser().parse_args(argv))
        config_values = {}
        for field in fields(Config):
            value = options.pop(field.name, None)
            if value is not None:
                config_values[field.name] = value
        env_file = options.pop('env_file')
        config, sources, hints = build_config(config_values, env_file=env_file)
        action = options.pop('action')
        output = options.pop('output')
        output_format = options.pop('output_format', 'json')
        args = read_object(options.pop('args_file'))
        expression_file = options.pop('expression_file', None)
        args.update({k: v for k, v in options.items() if v is not None})
        if expression_file:
            if 'expression' in args:
                raise ValueError('expression 与 expression-file 只能提供一个')
            args['expression'] = Path(expression_file).read_text(encoding='utf-8-sig')
        output = prepare_output(output)
        if action in COMPOSITE:
            COMPOSITE[action].validate(args)
            context = {'sources': sources, 'hints': hints, 'env_file': env_file} if action == 'doctor' else {}
            result = COMPOSITE_HANDLERS[action](Client(config), **args, **context)
        else:
            result = Client(config).call(action, **args)
        try:
            emit(result, output, action=action, output_format=output_format)
        except Exception as exc:
            # 已取得业务结果：输出失败不能把它伪装成请求失败，更不能重发动作。
            recovery = result
            if isinstance(result, Response):
                recovery = {'type': 'binary', 'status': result.status, 'contentType': result.content_type,
                            'byteLength': len(result.body), 'saved': False, 'bodyIncluded': False}
            print(json.dumps({'error': describe(exc), 'phase': 'output', 'action': action,
                              'result': recovery}, ensure_ascii=False))
            return 1
        return 0
    except Exception as exc:
        print(json.dumps({'error': describe(exc)}, ensure_ascii=False))
        return 1


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def flag(name):
    return '--' + re.sub(r'(?<!^)(?=[A-Z])', '-', name).replace('_', '-').lower()


def build_parser():
    parser = Parser(description='显式选择 userId，在同一工作流中交替调用不同 Camofox 用户上下文。')
    for field in fields(Config):
        if not field.metadata.get('secret'):
            parser.add_argument(flag(field.name), dest=field.name, type=field.type, help=field.metadata.get('help'))
    parser.add_argument('--env-file', help='显式读取配置文件；未指定时只使用环境变量和默认值')
    subparsers = parser.add_subparsers(dest='action', required=True)
    for name, spec in {**ACTIONS, **COMPOSITE}.items():
        if spec.path:
            help_text = f'{spec.method} {spec.path}'
        else:
            help_text = '组合动作（点击后限时只读观察）' if name == 'click-and-observe' else \
                '组合动作（只读诊断）' if name == 'doctor' else '调用其他 REST 端点'
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument('--args-file', help='动作参数 JSON 对象；- 表示 UTF-8 stdin')
        command.add_argument('--output', help='将响应保存到文件；父目录须已存在，同名文件会覆盖')
        for key, field in spec.fields.items():
            kwargs = {'dest': key, 'default': None}
            if field.kind is bool:
                kwargs['action'] = argparse.BooleanOptionalAction
            else:
                kwargs['type'] = json.loads if field.kind is dict else field.kind
            requirement = '必填' if field.required else '可选'
            kwargs['help'] = requirement + (f'，选项 {field.choices}' if field.choices else '')
            command.add_argument(flag(key), **kwargs)
        if 'expression' in spec.fields:
            command.add_argument('--expression-file', help='从 UTF-8 JS 文件读取 expression')
        if name == 'snapshot':
            command.add_argument('--output-format', dest='output_format', choices=('text', 'json'), default='json',
                                 help='输出呈现方式：text 输出 URL 与可读快照，json 保持服务端原文（默认）')
    return parser


def read_object(path):
    if path is None:
        return {}
    raw = sys.stdin.buffer.read().decode('utf-8-sig') if path == '-' else Path(path).read_text(encoding='utf-8-sig')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('args-file 必须包含 JSON 对象')
    return value


def paginate_hint(result):
    total = result.get('totalChars')
    if result.get('hasMore'):
        return f'[分页] 共 {total} 字符，还有更多内容；使用 --offset {result.get("nextOffset")} 继续。'
    if total is not None:
        return f'[分页] 共 {total} 字符，已到末尾。'
    return '[分页] 服务端未返回分页信息。'


def format_text(result, action):
    if action != 'snapshot':
        raise ValueError(f'--output-format text 仅支持 snapshot，不支持 {action}')
    if not isinstance(result, dict) or not isinstance(result.get('snapshot'), str):
        raise ValueError('服务端返回与 text 快照格式不匹配：缺少 snapshot 字符串字段')
    url = result.get('url')
    lines = [f'URL: {url}' if url is not None else 'URL: (服务端未返回)', result['snapshot'], paginate_hint(result)]
    return '\n'.join(lines) + '\n'


def prepare_output(output):
    """先验证确定会失败的输出条件，不截断或改写目标文件。"""
    if output is None:
        return None
    path = Path(output).expanduser().resolve()
    if not path.parent.is_dir():
        raise ValueError('output 的父目录必须存在')
    if path.exists() and (not path.is_file() or not os.access(path, os.W_OK)):
        raise ValueError('output 必须是可写的普通文件路径')
    with tempfile.TemporaryFile(dir=path.parent):
        pass
    return path


def write_output(path, content):
    """同目录写入后原子替换；失败时保留原文件并清理未完成的临时文件。"""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.camofox-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def emit(result, output, *, action=None, output_format='json'):
    if isinstance(result, Response):
        if output is None:
            raise ValueError('二进制响应需要 --output 文件路径')
        content = result.body
    elif output_format == 'text':
        content = format_text(result, action).encode('utf-8')
    else:
        content = (json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    if output is None:
        sys.stdout.write(content.decode('utf-8'))
    else:
        path = Path(output).expanduser()
        write_output(path, content)
        print(json.dumps({'file': str(path.resolve())}, ensure_ascii=False))


if __name__ == '__main__':
    raise SystemExit(main())
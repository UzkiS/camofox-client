"""用途：定义配置及其唯一默认值；配置读取不依赖 CLI 或业务层。"""
from dataclasses import dataclass, field as config_field, fields
import math
import os
from pathlib import Path
import re
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Config:
    base_url: str = config_field(default='http://localhost:9377', metadata={'help': 'Camofox REST 服务地址'})
    user_id: str = config_field(default='', metadata={'help': '本次调用身份；没有默认身份时访问用户资源必须显式指定'})
    session_key: str = config_field(default='default', metadata={'help': '同一用户内的标签分组，不是 profile'})
    timeout: float = config_field(default=30, metadata={'help': '单次请求网络预算（正数秒；系统 DNS 解析可能超出）'})
    access_key: str = config_field(default='', repr=False, metadata={'secret': True})
    api_key: str = config_field(default='', repr=False, metadata={'secret': True})

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name != 'timeout' and (not isinstance(value, str) or '\r' in value or '\n' in value):
                raise ValueError(f'{field.name} 必须是单行字符串')
        parts = urlsplit(self.base_url)
        parts.port
        if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username
                or parts.password or parts.query or parts.fragment
                or any(c.isspace() for c in self.base_url)):
            raise ValueError('base_url 必须是无凭据、查询和片段的 http/https 服务地址')
        object.__setattr__(self, 'base_url', self.base_url.rstrip('/'))
        if type(self.timeout) not in (int, float) or not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError('timeout 必须是有限正数秒')
        if not self.session_key.strip():
            raise ValueError('session_key 不能为空')


def env_names():
    return {'CAMOFOX_' + field.name.upper() for field in fields(Config)}


def read_env_file(path):
    """解析显式指定的 .env：单行 KEY=value、export 前缀、引号与注释，不做变量展开。"""
    values = {}
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if line.startswith('export '):
            line = line[7:].lstrip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, raw = line.split('=', 1)
        value = raw.lstrip()
        if value.startswith(('"', "'")):
            end = value.find(value[0], 1)
            if end < 0 or (value[end + 1:].strip() and not value[end + 1:].strip().startswith('#')):
                raise ValueError(f'.env 中 {key.strip()} 的引号不完整或尾部不是注释')
            value = value[1:end]
        else:
            value = re.split(r'\s+#', raw, maxsplit=1)[0].strip()
        values[key.strip()] = value
    return values


def configuration_hints(file_values, environ):
    """提示未识别的 CAMOFOX_* 键，不检查其他宿主的配置。"""
    known = env_names()
    seen, hints = set(), []
    for where, values in (('显式配置文件', file_values), ('环境变量', environ)):
        for key in values:
            if key.startswith('CAMOFOX_') and key not in known and key not in seen:
                hints.append(f'{where}的 {key} 不是受支持的配置项，不会被读取')
                seen.add(key)
    return hints


def resolve_config(overrides=None, *, env_file=None, environ=None):
    """返回 (值, 来源, 提示)。

    值仅含被显式提供的字段；来源逐字段记录 cli/env/file/default；提示列出未知配置项。
    优先级：显式字段 > 环境变量 > 显式指定的 .env > Config 默认值。
    """
    environ = os.environ if environ is None else environ
    unknown = (overrides or {}).keys() - {field.name for field in fields(Config)}
    if unknown:
        raise ValueError(f'未知配置：{", ".join(sorted(unknown))}')
    file_values = read_env_file(env_file) if env_file is not None else {}
    values, sources = {}, {}
    for field in fields(Config):
        name = 'CAMOFOX_' + field.name.upper()
        if overrides is not None and field.name in overrides:
            source, raw = 'cli', overrides[field.name]
        elif name in environ:
            source, raw = 'env', environ[name]
        elif name in file_values:
            source, raw = 'file', file_values[name]
        else:
            sources[field.name] = 'default'
            continue
        values[field.name] = float(raw) if field.type is float and source != 'cli' else raw
        sources[field.name] = source
    return values, sources, configuration_hints(file_values, environ)


def build_config(overrides=None, *, env_file=None, environ=None):
    """返回 (Config, 来源, 提示)，供 CLI 与只读诊断共用，避免重复解析配置来源。"""
    values, sources, hints = resolve_config(overrides, env_file=env_file, environ=environ)
    return Config(**values), sources, hints


def load_config(overrides=None, *, env_file=None):
    """显式字段 > 环境变量 > 显式指定的 .env > Config 默认值。"""
    return build_config(overrides, env_file=env_file)[0]
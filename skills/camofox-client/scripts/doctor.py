"""用途：只读诊断配置有效值及来源、健康与版本信息；不回显密钥，不触发副作用端点。"""
from dataclasses import fields
from errors import describe

# 只读诊断允许的公开端点，绝不包含 /pressure/cleanup 等有副作用的接口。
_HEALTH = 'health'
_OPENAPI_PATH = '/openapi.json'
REDACTED = '[REDACTED]'


def _redact(value, secrets):
    """递归把已知密钥值替换为占位符；用于服务端或错误详情回显密钥的情况。"""
    if isinstance(value, dict):
        return {_redact(key, secrets): _redact(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, REDACTED)
        return value
    return value


def _config_summary(config, sources):
    summary = {}
    for field in fields(config):
        value = getattr(config, field.name)
        entry = {'set': bool(value)} if field.metadata.get('secret') else {'value': value}
        summary[field.name] = {**entry, 'source': sources.get(field.name, 'unknown')}
    return summary


def _version(client):
    spec = client.call('request', method='GET', path=_OPENAPI_PATH)
    info = spec.get('info') if isinstance(spec, dict) else None
    return info.get('version') if isinstance(info, dict) else None


def diagnose(client, *, sources=None, hints=None, env_file=None):
    """返回只读诊断结果：配置来源、未知配置项提示、问题列表、health 与版本可用信息。"""
    config = client.config
    result = {
        'config': _config_summary(config, sources or {}),
        'envFile': env_file,
        'hints': list(hints or []),
        'issues': [],
        'healthy': False,
        'health': None,
        'connectError': None,
        'version': None,
        'versionError': None,
    }
    if not config.user_id.strip():
        result['issues'].append('未配置 user_id：除 health 等公开端点外的操作会被拒绝')
    if not config.access_key and not config.api_key:
        result['issues'].append('未配置 access_key/api_key：若目标服务要求认证则会失败')
    try:
        result['health'] = client.call(_HEALTH)
        result['healthy'] = True
    except Exception as exc:
        result['connectError'] = describe(exc)
    try:
        result['version'] = _version(client)
    except Exception as exc:
        result['versionError'] = describe(exc)
    # health 与错误详情可能回显密钥，需对整个诊断结果递归脱敏后再返回。
    secrets = sorted({getattr(config, field.name) for field in fields(config)
                      if field.metadata.get('secret') and getattr(config, field.name)}, key=len, reverse=True)
    return _redact(result, secrets) if secrets else result
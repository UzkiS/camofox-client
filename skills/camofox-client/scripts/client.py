"""用途：协调 camofox 动作、身份、认证与响应；不读取环境、命令行或文件。"""
from urllib.parse import quote, unquote
from actions import ACTIONS
from errors import RequestError
from transport import HttpTransport


class Client:
    def __init__(self, config, *, transport=None):
        self.config = config
        self.transport = transport if transport is not None else HttpTransport(config.base_url, config.timeout)

    def call(self, action, *, timeout=None, **args):
        spec = ACTIONS.get(action)
        if spec is None:
            raise ValueError(f'未知动作：{action}')
        spec.validate(args)
        if action == 'request':
            method, path = args['method'], args['path']
            params, body = dict(args.get('params', {})), dict(args.get('body', {}))
            if method == 'GET' and 'body' in args:
                raise ValueError('GET 不接受 body')
        else:
            method = spec.method
            path = spec.path.format(tabId=quote(args.get('tabId', ''), safe=''))
            payload = {k: v for k, v in args.items() if k != 'tabId'}
            params, body = (payload, {}) if method == 'GET' else ({}, payload)
        self._identity(method, path, params, body)
        token = self._token(method, path)
        send_args = {'params': params, 'body': None if method == 'GET' else body, 'token': token}
        if timeout is not None:
            send_args['timeout'] = timeout
        response = self.transport.send(method, path, **send_args)
        if response.status == 204:
            return None
        if response.content_type.startswith('image/') or response.content_type in ('application/octet-stream', 'application/zip'):
            return response
        if 'json' not in response.content_type:
            raise RequestError('服务端返回了非 JSON/二进制响应', status=response.status,
                               details={'contentType': response.content_type})
        data = response.json()
        if isinstance(data, dict) and (data.get('ok') is False or data.get('error')):
            raise RequestError('服务端操作失败', status=response.status, details=data)
        return data

    def _token(self, method, path):
        parts = path.split('/')
        if len(parts) == 4 and parts[1] == 'sessions' and parts[3] == 'cookies' and method == 'POST':
            return self.config.api_key or self.config.access_key
        trace = (len(parts) in (4, 5) and parts[1] == 'sessions' and parts[3] == 'traces'
                 and (method == 'GET' or (len(parts) == 5 and method == 'DELETE')))
        if trace:
            # Trace 还经过全局 access-key 门禁；未配置时才使用端点接受的 api-key。
            return self.config.access_key or self.config.api_key
        return self.config.access_key

    def _identity(self, method, path, params, body):
        user_id = self.config.user_id
        requires_identity = path not in ('/health', '/openapi.json')
        if requires_identity and not user_id.strip():
            raise ValueError('需要配置 user_id')
        # 用户及分组仅由 Config 决定，request 也不能暗中改写它们。
        for container in (params, body):
            if 'userId' in container or 'sessionKey' in container:
                raise ValueError('userId/sessionKey 由配置管理，不能在请求参数中重复指定')
        if path.startswith('/sessions/') and unquote(path.split('/')[2]) != user_id:
            raise ValueError('sessions 路径中的用户与配置不一致')
        if requires_identity:
            (params if method == 'GET' else body)['userId'] = user_id
        if method == 'POST' and path == '/tabs':
            body['sessionKey'] = self.config.session_key

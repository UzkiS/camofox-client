"""用途：纯 HTTP 传输；不认识用户、动作、配置文件或 camofox 响应语义。"""
from dataclasses import dataclass
from http.client import HTTPException
import json
from urllib import error, parse, request
from deadline import Deadline, DeadlineHTTPHandler, DeadlineHTTPSHandler
from errors import RequestError


@dataclass(frozen=True)
class Response:
    status: int
    content_type: str
    body: bytes

    def json(self):
        try:
            return json.loads(self.body.decode('utf-8-sig'))
        except (ValueError, UnicodeError):
            raise RequestError('响应不是合法 JSON', status=self.status) from None


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpTransport:
    def __init__(self, base_url, timeout):
        self.base_url = base_url
        self.timeout = timeout
        self.opener = request.build_opener(NoRedirect(), DeadlineHTTPHandler(), DeadlineHTTPSHandler())

    def send(self, method, path, *, params=None, body=None, token='', timeout=None):
        decoded = parse.unquote(path)
        if (not path.startswith('/') or path.startswith('//') or '?' in path or '#' in path
                or '\\' in decoded or any(p in ('.', '..') for p in decoded.split('/'))
                or any(c.isspace() or ord(c) < 32 for c in path)):
            raise ValueError('path 必须为同一服务的相对路径；查询放入 params')
        query = {k: str(v).lower() if type(v) is bool else v
                 for k, v in (params or {}).items() if v is not None}
        url = self.base_url + path
        if query:
            url += '?' + parse.urlencode(query, doseq=True)
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
            headers['Content-Type'] = 'application/json; charset=utf-8'
        req = request.Request(url, data=data, headers=headers, method=method)
        seconds = self.timeout if timeout is None else timeout
        req.network_deadline = Deadline(seconds)
        status = None
        try:
            try:
                with self.opener.open(req, timeout=seconds) as response:
                    status = response.status
                    content = response.read()
                    req.network_deadline.remaining()
                    return Response(status, response.headers.get_content_type(), content)
            except error.HTTPError as exc:
                status = exc.code
                with exc:
                    raw = exc.read(65536).decode('utf-8', errors='replace')
                    req.network_deadline.remaining()
                try:
                    details = json.loads(raw)
                except ValueError:
                    details = raw
                raise RequestError(f'HTTP {status}', status=status, details=details) from None
        except (error.URLError, OSError, HTTPException) as exc:
            raise RequestError(f'连接或响应读取失败：{exc}', status=status) from None

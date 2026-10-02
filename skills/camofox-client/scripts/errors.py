"""用途：传递请求失败信息，不推测服务端故障原因。"""


class RequestError(Exception):
    def __init__(self, message, *, status=None, details=None):
        super().__init__(message)
        self.status = status
        self.details = details


def describe(exc):
    """把异常转成 CLI 与组合结果共用的错误对象，不推测服务端故障原因。"""
    info = {'message': str(exc)}
    if getattr(exc, 'status', None) is not None:
        info['http_status'] = exc.status
    if getattr(exc, 'details', None) is not None:
        info['details'] = exc.details
    return info

"""用途：为 urllib 的连接、发送、响应头和正文读取提供共享网络预算。"""
from functools import partial
import http.client
import io
import math
import socket
import time
from urllib import request


class Deadline:
    def __init__(self, seconds):
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError('请求超时必须是有限正数秒')
        self.end = time.monotonic() + seconds

    def remaining(self):
        remaining = self.end - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('网络请求预算已耗尽')
        return remaining


class _DeadlineReader(io.RawIOBase):
    def __init__(self, sock, deadline):
        self.sock = sock
        self.deadline = deadline
        self.raw = sock.makefile('rb', buffering=0)

    def readable(self):
        return True

    def readinto(self, buffer):
        self.sock.settimeout(self.deadline.remaining())
        size = self.raw.readinto(buffer)
        self.deadline.remaining()
        return size

    def close(self):
        try:
            self.raw.close()
        finally:
            super().close()


class _DeadlineResponse(http.client.HTTPResponse):
    def __init__(self, sock, *, deadline, **kwargs):
        super().__init__(sock, **kwargs)
        self.fp.close()
        # 在缓冲层之下检查每次 recv，避免 readline/read 被持续小块数据无限延长。
        self.fp = io.BufferedReader(_DeadlineReader(sock, deadline))


class _DeadlineConnection:
    def __init__(self, *args, deadline, **kwargs):
        self.deadline = deadline
        super().__init__(*args, **kwargs)
        self._create_connection = self._connect_socket
        self.response_class = partial(_DeadlineResponse, deadline=deadline)

    def _connect_socket(self, address, timeout=None, source_address=None):
        # 系统 DNS 是同步调用，无法靠 socket timeout 中断；返回后不再执行过期连接。
        self.deadline.remaining()
        addresses = socket.getaddrinfo(*address, 0, socket.SOCK_STREAM)
        failure = None
        for family, kind, protocol, _, target in addresses:
            remaining = self.deadline.remaining()
            sock = socket.socket(family, kind, protocol)
            try:
                sock.settimeout(remaining)
                if source_address:
                    sock.bind(source_address)
                sock.connect(target)
                sock.settimeout(self.deadline.remaining())
                return sock
            except OSError as exc:
                sock.close()
                failure = exc
        if failure is not None:
            raise failure
        raise OSError('DNS 未返回可用地址')

    def _tunnel(self):
        super()._tunnel()
        self.sock.settimeout(self.deadline.remaining())

    def send(self, data):
        if self.sock is None and self.auto_open:
            self.connect()
        if self.sock is not None:
            self.sock.settimeout(self.deadline.remaining())
        super().send(data)
        self.deadline.remaining()


class _HTTPConnection(_DeadlineConnection, http.client.HTTPConnection):
    pass


class _HTTPSConnection(_DeadlineConnection, http.client.HTTPSConnection):
    pass


class DeadlineHTTPHandler(request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(partial(_HTTPConnection, deadline=req.network_deadline), req)


class DeadlineHTTPSHandler(request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(partial(_HTTPSConnection, deadline=req.network_deadline), req, context=self._context)

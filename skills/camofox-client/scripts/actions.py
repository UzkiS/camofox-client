"""用途：动作及参数的唯一规格，供业务调用和 CLI 共用；不执行网络或文件操作。"""
from dataclasses import dataclass
import math
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Field:
    kind: type = str
    required: bool = False
    choices: tuple = ()
    minimum: int | None = None
    allow_empty: bool = False

    def validate(self, name, value):
        # int 是 float 的合法取值（如 JSON 中的 5 传给秒数参数），语义等价。
        if type(value) is not self.kind and not (self.kind is float and type(value) is int):
            raise ValueError(f'{name} 必须为 {self.kind.__name__}')
        if self.kind is float and not math.isfinite(value):
            raise ValueError(f'{name} 必须是有限数')
        if self.kind is str and not self.allow_empty and not value.strip():
            raise ValueError(f'{name} 不能为空')
        if self.choices and value not in self.choices:
            raise ValueError(f'{name} 必须为 {self.choices}')
        if self.minimum is not None and value < self.minimum:
            raise ValueError(f'{name} 不能小于 {self.minimum}')


@dataclass(frozen=True)
class Action:
    method: str
    path: str
    fields: dict
    check: object = None

    def validate(self, args):
        unknown = args.keys() - self.fields.keys()
        if unknown:
            raise ValueError(f'未知参数：{", ".join(sorted(unknown))}')
        for name, field in self.fields.items():
            if name in args:
                field.validate(name, args[name])
            elif field.required:
                raise ValueError(f'缺少参数：{name}')
        if self.check:
            self.check(args)


def target(args):
    if not (args.get('ref') or args.get('selector')):
        raise ValueError('需要 ref 或 selector')


def type_target(args):
    if args.get('mode') != 'keyboard':
        target(args)


def web_url(args):
    if 'url' in args:
        parts = urlsplit(args['url'])
        if parts.scheme not in ('http', 'https') or not parts.hostname:
            raise ValueError('url 必须是 http/https 地址；空白页请省略 url')


def navigation(args):
    if ('url' in args) == ('macro' in args):
        raise ValueError('url 与 macro 必须且只能提供一个')
    web_url(args)


TAB = {'tabId': Field(required=True)}
TARGET = {'ref': Field(), 'selector': Field()}
ACTIONS = {
    'health': Action('GET', '/health', {}),
    'tabs': Action('GET', '/tabs', {}),
    'create': Action('POST', '/tabs', {'url': Field(), 'trace': Field(bool)}, web_url),
    'navigate': Action('POST', '/tabs/{tabId}/navigate', {**TAB, 'url': Field(), 'macro': Field(), 'query': Field()}, navigation),
    'snapshot': Action('GET', '/tabs/{tabId}/snapshot', {**TAB, 'format': Field(choices=('text', 'json')),
                   'offset': Field(int, minimum=0), 'includeScreenshot': Field(bool)}),
    'click': Action('POST', '/tabs/{tabId}/click', {**TAB, **TARGET}, target),
    'type': Action('POST', '/tabs/{tabId}/type', {**TAB, **TARGET, 'text': Field(required=True, allow_empty=True),
                   'mode': Field(choices=('fill', 'keyboard')), 'delay': Field(int, minimum=0), 'submit': Field(bool)}, type_target),
    'press': Action('POST', '/tabs/{tabId}/press', {**TAB, 'key': Field(required=True)}),
    'scroll': Action('POST', '/tabs/{tabId}/scroll', {**TAB, 'direction': Field(required=True, choices=('up', 'down', 'left', 'right')),
                     'amount': Field(int, minimum=1)}),
    'evaluate': Action('POST', '/tabs/{tabId}/evaluate', {**TAB, 'expression': Field(required=True)}),
    'screenshot': Action('GET', '/tabs/{tabId}/screenshot', {**TAB, 'fullPage': Field(bool)}),
    'images': Action('GET', '/tabs/{tabId}/images', {**TAB, 'includeData': Field(bool), 'maxBytes': Field(int, minimum=1), 'limit': Field(int, minimum=1)}),
    'links': Action('GET', '/tabs/{tabId}/links', {**TAB, 'offset': Field(int, minimum=0), 'limit': Field(int, minimum=1)}),
    'close': Action('DELETE', '/tabs/{tabId}', TAB),
    **{name: Action('POST', '/tabs/{tabId}/' + name, TAB) for name in ('back', 'forward', 'refresh')},
    'request': Action('', '', {'method': Field(required=True, choices=('GET', 'POST', 'PUT', 'PATCH', 'DELETE')),
                              'path': Field(required=True), 'params': Field(dict), 'body': Field(dict)}),
}

# 组合动作不走单一 REST 请求，由 main 分派到 observe / doctor；参数规格仍在此集中定义。
COMPOSITE = {
    'click-and-observe': Action('', '', {**TAB, **TARGET, 'expectUrlPrefix': Field(),
                                         'observeTimeout': Field(float, minimum=0.05),
                                         'pollInterval': Field(float, minimum=0.01),
                                         'requestTimeout': Field(float, minimum=0.05)}, target),
    'doctor': Action('', '', {}),
}

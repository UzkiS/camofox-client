"""用途：点击后限时只读观察同用户标签页变化；复用 Client/Config，不重发点击。"""
import time
from actions import COMPOSITE
from errors import RequestError, describe

DEFAULT_OBSERVE_TIMEOUT = 5.0
DEFAULT_POLL_INTERVAL = 0.25
DEFAULT_REQUEST_TIMEOUT = 5.0


def _read_tabs(client, timeout):
    """只读拉取同 userId 的标签页列表，并按剩余预算限制单次 HTTP 超时。"""
    data = client.call('tabs', timeout=timeout)
    tabs = data.get('tabs') if isinstance(data, dict) else None
    if not isinstance(tabs, list):
        raise RequestError('tabs 响应缺少 tabs 列表', details=data if isinstance(data, dict) else None)
    for tab in tabs:
        if (not isinstance(tab, dict) or not isinstance(tab.get('tabId'), str)
                or not tab['tabId'].strip()):
            raise RequestError('tabs 元素必须是含非空字符串 tabId 的对象')
    return tabs


def _source_url(tabs, tab_id):
    for tab in tabs:
        if tab.get('tabId') == tab_id:
            return tab.get('url')
    return None


def _candidate(tab, prefix):
    candidate = {
        'tabId': tab.get('tabId'),
        'url': tab.get('url'),
        'title': tab.get('title'),
        'sessionKey': tab.get('listItemId'),
    }
    if prefix is not None:
        candidate['matchesExpectPrefix'] = str(tab.get('url') or '').startswith(prefix)
    return candidate


def click_and_observe(client, *, tabId, ref=None, selector=None, expectUrlPrefix=None,
                      observeTimeout=DEFAULT_OBSERVE_TIMEOUT, pollInterval=DEFAULT_POLL_INTERVAL,
                      requestTimeout=DEFAULT_REQUEST_TIMEOUT, clock=None, sleep=None):
    """执行一次 click，并在有限窗口内只读观察同 userId 的标签页差集。

    约束：只发送一次 click，绝不重试；观察只调用只读的 tabs，不对候选页执行
    evaluate/snapshot；候选是同用户全部标签页的差集，不按分组过滤，最多用 URL
    前缀标记。业务层失败（点击/观察/基线）作为结果字段返回，不向外抛出。

    超时预算：基线用 min(requestTimeout, config.timeout)；点击沿用 config.timeout；
    观察窗口在点击结束之后才独立计时，故慢基线或慢点击不会耗尽观察时间；每次观察
    请求的超时为 min(requestTimeout, config.timeout, 剩余窗口)。
    """
    # 与 CLI 共用同一份规格校验：Python 直接调用也不能绕过 NaN/inf/0 检查。
    params = {'tabId': tabId, 'observeTimeout': observeTimeout, 'pollInterval': pollInterval,
              'requestTimeout': requestTimeout}
    for key, value in (('ref', ref), ('selector', selector), ('expectUrlPrefix', expectUrlPrefix)):
        if value is not None:
            params[key] = value
    COMPOSITE['click-and-observe'].validate(params)

    clock = time.monotonic if clock is None else clock
    sleep = time.sleep if sleep is None else sleep
    config_timeout = client.config.timeout

    result = {
        'sourceTabId': tabId,
        'target': {'ref': ref, 'selector': selector},
        'expectUrlPrefix': expectUrlPrefix,
        'observeTimeout': observeTimeout,
        'click': {'executed': False},
        'sourceUrl': None,
        'baselineCount': None,
        'candidates': [],
        'polls': 0,
        'observe': {'endedBy': None, 'error': None},
    }

    # 1) 只读基线：拿不到基线就不点击，避免无法观察的无依据操作。
    try:
        baseline = _read_tabs(client, min(requestTimeout, config_timeout))
    except Exception as exc:
        result['observe']['endedBy'] = 'baseline_failed'
        result['observe']['error'] = describe(exc)
        result['click']['skipped'] = 'baseline_failed'
        return result
    baseline_ids = {tab.get('tabId') for tab in baseline}
    result['baselineCount'] = len(baseline)
    result['sourceUrl'] = _source_url(baseline, tabId)

    # 2) 单次点击，不重试；无论服务端是否报错都保留原始结果或错误。
    click_args = {'tabId': tabId}
    if ref is not None:
        click_args['ref'] = ref
    if selector is not None:
        click_args['selector'] = selector
    try:
        result['click'] = {'executed': True, 'uncertain': False, 'result': client.call('click', **click_args)}
    except Exception as exc:
        result['click'] = {'executed': True, 'uncertain': True, 'error': describe(exc)}

    # 3) 观察窗口从点击结束后才开始计时；限时只读轮询，不无限等待。
    observe_deadline = clock() + observeTimeout
    while True:
        remaining = observe_deadline - clock()
        if remaining <= 0:
            result['observe']['endedBy'] = 'timeout'
            break
        try:
            tabs = _read_tabs(client, min(requestTimeout, config_timeout, remaining))
        except Exception as exc:
            result['observe']['endedBy'] = 'timeout' if clock() >= observe_deadline else 'observe_error'
            result['observe']['error'] = describe(exc)
            break
        result['polls'] += 1
        if clock() >= observe_deadline:
            result['observe']['endedBy'] = 'timeout'
            break
        url = _source_url(tabs, tabId)
        if url is not None:
            result['sourceUrl'] = url
        new_tabs = [tab for tab in tabs if tab.get('tabId') not in baseline_ids]
        result['candidates'] = [_candidate(tab, expectUrlPrefix) for tab in new_tabs]
        if new_tabs:
            result['observe']['endedBy'] = 'new_tabs_found'
            break
        pause = min(pollInterval, max(0.0, observe_deadline - clock()))
        if pause > 0:
            sleep(pause)
    return result
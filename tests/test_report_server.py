# -*- coding: utf-8 -*-
"""报告服务路由：静态文件、404、参数校验（离线，起本地端口）"""
import json
import threading
import urllib.error
import urllib.request

import pytest

import report_server


@pytest.fixture
def base_url():
    server = report_server.Server(("127.0.0.1", 0), report_server.Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.read()


def test_echarts_static_asset(base_url):
    status, body = _get(base_url + "/echarts.min.js")
    assert status == 200
    assert len(body) > 1000        # 内置图表库，离线也能出图


def test_unknown_path_returns_404(base_url):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base_url + "/no-such-route")
    assert e.value.code == 404


def test_researchfile_rejects_bad_code(base_url):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base_url + "/researchfile?code=abc&file=x.html")
    assert e.value.code == 404


def test_watchlist_endpoint_returns_codes(base_url):
    status, body = _get(base_url + "/watchlist")
    assert status == 200
    payload = json.loads(body.decode("utf-8"))
    assert payload["ok"] is True
    assert isinstance(payload["codes"], list)

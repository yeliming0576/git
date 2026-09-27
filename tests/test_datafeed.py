# -*- coding: utf-8 -*-
"""统一数据层：降级链、重试退避、限流（全部离线）"""
import pytest

import datafeed


def test_fallback_prefers_primary_and_tags_source():
    value, source = datafeed.fallback("测试", [
        ("主源", lambda: {"a": 1}),
        ("akshare", lambda: {"a": 2}),
    ])
    assert source == "主源"
    assert value == {"a": 1, "_source": "主源"}


def test_fallback_skips_empty_and_broken_sources():
    def boom():
        raise RuntimeError("接口挂了")

    value, source = datafeed.fallback("测试", [
        ("主源", lambda: []),
        ("东财", boom),
        ("akshare", lambda: [{"code": "600000"}]),
    ])
    assert source == "akshare"
    assert value == [{"code": "600000"}]


def test_fallback_raises_when_all_sources_fail():
    with pytest.raises(datafeed.DataFeedError):
        datafeed.fallback("测试", [("主源", lambda: None),
                                   ("akshare", lambda: None)])


def test_kline_uses_primary_rows(rows_factory):
    rows = rows_factory([10.0, 11.0])
    got, source = datafeed.kline("600000", days=2, native=lambda: rows)
    assert source == "主源"
    assert got == rows


def test_quote_falls_back_then_fails_cleanly(monkeypatch):
    def boom(code):
        raise datafeed.DataFeedError("未安装")
    monkeypatch.setattr(datafeed, "ak_quote", boom)
    monkeypatch.setattr(datafeed, "ef_quote", boom)
    with pytest.raises(datafeed.DataFeedError):
        datafeed.quote("600000", native=None)


def test_http_get_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    class Resp:
        status_code = 200
        text = "ok"

    def fake_get(url, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("限流")
        return Resp()

    monkeypatch.setattr(datafeed.requests, "get", fake_get)
    monkeypatch.setattr(datafeed.time, "sleep", lambda *_: None)
    r = datafeed.http_get("https://example.com/x", retries=3, min_interval=0)
    assert r.text == "ok"
    assert calls["n"] == 3


def test_http_get_raises_after_exhausting_retries(monkeypatch):
    def fake_get(url, **kw):
        raise ConnectionError("断网")

    monkeypatch.setattr(datafeed.requests, "get", fake_get)
    monkeypatch.setattr(datafeed.time, "sleep", lambda *_: None)
    with pytest.raises(datafeed.DataFeedError):
        datafeed.http_get("https://example.com/x", retries=2, min_interval=0)


def test_throttle_spacing(monkeypatch):
    slept = []
    monkeypatch.setattr(datafeed.time, "sleep", lambda s: slept.append(s))
    datafeed._last_call.clear()
    datafeed._throttle("https://example.com/a", min_interval=1.0)
    datafeed._throttle("https://example.com/a", min_interval=1.0)
    assert slept and slept[0] > 0, "同一 host 第二次请求应等待"

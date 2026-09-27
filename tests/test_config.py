# -*- coding: utf-8 -*-
"""配置层：默认值、config.json 覆盖优先级、容错与下游接线（离线）"""
import json

import pytest

from stockapp import config


@pytest.fixture
def restore_config():
    """用例结束后把 config 模块属性还原，避免相互污染"""
    snapshot = dict(config.defaults())
    yield snapshot
    for key, value in snapshot.items():
        setattr(config, key, value)


def test_defaults_match_documented_values(restore_config):
    assert config.PRICE_FLOOR == 2.0
    assert config.PRICE_CAP_FLOOR == 12.0
    assert config.LOWVAL_LIMIT == 5
    assert config.COST_PER_SIDE == 0.0015
    assert config.TARGET_ATR_MULT == 4.0
    assert config.PORT == 8765
    assert config.HOST == "0.0.0.0"
    assert config.HTTP_RETRIES == 3


def test_json_overrides_defaults(tmp_path, restore_config):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"PRICE_CAP_FLOOR": 15,
                                "LOWVAL_LIMIT": 8,
                                "PORT": 9000}), encoding="utf-8")
    result = config.load(str(path))
    assert set(result["applied"]) == {"PRICE_CAP_FLOOR", "LOWVAL_LIMIT", "PORT"}
    assert config.PRICE_CAP_FLOOR == 15
    assert config.LOWVAL_LIMIT == 8
    assert config.PORT == 9000


def test_unknown_key_and_type_mismatch_are_ignored(tmp_path, restore_config):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"NOT_A_KEY": 1, "PRICE_FLOOR": "两块"}),
                    encoding="utf-8")
    result = config.load(str(path))
    assert result["applied"] == []
    ignored_keys = [k for k, _ in result["ignored"]]
    assert "NOT_A_KEY" in ignored_keys and "PRICE_FLOOR" in ignored_keys
    assert config.PRICE_FLOOR == 2.0, "类型不符应回退默认值"


def test_list_overrides_tuple_key(tmp_path, restore_config):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"LOWVAL_TURNOVER_RELAX": [1.5, 15]}),
                    encoding="utf-8")
    config.load(str(path))
    assert config.LOWVAL_TURNOVER_RELAX == (1.5, 15)


def test_missing_file_is_noop(tmp_path, restore_config):
    result = config.load(str(tmp_path / "nope.json"))
    assert result["applied"] == []
    assert config.PRICE_FLOOR == 2.0


def test_broken_json_falls_back_to_defaults(tmp_path, restore_config):
    path = tmp_path / "config.json"
    path.write_text("{不是合法 JSON", encoding="utf-8")
    result = config.load(str(path))
    assert result["applied"] == []
    assert config.PRICE_FLOOR == 2.0


def test_downstream_modules_are_wired_to_config():
    from stockapp import backtest_engine as E
    from stockapp import datafeed
    from stockapp import quant_engine as Q
    from stockapp import selection
    from stockapp import v2

    assert selection.PRICE_FLOOR == config.PRICE_FLOOR
    assert selection.LOWVAL_LIMIT == config.LOWVAL_LIMIT
    assert Q.COST_RATE == config.COST_PER_SIDE
    assert E.COST_PER_SIDE == config.COST_PER_SIDE
    assert E.TARGET_ATR_MULT == config.TARGET_ATR_MULT
    assert v2.RISK_PER_TRADE == config.RISK_PER_TRADE
    assert v2.COST_PER_SIDE == config.COST_PER_SIDE
    assert datafeed.DEFAULT_RETRIES == config.HTTP_RETRIES

"""회화앱 연동 부분 테스트. 실행: python -m pytest tests"""

import datetime
import importlib.machinery
import importlib.util
import json
from pathlib import Path

_path = Path(__file__).resolve().parent.parent / "time_alarm.pyw"
_loader = importlib.machinery.SourceFileLoader("time_alarm", str(_path))
_spec = importlib.util.spec_from_loader("time_alarm", _loader)
ta = importlib.util.module_from_spec(_spec)
_loader.exec_module(ta)

DAY = datetime.date(2026, 11, 4)


def make_hoehwa(tmp_path, goal=None, seconds=None):
    folder = tmp_path / "회화앱"
    (folder / "talk").mkdir(parents=True)
    (folder / "main.pyw").write_text("", encoding="utf-8")
    if goal is not None:
        (folder / "settings.json").write_text(json.dumps({"api_key": "secret", "daily_goal_min": goal}), "utf-8")
    if seconds is not None:
        (folder / "data" / "daily").mkdir(parents=True)
        (folder / "data" / "daily" / f"{DAY.isoformat()}.json").write_text(json.dumps({"seconds": seconds}), "utf-8")
    return folder


def test_english_dir_accepts_only_real_folder(tmp_path):
    folder = make_hoehwa(tmp_path)
    assert ta.english_dir(str(folder)) == folder
    assert ta.english_dir(None) is None
    assert ta.english_dir(str(tmp_path)) is None  # main.pyw, talk 폴더가 없음


def test_english_progress_reads_goal_and_today(tmp_path):
    assert ta.english_progress(make_hoehwa(tmp_path, goal=30, seconds=12 * 60 + 30), DAY) == (12, 30)


def test_english_progress_defaults_when_missing(tmp_path):
    assert ta.english_progress(make_hoehwa(tmp_path), DAY) == (0, 20)


def test_english_message():
    assert ta.english_message(20, 20) is None  # 이미 했으면 조용히
    assert "영어 회화 시간이에요" in ta.english_message(0, 20)
    assert ta.english_message(12, 20) == "오늘 영어 회화 12분 했어요. 8분 남았어요."


def test_english_due():
    cfg = {"path": "x", "time": "21:00", "fired_on": None}
    assert not ta.english_due(cfg, datetime.datetime(2026, 11, 4, 20, 59))
    assert ta.english_due(cfg, datetime.datetime(2026, 11, 4, 21, 0))
    assert ta.english_due(cfg, datetime.datetime(2026, 11, 4, 23, 0))  # 늦게 켜도 그날 한 번
    assert not ta.english_due(dict(cfg, fired_on="2026-11-04"), datetime.datetime(2026, 11, 4, 22, 0))


def test_store_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(ta, "STORE_PATH", tmp_path / "alarms.json")
    store = ta.load_store()
    assert store["english"] == {"path": None, "time": "21:00", "fired_on": None}
    assert store["alert_modes"]["english"] == "both"  # 팝업의 [회화앱 열기]를 쓸 수 있게

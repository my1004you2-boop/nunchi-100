"""시간 알림: 작업/휴식 사이클과 특정 시각 알람을 음성으로 알려 준다."""

import asyncio
import ctypes
import datetime
import functools
import json
import math
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import filedialog, ttk
from tkinter import font as tkfont

import customtkinter as ctk
import pystray
from PIL import Image, ImageDraw, ImageFont

APP_VERSION = "1.0.0"

BASE_DIR = Path(__file__).resolve().parent
STORE_PATH = BASE_DIR / "alarms.json"
# 'PC 켜면 자동으로 실행'은 Windows 시작 프로그램 폴더의 바로가기로 켜고 끈다
STARTUP_LINK = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup/시간 알림.lnk"
RECORDS_PATH = BASE_DIR / "records.json"
BACKUP_DIR = BASE_DIR / "기록 백업"

# 사용 기록: 꺼진 시각을 남기려고 이 간격(초)마다 저장한다
HEARTBEAT_SEC = 30
# 이 시간(초) 안에 프로그램이 다시 켜지면 PC를 끈 게 아니라 프로그램만 다시 켠 것으로 보고 이어서 기록한다
RESUME_GAP_SEC = 180
# 1년 지난 기록을 "보관"하기로 하면 이만큼(일) 뒤에 다시 물어본다
SNOOZE_DAYS = 30

# "sapi"는 PC에 설치된 한국어 음성(오프라인), 나머지는 Microsoft 온라인 음성(edge-tts)
VOICES = {
    "sapi": "해미 · 여성 (기본, 오프라인)",
    "ko-KR-SunHiNeural": "선희 · 여성 (온라인)",
    "ko-KR-InJoonNeural": "인준 · 남성 (온라인)",
    "ko-KR-HyunsuMultilingualNeural": "현수 · 남성 (온라인)",
}

# 빠르기: -10(느림) ~ 10(빠름), 볼륨: 0 ~ 100
# duck: 알림 동안 다른 프로그램 소리를 줄일지, duck_level: 줄였을 때 원래 소리의 몇 %로 할지
DEFAULT_VOICE = {"name": "sapi", "rate": 0, "volume": 100, "duck": True, "duck_level": 20}

# 소리를 줄인 동안 원래 볼륨을 적어 두는 파일 (도중에 강제 종료돼도 다음 실행 때 되돌리기 위해)
DUCK_STATE_PATH = Path(tempfile.gettempdir()) / "time_alarm_duck.json"

DEFAULT_CYCLE = {
    "enabled": True,
    "work_min": 60,
    "break_min": 10,
    "break_msg": "{시각}입니다. {휴식}분간 휴식하세요.",
    "work_msg": "{시각}입니다. 휴식이 끝났습니다. 다시 시작하세요.",
}

# 이 시간(초) 이상 루프가 멈춰 있었다면 절전에서 깨어난 것으로 본다
SLEEP_GAP_SEC = 30
# 게임 실행 여부를 확인하는 간격(초)
GAME_CHECK_SEC = 5

WEEKDAYS = "월화수목금토일"

# 캘린더 일정 알림: 기본으로 몇 분 전부터 알릴지, 고를 수 있는 시작 전 시간과 간격(분)
EVENT_LEAD_MIN = 120
LEADS = {"10분": 10, "30분": 30, "1시간": 60, "2시간": 120, "3시간": 180, "6시간": 360, "12시간": 720, "1일": 1440, "2일": 2880}
# -1 = 한 번만 (시작 전 그 시각에 한 번)
INTERVALS = {"알림 없음": 0, "한 번만": -1, "10분": 10, "15분": 15, "20분": 20, "30분": 30, "1시간": 60, "2시간": 120}
MAX_ALERTS = 50
# 일정 색 (하루 일정 기본 보라, 기간 일정 기본 노랑)
EVENT_COLORS = {
    "purple": "#8b6cff",
    "yellow": "#e9c46a",
    "blue": "#4dabf7",
    "green": "#38d9a9",
    "pink": "#f783ac",
}
REPEAT_LABELS = {"once": "한 번만", "daily": "매일", "weekdays": "평일", "days": "요일 선택"}

# 트레이 아이콘 색
COLORS = {
    "work": (46, 160, 67),
    "break": (56, 120, 220),
    "game": (150, 80, 200),
    "off": (90, 110, 120),
    "paused": (140, 140, 140),
}

# 창 색 (다크 게이밍 테마)
C = {
    "bg": "#12141c",
    "side": "#0d0f15",
    "side_hover": "#171a24",
    "card": "#1a1d29",
    "card_hl": "#1d1934",
    "input": "#11131a",
    "btn": "#232738",
    "btn_hover": "#2c3147",
    "line": "#2a2f44",
    "line_hl": "#3a3170",
    "fg": "#e6e8f2",
    "sub": "#8a8fab",
    "dim": "#4a5070",
    "accent": "#8b6cff",
    "accent_hover": "#7a5cf0",
    "accent_soft": "#2a2450",
    "mode": "#b7a6ff",
    "good": "#38d9a9",
    "brk": "#4dabf7",
    "warn": "#ff8787",
    "track": "#262a3c",
    "off": "#343a52",
    "sel": "#1e1a38",
    "game_bg": "#241d47",
    "game_fg": "#c9bcff",
    "game_icon": "#262046",
}

UI_FONT = "Malgun Gothic"
MONO_FONT = "Consolas"
FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

# 사이드바 메뉴: (키, 이름, Segoe 아이콘 글꼴 코드)
NAV = [
    ("main", "메인 알람", 0xE916),
    ("alarms", "특정 시각", 0xEA8F),
    ("calendar", "캘린더", 0xE787),
    ("voice", "음성", 0xE767),
    ("games", "게임 목록", 0xE7FC),
    ("records", "사용 기록", 0xE81C),
    ("settings", "설정", 0xE713),
]

user32 = ctypes.windll.user32


def load_store():
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {
        "cycle": {**DEFAULT_CYCLE, **data.get("cycle", {})},
        "voice": {**DEFAULT_VOICE, **data.get("voice", {})},
        "alarms": data.get("alarms", []),
        "games": data.get("games", []),
        # 캘린더: [{"id", "date": YYYY-MM-DD, "time": HH:MM, "title", "memo", "interval": 분}]
        "events": data.get("events", []),
        "briefing": data.get("briefing", True),  # PC를 켤 때 오늘 일정 브리핑
        "briefed_on": data.get("briefed_on"),  # 마지막으로 브리핑한 날짜
    }


def save_store(store):
    tmp = STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STORE_PATH)


# ----- 사용 기록 -----
def load_records():
    try:
        data = json.loads(RECORDS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {
        "sessions": data.get("sessions", []),  # [{"start": ISO 시각, "end": ISO 시각}]
        "counts": data.get("counts", {}),  # {"YYYY-MM-DD": {"work": 횟수, "break": 횟수}}
        "snooze_until": data.get("snooze_until"),  # 1년 지난 기록을 다시 물어볼 날짜
    }


def save_records(records):
    tmp = RECORDS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, RECORDS_PATH)


def parse_ts(text):
    return datetime.datetime.fromisoformat(text)


def to_ts(moment):
    return moment.isoformat(timespec="seconds")


def one_year_before(day):
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 2월 29일
        return day.replace(year=day.year - 1, day=28)


def fmt_duration(seconds):
    hours, minutes = divmod(int(seconds // 60), 60)
    if hours:
        return f"{hours}시간 {minutes}분" if minutes else f"{hours}시간"
    return f"{minutes}분"


def session_row(session, current=False):
    start, end = parse_ts(session["start"]), parse_ts(session["end"])
    days = (end.date() - start.date()).days
    if current:
        off = "사용 중"
    elif days == 0:
        off = end.strftime("%H:%M")
    elif days == 1:
        off = "다음날 " + end.strftime("%H:%M")
    else:
        off = end.strftime("%m-%d %H:%M")
    return (
        start.strftime("%Y-%m-%d"),
        WEEKDAYS[start.weekday()],
        start.strftime("%H:%M"),
        off,
        fmt_duration((end - start).total_seconds()),
    )


def records_text(sessions):
    first = parse_ts(sessions[0]["start"]).strftime("%Y-%m-%d")
    last = parse_ts(sessions[-1]["start"]).strftime("%Y-%m-%d")
    lines = [
        f"PC 사용 기록 ({first} ~ {last})",
        f"내보낸 날짜: {datetime.datetime.now():%Y-%m-%d %H:%M}",
        "",
        "날짜\t\t요일\t켜진 시간\t꺼진 시간\t사용 시간",
    ]
    lines += ["\t".join(session_row(s)) for s in sessions]
    return "\n".join(lines) + "\n"


def korean_time(now=None):
    now = now or datetime.datetime.now()
    ampm = "오전" if now.hour < 12 else "오후"
    hour = now.hour % 12 or 12
    if now.minute == 0:
        return f"{ampm} {hour}시"
    return f"{ampm} {hour}시 {now.minute}분"


def fmt_min(value):
    return str(int(value)) if float(value).is_integer() else str(value)


def cycle_message(template, cycle):
    return template.format(
        시각=korean_time(), 작업=fmt_min(cycle["work_min"]), 휴식=fmt_min(cycle["break_min"])
    )


def clip(text, limit):
    return text if len(text) <= limit else text[: limit - 1] + "…"


def speak_sapi(text, rate=0, volume=100):
    """Windows 기본 음성(System.Speech)으로 읽는다. 한국어 음성이 있으면 우선 사용."""
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$v = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq 'ko-KR' } | Select-Object -First 1;"
        "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) };"
        "$s.Rate = [int]$env:TA_RATE; $s.Volume = [int]$env:TA_VOLUME;"
        "$s.Speak($env:TA_TEXT)"
    )
    env = dict(os.environ, TA_TEXT=text, TA_RATE=str(rate), TA_VOLUME=str(volume))
    return subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def synth_online(text, voice, rate=0):
    """Microsoft 온라인 음성으로 mp3를 만든다. 인터넷이 없으면 예외가 난다."""
    import edge_tts

    path = Path(tempfile.gettempdir()) / f"time_alarm_{uuid.uuid4().hex[:8]}.mp3"
    communicate = edge_tts.Communicate(text, voice, rate=f"{rate * 5:+d}%")
    try:
        asyncio.run(asyncio.wait_for(communicate.save(str(path)), timeout=15))
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def play_mp3(path, volume=100):
    def mci(command):
        return ctypes.windll.winmm.mciSendStringW(command, None, 0, None)

    alias = "ta" + path.stem[-8:]
    try:
        if mci(f'open "{path}" type mpegvideo alias {alias}') != 0:
            raise OSError("mp3 재생 실패")
        try:
            mci(f"setaudio {alias} volume to {volume * 10}")
            mci(f"play {alias} wait")
        finally:
            mci(f"close {alias}")
    finally:
        path.unlink(missing_ok=True)


class Ducker:
    """알림이 나오는 동안 다른 프로그램 소리를 잠깐 줄였다가 되돌린다 (Windows 볼륨 믹서와 같은 방식)."""

    STEPS = 10

    def __init__(self):
        self.saved = []  # (볼륨 조절기, 프로그램 이름, 원래 볼륨, 줄인 볼륨)

    @staticmethod
    def sessions():
        from pycaw.pycaw import AudioUtilities

        me = os.getpid()
        for session in AudioUtilities.GetAllSessions():
            if session.ProcessId in (0, me) or not session.Process:
                continue  # 시스템 소리와 이 프로그램은 건드리지 않는다
            yield session.Process.name(), session.SimpleAudioVolume

    def fade(self, pairs, seconds):
        for step in range(1, self.STEPS + 1):
            t = step / self.STEPS
            for control, start, end in pairs:
                try:
                    control.SetMasterVolume(start + (end - start) * t, None)
                except Exception:  # 그 사이 프로그램이 꺼졌으면 건너뛴다
                    pass
            time.sleep(seconds / self.STEPS)

    def duck(self, level):
        self.saved = []
        try:
            for name, control in self.sessions():
                original = control.GetMasterVolume()
                if original > 0.01:
                    self.saved.append((control, name, original, original * level / 100))
        except Exception:  # pycaw가 없거나 오디오 장치 오류 → 줄이지 않고 그냥 읽는다
            self.saved = []
            return
        if not self.saved:
            return
        try:
            DUCK_STATE_PATH.write_text(
                json.dumps({name: original for _, name, original, _ in self.saved}), encoding="utf-8"
            )
        except OSError:
            pass
        self.fade([(c, original, low) for c, _, original, low in self.saved], 0.3)

    def restore(self):
        if self.saved:
            self.fade([(c, low, original) for c, _, original, low in self.saved], 0.6)
            self.saved = []
        DUCK_STATE_PATH.unlink(missing_ok=True)

    @classmethod
    def recover(cls):
        """지난번 알림 도중 강제 종료돼서 줄어든 채 남은 볼륨을 되돌린다."""
        try:
            originals = json.loads(DUCK_STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        try:
            for name, control in cls.sessions():
                if name in originals:
                    control.SetMasterVolume(originals[name], None)
        except Exception:
            pass
        DUCK_STATE_PATH.unlink(missing_ok=True)


class Speaker:
    """음성이 겹치지 않도록 한 번에 하나씩 읽는다."""

    def __init__(self):
        self.queue = queue.Queue()
        self.ducker = Ducker()
        threading.Thread(target=self._run, daemon=True).start()

    def say(self, text, voice=None):
        self.queue.put((text, {**DEFAULT_VOICE, **(voice or {})}))

    def _run(self):
        try:
            import comtypes  # 이 스레드에서 Windows 오디오(COM)를 쓰기 위한 준비

            comtypes.CoInitialize()
        except (ImportError, OSError):
            pass
        Ducker.recover()
        while True:
            text, voice = self.queue.get()
            rate, volume = voice["rate"], voice["volume"]
            mp3 = None
            if voice["name"] != "sapi":
                try:
                    mp3 = synth_online(text, voice["name"], rate)  # 음성을 다 받은 뒤에 소리를 줄인다
                except Exception:  # 인터넷 끊김, 서버 오류 등 → 기본 음성으로 대신 읽는다
                    mp3 = None
            if voice["duck"]:
                self.ducker.duck(voice["duck_level"])
            try:
                if mp3:
                    play_mp3(mp3, volume)
                else:
                    speak_sapi(text, rate, volume).wait(timeout=180)
            except (OSError, subprocess.SubprocessError):
                pass
            finally:
                self.ducker.restore()


def autostart_enabled():
    return STARTUP_LINK.exists()


def set_autostart(enabled):
    """시작 프로그램 폴더에 바로가기를 만들거나 지운다. 성공하면 True."""
    if not enabled:
        STARTUP_LINK.unlink(missing_ok=True)
        return not STARTUP_LINK.exists()
    pythonw = Path(sys.executable).with_name("pythonw.exe")  # 콘솔 창 없이 실행
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:TA_LINK);"
        "$s.TargetPath = $env:TA_TARGET;"
        "$s.Arguments = '\"' + $env:TA_SCRIPT + '\"';"
        "$s.WorkingDirectory = $env:TA_DIR;"
        "$s.Save()"
    )
    env = dict(
        os.environ,
        TA_LINK=str(STARTUP_LINK),
        TA_TARGET=str(pythonw if pythonw.exists() else sys.executable),
        TA_SCRIPT=str(Path(__file__).resolve()),
        TA_DIR=str(BASE_DIR),
    )
    try:
        STARTUP_LINK.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            env=env, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return STARTUP_LINK.exists()


def is_locked():
    """잠금 화면이면 입력 데스크톱으로 전환할 수 없다."""
    desk = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
    if not desk:
        return True
    try:
        return not user32.SwitchDesktop(desk)
    finally:
        user32.CloseDesktop(desk)


def running_processes():
    try:
        out = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True,
            encoding="oem",
            errors="replace",
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {line.split('","')[0].strip('"') for line in out.splitlines() if line.startswith('"')}


# ----- 특정 시각 알람 -----
def next_once_date(hh, mm):
    now = datetime.datetime.now()
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    return target.strftime("%Y-%m-%d")


def alarm_due(alarm, now):
    if not alarm.get("enabled", True) or alarm["time"] != now.strftime("%H:%M"):
        return False
    repeat = alarm["repeat"]
    if repeat == "once":
        return alarm.get("date") == now.strftime("%Y-%m-%d")
    if repeat == "weekdays":
        return now.weekday() < 5
    if repeat == "days":
        return now.weekday() in alarm.get("days", [])
    return True


def alarm_expired(alarm, now):
    if alarm["repeat"] != "once":
        return False
    end = datetime.datetime.strptime(f"{alarm['date']} {alarm['time']}", "%Y-%m-%d %H:%M")
    return end + datetime.timedelta(minutes=1) <= now


def next_occurrence(alarm, now):
    """앞으로 일주일 안에 이 알람이 울릴 가장 가까운 시각."""
    hh, mm = map(int, alarm["time"].split(":"))
    for offset in range(8):
        day = now.date() + datetime.timedelta(days=offset)
        at = datetime.datetime.combine(day, datetime.time(hh, mm))
        if at > now and alarm_due(alarm, at):
            return at
    return None


def describe_repeat(alarm):
    repeat = alarm["repeat"]
    if repeat == "once":
        date = datetime.datetime.strptime(alarm["date"], "%Y-%m-%d")
        return f"한 번 · {date.month}/{date.day}"
    if repeat == "days":
        return " · ".join(WEEKDAYS[d] for d in sorted(alarm.get("days", [])))
    return REPEAT_LABELS[repeat]


# ----- 캘린더 일정 -----
def event_dt(event):
    hh, mm = map(int, event["time"].split(":"))
    return datetime.datetime.combine(datetime.date.fromisoformat(event["date"]), datetime.time(hh, mm))


def event_start(event):
    return datetime.date.fromisoformat(event["date"])


def event_end(event):
    """기간 일정이면 종료일, 하루 일정이면 그날."""
    return datetime.date.fromisoformat(event.get("end_date") or event["date"])


def is_period(event):
    return event_end(event) > event_start(event)


# ----- 반복 일정 -----
NTH = ["첫째", "둘째", "셋째", "넷째", "다섯째"]


def is_last_weekday(day):
    return (day + datetime.timedelta(days=7)).month != day.month


def repeat_options(day):
    """시작 날짜에 맞는 반복 선택지: [(종류, 이름)]."""
    weekday = WEEKDAYS[day.weekday()]
    options = [
        ("none", "반복 안 함"),
        ("weekly", f"매주 {weekday}요일"),
        ("monthly_day", f"매월 {day.day}일"),
        ("monthly_nth", f"매월 {NTH[(day.day - 1) // 7]} {weekday}요일"),
    ]
    if is_last_weekday(day):
        options.append(("monthly_last", f"매월 마지막 {weekday}요일"))
    options.append(("yearly", f"매년 {day.month}월 {day.day}일"))
    return options


def repeat_kind(event):
    return (event.get("repeat") or {}).get("type", "none")


def is_recurring(event):
    return repeat_kind(event) != "none"


def repeat_label(event):
    return dict(repeat_options(event_start(event))).get(repeat_kind(event), "")


def occurs_on(event, day):
    """이 일정이 그 날짜에 걸쳐 있는지 (기간 일정과 반복 일정 포함)."""
    start = event_start(event)
    kind = repeat_kind(event)
    if kind == "none":
        return start <= day <= event_end(event)
    until = (event.get("repeat") or {}).get("until")
    if day < start or (until and day > datetime.date.fromisoformat(until)):
        return False
    if day.isoformat() in event.get("skip", []):
        return False  # '이 날만 삭제'한 날
    if kind == "weekly":
        return day.weekday() == start.weekday()
    if kind == "monthly_day":
        return day.day == start.day
    if kind == "monthly_nth":
        return day.weekday() == start.weekday() and (day.day - 1) // 7 == (start.day - 1) // 7
    if kind == "monthly_last":
        return day.weekday() == start.weekday() and is_last_weekday(day)
    if kind == "yearly":
        return (day.month, day.day) == (start.month, start.day)
    return False


def next_event_time(event, now):
    """지금 이후 이 일정이 처음 시작하는 시각 (반복이면 1년 안에서 찾는다)."""
    if not is_recurring(event):
        at = event_dt(event)
        return at if at > now else None
    hh, mm = map(int, event["time"].split(":"))
    for offset in range(367):
        day = now.date() + datetime.timedelta(days=offset)
        if occurs_on(event, day):
            at = datetime.datetime.combine(day, datetime.time(hh, mm))
            if at > now:
                return at
    return None


def event_color(event):
    default = "yellow" if is_period(event) else "purple"
    return EVENT_COLORS.get(event.get("color"), EVENT_COLORS[default])


def mix(color, other, amount):
    """두 색을 섞는다. amount = color 쪽 비율(0~1)."""
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x * amount + y * (1 - amount)):02x}" for x, y in zip(a, b))


def round_rect(canvas, x1, y1, x2, y2, radius, **kw):
    radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius, x2, y2 - radius, x2, y2,
        x2 - radius, y2, x1 + radius, y2, x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kw)


def fit_text(font, text, max_width):
    """주어진 폭에 맞게 글자를 자르고 …을 붙인다."""
    if max_width <= 0:
        return ""
    if font.measure(text) <= max_width:
        return text
    while text and font.measure(text + "…") > max_width:
        text = text[:-1]
    return text + "…" if text else ""


def alert_offsets(lead, interval, at_start):
    """시작 몇 분 전에 알릴지 목록 (큰 것부터). interval 0 = 알림 없음, -1 = 한 번만."""
    if interval == 0:
        return []
    offsets = [lead] if interval < 0 else list(range(lead, 0, -interval))
    if at_start:
        offsets.append(0)
    return sorted({o for o in offsets if o >= 0}, reverse=True)


def event_alert_settings(event):
    # 예전에 만든 일정은 lead/at_start가 없으니 기본값(2시간 전부터, 시작 시각 포함)
    return int(event.get("lead", EVENT_LEAD_MIN)), int(event.get("interval") or 0), event.get("at_start", True)


def event_alerts(event, on_day=None):
    """(알림 시각, 일정까지 남은 분) 목록. 반복 일정은 on_day 날짜의 일정 기준으로 계산한다."""
    at = event_dt(event)
    if on_day:
        at = datetime.datetime.combine(on_day, at.time())
    return [(at - datetime.timedelta(minutes=left), left) for left in alert_offsets(*event_alert_settings(event))]


def fmt_left(minutes):
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    parts = [f"{days}일"] if days else []
    if hours:
        parts.append(f"{hours}시간")
    if mins or not parts:
        parts.append(f"{mins}분")
    return " ".join(parts)


def parse_minutes(text):
    """'90', '90분', '1시간 30분', '2일' 같은 글자를 분으로. 못 읽으면 None."""
    import re

    text = text.replace(" ", "")
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total, rest = 0, text
    for unit, factor in (("일", 1440), ("시간", 60), ("분", 1)):
        match = re.match(rf"(\d+){unit}", rest)
        if match:
            total += int(match.group(1)) * factor
            rest = rest[match.end():]
    return total if not rest and text != rest else None


def interval_label(minutes):
    return next((k for k, v in INTERVALS.items() if v == minutes), fmt_left(minutes))


def describe_alerts(event):
    lead, interval, at_start = event_alert_settings(event)
    if interval == 0:
        return "알림 없음"
    text = f"{fmt_left(lead)} 전" if interval < 0 else f"{fmt_left(lead)} 전부터 {fmt_left(interval)}마다"
    return text + (" + 시작" if at_start else "")


@functools.lru_cache(maxsize=16)
def kr_holidays(year):
    """한국 공휴일 (대체공휴일, 설날·추석 같은 음력 공휴일 포함)."""
    try:
        import holidays

        return dict(holidays.KR(years=year, language="ko"))
    except Exception:  # 라이브러리가 없거나 오류 → 공휴일 없이 표시
        return {}


def holiday_name(day):
    return kr_holidays(day.year).get(day)


@functools.lru_cache(maxsize=1024)
def lunar_date(day):
    """양력 → 음력 (한국천문연구원 기준). (윤달 여부, 월, 일) 또는 None."""
    try:
        from korean_lunar_calendar import KoreanLunarCalendar

        calendar = KoreanLunarCalendar()
        if not calendar.setSolarDate(day.year, day.month, day.day):
            return None
        return bool(calendar.isIntercalation), calendar.lunarMonth, calendar.lunarDay
    except Exception:
        return None


def lunar_short(day):
    lunar = lunar_date(day)
    return f"{'윤' if lunar[0] else ''}{lunar[1]}.{lunar[2]}" if lunar else ""


# ----- 그림 -----
def make_icon(color):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=color, outline=(255, 255, 255), width=4)
    d.line((32, 32, 32, 14), fill=(255, 255, 255), width=5)
    d.line((32, 32, 46, 32), fill=(255, 255, 255), width=5)
    return img


@functools.lru_cache(maxsize=32)
def load_font(filename, size):
    try:
        return ImageFont.truetype(str(FONT_DIR / filename), size)
    except OSError:
        return ImageFont.load_default(size)


def ring_image(fraction, color, clock, label, size=184, scale=3):
    """원형 타이머. 남은 비율만큼 12시 방향부터 시계 방향으로 채운다."""
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    width = 12 * scale
    pad = 6 * scale
    box = (pad, pad, s - pad, s - pad)
    d.ellipse(box, outline=C["track"], width=width)
    if fraction > 0.002:
        end = -90 + 360 * min(fraction, 1.0)
        d.arc(box, start=-90, end=end, fill=color, width=width)
        center, radius = s / 2, (s - 2 * pad) / 2 - width / 2
        for angle in (-90, end):  # 둥근 끝
            x = center + radius * math.cos(math.radians(angle))
            y = center + radius * math.sin(math.radians(angle))
            d.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=color)
    d.text((s / 2, s / 2 - 8 * scale), clock, font=load_font("consolab.ttf", 36 * scale), fill=C["fg"], anchor="mm")
    d.text((s / 2, s / 2 + 28 * scale), label, font=load_font("malgun.ttf", 13 * scale), fill=C["sub"], anchor="mm")
    return img


def glyph_image(code, color, size=18, scale=3):
    """Windows 아이콘 글꼴(Segoe Fluent/MDL2)에서 아이콘 하나를 그린다."""
    for filename in ("SegoeIcons.ttf", "segmdl2.ttf"):
        if (FONT_DIR / filename).exists():
            break
    else:
        return None
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(img).text(
        (s / 2, s / 2), chr(code), font=load_font(filename, int(s * 0.85)), fill=color, anchor="mm"
    )
    return img


class TimeAlarm:
    def __init__(self):
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.ui_requests = queue.Queue()  # 트레이 → 창 ("show", "quit")
        self.speaker = Speaker()
        self.paused = False
        self.paused_remaining = 0.0
        self.gaming = False
        self.active_games = []  # 지금 실행 중인 등록 게임
        self.missed = []  # 게임 중에 보류한 알림 (종류, 문구)
        self.fired = {}  # 알람 id → 마지막으로 울린 날짜
        self.event_fired = set()  # 이미 알린 (일정 id, 남은 분)
        self.brief_pending = True  # 켜진 직후, 절전에서 깬 뒤, 잠금을 푼 뒤에 오늘 브리핑을 확인한다
        self.first_run = not STORE_PATH.exists()  # 처음 실행이면 창을 바로 띄워 준다
        self.store = load_store()
        self.store_version = 0
        self.records = load_records()
        self.records_version = 0
        self.start_session()

        now = datetime.datetime.now()
        expired = [a for a in self.store["alarms"] if alarm_expired(a, now)]
        if expired:
            self.update_store(lambda s: [s["alarms"].remove(a) for a in expired])

        self.icon = pystray.Icon(
            "time_alarm",
            make_icon(COLORS["work"]),
            "시간 알림",
            menu=pystray.Menu(
                pystray.MenuItem("열기", self.on_open, default=True),
                pystray.MenuItem("음성 테스트", self.on_test),
                pystray.MenuItem(
                    lambda _: "재개" if self.paused else "일시정지", self.on_toggle_pause
                ),
                pystray.MenuItem("사이클 처음부터", self.on_restart),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("종료", self.on_quit),
            ),
        )
        self.restart_cycle()

    def update_store(self, change):
        with self.lock:
            change(self.store)
            save_store(self.store)
            self.store_version += 1

    # ----- 사용 기록 -----
    def save_records_locked(self):
        save_records(self.records)
        self.records_version += 1

    def start_session(self, now=None):
        now = now or datetime.datetime.now()
        with self.lock:
            sessions = self.records["sessions"]
            last = sessions[-1] if sessions else None
            if last and 0 <= (now - parse_ts(last["end"])).total_seconds() <= RESUME_GAP_SEC:
                self.session = last  # 프로그램만 잠깐 다시 켠 경우는 이어서 기록
            else:
                self.session = {"start": to_ts(now), "end": to_ts(now)}
                sessions.append(self.session)
            self.session["end"] = to_ts(now)
            self.save_records_locked()

    def heartbeat(self, now=None):
        with self.lock:
            self.session["end"] = to_ts(now or datetime.datetime.now())
            self.save_records_locked()

    def count_phase(self, kind):
        """작업이나 휴식이 한 번 끝날 때마다 오늘 횟수를 센다."""
        day = datetime.date.today().isoformat()
        with self.lock:
            counts = self.records["counts"].setdefault(day, {"work": 0, "break": 0})
            counts[kind] += 1
            self.save_records_locked()

    def today_counts(self):
        with self.lock:
            return dict(self.records["counts"].get(datetime.date.today().isoformat(), {"work": 0, "break": 0}))

    def records_snapshot(self):
        with self.lock:
            return [dict(s) for s in self.records["sessions"]], dict(self.session)

    def expired_records(self, today):
        """보관 기간(1년)이 지난 기록과, 다시 물어볼 날짜."""
        cutoff = one_year_before(today)
        with self.lock:
            expired = [dict(s) for s in self.records["sessions"] if parse_ts(s["start"]).date() <= cutoff]
            return expired, self.records["snooze_until"]

    def delete_expired(self, today):
        cutoff = one_year_before(today)
        with self.lock:
            before = len(self.records["sessions"])
            self.records["sessions"] = [
                s for s in self.records["sessions"]
                if parse_ts(s["start"]).date() > cutoff or s is self.session
            ]
            self.records["counts"] = {
                d: c for d, c in self.records["counts"].items() if datetime.date.fromisoformat(d) > cutoff
            }
            self.records["snooze_until"] = None
            self.save_records_locked()
            return before - len(self.records["sessions"])

    def snooze_retention(self, days):
        with self.lock:
            self.records["snooze_until"] = (datetime.date.today() + datetime.timedelta(days=days)).isoformat()
            self.save_records_locked()

    # ----- 사이클 -----
    def restart_cycle(self):
        with self.lock:
            self.phase = "work"
            self.phase_end = time.monotonic() + self.store["cycle"]["work_min"] * 60
            self.paused = False
            self.cycle_no = 1
            self.cycle_started = datetime.datetime.now()
        self.refresh_icon()

    def cycle_snapshot(self):
        with self.lock:
            cycle = dict(self.store["cycle"])
            left = self.paused_remaining if self.paused else self.phase_end - time.monotonic()
            return {
                "cycle": cycle,
                "paused": self.paused,
                "phase": self.phase,
                "left": max(0.0, left),
                "total": cycle["work_min" if self.phase == "work" else "break_min"] * 60,
                "cycle_no": self.cycle_no,
                "started": self.cycle_started,
            }

    def next_alarm(self):
        now = datetime.datetime.now()
        with self.lock:
            alarms = [dict(a) for a in self.store["alarms"]]
            events = [dict(e) for e in self.store["events"]]
        upcoming = [(at, a) for a in alarms if (at := next_occurrence(a, now))]
        upcoming += [(at, {"message": e["title"]}) for e in events if (at := next_event_time(e, now))]
        return min(upcoming, key=lambda pair: pair[0]) if upcoming else None

    def say(self, text):
        with self.lock:
            voice = dict(self.store["voice"])
        self.speaker.say(text, voice)

    def notify(self, text, kind):
        """일시정지면 버리고, 게임 중이면 보류하고, 아니면 바로 읽는다."""
        if self.paused:
            return
        if self.gaming:
            if kind == "cycle":
                # 사이클 알림은 가장 최근 것만 의미가 있다
                self.missed = [m for m in self.missed if m[0] != "cycle"]
            self.missed.append((kind, text))
            return
        self.say(text)

    def run_loop(self):
        last_wall = time.time()
        last_game_check = 0.0
        last_beat = time.monotonic()
        was_locked = False
        while not self.stop_event.wait(1):
            now_wall = time.time()
            before_sleep = last_wall
            woke_up = now_wall - last_wall > SLEEP_GAP_SEC
            last_wall = now_wall
            if woke_up:
                self.brief_pending = True
                # 절전 = 꺼짐으로 보고 기록을 나눈다 (짧은 절전이면 start_session이 이어 붙인다)
                self.heartbeat(datetime.datetime.fromtimestamp(before_sleep))
                self.start_session()
                last_beat = time.monotonic()
                self.restart_cycle()
                continue
            if time.monotonic() - last_beat >= HEARTBEAT_SEC:
                last_beat = time.monotonic()
                self.heartbeat()

            if time.monotonic() - last_game_check >= GAME_CHECK_SEC:
                last_game_check = time.monotonic()
                self.update_gaming()
            self.check_alarms()
            self.check_events()

            locked = is_locked()
            unlocked = was_locked and not locked
            was_locked = locked
            if unlocked:
                self.brief_pending = True
                self.restart_cycle()
                continue
            if locked:
                continue
            if self.brief_pending:
                self.brief_pending = False
                self.brief_today()

            with self.lock:
                cycle = self.store["cycle"]
                if not cycle["enabled"] or self.paused or time.monotonic() < self.phase_end:
                    continue
                finished = self.phase
                if self.phase == "work":
                    self.phase = "break"
                    self.phase_end = time.monotonic() + cycle["break_min"] * 60
                    template = cycle["break_msg"]
                else:
                    self.phase = "work"
                    self.phase_end = time.monotonic() + cycle["work_min"] * 60
                    self.cycle_no += 1
                    template = cycle["work_msg"]
            self.count_phase(finished)
            self.notify(cycle_message(template, cycle), "cycle")
            self.refresh_icon()

    def check_alarms(self):
        now = datetime.datetime.now()
        today = now.strftime("%Y-%m-%d")
        with self.lock:
            due = [
                a
                for a in self.store["alarms"]
                if alarm_due(a, now) and self.fired.get(a["id"]) != today
            ]
            for alarm in due:
                self.fired[alarm["id"]] = today
        if not due:
            return
        once = [a for a in due if a["repeat"] == "once"]
        if once:
            self.update_store(lambda s: [s["alarms"].remove(a) for a in once])
        for alarm in due:
            self.notify(f"{korean_time(now)}입니다. {alarm['message']}", "alarm")

    def check_events(self):
        now = datetime.datetime.now()
        minute = now.strftime("%Y-%m-%d %H:%M")
        with self.lock:
            events = [dict(e) for e in self.store["events"]]
        today = now.date()
        for event in events:
            if is_recurring(event):
                # 시작 전 알림이 며칠 앞에 걸칠 수 있어서 그만큼 앞의 회차까지 본다
                ahead = event_alert_settings(event)[0] // 1440 + 1
                days = [
                    d for d in (today + datetime.timedelta(days=n) for n in range(ahead + 1))
                    if occurs_on(event, d)
                ]
            else:
                days = [event_start(event)]
            alerts = [(when, left, d) for d in days for when, left in event_alerts(event, d)]
            for when, left, day in alerts:
                key = (event["id"], day.isoformat(), left)
                if when.strftime("%Y-%m-%d %H:%M") != minute or key in self.event_fired:
                    continue
                self.event_fired.add(key)
                if left and day != today:
                    # 날짜가 다른 일정은 '내일 오후 2시에'처럼 언제인지로 말한다
                    start_at = datetime.datetime.combine(day, event_dt(event).time())
                    when_text = "내일" if day == today + datetime.timedelta(days=1) else f"{day.month}월 {day.day}일"
                    text = f"{korean_time(now)}입니다. {when_text} {korean_time(start_at)}에 {event['title']} 일정이 있습니다."
                elif left:
                    text = f"{korean_time(now)}입니다. {fmt_left(left)} 뒤에 {event['title']} 일정이 있습니다."
                else:
                    text = f"{korean_time(now)}입니다. 지금 {event['title']} 일정 시간입니다."
                self.notify(text, "alarm")

    def brief_today(self):
        """그날 처음 한 번, 오늘 일정을 읽어 준다."""
        today = datetime.date.today()
        with self.lock:
            if not self.store["briefing"] or self.store["briefed_on"] == today.isoformat():
                return
            events = sorted(
                (
                    dict(e) for e in self.store["events"]
                    if occurs_on(e, today) and (is_recurring(e) or event_start(e) == today)
                ),
                key=lambda e: e["time"],
            )
            ongoing = [
                dict(e) for e in self.store["events"]
                if not is_recurring(e) and event_start(e) < today <= event_end(e)
            ]
        self.update_store(lambda s: s.__setitem__("briefed_on", today.isoformat()))
        day = f"{today.month}월 {today.day}일 {WEEKDAYS[today.weekday()]}요일"
        holiday = holiday_name(today)
        if holiday:
            day += f", 오늘은 {holiday}입니다"
        if events:
            items = ", ".join(f"{korean_time(event_dt(e))} {e['title']}" for e in events)
            text = f"{day}. 오늘 일정은 {len(events)}개입니다. {items}."
        elif not ongoing:
            text = f"{day}. 오늘은 등록된 일정이 없습니다."
        else:
            text = f"{day}. 오늘 새로 시작하는 일정은 없습니다."
        if ongoing:
            items = []
            for e in ongoing:
                left = (event_end(e) - today).days
                items.append(f"{e['title']}, " + ("오늘 마감" if left == 0 else f"{left}일 남음"))
            text += " 진행 중인 일정: " + ", ".join(items) + "."
        self.notify(text, "alarm")

    def update_gaming(self):
        with self.lock:
            games = list(self.store["games"])
        running = {p.lower() for p in running_processes()} if games else set()
        self.active_games = [g for g in games if g.lower() in running]
        gaming = bool(self.active_games)
        if gaming == self.gaming:
            return
        self.gaming = gaming
        if not gaming and self.missed:
            texts = [text for _, text in self.missed]
            self.missed = []
            self.say(f"게임 중에 놓친 알림이 {len(texts)}개 있습니다. " + " ".join(texts))
        self.refresh_icon()

    def refresh_icon(self):
        if self.paused:
            key, title = "paused", "시간 알림 - 일시정지"
        elif self.gaming:
            key, title = "game", "시간 알림 - 게임 중 (알림 보류)"
        elif not self.store["cycle"]["enabled"]:
            key, title = "off", "시간 알림 - 메인 알람 꺼짐"
        elif self.phase == "work":
            key, title = "work", "시간 알림 - 작업 중"
        else:
            key, title = "break", "시간 알림 - 휴식 중"
        self.icon.icon = make_icon(COLORS[key])
        self.icon.title = title
        self.icon.update_menu()

    # ----- 트레이 메뉴 (트레이 스레드에서 불림) -----
    def on_open(self, *_):
        self.ui_requests.put("show")

    def on_test(self, *_):
        cycle = self.store["cycle"]
        self.say(cycle_message(cycle["break_msg"], cycle))

    def on_toggle_pause(self, *_):
        with self.lock:
            if self.paused:
                self.phase_end = time.monotonic() + self.paused_remaining
                self.paused = False
            else:
                self.paused_remaining = max(0.0, self.phase_end - time.monotonic())
                self.paused = True
        self.refresh_icon()

    def on_restart(self, *_):
        self.restart_cycle()

    def on_quit(self, *_):
        self.ui_requests.put("quit")

    def run(self):
        threading.Thread(target=self.run_loop, daemon=True).start()
        self.icon.run_detached()
        if self.first_run:
            self.ui_requests.put("show")
        try:
            MainWindow(self).run()
        finally:
            self.stop_event.set()
            self.heartbeat()  # 종료 시각을 남긴다
            self.icon.stop()


class MainWindow:
    """다크 테마 창: 왼쪽 메뉴 + 화면 4개. 처음엔 숨겨 두고 트레이에서 열고, X를 누르면 다시 숨긴다."""

    def __init__(self, app):
        self.app = app
        self.seen_version = -1
        self.editing_id = None
        self.days = set()
        self.ring_key = None
        self.voice_job = None
        self.flash_jobs = {}
        self.records_seen = -1
        self.retention_day = None
        self.cal_month = datetime.date.today().replace(day=1)
        self.day_dialog = None
        self.event_editing = None

        ctk.set_appearance_mode("dark")
        self.root = ctk.CTk(fg_color=C["bg"])
        self.root.withdraw()
        self.root.title("시간 알림")
        try:
            scaling = self.root._get_window_scaling()
        except AttributeError:
            scaling = 1.0
        self.scaling = scaling
        # 작은 화면에서도 창이 작업 표시줄에 가리지 않게
        height = min(680, int(self.root.winfo_screenheight() / scaling) - 90)
        self.root.geometry(f"860x{height}")
        self.root.minsize(800, min(600, height))
        self.root.protocol("WM_DELETE_WINDOW", self.root.withdraw)
        self.set_window_icon()

        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=1)
        self.build_gamebar()
        shell = ctk.CTkFrame(self.root, fg_color=C["bg"], corner_radius=0)
        shell.grid(row=1, column=0, sticky="nsew")
        shell.grid_columnconfigure(1, weight=1)
        shell.grid_rowconfigure(0, weight=1)
        self.build_sidebar(shell)
        content = ctk.CTkFrame(shell, fg_color=C["bg"], corner_radius=0)
        content.grid(row=0, column=1, sticky="nsew", padx=20, pady=16)
        self.pages = {
            "main": self.build_main_page(content),
            "alarms": self.build_alarm_page(content),
            "calendar": self.build_calendar_page(content),
            "voice": self.build_voice_page(content),
            "games": self.build_game_page(content),
            "records": self.build_records_page(content),
            "settings": self.build_settings_page(content),
        }
        self.show_page("main")
        self.clear_form()

    # ----- 공통 위젯 -----
    def font(self, size=13, weight="normal", mono=False):
        return ctk.CTkFont(family=MONO_FONT if mono else UI_FONT, size=size, weight=weight)

    def label(self, parent, text="", size=13, weight="normal", color=None, mono=False, **kw):
        return ctk.CTkLabel(
            parent, text=text, font=self.font(size, weight, mono), text_color=color or C["fg"], **kw
        )

    def button(self, parent, text, command, primary=False, width=90):
        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            width=width,
            height=32,
            corner_radius=7,
            fg_color=C["accent"] if primary else C["btn"],
            hover_color=C["accent_hover"] if primary else C["btn_hover"],
            border_width=0 if primary else 1,
            border_color=C["line"],
            text_color="#ffffff" if primary else C["fg"],
            text_color_disabled=C["dim"],
            font=self.font(13, "bold" if primary else "normal"),
        )

    def entry(self, parent, width=200):
        return ctk.CTkEntry(
            parent,
            width=width,
            height=32,
            corner_radius=6,
            fg_color=C["input"],
            border_color=C["line"],
            border_width=1,
            text_color=C["fg"],
            font=self.font(13),
        )

    def card(self, parent, highlight=False):
        return ctk.CTkFrame(
            parent,
            fg_color=C["card_hl"] if highlight else C["card"],
            border_width=1,
            border_color=C["line_hl"] if highlight else C["line"],
            corner_radius=10,
        )

    def switch(self, parent, command):
        return ctk.CTkSwitch(
            parent,
            text="",
            width=44,
            switch_width=38,
            switch_height=20,
            command=command,
            progress_color=C["accent"],
            fg_color=C["off"],
            button_color="#ffffff",
            button_hover_color="#ffffff",
        )

    def dropdown_style(self):
        return dict(
            height=32,
            fg_color=C["btn"],
            button_color=C["btn"],
            button_hover_color=C["btn_hover"],
            dropdown_fg_color=C["card"],
            dropdown_hover_color=C["accent_soft"],
            dropdown_text_color=C["fg"],
            text_color=C["fg"],
            font=self.font(13),
            dropdown_font=self.font(12),
        )

    def combobox(self, parent, values, width):
        style = self.dropdown_style()
        style.update(fg_color=C["input"], border_color=C["line"])
        return ctk.CTkComboBox(parent, values=values, width=width, **style)

    def page_header(self, page, title):
        head = ctk.CTkFrame(page, fg_color="transparent")
        head.pack(fill="x")
        self.label(head, title, 19, "bold").pack(side="left")
        return head

    def flash(self, label, text, color=None, ms=4000):
        label.configure(text=text, text_color=color or C["good"])
        key = str(label)
        if key in self.flash_jobs:
            self.root.after_cancel(self.flash_jobs[key])
        self.flash_jobs[key] = self.root.after(ms, lambda: label.configure(text=""))

    def set_window_icon(self):
        try:
            path = Path(tempfile.gettempdir()) / "time_alarm.ico"
            make_icon((139, 108, 255)).save(path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
            # customtkinter가 시작할 때 자기 아이콘을 덮어쓰므로 조금 뒤에 바꾼다
            self.root.after(300, lambda: self.root.iconbitmap(str(path)))
        except (OSError, tk.TclError):
            pass

    # ----- 게임 감지 막대 + 사이드바 -----
    def build_gamebar(self):
        self.gamebar = ctk.CTkFrame(self.root, fg_color=C["game_bg"], corner_radius=0, height=36)
        self.label(self.gamebar, "●", 12, color=C["accent"]).pack(side="left", padx=(16, 8))
        self.game_text = self.label(self.gamebar, "", 12, "bold", C["game_fg"])
        self.game_text.pack(side="left")
        self.game_wait = self.label(self.gamebar, "", 12, color="#a596e6")
        self.game_wait.pack(side="right", padx=16)
        self.gamebar_shown = False

    def build_sidebar(self, shell):
        side = ctk.CTkFrame(shell, fg_color=C["side"], corner_radius=0, width=184)
        side.grid(row=0, column=0, sticky="ns")
        side.pack_propagate(False)
        self.label(side, "시간 알림", 16, "bold").pack(anchor="w", padx=20, pady=(20, 16))

        self.nav = {}
        for key, text, glyph in NAV:
            images = [glyph_image(glyph, color) for color in (C["sub"], "#ffffff")]
            icons = [ctk.CTkImage(img, size=(18, 18)) if img else None for img in images]
            btn = ctk.CTkButton(
                side,
                text="  " + text,
                image=icons[0],
                compound="left",
                anchor="w",
                height=40,
                corner_radius=8,
                fg_color="transparent",
                hover_color=C["side_hover"],
                text_color=C["sub"],
                font=self.font(13),
                command=lambda k=key: self.show_page(k),
            )
            btn.pack(fill="x", padx=10, pady=1)
            self.nav[key] = (btn, icons)

        status = ctk.CTkFrame(side, fg_color="transparent")
        status.pack(side="bottom", fill="x", padx=20, pady=(0, 20))
        ctk.CTkFrame(status, height=1, fg_color=C["line"]).pack(fill="x", pady=(0, 14))
        self.side_label = self.label(status, "", 12, color=C["sub"])
        self.side_label.pack(anchor="w")
        self.side_clock = self.label(status, "", 26, "bold", mono=True)
        self.side_clock.pack(anchor="w")
        self.side_next = self.label(status, "", 12, color=C["sub"], justify="left", anchor="w", wraplength=150)
        self.side_next.pack(anchor="w", fill="x", pady=(12, 0))

    def show_page(self, key):
        for name, page in self.pages.items():
            if name != key:
                page.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        for name, (btn, icons) in self.nav.items():
            active = name == key
            btn.configure(
                fg_color=C["accent_soft"] if active else "transparent",
                text_color="#ffffff" if active else C["sub"],
                image=icons[1] if active else icons[0],
                font=self.font(13, "bold" if active else "normal"),
            )

    # ----- 메인 알람 화면 -----
    def build_main_page(self, parent):
        with self.app.lock:
            cycle = dict(self.app.store["cycle"])
        page = ctk.CTkFrame(parent, fg_color="transparent")
        head = self.page_header(page, "메인 알람")
        self.cycle_switch = self.switch(head, self.toggle_cycle)
        self.cycle_switch.pack(side="right")
        self.label(head, "사용", 13, color=C["sub"]).pack(side="right", padx=(0, 8))
        if cycle["enabled"]:
            self.cycle_switch.select()

        hero = self.card(page, highlight=True)
        hero.pack(fill="x", pady=(14, 0))
        self.ring = ctk.CTkLabel(hero, text="")
        self.ring.grid(row=0, column=0, padx=(22, 18), pady=18)
        info = ctk.CTkFrame(hero, fg_color="transparent")
        info.grid(row=0, column=1, sticky="w")
        pills = ctk.CTkFrame(info, fg_color="transparent")
        pills.pack(anchor="w")
        self.pill_work = ctk.CTkLabel(pills, text="", corner_radius=13, height=28, font=self.font(12, "bold"))
        self.pill_work.pack(side="left", padx=(0, 6))
        self.pill_break = ctk.CTkLabel(pills, text="", corner_radius=13, height=28, font=self.font(12, "bold"))
        self.pill_break.pack(side="left")
        self.state_label = self.label(info, "", 17, "bold")
        self.state_label.pack(anchor="w", pady=(14, 0))
        self.info_label = self.label(info, "", 12, color=C["sub"])
        self.info_label.pack(anchor="w")
        self.count_label = self.label(info, "", 13, "bold", C["mode"])
        self.count_label.pack(anchor="w", pady=(2, 0))
        buttons = ctk.CTkFrame(info, fg_color="transparent")
        buttons.pack(anchor="w", pady=(14, 0))
        self.pause_btn = self.button(buttons, "일시정지", self.app.on_toggle_pause, width=100)
        self.pause_btn.pack(side="left")
        self.button(buttons, "처음부터", self.app.on_restart, width=100).pack(side="left", padx=8)

        settings = self.card(page)
        settings.pack(fill="x", pady=(12, 0))
        settings.grid_columnconfigure(1, weight=1)
        self.label(settings, "시간", color=C["sub"]).grid(row=0, column=0, sticky="w", padx=(18, 14), pady=(16, 6))
        times = ctk.CTkFrame(settings, fg_color="transparent")
        times.grid(row=0, column=1, sticky="w", pady=(16, 6))
        self.label(times, "작업", color=C["sub"]).pack(side="left")
        self.work_entry = self.entry(times, 60)
        self.work_entry.pack(side="left", padx=6)
        self.work_entry.insert(0, fmt_min(cycle["work_min"]))
        self.label(times, "분   ·   휴식", color=C["sub"]).pack(side="left")
        self.break_entry = self.entry(times, 60)
        self.break_entry.pack(side="left", padx=6)
        self.break_entry.insert(0, fmt_min(cycle["break_min"]))
        self.label(times, "분", color=C["sub"]).pack(side="left")
        self.cycle_entries = {}
        for row, (text, key) in enumerate([("휴식 시작 문구", "break_msg"), ("작업 시작 문구", "work_msg")], start=1):
            self.label(settings, text, color=C["sub"]).grid(row=row, column=0, sticky="w", padx=(18, 14), pady=6)
            field = self.entry(settings)
            field.grid(row=row, column=1, sticky="we", padx=(0, 18), pady=6)
            field.insert(0, cycle[key])
            self.cycle_entries[key] = field
        bottom = ctk.CTkFrame(settings, fg_color="transparent")
        bottom.grid(row=3, column=0, columnspan=2, sticky="we", padx=18, pady=(8, 16))
        self.label(bottom, "문구 안 치환어: {시각} {작업} {휴식}", 12, color=C["sub"]).pack(side="left")
        self.button(bottom, "저장·적용", self.save_cycle, primary=True, width=100).pack(side="right")
        self.button(bottom, "미리 듣기", self.preview_cycle).pack(side="right", padx=8)
        self.cycle_msg = self.label(page, "", 12)
        self.cycle_msg.pack(anchor="e", pady=(8, 0))
        return page

    def read_cycle_form(self):
        try:
            work, rest = float(self.work_entry.get()), float(self.break_entry.get())
            if work <= 0 or rest <= 0:
                raise ValueError
        except ValueError:
            self.flash(self.cycle_msg, "작업/휴식 시간은 0보다 큰 숫자(분)로 입력하세요.", C["warn"])
            return None
        cycle = {
            "enabled": bool(self.cycle_switch.get()),
            "work_min": work,
            "break_min": rest,
            "break_msg": self.cycle_entries["break_msg"].get().strip(),
            "work_msg": self.cycle_entries["work_msg"].get().strip(),
        }
        if not cycle["break_msg"] or not cycle["work_msg"]:
            self.flash(self.cycle_msg, "문구를 입력하세요.", C["warn"])
            return None
        try:
            cycle_message(cycle["break_msg"], cycle)
            cycle_message(cycle["work_msg"], cycle)
        except (KeyError, ValueError, IndexError):
            self.flash(self.cycle_msg, "문구의 {중괄호}에는 {시각} {작업} {휴식}만 쓸 수 있어요.", C["warn"])
            return None
        return cycle

    def save_cycle(self):
        cycle = self.read_cycle_form()
        if cycle:
            self.app.update_store(lambda s: s.__setitem__("cycle", cycle))
            self.app.restart_cycle()
            self.flash(self.cycle_msg, "저장했어요. 작업 시간이 지금부터 새로 시작돼요.")

    def preview_cycle(self):
        cycle = self.read_cycle_form()
        if cycle:
            self.app.say(cycle_message(cycle["break_msg"], cycle))

    def toggle_cycle(self):
        enabled = bool(self.cycle_switch.get())
        self.app.update_store(lambda s: s["cycle"].__setitem__("enabled", enabled))
        if enabled:
            self.app.restart_cycle()
        else:
            self.app.refresh_icon()

    # ----- 특정 시각 화면 -----
    def build_alarm_page(self, parent):
        page = ctk.CTkFrame(parent, fg_color="transparent")
        head = self.page_header(page, "특정 시각 알람")
        self.button(head, "＋ 새 알람", self.clear_form, primary=True, width=104).pack(side="right")
        self.label(page, "카드를 누르면 아래에서 수정해요 · 스위치로 켜기/끄기", 12, color=C["sub"]).pack(
            anchor="w", pady=(4, 6)
        )
        self.alarm_list = ctk.CTkScrollableFrame(
            page,
            fg_color="transparent",
            height=180,
            scrollbar_button_color=C["btn"],
            scrollbar_button_hover_color=C["btn_hover"],
        )
        self.alarm_list.pack(fill="both", expand=True)

        form = self.card(page)
        form.configure(border_color=C["accent"])
        form.pack(fill="x", pady=(10, 0))
        form.grid_columnconfigure(1, weight=1)
        self.mode_label = self.label(form, "", 13, "bold", C["mode"])
        self.mode_label.grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(14, 6))

        self.label(form, "시각", color=C["sub"]).grid(row=1, column=0, sticky="w", padx=(18, 14), pady=5)
        when = ctk.CTkFrame(form, fg_color="transparent")
        when.grid(row=1, column=1, sticky="w", pady=5)
        self.hour = self.combobox(when, [f"{h:02d}" for h in range(24)], 76)
        self.hour.pack(side="left")
        self.label(when, ":", 15, "bold").pack(side="left", padx=6)
        self.minute = self.combobox(when, [f"{m:02d}" for m in range(0, 60, 5)], 76)
        self.minute.pack(side="left")
        self.label(when, "반복", color=C["sub"]).pack(side="left", padx=(22, 8))
        self.repeat = ctk.CTkOptionMenu(
            when, values=list(REPEAT_LABELS.values()), width=120,
            command=lambda _: self.update_day_buttons(), **self.dropdown_style()
        )
        self.repeat.pack(side="left")

        self.label(form, "요일", color=C["sub"]).grid(row=2, column=0, sticky="w", padx=(18, 14), pady=5)
        days = ctk.CTkFrame(form, fg_color="transparent")
        days.grid(row=2, column=1, sticky="w", pady=5)
        self.day_btns = []
        for i, name in enumerate(WEEKDAYS):
            btn = ctk.CTkButton(
                days, text=name, width=34, height=30, corner_radius=7, font=self.font(12),
                text_color_disabled=C["dim"], command=lambda i=i: self.toggle_day(i),
            )
            btn.pack(side="left", padx=(0, 5))
            self.day_btns.append(btn)

        self.label(form, "문구", color=C["sub"]).grid(row=3, column=0, sticky="w", padx=(18, 14), pady=5)
        self.message = self.entry(form)
        self.message.grid(row=3, column=1, sticky="we", padx=(0, 18), pady=5)
        self.message.bind("<Return>", lambda _: self.save_alarm())

        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.grid(row=4, column=0, columnspan=2, sticky="we", padx=18, pady=(8, 16))
        self.form_msg = self.label(actions, "", 12)
        self.form_msg.pack(side="left")
        self.save_btn = self.button(actions, "추가", self.save_alarm, primary=True, width=100)
        self.save_btn.pack(side="right")
        self.button(actions, "새로 입력", self.clear_form).pack(side="right", padx=(8, 0))
        self.delete_btn = self.button(actions, "삭제", self.delete_alarm, width=70)
        self.delete_btn.pack(side="right", padx=(8, 0))
        self.button(actions, "미리 듣기", self.preview_alarm).pack(side="right")
        return page

    def render_alarms(self):
        for child in self.alarm_list.winfo_children():
            child.destroy()
        with self.app.lock:
            alarms = sorted((dict(a) for a in self.app.store["alarms"]), key=lambda a: a["time"])
        if not alarms:
            self.label(
                self.alarm_list, "아직 알람이 없어요. 아래에서 시각과 문구를 넣고 [추가]를 눌러 보세요.",
                12, color=C["sub"],
            ).pack(anchor="w", pady=10)
            return
        for alarm in alarms:
            selected = alarm["id"] == self.editing_id
            on = alarm.get("enabled", True)
            card_color = C["sel"] if selected else C["card"]
            card = ctk.CTkFrame(
                self.alarm_list,
                fg_color=card_color,
                border_width=1,
                border_color=C["accent"] if selected else C["line"],
                corner_radius=9,
            )
            card.pack(fill="x", pady=3, padx=(0, 6))
            time_label = self.label(card, alarm["time"], 19, "bold", C["fg"] if on else C["dim"], mono=True)
            time_label.pack(side="left", padx=(16, 16), pady=10)
            # "transparent"로 두면 카드 안쪽에 바깥 배경색이 비쳐서 카드 색을 직접 지정한다
            text = ctk.CTkFrame(card, fg_color=card_color)
            text.pack(side="left", fill="x", expand=True, pady=4)
            msg = self.label(text, clip(alarm["message"], 34), 13, color=C["fg"] if on else C["dim"], anchor="w")
            msg.pack(anchor="w")
            chip = ctk.CTkLabel(
                text, text=describe_repeat(alarm), height=20, corner_radius=10, fg_color=C["btn"],
                text_color=C["sub"], font=self.font(11),
            )
            chip.pack(anchor="w", pady=(0, 2))
            toggle = self.switch(card, lambda i=alarm["id"]: self.toggle_alarm(i))
            toggle.pack(side="right", padx=14)
            if on:
                toggle.select()
            for widget in (card, time_label, text, msg, chip):
                widget.bind("<Button-1>", lambda _e, i=alarm["id"]: self.select_alarm(i))

    def select_alarm(self, alarm_id):
        with self.app.lock:
            alarm = next((dict(a) for a in self.app.store["alarms"] if a["id"] == alarm_id), None)
        if not alarm:
            return
        self.editing_id = alarm_id
        hh, mm = alarm["time"].split(":")
        self.hour.set(hh)
        self.minute.set(mm)
        self.repeat.set(REPEAT_LABELS[alarm["repeat"]])
        self.days = set(alarm.get("days", []))
        self.update_day_buttons()
        self.message.delete(0, "end")
        self.message.insert(0, alarm["message"])
        self.update_form_mode(alarm)
        self.render_alarms()

    def update_form_mode(self, alarm):
        if alarm:
            self.mode_label.configure(text=f"✎ 수정 중: {alarm['time']} {clip(alarm['message'], 30)}")
            self.save_btn.configure(text="수정 저장")
            self.delete_btn.configure(state="normal")
        else:
            self.mode_label.configure(text="＋ 새 알람")
            self.save_btn.configure(text="추가")
            self.delete_btn.configure(state="disabled")

    def clear_form(self):
        self.editing_id = None
        now = datetime.datetime.now()
        self.hour.set(f"{now.hour:02d}")
        self.minute.set(f"{now.minute:02d}")
        self.repeat.set(REPEAT_LABELS["once"])
        self.days = set()
        self.update_day_buttons()
        self.message.delete(0, "end")
        self.update_form_mode(None)
        self.render_alarms()

    def repeat_key(self):
        label = self.repeat.get()
        return next(k for k, v in REPEAT_LABELS.items() if v == label)

    def toggle_day(self, index):
        self.days ^= {index}
        self.update_day_buttons()

    def update_day_buttons(self):
        enabled = self.repeat_key() == "days"
        for i, btn in enumerate(self.day_btns):
            on = enabled and i in self.days
            btn.configure(
                state="normal" if enabled else "disabled",
                fg_color=C["accent"] if on else (C["btn"] if enabled else C["card"]),
                hover_color=C["accent_hover"] if on else C["btn_hover"],
                text_color="#ffffff" if on else C["fg"],
            )

    def alarm_from_form(self):
        try:
            hh, mm = int(self.hour.get()), int(self.minute.get())
            if not (0 <= hh < 24 and 0 <= mm < 60):
                raise ValueError
        except ValueError:
            self.flash(self.form_msg, "시각을 0~23시, 0~59분으로 입력하세요.", C["warn"])
            return None
        message = self.message.get().strip()
        if not message:
            self.flash(self.form_msg, "알림 문구를 입력하세요.", C["warn"])
            return None
        repeat = self.repeat_key()
        days = sorted(self.days)
        if repeat == "days" and not days:
            self.flash(self.form_msg, "요일을 하나 이상 고르세요.", C["warn"])
            return None
        alarm = {"time": f"{hh:02d}:{mm:02d}", "repeat": repeat, "days": days, "message": message}
        if repeat == "once":
            alarm["date"] = next_once_date(hh, mm)
        return alarm

    def save_alarm(self):
        fields = self.alarm_from_form()
        if not fields:
            return
        alarm_id = self.editing_id
        if alarm_id:
            def change(store):
                for alarm in store["alarms"]:
                    if alarm["id"] == alarm_id:
                        alarm.pop("date", None)
                        alarm.update(fields)
                # 시각을 바꿨으면 오늘 이미 울렸더라도 새 시각에 다시 울리게 한다
                self.app.fired.pop(alarm_id, None)

            self.app.update_store(change)
            done = "수정했어요."
        else:
            fields.update(id=uuid.uuid4().hex[:8], enabled=True)
            self.app.update_store(lambda s: s["alarms"].append(fields))
            done = "추가했어요."
        self.clear_form()
        self.flash(self.form_msg, done)

    def delete_alarm(self):
        alarm_id = self.editing_id
        if alarm_id:
            self.app.update_store(
                lambda s: s.__setitem__("alarms", [a for a in s["alarms"] if a["id"] != alarm_id])
            )
            self.clear_form()
            self.flash(self.form_msg, "삭제했어요.")

    def toggle_alarm(self, alarm_id):
        def change(store):
            for alarm in store["alarms"]:
                if alarm["id"] == alarm_id:
                    alarm["enabled"] = not alarm.get("enabled", True)

        self.app.update_store(change)

    def preview_alarm(self):
        message = self.message.get().strip()
        if message:
            self.app.say(f"{korean_time()}입니다. {message}")
        else:
            self.flash(self.form_msg, "알림 문구를 입력하세요.", C["warn"])

    # ----- 캘린더 화면 -----
    def build_calendar_page(self, parent):
        page = ctk.CTkFrame(parent, fg_color="transparent")
        head = self.page_header(page, "캘린더")
        self.brief_switch = self.switch(head, self.toggle_briefing)
        self.brief_switch.pack(side="right")
        self.label(head, "PC 켤 때 오늘 일정 브리핑", 12, color=C["sub"]).pack(side="right", padx=(0, 8))
        if self.app.store["briefing"]:
            self.brief_switch.select()

        bar = ctk.CTkFrame(page, fg_color="transparent")
        bar.pack(fill="x", pady=(10, 8))
        self.button(bar, "◀", lambda: self.move_month(-1), width=36).pack(side="left")
        self.month_label = self.label(bar, "", 17, "bold", width=150)
        self.month_label.pack(side="left", padx=6)
        self.button(bar, "▶", lambda: self.move_month(1), width=36).pack(side="left")
        self.button(bar, "오늘", lambda: self.move_month(0), width=60).pack(side="left", padx=(10, 0))
        self.label(bar, "날짜를 누르면 일정을 추가·수정해요", 12, color=C["sub"]).pack(side="right")

        # 여러 날 일정 막대가 칸을 가로질러야 해서 달력은 캔버스 하나에 직접 그린다
        self.cal_canvas = tk.Canvas(page, bg=C["bg"], highlightthickness=0, bd=0, cursor="hand2")
        self.cal_canvas.pack(fill="both", expand=True)
        self.cal_canvas.bind("<Configure>", lambda _e: self.render_calendar())
        self.cal_canvas.bind("<Button-1>", self.on_calendar_click)
        self.cal_dates = []
        self.cal_geom = None
        return page

    def move_month(self, step):
        if step == 0:
            self.cal_month = datetime.date.today().replace(day=1)
        else:
            year, month = divmod(self.cal_month.month - 1 + step, 12)
            self.cal_month = datetime.date(self.cal_month.year + year, month + 1, 1)
        self.render_calendar()

    def render_calendar(self):
        first = self.cal_month
        self.month_label.configure(text=f"{first.year}년 {first.month}월")
        canvas = self.cal_canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 100 or height < 100:
            return  # 아직 화면에 나오기 전
        s = self.scaling
        head = 26 * s
        col_w, row_h = width / 7, (height - head) / 6
        self.cal_geom = (col_w, row_h, head)
        start = first - datetime.timedelta(days=(first.weekday() + 1) % 7)  # 일요일부터
        self.cal_dates = [start + datetime.timedelta(days=i) for i in range(42)]
        today = datetime.date.today()
        with self.app.lock:
            events = [dict(e) for e in self.app.store["events"]]

        f_head = tkfont.Font(family=UI_FONT, size=-int(12 * s), weight="bold")
        f_num = tkfont.Font(family=UI_FONT, size=-int(13 * s), weight="bold")
        f_holiday = tkfont.Font(family=UI_FONT, size=-int(10 * s), weight="bold")
        f_bar = tkfont.Font(family=UI_FONT, size=-int(11 * s))
        f_tiny = tkfont.Font(family=UI_FONT, size=-int(10 * s))

        for col, name in enumerate("일월화수목금토"):
            color = C["warn"] if col == 0 else (C["brk"] if col == 6 else C["sub"])
            canvas.create_text(col_w * col + col_w / 2, head / 2, text=name, fill=color, font=f_head)

        lane_top, lane_h, lane_gap = 30 * s, 18 * s, 3 * s
        max_lanes = max(1, int((row_h - lane_top - 16 * s) // (lane_h + lane_gap)))
        for week in range(6):
            y0 = head + week * row_h
            days = self.cal_dates[week * 7: week * 7 + 7]

            # 날짜 칸: 숫자, 공휴일, 음력
            for col, day in enumerate(days):
                x0 = col * col_w
                in_month = day.month == first.month
                is_today = day == today
                round_rect(
                    canvas, x0 + 2 * s, y0 + 2 * s, x0 + col_w - 2 * s, y0 + row_h - 2 * s, 8 * s,
                    fill=C["card"] if in_month else C["bg"],
                    outline=C["accent"] if is_today else C["line"], width=2 if is_today else 1,
                )
                holiday = holiday_name(day)
                weekday = (day.weekday() + 1) % 7  # 0 = 일요일
                color = C["warn"] if weekday == 0 or holiday else (C["brk"] if weekday == 6 else C["fg"])
                if is_today:
                    color = C["mode"]
                if not in_month:
                    color = C["dim"]
                number = f"{day.day}" + (" 오늘" if is_today else "")
                canvas.create_text(x0 + 9 * s, y0 + 7 * s, text=number, anchor="nw", fill=color, font=f_num)
                if holiday:
                    hx = x0 + 9 * s + f_num.measure(number) + 5 * s
                    canvas.create_text(
                        hx, y0 + 9 * s, anchor="nw", font=f_holiday,
                        text=fit_text(f_holiday, holiday.replace(" 대체 휴일", " 대체"), x0 + col_w - hx - 6 * s),
                        fill=C["warn"] if in_month else C["dim"],
                    )
                canvas.create_text(
                    x0 + col_w - 8 * s, y0 + row_h - 6 * s, text=lunar_short(day), anchor="se",
                    fill=C["dim"], font=f_tiny,
                )

            # 일정 막대: 기간 일정은 여러 칸을 이어서, 겹치지 않게 줄(lane)을 나눈다
            segments = []
            for event in events:
                if is_recurring(event):
                    # 반복 일정은 이 주에서 해당하는 날마다 하루짜리로 그린다
                    segments += [(c, c, False, False, event) for c, day in enumerate(days) if occurs_on(event, day)]
                    continue
                first_day, last_day = event_start(event), event_end(event)
                if last_day < days[0] or first_day > days[-1]:
                    continue
                c1 = max(0, (first_day - days[0]).days)
                c2 = min(6, (last_day - days[0]).days)
                segments.append((c1, c2, first_day < days[0], last_day > days[-1], event))
            segments.sort(key=lambda g: (g[0], -(g[1] - g[0]), g[4]["time"]))
            lanes, hidden = [], [0] * 7
            for c1, c2, cont_left, cont_right, event in segments:
                cols = set(range(c1, c2 + 1))
                lane = next((i for i, used in enumerate(lanes) if not used & cols), len(lanes))
                if lane == len(lanes):
                    lanes.append(set())
                lanes[lane] |= cols
                if lane >= max_lanes:
                    for col in cols:
                        hidden[col] += 1
                    continue
                x1 = c1 * col_w + (2 * s if cont_left else 6 * s)
                x2 = (c2 + 1) * col_w - (2 * s if cont_right else 6 * s)
                y1 = y0 + lane_top + lane * (lane_h + lane_gap)
                base = event_color(event)
                round_rect(canvas, x1, y1, x2, y1 + lane_h, 4 * s, fill=mix(base, C["card"], 0.32), outline="")
                if cont_left:
                    text = "… " + event["title"]
                elif is_period(event):
                    text = event["title"]
                else:
                    text = ("↻" if is_recurring(event) else "") + f"{event['time']} {event['title']}"
                canvas.create_text(
                    x1 + 6 * s, y1 + lane_h / 2, anchor="w", font=f_bar,
                    text=fit_text(f_bar, text, x2 - x1 - 10 * s), fill=mix(base, "#ffffff", 0.5),
                )
            for col, count in enumerate(hidden):
                if count:
                    canvas.create_text(
                        col * col_w + 9 * s, y0 + row_h - 6 * s, text=f"+{count}개", anchor="sw",
                        fill=C["sub"], font=f_tiny,
                    )

    def on_calendar_click(self, event):
        if not self.cal_geom or not self.cal_dates:
            return
        col_w, row_h, head = self.cal_geom
        if event.y < head:
            return
        col = min(6, int(event.x // col_w))
        row = min(5, int((event.y - head) // row_h))
        self.open_day(self.cal_dates[row * 7 + col])

    def toggle_briefing(self):
        enabled = bool(self.brief_switch.get())
        self.app.update_store(lambda s: s.__setitem__("briefing", enabled))

    # ----- 일정 팝업 -----
    def open_day(self, day):
        if not day:
            return
        if self.day_dialog and self.day_dialog.winfo_exists():
            self.day_dialog.destroy()
        self.day_date = day
        self.event_editing = None
        dialog = ctk.CTkToplevel(self.root, fg_color=C["bg"])
        self.day_dialog = dialog
        dialog.title(f"{day.month}월 {day.day}일 일정")
        self.root.update_idletasks()
        w = 580
        h = min(780, int(self.root.winfo_screenheight() / self.scaling) - 80)
        x = self.root.winfo_rootx() + (self.root.winfo_width() - int(w * self.scaling)) // 2
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - int(h * self.scaling)) // 2)
        # 팝업 아래쪽이 화면(작업 표시줄) 밖으로 나가지 않게 올린다
        y = min(y, self.root.winfo_screenheight() - int((h + 60) * self.scaling))
        dialog.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        dialog.transient(self.root)
        dialog.after(150, dialog.grab_set)

        head = ctk.CTkFrame(dialog, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(16, 6))
        titles = ctk.CTkFrame(head, fg_color="transparent")
        titles.pack(side="left")
        self.label(titles, f"{day:%Y-%m-%d} ({WEEKDAYS[day.weekday()]})", 18, "bold").pack(anchor="w")
        lunar = lunar_date(day)
        details = [f"음력 {'윤' if lunar[0] else ''}{lunar[1]}월 {lunar[2]}일"] if lunar else []
        if holiday_name(day):
            details.append(holiday_name(day))
        if details:
            self.label(
                titles, " · ".join(details), 12, color=C["warn"] if holiday_name(day) else C["sub"]
            ).pack(anchor="w")
        self.button(head, "닫기", dialog.destroy, width=64).pack(side="right")

        self.day_list = ctk.CTkScrollableFrame(
            dialog, fg_color="transparent", height=120,
            scrollbar_button_color=C["btn"], scrollbar_button_hover_color=C["btn_hover"],
        )
        self.day_list.pack(fill="x", padx=12)

        form = self.card(dialog)
        form.configure(border_color=C["accent"])
        form.pack(fill="both", expand=True, padx=18, pady=(8, 18))
        form.grid_columnconfigure(1, weight=1)
        self.ev_mode = self.label(form, "", 13, "bold", C["mode"])
        self.ev_mode.grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(14, 6))

        self.label(form, "시간", color=C["sub"]).grid(row=1, column=0, sticky="w", padx=(18, 14), pady=5)
        when = ctk.CTkFrame(form, fg_color="transparent")
        when.grid(row=1, column=1, sticky="w", pady=5)
        self.ev_hour = self.combobox(when, [f"{h:02d}" for h in range(24)], 72)
        self.ev_hour.pack(side="left")
        self.label(when, ":", 15, "bold").pack(side="left", padx=5)
        self.ev_minute = self.combobox(when, [f"{m:02d}" for m in range(0, 60, 5)], 72)
        self.ev_minute.pack(side="left")

        # 알림: 시작 [얼마] 전부터 [간격] 마다 + 시작 시각에도 (고르거나 직접 입력)
        self.label(form, "알림", color=C["sub"]).grid(row=2, column=0, sticky="nw", padx=(18, 14), pady=(9, 5))
        alerts = ctk.CTkFrame(form, fg_color="transparent")
        alerts.grid(row=2, column=1, sticky="w", pady=5)
        controls = ctk.CTkFrame(alerts, fg_color="transparent")
        controls.pack(anchor="w")
        self.label(controls, "시작", color=C["sub"]).pack(side="left", padx=(0, 6))
        self.ev_lead = self.combobox(controls, list(LEADS), 92)
        self.ev_lead.configure(command=lambda _v: self.update_alert_preview())
        self.ev_lead.pack(side="left")
        self.label(controls, "전부터", color=C["sub"]).pack(side="left", padx=6)
        self.ev_interval = self.combobox(controls, list(INTERVALS), 104)
        self.ev_interval.configure(command=lambda _v: self.update_alert_preview())
        self.ev_interval.pack(side="left")
        self.label(controls, "마다", color=C["sub"]).pack(side="left", padx=(6, 12))
        self.ev_at_start = ctk.CTkCheckBox(
            controls, text="시작 시각에도", command=self.update_alert_preview, width=40, font=self.font(12),
            text_color=C["fg"], fg_color=C["accent"], hover_color=C["accent_hover"], border_color=C["line_hl"],
            checkbox_width=20, checkbox_height=20,
        )
        self.ev_at_start.pack(side="left")
        self.ev_preview = self.label(alerts, "", 11, color=C["mode"], anchor="w", justify="left")
        self.ev_preview.pack(anchor="w", pady=(4, 0))
        for widget in (self.ev_hour, self.ev_minute, self.ev_lead, self.ev_interval):
            widget.bind("<KeyRelease>", lambda _e: self.update_alert_preview())
        self.ev_hour.configure(command=lambda _v: self.update_alert_preview())
        self.ev_minute.configure(command=lambda _v: self.update_alert_preview())

        self.label(form, "날짜", color=C["sub"]).grid(row=3, column=0, sticky="w", padx=(18, 14), pady=5)
        dates = ctk.CTkFrame(form, fg_color="transparent")
        dates.grid(row=3, column=1, sticky="w", pady=5)
        self.ev_start = self.entry(dates, 118)
        self.ev_start.pack(side="left")
        self.ev_period = ctk.CTkCheckBox(
            dates, text="기간", command=self.on_period_toggle, width=60, font=self.font(13),
            text_color=C["fg"], fg_color=C["accent"], hover_color=C["accent_hover"], border_color=C["line_hl"],
        )
        self.ev_period.pack(side="left", padx=(14, 6))
        self.label(dates, "~", 14, color=C["sub"]).pack(side="left", padx=(0, 6))
        self.ev_end = self.entry(dates, 118)
        self.ev_end.pack(side="left")
        self.ev_start.bind("<FocusOut>", lambda _e: self.update_repeat_options())
        self.ev_start.bind("<Return>", lambda _e: self.update_repeat_options())

        self.label(form, "반복", color=C["sub"]).grid(row=4, column=0, sticky="w", padx=(18, 14), pady=5)
        repeat_row = ctk.CTkFrame(form, fg_color="transparent")
        repeat_row.grid(row=4, column=1, sticky="w", pady=5)
        self.ev_repeat = ctk.CTkOptionMenu(
            repeat_row, values=["반복 안 함"], width=190, command=lambda _v: self.on_repeat_change(),
            **self.dropdown_style()
        )
        self.ev_repeat.pack(side="left")
        self.ev_until_label = self.label(repeat_row, "종료", color=C["sub"])
        self.ev_until_label.pack(side="left", padx=(14, 6))
        self.ev_until = ctk.CTkEntry(
            repeat_row, width=118, height=32, corner_radius=6, fg_color=C["input"], border_color=C["line"],
            border_width=1, text_color=C["fg"], font=self.font(13), placeholder_text="계속 반복",
            placeholder_text_color=C["dim"],
        )
        self.ev_until.pack(side="left")

        self.label(form, "색", color=C["sub"]).grid(row=5, column=0, sticky="w", padx=(18, 14), pady=5)
        swatches = ctk.CTkFrame(form, fg_color="transparent")
        swatches.grid(row=5, column=1, sticky="w", pady=5)
        self.ev_swatches = {}
        for key, color in EVENT_COLORS.items():
            swatch = ctk.CTkButton(
                swatches, text="", width=26, height=26, corner_radius=13, fg_color=color, hover_color=color,
                border_color="#ffffff", border_width=0, command=lambda k=key: self.pick_color(k, touched=True),
            )
            swatch.pack(side="left", padx=(0, 8))
            self.ev_swatches[key] = swatch

        self.label(form, "제목", color=C["sub"]).grid(row=6, column=0, sticky="w", padx=(18, 14), pady=5)
        self.ev_title = self.entry(form)
        self.ev_title.grid(row=6, column=1, sticky="we", padx=(0, 18), pady=5)

        self.label(form, "메모", color=C["sub"]).grid(row=7, column=0, sticky="nw", padx=(18, 14), pady=8)
        self.ev_memo = ctk.CTkTextbox(
            form, height=60, corner_radius=6, fg_color=C["input"], border_color=C["line"], border_width=1,
            text_color=C["fg"], font=self.font(13),
        )
        self.ev_memo.grid(row=7, column=1, sticky="nsew", padx=(0, 18), pady=5)
        form.grid_rowconfigure(7, weight=1)

        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.grid(row=8, column=0, columnspan=2, sticky="we", padx=18, pady=(8, 16))
        self.ev_msg = self.label(actions, "", 12)
        self.ev_msg.pack(side="left")
        self.ev_save = self.button(actions, "추가", self.save_event, primary=True, width=100)
        self.ev_save.pack(side="right")
        self.button(actions, "새로 입력", self.clear_event_form).pack(side="right", padx=8)

        self.clear_event_form()
        self.ev_title.focus_set()

    @staticmethod
    def set_entry(entry, text):
        state = entry.cget("state")
        entry.configure(state="normal")
        entry.delete(0, "end")
        entry.insert(0, text)
        entry.configure(state=state)

    def pick_color(self, key, touched=False):
        self.ev_color = key
        if touched:
            self.ev_color_touched = True
        for name, swatch in self.ev_swatches.items():
            swatch.configure(border_width=3 if name == key else 0)

    def current_start(self):
        try:
            return datetime.date.fromisoformat(self.ev_start.get().strip())
        except ValueError:
            return self.day_date

    def selected_repeat(self):
        label = self.ev_repeat.get()
        return next((k for k, v in getattr(self, "repeat_choices", []) if v == label), "none")

    def update_repeat_options(self, kind=None):
        """시작 날짜가 바뀌면 반복 선택지(예: '매월 셋째 화요일')를 그 날짜에 맞게 다시 만든다."""
        kind = kind or self.selected_repeat()
        self.repeat_choices = repeat_options(self.current_start())
        self.ev_repeat.configure(values=[label for _, label in self.repeat_choices])
        self.ev_repeat.set(dict(self.repeat_choices).get(kind, "반복 안 함"))
        self.on_repeat_change()

    def on_repeat_change(self):
        on = self.selected_repeat() != "none"
        self.ev_until.configure(state="normal" if on else "disabled")
        self.ev_until_label.configure(text_color=C["sub"] if on else C["dim"])
        if on and self.ev_period.get():
            self.ev_period.deselect()  # 반복은 하루 일정에만
            self.on_period_toggle()

    def set_until(self, text):
        self.ev_until.configure(state="normal")
        self.ev_until.delete(0, "end")
        if text:
            self.ev_until.insert(0, text)
        elif hasattr(self.ev_until, "_activate_placeholder"):
            self.ev_until._activate_placeholder()  # 비어 있으면 '계속 반복' 안내를 다시 보여 준다
        self.on_repeat_change()

    def on_period_toggle(self):
        on = bool(self.ev_period.get())
        if on and self.selected_repeat() != "none":
            self.ev_repeat.set("반복 안 함")  # 기간 일정은 반복할 수 없다
            self.on_repeat_change()
        self.ev_end.configure(state="normal" if on else "disabled", text_color=C["fg"] if on else C["dim"])
        if on:
            try:
                start = datetime.date.fromisoformat(self.ev_start.get().strip())
                end = datetime.date.fromisoformat(self.ev_end.get().strip())
                if end <= start:
                    self.set_entry(self.ev_end, (start + datetime.timedelta(days=1)).isoformat())
            except ValueError:
                pass
        if not self.ev_color_touched:
            self.pick_color("yellow" if on else "purple")

    def render_day_events(self):
        for child in self.day_list.winfo_children():
            child.destroy()
        day = self.day_date
        with self.app.lock:
            events = sorted(
                (dict(e) for e in self.app.store["events"] if occurs_on(e, day)),
                key=lambda e: (not is_period(e), e["time"]),
            )
        if not events:
            self.label(self.day_list, "이 날은 일정이 없어요. 아래에서 추가해 보세요.", 12, color=C["sub"]).pack(
                anchor="w", padx=6, pady=8
            )
            return
        for event in events:
            editing = event["id"] == self.event_editing
            bg = C["sel"] if editing else C["card"]
            row = ctk.CTkFrame(
                self.day_list, fg_color=bg, border_width=1,
                border_color=C["accent"] if editing else C["line"], corner_radius=8,
            )
            row.pack(fill="x", pady=3, padx=(0, 6))
            self.label(row, event["time"], 16, "bold", event_color(event), mono=True).pack(
                side="left", padx=(14, 12), pady=8
            )
            text = ctk.CTkFrame(row, fg_color=bg)
            text.pack(side="left", fill="x", expand=True, pady=4)
            self.label(text, clip(event["title"], 26), 13, "bold", anchor="w").pack(anchor="w")
            parts = []
            if is_recurring(event):
                parts.append("↻ " + repeat_label(event))
            if is_period(event):
                first, last = event_start(event), event_end(event)
                parts.append(f"{first.month}/{first.day} ~ {last.month}/{last.day}")
            parts.append(describe_alerts(event))
            memo = event.get("memo", "").strip().splitlines()
            if memo:
                parts.append(clip(memo[0], 18))
            self.label(text, " · ".join(parts), 11, color=C["sub"], anchor="w").pack(anchor="w")
            self.button(row, "삭제", lambda e=event: self.ask_delete(e), width=54).pack(side="right", padx=(4, 12))
            self.button(row, "수정", lambda e=event: self.load_event(e), width=54).pack(side="right")

    def clear_event_form(self):
        self.event_editing = None
        self.ev_hour.set("09")
        self.ev_minute.set("00")
        self.ev_lead.set("2시간")
        self.ev_interval.set("30분")
        self.ev_at_start.select()
        self.set_entry(self.ev_start, self.day_date.isoformat())
        self.ev_period.deselect()
        self.ev_end.configure(state="normal")
        self.set_entry(self.ev_end, (self.day_date + datetime.timedelta(days=1)).isoformat())
        self.ev_end.configure(state="disabled", text_color=C["dim"])
        self.ev_color_touched = False
        self.pick_color("purple")
        self.update_repeat_options("none")
        self.set_until("")
        self.ev_title.delete(0, "end")
        self.ev_memo.delete("1.0", "end")
        self.ev_mode.configure(text="＋ 새 일정")
        self.ev_save.configure(text="추가")
        self.update_alert_preview()
        self.render_day_events()

    def load_event(self, event):
        self.event_editing = event["id"]
        hh, mm = event["time"].split(":")
        self.ev_hour.set(hh)
        self.ev_minute.set(mm)
        lead, interval, at_start = event_alert_settings(event)
        self.ev_lead.set(fmt_left(lead))
        self.ev_interval.set(interval_label(interval))
        self.ev_at_start.select() if at_start else self.ev_at_start.deselect()
        self.set_entry(self.ev_start, event["date"])
        self.ev_end.configure(state="normal", text_color=C["fg"])
        if is_period(event):
            self.ev_period.select()
            self.set_entry(self.ev_end, event["end_date"])
        else:
            self.ev_period.deselect()
            self.set_entry(self.ev_end, (event_start(event) + datetime.timedelta(days=1)).isoformat())
            self.ev_end.configure(state="disabled", text_color=C["dim"])
        self.ev_color_touched = True
        self.pick_color(event.get("color") or ("yellow" if is_period(event) else "purple"))
        self.update_repeat_options(repeat_kind(event))
        self.set_until((event.get("repeat") or {}).get("until") or "")
        self.ev_title.delete(0, "end")
        self.ev_title.insert(0, event["title"])
        self.ev_memo.delete("1.0", "end")
        self.ev_memo.insert("1.0", event.get("memo", ""))
        self.ev_mode.configure(text=f"✎ 수정 중: {clip(event['title'], 28)}")
        self.ev_save.configure(text="수정 저장")
        self.update_alert_preview()
        self.render_day_events()

    def save_event(self):
        try:
            hh, mm = int(self.ev_hour.get()), int(self.ev_minute.get())
            if not (0 <= hh < 24 and 0 <= mm < 60):
                raise ValueError
        except ValueError:
            self.flash(self.ev_msg, "시간을 0~23시, 0~59분으로 입력하세요.", C["warn"])
            return
        try:
            start = datetime.date.fromisoformat(self.ev_start.get().strip())
            end = datetime.date.fromisoformat(self.ev_end.get().strip()) if self.ev_period.get() else start
        except ValueError:
            self.flash(self.ev_msg, "날짜는 2026-10-17처럼 입력하세요.", C["warn"])
            return
        if end < start:
            self.flash(self.ev_msg, "종료일이 시작일보다 빠를 수 없어요.", C["warn"])
            return
        if (end - start).days > 366:
            self.flash(self.ev_msg, "기간은 1년까지만 정할 수 있어요.", C["warn"])
            return
        self.update_repeat_options()  # 시작 날짜를 고친 뒤 바로 저장해도 선택지가 맞도록
        repeat = self.selected_repeat()
        until = None
        if repeat != "none" and self.ev_until.get().strip():
            try:
                until = datetime.date.fromisoformat(self.ev_until.get().strip())
            except ValueError:
                self.flash(self.ev_msg, "반복 종료일은 2026-12-31처럼 입력하거나 비워 두세요.", C["warn"])
                return
            if until < start:
                self.flash(self.ev_msg, "반복 종료일이 시작일보다 빠를 수 없어요.", C["warn"])
                return
        title = self.ev_title.get().strip()
        if not title:
            self.flash(self.ev_msg, "제목을 입력하세요.", C["warn"])
            return
        alert = self.read_alert_settings()
        if isinstance(alert, str):
            self.flash(self.ev_msg, alert, C["warn"])
            return
        fields = {
            "date": start.isoformat(),
            "time": f"{hh:02d}:{mm:02d}",
            "title": title,
            "memo": self.ev_memo.get("1.0", "end").strip(),
            "interval": alert[1],
            "lead": alert[0],
            "at_start": alert[2],
            "color": self.ev_color,
        }
        if end > start:
            fields["end_date"] = end.isoformat()
        if repeat != "none":
            fields["repeat"] = {"type": repeat, "until": until.isoformat() if until else None}
        event_id = self.event_editing
        if event_id:
            def change(store):
                for event in store["events"]:
                    if event["id"] == event_id:
                        event.pop("end_date", None)
                        event.pop("repeat", None)
                        event.update(fields)

            self.app.update_store(change)
            # 시간을 바꿨으면 알림을 처음부터 다시 보낸다
            self.app.event_fired = {k for k in self.app.event_fired if k[0] != event_id}
            done = "수정했어요."
        else:
            fields["id"] = uuid.uuid4().hex[:8]
            self.app.update_store(lambda s: s["events"].append(fields))
            done = "추가했어요."
        if repeat != "none":
            done += f" ({repeat_label(fields)})"
        elif event_dt(fields) <= datetime.datetime.now():
            done += " 시작 시각이 지나서 알림은 울리지 않아요."
        self.clear_event_form()
        self.flash(self.ev_msg, done, ms=5000)

    def read_alert_settings(self):
        """(시작 몇 분 전부터, 간격, 시작 시각에도) 또는 잘못됐을 때 안내 문구."""
        interval_text = self.ev_interval.get().strip()
        interval = INTERVALS.get(interval_text)
        if interval is None:
            interval = parse_minutes(interval_text)
            if not interval:
                return "간격은 목록에서 고르거나 '45분'처럼 입력하세요."
        lead = LEADS.get(self.ev_lead.get().strip())
        if lead is None:
            lead = parse_minutes(self.ev_lead.get())
            if lead is None:
                return "시작 전 시간은 목록에서 고르거나 '90분', '1시간 30분', '3일'처럼 입력하세요."
        if lead > 7 * 1440:
            return "시작 전 알림은 7일 전까지만 정할 수 있어요."
        at_start = bool(self.ev_at_start.get())
        if len(alert_offsets(lead, interval, at_start)) > MAX_ALERTS:
            return f"알림이 {MAX_ALERTS}번을 넘어요. 간격을 늘리거나 시작 전 시간을 줄여 주세요."
        return lead, interval, at_start

    def update_alert_preview(self):
        """고른 설정으로 실제 알림이 울릴 시각을 미리 보여 준다."""
        alert = self.read_alert_settings()
        if isinstance(alert, str):
            self.ev_preview.configure(text=alert, text_color=C["warn"])
            return
        try:
            start = datetime.datetime.combine(
                self.current_start(), datetime.time(int(self.ev_hour.get()), int(self.ev_minute.get()))
            )
        except ValueError:
            self.ev_preview.configure(text="시간을 먼저 바르게 입력하세요.", text_color=C["warn"])
            return
        offsets = alert_offsets(*alert)
        if not offsets:
            self.ev_preview.configure(text="음성 알림을 보내지 않아요.", text_color=C["sub"])
            return

        def stamp(left):
            at = start - datetime.timedelta(minutes=left)
            days = (start.date() - at.date()).days
            prefix = "" if days == 0 else ("전날 " if days == 1 else f"{days}일 전 ")
            return prefix + at.strftime("%H:%M") + ("(시작)" if left == 0 else "")

        stamps = [stamp(left) for left in offsets]
        if len(stamps) > 7:
            stamps = stamps[:3] + ["…"] + stamps[-2:]
        self.ev_preview.configure(text=f"→ {' · '.join(stamps)}  (총 {len(offsets)}번)", text_color=C["mode"])

    def ask_delete(self, event):
        """반복 일정이면 '이 날만'과 '반복 전체' 중에서 고르게 한다."""
        if not is_recurring(event):
            self.delete_event(event["id"])
            return
        parent = self.day_dialog
        popup = ctk.CTkToplevel(parent, fg_color=C["bg"])
        popup.title("반복 일정 삭제")
        parent.update_idletasks()
        w, h = 400, 190
        x = parent.winfo_rootx() + (parent.winfo_width() - int(w * self.scaling)) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - int(h * self.scaling)) // 2
        popup.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        popup.resizable(False, False)
        popup.transient(parent)
        popup.after(150, popup.grab_set)

        def close():
            popup.destroy()
            if parent.winfo_exists():
                parent.grab_set()  # 일정 팝업으로 다시 돌아간다

        day = self.day_date
        body = self.card(popup, highlight=True)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        self.label(body, f"'{clip(event['title'], 16)}'은(는) 반복 일정이에요", 14, "bold").pack(
            anchor="w", padx=16, pady=(16, 4)
        )
        self.label(
            body, f"{day.month}월 {day.day}일만 지울까요, 반복 전체를 지울까요?", 12, color=C["sub"]
        ).pack(anchor="w", padx=16)
        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(fill="x", side="bottom", padx=16, pady=16)

        def only_this_day():
            self.skip_occurrence(event["id"], day)
            close()

        def all_of_them():
            self.delete_event(event["id"])
            close()

        self.button(buttons, "반복 전체 삭제", all_of_them, primary=True, width=120).pack(side="right")
        self.button(buttons, "이 날만 삭제", only_this_day, width=110).pack(side="right", padx=8)
        self.button(buttons, "취소", close, width=60).pack(side="right")
        popup.protocol("WM_DELETE_WINDOW", close)

    def skip_occurrence(self, event_id, day):
        def change(store):
            for event in store["events"]:
                if event["id"] == event_id:
                    event.setdefault("skip", []).append(day.isoformat())

        self.app.update_store(change)
        self.flash(self.ev_msg, f"{day.month}월 {day.day}일 일정만 삭제했어요.")

    def delete_event(self, event_id):
        self.app.update_store(lambda s: s.__setitem__("events", [e for e in s["events"] if e["id"] != event_id]))
        if self.event_editing == event_id:
            self.clear_event_form()
        self.flash(self.ev_msg, "삭제했어요.")

    # ----- 음성 화면 -----
    def build_voice_page(self, parent):
        with self.app.lock:
            voice = dict(self.app.store["voice"])
        page = ctk.CTkFrame(parent, fg_color="transparent")
        self.page_header(page, "음성")
        box = self.card(page)
        box.pack(fill="x", pady=(14, 0))
        box.grid_columnconfigure(1, weight=1)

        self.label(box, "목소리", color=C["sub"]).grid(row=0, column=0, sticky="w", padx=(18, 14), pady=(18, 8))
        self.voice_name = ctk.CTkOptionMenu(
            box, values=list(VOICES.values()), width=260, command=lambda _: self.save_voice(),
            **self.dropdown_style()
        )
        self.voice_name.set(VOICES.get(voice["name"], VOICES["sapi"]))
        self.voice_name.grid(row=0, column=1, sticky="w", pady=(18, 8))

        self.voice_values = {}
        sliders = [
            ("volume", "볼륨", 0, 100, 100, lambda v: f"{v}%"),
            ("rate", "빠르기", -10, 10, 20, lambda v: f"{v:+d}" if v else "보통"),
        ]
        for row, (key, text, lo, hi, steps, fmt) in enumerate(sliders, start=1):
            self.label(box, text, color=C["sub"]).grid(row=row, column=0, sticky="w", padx=(18, 14), pady=10)
            value_label = self.label(box, fmt(voice[key]), 13, width=56, anchor="e")
            slider = ctk.CTkSlider(
                box, from_=lo, to=hi, number_of_steps=steps, height=18,
                progress_color=C["accent"], fg_color=C["line"], button_color=C["accent"],
                button_hover_color=C["accent_hover"],
                command=lambda v, k=key, lbl=value_label, f=fmt: self.on_slide(k, v, lbl, f),
            )
            slider.set(voice[key])
            slider.grid(row=row, column=1, sticky="we", pady=10)
            value_label.grid(row=row, column=2, padx=(10, 18))
            self.voice_values[key] = voice[key]

        ctk.CTkFrame(box, height=1, fg_color=C["line"]).grid(
            row=3, column=0, columnspan=3, sticky="we", padx=18, pady=(8, 4)
        )
        duck_row = ctk.CTkFrame(box, fg_color="transparent")
        duck_row.grid(row=4, column=0, columnspan=3, sticky="we", padx=18, pady=(8, 2))
        title = ctk.CTkFrame(duck_row, fg_color="transparent")
        title.pack(side="left")
        self.label(title, "알림 동안 다른 소리 줄이기", 13, "bold").pack(anchor="w")
        self.label(
            title, "유튜브·방송 같은 다른 프로그램 소리를 잠깐 줄였다가 알림이 끝나면 되돌려요.",
            12, color=C["sub"],
        ).pack(anchor="w")
        self.duck_switch = self.switch(duck_row, self.on_duck_toggle)
        self.duck_switch.pack(side="right")
        if voice["duck"]:
            self.duck_switch.select()

        self.label(box, "줄였을 때 크기", color=C["sub"]).grid(row=5, column=0, sticky="w", padx=(18, 14), pady=10)
        duck_input = ctk.CTkFrame(box, fg_color="transparent")
        self.duck_entry = self.entry(duck_input, 52)
        self.duck_entry.configure(justify="right")
        self.duck_entry.insert(0, str(voice["duck_level"]))
        self.duck_entry.pack(side="left")
        self.duck_entry.bind("<Return>", lambda _: self.on_duck_entry())
        self.duck_entry.bind("<FocusOut>", lambda _: self.on_duck_entry())
        self.duck_percent = self.label(duck_input, "%", 13)
        self.duck_percent.pack(side="left", padx=(4, 0))
        self.duck_slider = ctk.CTkSlider(
            box, from_=0, to=80, number_of_steps=80, height=18,
            progress_color=C["accent"], fg_color=C["line"], button_color=C["accent"],
            button_hover_color=C["accent_hover"], command=self.on_duck_slide,
        )
        self.duck_slider.set(voice["duck_level"])
        self.duck_slider.grid(row=5, column=1, sticky="we", pady=10)
        duck_input.grid(row=5, column=2, padx=(10, 18))
        self.voice_values["duck_level"] = voice["duck_level"]
        self.update_duck_slider()

        actions = ctk.CTkFrame(box, fg_color="transparent")
        actions.grid(row=6, column=0, columnspan=3, sticky="we", padx=18, pady=(6, 18))
        self.voice_msg = self.label(actions, "", 12)
        self.voice_msg.pack(side="left")
        self.button(actions, "미리 듣기", self.preview_voice, primary=True, width=100).pack(side="right")
        self.label(
            page, "온라인 목소리는 인터넷이 필요해요. 연결이 안 되면 해미 목소리로 대신 읽어요.\n"
            "바꾼 설정은 바로 저장되고 다음 알림부터 적용돼요.",
            12, color=C["sub"], justify="left",
        ).pack(anchor="w", pady=(10, 0))
        return page

    def on_slide(self, key, value, value_label, fmt):
        self.voice_values[key] = int(round(value))
        if value_label:
            value_label.configure(text=fmt(self.voice_values[key]))
        # 끄는 동안 계속 저장하지 않도록 손을 뗀 뒤 잠깐 있다가 저장한다
        if self.voice_job:
            self.root.after_cancel(self.voice_job)
        self.voice_job = self.root.after(400, self.save_voice)

    def on_duck_toggle(self):
        self.update_duck_slider()
        self.save_voice()

    def on_duck_slide(self, value):
        level = int(round(value))
        self.duck_entry.delete(0, "end")
        self.duck_entry.insert(0, str(level))
        self.on_slide("duck_level", value, None, None)

    def on_duck_entry(self):
        """칸에 직접 넣은 숫자를 0~80으로 맞춰 슬라이더와 설정에 반영한다."""
        try:
            level = min(80, max(0, int(float(self.duck_entry.get().strip().rstrip("%")))))
        except ValueError:
            level = self.voice_values["duck_level"]  # 숫자가 아니면 원래 값으로 되돌린다
        self.duck_entry.delete(0, "end")
        self.duck_entry.insert(0, str(level))
        if level != self.voice_values["duck_level"]:
            self.duck_slider.set(level)
            self.voice_values["duck_level"] = level
            self.save_voice()

    def update_duck_slider(self):
        on = bool(self.duck_switch.get())
        self.duck_slider.configure(
            state="normal" if on else "disabled",
            progress_color=C["accent"] if on else C["off"],
            button_color=C["accent"] if on else C["off"],
        )
        self.duck_entry.configure(state="normal" if on else "disabled", text_color=C["fg"] if on else C["dim"])
        self.duck_percent.configure(text_color=C["fg"] if on else C["dim"])

    def save_voice(self):
        self.voice_job = None
        label = self.voice_name.get()
        voice = {
            "name": next((k for k, v in VOICES.items() if v == label), "sapi"),
            "rate": self.voice_values["rate"],
            "volume": self.voice_values["volume"],
            "duck": bool(self.duck_switch.get()),
            "duck_level": self.voice_values["duck_level"],
        }
        self.app.update_store(lambda s: s.__setitem__("voice", voice))

    def preview_voice(self):
        self.save_voice()
        self.app.say(f"{korean_time()}입니다. 이 목소리로 알려 드릴게요.")
        self.flash(self.voice_msg, "읽는 중… 온라인 목소리는 2~4초 걸려요.", C["sub"])

    # ----- 게임 목록 화면 -----
    def build_game_page(self, parent):
        page = ctk.CTkFrame(parent, fg_color="transparent")
        self.page_header(page, "게임 목록")
        self.label(
            page, "목록에 있는 프로그램이 실행 중이면 알림을 모아 두었다가 게임이 끝나면 한꺼번에 읽어 줘요.",
            12, color=C["sub"],
        ).pack(anchor="w", pady=(4, 8))
        self.game_list = ctk.CTkScrollableFrame(
            page, fg_color="transparent", height=200,
            scrollbar_button_color=C["btn"], scrollbar_button_hover_color=C["btn_hover"],
        )
        self.game_list.pack(fill="both", expand=True)

        add = self.card(page)
        add.pack(fill="x", pady=(10, 0))
        row = ctk.CTkFrame(add, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(16, 6))
        self.game_entry = self.combobox(row, [], 260)
        self.game_entry.set("")
        self.game_entry.pack(side="left", fill="x", expand=True)
        self.game_entry.bind("<Return>", lambda _: self.add_game())
        self.button(row, "추가", self.add_game, primary=True, width=80).pack(side="right")
        self.button(row, "실행 중 목록", self.refresh_processes, width=110).pack(side="right", padx=8)
        self.game_msg = self.label(add, "게임을 켠 상태에서 [실행 중 목록]을 누르고 ▾에서 고르세요.", 12, color=C["sub"])
        self.game_msg.pack(anchor="w", padx=18, pady=(0, 14))
        self.game_status = {}
        return page

    def render_games(self):
        for child in self.game_list.winfo_children():
            child.destroy()
        self.game_status = {}
        with self.app.lock:
            games = list(self.app.store["games"])
        if not games:
            self.label(self.game_list, "등록한 게임이 없어요.", 12, color=C["sub"]).pack(anchor="w", pady=10)
            return
        for game in games:
            row = ctk.CTkFrame(self.game_list, fg_color=C["card"], border_width=1, border_color=C["line"], corner_radius=8)
            row.pack(fill="x", pady=3, padx=(0, 6))
            ctk.CTkLabel(
                row, text=game[:1].upper(), width=30, height=30, corner_radius=7, fg_color=C["game_icon"],
                text_color=C["mode"], font=self.font(13, "bold", mono=True),
            ).pack(side="left", padx=(12, 12), pady=9)
            self.label(row, game).pack(side="left")
            self.button(row, "삭제", lambda g=game: self.delete_game(g), width=60).pack(side="right", padx=12)
            status = self.label(row, "", 12)
            status.pack(side="right", padx=4)
            self.game_status[game.lower()] = status
        self.update_game_status()

    def update_game_status(self):
        active = {g.lower() for g in self.app.active_games}
        for name, status in self.game_status.items():
            if name in active:
                status.configure(text="● 실행 중", text_color=C["good"])
            else:
                status.configure(text="꺼짐", text_color=C["sub"])

    def refresh_processes(self):
        names = sorted(running_processes(), key=str.lower)
        self.game_entry.configure(values=names)
        self.flash(self.game_msg, f"실행 중인 프로그램 {len(names)}개를 불러왔어요. ▾를 눌러 고르세요.", C["sub"], ms=8000)

    def add_game(self):
        name = self.game_entry.get().strip()
        if not name:
            self.flash(self.game_msg, "게임 실행 파일 이름을 입력하거나 고르세요.", C["warn"])
            return
        if "." not in name:
            name += ".exe"
        with self.app.lock:
            exists = name.lower() in {g.lower() for g in self.app.store["games"]}
        if exists:
            self.flash(self.game_msg, f"{name}은(는) 이미 목록에 있어요.", C["warn"])
            return
        self.app.update_store(lambda s: s["games"].append(name))
        self.game_entry.set("")
        self.flash(self.game_msg, f"{name}을(를) 추가했어요.")

    def delete_game(self, name):
        self.app.update_store(lambda s: s.__setitem__("games", [g for g in s["games"] if g != name]))

    # ----- 설정 화면 -----
    def build_settings_page(self, parent):
        page = ctk.CTkFrame(parent, fg_color="transparent")
        self.page_header(page, "설정")

        startup = self.card(page)
        startup.pack(fill="x", pady=(14, 0))
        text = ctk.CTkFrame(startup, fg_color="transparent")
        text.pack(side="left", padx=18, pady=16)
        self.label(text, "PC 켜면 자동으로 실행", 14, "bold").pack(anchor="w")
        self.label(
            text, "Windows에 로그인하면 작업 표시줄 트레이에서 조용히 시작해요.", 12, color=C["sub"]
        ).pack(anchor="w")
        self.autostart_switch = self.switch(startup, self.toggle_autostart)
        self.autostart_switch.pack(side="right", padx=18)
        if autostart_enabled():
            self.autostart_switch.select()

        data = self.card(page)
        data.pack(fill="x", pady=(12, 0))
        info = ctk.CTkFrame(data, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True, padx=18, pady=16)
        self.label(info, "데이터 저장 위치", 14, "bold").pack(anchor="w")
        self.label(info, str(BASE_DIR), 12, color=C["sub"], anchor="w").pack(anchor="w")
        self.label(
            info, "alarms.json: 알람·일정·설정  ·  records.json: PC 사용 기록", 12, color=C["sub"]
        ).pack(anchor="w")
        self.button(data, "폴더 열기", lambda: os.startfile(BASE_DIR), width=90).pack(side="right", padx=18)

        self.settings_msg = self.label(page, "", 12)
        self.settings_msg.pack(anchor="w", pady=(10, 0))
        self.label(page, f"시간 알림 v{APP_VERSION}", 12, color=C["dim"]).pack(side="bottom", anchor="w")
        return page

    def toggle_autostart(self):
        enabled = bool(self.autostart_switch.get())
        if set_autostart(enabled):
            self.flash(
                self.settings_msg,
                "PC를 켜면 자동으로 실행돼요." if enabled else "자동 실행을 껐어요.",
            )
        else:
            # 실패하면 스위치를 실제 상태로 되돌린다
            self.autostart_switch.select() if autostart_enabled() else self.autostart_switch.deselect()
            self.flash(self.settings_msg, "자동 실행 설정을 바꾸지 못했어요. 다시 시도해 주세요.", C["warn"])

    # ----- 사용 기록 화면 -----
    def build_records_page(self, parent):
        page = ctk.CTkFrame(parent, fg_color="transparent")
        head = self.page_header(page, "사용 기록")
        self.button(head, "txt로 내보내기", self.export_all, width=120).pack(side="right")
        self.label(
            page, "PC를 켜고 끈 시각을 날짜별로 남겨요. 기록은 1년 동안 보관해요.", 12, color=C["sub"]
        ).pack(anchor="w", pady=(4, 10))

        stats = ctk.CTkFrame(page, fg_color="transparent")
        stats.pack(fill="x")
        self.stat_values = {}
        for col, (key, title) in enumerate([("today", "오늘 사용"), ("week", "이번 주 사용"), ("kept", "보관 중인 기록")]):
            box = self.card(stats)
            box.grid(row=0, column=col, sticky="we", padx=(0 if col == 0 else 10, 0))
            stats.grid_columnconfigure(col, weight=1)
            self.label(box, title, 12, color=C["sub"]).pack(anchor="w", padx=16, pady=(12, 0))
            value = self.label(box, "", 18, "bold")
            value.pack(anchor="w", padx=16, pady=(0, 12))
            self.stat_values[key] = value

        table = self.card(page)
        table.pack(fill="both", expand=True, pady=(12, 0))
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Rec.Treeview", background=C["card"], fieldbackground=C["card"], foreground=C["fg"],
            rowheight=int(30 * self.scaling), borderwidth=0, font=(UI_FONT, 10),
        )
        style.configure(
            "Rec.Treeview.Heading", background=C["btn"], foreground=C["sub"], relief="flat",
            borderwidth=0, font=(UI_FONT, 10, "bold"),
        )
        style.map("Rec.Treeview", background=[("selected", C["accent_soft"])], foreground=[("selected", "#ffffff")])
        style.map("Rec.Treeview.Heading", background=[("active", C["btn_hover"])])
        style.layout("Rec.Treeview", [("Rec.Treeview.treearea", {"sticky": "nswe"})])  # 테두리 없애기

        columns = [("date", "날짜", 120), ("wd", "요일", 60), ("on", "켜진 시간", 100), ("off", "꺼진 시간", 120), ("dur", "사용 시간", 120)]
        self.records_tree = ttk.Treeview(table, columns=[c[0] for c in columns], show="headings", style="Rec.Treeview")
        for key, title, width in columns:
            self.records_tree.heading(key, text=title)
            self.records_tree.column(key, width=int(width * self.scaling), anchor="center")
        self.records_tree.tag_configure("today", foreground=C["fg"])
        self.records_tree.tag_configure("old", foreground=C["sub"])
        self.records_tree.tag_configure("current", foreground=C["good"])
        bar = ctk.CTkScrollbar(table, command=self.records_tree.yview, button_color=C["btn"], button_hover_color=C["btn_hover"])
        self.records_tree.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y", padx=(0, 6), pady=8)
        self.records_tree.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)

        self.records_msg = self.label(page, "", 12)
        self.records_msg.pack(anchor="w", pady=(8, 0))
        return page

    def render_records(self):
        sessions, current = self.app.records_snapshot()
        today = datetime.date.today()
        monday = today - datetime.timedelta(days=today.weekday())
        today_sec = week_sec = 0
        tree = self.records_tree
        tree.delete(*tree.get_children())
        for session in reversed(sessions):  # 최근 기록이 위로
            start, end = parse_ts(session["start"]), parse_ts(session["end"])
            is_current = session["start"] == current["start"]
            seconds = (end - start).total_seconds()
            if start.date() == today:
                today_sec += seconds
            if start.date() >= monday:
                week_sec += seconds
            tag = "current" if is_current else ("today" if start.date() == today else "old")
            tree.insert("", "end", values=session_row(session, current=is_current), tags=(tag,))
        self.stat_values["today"].configure(text=fmt_duration(today_sec))
        self.stat_values["week"].configure(text=fmt_duration(week_sec))
        since = parse_ts(sessions[0]["start"]).strftime("%Y-%m-%d") if sessions else "-"
        self.stat_values["kept"].configure(text=f"{len(sessions)}건 · {since}부터")

    def save_txt(self, sessions, filename):
        """txt 저장 위치를 물어보고 저장한다. 취소하면 None."""
        BACKUP_DIR.mkdir(exist_ok=True)
        path = filedialog.asksaveasfilename(
            parent=self.root, title="txt로 저장", initialdir=str(BACKUP_DIR), initialfile=filename,
            defaultextension=".txt", filetypes=[("텍스트 파일", "*.txt")],
        )
        if not path:
            return None
        Path(path).write_text(records_text(sessions), encoding="utf-8-sig")
        return path

    def export_all(self):
        sessions, _ = self.app.records_snapshot()
        if not sessions:
            self.flash(self.records_msg, "아직 기록이 없어요.", C["warn"])
            return
        path = self.save_txt(sessions, f"PC사용기록_{datetime.date.today()}.txt")
        if path:
            self.flash(self.records_msg, f"저장했어요: {path}", ms=8000)

    def check_retention(self):
        """하루에 한 번, 1년 지난 기록이 있으면 정리할지 묻는다."""
        today = datetime.date.today()
        if self.retention_day == today:
            return
        self.retention_day = today
        expired, snooze_until = self.app.expired_records(today)
        if expired and not (snooze_until and today.isoformat() < snooze_until):
            self.show_retention_dialog(expired)

    def show_retention_dialog(self, expired):
        self.root.deiconify()
        self.root.lift()
        self.show_page("records")
        first = parse_ts(expired[0]["start"]).strftime("%Y-%m-%d")
        last = parse_ts(expired[-1]["start"]).strftime("%Y-%m-%d")

        dialog = ctk.CTkToplevel(self.root, fg_color=C["bg"])
        dialog.title("기록 정리")
        # 메인 창 가운데에 띄운다 (geometry 크기는 배율 적용 전, 위치는 실제 화면 좌표)
        self.root.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - int(480 * self.scaling)) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - int(330 * self.scaling)) // 3
        dialog.geometry(f"480x330+{max(0, x)}+{max(0, y)}")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.attributes("-topmost", True)
        dialog.after(150, dialog.grab_set)  # 답할 때까지 뒤 창은 못 누르게

        body = self.card(dialog, highlight=True)
        body.pack(fill="both", expand=True, padx=16, pady=16)
        self.label(body, "보관 기간이 지난 기록이 있어요", 17, "bold").pack(anchor="w", padx=20, pady=(20, 6))
        self.label(
            body, f"1년이 지난 PC 사용 기록 {len(expired)}건\n{first} ~ {last}", 13,
            color=C["sub"], justify="left",
        ).pack(anchor="w", padx=20)
        save_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            body, text="txt 파일로 따로 저장하기", variable=save_var, font=self.font(13),
            text_color=C["fg"], fg_color=C["accent"], hover_color=C["accent_hover"], border_color=C["line_hl"],
        ).pack(anchor="w", padx=20, pady=(18, 4))
        self.label(
            body, f"'지우지 않고 보관'을 고르면 {SNOOZE_DAYS}일 뒤에 다시 물어볼게요.", 12, color=C["sub"]
        ).pack(anchor="w", padx=20)
        msg = self.label(body, "", 12)
        msg.pack(anchor="w", padx=20, pady=(6, 0))

        def finish(delete):
            saved = None
            if save_var.get():
                saved = self.save_txt(expired, f"PC사용기록_{first}_{last}.txt")
                if not saved:
                    self.flash(msg, "저장을 취소했어요. 다시 고르거나 체크를 풀어 주세요.", C["warn"])
                    return
            if delete:
                count = self.app.delete_expired(datetime.date.today())
                text = f"1년 지난 기록 {count}건을 삭제했어요."
            else:
                self.app.snooze_retention(SNOOZE_DAYS)
                text = f"기록을 보관해요. {SNOOZE_DAYS}일 뒤에 다시 물어볼게요."
            if saved:
                text += f" (txt 저장: {saved})"
            dialog.destroy()
            self.flash(self.records_msg, text, ms=10000)

        def later():
            # 창을 그냥 닫으면 내일 다시 묻는다
            self.app.snooze_retention(1)
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", later)
        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(fill="x", side="bottom", padx=20, pady=20)
        self.button(buttons, "삭제", lambda: finish(True), primary=True, width=100).pack(side="right")
        self.button(buttons, "지우지 않고 보관", lambda: finish(False), width=140).pack(side="right", padx=8)

    # ----- 주기적 갱신 -----
    def refresh_store_views(self):
        with self.app.lock:
            ids = {a["id"] for a in self.app.store["alarms"]}
        if self.editing_id and self.editing_id not in ids:
            # 수정 중이던 알람이 울리고 사라짐
            self.editing_id = None
            self.update_form_mode(None)
        self.render_alarms()
        self.render_games()
        self.render_calendar()
        if self.day_dialog and self.day_dialog.winfo_exists():
            self.render_day_events()

    def update_status(self):
        snap = self.app.cycle_snapshot()
        cycle = snap["cycle"]
        left = int(math.ceil(snap["left"]))
        clock = f"{left // 60:02d}:{left % 60:02d}"
        fraction = snap["left"] / snap["total"] if snap["total"] else 0
        work = snap["phase"] == "work"
        if not cycle["enabled"]:
            ring_color, label, state, state_color = C["off"], "꺼짐", "꺼짐", C["sub"]
            clock, fraction = "--:--", 0
        elif snap["paused"]:
            ring_color, label, state, state_color = C["sub"], "일시정지", "일시정지", C["sub"]
        elif work:
            ring_color, label, state, state_color = C["accent"], "휴식까지", "작업 중", C["good"]
        else:
            ring_color, label, state, state_color = C["brk"], "작업까지", "휴식 중", C["brk"]

        key = (clock, label, ring_color, round(fraction, 3))
        if key != self.ring_key and self.root.winfo_viewable():
            self.ring_key = key
            self.ring_img = ctk.CTkImage(ring_image(fraction, ring_color, clock, label), size=(184, 184))
            self.ring.configure(image=self.ring_img)

        self.pill_work.configure(
            text=f"  작업 {fmt_min(cycle['work_min'])}분  ",
            fg_color=C["accent"] if cycle["enabled"] and work else C["btn"],
            text_color="#ffffff" if cycle["enabled"] and work else C["sub"],
        )
        self.pill_break.configure(
            text=f"  휴식 {fmt_min(cycle['break_min'])}분  ",
            fg_color=C["brk"] if cycle["enabled"] and not work else C["btn"],
            text_color="#ffffff" if cycle["enabled"] and not work else C["sub"],
        )
        self.state_label.configure(text=state, text_color=state_color)
        if cycle["enabled"]:
            self.info_label.configure(text=f"{snap['cycle_no']}번째 사이클 · {korean_time(snap['started'])}부터")
        else:
            self.info_label.configure(text="메인 알람이 꺼져 있어요. 특정 시각 알람은 그대로 울려요.")
        counts = self.app.today_counts()
        self.count_label.configure(text=f"오늘 작업 {counts['work']}회 · 휴식 {counts['break']}회")
        self.pause_btn.configure(
            text="재개" if snap["paused"] else "일시정지", state="normal" if cycle["enabled"] else "disabled"
        )
        if bool(self.cycle_switch.get()) != cycle["enabled"]:
            self.cycle_switch.select() if cycle["enabled"] else self.cycle_switch.deselect()

        self.side_label.configure(text=label)
        self.side_clock.configure(text=clock)
        upcoming = self.app.next_alarm()
        if upcoming:
            at, alarm = upcoming
            days_away = (at.date() - datetime.date.today()).days
            when = at.strftime("%H:%M")
            if days_away == 1:
                when = "내일 " + when
            elif days_away > 1:
                when = f"{WEEKDAYS[at.weekday()]}요일 {when}"
            self.side_next.configure(text=f"다음 알람  {when}\n{clip(alarm['message'], 20)}")
        else:
            self.side_next.configure(text="다음 알람 없음")

        if self.app.gaming:
            self.game_text.configure(text="게임 감지됨 · " + ", ".join(self.app.active_games))
            count = len(self.app.missed)
            self.game_wait.configure(
                text=f"알림 {count}개 보류 중 · 게임이 끝나면 알려 드려요" if count
                else "알림이 오면 모아 뒀다가 게임이 끝나면 알려 드려요"
            )
            if not self.gamebar_shown:
                self.gamebar.grid(row=0, column=0, sticky="we")
                self.gamebar_shown = True
        elif self.gamebar_shown:
            self.gamebar.grid_remove()
            self.gamebar_shown = False
        self.update_game_status()

    def poll(self):
        while True:
            try:
                request = self.app.ui_requests.get_nowait()
            except queue.Empty:
                break
            if request == "quit":
                self.root.destroy()
                return
            if request == "show":
                self.root.deiconify()
                self.root.lift()
                self.root.focus_force()
                self.ring_key = None  # 숨어 있는 동안 멈췄던 타이머를 바로 다시 그린다
        if self.app.store_version != self.seen_version:
            self.seen_version = self.app.store_version
            self.refresh_store_views()
        if self.app.records_version != self.records_seen:
            self.records_seen = self.app.records_version
            self.render_records()
        self.check_retention()
        if getattr(self, "cal_today", None) != datetime.date.today():
            self.cal_today = datetime.date.today()  # 자정이 지나면 '오늘' 표시를 옮긴다
            self.render_calendar()
        self.update_status()
        self.root.after(500, self.poll)

    def run(self):
        self.poll()
        self.root.mainloop()


def already_running():
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\TimeAlarm_PatternApp")
    return ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


if __name__ == "__main__":
    if already_running():
        sys.exit(0)
    TimeAlarm().run()

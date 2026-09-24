"""
系统健康看门狗 — 外部定时检查，静默故障主动邮件告警 + 自动重启雷达。

背景：本次故障是「雷达进程假死 5 天 + 每日推荐被数据门禁阻断」，系统没有任何
主动告警，用户只能靠「收不到邮件」才被动发现。本脚本作为外部独立进程定时跑，
不再依赖被监控对象自身存活。

检查项：
1. 盘中雷达：launchd 进程存活 + 交易时段内最新快照 now 是否新鲜（超阈值判假死）
2. 每日推荐：latest.json 的 core_source_meta.quotes.status（非 fresh 判推荐被阻断）
3. 行情快照：quotes.json 的 fetched_at 是否当日

用法：
  python -m scripts.health_watchdog            # 只检查，打印报告
  python -m scripts.health_watchdog --alert    # 异常时发告警邮件
  python -m scripts.health_watchdog --restart  # 雷达假死时自动 kickstart 重启
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

BEIJING_TZ = timezone(timedelta(hours=8))
RADAR_LAUNCHD_LABEL = "com.fangcang.new-france-radar"
RADAR_LATEST = PROJECT_DIR / "reports" / "smart_money_radar_latest.json"
LATEST_JSON = PROJECT_DIR / "reports" / "latest.json"
QUOTES_SNAPSHOT = PROJECT_DIR / "reports" / "data_backend" / "quotes.json"
# 交易时段内，雷达快照超过该分钟未更新即判假死
RADAR_STALE_MINUTES = 15


def _now() -> datetime:
    return datetime.now(BEIJING_TZ)


def _parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BEIJING_TZ)
    return dt.astimezone(BEIJING_TZ)


def _trading_session(now: datetime) -> str:
    from backend.services.data_backend.trading_session import current_trading_session

    return current_trading_session(now)


def check_radar() -> tuple[bool, str]:
    """返回 (healthy, detail)。"""
    # 1) launchd 进程是否存活
    try:
        out = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=10
        ).stdout
        alive = RADAR_LAUNCHD_LABEL in out
    except Exception as exc:
        return False, f"launchctl 查询失败: {exc}"
    if not alive:
        return False, "雷达 launchd 进程未运行"

    # 2) 交易时段内检查快照新鲜度
    now = _now()
    session = _trading_session(now)
    if session != "open":
        return True, f"雷达进程存活（非交易时段 {session}，跳过新鲜度检查）"
    try:
        data = json.loads(RADAR_LATEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "雷达快照文件缺失或损坏"
    last = _parse_iso(data.get("now"))
    if last is None:
        return False, "雷达快照 now 字段缺失"
    age_min = (now - last).total_seconds() / 60
    if age_min > RADAR_STALE_MINUTES:
        return False, f"雷达假死：快照已 {age_min:.0f} 分钟未更新（阈值 {RADAR_STALE_MINUTES}）"
    return True, f"雷达正常（快照 {age_min:.1f} 分钟前更新）"


def check_recommendation() -> tuple[bool, str]:
    """返回 (healthy, detail)。healthy=False 表示推荐可能被阻断。"""
    now = _now()
    if now.weekday() >= 5:
        return True, "周末，跳过推荐检查"
    if now.hour * 60 + now.minute < 15 * 60 + 20:
        return True, "未到 15:20，跳过推荐检查（每日任务 15:10 才跑）"
    try:
        data = json.loads(LATEST_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "latest.json 缺失或损坏（今日每日任务可能未运行）"
    quotes_meta = (data.get("core_source_meta") or {}).get("quotes") or {}
    status = quotes_meta.get("status")
    if status != "fresh":
        return False, f"推荐被阻断：quotes 状态={status}（error={quotes_meta.get('error')}）"
    return True, "推荐正常（quotes fresh）"


def check_data_freshness() -> tuple[bool, str]:
    """行情快照是否当日。"""
    try:
        data = json.loads(QUOTES_SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "quotes 快照缺失或损坏"
    fetched = _parse_iso(data.get("fetched_at"))
    if fetched is None:
        return False, "quotes 快照 fetched_at 缺失"
    if fetched.date() != _now().date():
        return False, f"quotes 快照非当日（{fetched.date()}）"
    return True, "quotes 快照当日"


def restart_radar() -> tuple[bool, str]:
    """launchctl kickstart -k 强制重启雷达。"""
    try:
        uid = subprocess.run(["id", "-u"], capture_output=True, text=True, timeout=5).stdout.strip()
        subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{uid}/{RADAR_LAUNCHD_LABEL}"],
            capture_output=True, text=True, timeout=30,
        )
        return True, "已发送 kickstart 重启指令"
    except Exception as exc:
        return False, f"重启失败: {exc}"


def send_alert(problems: list[str]) -> tuple[bool, str]:
    from backend.agents.layer3_recommendation.notifier import _get_notify_config, _send_email

    now_str = _now().strftime("%Y-%m-%d %H:%M:%S")
    lines = ["New France 系统健康告警", f"时间：{now_str}", "", "以下检查项异常：", ""]
    for p in problems:
        lines.append(f"  - {p}")
    lines.extend(["", "请检查系统状态。"])
    text = "\n".join(lines)
    html_body = "".join(f"<li>{p}</li>" for p in problems)
    html = f"""<!DOCTYPE html><html><body style="font-family:sans-serif;padding:20px">
<h2 style="color:#C0392B">New France 系统健康告警</h2><p>时间：{now_str}</p>
<ul>{html_body}</ul></body></html>"""
    return _send_email(
        subject="[New France] 系统健康告警",
        text_content=text,
        html_content=html,
        notify_config=_get_notify_config(),
    )


def main():
    parser = argparse.ArgumentParser(description="New France 系统健康看门狗")
    parser.add_argument("--alert", action="store_true", help="异常时发告警邮件")
    parser.add_argument("--restart", action="store_true", help="雷达假死时自动重启")
    args = parser.parse_args()

    checks = [
        ("盘中雷达", check_radar),
        ("每日推荐", check_recommendation),
        ("行情快照", check_data_freshness),
    ]
    problems = []
    radar_dead = False
    for name, fn in checks:
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"检查异常: {exc}"
        print(f"[{'OK' if ok else 'FAIL'}] {name}: {detail}")
        if not ok:
            problems.append(f"{name}: {detail}")
            if name == "盘中雷达":
                radar_dead = True

    if not problems:
        print("全部健康")
        return 0

    if args.restart and radar_dead:
        ok, detail = restart_radar()
        print(f"[restart] {detail}")
        problems.append(f"雷达自动重启: {'成功' if ok else detail}")

    if args.alert:
        ok, msg = send_alert(problems)
        print(f"[alert] 邮件{'已发送' if ok else '发送失败: ' + msg}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

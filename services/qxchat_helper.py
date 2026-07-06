"""
qxChat API helper - fetch raw message timestamps for M05/M13
Reads msgtime from qxChat API, processes per-group timing data.
Uses in-memory caching (TTL 5 min) to avoid repeated API calls.
"""

import time
from collections import defaultdict
from typing import Any, Dict, List, Optional
from loguru import logger

_cache: dict = {"data": None, "ts": 0, "ttl": 300}

def _fetch_raw() -> List[dict]:
    import httpx
    from config.settings import settings
    url = settings.JAVA_DATA_SOURCE_URL
    timeout = settings.JAVA_DATA_SOURCE_TIMEOUT
    try:
        with httpx.Client(timeout=timeout) as cli:
            resp = cli.get(url)
            resp.raise_for_status()
            raw = resp.json()
            msgs = raw.get("data", [])
            logger.info(f"qxChat API returned {len(msgs)} messages")
            return msgs
    except Exception as e:
        logger.error(f"qxChat API call failed: {e}")
        return []

def _process(msgs: List[dict]) -> dict:
    from datetime import datetime
    groups = {}
    for msg in msgs:
        rid = str(msg.get("roomid", "") or "")
        mt = str(msg.get("msgtime", "") or "")
        if not rid or not mt:
            continue
        if rid not in groups:
            groups[rid] = {
                "first_time": mt, "last_time": mt,
                "hours": {}, "days": {}, "weekday_hours": {},
            }
        g = groups[rid]
        if mt < g["first_time"]:
            g["first_time"] = mt
        if mt > g["last_time"]:
            g["last_time"] = mt
        try:
            if len(mt) >= 13:
                h = int(mt[11:13])
                g["hours"][h] = g["hours"].get(h, 0) + 1
            if len(mt) >= 10:
                d = mt[:10]
                g["days"][d] = g["days"].get(d, 0) + 1
                dt = datetime.strptime(d, "%Y-%m-%d")
                wd = dt.weekday()
                key = "{}:{}".format(wd, h)
                g["weekday_hours"][key] = g["weekday_hours"].get(key, 0) + 1
        except (ValueError, IndexError):
            pass
    return groups

def get_time_data(force_refresh: bool = False) -> dict:
    now = time.time()
    if not force_refresh and _cache["data"] and (now - _cache["ts"]) < _cache["ttl"]:
        return _cache["data"]
    msgs = _fetch_raw()
    if not msgs:
        if _cache["data"]:
            return _cache["data"]
        return {"groups": {}, "total_messages": 0, "error": "qxChat API unreachable"}
    groups = _process(msgs)
    result = {
        "groups": groups,
        "total_messages": len(msgs),
        "total_groups": len(groups),
        "fetched_at": now,
    }
    _cache["data"] = result
    _cache["ts"] = now
    logger.info(f"Msgtime processed: {len(msgs)} msgs, {len(groups)} groups")
    return result

def _day_diff(t1: str, t2: str) -> int:
    from datetime import datetime
    try:
        d1 = datetime.strptime(t1[:10], "%Y-%m-%d")
        d2 = datetime.strptime(t2[:10], "%Y-%m-%d")
        return abs((d2 - d1).days)
    except Exception:
        return 0

BUCKET_DEFS = [
    ("<=7\u5929", "\u6781\u77ed\u671f\u54a8\u8be2"),
    ("8-30\u5929", "\u77ed\u671f\u670d\u52a1"),
    ("1-3\u4e2a\u6708", "\u5e38\u89c4\u9879\u76ee\u5468\u671f"),
    ("3-6\u4e2a\u6708", "\u4e2d\u957f\u671f\u9879\u76ee"),
    ("6-12\u4e2a\u6708", "\u957f\u671f\u670d\u52a1"),
    (">12\u4e2a\u6708", "\u8d85\u957f\u671f\u5408\u4f5c"),
]

def compute_active_duration() -> dict:
    """M05: Group active duration from msgtime"""
    data = get_time_data()
    groups = data.get("groups", {})
    if not groups:
        err = data.get("error", "")
        return {
            "buckets": [{"range": r, "label": l, "count": 0, "percentage": 0}
                        for r, l in BUCKET_DEFS],
            "note": err or "No msgtime data", "total_groups": 0,
        }
    buckets = {}
    for rid, g in groups.items():
        d = _day_diff(g["first_time"], g["last_time"])
        key = "<=7\u5929"
        if d > 365: key = ">12\u4e2a\u6708"
        elif d > 180: key = "6-12\u4e2a\u6708"
        elif d > 90: key = "3-6\u4e2a\u6708"
        elif d > 30: key = "1-3\u4e2a\u6708"
        elif d > 7: key = "8-30\u5929"
        buckets[key] = buckets.get(key, 0) + 1
    total = sum(buckets.values()) or 1
    items = [{"range": r, "label": l, "count": buckets.get(r, 0),
              "percentage": round(buckets.get(r, 0) / total * 100, 1)}
             for r, l in BUCKET_DEFS]
    return {"buckets": items, "note": "", "total_groups": total}

def compute_time_distribution(days: int = 30) -> dict:
    """M13: Message time distribution (hourly/daily/weekday-heatmap)"""
    from datetime import datetime, timedelta
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    data = get_time_data()
    groups = data.get("groups", {})
    if not groups:
        err = data.get("error", "")
        return {"hours": [{"hour": h, "count": 0} for h in range(24)],
                "days": [], "total_messages": 0,
                "weekday_heatmap": {wd: [0]*24 for wd in weekday_names},
                "weekday_names": weekday_names, "note": err or "No data"}
    total_hours = {}
    total_days = {}
    total_weekday_hours = {}
    for g in groups.values():
        for h, c in g["hours"].items():
            total_hours[h] = total_hours.get(h, 0) + c
        for d, c in g["days"].items():
            total_days[d] = total_days.get(d, 0) + c
        for wk, c in g.get("weekday_hours", {}).items():
            total_weekday_hours[wk] = total_weekday_hours.get(wk, 0) + c
    hours = [{"hour": h, "count": total_hours.get(h, 0)} for h in range(24)]
    cutoff = None
    if days:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    days_list = [{"date": d, "count": total_days[d]}
                 for d in sorted(total_days.keys())
                 if not cutoff or d >= cutoff]
    weekday_heatmap = {}
    for wd_idx, wd_name in enumerate(weekday_names):
        row = [total_weekday_hours.get("{}:{}".format(wd_idx, h), 0) for h in range(24)]
        weekday_heatmap[wd_name] = row
    return {"hours": hours, "days": days_list,
            "weekday_heatmap": weekday_heatmap,
            "weekday_names": weekday_names,
            "total_messages": data.get("total_messages", 0), "note": ""}

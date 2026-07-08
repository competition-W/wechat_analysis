"""Read-only dashboard analytics built from the existing MySQL analysis tables."""

from __future__ import annotations

import copy
import json
import re
import threading
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from loguru import logger


CACHE_TTL_SECONDS = 300
PROJECT_CODE_RE = re.compile(r"LC-(?:SP|P)\d+(?![A-Z0-9])", re.IGNORECASE)
ALLOWED_PRODUCT_L2 = {"常规转录组", "表观组学", "微生物"}
_cache: Dict[str, Tuple[float, dict]] = {}
_last_success: Dict[str, dict] = {}
_cache_lock = threading.Lock()


@contextmanager
def database(operation: str):
    """Open an isolated connection so concurrent requests never share protocol state."""
    import pymysql
    from config.settings import settings

    started = time.perf_counter()
    logger.info("dashboard.db.connect.start operation={}", operation)
    conn = pymysql.connect(
        host=settings.MYSQL_HOST,
        port=settings.MYSQL_PORT,
        user=settings.MYSQL_USER,
        password=settings.MYSQL_PASSWORD,
        database=settings.MYSQL_DATABASE,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=8,
        read_timeout=30,
        write_timeout=30,
        autocommit=True,
    )
    try:
        yield conn
    finally:
        conn.close()
        logger.info(
            "dashboard.db.connect.end operation={} elapsed_ms={:.1f}",
            operation,
            (time.perf_counter() - started) * 1000,
        )


def _query(conn, operation: str, sql: str, params: Iterable[Any] = ()) -> List[dict]:
    started = time.perf_counter()
    logger.info("dashboard.db.query.start operation={}", operation)
    with conn.cursor() as cursor:
        cursor.execute(sql, tuple(params))
        rows = list(cursor.fetchall())
    logger.info(
        "dashboard.db.query.end operation={} rows={} elapsed_ms={:.1f}",
        operation,
        len(rows),
        (time.perf_counter() - started) * 1000,
    )
    return rows


def _table_columns(conn, table_name: str) -> set[str]:
    rows = _query(conn, f"schema.{table_name}", f"SHOW COLUMNS FROM {table_name}")
    return {str(row.get("Field") or "").lower() for row in rows}


def _first_existing_column(columns: set[str], *candidates: str) -> Optional[str]:
    for candidate in candidates:
        if candidate.lower() in columns:
            return candidate
    return None


def _field_value_case_insensitive(item: dict, *names: str) -> Any:
    if not item:
        return None
    lowered = {str(key).lower(): value for key, value in item.items()}
    for name in names:
        if name in item and item[name] not in (None, ""):
            return item[name]
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def parse_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"-?\d+", str(value))
    return int(match.group()) if match else None


def extract_lims_active_day(item: dict) -> Optional[int]:
    return parse_int(_field_value_case_insensitive(
        item,
        "activeDay", "active_day", "activeDays", "activeLay", "activeLlay",
        "activelay", "activellay", "ACTIVE_DAY", "ACTIVEDAY",
    ))


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def extract_project_codes(group_name: str) -> List[str]:
    if not group_name:
        return []
    codes = []
    for match in PROJECT_CODE_RE.findall(group_name.upper()):
        code = match.rstrip("-")
        if code not in codes:
            codes.append(code)
    return codes


def is_focus_group_name(group_name: str) -> bool:
    return bool(extract_project_codes(group_name))


def get_project_codes(group_name: str) -> List[str]:
    return extract_project_codes(group_name)


def parse_count_map(value: Any) -> Dict[str, int]:
    """Parse the database's comma-separated `label: count` fields."""
    if value is None:
        return {}
    text = str(value).strip()
    if not text:
        return {}
    result: Dict[str, int] = {}
    for part in re.split(r"[,，]", text):
        part = part.strip()
        if not part or ":" not in part and "：" not in part:
            continue
        key, raw = re.split(r"[:：]", part, maxsplit=1)
        match = re.search(r"-?\d+", raw)
        if match:
            result[key.strip().strip("\"'")] = int(match.group())
    return result


def parse_emotion_field(value: Any) -> Dict[str, int]:
    return parse_count_map(value)


def parse_send_detail(value: Any) -> Dict[str, int]:
    return parse_count_map(value)


def parse_high_freq(value: Any) -> List[dict]:
    return [
        {"word": word, "count": count}
        for word, count in parse_count_map(value).items()
    ]


def parse_members(value: Any) -> List[str]:
    if not value:
        return []
    text = str(value).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            names = []
            for item in parsed:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("group_nickname")
                else:
                    name = str(item)
                if name:
                    names.append(str(name).strip())
            return names
    except (json.JSONDecodeError, TypeError):
        pass
    return [item.strip() for item in re.split(r"[,，、;；\n]", text) if item.strip()]


def parse_json_object(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_msg_datetime(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{10,13}", text):
        timestamp = int(text)
        if len(text) >= 13:
            timestamp = timestamp / 1000
        try:
            return datetime.fromtimestamp(timestamp)
        except (OSError, OverflowError, ValueError):
            return None
    normalized = text.replace("T", " ").replace("/", "-").replace("Z", "")
    if "." in normalized:
        normalized = normalized.split(".", 1)[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(normalized[:len(fmt.replace("%Y", "0000").replace("%m", "00").replace("%d", "00").replace("%H", "00").replace("%M", "00").replace("%S", "00"))], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(normalized).replace(tzinfo=None)
    except ValueError:
        return None


def chat_msgtime(row: dict) -> Optional[datetime]:
    raw = parse_json_object(row_get(row, "raw_json", "rawJson", default=None))
    value = _field_value_case_insensitive(raw, "msgtime", "msgTime", "msg_time", "createTime", "createtime")
    return parse_msg_datetime(value or row_get(row, "msgtime", "msg_time", default=None))


def chat_text(row: dict) -> str:
    raw = parse_json_object(row_get(row, "raw_json", "rawJson", default=None))
    return first_nonempty(
        _field_value_case_insensitive(raw, "content", "text", "msg", "message", "msgContent"),
        row_get(row, "content", "text", "message", default=""),
    )


def chat_sender(row: dict) -> str:
    raw = parse_json_object(row_get(row, "raw_json", "rawJson", default=None))
    return first_nonempty(
        _field_value_case_insensitive(raw, "sender_name", "sender", "from", "fromName", "name"),
        row_get(row, "sender_name", "sender", "from", default=""),
    )


def chat_msgid(row: dict) -> str:
    raw = parse_json_object(row_get(row, "raw_json", "rawJson", default=None))
    return first_nonempty(
        _field_value_case_insensitive(raw, "msgid", "msgId", "id"),
        row_get(row, "msgid", "msgId", "id", default=""),
    )


def row_get(row: dict, *names: str, default: Any = "") -> Any:
    """Case-insensitive row lookup for tables whose column casing is inconsistent."""
    if not row:
        return default
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    lowered = {str(key).lower(): value for key, value in row.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value is not None:
            return value
    return default


def first_nonempty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def customer_lookup(customers: Dict[Any, dict], *keys: Any) -> dict:
    for key in keys:
        if key is None:
            continue
        for normalized in (key, str(key), str(key).strip()):
            if normalized in customers:
                return customers[normalized]
    return {}


def lims_base_data_url() -> str:
    from config.settings import settings

    base = settings.LIMS_API_URL.rstrip("/")
    path = settings.LIMS_BASE_DATA_PATH.strip("/")
    return f"{base}/{path}/"


def _chunked(values: List[str], size: int = 50) -> Iterable[List[str]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def fetch_lims_base_data(project_codes: List[str]) -> Tuple[Dict[str, List[dict]], dict]:
    """Fetch LIMS project data using POST /unionLims/base_data/ with projectCode body."""
    if not project_codes:
        return {}, {"available": True, "requests": 0, "records": 0, "errors": 0}

    import httpx
    from config.settings import settings

    records_by_code: Dict[str, List[dict]] = defaultdict(list)
    stats = {"available": False, "requests": 0, "records": 0, "errors": 0}
    url = lims_base_data_url()
    timeout = settings.LIMS_API_TIMEOUT
    try:
        with httpx.Client(timeout=timeout) as client:
            for batch in _chunked(project_codes):
                stats["requests"] += 1
                try:
                    response = client.post(
                        url,
                        json=[{"projectCode": code} for code in batch],
                        headers={"Accept": "application/json"},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if payload.get("status") is False:
                        stats["errors"] += 1
                        logger.warning("lims.base_data.status_false message={}", payload.get("message"))
                        continue
                    for item in payload.get("data") or []:
                        code = str(item.get("projectCode") or "").upper()
                        if code:
                            records_by_code[code].append(item)
                            stats["records"] += 1
                    stats["available"] = True
                except Exception as exc:
                    stats["errors"] += 1
                    logger.warning("lims.base_data.batch_failed size={} error={}", len(batch), exc)
    except Exception as exc:
        logger.warning("lims.base_data.unavailable url={} error={}", url, exc)
    return dict(records_by_code), stats


def normalize_lims_api_record(item: dict, code: str) -> dict:
    members = parse_members(item.get("members"))
    return {
        "project_code": item.get("projectCode") or code,
        "customer_name": item.get("customerName") or "",
        "key_account": normalize_key_account(item.get("keyAccount"), item.get("customerName") or ""),
        "region": item.get("orgName") or "",
        "sales_person": item.get("saleName") or item.get("assignmentUser") or "",
        "product_name": item.get("productName") or "",
        "category_l1": item.get("productBigSortOne") or "未分类",
        "category_l2": item.get("productBigSortTwo") or "",
        "category_l3": item.get("productBigSortThree") or "",
        "work_unit": item.get("workUnit") or "",
        "raw_aftersaler": item.get("afterSaler") or "",
        "lims_members": members,
        "group_id": item.get("groupId") or "",
        "active_day": extract_lims_active_day(item),
        "start_time": item.get("startTime") or "",
        "end_time": item.get("endTime") or "",
        "dimension_source": "lims_base_data_api",
    }


def _dimensions_from_lims_api(
    group_codes: Dict[str, List[str]],
    records_by_code: Dict[str, List[dict]],
    stats: dict,
) -> Tuple[Dict[str, dict], dict]:
    dimensions: Dict[str, dict] = {}
    aftersaler_group_count = product_project_count = matched_product_count = 0
    groups_with_lims_link = groups_with_region = groups_with_key_account = 0
    groups_with_raw_aftersaler = groups_with_lims_members = 0

    for group_name, codes_for_group in group_codes.items():
        projects = []
        regions = set()
        aftersalers = set()
        raw_aftersalers = set()
        has_lims_link = has_region = has_key_account = False
        has_raw_aftersaler = has_lims_members = False

        for code in codes_for_group:
            for raw in records_by_code.get(code.upper(), []):
                item = normalize_lims_api_record(raw, code)
                has_lims_link = True
                if item["product_name"]:
                    product_project_count += 1
                if item["category_l1"] != "未分类":
                    matched_product_count += 1
                if item["region"]:
                    regions.add(item["region"])
                    has_region = True
                if item["key_account"]:
                    has_key_account = True
                raw_after = item["raw_aftersaler"]
                if raw_after:
                    raw_aftersalers.add(raw_after)
                    aftersalers.add(raw_after)
                    has_raw_aftersaler = True
                lims_members = set(item["lims_members"])
                if lims_members:
                    has_lims_members = True
                projects.append(item)

        if aftersalers:
            aftersaler_group_count += 1
        groups_with_lims_link += 1 if has_lims_link else 0
        groups_with_region += 1 if has_region else 0
        groups_with_key_account += 1 if has_key_account else 0
        groups_with_raw_aftersaler += 1 if has_raw_aftersaler else 0
        groups_with_lims_members += 1 if has_lims_members else 0
        dimensions[group_name] = {
            "codes": codes_for_group,
            "projects": projects,
            "regions": sorted(regions),
            "aftersalers": sorted(aftersalers),
            "tentative_aftersalers": [],
            "raw_aftersalers": sorted(raw_aftersalers),
            "chat_members": [],
        }

    requested_codes = sorted({code for codes in group_codes.values() for code in codes})
    matched_codes = sum(1 for code in requested_codes if records_by_code.get(code.upper()))
    groups_without_project_code = sum(1 for codes in group_codes.values() if not codes)
    matched_code_set = {code.upper() for code in requested_codes if records_by_code.get(code.upper())}
    quality = {
        "project_codes": len(requested_codes),
        "matched_project_codes": matched_codes,
        "unmatched_project_codes": len(requested_codes) - matched_codes,
        "groups_with_project_code": len(group_codes) - groups_without_project_code,
        "groups_without_project_code": groups_without_project_code,
        "groups_without_lims_link": sum(
            1 for codes in group_codes.values()
            if codes and not any(code.upper() in matched_code_set for code in codes)
        ),
        "product_projects": product_project_count,
        "matched_products": matched_product_count,
        "groups_with_aftersaler": aftersaler_group_count,
        "groups_with_confirmed_aftersaler": aftersaler_group_count,
        "groups_with_lims_link": groups_with_lims_link,
        "groups_with_region": groups_with_region,
        "groups_with_key_account": groups_with_key_account,
        "groups_with_raw_aftersaler": groups_with_raw_aftersaler,
        "groups_with_lims_members": groups_with_lims_members,
        "lims_source": "base_data_api",
        "lims_api_requests": stats.get("requests", 0),
        "lims_api_records": stats.get("records", 0),
        "lims_api_errors": stats.get("errors", 0),
    }
    return dimensions, quality


def _quality_from_dimensions(dimensions: Dict[str, dict], base_quality: dict) -> dict:
    project_codes = {
        str(code).upper()
        for dim in dimensions.values()
        for code in dim.get("codes", [])
        if code
    }
    matched_codes = {
        str(project.get("project_code") or "").upper()
        for dim in dimensions.values()
        for project in dim.get("projects", [])
        if project.get("project_code")
    }
    return {
        **base_quality,
        "project_codes": len(project_codes),
        "matched_project_codes": len(project_codes & matched_codes),
        "unmatched_project_codes": len(project_codes - matched_codes),
        "groups_with_project_code": sum(1 for dim in dimensions.values() if dim.get("codes")),
        "groups_without_project_code": sum(1 for dim in dimensions.values() if not dim.get("codes")),
        "groups_without_lims_link": sum(1 for dim in dimensions.values() if dim.get("codes") and not dim.get("projects")),
        "product_projects": sum(
            1 for dim in dimensions.values() for project in dim.get("projects", [])
            if project.get("product_name")
        ),
        "matched_products": sum(
            1 for dim in dimensions.values() for project in dim.get("projects", [])
            if project.get("category_l2") in ALLOWED_PRODUCT_L2
        ),
        "groups_with_aftersaler": sum(1 for dim in dimensions.values() if dim.get("aftersalers")),
        "groups_with_confirmed_aftersaler": sum(1 for dim in dimensions.values() if dim.get("aftersalers")),
        "groups_with_lims_link": sum(1 for dim in dimensions.values() if dim.get("projects")),
        "groups_with_region": sum(1 for dim in dimensions.values() if dim.get("regions")),
        "groups_with_key_account": sum(
            1 for dim in dimensions.values()
            if any(project.get("key_account") for project in dim.get("projects", []))
        ),
        "groups_with_raw_aftersaler": sum(1 for dim in dimensions.values() if dim.get("raw_aftersalers")),
        "groups_with_lims_members": sum(1 for dim in dimensions.values() if dim.get("chat_members")),
    }


def _emotion_total(mapping: Dict[str, int], labels: Iterable[str]) -> int:
    total = 0
    for key, count in mapping.items():
        if any(label in key for label in labels):
            total += count
    return total


def normalize_key_account(value: Any, customer_name: str = "", fallback: str = "") -> str:
    raw = str(value or "").strip()
    lowered = raw.lower()
    if lowered in ("", "0", "false", "no", "null", "none", "否", "无"):
        return str(fallback or "").strip()
    if lowered in ("1", "true", "yes", "是", "有"):
        return str(fallback or customer_name or "").strip()
    return raw


def resolve_period(
    period: str = "month",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    today: Optional[date] = None,
) -> Tuple[date, date, str]:
    current = today or date.today()
    period = (period or "month").lower()
    if period == "custom":
        if not start_date or not end_date:
            raise ValueError("custom period requires start_date and end_date")
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    elif period in ("today", "daily"):
        start = end = current
        period = "today"
    elif period in ("week", "weekly"):
        start = current - timedelta(days=current.weekday())
        end = current
        period = "week"
    elif period in ("quarter", "quarterly"):
        month = ((current.month - 1) // 3) * 3 + 1
        start = date(current.year, month, 1)
        end = current
        period = "quarter"
    elif period in ("year", "yearly"):
        start = date(current.year, 1, 1)
        end = current
        period = "year"
    else:
        start = date(current.year, current.month, 1)
        end = current
        period = "month"
    if start > end:
        raise ValueError("start_date must not be later than end_date")
    if (end - start).days > 730:
        raise ValueError("date range cannot exceed 731 days")
    return start, end, period


def _latest_rows(conn, start: date, end: date) -> Tuple[List[dict], int]:
    params = (start.isoformat(), (end + timedelta(days=1)).isoformat())
    sql = """
        SELECT a.*
        FROM qx_analysis_result a
        JOIN (
            SELECT groupName, DATE(CREATEDTIME) analysis_date, MAX(id) latest_id
            FROM qx_analysis_result
            WHERE CREATEDTIME >= %s AND CREATEDTIME < %s
            GROUP BY groupName, DATE(CREATEDTIME)
        ) latest ON latest.latest_id = a.id
        ORDER BY a.CREATEDTIME
    """
    rows = [
        row for row in _query(conn, "analysis.latest_daily", sql, params)
        if is_focus_group_name(row.get("groupName", ""))
    ]
    raw_count_rows = _query(
        conn,
        "analysis.raw_count",
        "SELECT groupName FROM qx_analysis_result WHERE CREATEDTIME >= %s AND CREATEDTIME < %s",
        params,
    )
    raw_count = sum(1 for row in raw_count_rows if is_focus_group_name(row.get("groupName", "")))
    return rows, raw_count


def _load_dimensions(conn, rows: List[dict]) -> Tuple[Dict[str, dict], dict]:
    group_codes = {row["groupName"]: extract_project_codes(row.get("groupName", "")) for row in rows}
    codes = sorted({code for values in group_codes.values() for code in values})
    if not codes:
        groups_without_project_code = len(group_codes)
        return {name: {"codes": [], "projects": [], "regions": [], "aftersalers": []}
                for name in group_codes}, {
                    "project_codes": 0, "matched_project_codes": 0,
                    "unmatched_project_codes": 0,
                    "groups_with_project_code": 0,
                    "groups_without_project_code": groups_without_project_code,
                    "groups_without_lims_link": 0,
                    "product_projects": 0, "matched_products": 0,
                    "groups_with_aftersaler": 0,
                    "groups_with_confirmed_aftersaler": 0,
                    "groups_with_lims_link": 0,
                    "groups_with_region": 0,
                    "groups_with_key_account": 0,
                    "groups_with_raw_aftersaler": 0,
                    "groups_with_lims_members": 0,
                    "lims_source": "none",
                }

    lims_records_by_code, lims_stats = fetch_lims_base_data(codes)
    if lims_stats.get("available") and lims_records_by_code:
        return _dimensions_from_lims_api(group_codes, lims_records_by_code, lims_stats)

    logger.warning(
        "dashboard.dimension.lims_unavailable_no_business_fallback requested_codes={} lims_available={} lims_records={} lims_errors={}",
        len(codes),
        lims_stats.get("available"),
        lims_stats.get("records"),
        lims_stats.get("errors"),
    )
    dimensions: Dict[str, dict] = {
        group_name: {
            "codes": codes_for_group,
            "projects": [],
            "regions": [],
            "aftersalers": [],
            "tentative_aftersalers": [],
            "raw_aftersalers": [],
            "chat_members": parse_members(row.get("member")) if (row := next((r for r in rows if r.get("groupName") == group_name), None)) else [],
            "dimension_source": "lims_unavailable",
        }
        for group_name, codes_for_group in group_codes.items()
    }
    quality = {
        "project_codes": len(codes),
        "matched_project_codes": 0,
        "unmatched_project_codes": len(codes),
        "groups_with_project_code": sum(1 for codes_for_group in group_codes.values() if codes_for_group),
        "groups_without_project_code": sum(1 for codes_for_group in group_codes.values() if not codes_for_group),
        "groups_without_lims_link": sum(1 for codes_for_group in group_codes.values() if codes_for_group),
        "product_projects": 0,
        "matched_products": 0,
        "groups_with_aftersaler": 0,
        "groups_with_confirmed_aftersaler": 0,
        "groups_with_lims_link": 0,
        "groups_with_region": 0,
        "groups_with_key_account": 0,
        "groups_with_raw_aftersaler": 0,
        "groups_with_lims_members": 0,
        "lims_source": "base_data_api_unavailable",
        "lims_api_requests": lims_stats.get("requests", 0),
        "lims_api_records": lims_stats.get("records", 0),
        "lims_api_errors": lims_stats.get("errors", 0),
    }
    return dimensions, quality


def _active_durations(conn, group_names: List[str], start: date, end: date) -> Dict[str, int]:
    if not group_names:
        return {}
    placeholders = ",".join(["%s"] * len(group_names))
    rows = _query(
        conn,
        "analysis.active_duration",
        f"""SELECT groupName, MIN(DATE(CREATEDTIME)) first_date,
                   MAX(DATE(CREATEDTIME)) last_date
            FROM qx_analysis_result
            WHERE groupName IN ({placeholders})
              AND CREATEDTIME >= %s AND CREATEDTIME < %s
            GROUP BY groupName""",
        group_names + [start.isoformat(), (end + timedelta(days=1)).isoformat()],
    )
    return {
        row["groupName"]: (row["last_date"] - row["first_date"]).days
        for row in rows if row.get("first_date") and row.get("last_date")
    }


def _active_durations_from_lims(dimensions: Dict[str, dict]) -> Dict[str, int]:
    durations: Dict[str, int] = {}
    for group_name, dim in dimensions.items():
        values = [
            parse_int(project.get("active_day"))
            for project in dim.get("projects", [])
            if parse_int(project.get("active_day")) is not None
        ]
        if values:
            durations[group_name] = max(values)
    return durations


def _group_aggregates(rows: List[dict], dimensions: Dict[str, dict]) -> Dict[str, dict]:
    groups: Dict[str, dict] = {}
    for row in rows:
        name = row["groupName"]
        item = groups.setdefault(name, {
            "group_name": name, "messages": 0, "dates": set(), "missed_days": 0,
            "customer_good": 0, "customer_bad": 0, "employee_positive": 0,
            "employee_negative": 0, "high_freq": Counter(), "rows": [],
            "dimension": dimensions.get(name, {}),
        })
        item["messages"] += int(row.get("messageToDayCount") or 0)
        item["dates"].add(str(row.get("CREATEDTIME"))[:10])
        item["missed_days"] += 1 if str(row.get("isMissedMessage")) == "1" else 0
        customer = parse_emotion_field(row.get("customerEmotionAnalysis"))
        employee = parse_emotion_field(row.get("saleEmotionAnalysis"))
        item["customer_good"] += _emotion_total(customer, ("好评", "正向", "满意"))
        item["customer_bad"] += _emotion_total(customer, ("差评", "负向", "不满"))
        item["employee_positive"] += _emotion_total(employee, ("积极", "正向"))
        item["employee_negative"] += _emotion_total(employee, ("恶劣", "负向", "消极"))
        for word in parse_high_freq(row.get("highFrequencyWords")):
            item["high_freq"][word["word"]] += word["count"]
        item["rows"].append(row)
    return groups


def _ratio(value: int, total: int) -> float:
    return round(value / total * 100, 1) if total else 0.0


def _counter_items(counter: Counter, label: str = "name") -> List[dict]:
    total = sum(counter.values())
    return [
        {label: name, "count": count, "percentage": _ratio(count, total)}
        for name, count in counter.most_common()
    ]


def _dimension_matches(
    dimension: dict,
    region: str = "",
    aftersaler: str = "",
    category: str = "",
    key_account: str = "",
) -> bool:
    projects = dimension.get("projects", [])
    return not (
        (region and region not in dimension.get("regions", []))
        or (aftersaler and aftersaler not in dimension.get("aftersalers", []))
        or (category and not any(project.get("category_l2") == category for project in projects))
        or (key_account and not any(project.get("key_account") == key_account for project in projects))
    )



def _time_period_breakdown(groups, dimensions, chat_rows_by_group: Optional[Dict[str, List[dict]]] = None):
    """按售后员统计真实消息时间所在时段的消息数量和群聊数量。"""
    aftersaler_stats = {}
    chat_rows_by_group = chat_rows_by_group or {}
    for group_name, item in groups.items():
        dim = dimensions.get(group_name, item.get("dimension", {}))
        aftersalers = dim.get("aftersalers", []) or ["未关联售后"]
        messages = chat_rows_by_group.get(group_name, [])
        if not messages:
            continue
        buckets = {"morning": 0, "afternoon": 0, "after_hours": 0, "weekend": 0}
        for message in messages:
            msg_time = chat_msgtime(message)
            if not msg_time:
                continue
            minutes = msg_time.hour * 60 + msg_time.minute
            if msg_time.weekday() >= 5:
                buckets["weekend"] += 1
            elif 8 * 60 + 30 <= minutes < 12 * 60:
                buckets["morning"] += 1
            elif 12 * 60 <= minutes < 17 * 60 + 30:
                buckets["afternoon"] += 1
            else:
                buckets["after_hours"] += 1
        total_msgs = sum(buckets.values())
        if total_msgs == 0:
            continue
        for person in aftersalers:
            s = aftersaler_stats.setdefault(person, {
                "aftersaler": person, "groups": set(),
                "morning": 0, "afternoon": 0, "after_hours": 0, "weekend": 0,
                "total": 0,
            })
            s["groups"].add(group_name)
            s["morning"] += buckets["morning"]
            s["afternoon"] += buckets["afternoon"]
            s["after_hours"] += buckets["after_hours"]
            s["weekend"] += buckets["weekend"]
            s["total"] += total_msgs
    items = []
    for person, s in aftersaler_stats.items():
        total = s["total"]
        items.append({
            "aftersaler": person,
            "group_count": len(s["groups"]),
            "morning": {"count": s["morning"], "percentage": round(s["morning"] / total * 100, 1) if total else 0},
            "afternoon": {"count": s["afternoon"], "percentage": round(s["afternoon"] / total * 100, 1) if total else 0},
            "after_hours": {"count": s["after_hours"], "percentage": round(s["after_hours"] / total * 100, 1) if total else 0},
            "weekend": {"count": s["weekend"], "percentage": round(s["weekend"] / total * 100, 1) if total else 0},
            "total": total,
        })
    items.sort(key=lambda x: -x["total"])
    all_groups = set()
    for s in aftersaler_stats.values():
        all_groups.update(s["groups"])
    return {
        "items": items,
        "total_aftersalers": len(items),
        "total_groups": len(all_groups),
        "workday_morning": "08:30-12:00",
        "workday_afternoon": "12:00-17:30",
        "after_hours": "工作日 17:30-次日08:30，不包含周末",
        "weekend": "周六、周日全天单独统计",
    }


def _filter_chat_rows_by_period(chat_rows: List[dict], start: date, end: date) -> List[dict]:
    filtered = []
    for row in chat_rows:
        msg_time = chat_msgtime(row)
        if msg_time and start <= msg_time.date() <= end:
            filtered.append(row)
    return filtered


def _chat_rows_by_group_name(group_rows: List[dict], chat_rows: List[dict]) -> Dict[str, List[dict]]:
    room_to_group = {
        str(row.get("chat_id") or "").strip(): str(row.get("name") or "").strip()
        for row in group_rows
        if str(row.get("chat_id") or "").strip() and str(row.get("name") or "").strip()
    }
    result: Dict[str, List[dict]] = defaultdict(list)
    for row in chat_rows:
        group_name = room_to_group.get(str(row.get("roomid") or "").strip())
        if group_name:
            result[group_name].append(row)
    return result


def _build_overview(
    start: date,
    end: date,
    period: str,
    region: str = "",
    aftersaler: str = "",
    category: str = "",
    key_account: str = "",
) -> dict:
    with database("dashboard.overview") as conn:
        rows, raw_count = _latest_rows(conn, start, end)
        dimensions, quality = _load_dimensions(conn, rows)
        filter_options = {
            "regions": sorted({value for dim in dimensions.values() for value in dim.get("regions", [])}),
            "aftersalers": sorted({value for dim in dimensions.values() for value in dim.get("aftersalers", [])}),
            "categories": sorted({p.get("category_l2") for dim in dimensions.values() for p in dim.get("projects", []) if p.get("category_l2") in ALLOWED_PRODUCT_L2}),
            "key_accounts": sorted({p.get("key_account") for dim in dimensions.values() for p in dim.get("projects", []) if p.get("key_account")}),
        }
        allowed_groups = set()
        for group_name, dim in dimensions.items():
            if _dimension_matches(dim, region, aftersaler, category, key_account):
                allowed_groups.add(group_name)
        if region or aftersaler or category or key_account:
            rows = [row for row in rows if row["groupName"] in allowed_groups]
            dimensions = {name: value for name, value in dimensions.items() if name in allowed_groups}
        quality = _quality_from_dimensions(dimensions, quality)
        groups = _group_aggregates(rows, dimensions)
        durations = _active_durations_from_lims(dimensions)
        missing_duration_groups = [name for name in groups if name not in durations]
        fallback_durations = _active_durations(conn, missing_duration_groups, start, end)
        durations.update(fallback_durations)
        scoped_group_names = sorted({row.get("groupName") for row in rows if row.get("groupName")})
        group_rows = _query_group_rows(conn, scoped_group_names)
        chat_rows = _filter_chat_rows_by_period(_query_chat_rows(conn, group_rows), start, end)
        chat_rows_by_group = _chat_rows_by_group_name(group_rows, chat_rows)

    _filter_region = region
    _filter_aftersaler = aftersaler
    _filter_category = category
    _filter_key_account = key_account
    total_groups = len(groups)
    total_messages = sum(item["messages"] for item in groups.values())
    missed_groups = sum(1 for item in groups.values() if item["missed_days"])
    daily: Dict[str, dict] = defaultdict(lambda: {"messages": 0, "groups": set(), "missed": 0})
    customer_good = customer_bad = employee_positive = employee_negative = 0
    words = Counter()
    regions = Counter()
    region_messages = Counter()
    region_group_count: Dict[str, int] = {}
    aftersalers = Counter()
    aftersaler_messages = Counter()
    aftersaler_projects: Dict[str, set] = defaultdict(set)
    tentative_aftersalers = Counter()
    categories = Counter()
    category_groups: Dict[str, set] = defaultdict(set)
    category_messages = Counter()
    category_projects: Dict[str, set] = defaultdict(set)
    product_tree = defaultdict(lambda: defaultdict(lambda: {"projects": set(), "groups": set(), "messages": 0}))
    region_sales = defaultdict(Counter)
    region_after = defaultdict(Counter)
    region_product = defaultdict(Counter)
    key_accounts: Dict[str, dict] = {}

    for row in rows:
        day = str(row.get("CREATEDTIME"))[:10]
        daily[day]["messages"] += int(row.get("messageToDayCount") or 0)
        daily[day]["groups"].add(row["groupName"])
        daily[day]["missed"] += 1 if str(row.get("isMissedMessage")) == "1" else 0

    for group_name, item in groups.items():
        customer_good += item["customer_good"]
        customer_bad += item["customer_bad"]
        employee_positive += item["employee_positive"]
        employee_negative += item["employee_negative"]
        words.update(item["high_freq"])
        dim = item["dimension"]
        project_codes_for_group = {
            project.get("project_code")
            for project in dim.get("projects", [])
            if project.get("project_code")
        }
        for person in dim.get("aftersalers", []):
            aftersalers[person] += 1
            aftersaler_messages[person] += item["messages"]
            aftersaler_projects[person].update(project_codes_for_group)
        for person in dim.get("tentative_aftersalers", []):
            tentative_aftersalers[person] += 1
        group_regions = set(dim.get("regions", [])) or {"未关联区域"}
        for region in group_regions:
            regions[region] += 1
            region_group_count[region] = region_group_count.get(region, 0) + 1
            region_messages[region] += item["messages"]
        seen_pairs = set()
        for project in dim.get("projects", []):
            region = project.get("region") or "未关联区域"
            category = project.get("category_l2") or ""
            if category in ALLOWED_PRODUCT_L2:
                pair = (project.get("project_code"), region, category)
                if pair not in seen_pairs:
                    categories[category] += 1
                    category_projects[category].add(project.get("project_code"))
                    category_groups[category].add(group_name)
                    category_messages[category] += item["messages"]
                    region_product[region][category] += 1
                    level3 = project.get("category_l3") or "未细分"
                    leaf = product_tree[category][level3]
                    leaf["projects"].add(project.get("project_code"))
                    leaf["groups"].add(group_name)
                    leaf["messages"] += item["messages"]
                    seen_pairs.add(pair)
            sales = project.get("sales_person") or "未分配销售"
            region_sales[region][sales] += 1
            for after in dim.get("aftersalers", []) or ["未关联售后"]:
                region_after[region][after] += 1
            key = project.get("key_account")
            if key:
                account = key_accounts.setdefault(key, {
                    "key_account": key, "projects": set(), "customers": set(), "aftersalers": set(),
                    "groups": set(), "messages": 0, "work_units": set(), "category_l3": set(),
                    "high_freq": Counter(), "active_days": [],
                })
                account["projects"].add(project.get("project_code"))
                if project.get("customer_name"):
                    account["customers"].add(project["customer_name"])
                account["aftersalers"].update(dim.get("aftersalers", []))
                if group_name not in account["groups"]:
                    account["messages"] += item["messages"]
                    account["high_freq"].update(item["high_freq"])
                account["groups"].add(group_name)
                if project.get("work_unit"):
                    account["work_units"].add(project["work_unit"])
                if project.get("category_l3"):
                    account["category_l3"].add(project["category_l3"])
                if parse_int(project.get("active_day")) is not None:
                    account["active_days"].append(parse_int(project.get("active_day")))
    if _filter_aftersaler:
        aftersalers = Counter({_filter_aftersaler: aftersalers.get(_filter_aftersaler, 0)})
    if _filter_region:
        regions = Counter({_filter_region: regions.get(_filter_region, 0)})
        region_group_count = {_filter_region: region_group_count.get(_filter_region, 0)}
    if _filter_category:
        categories = Counter({_filter_category: categories.get(_filter_category, 0)})

    duration_defs = [
        ("≤7天", "极短期咨询", 0, 7), ("8-30天", "短期服务", 8, 30),
        ("1-3个月", "常规项目周期", 31, 90), ("3-6个月", "中长期项目", 91, 180),
        ("6-12个月", "长期服务", 181, 365), (">12个月", "超长期合作", 366, 10**9),
    ]
    duration_items = []
    for range_name, label, low, high in duration_defs:
        count = sum(1 for value in durations.values() if low <= value <= high)
        duration_items.append({"range": range_name, "label": label, "count": count, "percentage": _ratio(count, len(durations))})

    total_region_groups = sum(regions.values())
    region_items = []
    for region, count in regions.most_common():
        region_items.append({
            "region": region, "group_count": count,
            "message_count": region_messages[region],
            "percentage": _ratio(count, total_region_groups),
        })

    top5_coverage = _ratio(sum(x[1] for x in regions.most_common(5)), total_region_groups)
    account_items = []
    for value in key_accounts.values():
        customer_names = sorted(value.get("customers", set()))
        work_units = sorted(value.get("work_units", set()))
        account_items.append({
            "key_account": value["key_account"],
            "work_unit": work_units[0] if work_units else "",
            "work_units": work_units[:5],
            "customer_name": customer_names[0] if customer_names else "",
            "customer_names": customer_names[:5],
            "group_count": len(value.get("groups", set())),
            "project_count": len(value["projects"]),
            "message_count": value.get("messages", 0),
            "customer_count": len(value["customers"]),
            "aftersalers": sorted(value["aftersalers"]),
            "category_l3": sorted(value.get("category_l3", set()))[:8],
            "high_frequency_top5": [
                {"word": word, "count": count}
                for word, count in value.get("high_freq", Counter()).most_common(5)
            ],
            "active_day": max(value.get("active_days", []) or [0]),
        })
    account_items.sort(key=lambda value: (-value["group_count"], -value["project_count"], value["key_account"]))
    product_hierarchy = []
    for level2, level3_map in product_tree.items():
        children = []
        for level3, stats in level3_map.items():
            children.append({
                "name": level3,
                "project_count": len(stats["projects"]),
                "group_count": len(stats["groups"]),
                "message_count": stats["messages"],
                "count": len(stats["projects"]),
            })
        children.sort(key=lambda value: (-value["project_count"], value["name"]))
        product_hierarchy.append({
            "name": level2,
            "project_count": len(category_projects[level2]),
            "group_count": len(category_groups[level2]),
            "message_count": category_messages[level2],
            "count": len(category_projects[level2]),
            "children": children,
        })
    product_hierarchy.sort(key=lambda value: (-value["project_count"], value["name"]))

    matched_codes = quality["matched_project_codes"]
    project_codes = quality["project_codes"]
    matched_products = quality["matched_products"]
    product_projects = quality["product_projects"]
    aftersaler_groups = quality.get("groups_with_aftersaler", quality.get("groups_with_confirmed_aftersaler", 0))
    cross_sales = [{"region": region, "group_count": region_group_count.get(region, 0), "items": _counter_items(counter)} for region, counter in region_sales.items()]
    cross_after = [{"region": region, "group_count": region_group_count.get(region, 0), "items": _counter_items(counter)} for region, counter in region_after.items()]
    cross_product = [{"region": region, "group_count": region_group_count.get(region, 0), "items": _counter_items(counter, "category")} for region, counter in region_product.items()]

    return {
        "meta": {
            "period": period, "start_date": start.isoformat(), "end_date": end.isoformat(),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "source_min_date": min((str(row["CREATEDTIME"])[:10] for row in rows), default=None),
            "source_max_date": max((str(row["CREATEDTIME"])[:10] for row in rows), default=None),
            "filters": {"region": region, "aftersaler": aftersaler, "category": category, "key_account": key_account},
            "filter_options": filter_options,
        },
        "summary": {
            "total_groups": total_groups, "total_messages": total_messages,
            "project_groups": sum(1 for item in groups.values() if item["dimension"].get("codes")),
            "regions": len([name for name in regions if name != "未关联区域"]),
            "aftersaler_count": len(aftersalers),
            "confirmed_aftersalers": len(aftersalers), "product_categories": len(categories),
            "key_accounts": len(key_accounts),
            "short_active_ratio": _ratio(sum(1 for value in durations.values() if value <= 30), len(durations)),
        },
        "service_quality": {
            "unanswered": {"total_groups": total_groups, "missed_groups": missed_groups, "answered_groups": total_groups - missed_groups, "missed_rate": _ratio(missed_groups, total_groups)},
            "sentiment": {"customer_good": customer_good, "customer_bad": customer_bad, "employee_positive": employee_positive, "employee_negative": employee_negative},
        },
        "communication": {
            "trend": [{"date": day, "messages": value["messages"], "groups": len(value["groups"]), "missed": value["missed"]} for day, value in sorted(daily.items())],
            "high_frequency": [{"word": word, "count": count} for word, count in words.most_common(20)],
            "active_duration": duration_items,
            "time_period_breakdown": _time_period_breakdown(groups, dimensions, chat_rows_by_group),
        },
        "business": {
            "aftersalers": [
                {
                    "name": name,
                    "count": count,
                    "group_count": count,
                    "project_count": len(aftersaler_projects[name]),
                    "message_count": aftersaler_messages[name],
                    "percentage": _ratio(count, sum(aftersalers.values())),
                }
                for name, count in aftersalers.most_common()
            ],
            "tentative_aftersalers": _counter_items(tentative_aftersalers),
            "regions": region_items, "top5_coverage": top5_coverage,
            "product_categories": [
                {
                    "category": name,
                    "count": len(category_projects[name]),
                    "project_count": len(category_projects[name]),
                    "group_count": len(category_groups[name]),
                    "message_count": category_messages[name],
                    "percentage": _ratio(len(category_projects[name]), sum(len(v) for v in category_projects.values())),
                }
                for name, _ in categories.most_common()
            ],
            "product_hierarchy": product_hierarchy,
            "key_accounts": account_items,
        },
        "cross_analysis": {
            "region_sales": sorted(cross_sales, key=lambda x: -x.get("group_count", 0)),
            "region_after": sorted(cross_after, key=lambda x: -x.get("group_count", 0)),
            "region_product": sorted(cross_product, key=lambda x: -x.get("group_count", 0)),
        },
        "data_quality": {
            "raw_rows": raw_count, "deduplicated_rows": len(rows),
            "duplicate_rows_removed": max(0, raw_count - len(rows)),
            "project_codes": project_codes, "matched_project_codes": matched_codes,
            "unmatched_project_codes": quality.get("unmatched_project_codes", max(0, project_codes - matched_codes)),
            "groups_with_project_code": quality.get("groups_with_project_code", sum(1 for item in groups.values() if item["dimension"].get("codes"))),
            "groups_without_project_code": quality.get("groups_without_project_code", total_groups - sum(1 for item in groups.values() if item["dimension"].get("codes"))),
            "groups_without_lims_link": quality.get("groups_without_lims_link", 0),
            "project_match_rate": _ratio(matched_codes, project_codes),
            "product_records": product_projects, "matched_products": matched_products,
            "product_match_rate": _ratio(matched_products, product_projects),
            "groups_with_aftersaler": aftersaler_groups,
            "aftersaler_coverage_rate": _ratio(aftersaler_groups, total_groups),
            "groups_with_confirmed_aftersaler": aftersaler_groups,
            "aftersaler_confirmation_rate": _ratio(aftersaler_groups, total_groups),
            "groups_with_lims_link": quality.get("groups_with_lims_link", 0),
            "lims_link_rate": _ratio(quality.get("groups_with_lims_link", 0), total_groups),
            "groups_with_region": quality.get("groups_with_region", 0),
            "region_link_rate": _ratio(quality.get("groups_with_region", 0), total_groups),
            "groups_with_key_account": quality.get("groups_with_key_account", 0),
            "key_account_link_rate": _ratio(quality.get("groups_with_key_account", 0), total_groups),
            "groups_with_raw_aftersaler": quality.get("groups_with_raw_aftersaler", 0),
            "groups_with_lims_members": quality.get("groups_with_lims_members", 0),
            "active_duration_source": "lims_base_data_active_day",
            "active_duration_lims_groups": len(durations) - len(fallback_durations),
            "active_duration_fallback_groups": len(fallback_durations),
            "lims_source": quality.get("lims_source", "unknown"),
            "lims_api_requests": quality.get("lims_api_requests", 0),
            "lims_api_records": quality.get("lims_api_records", 0),
            "lims_api_errors": quality.get("lims_api_errors", 0),
            "note": "业务维度仅来源于 POST /unionLims/base_data/，请求体 [{projectCode: 项目号}]。售后人员取接口 afterSaler 字段，销售区域取 orgName，重点客户取 keyAccount，活跃周期取 activeDay/activellay；接口不可用时维度标记为未关联，不再读取其他业务表补数。",
        },
    }


def get_overview(
    period: str = "month",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    force_refresh: bool = False,
    region: str = "",
    aftersaler: str = "",
    category: str = "",
    key_account: str = "",
) -> dict:
    start, end, normalized_period = resolve_period(period, start_date, end_date)
    key = f"{start}:{end}:{normalized_period}:{region}:{aftersaler}:{category}:{key_account}"
    now = time.time()
    with _cache_lock:
        cached = _cache.get(key)
        if not force_refresh and cached and now - cached[0] < CACHE_TTL_SECONDS:
            result = copy.deepcopy(cached[1])
            result["meta"]["cache"] = "hit"
            return result
    try:
        result = _build_overview(
            start, end, normalized_period,
            region=region, aftersaler=aftersaler, category=category, key_account=key_account,
        )
        result["meta"]["cache"] = "miss"
        result["meta"]["stale"] = False
        with _cache_lock:
            _cache[key] = (now, copy.deepcopy(result))
            _last_success[key] = copy.deepcopy(result)
        return result
    except Exception as exc:
        logger.exception("dashboard.overview.error key={} error={}", key, exc)
        with _cache_lock:
            stale = _last_success.get(key)
        if stale:
            result = copy.deepcopy(stale)
            result["meta"]["stale"] = True
            result["meta"]["stale_reason"] = type(exc).__name__
            return result
        raise


def get_evidence(
    metric: str,
    period: str = "month",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    keyword: Optional[str] = None,
    search: Optional[str] = None,
    region: str = "",
    aftersaler: str = "",
    category: str = "",
    key_account: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict:
    start, end, normalized_period = resolve_period(period, start_date, end_date)
    with database("dashboard.evidence") as conn:
        rows, _ = _latest_rows(conn, start, end)
        dimensions, _ = _load_dimensions(conn, rows)
        allowed_groups = [
            row.get("groupName") for row in rows
            if row.get("groupName") and _dimension_matches(dimensions.get(row.get("groupName"), {}), region, aftersaler, category, key_account)
        ]
        group_rows = _query_group_rows(conn, sorted(set(allowed_groups)))
        chat_rows = _filter_chat_rows_by_period(_query_chat_rows(conn, group_rows), start, end)
        raw_messages = _raw_messages_by_group(group_rows, chat_rows)
    items = []
    metric = metric.lower()
    for row in reversed(rows):
        group_name = row.get("groupName") or ""
        dim = dimensions.get(group_name, {})
        if not _dimension_matches(dim, region, aftersaler, category, key_account):
            continue
        content = ""
        matched = False
        if metric == "unanswered":
            matched = str(row.get("isMissedMessage")) == "1"
            content = row.get("missedMessageList") or ""
            missed_messages = _extract_missed_messages(content)
        elif metric == "customer_negative":
            mapping = parse_emotion_field(row.get("customerEmotionAnalysis"))
            matched = _emotion_total(mapping, ("差评", "负向", "不满")) > 0 or bool(row.get("customerNegativeEmotionInfo"))
            content = row.get("customerNegativeEmotionInfo") or ""
            missed_messages = []
        elif metric == "employee_negative":
            mapping = parse_emotion_field(row.get("saleEmotionAnalysis"))
            matched = _emotion_total(mapping, ("恶劣", "负向", "消极")) > 0 or bool(row.get("saleNegativeEmotionInfo"))
            content = row.get("saleNegativeEmotionInfo") or ""
            missed_messages = []
        elif metric == "highfreq":
            words = parse_high_freq(row.get("highFrequencyWords"))
            matched = bool(words) and (not keyword or any(keyword.lower() in value["word"].lower() for value in words))
            content = row.get("highFrequencyWords") or ""
            missed_messages = []
        else:
            raise ValueError("unsupported evidence metric")
        if not matched:
            continue
        if search and search.lower() not in (group_name + " " + str(content) + " " + str(row.get("member") or "")).lower():
            continue
        group_raw_messages = raw_messages.get(group_name, [])
        display_messages = _evidence_messages(metric, content, keyword or "", missed_messages, group_raw_messages)
        msg_times = [
            item["msgtime"] for item in display_messages if item.get("msgtime")
        ] or [
            item["msgtime"] for item in group_raw_messages if item.get("msgtime")
        ][:1]
        items.append({
            "id": row.get("id"), "group_name": group_name,
            "analysis_date": str(row.get("CREATEDTIME"))[:19].replace("T", " "),
            "msg_times": msg_times,
            "messages": display_messages,
            "members": parse_members(row.get("member")),
            "content": content, "core_summary": row.get("coreInfoSummary") or "",
            "project_codes": dim.get("codes", []), "projects": dim.get("projects", []),
            "aftersalers": dim.get("aftersalers", []),
        })
    total = len(items)
    start_index = (page - 1) * page_size
    return {
        "metric": metric, "period": normalized_period,
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "total": total, "page": page, "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "items": items[start_index:start_index + page_size],
    }


# Compatibility helpers for existing callers.
def get_summary(date_str: Optional[str] = None) -> dict:
    data = get_overview("custom", date_str, date_str) if date_str else get_overview("year")
    return {**data["summary"], "date_range": f"{data['meta']['start_date']} ~ {data['meta']['end_date']}", "missed_groups": data["service_quality"]["unanswered"]["missed_groups"]}


def get_full_summary() -> dict:
    return get_summary()


def get_after_saler_distribution() -> dict:
    data = get_overview("year")
    return {"items": data["business"]["aftersalers"], "total_groups": data["summary"]["total_groups"]}


def get_product_category_hierarchy() -> dict:
    data = get_overview("year")
    return {
        "categories": data["business"]["product_hierarchy"],
        "total_projects": data["data_quality"]["product_records"],
    }


def get_key_account_hierarchy() -> dict:
    data = get_overview("year")
    return {"hierarchy": data["business"]["key_accounts"], "total_key_accounts": data["summary"]["key_accounts"]}


def get_org_distribution() -> dict:
    data = get_overview("year")
    return {"items": data["business"]["regions"], "total_regions": data["summary"]["regions"], "top5_coverage": data["business"]["top5_coverage"]}


def get_sentiment_analysis_summary() -> dict:
    return get_overview("year")["service_quality"]["sentiment"]


def get_high_freq_summary(limit: int = 20) -> dict:
    values = get_overview("year")["communication"]["high_frequency"][:limit]
    return {"top_words": values, "total_unique_words": len(values)}





def _extract_missed_messages(missed_list_json: Any) -> List[dict]:
    """Normalize missedMessageList into displayable original messages."""
    if not missed_list_json:
        return []
    try:
        import json
        items = json.loads(missed_list_json) if isinstance(missed_list_json, str) else missed_list_json
        if isinstance(items, list):
            messages = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                messages.append({
                    "msgid": item.get("msgid") or item.get("id") or "",
                    "sender_name": item.get("sender_name") or item.get("sender") or item.get("from") or "",
                    "content": item.get("content") or item.get("text") or item.get("message") or "",
                    "msgtime": item.get("msgtime") or "",
                })
            return messages
    except Exception:
        logger.debug("dashboard.evidence.missed_messages_parse_failed", exc_info=True)
    return []


def _extract_msg_times(missed_list_json: Any) -> list:
    """Extract original msgtime values from missedMessageList."""
    return [item["msgtime"] for item in _extract_missed_messages(missed_list_json) if item.get("msgtime")]


def _display_chat_message(row: dict) -> dict:
    msg_time = chat_msgtime(row)
    return {
        "msgid": chat_msgid(row),
        "sender_name": chat_sender(row),
        "content": chat_text(row),
        "msgtime": msg_time.strftime("%Y-%m-%d %H:%M:%S") if msg_time else "",
    }


def _raw_messages_by_group(group_rows: List[dict], chat_rows: List[dict]) -> Dict[str, List[dict]]:
    grouped = _chat_rows_by_group_name(group_rows, chat_rows)
    result = {}
    for group_name, rows in grouped.items():
        messages = [_display_chat_message(row) for row in rows]
        messages.sort(key=lambda item: item.get("msgtime") or "")
        result[group_name] = messages
    return result


def _match_raw_message(message: dict, raw_messages: List[dict]) -> dict:
    msgid = str(message.get("msgid") or "").strip()
    content = str(message.get("content") or "").strip()
    for raw in raw_messages:
        if msgid and msgid == str(raw.get("msgid") or "").strip():
            return {**message, **{k: v for k, v in raw.items() if v}}
    if content:
        for raw in raw_messages:
            raw_content = str(raw.get("content") or "")
            if content in raw_content or raw_content in content:
                return {**message, **{k: v for k, v in raw.items() if v}}
    return message


def _evidence_messages(metric: str, content: str, keyword: str, missed_messages: List[dict], raw_messages: List[dict]) -> List[dict]:
    if metric == "unanswered":
        return [_match_raw_message(message, raw_messages) for message in missed_messages]
    if metric == "highfreq" and keyword:
        lowered = keyword.lower()
        return [
            message for message in raw_messages
            if lowered in str(message.get("content") or "").lower()
        ][:10]
    text = str(content or "")
    if metric in ("customer_negative", "employee_negative") and text:
        return [
            message for message in raw_messages
            if message.get("content") and str(message["content"]) in text
        ][:10]
    return []


def _project_code_diagnostics(
    group_names: List[str],
    dimensions: Dict[str, dict],
    lims_records_by_code: Dict[str, List[dict]],
) -> dict:
    group_to_codes = {
        group_name: [
            str(code).strip().upper()
            for code in dimensions.get(group_name, {}).get("codes", [])
            if str(code or "").strip()
        ]
        for group_name in group_names
    }
    matched_codes = {
        str(code).strip().upper()
        for code, records in lims_records_by_code.items()
        if str(code or "").strip() and records
    }
    code_to_groups: Dict[str, List[str]] = defaultdict(list)
    for group_name, codes in group_to_codes.items():
        for code in codes:
            code_to_groups[code].append(group_name)

    requested_codes = sorted(code_to_groups)
    unmatched_codes = [code for code in requested_codes if code not in matched_codes]
    unmatched_set = set(unmatched_codes)
    groups_without_project_code = sorted(
        group_name for group_name, codes in group_to_codes.items() if not codes
    )
    groups_with_unmatched_project_code = sorted(
        group_name
        for group_name, codes in group_to_codes.items()
        if any(code in unmatched_set for code in codes)
    )
    groups_without_lims_link = sorted(
        group_name
        for group_name, codes in group_to_codes.items()
        if codes and not any(code in matched_codes for code in codes)
    )

    return {
        "group_to_codes": group_to_codes,
        "code_to_groups": {code: sorted(groups) for code, groups in code_to_groups.items()},
        "requested_codes": requested_codes,
        "matched_codes": sorted(matched_codes),
        "unmatched_codes": unmatched_codes,
        "groups_without_project_code": groups_without_project_code,
        "groups_with_unmatched_project_code": groups_with_unmatched_project_code,
        "groups_without_lims_link": groups_without_lims_link,
    }


def get_verification_stats(
    period: str = "month",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    region: str = "",
    aftersaler: str = "",
    category: str = "",
    key_account: str = "",
) -> dict:
    """Return scoped verification data for the current dashboard filters."""
    start, end, normalized_period = resolve_period(period, start_date, end_date)

    with database("dashboard.verify") as conn:
        scope = _current_dashboard_scope(conn, start, end, region, aftersaler, category, key_account)
        project_codes = scope["project_codes"]
        latest_rows = scope["latest_rows"]
        raw_rows = scope["raw_rows"]
        dimensions = scope["dimensions"]
        group_rows = _query_group_rows(conn, scope["group_names"])
        chat_rows = _query_chat_rows(conn, group_rows)

    lims_records_by_code, lims_stats = fetch_lims_base_data(project_codes)

    group_names = scope["group_names"]
    dashboard_missed_groups = {
        row.get("groupName")
        for row in latest_rows
        if row.get("groupName") and str(row.get("isMissedMessage")) == "1"
    }
    qx_group_names = {str(row.get("name") or "") for row in group_rows if row.get("name")}
    qx_group_ids = {str(row.get("chat_id") or "") for row in group_rows if row.get("chat_id")}
    qx_chat_room_ids = {str(row.get("roomid") or "") for row in chat_rows if row.get("roomid")}
    qx_chat_with_msgtime = sum(1 for row in chat_rows if row.get("msgtime"))

    diagnostics = _project_code_diagnostics(group_names, dimensions, lims_records_by_code)
    lims_codes = set(lims_records_by_code)
    lims_region_codes = set()
    lims_after_codes = set()
    lims_key_codes = set()
    lims_active_codes = set()
    for code, records in lims_records_by_code.items():
        for record in records:
            if str(_field_value_case_insensitive(record, "orgName") or "").strip():
                lims_region_codes.add(code)
            if str(_field_value_case_insensitive(record, "afterSaler") or "").strip():
                lims_after_codes.add(code)
            if _valid_key_account(_field_value_case_insensitive(record, "keyAccount")):
                lims_key_codes.add(code)
            if extract_lims_active_day(record) is not None:
                lims_active_codes.add(code)

    lims_active_groups = set()
    for group_name, dim in dimensions.items():
        if any(parse_int(project.get("active_day")) is not None for project in dim.get("projects", [])):
            lims_active_groups.add(group_name)

    project_total = len(project_codes)
    lims_unreturned_note = (
        "LIMS接口不可用，本项表示接口未返回，不能直接判定项目号错误。"
        if not lims_stats.get("available")
        else "项目号请求后 LIMS base_data 未返回记录，需核对项目号或 LIMS 数据。"
    )
    missing_group_rows = [
        [group_name, "群名中未匹配 LC-* 项目号"]
        for group_name in diagnostics["groups_without_project_code"]
    ] or [["(无)", "当前范围内所有群名都提取到了项目号"]]
    unmatched_code_rows = [
        [
            code,
            len(diagnostics["code_to_groups"].get(code, [])),
            "；".join(diagnostics["code_to_groups"].get(code, [])),
            lims_unreturned_note,
        ]
        for code in diagnostics["unmatched_codes"]
    ] or [["(无)", 0, "", "当前范围内提取到的项目号均有 LIMS 返回记录"]]
    unlinked_group_rows = [
        [
            group_name,
            "、".join(diagnostics["group_to_codes"].get(group_name, [])),
            "、".join(
                code for code in diagnostics["group_to_codes"].get(group_name, [])
                if code in set(diagnostics["unmatched_codes"])
            ),
            lims_unreturned_note,
        ]
        for group_name in diagnostics["groups_without_lims_link"]
    ] or [["(无)", "", "", "当前范围内带项目号的群均至少命中一个 LIMS 项目"]]
    sections = [
        {
            "title": "当前筛选范围",
            "columns": ["验证项", "范围值", "命中值", "说明"],
            "rows": [
                ["群聊数", len(group_names), len(group_names), "当前周期和筛选条件命中的群聊"],
                ["项目号数", project_total, project_total, "从命中群名提取 LC-* 项目号后去重"],
                ["分析原始记录数", len(raw_rows), len(raw_rows), "qx_analysis_result 当前范围原始记录"],
                ["看板去重记录数", len(latest_rows), len(latest_rows), "按 groupName + DATE(CREATEDTIME) 取最新记录"],
                ["漏回群数", len(group_names), len(dashboard_missed_groups), f"漏回率 {_ratio(len(dashboard_missed_groups), len(group_names))}%"],
            ],
        },
        {
            "title": "项目号与 LIMS 关联诊断",
            "columns": ["验证项", "当前范围", "异常/未命中", "说明"],
            "rows": [
                ["群名项目号提取", len(group_names), len(diagnostics["groups_without_project_code"]), "异常值为无法从群名提取 LC-* 项目号的群"],
                ["LIMS未返回项目号", project_total, len(diagnostics["unmatched_codes"]), lims_unreturned_note],
                ["完全无LIMS关联群", len(group_names), len(diagnostics["groups_without_lims_link"]), "群名能提取项目号，但这些项目号均未拿到 LIMS 返回记录"],
                ["含未返回项目号群", len(group_names), len(diagnostics["groups_with_unmatched_project_code"]), "群名中至少有一个项目号未被 LIMS 返回"],
            ],
        },
        {
            "title": "未提取项目号的群名",
            "columns": ["群名", "说明"],
            "rows": missing_group_rows,
        },
        {
            "title": "LIMS未返回项目号明细",
            "columns": ["项目号", "涉及群数", "涉及群名", "说明"],
            "rows": unmatched_code_rows,
        },
        {
            "title": "完全无LIMS关联的群名",
            "columns": ["群名", "提取项目号", "未返回项目号", "说明"],
            "rows": unlinked_group_rows,
        },
        {
            "title": "数据库群聊数据源覆盖",
            "columns": ["验证项", "当前范围", "命中值", "说明"],
            "rows": [
                ["qx_analysis_result 原始记录", len(group_names), len(raw_rows), "当前周期和筛选条件内的分析结果原始记录"],
                ["qx_group 群信息", len(group_names), len(qx_group_names), "按 qx_analysis_result.groupName = qx_group.name 匹配"],
                ["qx_chat 原始消息", len(qx_group_ids), len(qx_chat_room_ids), "按 qx_group.chat_id = qx_chat.roomid 匹配"],
                ["qx_chat 消息时间", len(chat_rows), qx_chat_with_msgtime, "原始消息 msgtime 非空数量"],
            ],
        },
        {
            "title": "LIMS base_data 接口覆盖",
            "columns": ["验证项", "当前范围项目号", "接口命中项目号", "说明"],
            "rows": [
                ["接口返回项目号", project_total, len(lims_codes), f"请求 {lims_stats.get('requests', 0)} 次，返回记录 {lims_stats.get('records', 0)} 条，错误 {lims_stats.get('errors', 0)} 次"],
                ["区域(orgName)", project_total, len(lims_region_codes), f"覆盖率 {_ratio(len(lims_region_codes), project_total)}%"],
                ["售后(afterSaler)", project_total, len(lims_after_codes), f"覆盖率 {_ratio(len(lims_after_codes), project_total)}%"],
                ["重点客户(keyAccount)", project_total, len(lims_key_codes), f"覆盖率 {_ratio(len(lims_key_codes), project_total)}%"],
                ["活跃周期(activeDay/activellay)", project_total, len(lims_active_codes), f"覆盖群聊 {len(lims_active_groups)} 个"],
            ],
        },
    ]

    return {
        "period": {"start": start.isoformat(), "end": end.isoformat(), "normalized": normalized_period},
        "scope": {
            "groups": len(group_names),
            "project_codes": project_total,
            "groups_without_project_code": len(diagnostics["groups_without_project_code"]),
            "unmatched_project_codes": len(diagnostics["unmatched_codes"]),
            "groups_without_lims_link": len(diagnostics["groups_without_lims_link"]),
            "raw_analysis_rows": len(raw_rows),
            "latest_analysis_rows": len(latest_rows),
        },
        "sections": sections,
        "note": "数据源限定为数据库 qx_analysis_result/qx_chat/qx_group 与 LIMS base_data 接口；不再读取其他业务表补数。",
    }


def get_unanswered_summary() -> dict:
    return get_overview("year")["service_quality"]["unanswered"]


def _query_by_chunks(
    conn,
    operation: str,
    sql_template: str,
    values: List[Any],
    prefix_params: Optional[List[Any]] = None,
    suffix_params: Optional[List[Any]] = None,
    chunk_size: int = 100,
) -> List[dict]:
    if not values:
        return []
    rows: List[dict] = []
    prefix_params = prefix_params or []
    suffix_params = suffix_params or []
    for chunk in _chunked(values, chunk_size):
        placeholders = ",".join(["%s"] * len(chunk))
        rows.extend(_query(conn, operation, sql_template.format(placeholders=placeholders), prefix_params + chunk + suffix_params))
    return rows


def _current_dashboard_scope(
    conn,
    start: date,
    end: date,
    region: str = "",
    aftersaler: str = "",
    category: str = "",
    key_account: str = "",
) -> dict:
    latest_rows, raw_count = _latest_rows(conn, start, end)
    dimensions, quality = _load_dimensions(conn, latest_rows)
    if region or aftersaler or category or key_account:
        allowed_groups = {
            group_name
            for group_name, dim in dimensions.items()
            if _dimension_matches(dim, region, aftersaler, category, key_account)
        }
        latest_rows = [row for row in latest_rows if row.get("groupName") in allowed_groups]
        dimensions = {name: value for name, value in dimensions.items() if name in allowed_groups}

    group_names = sorted({row.get("groupName") for row in latest_rows if row.get("groupName")})
    project_codes = sorted({
        code.upper()
        for dim in dimensions.values()
        for code in dim.get("codes", [])
        if code
    })
    raw_rows = _query_by_chunks(
        conn,
        "scope.analysis_raw",
        """SELECT * FROM qx_analysis_result
           WHERE CREATEDTIME >= %s AND CREATEDTIME < %s
             AND groupName IN ({placeholders})
           ORDER BY CREATEDTIME""",
        group_names,
        prefix_params=[start.isoformat(), (end + timedelta(days=1)).isoformat()],
    ) if group_names else []
    return {
        "latest_rows": latest_rows,
        "raw_rows": raw_rows,
        "raw_count_before_filter": raw_count,
        "dimensions": dimensions,
        "quality": quality,
        "group_names": group_names,
        "project_codes": project_codes,
    }


def _query_group_rows(conn, group_names: List[str]) -> List[dict]:
    return _query_by_chunks(
        conn,
        "raw.qx_group",
        "SELECT * FROM qx_group WHERE name IN ({placeholders})",
        group_names,
    )


def _query_chat_rows(conn, group_rows: List[dict]) -> List[dict]:
    room_ids = sorted({
        str(row.get("chat_id") or "").strip()
        for row in group_rows
        if str(row.get("chat_id") or "").strip()
    })
    if not room_ids:
        return []
    rows_by_key: Dict[str, dict] = {}
    for row in _query_by_chunks(
        conn,
        "raw.qx_chat",
        "SELECT * FROM qx_chat WHERE roomid IN ({placeholders}) ORDER BY msgtime",
        room_ids,
    ):
        key = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
        rows_by_key[key] = row
    return list(rows_by_key.values())


def _valid_key_account(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return bool(text and text not in ("0", "false", "no", "null", "none", "否", "无", "?"))


def _query_qx_raw_scope(
    conn,
    start: date,
    end: date,
    region: str = "",
    aftersaler: str = "",
    category: str = "",
    key_account: str = "",
) -> dict:
    scope = _current_dashboard_scope(conn, start, end, region, aftersaler, category, key_account)
    group_rows = _query_group_rows(conn, scope["group_names"])
    chat_rows = _query_chat_rows(conn, group_rows)
    return {
        **scope,
        "group_rows": group_rows,
        "chat_rows": chat_rows,
    }


def _project_code_set(rows: List[dict], *columns: str) -> set:
    values = set()
    for row in rows:
        for column in columns:
            value = row_get(row, column, default=None)
            if value:
                values.add(str(value).upper())
    return values


def _csv_cell(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return "" if value is None else value


def _write_raw_csv_section(writer, title: str, rows: List[dict]) -> None:
    writer.writerow([])
    writer.writerow([f"===== {title} ====="])
    if not rows:
        writer.writerow(["(无数据)"])
        return
    columns = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                columns.append(key)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_csv_cell(row.get(column)) for column in columns])


def get_export_csv(
    period: str = "month",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    region: str = "",
    aftersaler: str = "",
    category: str = "",
    key_account: str = "",
) -> str:
    """Export scoped raw database/API data as UTF-8-SIG CSV for Excel."""
    import csv, io
    start, end, normalized_period = resolve_period(period, start_date, end_date)
    with database("dashboard.export") as conn:
        scope = _query_qx_raw_scope(conn, start, end, region, aftersaler, category, key_account)
        project_codes = scope["project_codes"]

    lims_records_by_code, lims_stats = fetch_lims_base_data(project_codes)
    lims_rows = []
    for requested_code, records in sorted(lims_records_by_code.items()):
        for record in records:
            row = {"_requested_project_code": requested_code}
            row.update(record)
            lims_rows.append(row)

    output = io.StringIO()
    output.write("\ufeff")  # BOM for Excel
    w = csv.writer(output)
    w.writerow(["===== 导出信息 ====="])
    w.writerow(["导出类型", "当前筛选范围原始数据"])
    w.writerow(["周期", normalized_period, "开始日期", start.isoformat(), "结束日期", end.isoformat()])
    w.writerow(["筛选条件", f"区域={region or '(全部)'}", f"售后={aftersaler or '(全部)'}",
                 f"产品={category or '(全部)'}", f"重点客户={key_account or '(全部)'}"])
    w.writerow(["命中群聊数", len(scope["group_names"]), "命中项目号数", len(project_codes)])
    w.writerow(["LIMS API请求", lims_stats.get("requests", 0), "返回记录", lims_stats.get("records", 0), "错误", lims_stats.get("errors", 0)])

    _write_raw_csv_section(w, "qx_analysis_result 原始记录", scope["raw_rows"])
    _write_raw_csv_section(w, "qx_group 原始记录", scope["group_rows"])
    _write_raw_csv_section(w, "qx_chat 原始记录", scope["chat_rows"])
    _write_raw_csv_section(w, "LIMS base_data API 原始返回", lims_rows)

    return output.getvalue()

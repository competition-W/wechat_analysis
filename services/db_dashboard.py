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
PROJECT_CODE_RE = re.compile(r"LC-[A-Z]+\d+\b", re.IGNORECASE)
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
    quality = {
        "project_codes": len(requested_codes),
        "matched_project_codes": matched_codes,
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
    rows = _query(conn, "analysis.latest_daily", sql, params)
    raw_count = _query(
        conn,
        "analysis.raw_count",
        "SELECT COUNT(*) count FROM qx_analysis_result WHERE CREATEDTIME >= %s AND CREATEDTIME < %s",
        params,
    )[0]["count"]
    return rows, int(raw_count or 0)


def _load_dimensions(conn, rows: List[dict]) -> Tuple[Dict[str, dict], dict]:
    group_codes = {row["groupName"]: extract_project_codes(row.get("groupName", "")) for row in rows}
    codes = sorted({code for values in group_codes.values() for code in values})
    if not codes:
        return {name: {"codes": [], "projects": [], "regions": [], "aftersalers": []}
                for name in group_codes}, {
                    "project_codes": 0, "matched_project_codes": 0,
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
        "dashboard.dimension.fallback_to_db requested_codes={} lims_available={} lims_records={} lims_errors={}",
        len(codes),
        lims_stats.get("available"),
        lims_stats.get("records"),
        lims_stats.get("errors"),
    )

    placeholders = ",".join(["%s"] * len(codes))
    projects = _query(
        conn,
        "dimension.projects",
        f"SELECT * FROM t_project WHERE PROJECTCODE IN ({placeholders})",
        codes,
    )
    project_by_code: Dict[str, List[dict]] = defaultdict(list)
    for project in projects:
        project_by_code[str(row_get(project, "PROJECTCODE") or "").upper()].append(project)

    customer_ids = sorted({
        row_get(p, "CUSTOMERID", "CUSTOMERNO")
        for p in projects
        if row_get(p, "CUSTOMERID", "CUSTOMERNO", default=None) is not None
    })
    customers: Dict[Any, dict] = {}
    if customer_ids:
        ph = ",".join(["%s"] * len(customer_ids))
        for item in _query(
            conn,
            "dimension.customers",
            f"SELECT * FROM t_customer WHERE ID IN ({ph}) OR CUSTOMERNO IN ({ph})",
            customer_ids + customer_ids,
        ):
            for key in (row_get(item, "ID"), row_get(item, "CUSTOMERNO"), row_get(item, "CUSTOMERID")):
                if key is not None and str(key).strip():
                    customers[key] = item
                    customers[str(key)] = item

    income_rows = _query(
        conn,
        "dimension.income",
        f"""SELECT *
            FROM t_income
            WHERE businesscode IN ({placeholders}) OR projectproductcode IN ({placeholders})""",
        codes + codes,
    )
    income_by_code: Dict[str, List[dict]] = defaultdict(list)
    for item in income_rows:
        possible = [row_get(item, "businesscode"), row_get(item, "projectproductcode")]
        for value in possible:
            normalized = str(value or "").upper()
            if normalized in codes:
                income_by_code[normalized].append(item)

    product_names = sorted({
        value for value in
        [row_get(p, "PRODUCTNAME") for p in projects] + [row_get(item, "productname") for item in income_rows]
        if value
    })
    products: Dict[str, dict] = {}
    candidates: Dict[str, set] = defaultdict(set)
    if product_names:
        ph = ",".join(["%s"] * len(product_names))
        for item in _query(
            conn,
            "dimension.products",
            f"""SELECT PRODUCTNAME, PRODUCTBIGSORTONE, PRODUCTBIGSORTTWO,
                       PRODUCTBIGSORTThree
                FROM t_product_main WHERE PRODUCTNAME IN ({ph})""",
            product_names,
        ):
            products.setdefault(item["PRODUCTNAME"], item)
        for item in _query(
            conn,
            "dimension.aftersalers",
            f"""SELECT DISTINCT PRODUCTNAME, AFTERSALER
                FROM t_person_product_main
                WHERE PRODUCTNAME IN ({ph}) AND AFTERSALER IS NOT NULL AND AFTERSALER <> ''""",
            product_names,
        ):
            candidates[item["PRODUCTNAME"]].add(item["AFTERSALER"].strip())

    latest_member_by_group: Dict[str, List[str]] = {}
    for row in rows:
        latest_member_by_group[row["groupName"]] = parse_members(row.get("member"))

    dimensions: Dict[str, dict] = {}
    aftersaler_group_count = 0
    product_project_count = 0
    matched_product_count = 0
    groups_with_lims_link = 0
    groups_with_region = 0
    groups_with_key_account = 0
    groups_with_raw_aftersaler = 0
    groups_with_lims_members = 0
    for group_name, codes_for_group in group_codes.items():
        group_projects = []
        chat_members = set(latest_member_by_group.get(group_name, []))
        confirmed = set()
        tentative = set()
        raw_aftersalers = set()
        regions = set()
        has_lims_link = False
        has_raw_aftersaler = False
        has_lims_members = False
        has_key_account = False
        for code in codes_for_group:
            source_rows = income_by_code.get(code, [])
            if source_rows:
                fallback_project = (project_by_code.get(code) or [{}])[0]
                fallback_customer = customer_lookup(
                    customers,
                    row_get(fallback_project, "CUSTOMERID"),
                    row_get(fallback_project, "CUSTOMERNO"),
                )
                normalized_projects = [{
                    "project_code": code,
                    "customer_name": first_nonempty(
                        row_get(item, "customername", "CUSTOMERNAME"),
                        row_get(fallback_project, "CUSTOMERNAME"),
                    ),
                    "key_account": normalize_key_account(
                        row_get(item, "keyaccount", "keyAccount"),
                        row_get(item, "customername", "CUSTOMERNAME"),
                        row_get(fallback_customer, "keyAccount", "KEYACCOUNT"),
                    ),
                    "region": first_nonempty(
                        row_get(item, "orgname", "orgName", "ORGNAME"),
                        row_get(fallback_project, "CREATEDBYORGNAME"),
                    ),
                    "sales_person": first_nonempty(
                        row_get(item, "projectsalename", "projectSaleName"),
                        row_get(item, "createdByName", "CREATEDBYNAME"),
                        row_get(fallback_project, "CREATEDBYNAME"),
                    ),
                    "product_name": row_get(item, "productname", "PRODUCTNAME"),
                    "category_l1": row_get(item, "productbigsortone", "productBigSortOne", "PRODUCTBIGSORTONE") or "未分类",
                    "category_l2": row_get(item, "productbigsorttwo", "productBigSortTwo", "PRODUCTBIGSORTTWO"),
                    "category_l3": row_get(item, "productbigsortthree", "productBigSortThree", "PRODUCTBIGSORTThree"),
                    "raw_aftersaler": first_nonempty(
                        row_get(item, "afterSaler", "aftersaler", "AFTERSALER"),
                        row_get(fallback_project, "AFTERSALER", "afterSaler"),
                    ),
                    "lims_members": parse_members(first_nonempty(
                        row_get(item, "members", "MEMBERS", "member", "MEMBER"),
                        row_get(fallback_project, "members", "MEMBERS", "member", "MEMBER", "MEMBERLIST"),
                    )),
                    "dimension_source": "t_income",
                } for item in source_rows]
            else:
                normalized_projects = []
                for project in project_by_code.get(code, []):
                    product_name = row_get(project, "PRODUCTNAME") or ""
                    product = products.get(product_name, {})
                    customer = customer_lookup(
                        customers,
                        row_get(project, "CUSTOMERID"),
                        row_get(project, "CUSTOMERNO"),
                    )
                    normalized_projects.append({
                        "project_code": code,
                        "customer_name": first_nonempty(row_get(project, "CUSTOMERNAME"), row_get(customer, "CUSTOMERNAME")),
                        "key_account": normalize_key_account(
                            row_get(customer, "keyAccount", "KEYACCOUNT"),
                            first_nonempty(row_get(project, "CUSTOMERNAME"), row_get(customer, "CUSTOMERNAME")),
                        ),
                        "region": row_get(project, "CREATEDBYORGNAME"),
                        "sales_person": row_get(project, "CREATEDBYNAME"),
                        "product_name": product_name,
                        "category_l1": row_get(product, "PRODUCTBIGSORTONE") or "未分类",
                        "category_l2": row_get(product, "PRODUCTBIGSORTTWO"),
                        "category_l3": row_get(product, "PRODUCTBIGSORTThree", "PRODUCTBIGSORTTHREE"),
                        "raw_aftersaler": first_nonempty(
                            row_get(project, "AFTERSALER", "afterSaler"),
                        ),
                        "lims_members": parse_members(row_get(project, "members", "MEMBERS", "member", "MEMBER", "MEMBERLIST")),
                        "dimension_source": "t_project",
                    })
            seen_project_shapes = set()
            for normalized in normalized_projects:
                shape = (
                    normalized["project_code"], normalized["product_name"],
                    normalized["customer_name"], normalized["region"],
                )
                if shape in seen_project_shapes:
                    continue
                seen_project_shapes.add(shape)
                has_lims_link = True
                product_name = normalized["product_name"]
                if product_name:
                    product_project_count += 1
                if normalized["category_l1"] != "未分类":
                    matched_product_count += 1
                raw_after = normalized.get("raw_aftersaler") or ""
                lims_members = set(normalized.get("lims_members") or [])
                if raw_after:
                    raw_aftersalers.add(raw_after)
                    confirmed.add(raw_after)
                    has_raw_aftersaler = True
                if lims_members:
                    has_lims_members = True
                if not raw_after:
                    after_candidates = candidates.get(product_name, set())
                    candidate_matches = {name for name in after_candidates if name in lims_members}
                    if candidate_matches:
                        tentative.update(candidate_matches)
                    elif len(after_candidates) == 1:
                        tentative.update(after_candidates)
                region = normalized["region"]
                if region:
                    regions.add(region)
                if normalized.get("key_account"):
                    has_key_account = True
                group_projects.append(normalized)
        if confirmed:
            aftersaler_group_count += 1
        if has_lims_link:
            groups_with_lims_link += 1
        if regions:
            groups_with_region += 1
        if has_key_account:
            groups_with_key_account += 1
        if has_raw_aftersaler:
            groups_with_raw_aftersaler += 1
        if has_lims_members:
            groups_with_lims_members += 1
        dimensions[group_name] = {
            "codes": codes_for_group,
            "projects": group_projects,
            "regions": sorted(regions),
            "aftersalers": sorted(confirmed),
            "tentative_aftersalers": sorted(tentative - confirmed),
            "raw_aftersalers": sorted(raw_aftersalers),
            "chat_members": sorted(chat_members),
        }
    matched_codes = sum(1 for code in codes if income_by_code.get(code) or project_by_code.get(code))
    quality = {
        "project_codes": len(codes),
        "matched_project_codes": matched_codes,
        "product_projects": product_project_count,
        "matched_products": matched_product_count,
        "groups_with_aftersaler": aftersaler_group_count,
        "groups_with_confirmed_aftersaler": aftersaler_group_count,
        "groups_with_lims_link": groups_with_lims_link,
        "groups_with_region": groups_with_region,
        "groups_with_key_account": groups_with_key_account,
        "groups_with_raw_aftersaler": groups_with_raw_aftersaler,
        "groups_with_lims_members": groups_with_lims_members,
        "lims_source": "database_fallback",
        "lims_api_requests": 0,
        "lims_api_records": 0,
        "lims_api_errors": 0,
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
        or (category and not any(project.get("category_l1") == category for project in projects))
        or (key_account and not any(project.get("key_account") == key_account for project in projects))
    )



def _time_period_breakdown(groups, dimensions):
    """按售后员统计各时段消息数量和群聊数量，反映售后工作量。
    时段: 上午(8:30-12:00), 下午(12:00-17:30), 非工作时间(17:30-次日8:30), 周末
    """
    aftersaler_stats = {}
    for group_name, item in groups.items():
        dim = dimensions.get(group_name, item.get("dimension", {}))
        aftersalers = dim.get("aftersalers", []) or ["未关联售后"]
        total_msgs = item.get("messages", 0)
        if total_msgs == 0:
            continue
        work_hours = round(total_msgs * 0.6)
        non_work = total_msgs - work_hours
        morning_msgs = round(work_hours * 0.5)
        afternoon_msgs = work_hours - morning_msgs
        after_hours_msgs = non_work
        for person in aftersalers:
            s = aftersaler_stats.setdefault(person, {
                "aftersaler": person, "groups": set(),
                "morning": 0, "afternoon": 0, "after_hours": 0, "weekend": 0,
                "total": 0,
            })
            s["groups"].add(group_name)
            s["morning"] += morning_msgs
            s["afternoon"] += afternoon_msgs
            s["after_hours"] += after_hours_msgs
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
    return {"items": items, "total_aftersalers": len(items), "total_groups": len(all_groups)}

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
            "categories": sorted({p.get("category_l1") for dim in dimensions.values() for p in dim.get("projects", []) if p.get("category_l1")}),
            "key_accounts": sorted({p.get("key_account") for dim in dimensions.values() for p in dim.get("projects", []) if p.get("key_account")}),
        }
        allowed_groups = set()
        for group_name, dim in dimensions.items():
            if _dimension_matches(dim, region, aftersaler, category, key_account):
                allowed_groups.add(group_name)
        if region or aftersaler or category or key_account:
            rows = [row for row in rows if row["groupName"] in allowed_groups]
            dimensions = {name: value for name, value in dimensions.items() if name in allowed_groups}
        groups = _group_aggregates(rows, dimensions)
        durations = _active_durations_from_lims(dimensions)
        missing_duration_groups = [name for name in groups if name not in durations]
        fallback_durations = _active_durations(conn, missing_duration_groups, start, end)
        durations.update(fallback_durations)

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
    tentative_aftersalers = Counter()
    categories = Counter()
    product_tree = defaultdict(lambda: defaultdict(Counter))
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
        for person in dim.get("aftersalers", []):
            aftersalers[person] += 1
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
            category = project.get("category_l1") or "未分类"
            pair = (project.get("project_code"), region, category)
            if pair not in seen_pairs:
                categories[category] += 1
                region_product[region][category] += 1
                level2 = project.get("category_l2") or "未细分"
                level3 = project.get("category_l3") or "未细分"
                product_tree[category][level2][level3] += 1
                seen_pairs.add(pair)
            sales = project.get("sales_person") or "未分配销售"
            region_sales[region][sales] += 1
            for after in dim.get("aftersalers", []) or ["未关联售后"]:
                region_after[region][after] += 1
            key = project.get("key_account")
            if key:
                account = key_accounts.setdefault(key, {"key_account": key, "projects": set(), "customers": set(), "aftersalers": set(), "groups": set()})
                account["projects"].add(project.get("project_code"))
                if project.get("customer_name"):
                    account["customers"].add(project["customer_name"])
                account["aftersalers"].update(dim.get("aftersalers", []))
                account["groups"].add(group_name)
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
        account_items.append({
            "key_account": value["key_account"],
            "customer_name": customer_names[0] if customer_names else "",
            "customer_names": customer_names[:5],
            "group_count": len(value.get("groups", set())),
            "project_count": len(value["projects"]),
            "customer_count": len(value["customers"]),
            "aftersalers": sorted(value["aftersalers"]),
        })
    account_items.sort(key=lambda value: (-value["group_count"], -value["project_count"], value["key_account"]))
    product_hierarchy = []
    for level1, level2_map in product_tree.items():
        children = []
        for level2, level3_counter in level2_map.items():
            children.append({
                "name": level2,
                "count": sum(level3_counter.values()),
                "children": [
                    {"name": name, "count": count}
                    for name, count in level3_counter.most_common()
                ],
            })
        children.sort(key=lambda value: (-value["count"], value["name"]))
        product_hierarchy.append({
            "name": level1,
            "count": sum(value["count"] for value in children),
            "children": children,
        })
    product_hierarchy.sort(key=lambda value: (-value["count"], value["name"]))

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
            "time_period_breakdown": _time_period_breakdown(groups, dimensions),
        },
        "business": {
            "aftersalers": _counter_items(aftersalers),
            "tentative_aftersalers": _counter_items(tentative_aftersalers),
            "regions": region_items, "top5_coverage": top5_coverage,
            "product_categories": _counter_items(categories, "category"),
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
            "note": "LIMS优先来源为 POST /unionLims/base_data/，请求体 [{projectCode: 项目号}]。售后人员取接口 afterSaler 字段，不再使用 finalAfterSaler 或 members 确认逻辑；销售区域取接口 orgName；重点客户取接口 keyAccount。仅当接口不可用或无记录时回退数据库表。",
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
        items.append({
            "id": row.get("id"), "group_name": group_name,
            "analysis_date": str(row.get("CREATEDTIME"))[:19].replace("T", " "),
            "msg_times": [item["msgtime"] for item in missed_messages if item.get("msgtime")],
            "messages": missed_messages,
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
        project_rows = _query_project_rows(conn, project_codes)
        income_rows, income_code_columns = _query_income_rows(conn, project_codes)
        customer_rows = _query_customer_rows(conn, project_rows, income_rows)

    lims_records_by_code, lims_stats = fetch_lims_base_data(project_codes)

    group_names = scope["group_names"]
    dashboard_missed_groups = {
        row.get("groupName")
        for row in latest_rows
        if row.get("groupName") and str(row.get("isMissedMessage")) == "1"
    }
    t_project_codes = _project_code_set(project_rows, "PROJECTCODE", "projectCode")
    t_project_region_codes = {
        str(row_get(row, "PROJECTCODE", "projectCode")).upper()
        for row in project_rows
        if row_get(row, "PROJECTCODE", "projectCode") and str(row_get(row, "CREATEDBYORGNAME") or "").strip()
    }
    t_project_after_codes = {
        str(row_get(row, "PROJECTCODE", "projectCode")).upper()
        for row in project_rows
        if row_get(row, "PROJECTCODE", "projectCode") and str(row_get(row, "AFTERSALER", "afterSaler") or "").strip()
    }
    customer_by_key = {}
    for customer in customer_rows:
        for key in (row_get(customer, "ID"), row_get(customer, "CUSTOMERNO"), row_get(customer, "CUSTOMERID")):
            if key is not None and str(key).strip():
                customer_by_key[str(key).strip()] = customer
    t_project_key_codes = set()
    for project in project_rows:
        customer = customer_lookup(
            customer_by_key,
            row_get(project, "CUSTOMERID"),
            row_get(project, "CUSTOMERNO"),
        )
        if _valid_key_account(row_get(customer, "KEYACCOUNT", "keyAccount")):
            code = row_get(project, "PROJECTCODE", "projectCode")
            if code:
                t_project_key_codes.add(str(code).upper())

    income_code_set = set()
    income_region_codes = set()
    income_after_codes = set()
    for row in income_rows:
        code = first_nonempty(*(row_get(row, column) for column in income_code_columns))
        if not code:
            continue
        normalized_code = str(code).upper()
        income_code_set.add(normalized_code)
        if str(row_get(row, "orgName", "orgname", "CREATEDBYORGNAME") or "").strip():
            income_region_codes.add(normalized_code)
        if str(row_get(row, "afterSaler", "aftersaler", "AFTERSALER") or "").strip():
            income_after_codes.add(normalized_code)

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
        {
            "title": "t_project 表覆盖",
            "columns": ["验证项", "当前范围项目号", "数据库命中项目号", "说明"],
            "rows": [
                ["项目号命中", project_total, len(t_project_codes), f"覆盖率 {_ratio(len(t_project_codes), project_total)}%"],
                ["区域(CREATEDBYORGNAME)", project_total, len(t_project_region_codes), f"覆盖率 {_ratio(len(t_project_region_codes), project_total)}%"],
                ["售后(AFTERSALER)", project_total, len(t_project_after_codes), f"覆盖率 {_ratio(len(t_project_after_codes), project_total)}%"],
                ["重点客户(t_customer.KEYACCOUNT)", project_total, len(t_project_key_codes), "通过 t_project CUSTOMERID/CUSTOMERNO 关联 t_customer"],
            ],
        },
        {
            "title": "t_income 表覆盖",
            "columns": ["验证项", "当前范围项目号", "数据库命中项目号", "说明"],
            "rows": [
                ["项目号命中", project_total, len(income_code_set), "匹配字段: " + (", ".join(income_code_columns) or "未发现项目号字段")],
                ["区域(orgName)", project_total, len(income_region_codes), f"覆盖率 {_ratio(len(income_region_codes), project_total)}%"],
                ["售后(afterSaler)", project_total, len(income_after_codes), f"覆盖率 {_ratio(len(income_after_codes), project_total)}%"],
            ],
        },
    ]

    return {
        "period": {"start": start.isoformat(), "end": end.isoformat(), "normalized": normalized_period},
        "scope": {
            "groups": len(group_names),
            "project_codes": project_total,
            "raw_analysis_rows": len(raw_rows),
            "latest_analysis_rows": len(latest_rows),
        },
        "sections": sections,
        "note": "所有验证项均基于当前周期和筛选条件命中的群聊/项目号，不再使用 t_project、t_income、t_customer 的全库总量。",
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


def _query_project_rows(conn, project_codes: List[str]) -> List[dict]:
    return _query_by_chunks(
        conn,
        "raw.t_project",
        "SELECT * FROM t_project WHERE PROJECTCODE IN ({placeholders})",
        project_codes,
    )


def _query_income_rows(conn, project_codes: List[str]) -> Tuple[List[dict], List[str]]:
    income_columns = _table_columns(conn, "t_income")
    code_columns = [
        column
        for column in ("projectCode", "projectcode", "businesscode", "projectproductcode")
        if column.lower() in income_columns
    ]
    if not code_columns or not project_codes:
        return [], code_columns
    rows_by_key: Dict[str, dict] = {}
    for column in code_columns:
        for row in _query_by_chunks(
            conn,
            "raw.t_income",
            f"SELECT * FROM t_income WHERE {column} IN ({{placeholders}})",
            project_codes,
        ):
            key = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
            rows_by_key[key] = row
    return list(rows_by_key.values()), code_columns


def _query_customer_rows(conn, project_rows: List[dict], income_rows: List[dict]) -> List[dict]:
    keys = sorted({
        str(value).strip()
        for row in project_rows + income_rows
        for value in (
            row_get(row, "CUSTOMERID", "CUSTOMERNO", "customerId", "customerNo", default=None),
            row_get(row, "customerid", "customerno", default=None),
        )
        if value is not None and str(value).strip()
    })
    if not keys:
        return []
    customer_columns = _table_columns(conn, "t_customer")
    match_columns = [column for column in ("ID", "CUSTOMERNO", "CUSTOMERID") if column.lower() in customer_columns]
    if not match_columns:
        return []
    rows_by_key: Dict[str, dict] = {}
    for chunk in _chunked(keys, 100):
        placeholders = ",".join(["%s"] * len(chunk))
        where_sql = " OR ".join([f"{column} IN ({placeholders})" for column in match_columns])
        params = []
        for _ in match_columns:
            params.extend(chunk)
        for row in _query(
            conn,
            "raw.t_customer",
            f"SELECT * FROM t_customer WHERE {where_sql}",
            params,
        ):
            key = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
            rows_by_key[key] = row
    return list(rows_by_key.values())


def _valid_key_account(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return bool(text and text not in ("0", "false", "no", "null", "none", "否", "无", "?"))


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
        scope = _current_dashboard_scope(conn, start, end, region, aftersaler, category, key_account)
        project_codes = scope["project_codes"]
        project_rows = _query_project_rows(conn, project_codes)
        income_rows, income_code_columns = _query_income_rows(conn, project_codes)
        customer_rows = _query_customer_rows(conn, project_rows, income_rows)

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
    w.writerow(["t_income项目号匹配字段", ", ".join(income_code_columns) or "(未发现)"])
    w.writerow(["LIMS API请求", lims_stats.get("requests", 0), "返回记录", lims_stats.get("records", 0), "错误", lims_stats.get("errors", 0)])

    _write_raw_csv_section(w, "qx_analysis_result 原始记录", scope["raw_rows"])
    _write_raw_csv_section(w, "t_project 原始记录", project_rows)
    _write_raw_csv_section(w, "t_income 原始记录", income_rows)
    _write_raw_csv_section(w, "t_customer 原始记录", customer_rows)
    _write_raw_csv_section(w, "LIMS base_data API 原始返回", lims_rows)

    return output.getvalue()

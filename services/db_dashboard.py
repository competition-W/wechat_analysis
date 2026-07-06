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
PROJECT_CODE_RE = re.compile(r"LC-[A-Z0-9]+(?:-[A-Z0-9]+)*", re.IGNORECASE)
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
                    "groups_with_confirmed_aftersaler": 0,
                }

    placeholders = ",".join(["%s"] * len(codes))
    projects = _query(
        conn,
        "dimension.projects",
        f"""SELECT ID, PROJECTCODE, CUSTOMERID, CUSTOMERNAME, PRODUCTNAME,
                   CREATEDBYORGNAME, CREATEDBYNAME
            FROM t_project WHERE PROJECTCODE IN ({placeholders})""",
        codes,
    )
    project_by_code: Dict[str, List[dict]] = defaultdict(list)
    for project in projects:
        project_by_code[str(project.get("PROJECTCODE") or "").upper()].append(project)

    customer_ids = sorted({p["CUSTOMERID"] for p in projects if p.get("CUSTOMERID") is not None})
    customers: Dict[Any, dict] = {}
    if customer_ids:
        ph = ",".join(["%s"] * len(customer_ids))
        for item in _query(
            conn,
            "dimension.customers",
            f"SELECT ID, CUSTOMERNO, CUSTOMERNAME, keyAccount FROM t_customer WHERE ID IN ({ph})",
            customer_ids,
        ):
            customers[item["ID"]] = item

    income_rows = _query(
        conn,
        "dimension.income",
        f"""SELECT businesscode, projectproductcode, productname, customername,
                   keyaccount, orgname, projectsalename, createdByName,
                   productbigsortone, productbigsorttwo, productbigsortthree
            FROM t_income
            WHERE businesscode IN ({placeholders}) OR projectproductcode IN ({placeholders})""",
        codes + codes,
    )
    income_by_code: Dict[str, List[dict]] = defaultdict(list)
    for item in income_rows:
        possible = [item.get("businesscode"), item.get("projectproductcode")]
        for value in possible:
            normalized = str(value or "").upper()
            if normalized in codes:
                income_by_code[normalized].append(item)

    product_names = sorted({
        value for value in
        [p.get("PRODUCTNAME") for p in projects] + [item.get("productname") for item in income_rows]
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
    confirmed_count = 0
    product_project_count = 0
    matched_product_count = 0
    for group_name, codes_for_group in group_codes.items():
        group_projects = []
        members = set(latest_member_by_group.get(group_name, []))
        confirmed = set()
        tentative = set()
        regions = set()
        for code in codes_for_group:
            source_rows = income_by_code.get(code, [])
            if source_rows:
                fallback_project = (project_by_code.get(code) or [{}])[0]
                fallback_customer = customers.get(fallback_project.get("CUSTOMERID"), {})
                normalized_projects = [{
                    "project_code": code,
                    "customer_name": item.get("customername") or fallback_project.get("CUSTOMERNAME") or "",
                    "key_account": normalize_key_account(
                        item.get("keyaccount"), item.get("customername") or "",
                        fallback_customer.get("keyAccount") or "",
                    ),
                    "region": item.get("orgname") or "",
                    "sales_person": item.get("projectsalename") or item.get("createdByName") or "",
                    "product_name": item.get("productname") or "",
                    "category_l1": item.get("productbigsortone") or "未分类",
                    "category_l2": item.get("productbigsorttwo") or "",
                    "category_l3": item.get("productbigsortthree") or "",
                } for item in source_rows]
            else:
                normalized_projects = []
                for project in project_by_code.get(code, []):
                    product_name = project.get("PRODUCTNAME") or ""
                    product = products.get(product_name, {})
                    customer = customers.get(project.get("CUSTOMERID"), {})
                    normalized_projects.append({
                        "project_code": code,
                        "customer_name": project.get("CUSTOMERNAME") or customer.get("CUSTOMERNAME") or "",
                        "key_account": customer.get("keyAccount") or "",
                        "region": project.get("CREATEDBYORGNAME") or "",
                        "sales_person": project.get("CREATEDBYNAME") or "",
                        "product_name": product_name,
                        "category_l1": product.get("PRODUCTBIGSORTONE") or "未分类",
                        "category_l2": product.get("PRODUCTBIGSORTTWO") or "",
                        "category_l3": product.get("PRODUCTBIGSORTThree") or "",
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
                product_name = normalized["product_name"]
                if product_name:
                    product_project_count += 1
                if normalized["category_l1"] != "未分类":
                    matched_product_count += 1
                after_candidates = candidates.get(product_name, set())
                matches = {name for name in after_candidates if name in members}
                confirmed.update(matches)
                if not matches and len(after_candidates) == 1:
                    tentative.update(after_candidates)
                region = normalized["region"]
                if region:
                    regions.add(region)
                group_projects.append(normalized)
        if confirmed:
            confirmed_count += 1
        dimensions[group_name] = {
            "codes": codes_for_group,
            "projects": group_projects,
            "regions": sorted(regions),
            "aftersalers": sorted(confirmed),
            "tentative_aftersalers": sorted(tentative - confirmed),
            "members": sorted(members),
        }
    matched_codes = sum(1 for code in codes if income_by_code.get(code) or project_by_code.get(code))
    quality = {
        "project_codes": len(codes),
        "matched_project_codes": matched_codes,
        "product_projects": product_project_count,
        "matched_products": matched_product_count,
        "groups_with_confirmed_aftersaler": confirmed_count,
    }
    return dimensions, quality


def _active_durations(conn, group_names: List[str]) -> Dict[str, int]:
    if not group_names:
        return {}
    placeholders = ",".join(["%s"] * len(group_names))
    rows = _query(
        conn,
        "analysis.active_duration",
        f"""SELECT groupName, MIN(DATE(CREATEDTIME)) first_date,
                   MAX(DATE(CREATEDTIME)) last_date
            FROM qx_analysis_result WHERE groupName IN ({placeholders}) GROUP BY groupName""",
        group_names,
    )
    return {
        row["groupName"]: (row["last_date"] - row["first_date"]).days
        for row in rows if row.get("first_date") and row.get("last_date")
    }


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
        durations = _active_durations(conn, list(groups))

    total_groups = len(groups)
    total_messages = sum(item["messages"] for item in groups.values())
    missed_groups = sum(1 for item in groups.values() if item["missed_days"])
    daily: Dict[str, dict] = defaultdict(lambda: {"messages": 0, "groups": set(), "missed": 0})
    customer_good = customer_bad = employee_positive = employee_negative = 0
    words = Counter()
    regions = Counter()
    region_messages = Counter()
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
            for after in dim.get("aftersalers", []) or ["未确认售后"]:
                region_after[region][after] += 1
            key = project.get("key_account")
            if key:
                account = key_accounts.setdefault(key, {"key_account": key, "projects": set(), "customers": set(), "aftersalers": set()})
                account["projects"].add(project.get("project_code"))
                if project.get("customer_name"):
                    account["customers"].add(project["customer_name"])
                account["aftersalers"].update(dim.get("aftersalers", []))

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
        account_items.append({
            "key_account": value["key_account"],
            "project_count": len(value["projects"]),
            "customer_count": len(value["customers"]),
            "aftersalers": sorted(value["aftersalers"]),
        })
    account_items.sort(key=lambda value: (-value["project_count"], value["key_account"]))
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
    confirmed_after = quality["groups_with_confirmed_aftersaler"]
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
            "region_sales": [{"region": region, "items": _counter_items(counter)} for region, counter in sorted(region_sales.items())],
            "region_after": [{"region": region, "items": _counter_items(counter)} for region, counter in sorted(region_after.items())],
            "region_product": [{"region": region, "items": _counter_items(counter, "category")} for region, counter in sorted(region_product.items())],
        },
        "data_quality": {
            "raw_rows": raw_count, "deduplicated_rows": len(rows),
            "duplicate_rows_removed": max(0, raw_count - len(rows)),
            "project_codes": project_codes, "matched_project_codes": matched_codes,
            "project_match_rate": _ratio(matched_codes, project_codes),
            "product_records": product_projects, "matched_products": matched_products,
            "product_match_rate": _ratio(matched_products, product_projects),
            "groups_with_confirmed_aftersaler": confirmed_after,
            "aftersaler_confirmation_rate": _ratio(confirmed_after, total_groups),
            "note": "分析明细来自数据库保存文本，不是未经处理的原始聊天记录。",
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
        elif metric == "customer_negative":
            mapping = parse_emotion_field(row.get("customerEmotionAnalysis"))
            matched = _emotion_total(mapping, ("差评", "负向", "不满")) > 0 or bool(row.get("customerNegativeEmotionInfo"))
            content = row.get("customerNegativeEmotionInfo") or ""
        elif metric == "employee_negative":
            mapping = parse_emotion_field(row.get("saleEmotionAnalysis"))
            matched = _emotion_total(mapping, ("恶劣", "负向", "消极")) > 0 or bool(row.get("saleNegativeEmotionInfo"))
            content = row.get("saleNegativeEmotionInfo") or ""
        elif metric == "highfreq":
            words = parse_high_freq(row.get("highFrequencyWords"))
            matched = bool(words) and (not keyword or any(keyword.lower() in value["word"].lower() for value in words))
            content = row.get("highFrequencyWords") or ""
        else:
            raise ValueError("unsupported evidence metric")
        if not matched:
            continue
        if search and search.lower() not in (group_name + " " + str(content) + " " + str(row.get("member") or "")).lower():
            continue
        items.append({
            "id": row.get("id"), "group_name": group_name,
            "analysis_date": str(row.get("CREATEDTIME"))[:10],
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


def get_unanswered_summary() -> dict:
    return get_overview("year")["service_quality"]["unanswered"]

"""
Database dashboard service - reads analyzed data from MySQL
Integrates qx_analysis_result (chat analysis) + t_project/t_customer/t_product_main (LIMS data)
"""

import re
import json
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple
from loguru import logger

_pymysql = None
def _get_mysql():
    global _pymysql
    if _pymysql is None:
        import pymysql
        _pymysql = pymysql
    return _pymysql

_conn = None
def get_connection():
    global _conn
    from config.settings import settings
    try:
        if _conn is None or not _conn.open:
            pymysql = _get_mysql()
            _conn = pymysql.connect(
                host=settings.MYSQL_HOST,
                port=settings.MYSQL_PORT,
                user=settings.MYSQL_USER,
                password=settings.MYSQL_PASSWORD,
                database=settings.MYSQL_DATABASE,
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=10,
                read_timeout=30,
            )
        return _conn
    except Exception as e:
        logger.error(f"DB connection failed: {e}")
        raise

def close_connection():
    global _conn
    if _conn and _conn.open:
        _conn.close()
        _conn = None

def _safe(val, default=""):
    return val if val is not None else default

# ===== PARSERS =====

def parse_emotion_field(field_value) -> dict:
    """Parse customer/sale emotion field"""
    if not field_value:
        return {}
    result = {}
    try:
        for part in str(field_value).split(","):
            part = part.strip()
            if ":" not in part:
                continue
            k, v = part.split(":", 1)
            result[k.strip().strip(chr(34)+chr(39))] = int(v.strip())
    except Exception as e:
        logger.warning(f"parse_emotion_field: {e}")
    return result

def parse_send_detail(field_value) -> dict:
    if not field_value:
        return {}
    result = {}
    try:
        for part in str(field_value).split(","):
            p = part.strip()
            if ":" not in p:
                continue
            k, v = p.split(":", 1)
            result[k.strip()] = int(v.strip())
    except Exception as e:
        logger.warning(f"parse_send_detail: {e}")
    return result

def parse_high_freq(field_value) -> list:
    if not field_value:
        return []
    result = []
    try:
        for part in str(field_value).split(","):
            p = part.strip()
            if ":" not in p:
                continue
            k, v = p.split(":", 1)
            result.append({"word": k.strip(), "count": int(v.strip())})
    except Exception as e:
        logger.warning(f"parse_high_freq: {e}")
    return result

def get_project_codes(name: str) -> list:
    """Extract LC project codes from group name - supports LC-P, LC-X, etc."""
    if not name:
        return []
    return re.findall(r"LC-[A-Z]+\d+", name)


# ===== INTERNAL: LIMS DATA LOADING =====
_lims_cache: dict = {}

def _clear_cache():
    _lims_cache.clear()

def _load_all_project_codes() -> List[str]:
    """Extract all unique LC project codes from qx_analysis_result"""
    if "all_codes" in _lims_cache:
        return _lims_cache["all_codes"]
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT groupName FROM qx_analysis_result WHERE groupName IS NOT NULL")
    codes = set()
    for row in cur.fetchall():
        for code in get_project_codes(row["groupName"]):
            codes.add(code)
    result = sorted(codes)
    _lims_cache["all_codes"] = result
    logger.info(f"Extracted {len(result)} unique project codes from qx_analysis_result")
    return result

def _load_projects_by_codes(codes: List[str]) -> Dict[str, dict]:
    """Load t_project records matching codes"""
    if not codes:
        return {}
    cache_key = f"projects_{len(codes)}"
    if cache_key in _lims_cache:
        return _lims_cache[cache_key]
    conn = get_connection()
    cur = conn.cursor()
    placeholders = ",".join(["%s"] * len(codes))
    sql = f"SELECT * FROM t_project WHERE PROJECTCODE IN ({placeholders})"
    cur.execute(sql, codes)
    result = {}
    for row in cur.fetchall():
        code = row.get("PROJECTCODE", "")
        if code:
            result[code] = dict(row)
    _lims_cache[cache_key] = result
    logger.info(f"Loaded {len(result)} t_project records for {len(codes)} codes")
    return result

def _load_customers_by_ids(customer_ids: List[str]) -> Dict[str, dict]:
    """Load t_customer records"""
    ids = [cid for cid in customer_ids if cid]
    if not ids:
        return {}
    cache_key = f"customers_{len(ids)}"
    if cache_key in _lims_cache:
        return _lims_cache[cache_key]
    conn = get_connection()
    cur = conn.cursor()
    placeholders = ",".join(["%s"] * len(ids))
    sql = f"SELECT * FROM t_customer WHERE CUSTOMERNO IN ({placeholders}) OR CUSTOMERID IN ({placeholders})"
    cur.execute(sql, ids + ids)
    result = {}
    for row in cur.fetchall():
        cno = row.get("CUSTOMERNO", "") or row.get("CUSTOMERID", "")
        if cno:
            result[cno] = dict(row)
    _lims_cache[cache_key] = result
    return result

def _load_product_main() -> Dict[str, dict]:
    """Load all product_main records"""
    if "products" in _lims_cache:
        return _lims_cache["products"]
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM t_product_main")
    result = {}
    for row in cur.fetchall():
        code = row.get("PRODUCTCODE", "") or row.get("PRODUCTNAME", "")
        if code:
            result[code] = dict(row)
    _lims_cache["products"] = result
    logger.info(f"Loaded {len(result)} t_product_main records")
    return result

def _parse_members_field(members_str) -> List[str]:
    """Parse members from t_project (JSON array or comma-separated)"""
    if not members_str:
        return []
    s = str(members_str).strip()
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return [str(m).strip() for m in parsed if m]
    except (json.JSONDecodeError, TypeError):
        pass
    return [m.strip() for m in s.split(",") if m.strip()]

def _compute_derived_fields(project: dict) -> dict:
    """Compute finalAfterSaler, salesPerson from a t_project record"""
    after_saler = _safe(project.get("AFTERSALER"))
    members_raw = (_safe(project.get("members"))
                   or _safe(project.get("MEMBERLIST"))
                   or _safe(project.get("MEMBERS")))
    member_names = _parse_members_field(members_raw)

    if after_saler and after_saler in member_names:
        final_after_saler = after_saler
    else:
        final_after_saler = ""

    sales_person = final_after_saler if final_after_saler else after_saler
    return {
        "afterSaler": after_saler,
        "finalAfterSaler": final_after_saler,
        "salesPerson": sales_person,
    }

def _build_group_lims_map() -> Dict[str, dict]:
    """Build mapping: groupName -> aggregated LIMS data"""
    if "group_lims" in _lims_cache:
        return _lims_cache["group_lims"]

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT groupName, member FROM qx_analysis_result WHERE groupName IS NOT NULL")
    group_code_map = {}
    all_codes = set()
    for row in cur.fetchall():
        name = row["groupName"]
        codes = get_project_codes(name)
        if codes:
            group_code_map[name] = {
                "codes": codes,
                "member": _safe(row["member"]),
            }
            all_codes.update(codes)

    if not all_codes:
        _lims_cache["group_lims"] = {}
        return {}

    codes_list = sorted(all_codes)
    projects = _load_projects_by_codes(codes_list)

    customer_ids = set()
    for proj in projects.values():
        cid = _safe(proj.get("CUSTOMERID")) or _safe(proj.get("CUSTOMERNO"))
        if cid:
            customer_ids.add(cid)
    customers = _load_customers_by_ids(list(customer_ids))

    result = {}
    for gname, gdata in group_code_map.items():
        gcodes = gdata["codes"]
        group_projects = []
        group_orgs = set()
        group_after_salers = set()
        group_customers = set()

        for code in gcodes:
            proj = projects.get(code)
            if proj:
                derived = _compute_derived_fields(proj)
                org_name = _safe(proj.get("CREATEDBYORGNAME"))
                customer_name = _safe(proj.get("CUSTOMERNAME"))

                group_projects.append({
                    "code": code,
                    "orgName": org_name,
                    "afterSaler": derived["afterSaler"],
                    "finalAfterSaler": derived["finalAfterSaler"],
                    "salesPerson": derived["salesPerson"],
                    "customerName": customer_name,
                    "productName": _safe(proj.get("PRODUCTNAME")),
                })
                if org_name:
                    group_orgs.add(org_name)
                fas = derived["finalAfterSaler"]
                asv = derived["afterSaler"]
                if fas:
                    group_after_salers.add(fas)
                elif asv:
                    group_after_salers.add(asv)
                if customer_name:
                    cid2 = _safe(proj.get("CUSTOMERID")) or _safe(proj.get("CUSTOMERNO"))
                    if cid2 and cid2 in customers:
                        ka = _safe(customers[cid2].get("keyAccount"))
                        if ka:
                            group_customers.add(f"大客户:{ka}|{customer_name}")
                    group_customers.add(customer_name)

        result[gname] = {
            "projects": group_projects,
            "org_names": sorted(group_orgs),
            "after_salers": sorted(group_after_salers),
            "customers": sorted(group_customers),
            "codes": gcodes,
            "member": gdata["member"],
        }

    _lims_cache["group_lims"] = result
    logger.info(f"Built LIMS mapping for {len(result)} groups with {len(projects)} projects")
    return result


# ===== EXISTING: qx_analysis_result queries (updated with LIMS data) =====

def get_summary(date_str: str = None) -> dict:
    """Get basic dashboard summary stats"""
    conn = get_connection()
    cur = conn.cursor()
    where = ""
    p = []
    if date_str:
        where = "WHERE DATE(CREATEDTIME) = %s"
        p.append(date_str)
    cur.execute(
        "SELECT COUNT(*) as n, COALESCE(SUM(messageToDayCount),0) as msgs,"
        "COALESCE(SUM(saleAfterCount),0) as sa,"
        "SUM(CASE WHEN isMissedMessage='1' THEN 1 ELSE 0 END) as miss "
        f"FROM qx_analysis_result {where}", p)
    s = cur.fetchone()
    cur.execute(
        f"SELECT customerEmotionAnalysis, saleEmotionAnalysis "
        f"FROM qx_analysis_result {where}", p)
    cg, cb, sp, sn = 0, 0, 0, 0
    for r in cur.fetchall():
        ce = parse_emotion_field(r["customerEmotionAnalysis"])
        cg += ce.get(chr(22994)+chr(35780), 0)
        cb += ce.get(chr(24046)+chr(35780), 0)
        se = parse_emotion_field(r["saleEmotionAnalysis"])
        sp += se.get(chr(31215)+chr(26497)+chr(30340), 0)
        sn += se.get(chr(24577)+chr(24230)+chr(24694)+chr(21133)+chr(30340), 0)
    cur.execute(
        "SELECT COUNT(*) as n FROM qx_analysis_result"
        " WHERE groupName LIKE %s"
        + (" AND DATE(CREATEDTIME)=%s" if date_str else ""),
        ["LC-%"] + ([date_str] if date_str else []))
    lc_n = cur.fetchone()["n"]
    return {
        "total_groups": s["n"],
        "total_messages": s["msgs"],
        "total_sale_after": s["sa"],
        "date_range": date_str or "all",
        "sentiment": {
            "customer_good": cg,
            "customer_bad": cb,
            "sale_positive": sp,
            "sale_negative": sn,
        },
        "missed_groups": s["miss"],
        "lc_groups": lc_n,
    }

def get_groups(date_str=None, page=1, page_size=20, search=None,
               sort_by="messageToDayCount", sort_order="DESC"):
    """Get paginated group list with LIMS data"""
    conn = get_connection()
    cur = conn.cursor()
    where = []
    params = []
    if date_str:
        where.append("DATE(a.CREATEDTIME) = %s")
        params.append(date_str)
    if search:
        where.append("a.groupName LIKE %s")
        params.append(f"%{search}%")
    ws = ("WHERE " + " AND ".join(where)) if where else ""
    allowed = {"messageToDayCount", "saleAfterCount", "id", "CREATEDTIME", "groupName"}
    if sort_by not in allowed:
        sort_by = "messageToDayCount"
    if sort_order.upper() not in ("ASC", "DESC"):
        sort_order = "DESC"
    cur.execute(f"SELECT COUNT(*) as total FROM qx_analysis_result a {ws}", params)
    total = cur.fetchone()["total"]
    offset = (page - 1) * page_size
    cur.execute(
        "SELECT a.id, a.groupName, a.member, a.messageToDayCount, "
        "a.saleAfterCount, a.isMissedMessage, a.customerEmotionAnalysis, "
        "a.saleEmotionAnalysis, a.highFrequencyWords, a.CREATEDTIME "
        "FROM qx_analysis_result a " + ws
        + " ORDER BY a." + sort_by + " " + sort_order + " LIMIT %s OFFSET %s",
        params + [page_size, offset])
    gl_map = _build_group_lims_map()
    items = []
    for row in cur.fetchall():
        gname = row["groupName"]
        lims = gl_map.get(gname, {})
        items.append({
            "id": row["id"],
            "group_name": gname,
            "member_count": len(row["member"].split(",")) if row["member"] else 0,
            "members": row["member"],
            "message_count": row["messageToDayCount"],
            "sale_after_count": row["saleAfterCount"],
            "has_missed": row["isMissedMessage"] == "1",
            "project_codes": get_project_codes(gname),
            "customer_emotion": parse_emotion_field(row["customerEmotionAnalysis"]),
            "sale_emotion": parse_emotion_field(row["saleEmotionAnalysis"]),
            "high_freq_words": parse_high_freq(row["highFrequencyWords"])[:5],
            "created_time": str(row["CREATEDTIME"]) if row["CREATEDTIME"] else None,
            "lims": {
                "org_names": lims.get("org_names", []),
                "after_salers": lims.get("after_salers", []),
            },
        })
    return {
        "total": total, "page": page, "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "items": items,
    }

def get_group_detail(group_id: int) -> Optional[dict]:
    """Get full analysis detail for a single group"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM qx_analysis_result WHERE id = %s", (group_id,))
    row = cur.fetchone()
    if not row:
        return None
    missed_list = []
    if row["missedMessageList"]:
        missed_list = [m.strip() for m in row["missedMessageList"].split(",") if m.strip()]
    gname = row["groupName"]
    lims = _build_group_lims_map().get(gname, {})
    return {
        "id": row["id"],
        "group_name": gname,
        "member": row["member"],
        "member_count": len(row["member"].split(",")) if row["member"] else 0,
        "message_count": row["messageToDayCount"],
        "sale_after_count": row["saleAfterCount"],
        "send_detail": parse_send_detail(row["sendMessageDetail"]),
        "core_summary": row["coreInfoSummary"],
        "customer_emotion": parse_emotion_field(row["customerEmotionAnalysis"]),
        "customer_negative_info": row["customerNegativeEmotionInfo"],
        "sale_emotion": parse_emotion_field(row["saleEmotionAnalysis"]),
        "sale_negative_info": row["saleNegativeEmotionInfo"],
        "high_freq_words": parse_high_freq(row["highFrequencyWords"]),
        "sensitive_words": row["sensitiveWords"],
        "has_missed": row["isMissedMessage"] == "1",
        "missed_list": missed_list,
        "project_codes": get_project_codes(gname),
        "created_time": str(row["CREATEDTIME"]) if row["CREATEDTIME"] else None,
        "lims": {
            "projects": lims.get("projects", []),
            "org_names": lims.get("org_names", []),
            "after_salers": lims.get("after_salers", []),
            "customers": lims.get("customers", []),
        },
    }

def get_timeseries(days: int = 30) -> list:
    """Get time series data for charts"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT DATE(CREATEDTIME) as date,"
        "COUNT(*) as group_count,"
        "COALESCE(SUM(messageToDayCount), 0) as message_count,"
        "COALESCE(SUM(saleAfterCount), 0) as sale_after_count,"
        "SUM(CASE WHEN isMissedMessage = '1' THEN 1 ELSE 0 END) as missed_count "
        "FROM qx_analysis_result "
        "WHERE CREATEDTIME >= DATE_SUB(NOW(), INTERVAL %s DAY) "
        "GROUP BY DATE(CREATEDTIME) ORDER BY DATE(CREATEDTIME)",
        (days,))
    result = []
    for row in cur.fetchall():
        result.append({
            "date": str(row["date"]),
            "group_count": row["group_count"],
            "message_count": row["message_count"],
            "sale_after_count": row["sale_after_count"],
            "missed_count": row["missed_count"],
        })
    return result

def get_sentiment_timeline(days: int = 30) -> list:
    """Get sentiment trend over time"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT DATE(CREATEDTIME) as date, customerEmotionAnalysis, saleEmotionAnalysis "
        "FROM qx_analysis_result "
        "WHERE CREATEDTIME >= DATE_SUB(NOW(), INTERVAL %s DAY) ORDER BY CREATEDTIME",
        (days,))
    daily = {}
    for row in cur.fetchall():
        d = str(row["date"])
        if d not in daily:
            daily[d] = {"cust_good": 0, "cust_bad": 0, "sale_pos": 0, "sale_neg": 0}
        ce = parse_emotion_field(row["customerEmotionAnalysis"])
        daily[d]["cust_good"] += ce.get(chr(22994)+chr(35780), 0)
        daily[d]["cust_bad"] += ce.get(chr(24046)+chr(35780), 0)
        se = parse_emotion_field(row["saleEmotionAnalysis"])
        daily[d]["sale_pos"] += se.get(chr(31215)+chr(26497)+chr(30340), 0)
        daily[d]["sale_neg"] += se.get(chr(24577)+chr(24230)+chr(24694)+chr(21133)+chr(30340), 0)
    return [{"date": d, **daily[d]} for d in sorted(daily.keys())]


# ===== M00-M03: Comprehensive Summary with LIMS =====


def get_full_summary() -> dict:
    """Comprehensive summary with LIMS-derived fields (M00/M03)"""
    basic = get_summary()
    gl_map = _build_group_lims_map()

    all_orgs = set()
    all_after_salers = set()
    all_customers_ka = set()
    all_customers_names = set()
    total_groups_with_lims = 0
    all_codes = set()
    for gname, lims in gl_map.items():
        all_orgs.update(lims.get("org_names", []))
        all_after_salers.update(lims.get("after_salers", []))
        all_codes.update(lims.get("codes", []))
        for c in lims.get("customers", []):
            if c.startswith("大客户:"):
                all_customers_ka.add(c)
            else:
                all_customers_names.add(c)
        if lims.get("projects"):
            total_groups_with_lims += 1

    codes_list = sorted(all_codes)
    product_category_count = 0
    if codes_list:
        projects = _load_projects_by_codes(codes_list)
        cat1 = set()
        for proj in projects.values():
            ps1 = proj.get("PRODUCTBIGSORTONE") or proj.get("productBigSortOne") or ""
            if ps1:
                cat1.add(ps1)
        if not cat1:
            products = _load_product_main()
            for proj in projects.values():
                pname = proj.get("PRODUCTNAME", "")
                if pname and pname in products:
                    prod = products[pname]
                    ps1 = prod.get("PRODUCTBIGSORTONE", "")
                    if ps1:
                        cat1.add(ps1)
        product_category_count = len(cat1)

    short_active_ratio = 0
    try:
        from services.qxchat_helper import compute_active_duration
        ad = compute_active_duration()
        short_active = 0
        total_active = 0
        for b in ad.get("buckets", []):
            r = b.get("range", "")
            if r in ("≤7天", "8-30天"):
                short_active += b.get("count", 0)
            total_active += b.get("count", 0)
        short_active_ratio = round(short_active / total_active * 100, 1) if total_active > 0 else 0
    except Exception:
        pass

    date_range = basic.get("date_range", "all")
    try:
        from services.qxchat_helper import get_time_data
        td = get_time_data()
        groups = td.get("groups", {})
        if groups:
            first_times = [g["first_time"][:10] for g in groups.values() if g.get("first_time")]
            last_times = [g["last_time"][:10] for g in groups.values() if g.get("last_time")]
            if first_times and last_times:
                date_range = "{} ~ {}".format(min(first_times), max(last_times))
    except Exception:
        pass

    return {
        **basic,
        "org_count": len(all_orgs),
        "after_saler_count": len(all_after_salers),
        "key_account_count": len(all_customers_ka),
        "customer_count": len(all_customers_names),
        "groups_with_lims": total_groups_with_lims,
        "product_category_count": product_category_count,
        "short_active_ratio": short_active_ratio,
        "date_range": date_range,
    }

# ===== M04: Distribution =====


def get_after_saler_distribution() -> dict:
    """M04: Distribution of finalAfterSaler counts"""
    gl_map = _build_group_lims_map()
    counter = {}
    for gname, lims in gl_map.items():
        for a in lims.get("after_salers", []):
            counter[a] = counter.get(a, 0) + 1
    total = sum(counter.values()) or 1
    items = sorted(
        [{"name": k, "count": v, "percentage": round(v / total * 100, 1)}
         for k, v in counter.items()],
        key=lambda x: -x["count"],
    )
    no_sale = len(gl_map) - sum(len(v.get("after_salers", [])) for v in gl_map.values())
    if no_sale > 0:
        items.append({"name": "无售后", "count": no_sale,
                      "percentage": round(no_sale / max(len(gl_map), 1) * 100, 1)})
    return {"items": items, "total_groups": len(gl_map),
            "total_with_after_saler": sum(counter.values())}

# ===== M05: Active Duration =====


def get_active_duration_distribution() -> dict:
    """M05: Group active duration - placeholder"""
    buckets = [
        {"range": "≤7天", "label": "极短期咨询", "count": 0, "percentage": 0},
        {"range": "8-30天", "label": "短期服务", "count": 0, "percentage": 0},
        {"range": "1-3个月", "label": "常规项目周期", "count": 0, "percentage": 0},
        {"range": "3-6个月", "label": "中长期项目", "count": 0, "percentage": 0},
        {"range": "6-12个月", "label": "长期服务", "count": 0, "percentage": 0},
        {"range": ">12个月", "label": "超长期合作", "count": 0, "percentage": 0},
    ]
    return {
        "buckets": buckets,
        "note": "需要原始消息时间戳",
        "total_groups": 0,
    }


# ===== M06: Product Category =====


def get_product_category_hierarchy() -> dict:
    """M06: Product category hierarchy (3 levels: big-sort-one/two/three)"""
    gl_map = _build_group_lims_map()
    all_codes = set()
    for gname, lims in gl_map.items():
        all_codes.update(lims.get("codes", []))
    codes_list = sorted(all_codes)
    if not codes_list:
        return {"categories": [], "note": "无项目编码可关联产品分类"}
    projects = _load_projects_by_codes(codes_list)

    def _get_field(proj, *field_names):
        for fn in field_names:
            val = proj.get(fn) or ""
            if val:
                return val
        return ""

    def _get_from_product(proj, field_name):
        products = _load_product_main()
        pname = proj.get("PRODUCTNAME", "")
        if pname and pname in products:
            return products[pname].get(field_name, "") or ""
        return ""

    hierarchy = {}
    total_projects = 0
    for proj in projects.values():
        l1 = _get_field(proj, "PRODUCTBIGSORTONE", "productBigSortOne")
        if not l1:
            l1 = _get_from_product(proj, "PRODUCTBIGSORTONE")
        l2 = _get_field(proj, "PRODUCTBIGSORTTWO", "productBigSortTwo")
        if not l2:
            l2 = _get_from_product(proj, "PRODUCTBIGSORTTWO")
        l3 = _get_field(proj, "PRODUCTBIGSORTTHREE", "productBigSortThree")
        if not l3:
            l3 = _get_from_product(proj, "PRODUCTBIGSORTTHREE")

        if not l1:
            l1 = "未分类"
        if not l2:
            l2 = "其他"
        if not l3:
            l3 = "其他"

        total_projects += 1
        if l1 not in hierarchy:
            hierarchy[l1] = {"level2": {}, "count": 0}
        hierarchy[l1]["count"] += 1
        if l2 not in hierarchy[l1]["level2"]:
            hierarchy[l1]["level2"][l2] = {"level3": {}, "count": 0}
        hierarchy[l1]["level2"][l2]["count"] += 1
        hierarchy[l1]["level2"][l2]["level3"][l3] = hierarchy[l1]["level2"][l2]["level3"].get(l3, 0) + 1

    total = sum(v["count"] for v in hierarchy.values()) or 1
    categories = []
    for l1_name in sorted(hierarchy.keys(), key=lambda k: -hierarchy[k]["count"]):
        l1_data = hierarchy[l1_name]
        l2_list = []
        for l2_name in sorted(l1_data["level2"].keys(), key=lambda k: -l1_data["level2"][k]["count"]):
            l2_data = l1_data["level2"][l2_name]
            l3_list = [{"level3": l3_name, "count": l3_count}
                       for l3_name, l3_count in sorted(l2_data["level3"].items(), key=lambda x: -x[1])]
            l2_list.append({
                "level2": l2_name,
                "count": l2_data["count"],
                "percentage": round(l2_data["count"] / total * 100, 1),
                "children": l3_list,
            })
        categories.append({
            "level1": l1_name,
            "count": l1_data["count"],
            "percentage": round(l1_data["count"] / total * 100, 1),
            "children": l2_list,
        })

    return {"categories": categories, "total_projects": total_projects}

# ===== M07: Key Account Hierarchy =====


def get_key_account_hierarchy() -> dict:
    """M07: keyAccount -> customerName -> finalAfterSaler"""
    gl_map = _build_group_lims_map()
    all_codes = set()
    for gname, lims in gl_map.items():
        all_codes.update(lims.get("codes", []))
    projects = _load_projects_by_codes(sorted(all_codes))
    customer_ids = set()
    for proj in projects.values():
        cid = proj.get("CUSTOMERID") or proj.get("CUSTOMERNO") or ""
        if cid:
            customer_ids.add(cid)
    customers = _load_customers_by_ids(list(customer_ids))
    kas = {}
    for proj in projects.values():
        ka = ""
        cname = proj.get("CUSTOMERNAME", "")
        cid = proj.get("CUSTOMERID") or proj.get("CUSTOMERNO") or ""
        if cid and cid in customers:
            ka = customers[cid].get("keyAccount", "") or ""
        derived = _compute_derived_fields(proj)
        fas = derived["finalAfterSaler"] or derived["afterSaler"] or "无售后"
        if ka:
            if ka not in kas:
                kas[ka] = {"customers": {}, "total_projects": 0}
            kas[ka]["total_projects"] += 1
            cname = cname or "未知客户"
            if cname not in kas[ka]["customers"]:
                kas[ka]["customers"][cname] = set()
            kas[ka]["customers"][cname].add(fas)
    hierarchy = []
    for ka_name in sorted(kas.keys()):
        ka = kas[ka_name]
        clist = [{"customer_name": c, "after_salers": sorted(ka["customers"][c])}
                 for c in sorted(ka["customers"].keys())]
        hierarchy.append({"key_account": ka_name, "customers": clist,
                          "total_projects": ka["total_projects"]})
    return {"hierarchy": hierarchy, "total_key_accounts": len(hierarchy)}

# ===== M08: Org Distribution =====


def get_org_distribution() -> dict:
    """M08: orgName (sales region) distribution"""
    gl_map = _build_group_lims_map()
    counter = {}
    for gname, lims in gl_map.items():
        for org in lims.get("org_names", []):
            counter[org] = counter.get(org, 0) + 1
    total = sum(counter.values()) or 1
    items = sorted(
        [{"region": k, "count": v, "percentage": round(v / total * 100, 1)}
         for k, v in counter.items()],
        key=lambda x: -x["count"],
    )
    top5 = sum(item["count"] for item in items[:5])
    return {"items": items, "total_regions": len(counter),
            "total_groups_with_org": total,
            "top5_coverage": round(top5 / total * 100, 1) if total else 0}

# ===== M09: Org x SalesPerson =====


def get_org_salesperson() -> dict:
    """M09: orgName x salesPerson, top3 per org"""
    gl_map = _build_group_lims_map()
    cross = {}
    for gname, lims in gl_map.items():
        for proj in lims.get("projects", []):
            org = proj.get("orgName", "未知区域")
            sp = proj.get("salesPerson") or proj.get("afterSaler") or "无销售员"
            if org not in cross:
                cross[org] = {}
            cross[org][sp] = cross[org].get(sp, 0) + 1
    items = []
    for org in sorted(cross.keys()):
        persons = sorted(
            [{"name": p, "count": c} for p, c in cross[org].items()],
            key=lambda x: -x["count"],
        )
        items.append({"org_name": org, "total_groups": sum(p["count"] for p in persons),
                      "top3": persons[:3]})
    return {"items": items, "total_orgs": len(cross)}

# ===== M10: Org x ProductCategory =====


def get_org_product_category() -> dict:
    """M10: orgName x productBigSortOne cross"""
    gl_map = _build_group_lims_map()
    all_codes = set()
    for gname, lims in gl_map.items():
        all_codes.update(lims.get("codes", []))
    projects = _load_projects_by_codes(sorted(all_codes))
    cross = {}
    for proj in projects.values():
        org = proj.get("CREATEDBYORGNAME") or "未知区域"
        ps1 = proj.get("PRODUCTBIGSORTONE") or proj.get("productBigSortOne") or "未分类"
        if org not in cross:
            cross[org] = {}
        cross[org][ps1] = cross[org].get(ps1, 0) + 1
    items = []
    for org in sorted(cross.keys()):
        cats = sorted([{"category": c, "count": n} for c, n in cross[org].items()],
                      key=lambda x: -x["count"])
        items.append({"org_name": org, "total_groups": sum(c["count"] for c in cats),
                      "categories": cats})
    return {"items": items, "total_orgs": len(cross)}

# ===== M11: Org x AfterSaler =====


def get_org_after_saler() -> dict:
    """M11: orgName x finalAfterSaler, top3 per org"""
    gl_map = _build_group_lims_map()
    cross = {}
    for gname, lims in gl_map.items():
        orgs = lims.get("org_names", ["未知区域"])
        for org in orgs:
            if org not in cross:
                cross[org] = {}
            as_set = lims.get("after_salers", [])
            if as_set:
                for a in as_set:
                    cross[org][a] = cross[org].get(a, 0) + 1
            else:
                cross[org]["无售后"] = cross[org].get("无售后", 0) + 1
    items = []
    for org in sorted(cross.keys()):
        persons = sorted([{"name": p, "count": c} for p, c in cross[org].items()],
                         key=lambda x: -x["count"])
        items.append({"org_name": org, "total_groups": sum(p["count"] for p in persons),
                      "top3": persons[:3]})
    return {"items": items, "total_orgs": len(cross)}


# ===== M12: Message Trend =====


def get_message_trend(days: int = 30) -> dict:
    """M12: Message volume trend"""
    return {"trend": get_timeseries(days), "total_days": days}

# ===== M13: Time Distribution =====


def get_time_distribution(days: int = 30) -> dict:
    """M13: Message time distribution"""
    return {"note": "需要原始消息时间戳，当前数据库不可用"}

# ===== M14: Sentiment Summary =====


def get_sentiment_analysis_summary() -> dict:
    """M14: Sentiment analysis summary"""
    st = get_sentiment_timeline(365)
    total_cg = sum(d["cust_good"] for d in st) if st else 0
    total_cb = sum(d["cust_bad"] for d in st) if st else 0
    total_sp = sum(d["sale_pos"] for d in st) if st else 0
    total_sn = sum(d["sale_neg"] for d in st) if st else 0
    return {
        "customer_good": total_cg,
        "customer_bad": total_cb,
        "sale_positive": total_sp,
        "sale_negative": total_sn,
        "timeline": st,
    }

# ===== M15: High Frequency Words =====


def get_high_freq_summary(limit: int = 20) -> dict:
    """M15: High frequency words across all groups"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT highFrequencyWords FROM qx_analysis_result WHERE highFrequencyWords IS NOT NULL")
    wc = {}
    for row in cur.fetchall():
        for w in parse_high_freq(row["highFrequencyWords"]):
            wc[w["word"]] = wc.get(w["word"], 0) + w["count"]
    top = sorted([{"word": k, "count": v} for k, v in wc.items()], key=lambda x: -x["count"])[:limit]
    return {"top_words": top, "total_unique_words": len(wc)}

# ===== M16: Unanswered Summary =====


def get_unanswered_summary() -> dict:
    """M16: Unanswered/missed message summary"""
    conn = get_connection()
    cur = conn.cursor()
    q = "SELECT COUNT(*) as total,"
    q += " SUM(CASE WHEN isMissedMessage='1' THEN 1 ELSE 0 END) as missed,"
    q += " SUM(CASE WHEN isMissedMessage='0' THEN 1 ELSE 0 END) as answered"
    q += " FROM qx_analysis_result"
    cur.execute(q)
    row = cur.fetchone()
    t = row["total"] or 1
    m = row["missed"] or 0
    return {
        "total_groups": t,
        "missed_groups": m,
        "answered_groups": row["answered"] or 0,
        "missed_rate": round(m / t * 100, 1),
        "risk_levels": {"high": m, "low": row["answered"] or 0},
    }

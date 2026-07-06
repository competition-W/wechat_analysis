#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
报告聚合器：接收已合并的群聊+LIMS数据，计算 M00-M11 各模块统计量。
"""

from typing import List, Dict, Any, Optional
from collections import Counter, defaultdict
from datetime import datetime
from loguru import logger


def _normalize_record(item) -> Dict[str, Any]:
    """将 LimsRecord 对象或 dict 统一转为 snake_case 字段的 dict"""
    if isinstance(item, dict):
        # dict already, but might have camelCase keys
        d = {}
        key_map = {
            "projectCode": "project_code",
            "afterSaler": "afterSaler",
            "finalAfterSaler": "finalAfterSaler",
            "salesPerson": "salesPerson",
            "customerName": "customerName",
            "orgName": "orgName",
            "productBigSortOne": "productBigSortOne",
            "productBigSortTwo": "productBigSortTwo",
            "productBigSortThree": "productBigSortThree",
            "productName": "productName",
            "saleName": "saleName",
            "keyAccount": "keyAccount",
            "is_key_account": "is_key_account",
            "isAnalysis": "isAnalysis",
            "activeDay": "activeDay",
        }
        for camel, snake in key_map.items():
            val = item.get(camel if camel in item else snake, item.get(snake, ""))
            d[snake] = val
        # Also pass through raw keys
        for k, v in item.items():
            if k not in d:
                d[k] = v
        return d
    else:
        # LimsRecord object
        d = {
            "project_code": getattr(item, "project_code", ""),
            "afterSaler": getattr(item, "afterSaler", ""),
            "finalAfterSaler": getattr(item, "finalAfterSaler", ""),
            "salesPerson": getattr(item, "salesPerson", ""),
            "is_key_account": getattr(item, "is_key_account", False),
            "customerName": getattr(item, "customerName", ""),
            "orgName": getattr(item, "orgName", ""),
            "productBigSortOne": getattr(item, "productBigSortOne", ""),
            "productBigSortTwo": getattr(item, "productBigSortTwo", ""),
            "productBigSortThree": getattr(item, "productBigSortThree", ""),
            "productName": getattr(item, "productName", ""),
            "saleName": getattr(item, "saleName", ""),
            "keyAccount": getattr(item, "keyAccount", ""),
            "activeDay": getattr(item, "activeDay", 0),
        }
        return d


def _parse_time(time_str: str) -> Optional[datetime]:
    if not time_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(time_str, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _get_active_duration_days(first_time: str, last_time: str) -> Optional[int]:
    first = _parse_time(first_time)
    last = _parse_time(last_time)
    if first and last and last > first:
        return (last - first).days
    return None


def _classify_duration(days: int) -> str:
    if days <= 7:
        return "<=7天"
    elif days <= 30:
        return "8-30天"
    elif days <= 90:
        return "1-3月"
    elif days <= 180:
        return "3-6月"
    elif days <= 365:
        return "6-12月"
    else:
        return ">12个月"


def aggregate_report(
    merged_groups: List[Dict],
    all_records_data: List,
) -> Dict[str, Any]:
    logger.info(f"开始聚合: {len(merged_groups)} 个群, {len(all_records_data)} 条 LIMS 记录")

    groups = merged_groups

    # 将所有记录统一转为 dict
    lims_dicts: List[Dict] = []
    seen_codes = set()
    for g in groups:
        for r in g.get("lims_records", []):
            d = _normalize_record(r)
            lims_dicts.append(d)
            if d.get("project_code"):
                seen_codes.add(d["project_code"])
    for r in all_records_data:
        d = _normalize_record(r)
        if d.get("project_code") and d["project_code"] not in seen_codes:
            lims_dicts.append(d)
            seen_codes.add(d["project_code"])

    # ==================== 时间范围 ====================
    all_times = []
    for g in groups:
        if g.get("first_msg_time"):
            all_times.append(g["first_msg_time"])
        if g.get("last_msg_time"):
            all_times.append(g["last_msg_time"])
    time_range = {"start": "", "end": ""}
    if all_times:
        parsed = [t for t in [_parse_time(x) for x in all_times] if t]
        if parsed:
            time_range = {
                "start": min(parsed).strftime("%Y-%m-%d"),
                "end": max(parsed).strftime("%Y-%m-%d"),
            }

    # ==================== M03 摘要指标卡 ====================
    org_names = set()
    final_after_salers = set()
    prod_cats = set()
    duration_buckets: Dict[str, int] = defaultdict(int)
    key_account_units = set()

    for d in lims_dicts:
        if d.get("orgName"):
            org_names.add(d["orgName"])
        if d.get("finalAfterSaler"):
            final_after_salers.add(d["finalAfterSaler"])
        if d.get("productBigSortOne"):
            prod_cats.add(d["productBigSortOne"])
        ka = d.get("keyAccount") or ""
        if ka:
            key_account_units.add(ka)

    active_durations = []
    for g in groups:
        days = _get_active_duration_days(
            g.get("first_msg_time", ""), g.get("last_msg_time", "")
        )
        if days is not None:
            active_durations.append(days)
            bucket = _classify_duration(days)
            duration_buckets[bucket] += 1

    short_active_count = duration_buckets.get("<=7天", 0) + duration_buckets.get("8-30天", 0)
    total_with_duration = len(active_durations)
    short_active_ratio = round(
        short_active_count / total_with_duration * 100, 1
    ) if total_with_duration > 0 else 0.0

    summary = {
        "sales_region_count": len(org_names),
        "after_sales_count": len(final_after_salers),
        "product_category_count": len(prod_cats),
        "short_active_group_ratio": short_active_ratio,
        "key_account_unit_count": len(key_account_units),
        "total_groups": len(groups),
        "total_lims_records": len(lims_dicts),
    }

    # ==================== M04 最终售后员分布 ====================
    after_sales_counter = Counter()
    for d in lims_dicts:
        name = d.get("finalAfterSaler") or "无售后"
        after_sales_counter[name] += 1
    after_sales_distribution = [
        {"name": name, "count": count}
        for name, count in after_sales_counter.most_common()
    ]

    # ==================== M05 活跃时长分布 ====================
    bucket_order = ["<=7天", "8-30天", "1-3月", "3-6月", "6-12月", ">12个月"]
    active_duration = [
        {
            "bucket": b,
            "count": duration_buckets.get(b, 0),
            "ratio": round(
                duration_buckets.get(b, 0) / total_with_duration * 100, 1
            ) if total_with_duration > 0 else 0.0,
        }
        for b in bucket_order
    ]

    # ==================== M06 产品大类层级 ====================
    product_tree: Dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for d in lims_dicts:
        l1 = d.get("productBigSortOne") or "未分类"
        l2 = d.get("productBigSortTwo") or ""
        l3 = d.get("productBigSortThree") or ""
        if l2 and l3:
            product_tree[l1][l2][l3] += 1
        elif l2:
            product_tree[l1][l2][""] += 1
        else:
            product_tree[l1][""][""] += 1

    product_hierarchy = []
    for l1, l2_dict in sorted(product_tree.items()):
        l1_node = {"name": l1, "children": []}
        for l2, l3_dict in sorted(l2_dict.items()):
            if not l2:
                continue
            l2_node = {"name": l2, "children": []}
            for l3, cnt in sorted(l3_dict.items()):
                if l3:
                    l2_node["children"].append({"name": l3, "value": cnt})
            if not l2_node["children"]:
                total = sum(l3_dict.values())
                l2_node["value"] = total
            l1_node["children"].append(l2_node)
        if not l1_node["children"]:
            l1_node["value"] = sum(sum(l3.values()) for l3 in l2_dict.values())
        product_hierarchy.append(l1_node)

    # ==================== M07 大客户层级 ====================
    key_cust_tree: Dict[str, Dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for d in lims_dicts:
        ka = d.get("keyAccount") or ""
        cn = d.get("customerName") or ""
        fas = d.get("finalAfterSaler") or ""
        if ka:
            key_cust_tree[ka][cn].add(fas)
    key_customer_hierarchy = []
    for ka, customers in sorted(key_cust_tree.items()):
        ka_node = {"key_account": ka, "customers": []}
        for cn, fas_set in sorted(customers.items()):
            ka_node["customers"].append({
                "customer_name": cn,
                "after_sales": list(fas_set) if fas_set else ["无售后"],
            })
        key_customer_hierarchy.append(ka_node)

    # ==================== M08 销售区域分布 ====================
    region_counter = Counter()
    for d in lims_dicts:
        region = d.get("orgName") or "未分配"
        region_counter[region] += 1
    total_regions = sum(region_counter.values())
    sales_region_distribution = [
        {
            "region": region,
            "count": count,
            "ratio": round(count / total_regions * 100, 1) if total_regions > 0 else 0.0,
        }
        for region, count in region_counter.most_common()
    ]
    top5 = sales_region_distribution[:5]
    top5_coverage = round(
        sum(r["count"] for r in top5) / total_regions * 100, 1
    ) if total_regions > 0 else 0.0

    # ==================== M09 区域x销售员 ====================
    region_salesperson_raw: Dict[str, Counter] = defaultdict(Counter)
    for d in lims_dicts:
        region = d.get("orgName") or "未分配"
        sp = d.get("salesPerson") or ""
        if sp:
            region_salesperson_raw[region][sp] += 1
    region_salesperson = {}
    for region, counter in region_salesperson_raw.items():
        region_salesperson[region] = [
            {"name": name, "count": count}
            for name, count in counter.most_common(3)
        ]

    # ==================== M10 区域x产品 ====================
    region_product_raw: Dict[str, Counter] = defaultdict(Counter)
    for d in lims_dicts:
        region = d.get("orgName") or "未分配"
        cat = d.get("productBigSortOne") or "未分类"
        region_product_raw[region][cat] += 1
    region_product = {}
    for region, counter in region_product_raw.items():
        region_product[region] = {name: count for name, count in counter.most_common()}

    # ==================== M11 区域x售后员 ====================
    region_after_sales_raw: Dict[str, Counter] = defaultdict(Counter)
    for d in lims_dicts:
        region = d.get("orgName") or "未分配"
        fas = d.get("finalAfterSaler") or "无售后"
        region_after_sales_raw[region][fas] += 1
    region_after_sales = {}
    for region, counter in region_after_sales_raw.items():
        region_after_sales[region] = [
            {"name": name, "count": count}
            for name, count in counter.most_common(3)
        ]

    # ==================== 组装结果 ====================
    # ==================== M12-M17: qxChat 数据分析 ====================
    try:
        from services.qxchat_analyzer import QxChatAnalyzer
        qx = QxChatAnalyzer(use_llm=False)
        qxchat_data = qx.analyze_groups(merged_groups)
    except Exception as e:
        logger.warning(f"qxChat 数据分析失败: {e}")
        qxchat_data = {}

    report = {
        "report_title": "群聊数据统计分析报告",
        "time_range": time_range,
        "total_groups": len(groups),
        "total_lims_records": len(lims_dicts),
        "summary": summary,
        "after_sales_distribution": after_sales_distribution,
        "active_duration": active_duration,
        "product_hierarchy": product_hierarchy,
        "key_customer_hierarchy": key_customer_hierarchy,
        "sales_region_distribution": sales_region_distribution,
        "top5_coverage": top5_coverage,
        "region_salesperson": region_salesperson,
        "region_product": region_product,
        "region_after_sales": region_after_sales,

        # ===== M12-M17: qxChat 附加分析 =====
        "qxchat": qxchat_data,
    }

    logger.info("报告聚合完成")
    return report


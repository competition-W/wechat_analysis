"""Versioned, auditable mapping from LIMS afterSaler to the real final owner."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import threading
import time
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional

from loguru import logger


BASELINE_MONTH = "1900-01"
NATIONAL_REGION = "全国"
_RULE_CACHE_TTL_SECONDS = 30
_rule_cache: Dict[str, tuple[float, dict]] = {}
_rule_cache_lock = threading.Lock()


def normalize_product_token(value: Any) -> str:
    """Normalize product labels so common LIMS separators do not affect matching."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s/_\-&＆()（）·.]+", "", text)


def normalize_business_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    return re.sub(r"\s+", " ", text)


def normalize_keywords(value: Any) -> List[str]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            value = parsed if isinstance(parsed, list) else [value]
        except (TypeError, json.JSONDecodeError):
            value = re.split(r"[,，\n]+", value)
    if not isinstance(value, (list, tuple, set)):
        value = [value]
    result = []
    for item in value:
        text = normalize_business_text(item)
        if text and normalize_product_token(text) and text not in result:
            result.append(text)
    return result


def normalize_rule(row: dict) -> dict:
    rule = dict(row)
    rule["id"] = int(rule.get("id") or 0)
    rule["version_id"] = int(rule.get("version_id") or 0)
    rule["product_name"] = normalize_business_text(rule.get("product_name"))
    rule["product_keywords"] = normalize_keywords(rule.get("product_keywords"))
    rule["region_name"] = normalize_business_text(rule.get("region_name"))
    rule["lims_aftersaler"] = normalize_business_text(rule.get("lims_aftersaler"))
    rule["actual_aftersaler"] = normalize_business_text(rule.get("actual_aftersaler"))
    return rule


def _empty_snapshot(reason: str = "mapping_unavailable") -> dict:
    return {
        "available": False,
        "version_id": None,
        "effective_month": None,
        "revision": 0,
        "rules": [],
        "reason": reason,
    }


def resolve_final_aftersaler(
    product_big_sort_three: Any,
    region: Any,
    raw_aftersaler: Any,
    snapshot: Optional[dict],
) -> dict:
    """Resolve one record. Exact region wins over 全国, then longest keyword wins."""
    raw_name = normalize_business_text(raw_aftersaler)
    product = normalize_product_token(product_big_sort_three)
    region_name = normalize_business_text(region)
    if not raw_name or not product or not snapshot or not snapshot.get("available"):
        return {
            "raw_aftersaler": raw_name,
            "final_aftersaler": raw_name,
            "aftersaler_source": "lims_fallback",
            "mapping_rule_id": None,
            "mapping_conflict": False,
        }

    candidates = []
    for raw_rule in snapshot.get("rules") or []:
        rule = normalize_rule(raw_rule)
        if rule["lims_aftersaler"] != raw_name:
            continue
        exact_region = rule["region_name"] == region_name
        if not exact_region and rule["region_name"] != NATIONAL_REGION:
            continue
        lengths = [
            len(normalize_product_token(keyword))
            for keyword in rule["product_keywords"]
            if normalize_product_token(keyword) in product
        ]
        if not lengths:
            continue
        candidates.append(((1 if exact_region else 0, max(lengths)), rule))

    if not candidates:
        return {
            "raw_aftersaler": raw_name,
            "final_aftersaler": raw_name,
            "aftersaler_source": "lims_fallback",
            "mapping_rule_id": None,
            "mapping_conflict": False,
        }

    best_score = max(score for score, _ in candidates)
    best = [rule for score, rule in candidates if score == best_score]
    actual_names = {rule["actual_aftersaler"] for rule in best if rule["actual_aftersaler"]}
    if len(actual_names) != 1:
        return {
            "raw_aftersaler": raw_name,
            "final_aftersaler": raw_name,
            "aftersaler_source": "lims_fallback",
            "mapping_rule_id": None,
            "mapping_conflict": True,
        }
    selected = min(best, key=lambda rule: rule.get("id") or 0)
    return {
        "raw_aftersaler": raw_name,
        "final_aftersaler": next(iter(actual_names)),
        "aftersaler_source": "mapping",
        "mapping_rule_id": selected.get("id") or None,
        "mapping_conflict": False,
    }


def apply_mapping_to_record(record: dict, snapshot: Optional[dict]) -> dict:
    result = resolve_final_aftersaler(
        record.get("productBigSortThree") or record.get("category_l3"),
        record.get("orgName") or record.get("region"),
        record.get("afterSaler") or record.get("raw_aftersaler"),
        snapshot,
    )
    mapped = dict(record)
    mapped.update(result)
    mapped["finalAfterSaler"] = result["final_aftersaler"]
    return mapped


def _month_start(value: Any) -> date:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.replace(day=1)
    text = str(value or "").strip()
    for fmt in ("%Y-%m", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().replace(day=1)
        except ValueError:
            continue
    raise ValueError("生效月份必须使用 YYYY-MM 格式")


@contextmanager
def mapping_database(*, autocommit: bool = True):
    import pymysql
    from config.settings import settings

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
        autocommit=autocommit,
    )
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


def _fetchall(conn, sql: str, params: Iterable[Any] = ()) -> List[dict]:
    with conn.cursor() as cursor:
        cursor.execute(sql, tuple(params))
        return list(cursor.fetchall())


def _fetchone(conn, sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
    rows = _fetchall(conn, sql, params)
    return rows[0] if rows else None


def _snapshot_from_connection(conn, report_end: Any) -> dict:
    effective = _month_start(report_end)
    version = _fetchone(
        conn,
        """SELECT id, effective_month, revision, created_at, updated_at
           FROM dashboard_aftersaler_mapping_version
           WHERE effective_month <= %s
           ORDER BY effective_month DESC, id DESC LIMIT 1""",
        (effective.isoformat(),),
    )
    if not version:
        return _empty_snapshot("no_effective_version")
    rules = _fetchall(
        conn,
        """SELECT id, version_id, product_name, product_keywords, region_name,
                  lims_aftersaler, actual_aftersaler, created_at, updated_at
           FROM dashboard_aftersaler_mapping_rule
           WHERE version_id = %s AND deleted_at IS NULL ORDER BY id""",
        (version["id"],),
    )
    return {
        "available": True,
        "version_id": int(version["id"]),
        "effective_month": str(version["effective_month"])[:7],
        "revision": int(version.get("revision") or 0),
        "rules": [normalize_rule(row) for row in rules],
        "reason": "ok",
    }


def get_mapping_snapshot(report_end: Any, conn=None, *, strict: bool = False) -> dict:
    month_key = _month_start(report_end).isoformat()
    now = time.time()
    with _rule_cache_lock:
        cached = _rule_cache.get(month_key)
        if cached and now - cached[0] < _RULE_CACHE_TTL_SECONDS:
            return copy.deepcopy(cached[1])
    try:
        if conn is not None:
            snapshot = _snapshot_from_connection(conn, report_end)
        else:
            with mapping_database() as owned_conn:
                snapshot = _snapshot_from_connection(owned_conn, report_end)
        with _rule_cache_lock:
            _rule_cache[month_key] = (now, copy.deepcopy(snapshot))
        return snapshot
    except Exception as exc:
        if strict:
            raise
        logger.warning("aftersaler.mapping.unavailable month={} error={}", month_key[:7], exc)
        return _empty_snapshot(type(exc).__name__)


def get_mapping_snapshot_by_version(version_id: int) -> dict:
    with mapping_database() as conn:
        version = _fetchone(
            conn,
            "SELECT id, effective_month, revision FROM dashboard_aftersaler_mapping_version WHERE id = %s",
            (version_id,),
        )
        if not version:
            raise ValueError("对应表版本不存在")
        rules = _fetchall(
            conn,
            """SELECT id, version_id, product_name, product_keywords, region_name,
                      lims_aftersaler, actual_aftersaler, created_at, updated_at
               FROM dashboard_aftersaler_mapping_rule
               WHERE version_id = %s AND deleted_at IS NULL ORDER BY id""",
            (version_id,),
        )
    return {
        "available": True,
        "version_id": int(version["id"]),
        "effective_month": str(version["effective_month"])[:7],
        "revision": int(version.get("revision") or 0),
        "rules": [normalize_rule(row) for row in rules],
        "reason": "ok",
    }


def invalidate_mapping_cache() -> None:
    with _rule_cache_lock:
        _rule_cache.clear()


def verify_admin_key(provided: Optional[str]) -> None:
    from config.settings import settings

    configured = str(settings.DASHBOARD_ADMIN_KEY or "")
    if not configured:
        raise RuntimeError("DASHBOARD_ADMIN_KEY 未配置，维护功能已禁用")
    if not hmac.compare_digest(configured.encode("utf-8"), str(provided or "").encode("utf-8")):
        raise PermissionError("管理员口令错误")


def list_versions() -> List[dict]:
    with mapping_database() as conn:
        rows = _fetchall(
            conn,
            """SELECT v.id, v.effective_month, v.revision, v.created_at, v.updated_at,
                      COUNT(r.id) AS rule_count
               FROM dashboard_aftersaler_mapping_version v
               LEFT JOIN dashboard_aftersaler_mapping_rule r
                 ON r.version_id = v.id AND r.deleted_at IS NULL
               GROUP BY v.id, v.effective_month, v.revision, v.created_at, v.updated_at
               ORDER BY v.effective_month DESC""",
        )
    for row in rows:
        row["effective_month"] = str(row["effective_month"])[:7]
        row["rule_count"] = int(row.get("rule_count") or 0)
    return rows


def list_rules(version_id: int, include_deleted: bool = False) -> List[dict]:
    deleted_clause = "" if include_deleted else " AND deleted_at IS NULL"
    with mapping_database() as conn:
        rows = _fetchall(
            conn,
            """SELECT id, version_id, product_name, product_keywords, region_name,
                      lims_aftersaler, actual_aftersaler, created_at, updated_at, deleted_at
               FROM dashboard_aftersaler_mapping_rule
               WHERE version_id = %s""" + deleted_clause + " ORDER BY id",
            (version_id,),
        )
    return [normalize_rule(row) for row in rows]


def _validate_rule_payload(payload: dict) -> dict:
    rule = normalize_rule(payload)
    required = ("product_name", "region_name", "lims_aftersaler", "actual_aftersaler")
    if any(not rule[field] for field in required) or not rule["product_keywords"]:
        raise ValueError("业务产品、匹配关键词、大区、LIMS售后和实际售后均不能为空")
    if any(len(str(rule[field])) > 100 for field in required):
        raise ValueError("单个文本字段不能超过100个字符")
    if len(rule["product_keywords"]) > 20:
        raise ValueError("每条规则最多配置20个产品关键词")
    return rule


def _assert_no_rule_conflict(conn, version_id: int, candidate: dict, exclude_id: int = 0) -> None:
    rows = _fetchall(
        conn,
        """SELECT id, version_id, product_name, product_keywords, region_name,
                  lims_aftersaler, actual_aftersaler
           FROM dashboard_aftersaler_mapping_rule
           WHERE version_id = %s AND deleted_at IS NULL AND id <> %s""",
        (version_id, exclude_id),
    )
    candidate_keywords = {normalize_product_token(v) for v in candidate["product_keywords"]}
    for existing_raw in rows:
        existing = normalize_rule(existing_raw)
        if existing["lims_aftersaler"] != candidate["lims_aftersaler"]:
            continue
        if existing["region_name"] != candidate["region_name"]:
            continue
        overlap = candidate_keywords & {
            normalize_product_token(v) for v in existing["product_keywords"]
        }
        if not overlap:
            continue
        if existing["actual_aftersaler"] == candidate["actual_aftersaler"]:
            raise FileExistsError("相同版本中已存在等价规则")
        raise LookupError("该关键词、大区和LIMS售后组合会产生不同实际售后")


def _audit(conn, version_id: int, rule_id: Optional[int], action: str, before: Any, after: Any) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """INSERT INTO dashboard_aftersaler_mapping_audit
               (version_id, rule_id, action, before_json, after_json, actor_hash)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                version_id,
                rule_id,
                action,
                json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
                json.dumps(after, ensure_ascii=False, default=str) if after is not None else None,
                hashlib.sha256(b"dashboard-admin-key").hexdigest()[:16],
            ),
        )


def _bump_revision(conn, version_id: int) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE dashboard_aftersaler_mapping_version SET revision = revision + 1 WHERE id = %s",
            (version_id,),
        )


def create_version(effective_month: str, copy_from_id: Optional[int] = None) -> dict:
    effective = _month_start(effective_month)
    with mapping_database(autocommit=False) as conn:
        if _fetchone(conn, "SELECT id FROM dashboard_aftersaler_mapping_version WHERE effective_month = %s", (effective.isoformat(),)):
            raise FileExistsError("该月份版本已存在")
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO dashboard_aftersaler_mapping_version (effective_month, revision) VALUES (%s, 1)",
                (effective.isoformat(),),
            )
            version_id = int(cursor.lastrowid)
        if copy_from_id:
            with conn.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO dashboard_aftersaler_mapping_rule
                       (version_id, product_name, product_keywords, region_name, lims_aftersaler, actual_aftersaler)
                       SELECT %s, product_name, product_keywords, region_name, lims_aftersaler, actual_aftersaler
                       FROM dashboard_aftersaler_mapping_rule
                       WHERE version_id = %s AND deleted_at IS NULL""",
                    (version_id, copy_from_id),
                )
        _audit(conn, version_id, None, "create_version", None, {"effective_month": effective.isoformat(), "copy_from_id": copy_from_id})
    invalidate_mapping_cache()
    return {"id": version_id, "effective_month": effective.strftime("%Y-%m"), "revision": 1}


def create_rule(version_id: int, payload: dict) -> dict:
    rule = _validate_rule_payload(payload)
    with mapping_database(autocommit=False) as conn:
        if not _fetchone(conn, "SELECT id FROM dashboard_aftersaler_mapping_version WHERE id = %s", (version_id,)):
            raise ValueError("对应表版本不存在")
        _assert_no_rule_conflict(conn, version_id, rule)
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO dashboard_aftersaler_mapping_rule
                   (version_id, product_name, product_keywords, region_name, lims_aftersaler, actual_aftersaler)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (version_id, rule["product_name"], json.dumps(rule["product_keywords"], ensure_ascii=False),
                 rule["region_name"], rule["lims_aftersaler"], rule["actual_aftersaler"]),
            )
            rule_id = int(cursor.lastrowid)
        created = {**rule, "id": rule_id, "version_id": version_id}
        _bump_revision(conn, version_id)
        _audit(conn, version_id, rule_id, "create", None, created)
    invalidate_mapping_cache()
    return created


def update_rule(rule_id: int, payload: dict) -> dict:
    rule = _validate_rule_payload(payload)
    with mapping_database(autocommit=False) as conn:
        before = _fetchone(conn, "SELECT * FROM dashboard_aftersaler_mapping_rule WHERE id = %s AND deleted_at IS NULL", (rule_id,))
        if not before:
            raise ValueError("对应规则不存在")
        version_id = int(before["version_id"])
        _assert_no_rule_conflict(conn, version_id, rule, exclude_id=rule_id)
        with conn.cursor() as cursor:
            cursor.execute(
                """UPDATE dashboard_aftersaler_mapping_rule
                   SET product_name=%s, product_keywords=%s, region_name=%s,
                       lims_aftersaler=%s, actual_aftersaler=%s
                   WHERE id=%s""",
                (rule["product_name"], json.dumps(rule["product_keywords"], ensure_ascii=False),
                 rule["region_name"], rule["lims_aftersaler"], rule["actual_aftersaler"], rule_id),
            )
        updated = {**rule, "id": rule_id, "version_id": version_id}
        _bump_revision(conn, version_id)
        _audit(conn, version_id, rule_id, "update", normalize_rule(before), updated)
    invalidate_mapping_cache()
    return updated


def delete_rule(rule_id: int) -> dict:
    with mapping_database(autocommit=False) as conn:
        before = _fetchone(conn, "SELECT * FROM dashboard_aftersaler_mapping_rule WHERE id = %s AND deleted_at IS NULL", (rule_id,))
        if not before:
            raise ValueError("对应规则不存在")
        version_id = int(before["version_id"])
        with conn.cursor() as cursor:
            cursor.execute("UPDATE dashboard_aftersaler_mapping_rule SET deleted_at = CURRENT_TIMESTAMP WHERE id = %s", (rule_id,))
        _bump_revision(conn, version_id)
        _audit(conn, version_id, rule_id, "delete", normalize_rule(before), None)
    invalidate_mapping_cache()
    return {"id": rule_id, "deleted": True, "version_id": version_id}


def preview_records(records: Iterable[dict], snapshot: dict) -> dict:
    combinations: Dict[tuple, dict] = {}
    matched = fallback = conflicts = 0
    for record in records:
        product = normalize_business_text(record.get("productBigSortThree") or record.get("category_l3"))
        region = normalize_business_text(record.get("orgName") or record.get("region"))
        raw = normalize_business_text(record.get("afterSaler") or record.get("raw_aftersaler"))
        resolved = resolve_final_aftersaler(product, region, raw, snapshot)
        key = (product, region, raw, resolved["final_aftersaler"], resolved["aftersaler_source"], resolved["mapping_conflict"])
        item = combinations.setdefault(key, {
            "product_big_sort_three": product,
            "region": region,
            "raw_aftersaler": raw,
            "final_aftersaler": resolved["final_aftersaler"],
            "source": resolved["aftersaler_source"],
            "conflict": resolved["mapping_conflict"],
            "count": 0,
        })
        item["count"] += 1
        if resolved["mapping_conflict"]:
            conflicts += 1
        elif resolved["aftersaler_source"] == "mapping":
            matched += 1
        else:
            fallback += 1
    items = sorted(combinations.values(), key=lambda item: (-item["count"], item["product_big_sort_three"], item["region"]))
    total = matched + fallback + conflicts
    return {
        "mapping": {k: snapshot.get(k) for k in ("available", "version_id", "effective_month", "revision", "reason")},
        "total_records": total,
        "matched_records": matched,
        "fallback_records": fallback,
        "conflict_records": conflicts,
        "match_rate": round(matched / total * 100, 1) if total else 0.0,
        "items": items,
    }

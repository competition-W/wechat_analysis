from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from loguru import logger
import time

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])

def _call(fn, **kw):
    started = time.perf_counter()
    logger.info("dashboard.query.start operation={} params={}", fn.__name__, kw or "-")
    try:
        data = fn(**kw)
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "dashboard.query.end operation={} elapsed_ms={:.1f} result_type={}",
            fn.__name__, elapsed_ms, type(data).__name__,
        )
        return {"code": 0, "message": "success", "data": data}
    except Exception as e:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.exception(
            "dashboard.query.error operation={} elapsed_ms={:.1f} error={}",
            fn.__name__, elapsed_ms, e,
        )
        raise HTTPException(status_code=500, detail=str(e))

# ---- Existing endpoints ----

@router.get("/summary")
def get_dashboard_summary(date: Optional[str] = Query(None)):
    from services.db_dashboard import get_summary
    return _call(get_summary, date_str=date)

@router.get("/groups")
def get_groups(date: Optional[str] = None, page: int = 1,
                     page_size: int = 20, search: Optional[str] = None,
                     sort_by: str = "messageToDayCount", sort_order: str = "DESC"):
    from services.db_dashboard import get_groups
    return _call(get_groups, date_str=date, page=page, page_size=page_size,
                 search=search, sort_by=sort_by, sort_order=sort_order)

@router.get("/groups/{group_id}")
def get_group_detail(group_id: int):
    from services.db_dashboard import get_group_detail
    data = get_group_detail(group_id)
    if not data:
        raise HTTPException(status_code=404, detail="Group not found")
    return {"code": 0, "message": "success", "data": data}

@router.get("/timeseries")
def get_timeseries(days: int = Query(30, ge=1, le=365)):
    from services.db_dashboard import get_timeseries, get_sentiment_timeline
    ts = get_timeseries(days)
    st = get_sentiment_timeline(days)
    return {"code": 0, "message": "success", "data": {"overview": ts, "sentiment": st}}

@router.get("/today")
def get_today():
    import datetime
    return get_dashboard_summary(date=datetime.date.today().isoformat())

# ---- M00-M03 ----

@router.get("/full-summary")
def get_full_summary():
    from services.db_dashboard import get_full_summary
    return _call(get_full_summary)

# ---- M04-M11 LIMS ----

@router.get("/after-saler-distribution")
def after_saler_distribution():
    from services.db_dashboard import get_after_saler_distribution
    return _call(get_after_saler_distribution)

@router.get("/active-duration")
def active_duration():
    """M05: Active duration from qxChat msgtime"""
    from services.qxchat_helper import compute_active_duration
    return _call(compute_active_duration)

@router.get("/product-categories")
def product_categories():
    from services.db_dashboard import get_product_category_hierarchy
    return _call(get_product_category_hierarchy)

@router.get("/key-accounts")
def key_accounts():
    from services.db_dashboard import get_key_account_hierarchy
    return _call(get_key_account_hierarchy)

@router.get("/org-distribution")
def org_distribution():
    from services.db_dashboard import get_org_distribution
    return _call(get_org_distribution)

@router.get("/org-salesperson")
def org_salesperson():
    from services.db_dashboard import get_org_salesperson
    return _call(get_org_salesperson)

@router.get("/org-product-category")
def org_product_category():
    from services.db_dashboard import get_org_product_category
    return _call(get_org_product_category)

@router.get("/org-after-saler")
def org_after_saler():
    from services.db_dashboard import get_org_after_saler
    return _call(get_org_after_saler)

# ---- M12-M16 Chat Analysis ----

@router.get("/message-trend")
def message_trend(days: int = Query(30, ge=1, le=365)):
    from services.qxchat_helper import get_time_data
    data = get_time_data()
    trend = []
    if data.get("groups"):
        total_days = {}
        for g in data["groups"].values():
            for d, c in g.get("days", {}).items():
                total_days[d] = total_days.get(d, 0) + c
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        trend = [{"date": d, "count": c} for d, c in sorted(total_days.items())
                 if d >= cutoff]
    return {"code": 0, "message": "success", "data": {"trend": trend, "total_days": len(trend)}}

@router.get("/time-distribution")
def time_distribution(days: int = Query(30, ge=1, le=365)):
    """M13: Message time distribution from qxChat msgtime"""
    from services.qxchat_helper import compute_time_distribution
    return _call(compute_time_distribution, days=days)

@router.get("/sentiment-summary")
def sentiment_summary():
    from services.db_dashboard import get_sentiment_analysis_summary
    return _call(get_sentiment_analysis_summary)

@router.get("/high-freq")
def high_freq(limit: int = Query(20, ge=5, le=100)):
    from services.db_dashboard import get_high_freq_summary
    return _call(get_high_freq_summary, limit=limit)

@router.get("/unanswered-summary")
def unanswered_summary():
    from services.db_dashboard import get_unanswered_summary
    return _call(get_unanswered_summary)

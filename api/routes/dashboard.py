import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from services import db_dashboard


router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


def _success(operation: str, fn, *args, **kwargs):
    started = time.perf_counter()
    logger.info("dashboard.operation.start operation={}", operation)
    try:
        data = fn(*args, **kwargs)
        logger.info(
            "dashboard.operation.end operation={} elapsed_ms={:.1f}",
            operation,
            (time.perf_counter() - started) * 1000,
        )
        return {"code": 0, "message": "success", "data": data}
    except ValueError as exc:
        logger.warning("dashboard.operation.invalid operation={} error={}", operation, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("dashboard.operation.error operation={} error={}", operation, exc)
        raise HTTPException(status_code=500, detail="dashboard query failed") from exc


@router.get("/overview")
def overview(
    period: str = Query("month", pattern="^(today|daily|week|weekly|month|monthly|quarter|quarterly|year|yearly|custom)$"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    refresh: bool = Query(False),
    region: str = Query("", max_length=100),
    aftersaler: str = Query("", max_length=100),
    category: str = Query("", max_length=100),
    key_account: str = Query("", max_length=100),
):
    return _success(
        "overview", db_dashboard.get_overview,
        period=period, start_date=start_date, end_date=end_date, force_refresh=refresh,
        region=region, aftersaler=aftersaler, category=category, key_account=key_account,
    )


@router.get("/evidence")
def evidence(
    metric: str = Query(..., pattern="^(unanswered|customer_negative|employee_negative|highfreq)$"),
    period: str = Query("month", pattern="^(today|daily|week|weekly|month|monthly|quarter|quarterly|year|yearly|custom)$"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None, max_length=100),
    search: Optional[str] = Query(None, max_length=100),
    region: str = Query("", max_length=100),
    aftersaler: str = Query("", max_length=100),
    category: str = Query("", max_length=100),
    key_account: str = Query("", max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    return _success(
        "evidence", db_dashboard.get_evidence,
        metric=metric, period=period, start_date=start_date, end_date=end_date,
        keyword=keyword, search=search, region=region, aftersaler=aftersaler,
        category=category, key_account=key_account, page=page, page_size=page_size,
    )


@router.post("/cache/clear")
def clear_cache():
    db_dashboard.clear_cache()
    return {"code": 0, "message": "success", "data": {"cleared": True}}


# Compatibility endpoints retained for existing integrations.
@router.get("/summary")
def summary(date: Optional[str] = Query(None)):
    return _success("summary", db_dashboard.get_summary, date)


@router.get("/today")
def today():
    return _success("today", db_dashboard.get_overview, period="today")


@router.get("/full-summary")
def full_summary():
    return _success("full_summary", db_dashboard.get_full_summary)


@router.get("/after-saler-distribution")
def after_saler_distribution():
    return _success("after_saler_distribution", db_dashboard.get_after_saler_distribution)


@router.get("/active-duration")
def active_duration():
    return _success("active_duration", lambda: db_dashboard.get_overview("year")["communication"]["active_duration"])


@router.get("/product-categories")
def product_categories():
    return _success("product_categories", db_dashboard.get_product_category_hierarchy)


@router.get("/key-accounts")
def key_accounts():
    return _success("key_accounts", db_dashboard.get_key_account_hierarchy)


@router.get("/org-distribution")
def org_distribution():
    return _success("org_distribution", db_dashboard.get_org_distribution)


@router.get("/org-salesperson")
def org_salesperson():
    return _success("org_salesperson", lambda: {"items": db_dashboard.get_overview("year")["cross_analysis"]["region_sales"]})


@router.get("/org-product-category")
def org_product_category():
    return _success("org_product_category", lambda: {"items": db_dashboard.get_overview("year")["cross_analysis"]["region_product"]})


@router.get("/org-after-saler")
def org_after_saler():
    return _success("org_after_saler", lambda: {"items": db_dashboard.get_overview("year")["cross_analysis"]["region_after"]})


@router.get("/message-trend")
def message_trend():
    return _success("message_trend", lambda: {"trend": db_dashboard.get_overview("month")["communication"]["trend"]})


@router.get("/time-distribution")
def time_distribution():
    return _success(
        "time_distribution",
        lambda: {"days": db_dashboard.get_overview("month")["communication"]["trend"], "note": "数据库仅保存每日分析结果，未保存小时粒度。"},
    )


@router.get("/sentiment-summary")
def sentiment_summary():
    return _success("sentiment_summary", db_dashboard.get_sentiment_analysis_summary)


@router.get("/high-freq")
def high_freq(limit: int = Query(20, ge=5, le=100)):
    return _success("high_freq", db_dashboard.get_high_freq_summary, limit)


@router.get("/verify")
def verify(
    period: str = Query("month", pattern="^(today|daily|week|weekly|month|monthly|quarter|quarterly|year|yearly|custom)$"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    return _success("verify", db_dashboard.get_verification_stats, period=period, start_date=start_date, end_date=end_date)


@router.get("/export")
def export_data(
    period: str = Query("month", pattern="^(today|daily|week|weekly|month|monthly|quarter|quarterly|year|yearly|custom)$"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    region: str = Query("", max_length=100),
    aftersaler: str = Query("", max_length=100),
    category: str = Query("", max_length=100),
    key_account: str = Query("", max_length=100),
):
    from fastapi.responses import PlainTextResponse
    csv_content = db_dashboard.get_export_csv(
        period=period, start_date=start_date, end_date=end_date,
        region=region, aftersaler=aftersaler, category=category, key_account=key_account,
    )
    filename = f"dashboard_export_{period}_{start_date or ''}_{end_date or ''}.csv"
    return PlainTextResponse(
        content=csv_content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

@router.get("/unanswered-summary")
def unanswered_summary():
    return _success("unanswered_summary", db_dashboard.get_unanswered_summary)

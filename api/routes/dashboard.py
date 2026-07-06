from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from loguru import logger

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("/summary")
async def get_dashboard_summary(
    date: Optional[str] = Query(None, description="Date (YYYY-MM-DD), default all")):
    """Get dashboard summary statistics"""
    from services.db_dashboard import get_summary
    try:
        data = get_summary(date)
        return {'code': 0, 'message': 'success', 'data': data}
    except Exception as e:
        logger.error(f'Dashboard summary error: {e}')
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/groups")
async def get_groups(
    date: Optional[str] = Query(None, description='Date YYYY-MM-DD'),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, description='Search group name'),
    sort_by: str = Query("messageToDayCount"),
    sort_order: str = Query("DESC")):
    """Get paginated group list"""
    from services.db_dashboard import get_groups
    try:
        data = get_groups(date, page, page_size, search, sort_by, sort_order)
        return {'code': 0, 'message': 'success', 'data': data}
    except Exception as e:
        logger.error(f'Dashboard groups error: {e}')
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/groups/{group_id}")
async def get_group_detail(group_id: int):
    """Get group analysis detail"""
    from services.db_dashboard import get_group_detail
    try:
        data = get_group_detail(group_id)
        if not data:
            raise HTTPException(status_code=404, detail='Group not found')
        return {'code': 0, 'message': 'success', 'data': data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'Group detail error: {e}')
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/timeseries")
async def get_timeseries(
    days: int = Query(30, ge=1, le=365, description='Number of days')):
    """Get time series data for charts"""
    from services.db_dashboard import get_timeseries, get_sentiment_timeline
    try:
        ts = get_timeseries(days)
        st = get_sentiment_timeline(days)
        return {'code': 0, 'message': 'success', 'data': {'overview': ts, 'sentiment': st}}
    except Exception as e:
        logger.error(f'Timeseries error: {e}')
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/today")
async def get_today():
    """Get today dashboard data"""
    import datetime
    return await get_dashboard_summary(date=datetime.date.today().isoformat())
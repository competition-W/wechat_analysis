from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
from loguru import logger
from datetime import datetime
import httpx
import os

router = APIRouter(prefix="/api/v1/visualize", tags=["visualize"])


# ========== Chart Proxy (AntV API) ==========

class ChartRequest(BaseModel):
    type: str
    data: List[Dict[str, Any]]
    title: Optional[str] = ""
    theme: Optional[str] = "default"
    width: Optional[int] = 600
    height: Optional[int] = 400
    stack: Optional[bool] = None
    group: Optional[bool] = None
    innerRadius: Optional[float] = None


@router.post("/chart")
async def generate_chart(request: ChartRequest):
    """???? AntV ?? API??????? URL"""
    payload: Dict[str, Any] = {
        "type": request.type,
        "source": "chart-visualization-skills",
        "data": request.data,
        "title": request.title,
        "theme": request.theme,
        "width": request.width,
        "height": request.height,
    }
    if request.stack is not None:
        payload["stack"] = request.stack
    if request.group is not None:
        payload["group"] = request.group
    if request.innerRadius is not None:
        payload["innerRadius"] = request.innerRadius

    logger.info(f"AntV ????: type={request.type}, title={request.title}, data_size={len(request.data)}")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://antv-studio.alipay.com/api/gpt-vis",
                json=payload,
            )
        result = resp.json()
        if result.get("success"):
            logger.info(f"AntV ??????")
            return {
                "code": 0,
                "message": "success",
                "data": {
                    "image_url": result["resultObj"],
                    "chart_type": request.type,
                    "title": request.title,
                },
            }
        else:
            logger.error(f"AntV ??????: {result}")
            raise HTTPException(status_code=502, detail="AntV ??????")
    except httpx.TimeoutException:
        logger.error("AntV API ??")
        raise HTTPException(status_code=504, detail="AntV API ??")
    except Exception as e:
        logger.error(f"AntV API ????: {e}")
        raise HTTPException(status_code=502, detail=f"??????: {e}")


# ========== Report Data Provider (JSON for charting) ==========

@router.post("/generate")
async def visualize_generate(
    type: str = Query("daily", description="????: daily/weekly/monthly/quarterly/yearly"),
    date: Optional[str] = Query(None, description="???? YYYY-MM-DD"),
    fresh: bool = Query(False, description="????????"),
):
    """????????? JSON ????????????"""
    from services.data_archiver import DataArchiver, compute_time_range, filter_groups_by_time
    from services.report_aggregator import aggregate_report
    from services.report_generator import generate_and_save_report
    from config.settings import settings

    archiver = DataArchiver()

    if fresh or not archiver.list_snapshots():
        logger.info("??????...")
        from services.data_collector import DataCollector
        collector = DataCollector()
        try:
            merged_groups, all_records = collector.collect_all()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"????????: {e}")
        finally:
            collector.close()
        archiver._save_snapshot(merged_groups, all_records)
    else:
        logger.info("???????...")
        result = archiver.load_and_filter(type, date)
        merged_groups, all_records, time_range = result
        if not merged_groups:
            raise HTTPException(status_code=404, detail="?????")
        report_data = aggregate_report(merged_groups, all_records)
        report_data["report_time_range"] = time_range
        output_dir = settings.REPORT_OUTPUT_DIR
        filepath = generate_and_save_report(report_data, output_dir)
        return {
            "code": 0,
            "message": "success",
            "data": {
                "report_file": os.path.basename(filepath),
                "report_path": filepath,
                "report_data": report_data,
                "time_range": time_range,
            },
        }

    time_range = compute_time_range(type, date)
    logger.info(f"????: {type}, ????: {time_range['label']}")

    if not merged_groups and not all_records:
        raise HTTPException(status_code=404, detail="????????")

    if time_range.get("start"):
        merged_groups = filter_groups_by_time(merged_groups, time_range)
        if not merged_groups:
            raise HTTPException(status_code=404, detail=f"????????: {time_range['label']}")

    try:
        report_data = aggregate_report(merged_groups, all_records)
        report_data["report_time_range"] = time_range
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"??????: {e}")

    output_dir = settings.REPORT_OUTPUT_DIR
    try:
        filepath = generate_and_save_report(report_data, output_dir)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"??????: {e}")

    return {
        "code": 0,
        "message": "success",
        "data": {
            "report_file": os.path.basename(filepath),
            "report_path": filepath,
            "report_data": report_data,
            "generated_at": datetime.now().isoformat(),
            "report_type": type,
            "time_range": time_range,
        },
    }

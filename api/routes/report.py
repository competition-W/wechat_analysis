#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
报告生成 API 路由 - v2 支持时间维度报表和数据归档
"""

import os
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from datetime import datetime

from config.settings import settings

router = APIRouter(prefix="/api/v1/report", tags=["report"])


@router.post("/archive")
async def archive_data():
    """
    手动触发数据归档：拉取当日 qxChat + LIMS 数据并保存到 archive/ 目录。
    建议通过 cron 每日执行一次。
    """
    try:
        from services.data_archiver import DataArchiver
    except ImportError:
        raise HTTPException(status_code=500, detail="归档模块不可用")

    archiver = DataArchiver()
    filepath = archiver.archive_today()
    if not filepath:
        raise HTTPException(status_code=502, detail="数据归档失败，请检查 API 连通性")

    return {
        "code": 0,
        "message": "success",
        "data": {
            "archive_file": os.path.basename(filepath),
            "archive_path": filepath,
            "archived_at": datetime.now().isoformat(),
        },
    }


@router.post("/generate")
async def generate_report(
    type: str = Query("daily", description="报表类型: daily/weekly/monthly/quarterly/yearly"),
    date: Optional[str] = Query(None, description="参考日期 YYYY-MM-DD，默认今天"),
    fresh: bool = Query(False, description="是否忽略缓存，重新拉取数据"),
):
    """
    生成时间维度数据统计分析报告。
    
    流程：
    1. 如有缓存且非 fresh，从归档加载数据；否则从 qxChat+LIMS 拉取
    2. 按时间范围过滤消息
    3. 聚合分析
    4. 生成 HTML 可视化报告
    
    type 参数说明:
    - daily:   当日报表
    - weekly:  最近 7 天
    - monthly: 最近 30 天
    - quarterly: 最近 90 天
    - yearly:  最近 365 天
    """
    from services.data_archiver import DataArchiver, compute_time_range, filter_groups_by_time
    from services.report_aggregator import aggregate_report
    from services.report_generator import generate_and_save_report

    # 1. 获取数据：归档或实时拉取
    archiver = DataArchiver()
    
    if fresh or not archiver.list_snapshots():
        logger.info("实时拉取数据...")
        from services.data_collector import DataCollector
        collector = DataCollector()
        try:
            merged_groups, all_records = collector.collect_all()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"数据接口调用失败: {e}")
        finally:
            collector.close()
        # 归档以备后续使用
        archiver._save_snapshot(merged_groups, all_records)
    else:
        logger.info("从归档加载数据...")
        result = archiver.load_and_filter(type, date)
        merged_groups, all_records, time_range = result
        if not merged_groups:
            raise HTTPException(status_code=404, detail="无可用数据")
        
        # 已经过滤过了，直接聚合
        report_data = aggregate_report(merged_groups, all_records)
        report_data["report_time_range"] = time_range
        
        # 生成 HTML
        output_dir = settings.REPORT_OUTPUT_DIR
        try:
            filepath = generate_and_save_report(report_data, output_dir)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"报告生成失败: {e}")

        return {
            "code": 0,
            "message": "success",
            "data": {
                "report_file": os.path.basename(filepath),
                "report_path": filepath,
                "generated_at": datetime.now().isoformat(),
                "report_type": type,
                "time_range": time_range,
                "total_groups": report_data.get("total_groups", 0),
                "total_lims_records": report_data.get("total_lims_records", 0),
                "summary": report_data.get("summary", {}),
            },
        }

    # fresh 或首次运行：完整流程（含时间范围过滤）
    time_range = compute_time_range(type, date)
    logger.info(f"报表类型: {type}, 时间范围: {time_range['label']}")

    if not merged_groups and not all_records:
        raise HTTPException(status_code=404, detail="未获取到任何数据")

    # 按时间范围过滤消息
    if time_range.get("start"):
        merged_groups = filter_groups_by_time(merged_groups, time_range)
        if not merged_groups:
            raise HTTPException(status_code=404, detail=f"时间范围内无数据: {time_range['label']}")

    # 聚合分析
    try:
        report_data = aggregate_report(merged_groups, all_records)
        report_data["report_time_range"] = time_range
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"报告聚合失败: {e}")

    # 生成 HTML
    output_dir = settings.REPORT_OUTPUT_DIR
    try:
        filepath = generate_and_save_report(report_data, output_dir)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"报告生成失败: {e}")

    return {
        "code": 0,
        "message": "success",
        "data": {
            "report_file": os.path.basename(filepath),
            "report_path": filepath,
            "generated_at": datetime.now().isoformat(),
            "report_type": type,
            "time_range": time_range,
            "total_groups": report_data.get("total_groups", 0),
            "total_lims_records": report_data.get("total_lims_records", 0),
            "summary": report_data.get("summary", {}),
        },
    }


@router.get("/view/{filename}")
async def view_report(filename: str):
    """查看已生成的报告 HTML 文件"""
    output_dir = settings.REPORT_OUTPUT_DIR
    filepath = os.path.join(output_dir, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="报告文件不存在")
    from fastapi.responses import HTMLResponse
    with open(filepath, "r", encoding="utf-8") as f:
        html = f.read()
    return HTMLResponse(content=html)


@router.get("/list")
async def list_reports():
    """列出所有已生成的报告"""
    output_dir = settings.REPORT_OUTPUT_DIR
    if not os.path.exists(output_dir):
        return {"code": 0, "data": {"files": []}}
    files = sorted(
        [f for f in os.listdir(output_dir) if f.endswith(".html")],
        reverse=True,
    )
    return {
        "code": 0,
        "data": {
            "files": [
                {
                    "filename": f,
                    "path": os.path.join(output_dir, f),
                    "size": os.path.getsize(os.path.join(output_dir, f)),
                }
                for f in files
            ]
        },
    }


@router.get("/archive/list")
async def list_archives():
    """列出所有已归档的数据快照"""
    from services.data_archiver import DataArchiver
    archiver = DataArchiver()
    dates = archiver.list_snapshots()
    return {
        "code": 0,
        "data": {
            "dates": dates,
            "count": len(dates),
        },
    }

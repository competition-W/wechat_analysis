#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据归档器：将每日的群聊+LIMS 数据持久化到 JSON 文件，
支持按时间范围（日/周/月/季/年）加载和过滤，为多维度报表提供数据源。
"""

import json
import os
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any, Tuple, Callable
from loguru import logger


def _parse_time(time_str: str) -> Optional[datetime]:
    """解析时间字符串"""
    if not time_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(time_str, fmt)
        except (ValueError, TypeError):
            continue
    return None


def compute_time_range(report_type: str, ref_date: Optional[str] = None) -> Dict[str, str]:
    """
    根据报表类型和参考日期计算时间范围。
    
    Args:
        report_type: daily, weekly, monthly, quarterly, yearly
        ref_date: 参考日期 YYYY-MM-DD，默认今天
    
    Returns:
        {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "label": "描述"}
    """
    if ref_date:
        end_dt = datetime.strptime(ref_date, "%Y-%m-%d").date()
    else:
        end_dt = date.today()
    
    end_str = end_dt.isoformat()
    
    if report_type == "daily":
        start_dt = end_dt
        label = end_str
    elif report_type == "weekly":
        start_dt = end_dt - timedelta(days=6)
        label = f"{start_dt.isoformat()} ~ {end_str} (本周)"
    elif report_type == "monthly":
        start_dt = end_dt - timedelta(days=29)
        label = f"{start_dt.isoformat()} ~ {end_str} (本月)"
    elif report_type == "quarterly":
        start_dt = end_dt - timedelta(days=89)
        label = f"{start_dt.isoformat()} ~ {end_str} (本季度)"
    elif report_type == "yearly":
        start_dt = end_dt - timedelta(days=364)
        label = f"{start_dt.isoformat()} ~ {end_str} (本年)"
    else:
        start_dt = end_dt
        label = end_str
    
    return {
        "start": start_dt.isoformat(),
        "end": end_str,
        "label": label,
        "type": report_type,
    }


def filter_messages_by_time(messages: List[Dict], time_range: Dict[str, str]) -> List[Dict]:
    """
    按时间范围过滤消息。
    
    Args:
        messages: qxChat 消息列表，每条应有 msgtime 字段
        time_range: {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}
    
    Returns:
        过滤后的消息列表
    """
    if not time_range:
        return messages
    
    start_str = time_range.get("start", "")
    end_str = time_range.get("end", "")
    
    start_dt = datetime.strptime(start_str, "%Y-%m-%d") if start_str else None
    end_dt = datetime.strptime(end_str, "%Y-%m-%d") + timedelta(days=1) if end_str else None
    
    if not start_dt and not end_dt:
        return messages
    
    filtered = []
    for msg in messages:
        msg_time = _parse_time(msg.get("msgtime", ""))
        if not msg_time:
            continue
        if start_dt and msg_time < start_dt:
            continue
        if end_dt and msg_time >= end_dt:
            continue
        filtered.append(msg)
    
    return filtered


def filter_groups_by_time(
    merged_groups: List[Dict],
    time_range: Dict[str, str]
) -> List[Dict]:
    """
    按时间范围过滤合并后的群组数据。
    过滤每个群中的消息，并更新群级别的时间元数据。
    """
    if not time_range or not time_range.get("start"):
        return merged_groups
    
    filtered_groups = []
    for group in merged_groups:
        original_msgs = group.get("messages", [])
        filtered_msgs = filter_messages_by_time(original_msgs, time_range)
        
        if not filtered_msgs:
            continue  # 跳过没有消息的群
        
        # 更新群级别元数据
        msg_times = sorted(
            [_parse_time(m.get("msgtime", "")) for m in filtered_msgs if _parse_time(m.get("msgtime", ""))]
        )
        
        new_group = dict(group)
        new_group["messages"] = filtered_msgs
        new_group["message_count"] = len(filtered_msgs)
        if msg_times:
            new_group["first_msg_time"] = msg_times[0].strftime("%Y-%m-%d %H:%M:%S")
            new_group["last_msg_time"] = msg_times[-1].strftime("%Y-%m-%d %H:%M:%S")
        
        filtered_groups.append(new_group)
    
    return filtered_groups


class DataArchiver:
    """
    数据归档器。
    将每日的数据快照保存到 JSON 文件，支持按日期加载。
    归档文件位置: archive/YYYY-MM-DD.json
    """
    
    def __init__(self, archive_dir: str = "./archive"):
        self.archive_dir = archive_dir
    
    def archive_today(self) -> Optional[str]:
        """拉取当日数据并归档，返回文件路径"""
        from services.data_collector import DataCollector
        
        collector = DataCollector()
        try:
            logger.info("开始归档当日数据...")
            groups, records = collector.collect_all()
            return self._save_snapshot(groups, records)
        except Exception as e:
            logger.error(f"归档失败: {e}")
            return None
        finally:
            collector.close()
    
    def _save_snapshot(
        self, merged_groups: List[Dict], all_records: List
    ) -> str:
        """保存数据快照到 JSON 文件"""
        os.makedirs(self.archive_dir, exist_ok=True)
        today = date.today().isoformat()
        
        # 将 LimsRecord 对象转为 dict
        records_dicts = []
        for r in all_records:
            if hasattr(r, "__dict__"):
                records_dicts.append(r.__dict__)
            elif isinstance(r, dict):
                records_dicts.append(r)
            else:
                records_dicts.append({"project_code": str(r)})
        
        snapshot = {
            "snapshot_date": today,
            "created_at": datetime.now().isoformat(),
            "groups_count": len(merged_groups),
            "records_count": len(records_dicts),
            "groups": merged_groups,
            "records": records_dicts,
        }
        
        filename = f"{today}.json"
        filepath = os.path.join(self.archive_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        
        logger.info(
            f"归档完成: {filepath} "
            f"({len(merged_groups)} 个群, {len(records_dicts)} 条记录)"
        )
        return filepath
    
    def load_latest(self) -> Optional[Tuple[List[Dict], List[Dict]]]:
        """加载最新的数据快照"""
        dates = self.list_snapshots()
        if not dates:
            logger.warning("无可用归档")
            return None
        return self.load_snapshot(dates[0])
    
    def load_snapshot(
        self, date_str: str
    ) -> Optional[Tuple[List[Dict], List[Dict]]]:
        """加载指定日期的数据快照"""
        filepath = os.path.join(self.archive_dir, f"{date_str}.json")
        if not os.path.exists(filepath):
            logger.warning(f"归档不存在: {filepath}")
            return (None, None)
        
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        return data.get("groups", []), data.get("records", [])
    
    def list_snapshots(self) -> List[str]:
        """列出所有可用归档日期（倒序）"""
        if not os.path.exists(self.archive_dir):
            return []
        files = sorted(
            [
                f.replace(".json", "")
                for f in os.listdir(self.archive_dir)
                if f.endswith(".json")
            ],
            reverse=True,
        )
        return files
    
    def load_and_filter(
        self,
        report_type: str = "daily",
        ref_date: Optional[str] = None,
    ) -> Tuple[Optional[List[Dict]], Optional[List[Dict]], Dict[str, str]]:
        """
        加载归档并按时间范围过滤数据。
        
        Args:
            report_type: daily/weekly/monthly/quarterly/yearly
            ref_date: 参考日期 YYYY-MM-DD
        
        Returns:
            (filtered_groups, all_records, time_range)
        """
        time_range = compute_time_range(report_type, ref_date)
        logger.info(f"报表类型: {report_type}, 时间范围: {time_range['label']}")
        
        result = self.load_latest()
        if not result or not result[0]:
            logger.warning("无归档数据，尝试实时拉取...")
            self.archive_today()
            result = self.load_latest()
            if not result or not result[0]:
                return (None, None, time_range)
        
        groups, records = result
        filtered = filter_groups_by_time(groups, time_range)
        
        logger.info(
            f"过滤后: {len(filtered)} 个群 (原 {len(groups)} 个), "
            f"时间范围: {time_range['start']} ~ {time_range['end']}"
        )
        
        return (filtered, records, time_range)

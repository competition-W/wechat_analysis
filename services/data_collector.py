#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据采集层：从 qxChat API 拉取群聊消息，从 LIMS API 拉取售后数据，
计算派生字段 (finalAfterSaler / salesPerson / keyAccount 判定) 并按项目号关联。
"""

import re
import json
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass, field
from loguru import logger
import httpx


@dataclass
class QxChatGroup:
    """从 qxChat API 拉取并分组后的群信息"""
    room_id: str
    room_name: str
    messages: List[Dict[str, Any]] = field(default_factory=list)
    first_msg_time: Optional[str] = None
    last_msg_time: Optional[str] = None
    project_code: Optional[str] = None  # 从群名提取的第一个项目号
    project_codes: Optional[List[str]] = None  # 从群名提取的所有项目号


@dataclass
class LimsRecord:
    """单条 LIMS 售后记录，含计算后的派生字段"""
    project_code: str
    afterSaler: str = ""
    finalAfterSaler: str = ""
    salesPerson: str = ""
    is_key_account: bool = False
    customerName: str = ""
    orgName: str = ""
    productBigSortOne: str = ""
    productBigSortTwo: str = ""
    productBigSortThree: str = ""
    productName: str = ""
    saleName: str = ""
    keyAccount: str = ""
    members: str = ""
    activeDay: int = 0
    assignmentUser: str = ""
    dataAnalyst: str = ""
    groupId: str = ""
    name: str = ""
    sampleNumber: int = 0
    startTime: str = ""
    endTime: str = ""
    workUnit: str = ""
    isAnalysis: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api_response(cls, item: dict) -> "LimsRecord":
        """从 LIMS API 响应构造，同时计算派生字段"""
        after_saler = item.get("afterSaler", "") or ""
        members_raw = item.get("members", "") or ""

        member_names = _parse_members_field(members_raw)

        if after_saler and after_saler in member_names:
            final_after_saler = after_saler
        else:
            final_after_saler = ""

        sales_person = final_after_saler if final_after_saler else after_saler

        key_account_val = item.get("keyAccount", "") or ""
        is_key = bool(key_account_val.strip())

        record = cls(
            project_code=item.get("projectCode", "") or "",
            afterSaler=after_saler,
            finalAfterSaler=final_after_saler,
            salesPerson=sales_person,
            is_key_account=is_key,
            customerName=item.get("customerName", "") or "",
            orgName=item.get("orgName", "") or "",
            productBigSortOne=item.get("productBigSortOne", "") or "",
            productBigSortTwo=item.get("productBigSortTwo", "") or "",
            productBigSortThree=item.get("productBigSortThree", "") or "",
            productName=item.get("productName", "") or "",
            saleName=item.get("saleName", "") or "",
            keyAccount=key_account_val,
            members=members_raw,
            activeDay=item.get("activeDay", 0) or 0,
            assignmentUser=item.get("assignmentUser", "") or "",
            dataAnalyst=item.get("dataAnalyst", "") or "",
            groupId=item.get("groupId", "") or "",
            name=item.get("name", "") or "",
            sampleNumber=item.get("sampleNumber", 0) or 0,
            startTime=item.get("startTime", "") or "",
            endTime=item.get("endTime", "") or "",
            workUnit=item.get("workUnit", "") or "",
            isAnalysis=item.get("isAnalysis", "") or "",
            raw=item,
        )
        return record


def _parse_members_field(members_str: str) -> List[str]:
    """解析 LIMS 记录中的 members 字段（JSON 数组或逗号分隔）"""
    if not members_str:
        return []
    members_str = members_str.strip()
    try:
        parsed = json.loads(members_str)
        if isinstance(parsed, list):
            return [str(m).strip() for m in parsed if m]
        return []
    except (json.JSONDecodeError, TypeError):
        pass
    return [m.strip() for m in members_str.split(",") if m.strip()]


def extract_project_codes(room_name: str) -> List[str]:
    """从群聊名称中提取所有项目编号 LC-XXXX"""
    if not room_name:
        return []
    return re.findall(r"LC-[A-Z]+\d+", room_name)


def extract_project_code(room_name: str) -> Optional[str]:
    """从群聊名称中提取第一个项目编号"""
    if not room_name:
        return None
    codes = extract_project_codes(room_name)
    return codes[0] if codes else None


class DataCollector:
    """数据采集器"""

    def __init__(
        self,
        qxchat_url: Optional[str] = None,
        lims_base_url: Optional[str] = None,
        lims_path: Optional[str] = None,
        timeout: int = 30,
    ):
        from config.settings import settings
        self.qxchat_url = qxchat_url or settings.JAVA_DATA_SOURCE_URL
        self.lims_base_url = lims_base_url or settings.LIMS_API_URL
        self.lims_path = lims_path or settings.LIMS_BASE_DATA_PATH
        self.timeout = timeout
        self.http_client = httpx.Client(timeout=timeout)

    def close(self):
        self.http_client.close()

    def fetch_qxchat_data(self) -> List[QxChatGroup]:
        """从 qxChat API 获取全量消息，按 roomid 分组"""
        logger.info(f"开始拉取 qxChat 数据: {self.qxchat_url}")
        try:
            resp = self.http_client.get(self.qxchat_url)
            resp.raise_for_status()
            raw = resp.json()
        except Exception as e:
            logger.error(f"拉取 qxChat 数据失败: {e}")
            return []

        messages = raw.get("data", [])
        if not messages:
            logger.warning("qxChat 接口返回 data 为空")
            return []
        logger.info(f"qxChat 返回 {len(messages)} 条消息")

        groups_dict: Dict[str, QxChatGroup] = {}
        for msg in messages:
            room_id = msg.get("roomid", "") or "unknown"
            if room_id not in groups_dict:
                room_name = (
                    msg.get("re_truename")
                    or msg.get("roomname")
                    or f"群-{room_id[:8]}"
                )
                groups_dict[room_id] = QxChatGroup(
                    room_id=room_id, room_name=room_name,
                )
            groups_dict[room_id].messages.append(msg)

            msg_time = msg.get("msgtime", "")
            group = groups_dict[room_id]
            if msg_time:
                if not group.first_msg_time or msg_time < group.first_msg_time:
                    group.first_msg_time = msg_time
                if not group.last_msg_time or msg_time > group.last_msg_time:
                    group.last_msg_time = msg_time

        for group in groups_dict.values():
            all_codes = extract_project_codes(group.room_name)
            group.project_code = all_codes[0] if all_codes else None
            group.project_codes = all_codes if all_codes else None

        groups = list(groups_dict.values())
        has_project = sum(1 for g in groups if g.project_code)
        logger.info(f"分组完成: {len(groups)} 个群, {has_project} 个包含项目编号")
        return groups

    def fetch_lims_data(self, project_codes: List[str]) -> Dict[str, List[LimsRecord]]:
        """对每个项目号调用 LIMS API"""
        url = f"{self.lims_base_url.rstrip('/')}{self.lims_path}"
        logger.info(f"开始拉取 LIMS 数据, {len(project_codes)} 个项目号")

        result: Dict[str, List[LimsRecord]] = {}
        for code in project_codes:
            if not code:
                continue
            try:
                resp = self.http_client.post(url, json=[{"projectCode": code}])
                resp.raise_for_status()
                body = resp.json()
                items = body.get("data", [])
                records = [LimsRecord.from_api_response(item) for item in items]
                if records:
                    result[code] = records
                    logger.debug(f"  {code}: {len(records)} 条记录")
                else:
                    logger.debug(f"  {code}: 无数据")
            except Exception as e:
                logger.error(f"拉取 LIMS 数据失败 (project={code}): {e}")

        total_records = sum(len(v) for v in result.values())
        logger.info(f"LIMS 拉取完成: {len(result)} 个项目, 共 {total_records} 条记录")
        return result

    def merge_data(
        self, groups: List[QxChatGroup], lims_map: Dict[str, List[LimsRecord]],
    ) -> Tuple[List[Dict], List[LimsRecord]]:
        """将群组数据与 LIMS 数据按 projectCode 关联"""
        all_records: List[LimsRecord] = []
        merged_groups: List[Dict] = []

        for group in groups:
            pcs = group.project_codes or ([group.project_code] if group.project_code else [])
            related_records = []
            for pc in pcs:
                related_records.extend(lims_map.get(pc, []))
            merged_groups.append({
                "room_id": group.room_id,
                "room_name": group.room_name,
                "project_code": group.project_code,
                "project_codes": group.project_codes,
                "first_msg_time": group.first_msg_time,
                "last_msg_time": group.last_msg_time,
                "message_count": len(group.messages),
                "messages": group.messages,
                "lims_records": [r.raw for r in related_records],
                "lims_objects": related_records,
            })
            all_records.extend(related_records)

        all_qx_codes = {g.project_code for g in groups if g.project_code}
        for code, records in lims_map.items():
            if code not in all_qx_codes:
                all_records.extend(records)

        logger.info(
            f"数据合并完成: {len(merged_groups)} 个群, "
            f"共 {len(all_records)} 条 LIMS 记录"
        )
        return merged_groups, all_records

    def collect_all(self) -> Tuple[List[Dict], List[LimsRecord]]:
        """执行完整采集流程"""
        groups = self.fetch_qxchat_data()
        all_codes_set = set()
        for g in groups:
            if g.project_codes:
                all_codes_set.update(g.project_codes)
        project_codes = sorted(all_codes_set)
        logger.info(f"共 {len(project_codes)} 个唯一项目号")

        lims_map = self.fetch_lims_data(project_codes)

        for group in groups:
            pcs = group.project_codes or ([group.project_code] if group.project_code else [])
            for pc in pcs:
                if pc and pc not in lims_map:
                    record = LimsRecord(
                        project_code=pc,
                        orgName="", saleName="",
                        afterSaler="", finalAfterSaler="", salesPerson="",
                    )
                    lims_map[pc] = [record]

        merged_groups, all_records = self.merge_data(groups, lims_map)
        return merged_groups, all_records


def test_extract_project_codes():
    """测试提取多个项目编号"""
    cases = [
        ("xx公司-LC-P20230220041-售后", ["LC-P20230220041"]),
        ("LC-X20230220041-张三", ["LC-X20230220041"]),
        ("LC-P001-LC-X002", ["LC-P001", "LC-X002"]),
        ("测试群-12345", []),
        ("", []),
        (None, []),
        ("[LC-P20230505001] 项目群", ["LC-P20230505001"]),
    ]
    for name, expected in cases:
        r = extract_project_codes(name)
        ok = "OK" if r == expected else "FAIL"
        print(f"  {ok} codes({name!r}) -> {r!r}")


def test_extract_project_code():
    """测试提取第一个项目编号"""
    cases = [
        ("xx公司-LC-P20230220041-售后", "LC-P20230220041"),
        ("LC-X20230220041-张三", "LC-X20230220041"),
        ("LC-P001-LC-X002", "LC-P001"),
        ("测试群-12345", None),
        ("", None),
        (None, None),
        ("[LC-P20230505001] 项目群", "LC-P20230505001"),
    ]
    for name, expected in cases:
        r = extract_project_code(name)
        ok = "OK" if r == expected else "FAIL"
        print(f"  {ok} extract({name!r}) -> {r!r} (期望 {expected!r})")

def test_parse_members():
    cases = [
        ('["张三","李四","王五"]', ["张三", "李四", "王五"]),
        ("张三,李四", ["张三", "李四"]),
        ("", []),
        (None, []),
        ("张三", ["张三"]),
    ]
    for s, expected in cases:
        r = _parse_members_field(s)
        ok = "OK" if r == expected else "FAIL"
        print(f"  {ok} parse({s!r}) -> {r!r}")


def test_final_after_saler():
    cases = [
        ("张三", '["张三","李四"]', "张三", "张三"),
        ("王五", '["张三","李四"]', "", "王五"),
        ("", '["张三","李四"]', "", ""),
        ("张三", "", "", "张三"),
    ]
    for after, members_str, exp_final, exp_sales in cases:
        item = {"afterSaler": after, "members": members_str, "projectCode": "LC-P001"}
        rec = LimsRecord.from_api_response(item)
        f_ok = "OK" if rec.finalAfterSaler == exp_final else "FAIL"
        s_ok = "OK" if rec.salesPerson == exp_sales else "FAIL"
        print(f"  {f_ok} finalAfterSaler({after!r}) -> {rec.finalAfterSaler!r} (期望 {exp_final!r})")
        print(f"  {s_ok} salesPerson -> {rec.salesPerson!r} (期望 {exp_sales!r})")


if __name__ == "__main__":
    print("=== test_extract_project_codes ===")
    test_extract_project_codes()
    print()
    print("=== test_extract_project_code ===")
    test_extract_project_code()
    print()
    print("=== test_parse_members ===")
    test_parse_members()
    print()
    print("=== test_final_after_saler ===")
    test_final_after_saler()

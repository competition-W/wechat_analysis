import json
import re
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from loguru import logger


EMPLOYEE_JOBS = {"售后", "销售", "员工", "技术支持", "项目经理"}
EXTERNAL_USERID_RE = re.compile(r"^w[mo][A-Za-z0-9_-]{8,}$", re.IGNORECASE)


@dataclass
class MemberInfo:
    userid: str
    name: str = ""
    group_nickname: str = ""
    type: int = 1
    join_time: int = 0
    type_known: bool = True
    
    @property
    def is_customer(self) -> bool:
        return str(self.type) == "2"


def parse_members_payload(value) -> Dict[str, MemberInfo]:
    """Parse members from JSON text, a list, or a wrapper object."""
    if not value:
        return {}
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
    if isinstance(parsed, dict):
        parsed = parsed.get("members") or parsed.get("data") or []
    if not isinstance(parsed, list):
        return {}
    result: Dict[str, MemberInfo] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        userid = str(item.get("userid") or item.get("user_id") or "").strip()
        if not userid:
            continue
        try:
            member_type = int(item.get("type", 1))
        except (TypeError, ValueError):
            member_type = 1
        try:
            join_time = int(item.get("join_time") or 0)
        except (TypeError, ValueError):
            join_time = 0
        result[userid] = MemberInfo(
            userid=userid,
            name=str(item.get("name") or ""),
            group_nickname=str(item.get("group_nickname") or ""),
            type=member_type,
            join_time=join_time,
            type_known="type" in item and item.get("type") not in (None, ""),
        )
    return result


def infer_sender_role(
    from_userid: str,
    roomid: str = "",
    sender_job: str = "",
    sender_position: str = "",
    member: Optional[MemberInfo] = None,
) -> str:
    """Infer customer/employee without ever treating roomid as a person."""
    userid = str(from_userid or "").strip()
    if not userid or userid == str(roomid or "").strip():
        return "未知"
    if member is not None and member.type_known:
        return "客户" if member.is_customer else "员工"
    if str(sender_job or "").strip() in EMPLOYEE_JOBS:
        return "员工"
    if str(sender_position or "").strip() in EMPLOYEE_JOBS:
        return "员工"
    if EXTERNAL_USERID_RE.match(userid):
        return "客户"
    if member is not None:
        return "员工"
    # Enterprise WeChat internal accounts in the source are short aliases,
    # names, or phone numbers; external contacts use the wm/wo prefix above.
    return "员工"


def safe_sender_name(value: str, from_userid: str = "", roomid: str = "") -> str:
    """Return a display name only; internal identifiers are not person names."""
    name = str(value or "").strip()
    userid = str(from_userid or "").strip()
    room = str(roomid or "").strip()
    if not name or name in {userid, room} or EXTERNAL_USERID_RE.match(name):
        return "未知发送人"
    return name


@dataclass
class NormalizedMessage:
    msgid: str
    seq: int
    roomid: str
    from_userid: str
    sender_name: str
    sender_position: str
    sender_job: str
    sender_role: str
    text_content: str
    msgtime: str
    msgtype: str
    raw_content: str
    members: Dict[str, MemberInfo] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "msgid": self.msgid,
            "seq": self.seq,
            "roomid": self.roomid,
            "from_userid": self.from_userid,
            "sender_name": self.sender_name,
            "sender_position": self.sender_position,
            "sender_job": self.sender_job,
            "sender_role": self.sender_role,
            "text_content": self.text_content,
            "msgtime": self.msgtime,
            "msgtype": self.msgtype,
        }


class Preprocessor:
    def __init__(self):
        pass
    
    def process(self, messages: List[dict]) -> List[NormalizedMessage]:
        if not messages:
            return []
        
        normalized = []
        for msg in messages:
            try:
                processed = self._process_single(msg)
                if processed:
                    normalized.append(processed)
            except Exception as e:
                logger.warning(f"处理消息失败: {e}, msgid={msg.get('msgid')}")
                continue
        
        normalized.sort(key=lambda x: x.msgtime or "")
        return normalized
    
    def _process_single(self, msg: dict) -> Optional[NormalizedMessage]:
        members_map = self._parse_members(msg.get("members", ""))
        
        from_userid = msg.get("from", "")
        text_content = self._extract_text_content(msg.get("content", ""))
        
        sender_name = msg.get("truename", "")
        sender_position = msg.get("position", "")
        sender_job = msg.get("job", "")
        
        member = members_map.get(from_userid) if from_userid else None
        if member and not sender_name:
            sender_name = member.name or member.group_nickname
        sender_role = infer_sender_role(
            from_userid=from_userid,
            roomid=msg.get("roomid", ""),
            sender_job=sender_job,
            sender_position=sender_position,
            member=member,
        )
        sender_name = safe_sender_name(sender_name, from_userid, msg.get("roomid", ""))
        
        return NormalizedMessage(
            msgid=msg.get("msgid", ""),
            seq=msg.get("seq", 0),
            roomid=msg.get("roomid", ""),
            from_userid=from_userid,
            sender_name=sender_name,
            sender_position=sender_position,
            sender_job=sender_job,
            sender_role=sender_role,
            text_content=text_content,
            msgtime=msg.get("msgtime", ""),
            msgtype=msg.get("msgtype", "text"),
            raw_content=msg.get("content", ""),
            members=members_map,
        )
    
    def _parse_members(self, members_str: str) -> Dict[str, MemberInfo]:
        result = parse_members_payload(members_str)
        if members_str and not result:
            logger.debug("members payload did not contain usable user records")
        return result
    
    def _extract_text_content(self, content_str: str) -> str:
        if not content_str:
            return ""
        
        try:
            content_obj = json.loads(content_str)
            if isinstance(content_obj, dict):
                return content_obj.get("content", "")
            elif isinstance(content_obj, str):
                return content_obj
            return str(content_obj)
        except json.JSONDecodeError:
            return content_str

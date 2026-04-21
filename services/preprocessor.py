import json
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from loguru import logger


@dataclass
class MemberInfo:
    userid: str
    name: str = ""
    group_nickname: str = ""
    type: int = 1
    join_time: int = 0
    
    @property
    def is_customer(self) -> bool:
        return self.type == 2


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
        
        if from_userid and from_userid in members_map:
            member = members_map[from_userid]
            if not sender_name:
                sender_name = member.name
            sender_role = "客户" if member.is_customer else "员工"
        else:
            if sender_job in ["售后", "销售"]:
                sender_role = "员工"
            else:
                sender_role = "未知"
        
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
        if not members_str:
            return {}
        
        try:
            members_list = json.loads(members_str)
            if not isinstance(members_list, list):
                return {}
            
            result = {}
            for m in members_list:
                userid = m.get("userid", "")
                if userid:
                    result[userid] = MemberInfo(
                        userid=userid,
                        name=m.get("name", ""),
                        group_nickname=m.get("group_nickname", ""),
                        type=m.get("type", 1),
                        join_time=m.get("join_time", 0),
                    )
            return result
        except json.JSONDecodeError:
            logger.warning(f"解析members字段失败: {members_str[:100]}")
            return {}
    
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

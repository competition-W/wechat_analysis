import re
import json
from datetime import datetime
from typing import List, Optional
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from services.preprocessor import NormalizedMessage
from utils.llm_client import LLMClient


UNANSWERED_PROMPT = """你是企业客户服务质量审核员。下面仅列出已经通过程序确认的：
1) 客户发送；2) 发送后尚未发现员工回复；3) 已超过最短等待时间的消息。

请只判断哪些消息确实需要员工回复。确认、感谢、客套话、纯联系方式、纯@提醒、表情、附件占位、
对上一条员工消息的补充说明，均不能判为漏回。明确问题、新需求、报错、投诉或催促进度才需要回复。

【判定标准】：
1. 正常结束（无漏回）：如果客户发送的仅仅是确认、感谢、客套话（如"好的"、"谢谢"、"麻烦了"、"知道了"）或是毫无意义的表情/符号，表明当前沟通已自然闭环，不需要我方继续回复。
2. 存在漏回：如果客户提出了明确的问题（如"怎么弄？"）、提出了新需求、反馈了报错/不满、或者在催促进度，则表明我方必须回复但未回复。

【候选消息】：
{trailing_messages}

【输出格式要求】：
请直接输出 JSON 格式（不要有任何 Markdown 标记或多余文本），字段如下：
{{
  "is_missed": true 或 false,
  "risk_level": "high" 或 "low",
  "missed_msgids": ["确实需要回复的消息msgid"],
  "explanation": "简短说明为什么需要或不需要回复",
  "suggested_action": "一句话建议，比如'需排查XX报错问题'。如果没有漏回，填空字符串"
}}"""


CLOSING_PATTERN = re.compile(
    r'^(?:(?:好的?|好滴|好的呢|收到|谢谢|感谢|辛苦(?:了)?|麻烦了|知道了|ok|已加|我?加你了|1|👍|\[抱拳\]|\[玫瑰\])(?:[，,、\s]+)?)+[~～！!。]*$',
    re.IGNORECASE,
)

EMPLOYEE_ROLES = {"售后", "员工", "销售"}


class UnansweredAnalyzer:
    def __init__(self):
        self.llm_client = LLMClient()
    
    def analyze(
        self,
        messages: List[NormalizedMessage],
        analysis_time: Optional[datetime] = None,
    ) -> dict:
        if not messages:
            return self._default_result("no_messages", "没有可分析的消息。", analysis_time)

        messages = self._deduplicate_messages(messages)
        
        trailing_messages = self._extract_trailing_customer_messages(messages)
        
        if not trailing_messages:
            return self._default_result(
                "answered",
                "最后一批客户消息之后已存在员工消息，当前没有待回复消息。",
                analysis_time,
            )

        actionable = [message for message in trailing_messages if self._is_actionable_candidate(message)]
        if not actionable:
            return self._default_result(
                "no_action_needed",
                "尾部客户消息仅包含确认、客套、联系方式、纯@或无有效文本，不需要继续回复。",
                analysis_time,
            )

        evaluated_at = analysis_time or datetime.now()
        mature, pending = self._partition_by_waiting_time(actionable, evaluated_at)
        if not mature:
            return self._default_result(
                "pending",
                f"客户消息仍在 {settings.UNANSWERED_MIN_WAIT_MINUTES} 分钟响应观察期内。",
                evaluated_at,
                review_required=bool(pending),
            )
        
        return self._llm_analyze(mature, evaluated_at)

    def _deduplicate_messages(self, messages: List[NormalizedMessage]) -> List[NormalizedMessage]:
        result = []
        seen = set()
        for message in sorted(messages, key=lambda item: (item.msgtime or "", item.seq or 0)):
            key = (
                message.msgid
                or f"{message.roomid}|{message.from_userid}|{message.msgtime}|{self._normalize_text(message.text_content)}"
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(message)
        return result
    
    def _extract_trailing_customer_messages(self, messages: List[NormalizedMessage]) -> List[NormalizedMessage]:
        last_employee_idx = -1
        
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if msg.sender_role in EMPLOYEE_ROLES:
                last_employee_idx = i
                break
        
        if last_employee_idx == -1:
            customer_messages = [m for m in messages if m.sender_role == "客户"]
            return customer_messages
        
        trailing = messages[last_employee_idx + 1:]
        customer_trailing = [m for m in trailing if m.sender_role == "客户"]
        
        return customer_trailing

    def _normalize_text(self, value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def _is_actionable_candidate(self, message: NormalizedMessage) -> bool:
        text = self._normalize_text(message.text_content)
        if not text or self._is_all_closing_remarks([message]):
            return False
        without_mentions = re.sub(r"@[\w\u4e00-\u9fff ._-]+", "", text).strip(" ,，。!！")
        if not without_mentions:
            return False
        if re.fullmatch(r"[+\d\s()-]{6,}", without_mentions):
            return False
        if re.fullmatch(r"(?:\[[^\]]+\]|[\W_])+", without_mentions):
            return False
        return True

    def _parse_message_time(self, value: str) -> Optional[datetime]:
        text = str(value or "").strip().replace("T", " ").replace("Z", "")
        if not text:
            return None
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    def _partition_by_waiting_time(
        self,
        messages: List[NormalizedMessage],
        evaluated_at: datetime,
    ) -> tuple[List[NormalizedMessage], List[NormalizedMessage]]:
        mature, pending = [], []
        minimum_seconds = max(0, settings.UNANSWERED_MIN_WAIT_MINUTES) * 60
        for message in messages:
            sent_at = self._parse_message_time(message.msgtime)
            if sent_at is None:
                pending.append(message)
                continue
            age_seconds = (evaluated_at.replace(tzinfo=None) - sent_at.replace(tzinfo=None)).total_seconds()
            (mature if age_seconds >= minimum_seconds else pending).append(message)
        return mature, pending
    
    def _is_all_closing_remarks(self, messages: List[NormalizedMessage]) -> bool:
        if not messages:
            return True
        
        for msg in messages:
            text = (msg.text_content or "").strip()
            if not text:
                continue
            if not CLOSING_PATTERN.match(text):
                return False
        
        return True
    
    def _format_messages_for_llm(self, messages: List[NormalizedMessage]) -> str:
        lines = []
        for msg in messages:
            time_str = msg.msgtime or ""
            name = msg.sender_name or "客户"
            content = msg.text_content or ""
            lines.append(f"[msgid={msg.msgid}] [{time_str}] {name}：{content}")
        return "\n".join(lines)
    
    def _build_missed_messages(
        self,
        messages: List[NormalizedMessage],
        evaluated_at: datetime,
    ) -> List[dict]:
        return [
            {
                "msgid": str(m.msgid or ""),
                "sender_name": str(m.sender_name or "未知发送人"),
                "sender_userid": str(m.from_userid or ""),
                "sender_role": str(m.sender_role or "客户"),
                "roomid": str(m.roomid or ""),
                "msgtime": str(m.msgtime or ""),
                "content": str(m.text_content or ""),
                "time_source": "original_message",
                "waiting_minutes": max(0, int((
                    evaluated_at.replace(tzinfo=None) - self._parse_message_time(m.msgtime).replace(tzinfo=None)
                ).total_seconds() // 60)) if self._parse_message_time(m.msgtime) else None,
            }
            for m in messages
        ]
    
    @retry(
        stop=stop_after_attempt(settings.LLM_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    def _llm_analyze(self, messages: List[NormalizedMessage], evaluated_at: datetime) -> dict:
        formatted_text = self._format_messages_for_llm(messages)
        prompt = UNANSWERED_PROMPT.format(trailing_messages=formatted_text)
        
        try:
            response = self.llm_client.chat(
                prompt,
                model=settings.LLM_MODEL_SENTIMENT,
                temperature=0.3,
            )
            
            cleaned_response = self._clean_json_response(response)
            result = json.loads(cleaned_response)
            
            selected_ids = {
                str(value) for value in result.get("missed_msgids", []) if str(value).strip()
            }
            selected = [message for message in messages if message.msgid in selected_ids]
            is_missed = bool(result.get("is_missed", False))
            if is_missed and not selected:
                logger.warning("漏回模型未返回可核验的 missed_msgids，不自动判定漏回")
                return self._fallback_result(messages, evaluated_at)
            if not is_missed:
                selected = []
            return {
                "is_missed": bool(selected),
                "decision_status": "missed" if selected else "no_action_needed",
                "risk_level": result.get("risk_level", "low"),
                "missed_messages": self._build_missed_messages(selected, evaluated_at),
                "suggested_action": result.get("suggested_action", ""),
                "analysis_time": evaluated_at.isoformat(timespec="seconds"),
                "explanation": result.get("explanation") or "模型完成候选消息复核。",
                "criteria_version": "unanswered-v2",
                "review_required": False,
            }
        
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}, 原始响应: {response[:200] if response else 'None'}")
            return self._fallback_result(messages, evaluated_at)
        
        except Exception as e:
            logger.error(f"LLM调用失败: {e}")
            return self._fallback_result(messages, evaluated_at)
    
    def _clean_json_response(self, response: str) -> str:
        if not response:
            return "{}"
        
        cleaned = response.strip()
        
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        
        return cleaned.strip()
    
    def _default_result(
        self,
        decision_status: str = "no_missed",
        explanation: str = "当前没有需要员工回复的客户消息。",
        analysis_time: Optional[datetime] = None,
        review_required: bool = False,
    ) -> dict:
        evaluated_at = analysis_time or datetime.now()
        return {
            "is_missed": False,
            "decision_status": decision_status,
            "risk_level": "low",
            "missed_messages": [],
            "suggested_action": None,
            "analysis_time": evaluated_at.isoformat(timespec="seconds"),
            "explanation": explanation,
            "criteria_version": "unanswered-v2",
            "review_required": review_required,
        }
    
    def _fallback_result(self, messages: List[NormalizedMessage], evaluated_at: datetime) -> dict:
        return {
            "is_missed": False,
            "decision_status": "insufficient_data",
            "risk_level": "low",
            "missed_messages": [],
            "suggested_action": "模型复核失败，请人工核查；系统不会自动记为漏回。",
            "analysis_time": evaluated_at.isoformat(timespec="seconds"),
            "explanation": "模型调用或返回解析失败，证据不足，未自动判定漏回。",
            "criteria_version": "unanswered-v2",
            "review_required": True,
        }

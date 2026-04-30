import re
import json
from typing import List, Optional
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from services.preprocessor import NormalizedMessage
from utils.llm_client import LLMClient


UNANSWERED_PROMPT = """你是一个企业客户服务质量监督员。请分析以下截取的【群聊对话尾部记录】（全部由客户发送），判断我方员工是否漏回了客户的重要消息，并严格输出 JSON 格式。

【判定标准】：
1. 正常结束（无漏回）：如果客户发送的仅仅是确认、感谢、客套话（如"好的"、"谢谢"、"麻烦了"、"知道了"）或是毫无意义的表情/符号，表明当前沟通已自然闭环，不需要我方继续回复。
2. 存在漏回：如果客户提出了明确的问题（如"怎么弄？"）、提出了新需求、反馈了报错/不满、或者在催促进度，则表明我方必须回复但未回复。

【群聊尾部记录】：
{trailing_messages}

【输出格式要求】：
请直接输出 JSON 格式（不要有任何 Markdown 标记或多余文本），字段如下：
{{
  "is_missed": true 或 false,
  "risk_level": "high" 或 "low",
  "suggested_action": "一句话建议，比如'需排查XX报错问题'。如果没有漏回，填空字符串"
}}"""


CLOSING_PATTERN = re.compile(
    r'^(好的|好滴|好的呢|收到|谢谢|感谢|辛苦|辛苦了|麻烦了|知道了|ok|OK|1|👍|\[抱拳\]|\[玫瑰\])[~～！!。]*$'
)

EMPLOYEE_ROLES = ['售后', '员工', '销售', '未知']


class UnansweredAnalyzer:
    def __init__(self):
        self.llm_client = LLMClient()
    
    def analyze(self, messages: List[NormalizedMessage]) -> dict:
        if not messages:
            return self._default_result()
        
        trailing_messages = self._extract_trailing_customer_messages(messages)
        
        if not trailing_messages:
            return self._default_result()
        
        if self._is_all_closing_remarks(trailing_messages):
            logger.info("尾部消息均为结束客套话，判定无漏回")
            return self._default_result()
        
        return self._llm_analyze(trailing_messages)
    
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
            lines.append(f"[{time_str}] {name}：{content}")
        return "\n".join(lines)
    
    def _build_missed_messages(self, messages: List[NormalizedMessage]) -> List[dict]:
        return [
            {
                "msgid": m.msgid,
                "sender_name": m.sender_name,
                "msgtime": m.msgtime,
                "content": m.text_content,
            }
            for m in messages
        ]
    
    @retry(
        stop=stop_after_attempt(settings.LLM_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    def _llm_analyze(self, messages: List[NormalizedMessage]) -> dict:
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
            
            return {
                "is_missed": result.get("is_missed", False),
                "risk_level": result.get("risk_level", "low"),
                "missed_messages": self._build_missed_messages(messages) if result.get("is_missed") else [],
                "suggested_action": result.get("suggested_action", ""),
            }
        
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}, 原始响应: {response[:200] if response else 'None'}")
            return self._fallback_result(messages)
        
        except Exception as e:
            logger.error(f"LLM调用失败: {e}")
            return self._fallback_result(messages)
    
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
    
    def _default_result(self) -> dict:
        return {
            "is_missed": False,
            "risk_level": "low",
            "missed_messages": [],
            "suggested_action": None,
        }
    
    def _fallback_result(self, messages: List[NormalizedMessage]) -> dict:
        return {
            "is_missed": True,
            "risk_level": "low",
            "missed_messages": self._build_missed_messages(messages),
            "suggested_action": "系统判定降级，请人工核查是否漏回。",
        }

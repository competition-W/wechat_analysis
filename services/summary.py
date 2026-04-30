from typing import List, Optional
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from services.preprocessor import NormalizedMessage
from utils.llm_client import LLMClient


class SummaryGenerator:
    def __init__(self):
        self.llm_client = LLMClient()
        self.max_messages = min(settings.SUMMARY_MAX_MESSAGES, 100)
        self.max_length = settings.SUMMARY_MAX_LENGTH
    
    def generate(self, messages: List[NormalizedMessage]) -> Optional[str]:
        if not messages:
            return None

        text_messages = [m for m in messages if m.text_content and m.msgtype == "text"]

        if not text_messages:
            logger.warning(f"没有文本消息，使用全部消息生成摘要")
            text_messages = [m for m in messages if m.text_content]
            if not text_messages:
                return None

        sampled = self._sample_messages(text_messages)
        
        formatted_text = self._format_messages(sampled)
        
        try:
            summary = self._llm_generate(formatted_text)
            if summary:
                summary = self._ensure_valid_json(summary)
            return summary
        except Exception as e:
            logger.error(f"生成摘要失败: {e}")
            return None
    
    def _ensure_valid_json(self, text: str) -> str:
        try:
            import json
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass
        
        try:
            text = text.strip()
            if text.startswith("```json"):
                text = text[7:]
            elif text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass
        
        fixed = self._try_complete_json(text)
        try:
            json.loads(fixed)
            return fixed
        except json.JSONDecodeError:
            pass
        
        logger.warning(f"无法修复截断的JSON，返回原始文本")
        return text
    
    def _try_complete_json(self, text: str) -> str:
        in_string = False
        escape_next = False
        brace_depth = 0

        for i, c in enumerate(text):
            if escape_next:
                escape_next = False
                continue
            if c == '\\' and in_string:
                escape_next = True
                continue
            if c == '"' and not escape_next:
                in_string = not in_string
                continue

            if not in_string:
                if c == '{':
                    brace_depth += 1
                elif c == '}':
                    brace_depth -= 1
                    if brace_depth == 0:
                        return text[:i+1]

        return text
    
    def _sample_messages(self, messages: List[NormalizedMessage]) -> List[NormalizedMessage]:
        if len(messages) <= self.max_messages:
            return messages
        
        high_priority = []
        medium_priority = []
        low_priority = []
        
        negative_keywords = ["问题", "错误", "失败", "投诉", "不满", "差", "垃圾", "退款", "赔偿"]
        question_keywords = ["?", "？", "怎么", "为什么", "如何", "能不能", "可以吗"]
        
        for msg in messages:
            text = msg.text_content or ""
            
            is_negative = any(kw in text for kw in negative_keywords)
            is_question = any(kw in text for kw in question_keywords)
            
            if is_negative:
                high_priority.append(msg)
            elif is_question:
                medium_priority.append(msg)
            else:
                low_priority.append(msg)
        
        result = []
        
        result.extend(high_priority[:self.max_messages // 3])
        
        remaining = self.max_messages - len(result)
        medium_count = min(len(medium_priority), remaining // 2)
        result.extend(medium_priority[:medium_count])
        
        remaining = self.max_messages - len(result)
        if remaining > 0 and low_priority:
            step = max(1, len(low_priority) // remaining)
            sampled_low = [low_priority[i] for i in range(0, len(low_priority), step)][:remaining]
            result.extend(sampled_low)
        
        result.sort(key=lambda x: x.msgtime or "")
        return result[:self.max_messages]
    
    def _format_messages(self, messages: List[NormalizedMessage]) -> str:
        lines = []
        for msg in messages:
            role = msg.sender_role or "未知"
            name = msg.sender_name or "未知"
            content = msg.text_content or ""
            lines.append(f"[{role}]{name}：{content}")
        return "\n".join(lines)
    
    @retry(
        stop=stop_after_attempt(settings.LLM_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    def _llm_generate(self, formatted_text: str) -> Optional[str]:
        prompt = f"""你是一个资深的企业客户成功经理（CSM）。请根据以下企业微信售后/客户群聊记录，为公司领导层生成一份逻辑清晰、高度精炼的业务摘要。

    要求：
    1. 聚焦客观发生的业务事实，不要进行情绪或态度的分析。
    2. 语言必须精炼、专业，直接切入核心。
    3. 如果某一项在聊天记录中没有涉及，请返回空数组 [] 或填 ["无"]。
    4. 严禁自行编造或预估任何时间信息，只提取聊天记录中明确提到的时间节点。

    请严格输出 JSON 格式，不要包含任何 Markdown 标记。请注意，demands、actions 和 todos 字段必须是 JSON 字符串数组（Array of Strings）。
    {{
    "overview": "用1-2句话高度概括今日群聊的核心议题或突发状况",
    "demands": [
        "客户提出的核心问题1",
        "客户提出的核心问题2"
    ],
    "actions": [
        "我方员工的响应动作1",
        "我方员工的响应动作2"
    ],
    "todos": [
        "[待办]：事项1及责任人",
        "[待办]：事项2及责任人"
    ]
    }}

    群聊记录：
    {formatted_text}
    """

        response = self.llm_client.chat(
            prompt,
            model=settings.LLM_MODEL_SUMMARY,
            temperature=0.3,
            max_tokens=4000,
        )

        return response.strip() if response else None

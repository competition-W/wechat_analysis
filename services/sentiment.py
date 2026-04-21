import json
from typing import List, Optional, Tuple, Dict, Any
from loguru import logger
from concurrent.futures import ThreadPoolExecutor, as_completed

from config.settings import settings
from services.preprocessor import NormalizedMessage
from utils.llm_client import LLMClient
from models.response import (
    SentimentResult,
    SentimentSummary,
    SentimentDetails,
    SentimentDetailItem,
    CustomerSentimentStats,
    EmployeeSentimentStats,
)


class SentimentAnalyzer:
    
    def __init__(self):
        self.batch_size = settings.LLM_BATCH_SIZE
        self.max_concurrent = settings.LLM_MAX_CONCURRENT
        
        self.customer_good_words = set(settings.customer_good_words_list)
        self.customer_bad_words = set(settings.customer_bad_words_list)
        self.employee_pos_words = set(settings.employee_pos_words_list)
        self.employee_bad_words = set(settings.employee_bad_words_list)
        
        self.llm_client = LLMClient()
    
    def analyze(self, messages: List[NormalizedMessage]) -> SentimentResult:
        result = SentimentResult(
            summary=SentimentSummary(
                customer=CustomerSentimentStats(),
                employee=EmployeeSentimentStats()
            ),
            details=SentimentDetails()
        )
        
        text_messages = []
        for msg in messages:
            if not msg.text_content or msg.msgtype != "text":
                continue
            text_messages.append(msg)
        
        if not text_messages:
            return result
        
        rule_results = {}
        llm_needed = []
        
        for msg in text_messages:
            sentiment, confidence = self._rule_based_analyze(msg.text_content, msg.sender_role)
            rule_results[msg.msgid] = {
                "sentiment": sentiment,
                "confidence": confidence,
            }
            
            if sentiment == "neutral":
                llm_needed.append(msg)
        
        if llm_needed:
            logger.info(f"需要LLM分析的消息数: {len(llm_needed)}/{len(text_messages)}")
            llm_results = self._parallel_batch_llm_analyze(llm_needed)
            
            for msg in llm_needed:
                if msg.msgid in llm_results:
                    rule_results[msg.msgid] = llm_results[msg.msgid]
        
        for msg in text_messages:
            analysis = rule_results.get(msg.msgid, {
                "sentiment": "neutral",
                "confidence": 0.5,
            })
            
            sentiment = analysis["sentiment"]
            confidence = analysis["confidence"]
            
            if sentiment == "neutral":
                continue
            
            detail_item = SentimentDetailItem(
                msgid=msg.msgid,
                sender_name=msg.sender_name,
                content=msg.text_content[:200] if msg.text_content else "",
                msgtime=msg.msgtime,
                confidence=confidence
            )
            
            if msg.sender_role == "客户":
                if sentiment == "good_review":
                    result.summary.customer.good_reviews += 1
                    result.details.customer_good.append(detail_item)
                elif sentiment == "bad_review":
                    result.summary.customer.bad_reviews += 1
                    result.details.customer_bad.append(detail_item)
            
            elif msg.sender_role in ["员工", "售后", "销售"]:
                if sentiment == "positive":
                    result.summary.employee.positive += 1
                    result.details.employee_positive.append(detail_item)
                elif sentiment == "bad_attitude":
                    result.summary.employee.bad_attitude += 1
                    result.details.employee_bad_attitude.append(detail_item)
        
        return result
    
    def _rule_based_analyze(self, text: str, sender_role: str) -> Tuple[str, float]:
        if sender_role == "客户":
            for word in self.customer_bad_words:
                if word in text:
                    return "bad_review", 0.8
            
            for word in self.customer_good_words:
                if word in text:
                    return "good_review", 0.7
        
        elif sender_role in ["员工", "售后", "销售"]:
            for word in self.employee_bad_words:
                if word in text:
                    return "bad_attitude", 0.8
            
            for word in self.employee_pos_words:
                if word in text:
                    return "positive", 0.7
        
        return "neutral", 0.5
    
    def _parallel_batch_llm_analyze(self, messages: List[NormalizedMessage]) -> Dict[str, Dict[str, Any]]:
        batches = []
        for i in range(0, len(messages), self.batch_size):
            batches.append(messages[i:i + self.batch_size])
        
        logger.info(f"批处理数量: {len(batches)}, 每批大小: {self.batch_size}, 并发数: {self.max_concurrent}")
        
        results = {}
        
        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            future_to_batch = {
                executor.submit(self._analyze_batch, batch): batch 
                for batch in batches
            }
            
            for future in as_completed(future_to_batch):
                batch = future_to_batch[future]
                try:
                    batch_result = future.result()
                    results.update(batch_result)
                except Exception as e:
                    logger.warning(f"批次分析失败: {e}")
                    for msg in batch:
                        results[msg.msgid] = {"sentiment": "neutral", "confidence": 0.5}
        
        return results
    
    def _analyze_batch(self, messages: List[NormalizedMessage]) -> Dict[str, Dict[str, Any]]:
        if not messages:
            return {}
        
        messages_text = []
        for idx, msg in enumerate(messages):
            role_label = "客户" if msg.sender_role == "客户" else "售后"
            messages_text.append(f"[{idx}] [{role_label}]{msg.sender_name}：{msg.text_content}")
        
        combined_text = "\n".join(messages_text)
        
        prompt = f"""你是一个企业微信客户沟通质量监控专家。请根据发言者的角色，判断以下消息的情感与态度倾向，并严格输出JSON数组格式。

消息列表：
{combined_text}

分类规则：
1. 若角色为【客户】：
   - 必须分类为 "good_review"（好评：赞扬、感谢、满意） 或 "bad_review"（差评：抱怨、愤怒、催促、不满）。
2. 若角色为【售后/员工】：
   - 必须分类为 "positive"（积极：耐心解答、热情、主动推进） 或 "bad_attitude"（恶劣态度：推诿、不耐烦、敷衍、指责客户）。
3. 如果消息是毫无情感波动的纯客观陈述，请标记为 "neutral"（中性）。

请输出JSON数组，格式如下：
[
  {{"index": 0, "sentiment": "bad_review", "confidence": 0.95}},
  {{"index": 1, "sentiment": "positive", "confidence": 0.90}}
]
只输出JSON数组，不要包含任何额外解释。"""

        try:
            response = self.llm_client.chat(
                prompt,
                model=settings.LLM_MODEL_SENTIMENT,
                temperature=0.1,
                max_tokens=4000,
            )
            
            results = self._parse_batch_response(response, messages)
            return results
            
        except Exception as e:
            logger.warning(f"批量LLM情感分析失败: {e}")
            return {msg.msgid: {"sentiment": "neutral", "confidence": 0.5} for msg in messages}
    
    def _parse_batch_response(self, response: str, messages: List[NormalizedMessage]) -> Dict[str, Dict[str, Any]]:
        results = {}
        
        try:
            json_start = response.find('[')
            json_end = response.rfind(']') + 1
            if json_start != -1 and json_end > json_start:
                json_str = response[json_start:json_end]
                parsed = json.loads(json_str)
                
                for item in parsed:
                    idx = item.get("index", -1)
                    if 0 <= idx < len(messages):
                        msg = messages[idx]
                        sentiment = item.get("sentiment", "neutral")
                        
                        if msg.sender_role == "客户":
                            if sentiment not in ["good_review", "bad_review", "neutral"]:
                                sentiment = "neutral"
                        elif msg.sender_role in ["员工", "售后", "销售"]:
                            if sentiment not in ["positive", "bad_attitude", "neutral"]:
                                sentiment = "neutral"
                        
                        results[msg.msgid] = {
                            "sentiment": sentiment,
                            "confidence": float(item.get("confidence", 0.5)),
                        }
        except json.JSONDecodeError as e:
            logger.warning(f"解析批量LLM响应失败: {e}, response: {response[:200]}")
        
        for msg in messages:
            if msg.msgid not in results:
                results[msg.msgid] = {"sentiment": "neutral", "confidence": 0.5}
        
        return results

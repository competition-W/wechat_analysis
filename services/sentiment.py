import json
from typing import List, Optional, Tuple, Dict, Any
from loguru import logger
from concurrent.futures import ThreadPoolExecutor, as_completed

from config.settings import settings
from services.preprocessor import NormalizedMessage
from utils.llm_client import LLMClient
from models.response import (
    SentimentResult, SentimentSummary, SentimentDetails,
    SentimentDetailItem, CustomerSentimentStats, EmployeeSentimentStats,
)


class SentimentAnalyzer:

    def __init__(self):
        self.batch_size = settings.LLM_BATCH_SIZE
        self.max_concurrent = settings.LLM_MAX_CONCURRENT
        self.customer_good_words = set(settings.customer_good_words_list)
        self.customer_bad_words = set(settings.customer_bad_words_list)
        self.employee_pos_words = set(settings.employee_pos_words_list)
        self.employee_bad_words = set(settings.employee_bad_words_list)
        self.employee_polite_words = set(settings.employee_polite_words_list)
        self.llm_client = LLMClient()

    def analyze(self, messages):
        result = SentimentResult(
            summary=SentimentSummary(customer=CustomerSentimentStats(), employee=EmployeeSentimentStats()),
            details=SentimentDetails()
        )
        text_messages = [m for m in messages if m.text_content and m.msgtype == "text"]
        if not text_messages:
            logger.warning("没有文本消息，跳过情感分析")
            return result

        rule_results = {}
        llm_messages = []

        for msg in text_messages:
            hit = self._rule_based_prefilter(msg.text_content, msg.sender_role)
            if hit is not None:
                sentiment, confidence = hit
                rule_results[msg.msgid] = {"sentiment": sentiment, "confidence": confidence, "source": "rule"}
            else:
                llm_messages.append(msg)

        if llm_messages:
            logger.info(f"LLM主审消息数: {len(llm_messages)}/{len(text_messages)}")
            llm_results = self._parallel_batch_llm_analyze(llm_messages)
            rule_results.update(llm_results)

        for msg in text_messages:
            analysis = rule_results.get(msg.msgid, {"sentiment": "neutral", "confidence": 0.5})
            sentiment = analysis["sentiment"]
            confidence = analysis["confidence"]
            if sentiment == "neutral" or sentiment is None:
                continue
            detail = SentimentDetailItem(
                msgid=msg.msgid, sender_name=msg.sender_name,
                content=msg.text_content[:200] or "", msgtime=msg.msgtime, confidence=confidence
            )
            if msg.sender_role == "客户":
                if sentiment == "good_review":
                    result.summary.customer.good_reviews += 1
                    result.details.customer_good.append(detail)
                elif sentiment == "bad_review":
                    result.summary.customer.bad_reviews += 1
                    result.details.customer_bad.append(detail)
            elif msg.sender_role in ["员工", "售后", "销售", "未知"]:
                if sentiment == "positive":
                    result.summary.employee.positive += 1
                    result.details.employee_positive.append(detail)
                elif sentiment == "bad_attitude":
                    result.summary.employee.bad_attitude += 1
                    result.details.employee_bad_attitude.append(detail)
        return result

    def _rule_based_prefilter(self, text, sender_role):
        return None

    def _parallel_batch_llm_analyze(self, messages):
        batches = [messages[i:i + self.batch_size] for i in range(0, len(messages), self.batch_size)]
        results = {}
        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            future_map = {executor.submit(self._analyze_batch, b): b for b in batches}
            for f in future_map:
                try:
                    results.update(f.result())
                except Exception as e:
                    logger.warning(f"批次分析失败: {e}")
                    for m in future_map[f]:
                        results[m.msgid] = {"sentiment": "neutral", "confidence": 0.5}
        return results

    def _analyze_batch(self, messages):
        if not messages:
            return {}
        lines = []
        for i, m in enumerate(messages):
            role = "客户" if m.sender_role == "客户" else "售后"
            lines.append(f"[{i}] [{role}]{m.sender_name}：{m.text_content}")
        combined = "\n".join(lines)
        prompt = self._build_prompt(combined)
        try:
            resp = self.llm_client.chat(prompt, model=settings.LLM_MODEL_SENTIMENT, temperature=0.1, max_tokens=4000)
            return self._parse_batch_response(resp, messages)
        except Exception as e:
            logger.warning(f"批量LLM情感分析失败: {e}")
            return {m.msgid: {"sentiment": "neutral", "confidence": 0.5} for m in messages}

    def _build_prompt(self, combined_text):
        return f"""你是一个企业微信客户服务沟通质量监控专家。请严格按以下规则分析每条消息的情感与态度倾向。

## 消息列表
每条消息格式：[序号] [角色]发言人：消息内容
{combined_text}

## 分类规则

### 一、客户（customer）角色
- "good_review"（好评）：包含明确的赞扬、感谢、对交付结果满意（如"谢谢"、"辛苦了"、"解决了"、"图做得很好"）。
- "bad_review"（差评）：**必须**包含明确的批评、强烈的不满、情绪化的抱怨或针对服务质量的指责（如"速度太慢了"、"数据完全没法用"、"你们怎么搞的"、"我要退款"）。
- "neutral"（中性）：纯客观的陈述、**正常的技术探讨与提问**（如"为什么这个指标这么高？"、"这里不需要过滤吗？"）、**数据细节的确认与质询**（如"你们没有去杂吗？"、"这个结果和预期不符"）。只要没有明显人身攻击或严重情绪宣泄，**一律标记为 "neutral"**，绝对不能判定为差评。

### 二、员工/售后/销售（employee）角色
- "positive"（积极态度）：
  * 耐心解答、热情服务、主动推进项目进度
  * **使用礼貌用语的标准服务话术**（如"麻烦您稍等一下"、"我来帮您看看"、"我去核实一下"、"我确认一下"、"我查一下"）
  * **主动确认问题、查找资料、协调资源**——这些都是积极的服务表现
  * 回复"好的"、"收到"、"没问题"、"请放心"、"可以"
- "neutral"（中性）：
  * 正常的工作沟通与技术确认
  * 中性的信息告知（如"这个数据需要等结果"、"我明天查一下"）
  * **只要没有明确的负面言行，绝不判为 "bad_attitude"**
- "bad_attitude"（恶劣态度）：
  * **必须**包含明确的推诶扯皮（"不归我管"、"你自己看"）、极度不耐烦、指责客户、阴阳怪气、消极怠工
  * 示例：不归我管、你自己看、没空、烦不烦、没法处理、你别找我、不知道不清楚

### 三、⚡ 极其重要的判定警告 ⚡
1. 凡是说"麻烦您稍等一下"、"我查一下"、"我确认一下"、"我看看"等，**一律视为礼貌积极的服务表现**，绝不能判为 bad_attitude
2. 凡是说"好的"、"收到"、"可以"、"没问题"等确认性回复，一律判为 positive
3. 凡是正常的技术讨论、数据确认（哪怕是质疑），只要没有人身攻击，一律判为 neutral
4. 不要因为"麻烦"、"等等"等字面的表面含义产生误判，要理解**语气和意图**
5. 员工说"抱歉久等了"、"不好意思让你等了"是道歉性的礼貌用语，判为 positive

### 四、示例
输入：[0] [售后]小王：麻烦您稍等一下，我查一下这个数据。
输出：{{"index": 0, "sentiment": "positive", "confidence": 0.95}}

输入：[1] [售后]小李：好的收到，我马上去核实。
输出：{{"index": 1, "sentiment": "positive", "confidence": 0.90}}

输入：[2] [客户]张总：这个结果和预期完全不符，你们没有排除批次效应吗？
输出：{{"index": 2, "sentiment": "neutral", "confidence": 0.92}}

输入：[3] [售后]老刘：这个不归我管，你找销售那边吧。
输出：{{"index": 3, "sentiment": "bad_attitude", "confidence": 0.95}}

输入：[4] [售后]小王：好的，我先确认一下，稍后答复您。
输出：{{"index": 4, "sentiment": "positive", "confidence": 0.93}}

## 

### 四、☑ 自反问机制
在给出每条消息的最终判定前，你必须先反问自己：
1. 这句话真的是客户的负面评价/投诉吗，还是只是客观陈述一个事实？
2. 用户说“不行”“还是不行”，是对服务的不满，还是在表示自己“搞不定”“操作不了”？
3. 用户说“我今晚不行”“我现在不行”，是表达时间安排，还是在给差评？
4. 如果有任何怀疑，一律判为 “neutral”。宁放过，不误杀。

### 五、常见易误判情形（以下一律判为 neutral）
- “还是不行” —— 用户在陈述操作结果，不是差评
- “我今晚不行呛” / “我现在不行” —— 时间安排，与服务无关
- “账号密码都不行” —— 系统登录问题，非服务差评
- “试了不行” / “重新试了还是不行” —— 操作结果陈述，非差评

输出要求
只输出 JSON 数组，不要包含任何额外解释或标记。confidence 取值 0.0-1.0，表示对该判断的确信程度。
[
  {{"index": 0, "sentiment": "positive", "confidence": 0.95}}
]"""

    def _parse_batch_response(self, response, messages):
        results = {}
        try:
            s = response.find("[")
            e = response.rfind("]") + 1
            if s != -1 and e > s:
                parsed = json.loads(response[s:e])
                for item in parsed:
                    idx = item.get("index", -1)
                    if 0 <= idx < len(messages):
                        m = messages[idx]
                        sen = item.get("sentiment", "neutral")
                        if m.sender_role == "客户":
                            if sen not in ["good_review", "bad_review", "neutral"]:
                                sen = "neutral"
                        else:
                            if sen not in ["positive", "bad_attitude", "neutral"]:
                                sen = "neutral"
                        results[m.msgid] = {"sentiment": sen, "confidence": float(item.get("confidence", 0.5))}
        except Exception as e:
            logger.warning(f"解析批量LLM响应失败: {e}")
        for m in messages:
            results.setdefault(m.msgid, {"sentiment": "neutral", "confidence": 0.5})
        return results

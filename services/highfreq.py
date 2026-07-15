import json
import re
from typing import List, Optional, Dict, Any
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from services.preprocessor import NormalizedMessage
from utils.llm_client import LLMClient


BUSINESS_SEED_LIST = [
    "原始数据质控与预处理 (过滤/比对/片段分布)",
    "差异表达与丰度分析 (基因/转录本/Peak/蛋白/代谢物)",
    "功能富集分析 (GO/KEGG/GSEA/DisGeNET)",
    "常规可视化与聚类趋势分析 (热图/韦恩图/PCA/Kmeans/MFUZZ)",
    "WGCNA共表达网络分析",
    "网络图构建与可视化 (PPI/共线性/Cytoscape/桑基图)",
    "转录调控与转录因子分析 (Motif/靶基因预测)",
    "可变剪切与转录本分析 (APA/AS/降解位点)",
    "非编码RNA鉴定与调控分析 (miRNA/lncRNA/circRNA/ceRNA)",
    "Peak Calling与注释关联分析",
    "DNA甲基化与表观遗传分析 (DMR/表观时钟)",
    "m6A RNA甲基化整体与联合分析",
    "微生物群落多样性分析 (Alpha/Beta多样性)",
    "微生物群落差异分析 (ALDEx2/ANCOM/LEfSe/Metastats)",
    "微生物物种溯源与肠型分析 (Sourcetracker/ClusterSim)",
    "微生物功能与表型预测 (BugBase/Tax4Fun/FAPROTAX/PICRUSt)",
    "菌群与环境因子/代谢物相关性分析 (RDA/Mantel/Envfit)",
    "多组学联合分析 (微生态-代谢组/转录组-蛋白组等)",
    "变异检测与全基因组可视化 (SNP/InDel/CNV/体细胞突变)",
    "GWAS全基因组关联分析",
    "群体进化与遗传学分析 (BSA/QTL/遗传力)",
    "孟德尔随机化与中介分析 (Mediation Analysis)",
    "统计学检验与方差分析 (ANOVA/Adonis/MRPP)",
    "数据去批次效应分析 (ComBat/ConQuR/MMUPHin)",
    "机器学习与预测模型搭建 (随机森林/SVM/泛癌模型)",
    "偏最小二乘法及结构方程模型 (PLS-DA/PLS-PM/SEM)",
    "免疫浸润与免疫治疗分析",
    "单细胞转录组测序分析 (scRNA-seq)",
    "临床预后与生存分析 (Hazard/药物敏感性)",
    "ROC受试者诊断曲线分析",
]

HIGH_FREQ_PROMPT_TEMPLATE = """你是一个资深的生物信息学项目交付专家与数据分析师。你的任务是分析客户与售后的群聊记录，提取客户提及的"生信分析业务需求"，并统计高频词。

【标准业务参考库（Seed List）】：
{seed_list}

【处理规则】：
1. 意图提取：识别群聊中客户真正需要的分析动作。**必须强制忽略纯粹的闲聊、无意义数字、语气词或系统提示词（如"123"、"0"、"其它"、"重分析"、"有样本特殊性"、"方法学建立"等无具体业务指向的词汇）。**
2. 标准化映射（核心）：
   - 当客户的口语化描述（如"跑个差异"、"做个富集"、"查下靶基因"、"测个alpha多样性"、"做个随机森林"）或特定工具描述（如"用envfit"、"跑个ALDEx2"、"做个GSEA"）与【标准业务参考库】中的某一项语义匹配时，**必须统一合并并命名为参考库中的标准名称**。
   - 如果客户提及了完全新颖的、不在参考库中的前沿分析类型（如特定空间组学等），请参考库中的专业命名风格，为其生成一个标准名称。
3. 频次统计：统计每个标准分析类型在当前聊天记录中被独立讨论的次数。不要将同一句话中的同义词重复计数。

【输出格式】：
请严格输出JSON数组格式，按提及次数（count）从高到低排序。不要包含任何Markdown标记（如```json）或解释性文字。数组中的每个对象必须包含以下字段：
[
  {{
    "standard_name": "标准业务名称（优先使用参考库中的名称）",
    "aliases_found": ["提取到的客户原话或具体工具名称1", "原话2"],
    "count": 3
  }}
]

【群聊记录】：
{chat_messages}"""


class HighFreqAnalyzer:
    def __init__(self):
        self.llm_client = LLMClient()
        self.top_n = settings.HIGH_FREQ_TOP_N
        self.seed_list = BUSINESS_SEED_LIST
    
    def analyze(self, messages: List[NormalizedMessage]) -> List[dict]:
        text_messages = [m for m in messages if m.text_content and m.msgtype == "text"]

        if not text_messages:
            logger.warning(f"没有文本消息，使用全部消息进行高频词分析")
            text_messages = [m for m in messages if m.text_content]
            if not text_messages:
                return []

        chat_text = self._format_messages(text_messages)
        
        try:
            result = self._llm_analyze(chat_text)
            return result[:self.top_n]
        except Exception as e:
            logger.error(f"LLM高频词分析失败: {e}")
            return []
    
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
    def _llm_analyze(self, chat_text: str) -> List[dict]:
        seed_list_str = "\n".join([f'  "{item}",' for item in self.seed_list])
        
        prompt = HIGH_FREQ_PROMPT_TEMPLATE.format(
            seed_list=seed_list_str,
            chat_messages=chat_text
        )
        
        response = self.llm_client.chat(
            prompt,
            model=settings.LLM_MODEL_SUMMARY,
            temperature=0.3,
            max_tokens=4000,
        )
        
        if not response:
            logger.warning("LLM返回空响应")
            return []
        
        parsed_result = self._parse_llm_response(response)
        
        return parsed_result
    
    def _parse_llm_response(self, response: str) -> List[dict]:
        response = response.strip()
        
        json_match = re.search(r'\[[\s\S]*\]', response)
        if not json_match:
            logger.warning(f"无法从LLM响应中提取JSON数组: {response[:200]}")
            return []
        
        json_str = json_match.group(0)
        
        try:
            result = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}, 原始内容: {json_str[:200]}")
            return []
        
        if not isinstance(result, list):
            logger.warning(f"LLM返回的不是列表格式: {type(result)}")
            return []
        
        validated_result = []
        for item in result:
            if not isinstance(item, dict):
                continue
            
            standard_name = item.get("standard_name", "")
            aliases_found = item.get("aliases_found", [])
            count = item.get("count", 0)
            
            if not standard_name or not isinstance(count, int):
                continue
            
            if not isinstance(aliases_found, list):
                aliases_found = [str(aliases_found)] if aliases_found else []
            
            validated_result.append({
                "word": standard_name,
                "count": count,
                "aliases": aliases_found,
            })
        
        validated_result.sort(key=lambda x: x["count"], reverse=True)
        
        return validated_result

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qxChat 数据分析器：对群聊消息执行 M12-M17 各模块分析，
复用现有的 SentimentAnalyzer、HighFreqAnalyzer、UnansweredAnalyzer。
"""

from typing import List, Dict, Any, Optional, Tuple
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from loguru import logger
import re


def _parse_time(time_str: str) -> Optional[datetime]:
    if not time_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(time_str, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _extract_text_content(content_str: str) -> str:
    """从 qxChat 消息的 content 字段提取纯文本"""
    if not content_str:
        return ""
    import json
    try:
        obj = json.loads(content_str)
        if isinstance(obj, dict):
            return obj.get("content", "") or obj.get("text", "") or content_str
        elif isinstance(obj, str):
            return obj
    except (json.JSONDecodeError, TypeError):
        pass
    return content_str


class QxChatAnalyzer:
    """
    群聊数据分析器：接收 qxChat 原始消息组，计算 M12-M17。
    """

    def __init__(self, use_llm: bool = False):
        self.use_llm = use_llm
        self.sentiment_analyzer = None
        self.highfreq_analyzer = None
        self.unanswered_analyzer = None

    def _init_services(self):
        """延迟初始化分析服务"""
        if self.use_llm:
            from services.sentiment import SentimentAnalyzer
            from services.highfreq import HighFreqAnalyzer
            from services.unanswered import UnansweredAnalyzer
            self.sentiment_analyzer = SentimentAnalyzer()
            self.highfreq_analyzer = HighFreqAnalyzer()
            self.unanswered_analyzer = UnansweredAnalyzer()

    def _get_sender_role(self, msg: dict, room_members: List[Dict] = None) -> str:
        """推断消息发送者角色：客户/员工/未知"""
        job = msg.get("job", "")
        sender_type = self._get_member_type(msg.get("from", ""), room_members)
        if sender_type == 2:
            return "客户"
        elif sender_type == 1:
            if job in ["售后", "销售"]:
                return job
            return "员工"
        if job in ["售后", "销售"]:
            return job
        return "未知"

    def _get_member_type(self, userid: str, room_members: List[Dict] = None) -> int:
        """从 members 中查找用户类型"""
        if not room_members:
            return 0
        for m in room_members:
            if m.get("userid") == userid:
                return m.get("type", 0)
        return 0

    def _parse_members(self, members_str: str) -> List[Dict]:
        """解析 members JSON 字符串"""
        if not members_str:
            return []
        import json
        try:
            parsed = json.loads(members_str)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        return []

    def analyze_groups(self, merged_groups: List[Dict]) -> Dict[str, Any]:
        """对合并后的所有群组数据进行 M12-M17 分析"""
        logger.info(f"开始 qxChat 数据分析: {len(merged_groups)} 个群")

        self._init_services()

        # M12: 群消息量趋势
        m12 = self._compute_message_trend(merged_groups)

        # M13: 消息时段分布
        m13 = self._compute_time_distribution(merged_groups)

        # M14: 情感分析 (如果有 LLM 服务)
        m14 = self._compute_sentiment(merged_groups)

        # M15: 高频词
        m15 = self._compute_highfreq(merged_groups)

        # M16: 漏回消息
        m16 = self._compute_unanswered(merged_groups)

        # M17: 响应时长分析
        m17 = self._compute_response_time(merged_groups)

        # M17b: 消息类型分布 (补充)
        m17b = self._compute_msg_type_distribution(merged_groups)

        result = {
            "m12_message_trend": m12,
            "m13_time_distribution": m13,
            "m14_sentiment": m14,
            "m15_highfreq": m15,
            "m16_unanswered": m16,
            "m17_response_time": m17,
            "m17b_msg_type_distribution": m17b,
        }

        logger.info("qxChat 数据分析完成")
        return result

    # ==================== M12: 消息量趋势 ====================

    def _compute_message_trend(self, groups: List[Dict]) -> Dict:
        """按日统计消息数量趋势"""
        daily_counts: Dict[str, int] = Counter()
        daily_customer: Dict[str, int] = Counter()
        daily_employee: Dict[str, int] = Counter()

        for g in groups:
            members = self._parse_members(
                g.get("messages", [{}])[0].get("members", "")
                if g.get("messages") else ""
            )
            for msg in g.get("messages", []):
                t = _parse_time(msg.get("msgtime", ""))
                if not t:
                    continue
                day = t.strftime("%Y-%m-%d")
                daily_counts[day] += 1
                role = self._get_sender_role(msg, members)
                if role == "客户":
                    daily_customer[day] += 1
                else:
                    daily_employee[day] += 1

        sorted_days = sorted(daily_counts.keys())
        total_msgs = sum(daily_counts.values())
        avg_daily = round(total_msgs / len(sorted_days), 1) if sorted_days else 0

        return {
            "total_messages": total_msgs,
            "avg_daily": avg_daily,
            "days_covered": len(sorted_days),
            "trend": [
                {
                    "date": day,
                    "total": daily_counts[day],
                    "customer": daily_customer.get(day, 0),
                    "employee": daily_employee.get(day, 0),
                }
                for day in sorted_days
            ],
        }

    # ==================== M13: 时段分布 ====================

    def _compute_time_distribution(self, groups: List[Dict]) -> Dict:
        """按小时 × 星期几统计消息分布（热力图数据）"""
        hourly_counts = Counter()
        weekday_hourly: Dict[str, Counter] = defaultdict(Counter)
        weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

        all_msgs = []
        for g in groups:
            all_msgs.extend(g.get("messages", []))

        for msg in all_msgs:
            t = _parse_time(msg.get("msgtime", ""))
            if not t:
                continue
            hour = t.hour
            weekday = weekday_names[t.weekday()]
            hourly_counts[hour] += 1
            weekday_hourly[weekday][hour] += 1

        peak_hour = hourly_counts.most_common(1)[0][0] if hourly_counts else -1
        total = sum(hourly_counts.values())

        return {
            "total_messages": total,
            "peak_hour": peak_hour,
            "peak_hour_label": f"{peak_hour}:00-{peak_hour+1}:00" if peak_hour >= 0 else "无数据",
            "hourly_distribution": [
                {"hour": h, "count": hourly_counts.get(h, 0),
                 "ratio": round(hourly_counts.get(h, 0) / total * 100, 1) if total > 0 else 0}
                for h in range(24)
            ],
            "weekday_heatmap": {
                wd: [weekday_hourly[wd].get(h, 0) for h in range(24)]
                for wd in weekday_names
            },
            "weekday_names": weekday_names,
        }

    # ==================== M14: 情感分析 ====================

    def _compute_sentiment(self, groups: List[Dict]) -> Dict:
        """复用现有的 SentimentAnalyzer 进行情感分析"""
        from services.preprocessor import Preprocessor
        preprocessor = Preprocessor()

        # 汇总所有消息
        all_normalized = []
        for g in groups:
            msgs = g.get("messages", [])
            normalized = preprocessor.process(msgs)
            all_normalized.extend(normalized)

        # 汇总统计
        customer_good = 0
        customer_bad = 0
        employee_positive = 0
        employee_bad = 0
        neutral = 0
        total = len(all_normalized)

        if self.use_llm and self.sentiment_analyzer:
            try:
                result = self.sentiment_analyzer.analyze(all_normalized)
                summary = result.summary if hasattr(result, "summary") else result.get("summary", {})
                if hasattr(summary, "customer"):
                    customer_good = summary.customer.good_reviews
                    customer_bad = summary.customer.bad_reviews
                    employee_positive = summary.employee.positive
                    employee_bad = summary.employee.bad_attitude
                elif isinstance(summary, dict):
                    customer_good = summary.get("customer", {}).get("good_reviews", 0)
                    customer_bad = summary.get("customer", {}).get("bad_reviews", 0)
                    employee_positive = summary.get("employee", {}).get("positive", 0)
                    employee_bad = summary.get("employee", {}).get("bad_attitude", 0)
            except Exception as e:
                logger.warning(f"LLM 情感分析失败，使用规则分析: {e}")
                customer_good, customer_bad, employee_positive, employee_bad = \
                    self._rule_sentiment(all_normalized)
        else:
            customer_good, customer_bad, employee_positive, employee_bad = \
                self._rule_sentiment(all_normalized)

        neutral = total - customer_good - customer_bad - employee_positive - employee_bad
        if neutral < 0:
            neutral = 0

        return {
            "total_analyzed": total,
            "customer_good": customer_good,
            "customer_bad": customer_bad,
            "employee_positive": employee_positive,
            "employee_bad": employee_bad,
            "neutral": neutral,
            "good_ratio": round((customer_good + employee_positive) / total * 100, 1) if total > 0 else 0,
            "bad_ratio": round((customer_bad + employee_bad) / total * 100, 1) if total > 0 else 0,
            "categories": ["客户好评", "客户差评", "员工积极", "员工恶劣", "中性"],
            "values": [customer_good, customer_bad, employee_positive, employee_bad, neutral],
        }

    def _rule_sentiment(self, messages) -> Tuple[int, int, int, int]:
        """简易规则情感分析（无需 LLM）"""
        customer_good_words = {"谢谢", "感谢", "很好", "满意", "解决了", "辛苦了", "棒", "专业"}
        customer_bad_words = {"太慢", "不行", "问题", "错误", "投诉", "退款", "差", "垃圾"}
        employee_pos_words = {"好的", "收到", "没问题", "请放心", "我来", "帮您", "马上"}
        employee_bad_words = {"不归我管", "自己看", "没空", "没办法", "处理不了", "随便"}

        cg = cb = ep = eb = 0
        for m in messages:
            text = m.text_content or ""
            role = m.sender_role or ""
            if role == "客户":
                if any(w in text for w in customer_bad_words):
                    cb += 1
                elif any(w in text for w in customer_good_words):
                    cg += 1
            elif role in ["员工", "售后", "销售"]:
                if any(w in text for w in employee_bad_words):
                    eb += 1
                elif any(w in text for w in employee_pos_words):
                    ep += 1
        return cg, cb, ep, eb

    # ==================== M15: 高频词 ====================

    def _compute_highfreq(self, groups: List[Dict]) -> Dict:
        """高频关键词分析"""
        from services.preprocessor import Preprocessor
        preprocessor = Preprocessor()

        all_normalized = []
        for g in groups:
            msgs = g.get("messages", [])
            normalized = preprocessor.process(msgs)
            all_normalized.extend(normalized)

        # 简易词频统计（jieba 分词，无需 LLM）
        try:
            import jieba
            # 加载停用词
            stopwords = set()
            try:
                with open("data/stopwords.txt", "r", encoding="utf-8") as f:
                    for line in f:
                        word = line.strip()
                        if word:
                            stopwords.add(word)
            except:
                pass
            # 加载自定义词典
            try:
                jieba.load_userdict("data/custom_dict.txt")
            except:
                pass

            word_counter: Counter = Counter()
            for m in all_normalized:
                text = m.text_content or ""
                if not text:
                    continue
                words = jieba.lcut(text)
                seen = set()
                for w in words:
                    w = w.strip()
                    if len(w) < 2 or w.isdigit() or w in stopwords:
                        continue
                    if w not in seen:
                        seen.add(w)
                        word_counter[w] += 1

            top_words = word_counter.most_common(20)
        except ImportError:
            # 无 jieba 时用简单分词
            word_counter: Counter = Counter()
            for m in all_normalized:
                text = m.text_content or ""
                if not text:
                    continue
                # 按空格和标点分割
                words = re.split(r"[\s,，。！？、；：""''【】（）()—\n\r\t]+", text)
                seen = set()
                for w in words:
                    w = w.strip()
                    if len(w) < 2 or w.isdigit():
                        continue
                    if w not in seen:
                        seen.add(w)
                        word_counter[w] += 1
            top_words = word_counter.most_common(20)

        total_words = sum(word_counter.values())
        return {
            "total_unique_words": len(word_counter),
            "total_word_occurrences": total_words,
            "top_words": [
                {"word": w, "count": c, "ratio": round(c / total_words * 100, 1) if total_words > 0 else 0}
                for w, c in top_words
            ],
        }

    # ==================== M16: 漏回消息 ====================

    def _compute_unanswered(self, groups: List[Dict]) -> Dict:
        """漏回/未回复消息分析"""
        # 对每个群独立分析尾部队列，统计未被回复的客户消息
        total_missed = 0
        total_rooms_with_missed = 0
        all_missed_details = []
        risk_levels = Counter()

        for g in groups:
            msgs = g.get("messages", [])
            if len(msgs) < 2:
                continue

            # 按时间排序
            sorted_msgs = sorted(
                msgs, key=lambda x: x.get("msgtime", "")
            )

            members = self._parse_members(
                msgs[0].get("members", "") if msgs else ""
            )

            # 分析消息尾部（最后 N 条客户消息是否有员工回复）
            tail_size = min(20, len(sorted_msgs))
            tail = sorted_msgs[-tail_size:]

            room_missed = 0
            for i, msg in enumerate(tail):
                role = self._get_sender_role(msg, members)
                if role != "客户":
                    continue
                msgtime = _parse_time(msg.get("msgtime", ""))
                if not msgtime:
                    continue
                # 检查之后是否有员工回复
                has_reply = False
                for j in range(i + 1, len(tail)):
                    reply_role = self._get_sender_role(tail[j], members)
                    if reply_role not in ["客户", "未知"]:
                        reply_time = _parse_time(tail[j].get("msgtime", ""))
                        if reply_time and (reply_time - msgtime).total_seconds() < 3600:
                            has_reply = True
                            break
                if not has_reply:
                    room_missed += 1
                    content = _extract_text_content(msg.get("content", ""))
                    all_missed_details.append({
                        "room_id": g.get("room_id", ""),
                        "room_name": g.get("room_name", ""),
                        "sender_name": msg.get("truename", ""),
                        "content": content[:100],
                        "msgtime": msg.get("msgtime", ""),
                    })

            if room_missed > 0:
                total_missed += room_missed
                total_rooms_with_missed += 1
                if room_missed >= 3:
                    risk_levels["high"] += 1
                elif room_missed >= 1:
                    risk_levels["medium"] += 1

        total_messages_in_tail = sum(min(20, len(g.get("messages", []))) for g in groups)
        missed_rate = round(total_missed / total_messages_in_tail * 100, 1) if total_messages_in_tail > 0 else 0

        return {
            "total_missed": total_missed,
            "total_rooms_with_missed": total_rooms_with_missed,
            "missed_rate": missed_rate,
            "risk_levels": {
                "high": risk_levels.get("high", 0),
                "medium": risk_levels.get("medium", 0),
                "low": len(groups) - total_rooms_with_missed,
            },
            "missed_details": all_missed_details[:20],  # 仅返回最近 20 条
        }

    # ==================== M17: 响应时长 ====================

    def _compute_response_time(self, groups: List[Dict]) -> Dict:
        """员工响应时长分析"""
        all_response_times = []
        employee_response_stats: Dict[str, List[int]] = defaultdict(list)
        region_response_stats: Dict[str, List[int]] = defaultdict(list)

        for g in groups:
            msgs = g.get("messages", [])
            if len(msgs) < 2:
                continue
            sorted_msgs = sorted(msgs, key=lambda x: x.get("msgtime", ""))
            members = self._parse_members(
                msgs[0].get("members", "") if msgs else ""
            )

            # 找客户消息 → 员工回复的配对
            for i, msg in enumerate(sorted_msgs):
                role = self._get_sender_role(msg, members)
                if role != "客户":
                    continue
                msgtime = _parse_time(msg.get("msgtime", ""))
                if not msgtime:
                    continue
                # 找之后第一个员工回复
                for j in range(i + 1, min(i + 50, len(sorted_msgs))):
                    reply_role = self._get_sender_role(sorted_msgs[j], members)
                    if reply_role not in ["客户", "未知"]:
                        reply_time = _parse_time(sorted_msgs[j].get("msgtime", ""))
                        if reply_time:
                            diff_sec = (reply_time - msgtime).total_seconds()
                            if 0 < diff_sec < 86400:  # 24小时内
                                all_response_times.append(diff_sec)
                                emp_name = sorted_msgs[j].get("truename", "未知")
                                employee_response_stats[emp_name].append(diff_sec)
                        break

        # 计算统计量
        def _stats(times):
            if not times:
                return {"avg": 0, "min": 0, "max": 0, "median": 0, "count": 0}
            sorted_t = sorted(times)
            n = len(sorted_t)
            return {
                "avg": round(sum(sorted_t) / n, 0),
                "min": round(sorted_t[0], 0),
                "max": round(sorted_t[-1], 0),
                "median": round(sorted_t[n // 2], 0),
                "count": n,
            }

        overall = _stats(all_response_times)

        # 按售后员排名
        employee_ranking = sorted(
            [
                {"name": name, **_stats(times)}
                for name, times in employee_response_stats.items()
            ],
            key=lambda x: x["count"],
            reverse=True,
        )[:10]

        # 分档统计
        buckets = {"<5分钟": 0, "5-15分钟": 0, "15-30分钟": 0,
                    "30-60分钟": 0, "1-4小时": 0, "4-24小时": 0}
        for t in all_response_times:
            if t < 300:
                buckets["<5分钟"] += 1
            elif t < 900:
                buckets["5-15分钟"] += 1
            elif t < 1800:
                buckets["15-30分钟"] += 1
            elif t < 3600:
                buckets["30-60分钟"] += 1
            elif t < 14400:
                buckets["1-4小时"] += 1
            else:
                buckets["4-24小时"] += 1

        distribution = [
            {"bucket": k, "count": v,
             "ratio": round(v / len(all_response_times) * 100, 1) if all_response_times else 0}
            for k, v in buckets.items()
        ]

        return {
            "overall": overall,
            "employee_ranking": employee_ranking,
            "distribution": distribution,
            "bucket_labels": list(buckets.keys()),
            "bucket_values": list(buckets.values()),
        }

    # ==================== 消息类型分布 ====================

    def _compute_msg_type_distribution(self, groups: List[Dict]) -> Dict:
        """消息类型分布"""
        type_counter = Counter()
        for g in groups:
            for msg in g.get("messages", []):
                msgtype = msg.get("msgtype", "unknown") or "unknown"
                type_counter[msgtype] += 1
        total = sum(type_counter.values())
        return {
            "total": total,
            "distribution": [
                {"type": t, "count": c, "ratio": round(c / total * 100, 1) if total > 0 else 0}
                for t, c in type_counter.most_common()
            ],
        }

#!/usr/bin/env python3
"""
企业微信群聊分析服务 - 系统性能完整测试脚本

功能：
1. 从Java数据源接口拉取真实消息数据
2. 统计群聊数量、消息数量
3. 调用批量分析接口进行完整分析
4. 统计分析耗费时间
5. 统计Token使用情况
6. 测试所有分析类型（情感、敏感词、摘要、高频词、漏回分析）
7. 生成详细的性能测试报告

使用方式：
    python tests/test_system_performance.py

环境变量：
    JAVA_DATA_SOURCE_URL - Java数据源地址（默认: http://192.168.0.129:8081/qxChat/）
    ANALYSIS_SERVICE_URL - 分析服务地址（默认: http://localhost:8000）
"""

import json
import sys
import time
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict
import argparse

import httpx

sys.path.insert(0, str(__file__).rsplit("/tests", 1)[0])

from config.settings import settings
from utils.llm_client import LLMClient


@dataclass
class RoomData:
    room_id: str
    room_name: Optional[str]
    messages: List[Dict[str, Any]]


@dataclass
class PerformanceMetrics:
    total_rooms: int = 0
    total_messages: int = 0
    analysis_time: float = 0.0
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    success_count: int = 0
    failed_count: int = 0
    analysis_types: List[str] = field(default_factory=list)
    room_details: List[Dict[str, Any]] = field(default_factory=list)


class SystemPerformanceTester:
    def __init__(
        self,
        data_source_url: Optional[str] = None,
        analysis_service_url: Optional[str] = None,
        max_concurrent: int = 5,
        max_rooms: Optional[int] = None,
    ):
        self.data_source_url = data_source_url or settings.JAVA_DATA_SOURCE_URL
        self.analysis_service_url = analysis_service_url or "http://localhost:8000"
        self.max_concurrent = max_concurrent
        self.max_rooms = max_rooms
        self.http_client = httpx.Client(timeout=1200)
        self.llm_client = LLMClient()
        self.metrics = PerformanceMetrics()

    def fetch_data_from_java(self) -> List[RoomData]:
        print(f"\n{'='*80}")
        print(f"步骤1: 从Java数据源拉取数据")
        print(f"{'='*80}")
        print(f"数据源地址: {self.data_source_url}")
        print()

        try:
            response = self.http_client.get(self.data_source_url)
            response.raise_for_status()
            raw_data = response.json()

            messages = raw_data.get("data", [])
            print(f"✓ 数据拉取成功!")
            print(f"  - 总消息数: {len(messages)}")

            if not messages:
                print("✗ 没有消息数据")
                return []

            rooms_dict = defaultdict(lambda: {"room_id": "", "room_name": "", "messages": []})

            for msg in messages:
                room_id = msg.get("roomid", "unknown")
                if not rooms_dict[room_id]["room_id"]:
                    rooms_dict[room_id]["room_id"] = room_id
                    rooms_dict[room_id]["room_name"] = msg.get("roomname", f"群-{room_id[:8]}")
                rooms_dict[room_id]["messages"].append(msg)

            rooms = []
            for room_id, room_info in rooms_dict.items():
                rooms.append(RoomData(
                    room_id=room_info["room_id"],
                    room_name=room_info["room_name"],
                    messages=room_info["messages"]
                ))

            if self.max_rooms and len(rooms) > self.max_rooms:
                rooms = rooms[:self.max_rooms]
                print(f"  - 限制测试群聊数量: {self.max_rooms}")

            self.metrics.total_rooms = len(rooms)
            self.metrics.total_messages = sum(len(room.messages) for room in rooms)

            print(f"  - 群聊数量: {len(rooms)}")
            for room in rooms:
                print(f"    - {room.room_name}: {len(room.messages)}条消息")

            return rooms

        except httpx.ConnectError as e:
            print(f"✗ 连接失败: {e}")
            print(f"  请确认Java服务 ({self.data_source_url}) 已启动")
            sys.exit(1)
        except httpx.TimeoutException:
            print(f"✗ 请求超时")
            sys.exit(1)
        except Exception as e:
            print(f"✗ 请求失败: {e}")
            sys.exit(1)

    def batch_analyze(self, rooms: List[RoomData], analysis_types: List[str]) -> Dict[str, Any]:
        print(f"\n{'='*80}")
        print(f"步骤2: 调用批量分析接口")
        print(f"{'='*80}")
        print(f"分析服务地址: {self.analysis_service_url}/api/v1/chat/batch-analyze")
        print(f"群聊数量: {len(rooms)}")
        print(f"分析类型: {', '.join(analysis_types)}")
        print(f"并发数: {self.max_concurrent}")
        print()

        if not rooms:
            print("✗ 没有群聊数据可分析")
            return {}

        rooms_data = []
        for room in rooms:
            rooms_data.append({
                "room_id": room.room_id,
                "room_name": room.room_name,
                "messages": room.messages
            })

        request_body = {
            "rooms": rooms_data,
            "analysis_type": analysis_types,
            "max_concurrent": self.max_concurrent
        }

        try:
            self.llm_client.reset_token_stats()
            
            start_time = time.time()
            response = self.http_client.post(
                f"{self.analysis_service_url}/api/v1/chat/batch-analyze",
                json=request_body,
                timeout=1200,
            )
            elapsed = time.time() - start_time

            response.raise_for_status()
            result = response.json()

            self.metrics.analysis_time = elapsed
            token_stats = self.llm_client.token_stats
            self.metrics.total_tokens = token_stats["total_tokens"]
            self.metrics.prompt_tokens = token_stats["total_prompt_tokens"]
            self.metrics.completion_tokens = token_stats["total_completion_tokens"]
            self.metrics.analysis_types = analysis_types

            print(f"✓ 批量分析完成! (耗时: {elapsed:.2f}秒)")
            print(f"  - 返回码: {result.get('code')}")
            print(f"  - 状态: {result.get('message')}")

            if "data" in result:
                data = result["data"]
                self.metrics.success_count = data.get('success_count', 0)
                self.metrics.failed_count = data.get('failed_count', 0)
                print(f"  - 总群数: {data.get('total_rooms', 0)}")
                print(f"  - 成功: {self.metrics.success_count}")
                print(f"  - 失败: {self.metrics.failed_count}")
                print(f"  - 总耗时: {data.get('elapsed_seconds', 0)}秒")

            return result

        except httpx.ConnectError as e:
            print(f"✗ 连接失败: {e}")
            print(f"  请确认Python分析服务 ({self.analysis_service_url}) 已启动")
            print(f"  启动命令: uvicorn api.main:app --host 0.0.0.0 --port 8000")
            sys.exit(1)
        except httpx.TimeoutException:
            print(f"✗ 请求超时 (分析时间超过1200秒)")
            sys.exit(1)
        except Exception as e:
            print(f"✗ 批量分析失败: {e}")
            sys.exit(1)

    def print_performance_report(self, result: Dict[str, Any]):
        print(f"\n{'='*80}")
        print(f"步骤3: 系统性能测试报告")
        print(f"{'='*80}")

        print(f"\n【测试概览】")
        print(f"  测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  数据源: {self.data_source_url}")
        print(f"  分析服务: {self.analysis_service_url}")

        print(f"\n【数据统计】")
        print(f"  群聊数量: {self.metrics.total_rooms}")
        print(f"  消息总数: {self.metrics.total_messages}")
        if self.metrics.total_rooms > 0:
            avg_messages = self.metrics.total_messages / self.metrics.total_rooms
            print(f"  平均每群消息数: {avg_messages:.1f}")

        print(f"\n【分析性能】")
        print(f"  分析类型: {', '.join(self.metrics.analysis_types)}")
        print(f"  分析耗时: {self.metrics.analysis_time:.2f}秒")
        if self.metrics.total_rooms > 0:
            avg_time = self.metrics.analysis_time / self.metrics.total_rooms
            print(f"  平均每群耗时: {avg_time:.2f}秒")
        if self.metrics.total_messages > 0:
            time_per_msg = self.metrics.analysis_time / self.metrics.total_messages * 1000
            print(f"  平均每条消息耗时: {time_per_msg:.2f}毫秒")

        print(f"\n【Token使用统计】")
        print(f"  总Token数: {self.metrics.total_tokens:,}")
        print(f"  提示Token数: {self.metrics.prompt_tokens:,}")
        print(f"  完成Token数: {self.metrics.completion_tokens:,}")
        if self.metrics.total_rooms > 0:
            avg_tokens = self.metrics.total_tokens / self.metrics.total_rooms
            print(f"  平均每群Token数: {avg_tokens:.1f}")
        if self.metrics.total_messages > 0:
            tokens_per_msg = self.metrics.total_tokens / self.metrics.total_messages
            print(f"  平均每条消息Token数: {tokens_per_msg:.2f}")

        print(f"\n【成功率统计】")
        print(f"  成功分析: {self.metrics.success_count}")
        print(f"  失败分析: {self.metrics.failed_count}")
        if self.metrics.total_rooms > 0:
            success_rate = (self.metrics.success_count / self.metrics.total_rooms) * 100
            print(f"  成功率: {success_rate:.1f}%")

        if "data" in result:
            data = result["data"]
            results = data.get("results", [])

            print(f"\n【各群详细分析结果】")
            for i, room_result in enumerate(results, 1):
                room_name = room_result.get("room_name", "未知")
                status = room_result.get("status", "unknown")
                room_data = room_result.get("data", {})

                status_icon = "✓" if status == "success" else "✗"
                print(f"\n  {i}. {room_name} [{status_icon}]")

                if status == "success" and room_data:
                    msg_count = room_data.get("message_count", 0)
                    print(f"     消息数: {msg_count}")

                    sentiment = room_data.get("sentiment", {})
                    if sentiment:
                        summary = sentiment.get("summary", {})
                        print(f"     情感分析: 积极={summary.get('positive', 0)}, "
                              f"中性={summary.get('neutral', 0)}, "
                              f"消极={summary.get('negative', 0)}, "
                              f"恶劣={summary.get('very_negative', 0)}")

                    sensitive = room_data.get("sensitive_words", {})
                    if sensitive:
                        print(f"     敏感词: {sensitive.get('total_hits', 0)}次命中")

                    summary_text = room_data.get("summary")
                    if summary_text:
                        print(f"     摘要: {summary_text[:100]}{'...' if len(summary_text) > 100 else ''}")

                    highfreq = room_data.get("high_freq_words", {})
                    if highfreq and highfreq.get("words"):
                        words = highfreq["words"][:5]
                        word_str = ", ".join([f"{w['word']}({w['count']})" for w in words])
                        print(f"     高频词: {word_str}")

                    unanswered = room_data.get("unanswered_status", {})
                    if unanswered:
                        is_missed = unanswered.get("is_missed", False)
                        risk_level = unanswered.get("risk_level", "low")
                        missed_count = len(unanswered.get("missed_messages", []))
                        print(f"     漏回状态: {'存在漏回' if is_missed else '无漏回'} (风险等级: {risk_level}, 漏回消息数: {missed_count})")
                        if unanswered.get("suggested_action"):
                            print(f"     建议操作: {unanswered.get('suggested_action')}")
                else:
                    error = room_result.get("error_message", "未知错误")
                    print(f"     错误: {error}")

        print(f"\n{'='*80}")
        print("系统性能测试完成!")
        print(f"{'='*80}\n")

    def save_report_to_file(self, result: Dict[str, Any], output_file: str = "performance_report.json"):
        report = {
            "test_time": datetime.now().isoformat(),
            "data_source": self.data_source_url,
            "analysis_service": self.analysis_service_url,
            "metrics": {
                "total_rooms": self.metrics.total_rooms,
                "total_messages": self.metrics.total_messages,
                "analysis_time_seconds": round(self.metrics.analysis_time, 2),
                "total_tokens": self.metrics.total_tokens,
                "prompt_tokens": self.metrics.prompt_tokens,
                "completion_tokens": self.metrics.completion_tokens,
                "success_count": self.metrics.success_count,
                "failed_count": self.metrics.failed_count,
                "analysis_types": self.metrics.analysis_types,
            },
            "analysis_result": result.get("data", {})
        }

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        print(f"✓ 性能报告已保存到: {output_file}")

    def generate_markdown_report(self, result: Dict[str, Any], output_file: str = "系统测试报告.md"):
        md_content = f"""# 企业微信群聊分析服务 - 系统测试报告

## 一、测试概览

| 项目 | 内容 |
| :--- | :--- |
| 测试时间 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |
| 数据源地址 | {self.data_source_url} |
| 分析服务地址 | {self.analysis_service_url} |
| 并发数 | {self.max_concurrent} |

## 二、数据统计

| 指标 | 数值 |
| :--- | :--- |
| 群聊数量 | {self.metrics.total_rooms} |
| 消息总数 | {self.metrics.total_messages} |
| 平均每群消息数 | {(self.metrics.total_messages / self.metrics.total_rooms if self.metrics.total_rooms > 0 else 0):.1f} |

## 三、分析性能

| 指标 | 数值 |
| :--- | :--- |
| 分析类型 | {', '.join(self.metrics.analysis_types)} |
| 总分析耗时 | {self.metrics.analysis_time:.2f}秒 |
| 平均每群耗时 | {(self.metrics.analysis_time / self.metrics.total_rooms if self.metrics.total_rooms > 0 else 0):.2f}秒 |
| 平均每条消息耗时 | {(self.metrics.analysis_time / self.metrics.total_messages * 1000 if self.metrics.total_messages > 0 else 0):.2f}毫秒 |

## 四、Token使用统计

| 指标 | 数值 |
| :--- | :--- |
| 总Token数 | {self.metrics.total_tokens:,} |
| 提示Token数 | {self.metrics.prompt_tokens:,} |
| 完成Token数 | {self.metrics.completion_tokens:,} |
| 平均每群Token数 | {(self.metrics.total_tokens / self.metrics.total_rooms if self.metrics.total_rooms > 0 else 0):.1f} |
| 平均每条消息Token数 | {(self.metrics.total_tokens / self.metrics.total_messages if self.metrics.total_messages > 0 else 0):.2f} |

## 五、成功率统计

| 指标 | 数值 |
| :--- | :--- |
| 成功分析 | {self.metrics.success_count} |
| 失败分析 | {self.metrics.failed_count} |
| 成功率 | {(self.metrics.success_count / self.metrics.total_rooms * 100 if self.metrics.total_rooms > 0 else 0):.1f}% |

## 六、各群详细分析结果

"""
        if "data" in result:
            data = result["data"]
            results = data.get("results", [])
            
            for i, room_result in enumerate(results, 1):
                room_name = room_result.get("room_name", "未知")
                status = room_result.get("status", "unknown")
                room_data = room_result.get("data", {})
                
                status_icon = "✓" if status == "success" else "✗"
                md_content += f"### {i}. {room_name} [{status_icon}]\n\n"
                
                if status == "success" and room_data:
                    msg_count = room_data.get("message_count", 0)
                    md_content += f"**消息数**: {msg_count}\n\n"
                    
                    sentiment = room_data.get("sentiment", {})
                    if sentiment:
                        summary = sentiment.get("summary", {})
                        md_content += f"**情感分析**: 积极={summary.get('positive', 0)}, 中性={summary.get('neutral', 0)}, 消极={summary.get('negative', 0)}, 恶劣={summary.get('very_negative', 0)}\n\n"
                    
                    sensitive = room_data.get("sensitive_words", {})
                    if sensitive:
                        md_content += f"**敏感词**: {sensitive.get('total_hits', 0)}次命中\n\n"
                    
                    summary_text = room_data.get("summary")
                    if summary_text:
                        md_content += f"**摘要**: {summary_text}\n\n"
                    
                    highfreq = room_data.get("high_freq_words", {})
                    if highfreq and highfreq.get("words"):
                        words = highfreq["words"][:5]
                        word_str = ", ".join([f"{w['word']}({w['count']})" for w in words])
                        md_content += f"**高频词**: {word_str}\n\n"
                    
                    unanswered = room_data.get("unanswered_status", {})
                    if unanswered:
                        is_missed = unanswered.get("is_missed", False)
                        risk_level = unanswered.get("risk_level", "low")
                        missed_count = len(unanswered.get("missed_messages", []))
                        md_content += f"**漏回状态**: {'存在漏回' if is_missed else '无漏回'} (风险等级: {risk_level}, 漏回消息数: {missed_count})\n\n"
                        if unanswered.get("suggested_action"):
                            md_content += f"**建议操作**: {unanswered.get('suggested_action')}\n\n"
                else:
                    error = room_result.get("error_message", "未知错误")
                    md_content += f"**错误**: {error}\n\n"
                
                md_content += "---\n\n"

        md_content += self._generate_evaluation_report()
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        print(f"✓ 测试文档已保存到: {output_file}")

    def _generate_evaluation_report(self) -> str:
        avg_time_per_room = self.metrics.analysis_time / self.metrics.total_rooms if self.metrics.total_rooms > 0 else 0
        avg_tokens_per_room = self.metrics.total_tokens / self.metrics.total_rooms if self.metrics.total_rooms > 0 else 0
        success_rate = (self.metrics.success_count / self.metrics.total_rooms * 100) if self.metrics.total_rooms > 0 else 0
        
        performance_grade = "优秀" if avg_time_per_room < 5 else "良好" if avg_time_per_room < 10 else "一般" if avg_time_per_room < 20 else "需优化"
        reliability_grade = "优秀" if success_rate >= 95 else "良好" if success_rate >= 90 else "一般" if success_rate >= 80 else "需改进"
        efficiency_grade = "优秀" if avg_tokens_per_room < 1000 else "良好" if avg_tokens_per_room < 2000 else "一般" if avg_tokens_per_room < 3000 else "需优化"
        
        overall_score = 0
        if performance_grade == "优秀": overall_score += 30
        elif performance_grade == "良好": overall_score += 25
        elif performance_grade == "一般": overall_score += 20
        else: overall_score += 10
        
        if reliability_grade == "优秀": overall_score += 40
        elif reliability_grade == "良好": overall_score += 35
        elif reliability_grade == "一般": overall_score += 25
        else: overall_score += 15
        
        if efficiency_grade == "优秀": overall_score += 30
        elif efficiency_grade == "良好": overall_score += 25
        elif efficiency_grade == "一般": overall_score += 20
        else: overall_score += 10
        
        overall_grade = "优秀" if overall_score >= 90 else "良好" if overall_score >= 75 else "一般" if overall_score >= 60 else "需改进"
        
        evaluation = f"""## 七、系统评估报告

### 7.1 性能评估

| 评估项 | 指标 | 评级 |
| :--- | :--- | :--- |
| 平均分析速度 | {avg_time_per_room:.2f}秒/群 | {performance_grade} |
| 并发处理能力 | {self.max_concurrent}个并发 | {'优秀' if self.max_concurrent >= 10 else '良好' if self.max_concurrent >= 5 else '一般'} |
| 吞吐量 | {self.metrics.total_rooms / self.metrics.analysis_time * 60:.1f}群/分钟 | {'优秀' if self.metrics.total_rooms / self.metrics.analysis_time * 60 >= 10 else '良好' if self.metrics.total_rooms / self.metrics.analysis_time * 60 >= 5 else '一般'} |

**性能分析**:
- 系统在{self.metrics.analysis_time:.2f}秒内完成了{self.metrics.total_rooms}个群聊的分析
- 平均每个群聊耗时{avg_time_per_room:.2f}秒，{'表现优秀' if avg_time_per_room < 5 else '表现良好' if avg_time_per_room < 10 else '有优化空间'}
- 系统吞吐量为{self.metrics.total_rooms / self.metrics.analysis_time * 60:.1f}群/分钟

### 7.2 可靠性评估

| 评估项 | 指标 | 评级 |
| :--- | :--- | :--- |
| 成功率 | {success_rate:.1f}% | {reliability_grade} |
| 失败数量 | {self.metrics.failed_count}个 | {'优秀' if self.metrics.failed_count == 0 else '良好' if self.metrics.failed_count < 3 else '需改进'} |
| 稳定性 | {'稳定' if success_rate >= 95 else '较稳定' if success_rate >= 90 else '不稳定'} | {reliability_grade} |

**可靠性分析**:
- 系统成功分析了{self.metrics.success_count}个群聊，失败{self.metrics.failed_count}个
- 成功率达到{success_rate:.1f}%，{'表现优秀' if success_rate >= 95 else '表现良好' if success_rate >= 90 else '需要改进'}
- {'系统运行稳定，无失败案例' if self.metrics.failed_count == 0 else f'有{self.metrics.failed_count}个群聊分析失败，建议检查失败原因'}

### 7.3 成本评估

| 评估项 | 指标 | 评级 |
| :--- | :--- | :--- |
| Token使用效率 | {avg_tokens_per_room:.1f} tokens/群 | {efficiency_grade} |
| 提示Token占比 | {(self.metrics.prompt_tokens / self.metrics.total_tokens * 100 if self.metrics.total_tokens > 0 else 0):.1f}% | {'优秀' if (self.metrics.prompt_tokens / self.metrics.total_tokens * 100 if self.metrics.total_tokens > 0 else 0) < 70 else '良好'} |
| 完成Token占比 | {(self.metrics.completion_tokens / self.metrics.total_tokens * 100 if self.metrics.total_tokens > 0 else 0):.1f}% | {'优秀' if (self.metrics.completion_tokens / self.metrics.total_tokens * 100 if self.metrics.total_tokens > 0 else 0) < 30 else '良好'} |

**成本分析**:
- 本次测试共消耗{self.metrics.total_tokens:,}个Token
- 平均每个群聊消耗{avg_tokens_per_room:.1f}个Token
- 提示Token占比{(self.metrics.prompt_tokens / self.metrics.total_tokens * 100 if self.metrics.total_tokens > 0 else 0):.1f}%，完成Token占比{(self.metrics.completion_tokens / self.metrics.total_tokens * 100 if self.metrics.total_tokens > 0 else 0):.1f}%
- {'Token使用效率优秀，成本控制良好' if avg_tokens_per_room < 2000 else 'Token使用效率良好，建议优化Prompt以降低成本'}

### 7.4 综合评分

| 维度 | 权重 | 得分 | 加权得分 |
| :--- | :--- | :--- | :--- |
| 性能 | 30% | {30 if performance_grade == '优秀' else 25 if performance_grade == '良好' else 20 if performance_grade == '一般' else 10} | {(30 if performance_grade == '优秀' else 25 if performance_grade == '良好' else 20 if performance_grade == '一般' else 10) * 0.3:.1f} |
| 可靠性 | 40% | {40 if reliability_grade == '优秀' else 35 if reliability_grade == '良好' else 25 if reliability_grade == '一般' else 15} | {(40 if reliability_grade == '优秀' else 35 if reliability_grade == '良好' else 25 if reliability_grade == '一般' else 15) * 0.4:.1f} |
| 成本 | 30% | {30 if efficiency_grade == '优秀' else 25 if efficiency_grade == '良好' else 20 if efficiency_grade == '一般' else 10} | {(30 if efficiency_grade == '优秀' else 25 if efficiency_grade == '良好' else 20 if efficiency_grade == '一般' else 10) * 0.3:.1f} |
| **总分** | **100%** | **{overall_score}** | **{overall_score}** |

**综合评级**: **{overall_grade}**

### 7.5 改进建议

"""
        suggestions = []
        
        if performance_grade in ["一般", "需优化"]:
            suggestions.append("1. **性能优化建议**:\n   - 考虑增加并发数以提高处理速度\n   - 优化LLM调用策略，减少不必要的请求\n   - 对大消息量的群聊进行消息采样或分批处理")
        
        if reliability_grade in ["一般", "需改进"]:
            suggestions.append("2. **可靠性改进建议**:\n   - 检查失败案例的具体错误原因\n   - 增加重试机制以提高成功率\n   - 完善异常处理和错误恢复机制")
        
        if efficiency_grade in ["一般", "需优化"]:
            suggestions.append("3. **成本优化建议**:\n   - 优化Prompt长度，减少提示Token消耗\n   - 对消息进行预处理，过滤无效内容\n   - 考虑使用更经济的模型处理简单任务")
        
        if not suggestions:
            suggestions.append("系统表现优秀，建议保持当前配置并持续监控运行状态。")
        
        evaluation += "\n".join(suggestions)
        
        evaluation += f"""

### 7.6 测试结论

本次测试对系统进行了全面的性能评估，测试覆盖了{self.metrics.total_rooms}个群聊、{self.metrics.total_messages}条消息，执行了{len(self.metrics.analysis_types)}种分析类型。

**主要发现**:
- 系统整体性能{performance_grade}，平均每群分析耗时{avg_time_per_room:.2f}秒
- 系统可靠性{reliability_grade}，成功率达到{success_rate:.1f}%
- Token使用效率{efficiency_grade}，平均每群消耗{avg_tokens_per_room:.1f}个Token

**总体评价**: 系统综合评级为**{overall_grade}**，{'表现优秀，可以投入生产使用。' if overall_grade == '优秀' else '表现良好，建议根据改进建议进行优化后投入使用。' if overall_grade == '良好' else '表现一般，建议根据改进建议进行优化后再投入使用。' if overall_grade == '一般' else '需要改进，建议全面优化后再进行测试。'}

---

*报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
"""
        
        return evaluation

    def generate_visualization_table(self, result: Dict[str, Any], output_file: str = "可视化测试结果表格.md"):
        if "data" not in result:
            print("✗ 没有分析结果数据")
            return
        
        data = result["data"]
        results = data.get("results", [])
        
        table_header = """# 企业微信群聊智能分析测试结果可视化表格

## 测试概览

| 项目 | 内容 |
| :--- | :--- |
| 测试时间 | {test_time} |
| 群聊总数 | {total_rooms} |
| 成功分析 | {success_count} |
| 失败分析 | {failed_count} |
| 成功率 | {success_rate:.1f}% |

## 详细分析结果表格

| 序号 | 权限群聊名称（外部群）（助理机器人、销售VIP账号） | 当日消息总量(体现群活跃程度) | 售后同事回复消息量 | 核心信息摘要（通知/决策/待办） | 高频词统计 | 客户情感分析 | 客户负面情感信息输出 | 售后情感分析 | 售后负面情感信息输出 | 敏感词触发情况 | 风险等级（高/中/低/无） | 备注（建议/跟进要求） |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
""".format(
            test_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            total_rooms=data.get('total_rooms', 0),
            success_count=data.get('success_count', 0),
            failed_count=data.get('failed_count', 0),
            success_rate=(data.get('success_count', 0) / data.get('total_rooms', 1) * 100) if data.get('total_rooms', 0) > 0 else 0
        )
        
        table_rows = []
        
        for i, room_result in enumerate(results, 1):
            room_name = room_result.get("room_name", "未知")
            status = room_result.get("status", "unknown")
            room_data = room_result.get("data", {})
            
            if status == "success" and room_data:
                msg_count = room_data.get("message_count", 0)
                msg_total = f"{msg_count}条"
                
                employee_reply_count = self._extract_employee_reply_count(room_data)
                employee_reply = f"{employee_reply_count}条"
                
                core_summary = self._extract_core_summary(room_data.get("summary", ""))
                
                highfreq_words = self._extract_highfreq_words(room_data.get("high_freq_words", {}))
                
                customer_sentiment = self._extract_customer_sentiment(room_data.get("sentiment", {}))
                customer_negative = self._extract_customer_negative(room_data.get("sentiment", {}))
                
                employee_sentiment = self._extract_employee_sentiment(room_data.get("sentiment", {}))
                employee_negative = self._extract_employee_negative(room_data.get("sentiment", {}))
                
                sensitive_words = self._extract_sensitive_words(room_data.get("sensitive_words", {}))
                
                unanswered = room_data.get("unanswered_status", {})
                risk_level = self._determine_risk_level(room_data, unanswered)
                
                remarks = self._extract_remarks(unanswered)
                
            else:
                msg_total = "-"
                employee_reply = "-"
                core_summary = f"分析失败: {room_result.get('error_message', '未知错误')}"
                highfreq_words = "-"
                customer_sentiment = "-"
                customer_negative = "-"
                employee_sentiment = "-"
                employee_negative = "-"
                sensitive_words = "-"
                risk_level = "高"
                remarks = "需人工核查"
            
            row = f"| {i} | {room_name} | {msg_total} | {employee_reply} | {core_summary} | {highfreq_words} | {customer_sentiment} | {customer_negative} | {employee_sentiment} | {employee_negative} | {sensitive_words} | {risk_level} | {remarks} |"
            table_rows.append(row)
        
        table_content = table_header + "\n".join(table_rows)
        
        table_content += """

## 字段说明

1. **权限群聊名称**: 企业微信群聊的完整名称
2. **当日消息总量**: 群聊中的消息总数，反映群活跃程度
3. **售后同事回复消息量**: 售后人员回复的消息数量
4. **核心信息摘要**: 从群聊中提取的关键信息，包括通知、决策和待办事项
5. **高频词统计**: 群聊中出现频率最高的业务相关词汇
6. **客户情感分析**: 客户消息的情感倾向统计（好评/差评数量）
7. **客户负面情感信息**: 客户表达不满或抱怨的具体消息内容
8. **售后情感分析**: 售后人员消息的情感倾向统计（积极/恶劣数量）
9. **售后负面情感信息**: 售后人员表达恶劣态度的具体消息内容
10. **敏感词触发情况**: 群聊中触发的敏感词详情
11. **风险等级**: 综合评估的风险等级（高/中/低/无）
12. **备注**: 系统生成的跟进建议或人工备注

---

*表格生成时间: {time}*
""".format(time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(table_content)
        
        print(f"✓ 可视化表格已保存到: {output_file}")

    def _extract_employee_reply_count(self, room_data: Dict[str, Any]) -> int:
        sentiment = room_data.get("sentiment", {})
        if not sentiment:
            return 0
        details = sentiment.get("details", [])
        if not details or not isinstance(details, list):
            return 0
        count = 0
        for msg in details:
            if isinstance(msg, dict) and msg.get("sender_role") in ["售后", "员工", "销售"]:
                count += 1
        return count

    def _extract_core_summary(self, summary_text: str) -> str:
        if not summary_text:
            return "-"
        
        parts = []
        
        if "【核心概述】" in summary_text or "核心概述" in summary_text:
            parts.append("已生成摘要")
        
        if "【待办与跟进】" in summary_text or "待办" in summary_text:
            parts.append("有待办事项")
        
        if "【风险/商机预警】" in summary_text:
            if "无" not in summary_text.split("【风险/商机预警】")[1].split("\n")[0]:
                parts.append("有风险预警")
        
        if parts:
            return "<br>".join(parts)
        
        if len(summary_text) > 100:
            return summary_text[:100] + "..."
        return summary_text if summary_text else "-"

    def _extract_highfreq_words(self, highfreq_data: Dict[str, Any]) -> str:
        if not highfreq_data or not highfreq_data.get("words"):
            return "-"
        
        words = highfreq_data.get("words", [])[:5]
        if not words:
            return "-"
        
        word_list = []
        for w in words:
            word = w.get("word", "")
            count = w.get("count", 0)
            if word:
                word_list.append(f"{word}（{count}次）")
        
        return "、".join(word_list) if word_list else "-"

    def _extract_customer_sentiment(self, sentiment_data: Dict[str, Any]) -> str:
        if not sentiment_data:
            return "-"
        
        summary = sentiment_data.get("summary", {})
        customer = summary.get("customer", {})
        
        good = customer.get("good_reviews", 0)
        bad = customer.get("bad_reviews", 0)
        
        if good == 0 and bad == 0:
            return "无数据"
        
        return f"好评{good}条、差评{bad}条"

    def _extract_customer_negative(self, sentiment_data: Dict[str, Any]) -> str:
        if not sentiment_data:
            return "无"
        
        details = sentiment_data.get("details", [])
        if not isinstance(details, list):
            return "无"
        
        negative_msgs = []
        
        for msg in details:
            if isinstance(msg, dict) and msg.get("sender_role") == "客户" and msg.get("sentiment") in ["negative", "very_negative"]:
                content = msg.get("content", "")
                if content:
                    negative_msgs.append(content[:50])
        
        if not negative_msgs:
            return "无"
        
        return "<br>".join(negative_msgs[:3]) if negative_msgs else "无"

    def _extract_employee_sentiment(self, sentiment_data: Dict[str, Any]) -> str:
        if not sentiment_data:
            return "-"
        
        summary = sentiment_data.get("summary", {})
        employee = summary.get("employee", {})
        
        positive = employee.get("positive", 0)
        bad = employee.get("bad_attitude", 0)
        
        if positive == 0 and bad == 0:
            return "无数据"
        
        return f"积极{positive}条、恶劣{bad}条"

    def _extract_employee_negative(self, sentiment_data: Dict[str, Any]) -> str:
        if not sentiment_data:
            return "无"
        
        details = sentiment_data.get("details", [])
        if not isinstance(details, list):
            return "无"
        
        negative_msgs = []
        
        for msg in details:
            if isinstance(msg, dict) and msg.get("sender_role") in ["售后", "员工", "销售"] and msg.get("sentiment") == "bad_attitude":
                content = msg.get("content", "")
                if content:
                    negative_msgs.append(content[:50])
        
        if not negative_msgs:
            return "无"
        
        return "<br>".join(negative_msgs[:3]) if negative_msgs else "无"

    def _extract_sensitive_words(self, sensitive_data: Dict[str, Any]) -> str:
        if not sensitive_data:
            return "无"
        
        total_hits = sensitive_data.get("total_hits", 0)
        if total_hits == 0:
            return "无"
        
        words = sensitive_data.get("words", [])
        if not words:
            return f"触发{total_hits}次"
        
        word_list = []
        for w in words[:5]:
            word = w.get("word", "")
            count = w.get("count", 0)
            if word:
                word_list.append(f"{word}（{count}次）")
        
        return "、".join(word_list) if word_list else f"触发{total_hits}次"

    def _determine_risk_level(self, room_data: Dict[str, Any], unanswered: Dict[str, Any]) -> str:
        risk_score = 0
        
        sentiment = room_data.get("sentiment", {})
        if sentiment:
            summary = sentiment.get("summary", {})
            customer = summary.get("customer", {})
            if customer.get("bad_reviews", 0) > 0:
                risk_score += 2
            
            employee = summary.get("employee", {})
            if employee.get("bad_attitude", 0) > 0:
                risk_score += 3
        
        sensitive = room_data.get("sensitive_words", {})
        if sensitive and sensitive.get("total_hits", 0) > 0:
            risk_score += 2
        
        if unanswered:
            if unanswered.get("is_missed", False):
                risk_score += 2
            if unanswered.get("risk_level") == "high":
                risk_score += 2
        
        if risk_score >= 6:
            return "高"
        elif risk_score >= 3:
            return "中"
        elif risk_score >= 1:
            return "低"
        else:
            return "无"

    def _extract_remarks(self, unanswered: Dict[str, Any]) -> str:
        if not unanswered:
            return "-"
        
        if unanswered.get("is_missed", False):
            action = unanswered.get("suggested_action", "")
            if action:
                return action[:50] if len(action) > 50 else action
            return "存在漏回，需跟进"
        
        return "-"

    def run_test(self, analysis_types: Optional[List[str]] = None, save_report: bool = False, generate_doc: bool = False, generate_table: bool = False):
        print("\n" + "="*80)
        print("企业微信群聊分析服务 - 系统性能完整测试")
        print("="*80)
        print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Python服务: {self.analysis_service_url}")
        print(f"Java数据源: {self.data_source_url}")

        rooms = self.fetch_data_from_java()

        if not rooms:
            print("\n⚠️ Java接口返回数据为空")
            print("  可能原因:")
            print("  1. Java服务未启动或接口地址不正确")
            print("  2. 当前没有群聊消息数据")
            print("  3. 接口认证失败")
            return

        if analysis_types is None:
            analysis_types = ["sentiment", "sensitive", "summary", "highfreq", "unanswered"]

        result = self.batch_analyze(rooms, analysis_types)
        self.print_performance_report(result)
        
        if save_report:
            self.save_report_to_file(result)
        
        if generate_doc:
            self.generate_markdown_report(result)
        
        if generate_table:
            self.generate_visualization_table(result)

    def close(self):
        self.http_client.close()


def main():
    parser = argparse.ArgumentParser(description="企业微信群聊分析服务系统性能测试")
    parser.add_argument(
        "--data-source",
        type=str,
        default=settings.JAVA_DATA_SOURCE_URL,
        help=f"Java数据源地址 (默认: {settings.JAVA_DATA_SOURCE_URL})",
    )
    parser.add_argument(
        "--service-url",
        type=str,
        default="http://localhost:8000",
        help="Python分析服务地址 (默认: http://localhost:8000)",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=5,
        help="最大并发数 (默认: 5)",
    )
    parser.add_argument(
        "--max-rooms",
        type=int,
        default=None,
        help="最大测试群聊数量 (默认: 全部测试)",
    )
    parser.add_argument(
        "--types",
        type=str,
        nargs="+",
        default=["sentiment", "sensitive", "summary", "highfreq", "unanswered"],
        choices=["sentiment", "sensitive", "summary", "highfreq", "unanswered"],
        help="分析类型 (默认全部)",
    )
    parser.add_argument(
        "--save-report",
        action="store_true",
        help="保存性能报告到JSON文件",
    )
    parser.add_argument(
        "--generate-doc",
        action="store_true",
        help="生成Markdown格式的测试文档",
    )
    parser.add_argument(
        "--generate-table",
        action="store_true",
        help="生成可视化表格",
    )

    args = parser.parse_args()

    tester = SystemPerformanceTester(
        data_source_url=args.data_source,
        analysis_service_url=args.service_url,
        max_concurrent=args.max_concurrent,
        max_rooms=args.max_rooms,
    )

    try:
        tester.run_test(analysis_types=args.types, save_report=args.save_report, generate_doc=args.generate_doc, generate_table=args.generate_table)
    finally:
        tester.close()


if __name__ == "__main__":
    main()

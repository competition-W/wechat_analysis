#!/usr/bin/env python3
"""
企业微信群聊分析服务 - 真实数据测试脚本

功能：
1. 从Java数据源接口拉取真实消息数据
2. 调用Python分析服务进行完整分析
3. 输出详细分析报告

使用方式：
    python tests/test_with_real_data.py

环境变量：
    JAVA_DATA_SOURCE_URL - Java数据源地址（默认: http://192.168.0.129:8081/qxChat/）
    ANALYSIS_SERVICE_URL - 分析服务地址（默认: http://localhost:8000）
"""

import json
import sys
import time
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
from datetime import datetime

import httpx

sys.path.insert(0, str(__file__).rsplit("/tests", 1)[0])

from config.settings import settings


@dataclass
class DataSourceResponse:
    code: int
    status: bool
    message: str
    data: List[Dict[str, Any]]


@dataclass
class AnalysisReport:
    room_id: str
    room_name: Optional[str]
    message_count: int
    analysis_time: str
    sentiment_summary: Dict[str, int]
    sentiment_by_role: Dict[str, Dict[str, int]]
    sensitive_word_hits: int
    sensitive_words: List[Dict]
    summary: Optional[str]
    high_freq_words: List[Dict]


class RealDataTester:
    def __init__(
        self,
        data_source_url: Optional[str] = None,
        analysis_service_url: Optional[str] = None,
    ):
        self.data_source_url = data_source_url or settings.JAVA_DATA_SOURCE_URL
        self.analysis_service_url = analysis_service_url or "http://localhost:8000"
        self.http_client = httpx.Client(timeout=settings.JAVA_DATA_SOURCE_TIMEOUT)

    def fetch_data_from_java(self) -> DataSourceResponse:
        print(f"\n{'='*60}")
        print(f"步骤1: 从Java数据源拉取数据")
        print(f"{'='*60}")
        print(f"数据源地址: {self.data_source_url}")
        print()

        try:
            response = self.http_client.get(self.data_source_url)
            response.raise_for_status()
            raw_data = response.json()

            result = DataSourceResponse(
                code=raw_data.get("code", 0),
                status=raw_data.get("status", False),
                message=raw_data.get("message", ""),
                data=raw_data.get("data", []),
            )

            print(f"✓ 数据拉取成功!")
            print(f"  - 状态码: {result.code}")
            print(f"  - 消息数量: {len(result.data)}")
            print(f"  - 状态: {result.message}")

            return result

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

    def analyze_with_python_service(
        self,
        messages: List[Dict[str, Any]],
        room_id: Optional[str] = None,
        room_name: Optional[str] = None,
        analysis_types: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        print(f"\n{'='*60}")
        print(f"步骤2: 调用Python分析服务")
        print(f"{'='*60}")
        print(f"分析服务地址: {self.analysis_service_url}/api/v1/chat/analyze")
        print(f"消息数量: {len(messages)}")
        print(f"分析类型: {analysis_types or ['sentiment', 'sensitive', 'summary', 'highfreq']}")
        print()

        if not messages:
            print("✗ 没有消息可分析")
            sys.exit(1)

        extracted_room_id = room_id
        if not extracted_room_id and messages:
            extracted_room_id = messages[0].get("roomid", f"unknown-{int(time.time())}")

        request_body = {
            "room_id": extracted_room_id or f"test-room-{int(time.time())}",
            "room_name": room_name,
            "analysis_type": analysis_types or ["sentiment", "sensitive", "summary", "highfreq"],
            "messages": messages,
        }

        try:
            start_time = time.time()
            response = self.http_client.post(
                f"{self.analysis_service_url}/api/v1/chat/analyze",
                json=request_body,
                timeout=300,
            )
            elapsed = time.time() - start_time

            response.raise_for_status()
            result = response.json()

            print(f"✓ 分析完成! (耗时: {elapsed:.2f}秒)")
            print(f"  - 返回码: {result.get('code')}")
            print(f"  - 消息: {result.get('message')}")

            if "data" in result:
                data = result["data"]
                print(f"  - 处理消息数: {data.get('message_count', 0)}")

            return result

        except httpx.ConnectError as e:
            print(f"✗ 连接失败: {e}")
            print(f"  请确认Python分析服务 ({self.analysis_service_url}) 已启动")
            print(f"  启动命令: uvicorn api.main:app --host 0.0.0.0 --port 8000")
            sys.exit(1)
        except httpx.TimeoutException:
            print(f"✗ 请求超时 (分析时间超过300秒)")
            sys.exit(1)
        except Exception as e:
            print(f"✗ 分析失败: {e}")
            sys.exit(1)

    def print_analysis_report(self, result: Dict[str, Any]):
        print(f"\n{'='*60}")
        print(f"步骤3: 分析报告")
        print(f"{'='*60}")

        if "data" not in result:
            print("✗ 没有返回数据")
            return

        data = result["data"]

        print(f"\n【基本信息】")
        print(f"  群组ID: {data.get('room_id', 'N/A')}")
        print(f"  群组名称: {data.get('room_name', 'N/A')}")
        print(f"  分析时间: {data.get('analysis_time', 'N/A')}")
        print(f"  消息数量: {data.get('message_count', 0)}")

        if data.get("sentiment"):
            sentiment = data["sentiment"]
            summary = sentiment.get("summary", {})

            print(f"\n【情感分析】")
            print(f"  积极 (positive): {summary.get('positive', 0)}")
            print(f"  中性 (neutral): {summary.get('neutral', 0)}")
            print(f"  消极 (negative): {summary.get('negative', 0)}")
            print(f"  恶劣 (very_negative): {summary.get('very_negative', 0)}")

            by_role = sentiment.get("by_role", {})
            if by_role:
                print(f"  按角色统计:")
                for role, stats in by_role.items():
                    print(f"    - {role}: 积极={stats.get('positive',0)}, 中性={stats.get('neutral',0)}, 消极={stats.get('negative',0)}, 恶劣={stats.get('very_negative',0)}")

            alerts = sentiment.get("alerts", [])
            if alerts:
                print(f"  ⚠️ 预警消息 ({len(alerts)}条):")
                for alert in alerts[:5]:
                    print(f"    - [{alert.get('sentiment')}] {alert.get('sender_name')}: {alert.get('content', '')[:50]}...")

        if data.get("sensitive_words"):
            sensitive = data["sensitive_words"]
            print(f"\n【敏感词检测】")
            print(f"  总命中次数: {sensitive.get('total_hits', 0)}")

            words = sensitive.get("words", [])
            if words:
                print(f"  命中词汇 ({len(words)}个):")
                for w in words:
                    print(f"    - {w.get('word')}: {w.get('count')}次")
                    for hit in w.get("hits", [])[:2]:
                        print(f"        来源: {hit.get('sender_name')} ({hit.get('sender_job')})")

        if data.get("summary"):
            print(f"\n【摘要生成】")
            summary_text = data["summary"]
            print(f"  {summary_text[:300]}{'...' if len(summary_text) > 300 else ''}")

        if data.get("high_freq_words"):
            highfreq = data["high_freq_words"]
            print(f"\n【高频词统计】(Top10)")
            words = highfreq.get("words", [])
            for i, w in enumerate(words, 1):
                print(f"  {i}. {w.get('word')}: {w.get('count')}次")
                sources = w.get("sources", [])
                if sources:
                    src = sources[0]
                    print(f"      主要来源: {src.get('sender_name')} ({src.get('sender_role')})")

    def run_full_test(
        self,
        room_name: Optional[str] = None,
        analysis_types: Optional[List[str]] = None,
    ):
        print("\n" + "="*60)
        print("企业微信群聊分析服务 - 真实数据测试")
        print("="*60)
        print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Python服务: {self.analysis_service_url}")
        print(f"Java数据源: {self.data_source_url}")

        data_response = self.fetch_data_from_java()

        if not data_response.data:
            print("\n⚠️ Java接口返回数据为空")
            print("  可能原因:")
            print("  1. Java服务未启动或接口地址不正确")
            print("  2. 当前没有群聊消息数据")
            print("  3. 接口认证失败")
            return

        result = self.analyze_with_python_service(
            messages=data_response.data,
            room_name=room_name,
            analysis_types=analysis_types,
        )

        self.print_analysis_report(result)

        print(f"\n{'='*60}")
        print("测试完成!")
        print(f"{'='*60}\n")

    def close(self):
        self.http_client.close()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="企业微信群聊分析服务真实数据测试")
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
        "--room-name",
        type=str,
        default=None,
        help="群组名称",
    )
    parser.add_argument(
        "--types",
        type=str,
        nargs="+",
        default=None,
        choices=["sentiment", "sensitive", "summary", "highfreq"],
        help="分析类型 (默认全部)",
    )

    args = parser.parse_args()

    tester = RealDataTester(
        data_source_url=args.data_source,
        analysis_service_url=args.service_url,
    )

    try:
        tester.run_full_test(
            room_name=args.room_name,
            analysis_types=args.types,
        )
    finally:
        tester.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
企业微信群聊分析服务 - 批量分析测试脚本

功能：
1. 从Java数据源接口拉取真实消息数据
2. 将数据按群分组
3. 调用批量分析接口一次性分析所有群
4. 输出详细分析报告

使用方式：
    python tests/test_batch_analyze.py

环境变量：
    JAVA_DATA_SOURCE_URL - Java数据源地址（默认: http://192.168.0.129:8081/qxChat/）
    ANALYSIS_SERVICE_URL - 分析服务地址（默认: http://localhost:8000）
"""

import json
import sys
import time
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime
from collections import defaultdict
import argparse

import httpx

sys.path.insert(0, str(__file__).rsplit("/tests", 1)[0])

from config.settings import settings

@dataclass
class RoomData:
    room_id: str
    room_name: Optional[str]
    messages: List[Dict[str, Any]]


class BatchAnalyzeTester:
    def __init__(
        self,
        data_source_url: Optional[str] = None,
        analysis_service_url: Optional[str] = None,
        max_concurrent: int = 5,
    ):
        self.data_source_url = data_source_url or settings.JAVA_DATA_SOURCE_URL
        self.analysis_service_url = analysis_service_url or "http://localhost:8000"
        self.max_concurrent = max_concurrent
        self.http_client = httpx.Client(timeout=600)

    def fetch_data_from_java(self) -> List[RoomData]:
        print(f"\n{'='*60}")
        print(f"步骤1: 从Java数据源拉取数据")
        print(f"{'='*60}")
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

    def batch_analyze(self, rooms: List[RoomData]) -> Dict[str, Any]:
        print(f"\n{'='*60}")
        print(f"步骤2: 调用批量分析接口")
        print(f"{'='*60}")
        print(f"分析服务地址: {self.analysis_service_url}/api/v1/chat/batch-analyze")
        print(f"群聊数量: {len(rooms)}")
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
            "analysis_type": ["sentiment", "sensitive", "summary", "highfreq"],
            "max_concurrent": self.max_concurrent
        }

        try:
            start_time = time.time()
            response = self.http_client.post(
                f"{self.analysis_service_url}/api/v1/chat/batch-analyze",
                json=request_body,
                timeout=600,
            )
            elapsed = time.time() - start_time

            response.raise_for_status()
            result = response.json()

            print(f"✓ 批量分析完成! (耗时: {elapsed:.2f}秒)")
            print(f"  - 返回码: {result.get('code')}")
            print(f"  - 状态: {result.get('message')}")

            if "data" in result:
                data = result["data"]
                print(f"  - 总群数: {data.get('total_rooms', 0)}")
                print(f"  - 成功: {data.get('success_count', 0)}")
                print(f"  - 失败: {data.get('failed_count', 0)}")
                print(f"  - 总耗时: {data.get('elapsed_seconds', 0)}秒")

            return result

        except httpx.ConnectError as e:
            print(f"✗ 连接失败: {e}")
            print(f"  请确认Python分析服务 ({self.analysis_service_url}) 已启动")
            print(f"  启动命令: uvicorn api.main:app --host 0.0.0.0 --port 8000")
            sys.exit(1)
        except httpx.TimeoutException:
            print(f"✗ 请求超时 (分析时间超过600秒)")
            sys.exit(1)
        except Exception as e:
            print(f"✗ 批量分析失败: {e}")
            sys.exit(1)

    def print_batch_report(self, result: Dict[str, Any]):
        print(f"\n{'='*60}")
        print(f"步骤3: 批量分析报告")
        print(f"{'='*60}")

        if "data" not in result:
            print("✗ 没有返回数据")
            return

        data = result["data"]
        results = data.get("results", [])

        print(f"\n【批量分析汇总】")
        print(f"  总群数: {data.get('total_rooms', 0)}")
        print(f"  成功: {data.get('success_count', 0)}")
        print(f"  失败: {data.get('failed_count', 0)}")
        print(f"  总耗时: {data.get('elapsed_seconds', 0)}秒")

        print(f"\n【各群分析结果】")
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
                    print(f"     情感: 积极={summary.get('positive', 0)}, "
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
            else:
                error = room_result.get("error_message", "未知错误")
                print(f"     错误: {error}")

        print(f"\n{'='*60}")
        print("批量分析测试完成!")
        print(f"{'='*60}\n")

    def run_test(self, analysis_types: Optional[List[str]] = None):
        print("\n" + "="*60)
        print("企业微信群聊分析服务 - 批量分析测试")
        print("="*60)
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

        result = self.batch_analyze(rooms)
        self.print_batch_report(result)

    def close(self):
        self.http_client.close()


def main():
    parser = argparse.ArgumentParser(description="企业微信群聊分析服务批量分析测试")
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
        "--types",
        type=str,
        nargs="+",
        default=["sentiment", "sensitive", "summary", "highfreq"],
        choices=["sentiment", "sensitive", "summary", "highfreq"],
        help="分析类型 (默认全部)",
    )

    args = parser.parse_args()

    tester = BatchAnalyzeTester(
        data_source_url=args.data_source,
        analysis_service_url=args.service_url,
        max_concurrent=args.max_concurrent,
    )

    try:
        tester.run_test(analysis_types=args.types)
    finally:
        tester.close()


if __name__ == "__main__":
    main()

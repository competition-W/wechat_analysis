#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从Java接口获取真实群名称并更新JSON文件
"""

import json
import urllib.request
import urllib.error
from collections import defaultdict

def main():
    print("正在从Java接口获取数据...")
    
    java_url = "http://192.168.0.129:8081/qxChat/"
    
    try:
        with urllib.request.urlopen(java_url, timeout=1200) as response:
            raw_data = json.loads(response.read().decode('utf-8'))
        
        messages = raw_data.get("data", [])
        print(f"✓ 获取到 {len(messages)} 条消息")
        
        rooms_dict = defaultdict(lambda: {"room_id": "", "room_name": ""})
        
        for msg in messages:
            room_id = msg.get("roomid", "unknown")
            if not rooms_dict[room_id]["room_id"]:
                rooms_dict[room_id]["room_id"] = room_id
                room_name = msg.get("re_truename") or msg.get("roomname") or f"群-{room_id[:8]}"
                rooms_dict[room_id]["room_name"] = room_name
        
        print(f"\n找到 {len(rooms_dict)} 个群聊:")
        for room_id, info in list(rooms_dict.items())[:10]:
            print(f"  - {info['room_name']}")
        
        print(f"\n正在更新JSON文件...")
        
        with open('performance_report.json', 'r', encoding='utf-8') as f:
            json_data = json.load(f)
        
        results = json_data.get("analysis_result", {}).get("results", [])
        
        updated_count = 0
        for result in results:
            room_id = result.get("room_id", "")
            if room_id in rooms_dict:
                real_name = rooms_dict[room_id]["room_name"]
                result["room_name"] = real_name
                if "data" in result:
                    result["data"]["room_name"] = real_name
                updated_count += 1
        
        with open('performance_report.json', 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)
        
        print(f"✓ 已更新 {updated_count} 个群聊的名称")
        
    except Exception as e:
        print(f"✗ 错误: {e}")
        import traceback
        traceback.print_exc()
        return
    
    print("\n正在生成HTML表格...")
    
    from tests.test_system_performance import SystemPerformanceTester
    tester = SystemPerformanceTester()
    tester.generate_html_table(json_data, "可视化测试结果表格.html")
    
    print("\n✓ 完成！请打开 可视化测试结果表格.html 查看")

if __name__ == '__main__':
    main()

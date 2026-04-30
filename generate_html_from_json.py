#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
使用现有JSON数据生成HTML表格
"""

import json
import sys
from datetime import datetime

sys.path.insert(0, '/mnt/ai/omicshub/wechat_analysis')

from tests.test_system_performance import SystemPerformanceTester

def main():
    json_file = 'performance_report.json'
    
    print(f"正在加载JSON数据: {json_file}")
    with open(json_file, 'r', encoding='utf-8') as f:
        json_data = json.load(f)
    
    print("正在生成HTML表格...")
    tester = SystemPerformanceTester()
    tester.generate_html_table(json_data, "可视化测试结果表格.html")
    
    print("\n✓ 完成！请打开 可视化测试结果表格.html 查看")

if __name__ == '__main__':
    main()

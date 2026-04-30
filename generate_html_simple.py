#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成HTML可视化表格
"""

import json
from datetime import datetime

def format_summary(summary_text):
    if not summary_text or summary_text == "群内互动较少，暂无核心议题。":
        return summary_text
    return summary_text.replace('\n', '<br>')

def format_highfreq_words(highfreq_data):
    if not highfreq_data or not highfreq_data.get('words'):
        return '-'
    
    words = highfreq_data['words'][:5]
    if not words:
        return '-'
    
    word_list = []
    for w in words:
        word = w.get("word", "")
        count = w.get("count", 0)
        if word:
            word_list.append(f"{word}（{count}次）")
    
    return "<br>".join(word_list) if word_list else "-"

def format_unanswered_analysis(unanswered_data):
    if not unanswered_data:
        return "✅无漏回"
    
    is_missed = unanswered_data.get("is_missed", False)
    
    if is_missed:
        suggested_action = unanswered_data.get("suggested_action", "请及时跟进")
        return f"❗存在漏回<br>建议：{suggested_action}"
    else:
        return "✅无漏回"

def format_customer_sentiment(sentiment_data):
    if not sentiment_data:
        return "无数据"
    
    summary = sentiment_data.get("summary", {})
    customer = summary.get("customer", {})
    
    good = customer.get("good_reviews", 0)
    bad = customer.get("bad_reviews", 0)
    
    if good == 0 and bad == 0:
        return "无数据"
    
    return f"😊好评：{good}<br>😠差评：{bad}"

def format_employee_sentiment(sentiment_data):
    if not sentiment_data:
        return "无数据"
    
    summary = sentiment_data.get("summary", {})
    employee = summary.get("employee", {})
    
    positive = employee.get("positive", 0)
    bad = employee.get("bad_attitude", 0)
    
    if positive == 0 and bad == 0:
        return "无数据"
    
    return f"🌟积极：{positive}<br>⚠️态度：{bad}"

def format_customer_negative(sentiment_data):
    if not sentiment_data:
        return "无"
    
    details = sentiment_data.get("details", {})
    bad_list = details.get("customer_bad", [])
    
    if not bad_list:
        return "无"
    
    negative_items = []
    for item in bad_list[:3]:
        content = item.get("content", "")
        sender = item.get("sender_name", "")
        if content:
            negative_items.append(f"{sender}：{content}")
    
    return "<br>".join(negative_items) if negative_items else "无"

def format_employee_negative(sentiment_data):
    if not sentiment_data:
        return "无"
    
    details = sentiment_data.get("details", {})
    bad_list = details.get("employee_bad_attitude", [])
    
    if not bad_list:
        return "无"
    
    negative_items = []
    for item in bad_list[:3]:
        content = item.get("content", "")
        sender = item.get("sender_name", "")
        if content:
            negative_items.append(f"{sender}：{content}")
    
    return "<br>".join(negative_items) if negative_items else "无"

def format_sensitive_words(sensitive_data):
    if not sensitive_data or sensitive_data.get("total_hits", 0) == 0:
        return "无"
    
    words = sensitive_data.get("words", [])
    if not words:
        return "无"
    
    formatted_words = []
    for word_info in words[:5]:
        word = word_info.get("word", "")
        count = word_info.get("count", 0)
        if word:
            formatted_words.append(f"{word}（{count}次）")
    
    return "<br>".join(formatted_words) if formatted_words else "无"

def determine_risk_level(room_data, unanswered):
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
        return "🔴高"
    elif risk_score >= 3:
        return "🟡中"
    elif risk_score >= 1:
        return "🟢低"
    else:
        return "无"

def extract_remarks(room_data, unanswered):
    notes = []
    
    summary = room_data.get("summary", "")
    
    if "【待办与跟进】" in summary:
        todo_start = summary.find("【待办与跟进】")
        risk_start = summary.find("【风险/商机预警】")
        if risk_start > todo_start:
            todo_section = summary[todo_start:risk_start]
        else:
            todo_section = summary[todo_start:]
        
        lines = todo_section.split('\n')
        for line in lines:
            if '[待办]' in line:
                todo = line.replace('[待办]：', '').replace('[待办]:', '').strip()
                if todo and todo != '':
                    notes.append(f"- {todo}")
    
    if unanswered and unanswered.get("is_missed") and unanswered.get("suggested_action"):
        notes.append(f"漏报建议：{unanswered.get('suggested_action')}")
    
    return '<br>'.join(notes) if notes else '-'

def generate_html_table(json_file, output_file):
    print(f"正在加载JSON数据: {json_file}")
    with open(json_file, 'r', encoding='utf-8') as f:
        json_data = json.load(f)
    
    data = json_data.get("analysis_result", {})
    results = data.get("results", [])
    
    print(f"正在生成HTML表格...")
    
    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>企业微信群聊智能分析测试结果</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            min-height: 100vh;
        }}
        
        .container {{
            max-width: 1800px;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }}
        
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }}
        
        .header h1 {{
            font-size: 28px;
            margin-bottom: 10px;
        }}
        
        .header p {{
            opacity: 0.9;
            font-size: 14px;
        }}
        
        .overview {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            padding: 30px;
            background: #f8f9fa;
        }}
        
        .overview-card {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            text-align: center;
        }}
        
        .overview-card .label {{
            color: #6c757d;
            font-size: 14px;
            margin-bottom: 8px;
        }}
        
        .overview-card .value {{
            font-size: 24px;
            font-weight: bold;
            color: #667eea;
        }}
        
        .table-container {{
            padding: 30px;
            overflow-x: auto;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            background: white;
        }}
        
        thead {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }}
        
        th {{
            padding: 15px 10px;
            text-align: left;
            font-weight: 600;
            white-space: nowrap;
            border: none;
        }}
        
        td {{
            padding: 12px 10px;
            border-bottom: 1px solid #e9ecef;
            vertical-align: top;
        }}
        
        tr:hover {{
            background: #f8f9fa;
        }}
        
        .risk-high {{
            color: #dc3545;
            font-weight: bold;
        }}
        
        .risk-medium {{
            color: #ffc107;
            font-weight: bold;
        }}
        
        .risk-low {{
            color: #28a745;
            font-weight: bold;
        }}
        
        .summary-content {{
            max-width: 400px;
            line-height: 1.6;
        }}
        
        .footer {{
            text-align: center;
            padding: 20px;
            background: #f8f9fa;
            color: #6c757d;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>企业微信群聊智能分析测试结果</h1>
            <p>测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>
        
        <div class="overview">
            <div class="overview-card">
                <div class="label">群聊总数</div>
                <div class="value">{data.get('total_rooms', 0)}</div>
            </div>
            <div class="overview-card">
                <div class="label">成功分析</div>
                <div class="value">{data.get('success_count', 0)}</div>
            </div>
            <div class="overview-card">
                <div class="label">失败分析</div>
                <div class="value">{data.get('failed_count', 0)}</div>
            </div>
            <div class="overview-card">
                <div class="label">成功率</div>
                <div class="value">{(data.get('success_count', 0) / data.get('total_rooms', 1) * 100) if data.get('total_rooms', 0) > 0 else 0:.1f}%</div>
            </div>
        </div>
        
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th>序号</th>
                        <th>权限群聊名称</th>
                        <th>当日消息总量</th>
                        <th>售后回复量</th>
                        <th>核心信息摘要</th>
                        <th>漏报消息分析</th>
                        <th>高频词统计</th>
                        <th>客户情感分析</th>
                        <th>客户负面内容</th>
                        <th>售后情感分析</th>
                        <th>售后负面内容</th>
                        <th>敏感词触发</th>
                        <th>风险等级</th>
                        <th>备注</th>
                    </tr>
                </thead>
                <tbody>
"""
    
    for i, room_result in enumerate(results, 1):
        room_name = room_result.get("room_name", "未知")
        status = room_result.get("status", "unknown")
        room_data = room_result.get("data", {})
        
        if status == "success" and room_data:
            msg_count = room_data.get("message_count", 0)
            msg_total = f"{msg_count}条"
            
            sentiment = room_data.get("sentiment", {})
            if sentiment:
                summary = sentiment.get("summary", {})
                employee = summary.get("employee", {})
                employee_reply_count = employee.get("positive", 0) + employee.get("bad_attitude", 0)
            else:
                employee_reply_count = 0
            employee_reply = f"{employee_reply_count}条"
            
            core_summary = format_summary(room_data.get("summary", ""))
            
            unanswered_analysis = format_unanswered_analysis(room_data.get("unanswered_status", {}))
            
            highfreq_words = format_highfreq_words(room_data.get("high_freq_words", {}))
            
            customer_sentiment = format_customer_sentiment(room_data.get("sentiment", {}))
            customer_negative = format_customer_negative(room_data.get("sentiment", {}))
            
            employee_sentiment = format_employee_sentiment(room_data.get("sentiment", {}))
            employee_negative = format_employee_negative(room_data.get("sentiment", {}))
            
            sensitive_words = format_sensitive_words(room_data.get("sensitive_words", {}))
            
            unanswered = room_data.get("unanswered_status", {})
            risk_level = determine_risk_level(room_data, unanswered)
            
            remarks = extract_remarks(room_data, unanswered)
            
        else:
            msg_total = "-"
            employee_reply = "-"
            core_summary = f"分析失败: {room_result.get('error_message', '未知错误')}"
            unanswered_analysis = "✅无漏回"
            highfreq_words = "-"
            customer_sentiment = "无数据"
            customer_negative = "无"
            employee_sentiment = "无数据"
            employee_negative = "无"
            sensitive_words = "无"
            risk_level = "🔴高"
            remarks = "需人工核查"
        
        risk_class = ""
        if "🔴" in risk_level:
            risk_class = "risk-high"
        elif "🟡" in risk_level:
            risk_class = "risk-medium"
        elif "🟢" in risk_level:
            risk_class = "risk-low"
        
        html_content += f"""
                    <tr>
                        <td>{i}</td>
                        <td><strong>{room_name}</strong></td>
                        <td>{msg_total}</td>
                        <td>{employee_reply}</td>
                        <td class="summary-content">{core_summary}</td>
                        <td>{unanswered_analysis}</td>
                        <td>{highfreq_words}</td>
                        <td>{customer_sentiment}</td>
                        <td>{customer_negative}</td>
                        <td>{employee_sentiment}</td>
                        <td>{employee_negative}</td>
                        <td>{sensitive_words}</td>
                        <td class="{risk_class}">{risk_level}</td>
                        <td>{remarks}</td>
                    </tr>
"""
    
    html_content += """
                </tbody>
            </table>
        </div>
        
        <div class="footer">
            <p>企业微信群聊智能分析系统 | 自动生成报告</p>
            <p>如有疑问请联系技术支持</p>
        </div>
    </div>
</body>
</html>
"""
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"✓ HTML可视化表格已保存到: {output_file}")

if __name__ == '__main__':
    generate_html_table('performance_report.json', '可视化测试结果表格.html')

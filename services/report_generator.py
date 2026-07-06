#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
报告生成器：将聚合后的报告数据渲染为独立 HTML 页面（ECharts 可视化）。
"""

import json
import os
from datetime import datetime
from typing import Dict, Any, Optional
from loguru import logger
from services._report_sections import generate_qxchat_js



def _escape_json(obj: Any) -> str:
    """将 Python 对象转为安全的 JSON 字符串，用于嵌入 HTML"""
    s = json.dumps(obj, ensure_ascii=False)
    return s.replace("</script>", "<</script>")


def generate_report_html(report_data: Dict[str, Any]) -> str:
    """生成完整的 HTML 报告页面"""
    data_json = _escape_json(report_data)
    summary = report_data.get("summary", {})
    tr = report_data.get("report_time_range", {})

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{report_data.get("report_title", "群聊数据统计分析报告")}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.6.0/dist/echarts.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    background: #f0f2f5; color: #333; min-height: 100vh;
}}
.container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}

/* Header */
.header {{
    background: linear-gradient(135deg, #1a73e8 0%, #0d47a1 100%);
    color: #fff; padding: 30px; border-radius: 12px; margin-bottom: 24px;
}}
.header h1 {{ font-size: 26px; margin-bottom: 6px; }}
.header p {{ opacity: 0.85; font-size: 14px; }}

/* TOC */
.toc {{
    background: #fff; border-radius: 10px; padding: 20px; margin-bottom: 24px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
}}
.toc h3 {{ margin-bottom: 12px; font-size: 16px; color: #1a73e8; }}
.toc-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 8px; }}
.toc a {{
    display: block; padding: 8px 12px; background: #f6f8fa; border-radius: 6px;
    color: #333; text-decoration: none; font-size: 13px; transition: all 0.2s;
}}
.toc a:hover {{ background: #e3f2fd; color: #1a73e8; }}

/* Summary Cards */
.summary-cards {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 16px; margin-bottom: 24px;
}}
.card {{
    background: #fff; border-radius: 10px; padding: 20px; text-align: center;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08); transition: transform 0.2s;
}}
.card:hover {{ transform: translateY(-2px); }}
.card .value {{ font-size: 28px; font-weight: 700; color: #1a73e8; }}
.card .label {{ font-size: 13px; color: #666; margin-top: 6px; }}

/* Module Section */
.module {{
    background: #fff; border-radius: 10px; padding: 24px; margin-bottom: 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
}}
.module h2 {{ font-size: 18px; color: #1a73e8; margin-bottom: 16px; padding-bottom: 8px; border-bottom: 2px solid #e8eaf6; }}
.module .badge {{
    display: inline-block; background: #e3f2fd; color: #1a73e8;
    padding: 2px 10px; border-radius: 10px; font-size: 12px; font-weight: 600;
}}

/* Chart container */
.chart-box {{ width: 100%; height: 400px; }}
.chart-box-half {{ width: 100%; height: 350px; }}

/* Tables */
table.report-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
table.report-table th {{
    background: #f5f7fa; color: #333; font-weight: 600; padding: 10px 12px;
    text-align: left; border-bottom: 2px solid #e8eaed;
}}
table.report-table td {{ padding: 8px 12px; border-bottom: 1px solid #f0f0f0; }}
table.report-table tr:hover {{ background: #f8f9ff; }}
table.report-table .rank-1 {{ color: #f5222d; font-weight: 700; }}
table.report-table .rank-2 {{ color: #fa8c16; font-weight: 700; }}
table.report-table .rank-3 {{ color: #1890ff; font-weight: 700; }}

/* Hierarchy indentation */
.hierarchy-ul {{ list-style: none; padding-left: 20px; }}
.hierarchy-li {{ padding: 4px 0; position: relative; }}
.hierarchy-li::before {{ content: "│"; position: absolute; left: -16px; color: #ccc; }}
.key-account-node {{ color: #f5222d; font-weight: 600; }}

/* Responsive */
@media (max-width: 768px) {{
    .summary-cards {{ grid-template-columns: repeat(2, 1fr); }}
    .toc-grid {{ grid-template-columns: repeat(2, 1fr); }}
    .chart-box {{ height: 300px; }}
}}
</style>
</head>
<body>
<div class="container">

<!-- Header (M00) -->
<div class="header">
    <h1>{report_data.get("report_title", "群聊数据统计分析报告")}</h1>
    <p>
        <span class="period-badge">{tr.get("label", "")}</span>
        统计时间段: {tr.get("start", "-")} ~ {tr.get("end", "-")}
        | 数据总量: {report_data.get("total_lims_records", 0)} 条 LIMS 记录, {report_data.get("total_groups", 0)} 个群
    </p>
</div>

<!-- TOC (M02) -->
<div class="toc">
    <h3>目录导航</h3>
    <div class="toc-grid">
        <a href="#m03">摘要指标卡</a>
        <a href="#m04">最终售后员分布</a>
        <a href="#m05">群活跃时长分布</a>
        <a href="#m06">产品大类层级</a>
        <a href="#m07">大客户层级</a>
        <a href="#m08">销售区域分布</a>
        <a href="#m09">区域 × 销售员</a>
        <a href="#m10">区域 × 产品</a>
        <a href="#m11">区域 × 售后员</a>
    </div>
</div>

<!-- M03 Summary Cards -->
<div class="module" id="m03">
    <h2>摘要指标卡 <span class="badge">M03</span></h2>
    <div class="summary-cards" id="summary-cards">
        <div class="card"><div class="value">{summary.get("sales_region_count", 0)}</div><div class="label">销售区域数</div></div>
        <div class="card"><div class="value">{summary.get("after_sales_count", 0)}</div><div class="label">售后员数</div></div>
        <div class="card"><div class="value">{summary.get("product_category_count", 0)}</div><div class="label">产品大类数</div></div>
        <div class="card"><div class="value">{summary.get("short_active_group_ratio", 0)}%</div><div class="label">短活跃群占比</div></div>
        <div class="card"><div class="value">{summary.get("key_account_unit_count", 0)}</div><div class="label">大客户单位数</div></div>
        <div class="card"><div class="value">{summary.get("total_groups", 0)}</div><div class="label">总群数</div></div>
    </div>
</div>
"""

    # M04 - finalAfterSaler distribution (bar chart)
    after_sales = report_data.get("after_sales_distribution", [])
    m04_names = [a["name"] for a in after_sales[:20]]
    m04_counts = [a["count"] for a in after_sales[:20]]
    m04_json = _escape_json(m04_names)

    html += f"""
<!-- M04 -->
<div class="module" id="m04">
    <h2>最终售后员分布 <span class="badge">M04</span></h2>
    <div class="chart-box" id="chart-m04"></div>
</div>
"""

    # M05 - Active duration
    m05 = report_data.get("active_duration", [])
    m05_buckets = [d["bucket"] for d in m05]
    m05_counts = [d["count"] for d in m05]
    m05_ratios = [d["ratio"] for d in m05]

    html += f"""
<!-- M05 -->
<div class="module" id="m05">
    <h2>群活跃时长分布 <span class="badge">M05</span></h2>
    <div class="chart-box" id="chart-m05"></div>
    <table class="report-table" style="margin-top:16px">
        <thead><tr><th>分档</th><th>群数</th><th>占比</th></tr></thead>
        <tbody>
"""
    for d in m05:
        html += f"<tr><td>{d['bucket']}</td><td>{d['count']}</td><td>{d['ratio']}%</td></tr>"
    html += """</tbody></table></div>"""

    # M06 - Product hierarchy
    m06 = report_data.get("product_hierarchy", [])

    html += f"""
<!-- M06 -->
<div class="module" id="m06">
    <h2>产品大类层级 <span class="badge">M06</span></h2>
    <div class="chart-box" id="chart-m06"></div>
    <div id="product-hierarchy-view" style="margin-top:16px"></div>
</div>
"""

    # M07 - Key customer hierarchy
    m07 = report_data.get("key_customer_hierarchy", [])

    html += f"""
<!-- M07 -->
<div class="module" id="m07">
    <h2>大客户层级 <span class="badge">M07</span></h2>
"""
    if m07:
        html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px">'
        for ka_node in m07:
            ka = ka_node.get("key_account", "")
            html += f'<div style="background:#fff8e1;border-radius:8px;padding:12px;border:1px solid #ffe082">'
            html += f'<div style="font-weight:700;color:#e65100;margin-bottom:8px">[{ka}]</div>'
            for cust in ka_node.get("customers", []):
                cn = cust.get("customer_name", "")
                fas = ", ".join(cust.get("after_sales", []))
                html += f'<div style="padding:4px 0;font-size:13px">'
                html += f'<span style="color:#333">{cn}</span>'
                html += f' <span style="color:#999">→ 售后: {fas}</span></div>'
            html += "</div>"
        html += "</div>"
    else:
        html += '<p style="color:#999">暂无大客户数据</p>'
    html += "</div>"

    # M08 - Sales region distribution
    m08 = report_data.get("sales_region_distribution", [])
    top5 = report_data.get("top5_coverage", 0)

    html += f"""
<!-- M08 -->
<div class="module" id="m08">
    <h2>销售区域分布 <span class="badge">M08</span> <span class="badge" style="background:#fff3e0;color:#e65100">Top5 覆盖率: {top5}%</span></h2>
    <div class="chart-box" id="chart-m08"></div>
    <table class="report-table" style="margin-top:16px">
        <thead><tr><th>排名</th><th>销售区域</th><th>群聊数</th><th>占比</th></tr></thead>
        <tbody>
"""
    for i, d in enumerate(m08):
        rank_cls = f"rank-{i+1}" if i < 3 else ""
        html += f"<tr><td class='{rank_cls}'>{i+1}</td><td>{d['region']}</td><td>{d['count']}</td><td>{d['ratio']}%</td></tr>"
    html += """</tbody></table></div>"""

    # M09 - Region x SalesPerson
    m09 = report_data.get("region_salesperson", {})

    html += f"""
<!-- M09 -->
<div class="module" id="m09">
    <h2>销售区域 × 销售员 <span class="badge">M09</span></h2>
"""
    if m09:
        html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px">'
        for region, persons in sorted(m09.items()):
            html += f'<div style="background:#f5f7fa;border-radius:8px;padding:12px">'
            html += f'<div style="font-weight:700;margin-bottom:8px;color:#1a73e8">{region}</div>'
            for i, p in enumerate(persons):
                rank_cls = f"rank-{i+1}" if i < 3 else ""
                html += f'<div style="padding:2px 0;font-size:13px"><span class="{rank_cls}">Top{i+1}</span> {p["name"]} <span style="color:#999">({p["count"]})</span></div>'
            html += "</div>"
        html += "</div>"
    else:
        html += '<p style="color:#999">暂无数据</p>'
    html += "</div>"

    # M10 - Region x Product
    m10 = report_data.get("region_product", {})

    html += f"""
<!-- M10 -->
<div class="module" id="m10">
    <h2>销售区域 × 产品大类 <span class="badge">M10</span></h2>
"""
    if m10:
        html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(350px,1fr));gap:16px">'
        for region, products in sorted(m10.items()):
            html += f'<div style="background:#f5f7fa;border-radius:8px;padding:12px">'
            html += f'<div style="font-weight:700;margin-bottom:8px;color:#1a73e8">{region}</div>'
            for name, count in products.items():
                html += f'<div style="padding:2px 0;font-size:13px">{name}: <strong>{count}</strong></div>'
            html += "</div>"
        html += "</div>"
    else:
        html += '<p style="color:#999">暂无数据</p>'
    html += "</div>"

    # M11 - Region x AfterSales
    m11 = report_data.get("region_after_sales", {})

    html += f"""
<!-- M11 -->
<div class="module" id="m11">
    <h2>销售区域 × 最终售后员 <span class="badge">M11</span></h2>
"""
    if m11:
        html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px">'
        for region, persons in sorted(m11.items()):
            html += f'<div style="background:#f5f7fa;border-radius:8px;padding:12px">'
            html += f'<div style="font-weight:700;margin-bottom:8px;color:#1a73e8">{region}</div>'
            for i, p in enumerate(persons):
                rank_cls = f"rank-{i+1}" if i < 3 else ""
                html += f'<div style="padding:2px 0;font-size:13px"><span class="{rank_cls}">Top{i+1}</span> {p["name"]} <span style="color:#999">({p["count"]})</span></div>'
            html += "</div>"
        html += "</div>"
    else:
        html += '<p style="color:#999">暂无数据</p>'
    html += "</div>"

    # ==================== M12-M17: qxChat 附加分析 ====================

    # M12: 群消息量趋势
    qx = report_data.get("qxchat", {})
    m12 = qx.get("m12_message_trend", {})
    m12_trend = m12.get("trend", [])
    m12_total = m12.get("total_messages", 0)
    m12_avg = m12.get("avg_daily", 0)
    
    html += f"""<!-- M12 -->
<div class="module" id="m12">
    <h2>群消息量趋势 <span class="badge">M12</span></h2>
    <p style="color:#666;font-size:13px;margin-bottom:12px">
        消息总量: {m12_total} | 日均: {m12_avg} | 天数: {m12.get("days_covered", 0)}
    </p>
    <div class="chart-box" id="chart-m12"></div>
</div>"""

    # M13: 消息时段分布
    m13 = qx.get("m13_time_distribution", {})
    m13_peak = m13.get("peak_hour_label", "无数据")
    m13_total = m13.get("total_messages", 0)
    m13_hourly = m13.get("hourly_distribution", [])
    m13_heatmap = m13.get("weekday_heatmap", {})
    m13_weekdays = m13.get("weekday_names", [])
    
    html += f"""<!-- M13 -->
<div class="module" id="m13">
    <h2>消息时段分布 <span class="badge">M13</span></h2>
    <p style="color:#666;font-size:13px;margin-bottom:12px">
        峰值时段: {m13_peak} | 消息总量: {m13_total}
    </p>
    <div class="chart-box-half" id="chart-m13-heatmap"></div>
    <div class="chart-box-half" id="chart-m13-hourly" style="margin-top:20px"></div>
</div>"""

    # M14: 情感分析
    m14 = qx.get("m14_sentiment", {})
    m14_total = m14.get("total_analyzed", 0)
    m14_cats = m14.get("categories", [])
    m14_vals = m14.get("values", [])
    
    html += f"""<!-- M14 -->
<div class="module" id="m14">
    <h2>客户消息情感分析 <span class="badge">M14</span></h2>
    <div class="summary-cards" style="grid-template-columns:repeat(auto-fill,minmax(130px,1fr))">
        <div class="card"><div class="value">{m14.get("customer_good", 0)}</div><div class="label">客户好评</div></div>
        <div class="card"><div class="value">{m14.get("customer_bad", 0)}</div><div class="label">客户差评</div></div>
        <div class="card"><div class="value">{m14.get("employee_positive", 0)}</div><div class="label">员工积极</div></div>
        <div class="card"><div class="value">{m14.get("employee_bad", 0)}</div><div class="label">员工恶劣</div></div>
        <div class="card"><div class="value">{m14.get("good_ratio", 0)}%</div><div class="label">好评率</div></div>
    </div>
    <div class="chart-box-half" id="chart-m14"></div>
</div>"""

    # M15: 高频关键词
    m15 = qx.get("m15_highfreq", {})
    m15_top = m15.get("top_words", [])
    m15_total = m15.get("total_unique_words", 0)
    
    html += f"""<!-- M15 -->
<div class="module" id="m15">
    <h2>高频关键词 <span class="badge">M15</span></h2>
    <p style="color:#666;font-size:13px;margin-bottom:12px">
        共发现 {m15_total} 个唯一词汇，高频词 TOP 20 如下
    </p>
    <div class="chart-box" id="chart-m15"></div>
</div>"""

    # M16: 漏回消息
    m16 = qx.get("m16_unanswered", {})
    m16_missed = m16.get("total_missed", 0)
    m16_rate = m16.get("missed_rate", 0)
    m16_rooms = m16.get("total_rooms_with_missed", 0)
    m16_risk = m16.get("risk_levels", {})
    m16_details = m16.get("missed_details", [])
    m16_high = m16_risk.get("high", 0)
    m16_med = m16_risk.get("medium", 0)
    
    html += f"""<!-- M16 -->
<div class="module" id="m16">
    <h2>漏回消息分析 <span class="badge">M16</span></h2>
    <div class="summary-cards" style="grid-template-columns:repeat(auto-fill,minmax(130px,1fr))">
        <div class="card"><div class="value">{m16_missed}</div><div class="label">漏回消息数</div></div>
        <div class="card"><div class="value">{m16_rate}%</div><div class="label">漏回率</div></div>
        <div class="card"><div class="value">{m16_rooms}</div><div class="label">漏回群数</div></div>
        <div class="card"><div class="value">{m16_high}</div><div class="label">高风险群</div></div>
    </div>
    <div class="chart-box-half" id="chart-m16"></div>
</div>"""

    # M17: 响应时长
    m17 = qx.get("m17_response_time", {})
    m17_overall = m17.get("overall", {})
    m17_emp = m17.get("employee_ranking", [])
    
    html += f"""<!-- M17 -->
<div class="module" id="m17">
    <h2>员工响应时效分析 <span class="badge">M17</span></h2>
    <div class="summary-cards" style="grid-template-columns:repeat(auto-fill,minmax(130px,1fr))">
        <div class="card"><div class="value">{m17_overall.get("count", 0)}</div><div class="label">总响应数</div></div>
        <div class="card"><div class="value">{m17_overall.get("avg", 0)}s</div><div class="label">平均响应时长</div></div>
        <div class="card"><div class="value">{m17_overall.get("median", 0)}s</div><div class="label">中位响应时长</div></div>
        <div class="card"><div class="value">{m17_overall.get("max", 0)}s</div><div class="label">最慢响应</div></div>
    </div>
    <div class="chart-box-half" id="chart-m17"></div>
</div>"""

    # M17b: 消息类型分布
    m17b = qx.get("m17b_msg_type_distribution", {})
    
    html += f"""<!-- M17b -->
<div class="module" id="m17b">
    <h2>消息类型分布 <span class="badge">M17b</span></h2>
    <div class="chart-box" id="chart-m17b"></div>
</div>"""


    # JavaScript
    m12_m17_js_charts = generate_qxchat_js(report_data)

    html += f"""<script>
var reportData = {data_json};

function initChart(el, option) {{ var c = echarts.init(document.getElementById(el)); c.setOption(option); }}

// M04 - AfterSales Distribution
initChart('chart-m04', {{
    title: {{ text: '最终售后员群聊数排名', left: 'center', textStyle: {{fontSize:14}} }},
    tooltip: {{ trigger: 'axis' }},
    xAxis: {{ type: 'category', data: {_escape_json(m04_names)}, axisLabel: {{ rotate: 45, fontSize: 11 }} }},
    yAxis: {{ type: 'value', name: '群聊数' }},
    series: [{{ type: 'bar', data: {_escape_json(m04_counts)},
        itemStyle: {{ color: function(p) {{ var c=['#f5222d','#fa8c16','#1890ff']; return c[p.dataIndex]||'#5470c6'; }} }}
    }}]
}});

// M05 - Active Duration
initChart('chart-m05', {{
    title: {{ text: '群活跃时长分布', left: 'center', textStyle: {{fontSize:14}} }},
    tooltip: {{ trigger: 'axis' }},
    xAxis: {{ type: 'category', data: {_escape_json(m05_buckets)} }},
    yAxis: {{ type: 'value', name: '群数' }},
    series: [{{ type: 'bar', data: {_escape_json(m05_counts)}, itemStyle: {{ color: '#5470c6' }} }}]
}});

// M06 - Product Hierarchy
var m06Data = {_escape_json(m06)};
if (m06Data.length > 0) {{
    initChart('chart-m06', {{
        title: {{ text: '产品大类层级', left: 'center', textStyle: {{fontSize:14}} }},
        tooltip: {{ trigger: 'item' }},
        series: [{{
            type: 'sunburst', data: m06Data,
            radius: ['15%', '90%'], label: {{ rotate: 0, fontSize: 11 }},
            emphasis: {{ focus: 'ancestor' }}
        }}]
    }});
}}

// M08 - Sales Region
var m08Names = {_escape_json([d['region'] for d in m08])};
var m08Counts = {_escape_json([d['count'] for d in m08])};
initChart('chart-m08', {{
    title: {{ text: '销售区域群聊数分布', left: 'center', textStyle: {{fontSize:14}} }},
    tooltip: {{ trigger: 'axis' }},
    xAxis: {{ type: 'category', data: m08Names, axisLabel: {{ rotate: 45, fontSize: 11 }} }},
    yAxis: {{ type: 'value', name: '群聊数' }},
    series: [{{ type: 'bar', data: m08Counts }}]
}});


    {m12_m17_js_charts}
window.addEventListener('resize', function() {{
    ['chart-m04','chart-m05','chart-m06','chart-m08','chart-m12','chart-m13-heatmap','chart-m13-hourly','chart-m14','chart-m15','chart-m16','chart-m17','chart-m17b'].forEach(function(id) {{
        var c = echarts.getInstanceByDom(document.getElementById(id));
        if (c) c.resize();
    }});
}});
</script>
"""

    html += """
</div>
</body>
</html>
"""
    return html


def generate_and_save_report(
    report_data: Dict[str, Any],
    output_dir: str = "./reports",
) -> str:
    """生成报告并保存到文件，返回文件路径"""
    html = generate_report_html(report_data)
    os.makedirs(output_dir, exist_ok=True)
    filename = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info(f"报告已保存: {filepath}")
    return filepath


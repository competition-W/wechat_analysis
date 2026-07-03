#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate M12-M17 chart JS code using Python dicts + json.dumps()"""

import json
from typing import Dict, Any


def _qd(data, *keys):
    """Safely access nested dict keys"""
    for k in keys:
        if isinstance(data, dict):
            data = data.get(k, {})
        elif isinstance(data, list):
            return data
        else:
            return {}
    return data if data is not None else {}


def _qd_list(data, *keys):
    """Safely access nested dict keys, returning list"""
    result = _qd(data, *keys)
    if isinstance(result, list):
        return result
    return []


def generate_qxchat_js(qx_data: Dict[str, Any]) -> str:
    """Generate M12-M17 chart init JavaScript using report_data qxchat object references.
    All data is accessed via reportData.qxchat at runtime."""
    
    parts = []
    
    # M12: Message Trend
    parts.append("""
// M12: Message Trend
var m12Data = (reportData.qxchat && reportData.qxchat.m12_message_trend && reportData.qxchat.m12_message_trend.trend) || [];
if (m12Data.length > 0) {
    initChart('chart-m12', {
        title: {text:'按日消息量趋势', left:'center', textStyle:{fontSize:14}},
        tooltip: {trigger:'axis'},
        legend: {data:['总量','客户','员工'], bottom:0},
        xAxis: {type:'category', data: m12Data.map(function(d) { return d.date; }), axisLabel:{rotate:45,fontSize:10}},
        yAxis: {type:'value'},
        series: [
            {name:'总量', type:'line', data:m12Data.map(function(d) { return d.total; }), smooth:true},
            {name:'客户', type:'line', data:m12Data.map(function(d) { return d.customer; }), smooth:true},
            {name:'员工', type:'line', data:m12Data.map(function(d) { return d.employee; }), smooth:true}
        ]
    });
}
""")

    # M13: Time Distribution - Heatmap
    parts.append("""
// M13: Time Distribution
var m13Hours = [];
for (var h = 0; h < 24; h++) { m13Hours.push(h + ':00'); }
var wdNames = ['周一','周二','周三','周四','周五','周六','周日'];
var m13HeatData = [];
for (var w = 0; w < 7; w++) {
    for (var h = 0; h < 24; h++) {
        var val = (reportData.qxchat && reportData.qxchat.m13_time_distribution && 
                   reportData.qxchat.m13_time_distribution.weekday_heatmap) ?
                  (reportData.qxchat.m13_time_distribution.weekday_heatmap[wdNames[w]] || [])[h] || 0 : 0;
        m13HeatData.push([h, w, val]);
    }
}
initChart('chart-m13-heatmap', {
    title: {text:'消息活跃时段热力图', left:'center', textStyle:{fontSize:14}},
    tooltip: {position:'top', formatter: function(p) { 
        return p.data[1] + ' ' + p.data[0] + ':00<br/>消息数: ' + p.data[2]; 
    }},
    xAxis: {type:'category', data:m13Hours, splitArea:{show:true}},
    yAxis: {type:'category', data:['周一','周二','周三','周四','周五','周六','周日'], splitArea:{show:true}},
    visualMap: {min:0, max:10, calculable:true, orient:'horizontal', left:'center', bottom:0},
    series: [{type:'heatmap', data:m13HeatData, label:{show:false}, emphasis:{itemStyle:{shadowBlur:10}}}]
});
// Hourly bar chart
var m13HourlyData = [];
for (var h = 0; h < 24; h++) {
    var cnt = (reportData.qxchat && reportData.qxchat.m13_time_distribution && 
               reportData.qxchat.m13_time_distribution.hourly_distribution) ?
              (reportData.qxchat.m13_time_distribution.hourly_distribution[h] || {}).count || 0 : 0;
    m13HourlyData.push(cnt);
}
initChart('chart-m13-hourly', {
    title: {text:'各时段消息数量', left:'center', textStyle:{fontSize:14}},
    tooltip: {trigger:'axis'},
    xAxis: {type:'category', data:m13Hours, axisLabel:{rotate:45,fontSize:10}},
    yAxis: {type:'value'},
    series: [{type:'bar', data:m13HourlyData, itemStyle:{color:'#5470c6'}}]
});
""")

    # M14: Sentiment
    parts.append("""
// M14: Sentiment
var m14Cats = (reportData.qxchat && reportData.qxchat.m14_sentiment && 
    reportData.qxchat.m14_sentiment.categories) || [];
var m14Vals = (reportData.qxchat && reportData.qxchat.m14_sentiment && 
    reportData.qxchat.m14_sentiment.values) || [];
var m14Data = [];
for (var i = 0; i < m14Cats.length; i++) {
    m14Data.push({name: m14Cats[i], value: m14Vals[i] || 0});
}
initChart('chart-m14', {
    title: {text:'情感分析分布', left:'center', textStyle:{fontSize:14}},
    tooltip: {trigger:'item', formatter:'{b}: {c} ({d}%)'},
    series: [{type:'pie', radius:['30%','60%'], data:m14Data,
        label:{formatter:'{b}: {c}', fontSize:12},
        emphasis:{itemStyle:{shadowBlur:10,shadowOffsetX:0,shadowColor:'rgba(0,0,0,0.5)'}}
    }]
});
""")

    # M15: High Frequency Words
    parts.append("""
// M15: High Frequency Words
var m15Words = (reportData.qxchat && reportData.qxchat.m15_highfreq && 
    reportData.qxchat.m15_highfreq.top_words) || [];
var m15Names = m15Words.map(function(d) { return d.word; });
var m15Counts = m15Words.map(function(d) { return d.count; });
initChart('chart-m15', {
    title: {text:'高频关键词 TOP 20', left:'center', textStyle:{fontSize:14}},
    tooltip: {trigger:'axis'},
    xAxis: {type:'category', data:m15Names, axisLabel:{rotate:45,fontSize:10}},
    yAxis: {type:'value'},
    series: [{type:'bar', data:m15Counts, itemStyle:{color:'#5470c6'}}]
});
""")

    # M16: Unanswered
    parts.append("""
// M16: Unanswered
var m16Risk = (reportData.qxchat && reportData.qxchat.m16_unanswered && 
    reportData.qxchat.m16_unanswered.risk_levels) || {};
initChart('chart-m16', {
    title: {text:'漏回风险等级分布', left:'center', textStyle:{fontSize:14}},
    tooltip: {trigger:'item'},
    series: [{type:'pie', data:[
        {name:'高风险', value:m16Risk.high || 0, itemStyle:{color:'#f5222d'}},
        {name:'中风险', value:m16Risk.medium || 0, itemStyle:{color:'#fa8c16'}},
        {name:'低风险', value:m16Risk.low || 0, itemStyle:{color:'#52c41a'}}
    ]}]
});
""")

    # M17: Response Time
    parts.append("""
// M17: Response Time
var m17Labels = (reportData.qxchat && reportData.qxchat.m17_response_time && 
    reportData.qxchat.m17_response_time.bucket_labels) || [];
var m17Values = (reportData.qxchat && reportData.qxchat.m17_response_time && 
    reportData.qxchat.m17_response_time.bucket_values) || [];
initChart('chart-m17', {
    title: {text:'响应时长分布', left:'center', textStyle:{fontSize:14}},
    tooltip: {trigger:'axis'},
    xAxis: {type:'category', data:m17Labels, axisLabel:{rotate:30,fontSize:10}},
    yAxis: {type:'value'},
    series: [{type:'bar', data:m17Values, itemStyle:{color:'#73c0de'}}]
});
""")

    # M17b: Message Type Distribution
    parts.append("""
// M17b: Message Type
var m17bDist = (reportData.qxchat && reportData.qxchat.m17b_msg_type_distribution && 
    reportData.qxchat.m17b_msg_type_distribution.distribution) || [];
var m17bData = m17bDist.map(function(d) { return {name:d.type, value:d.count}; });
initChart('chart-m17b', {
    title: {text:'消息类型分布', left:'center', textStyle:{fontSize:14}},
    tooltip: {trigger:'item', formatter:'{b}: {c} ({d}%)'},
    series: [{type:'pie', radius:['30%','60%'], data:m17bData,
        label:{formatter:'{b}: {c}', fontSize:12},
        emphasis:{itemStyle:{shadowBlur:10}}
    }]
});
""")

    return '\n'.join(parts)

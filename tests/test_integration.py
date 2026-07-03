import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, ".")

from services.data_collector import LimsRecord
from services.report_aggregator import aggregate_report
from services.report_generator import generate_report_html

# Mock data: simulate 3 groups with LIMS records
mock_groups = []
for i in range(3):
    pc = f"LC-P202300{i+1}"
    mock_groups.append({
        "room_id": f"room_{i}",
        "room_name": f"XX客户-LC-P202300{i+1}-售后",
        "project_code": pc,
        "first_msg_time": "2026-01-01 09:00:00",
        "last_msg_time": f"2026-0{3+(i*2)}-15 18:00:00",
        "message_count": 100 + i * 50,
        "messages": [],
        "lims_records": [
            {"projectCode": pc, "afterSaler": "张三", "finalAfterSaler": "张三",
             "salesPerson": "张三", "customerName": f"客户{i*2+1}",
             "orgName": ["华东", "华南", "华北"][i],
             "productBigSortOne": ["RNA测序", "蛋白组学", "微生物组"][i],
             "productBigSortTwo": ["转录组", "蛋白质谱", "16S"][i],
             "productBigSortThree": ["mRNA", "DIA", "OTU"][i],
             "productName": ["RNA-seq", "Proteomics", "Microbiome"][i],
             "saleName": ["销售A", "销售B", "销售C"][i],
             "keyAccount": ["K001", "", "K002"][i],
             "is_key_account": i != 1,
             "activeDay": 30 + i * 10}
        ],
    })

# Additional LIMS records (no group)
extra_records = [
    LimsRecord(project_code="LC-P2023004", afterSaler="李四", finalAfterSaler="",
               salesPerson="李四", customerName="客户4", orgName="华东",
               productBigSortOne="RNA测序", keyAccount="K003", is_key_account=True),
    LimsRecord(project_code="LC-P2023005", afterSaler="王五", finalAfterSaler="",
               salesPerson="王五", customerName="客户5", orgName="华北",
               productBigSortOne="蛋白组学", keyAccount="", is_key_account=False),
]

report = aggregate_report(mock_groups, extra_records)
print(f'Report aggregate OK')
print(f'  total_groups: {report["total_groups"]}')
print(f'  total_lims_records: {report["total_lims_records"]}')
print(f'  regions: {len(report["sales_region_distribution"])}')
print(f'  after_sales: {len(report["after_sales_distribution"])}')
print(f'  products: {len(report["product_hierarchy"])}')
print(f'  key_customers: {len(report["key_customer_hierarchy"])}')
print()

# Generate HTML
html = generate_report_html(report)
print(f'HTML generated: {len(html)} bytes')
checks = ["摘要指标卡", "chart-m04", "chart-m05", "chart-m06", "chart-m08", "目录导航", "echarts"]
for c in checks:
    print(f'  Has {c}: {html.find(c) >= 0}')

with open("test_report.html", "w", encoding="utf-8") as f:
    f.write(html)
print("Report saved to test_report.html")

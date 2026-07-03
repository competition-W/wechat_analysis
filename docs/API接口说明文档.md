 # 企业微信客户群聊智能分析系统 - API接口说明文档
 
 | 文档名称 | 企业微信客户群聊智能分析系统API接口说明文档 |
 | :--- | :--- |
 | 版本号 | V2.0 |
 | 创建日期 | 2026-04-17 |
 | 更新日期 | 2026-07-03 |
 | 文档状态 | 已更新 |
 | 适用对象 | Java后端开发团队、系统运维人员 |
 
 ---
 
 ## 更新日志
 
 ### V2.0 (2026-07-03)
 
 新增功能：
 - 可视化API：POST /api/v1/visualize/chart（AntV 图表代理）、POST /api/v1/visualize/generate（看板数据）
 - 报告管理API：POST /api/v1/report/generate、GET /api/v1/report/list、GET /api/v1/report/view/{filename}
 - 数据归档API：POST /api/v1/report/archive、GET /api/v1/report/archive/list
 - 前端交互式数据看板（访问根路径 /）
 
 ### V1.1 (2026-04-22)
 
 新增 unanswered 分析类型（漏回检测），新增多个请求字段
 情感分析重构为按角色分类，高频词分析升级为业务需求提取
 
 ---
 
 ## 1. 接口概述
 
 本系统提供企业微信客户群聊的智能分析能力。服务地址：http://host:8000
 所有 API 根路径：/api/v1
 交互式文档：/docs (Swagger) 或 /redoc (ReDoc)
 
 ### 服务架构
 
 客户端/前端看板 -> FastAPI 服务 -> LLM 引擎 (OpenAI API)
                                    -> 数据采集器 (LIMS/企微API)
                                    -> 数据归档模块
                                    -> AntV 图表代理
 
 ---
 
 ## 2. 通用说明
 
 ### 2.1 请求格式
 - Content-Type: application/json
 - 字符编码: UTF-8
 
 ### 2.2 响应格式
 ```json
 {"code": 0, "message": "success", "data": {...}}
 ```
 code=0 表示成功，非零见错误码说明。
 
 ### 2.3 分析类型
 
 | 类型 | 说明 | LLM模型 |
 | sentiment | 情感分析 | gpt-4o-mini |
 | sensitive | 敏感词检测 | gpt-4o-mini |
 | summary | 摘要生成 | gpt-4o |
 | highfreq | 高频词提取 | gpt-4o-mini |
 | unanswered | 漏回检测 | gpt-4o-mini |
 
 ---
 
 ## 3. 聊天分析 API
 
 ### 3.1 健康检查
 GET /api/v1/health
 
 返回：{"status": "healthy", "timestamp": "..."}
 
 ### 3.2 单群聊分析
 POST /api/v1/chat/analyze
 
 分析单个群聊。请求体字段：
 - room_id (string, 必填): 房间ID
 - room_name (string): 群名称
 - analysis_types (string[]): 分析类型列表，默认全部
 - messages (object[], 必填): 消息列表
   - msgid, from, content, send_time, sender_role
 - members (object[]): 成员列表
   - userid, name, type, job, position
 
 响应包含：sentiment / sensitive_words / summary / high_freq_words / unanswered_status
 
 ### 3.3 批量分析
 POST /api/v1/chat/batch-analyze
 
 批量分析多个群聊。请求体额外字段：
 - rooms (object[]): 群聊列表
 - max_concurrent (int): 最大并发数，默认5
 
 响应包含每个群的分析结果和总体统计。
 
 ---
 
 ## 4. 报告管理 API
 
 ### 4.1 生成报告
 POST /api/v1/report/generate?type=weekly&date=YYYY-MM-DD&fresh=false
 
 参数：type=daily/weekly/monthly/quarterly/yearly
 返回：报告文件路径、摘要统计数据
 
 ### 4.2 查看报告
 GET /api/v1/report/view/{filename}
 返回 HTML 格式报告
 
 ### 4.3 报告列表
 GET /api/v1/report/list
 返回已生成的报告文件列表
 
 ### 4.4 数据归档
 POST /api/v1/report/archive
 从 LIMS/企微API 拉取最新数据并保存快照
 
 ### 4.5 归档列表
 GET /api/v1/report/archive/list
 返回所有归档快照日期
 
 ---
 
 ## 5. 可视化 API
 
 ### 5.1 生成图表
 POST /api/v1/visualize/chart
 
 通过 AntV API 生成图表图片。
 请求体字段：type(bar/column/pie/line/radar), data(array), title, theme, width, height
 返回：image_url 可直接用于 img 标签
 
 ### 5.2 看板数据
 POST /api/v1/visualize/generate?type=weekly&date=...
 
 同 4.1 生成报告，但返回完整 report_data JSON 供前端渲染。
 数据维度（8个）：售后员分布、销售区域分布、群活跃时长、情感分析、消息量趋势、高频关键词、漏回分析、响应时长
 
 ---
 
 ## 6. 错误码说明
 
 | 错误码 | 说明 |
 | 0 | 成功 |
 | 404 | 资源不存在 |
 | 500 | 服务内部错误（LLM异常等） |
 | 502 | 上游接口异常（LIMS/企微API不可用） |
 | 504 | 代理超时（AntV API超时） |
 
 HTTP状态码：200=成功, 404=不存在, 422=参数错误, 500=服务端错误
 
 ---
 
 ## 7. 附录
 
 前端看板图表数据维度说明：
 
 | 维度 | 数据来源 |
 | 售后员分布 | after_sales_distribution |
 | 销售区域分布 | sales_region_distribution |
 | 群活跃时长 | active_duration |
 | 情感分析 | qxchat.m14_sentiment |
 | 消息量趋势 | qxchat.m12_message_trend |
 | 高频关键词 | qxchat.m15_highfreq |
 | 漏回分析 | qxchat.m16_unanswered |
 | 响应时长 | qxchat.m17_response_time |
 
 ---
 文档版本：V2.0 | 更新日期：2026-07-03

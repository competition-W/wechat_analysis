# Enterprise WeChat Group Chat Intelligent Analysis System
# 企业微信群聊智能分析系统

基于 LLM 的企业微信群聊数据分析与可视化平台，提供群聊内容分析、情感分析、敏感词检测、高频词提取、漏回检测、LIMS 数据统计等功能，并支持交互式 Web 数据看板。

---

## 目录

1. [项目概述](#项目概述)
2. [技术栈](#技术栈)
3. [快速开始](#快速开始)
4. [API 文档](#api-文档)
5. [前端看板](#前端看板)
6. [Docker 部署](#docker-部署)
7. [配置文件](#配置文件)
8. [项目结构](#项目结构)

---

## 项目概述

本系统通过对企业微信客户群的聊天记录进行分析，结合 LLM 实现以下核心功能：

- **情感分析**：对客户和员工消息进行情感分类（好评/差评/积极/恶劣）
- **敏感词检测**：识别聊天内容中的敏感词汇并统计命中次数
- **报告摘要生成**：自动生成群聊内容的摘要总结
- **高频词提取**：提取聊天中的高频业务词汇
- **漏回检测**：检测客户消息是否有员工跟进回复
- **LIMS 数据关联**：集成 LIMS 系统数据，形成完整的销售/售后分析
- **可视化看板**：基于 AntV 的交互式数据图表展示

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 框架 | Python FastAPI |
| LLM | OpenAI API / 兼容接口 (GPT-4o, GPT-4o-mini) |
| 数据可视化 | AntV (antv-studio.alipay.com) |
| 部署 | Docker + Docker Compose |
| 运行环境 | Python 3.11+, Uvicorn |
| 包管理 | pip + requirements.txt |

---

## 快速开始

### 方式一：Docker 部署（推荐）

```bash
# 1. 克隆仓库
git clone https://github.com/competition-W/wechat_analysis.git
cd wechat_analysis

# 2. 配置环境变量
cp .env.example .env.production
# 编辑 .env.production，填入 API 密钥等配置

# 3. 启动
docker-compose up -d

# 4. 访问
# http://localhost:8000/    -- 数据看板
# http://localhost:8000/docs -- API 文档
```

### 方式二：本地开发

```bash
# 1. 创建虚拟环境
python -m venv .venv
source .venv/bin/activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置环境变量
cp .env.example .env.production

# 4. 启动服务
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# 5. 访问
# http://localhost:8000/    -- 数据看板
# http://localhost:8000/docs -- API 文档
```

---

## API 文档

所有 API 前缀为 `/api/v1`，使用 JSON 格式交互。完整的交互式 API 文档可访问 `/docs`（Swagger UI）或 `/redoc`（ReDoc）。

### 1. 健康检查

`GET /api/v1/health`

返回服务器运行状态。响应示例：
```json
{"status": "healthy", "timestamp": "2026-07-03T12:00:00"}
```

### 2. 单群聊分析

`POST /api/v1/chat/analyze`

分析单个群聊的聊天记录。支持分析类型：sentiment（情感）、sensitive（敏感词）、summary（摘要）、highfreq（高频词）、unanswered（漏回）。

### 3. 批量群聊分析

`POST /api/v1/chat/batch-analyze`

支持并发批量分析多个群聊数据。请求体包含 rooms 数组和 max_concurrent 参数。

### 4. 报告管理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v1/report/generate | 生成按时间维度的统计分析报告 |
| GET | /api/v1/report/view/{filename} | 查看已生成的 HTML 报告文件 |
| GET | /api/v1/report/list | 列出所有已生成的报告 |
| POST | /api/v1/report/archive | 手动触发数据归档 |
| GET | /api/v1/report/archive/list | 列出所有归档快照 |

### 5. 图表与可视化

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v1/visualize/chart | 通过 AntV API 生成图表图片 |
| POST | /api/v1/visualize/generate | 生成报告完整 JSON 数据供前端渲染 |

图表类型支持：bar（柱状图）、column（条形图）、pie（饼图）、line（折线图）、radar（雷达图）

数据维度（8 个）：售后员分布、销售区域分布、群活跃时长、情感分析、消息量趋势、高频关键词、漏回分析、响应时长

---

## 前端看板

服务启动后访问根路径 `/` 即可打开交互式数据看板。

### 功能

- **报告生成**：选择报告类型（日报/周报/月报/季报/年报）和日期，生成汇总报告
- **摘要卡片**：生成完成后显示群汇总统计（销售区域、售后员、总群数等）
- **图表可视化**：8 个数据维度 x 5 种图表类型，自由组合切换
- **报告管理**：列出所有生成的 HTML 报告，支持在线查看和复制链接

### 架构

前端为纯静态 SPA，嵌入在 api/static/index.html 中，由 FastAPI 直接服务。图表通过后端代理调用 AntV 渲染 API 生成图片。

---

## Docker 部署

```bash
# 构建镜像
docker-compose build

# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

生产环境建议在 Nginx 后反向代理：
```nginx
server {
    listen 80;
    server_name your-domain.com;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## 配置文件

主要配置通过 .env.production 环境变量注入（详情见 config/settings.py）：

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| LLM_API_KEY | LLM API 密钥 | - |
| LLM_BASE_URL | LLM API 基础地址 | https://api.openai.com/v1 |
| LLM_MODEL_SENTIMENT | 情感分析模型 | gpt-4o-mini |
| LLM_MODEL_SUMMARY | 摘要生成模型 | gpt-4o |
| LIMS_API_BASE | LIMS API 地址 | - |
| QXCHAT_API_BASE | 企微群聊 API 地址 | - |
| SERVICE_HOST | 服务监听地址 | 0.0.0.0 |
| SERVICE_PORT | 服务监听端口 | 8000 |

---

## 项目结构

```
wechat_analysis/
+-- api/
|   +-- main.py              # FastAPI 入口
|   +-- routes/
|   |   +-- __init__.py      # 路由注册
|   |   +-- analyze.py       # 聊天分析 API
|   |   +-- report.py        # 报告管理 API
|   |   +-- visualize.py     # 可视化 API
|   +-- static/
|       +-- index.html       # 前端看板 SPA
+-- config/settings.py       # 配置类
+-- models/                  # 请求/响应模型
+-- services/                # 业务服务层
+-- tests/                   # 测试用例
+-- docs/                    # 文档
+-- docker-compose.yml
+-- Dockerfile
+-- requirements.txt
```

---

## 详细文档

项目根目录下包含全套项目文档（中文）。

---

*图表由 AntV (antv-studio.alipay.com) 提供渲染*

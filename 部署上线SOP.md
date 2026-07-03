# 企业微信客户群聊智能分析服务 - 生产部署 SOP

## 1. 环境准备

### 1.1 服务器要求

| 项目 | 最低配置 | 推荐配置 |
|------|---------|---------|
| CPU | 2 核 | 4 核以上 |
| 内存 | 2 GB | 4 GB 以上 |
| 磁盘 | 20 GB | 50 GB 以上 |
| 系统 | CentOS 7 / Ubuntu 20.04 / Debian 11 |  |
| Docker | 20.10+ | 20.10+ |
| Docker Compose | v2.0+ | v2.0+ |

### 1.2 安装 Docker 和 Docker Compose

```bash
# CentOS / RHEL
sudo yum install -y docker-ce docker-ce-cli containerd.io
sudo systemctl enable --now docker

# Ubuntu / Debian
sudo apt update && sudo apt install -y docker.io docker-compose
sudo systemctl enable --now docker

# 验证版本
docker --version        # >= 20.10
docker compose version   # >= 2.0
```

### 1.3 配置 Docker 镜像加速（可选）

若服务器访问 Docker Hub 较慢，可配置国内镜像加速器：

```bash
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://docker.mirrors.ustc.edu.cn",
    "https://hub-mirror.c.163.com"
  ]
}
EOF
sudo systemctl restart docker
```

---

## 2. 镜像获取

### 方式一：从私有仓库拉取（推荐）

```bash
docker pull 110.1.1.43:5000/wechat-analysis:latest
```

### 方式二：现场构建

```bash
git clone <repository_url> /opt/wechat-analysis
cd /opt/wechat-analysis
docker build -t 110.1.1.43:5000/wechat-analysis:latest .
docker push 110.1.1.43:5000/wechat-analysis:latest
```

---

## 3. 配置部署

### 3.1 创建部署目录

```bash
sudo mkdir -p /opt/wechat-analysis
sudo chown $(whoami):$(whoami) /opt/wechat-analysis
cd /opt/wechat-analysis
```

### 3.2 创建生产环境配置文件

从项目中的 `.env.production` 模板复制并填写实际值：

```bash
cp /path/to/wechat-analysis/.env.production /opt/wechat-analysis/.env
vi /opt/wechat-analysis/.env
```

必须修改的配置项：

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `LLM_API_KEY` | LLM API 密钥（必填） | `sk-xxxxxxxxxxxxxxxx` |
| `LLM_BASE_URL` | LLM API 地址 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `LLM_MODEL_SUMMARY` | 摘要模型 | `qwen-long` |
| `LLM_MODEL_SENTIMENT` | 情感分析模型 | `qwen-plus` |
| `SENSITIVE_WORDS` | 敏感词列表（逗号分隔） | `返点,私下,回扣,投诉` |
| `JAVA_DATA_SOURCE_URL` | Java 数据源接口地址 | `http://192.168.0.129:8081/qxChat/` |
| `LOG_LEVEL` | 日志级别 | `INFO` / `WARNING` / `ERROR` |

### 3.3 部署服务

```bash
# 启动服务（后台运行）
docker compose up -d --build

# 查看服务状态
docker compose ps

# 查看实时日志
docker compose logs -f

# 查看服务健康状态
curl http://localhost:8000/api/v1/health
```

---

## 4. 验证部署

### 4.1 健康检查

```bash
curl http://localhost:8000/api/v1/health
```

正常响应：
```json
{"status":"healthy","timestamp":"2026-04-30T12:00:00.000000"}
```

### 4.2 API 接口测试

访问 API 文档页面：`http://<服务器IP>:8000/docs`

推荐测试接口：

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/v1/health` | GET | 健康检查 |
| `/api/v1/chat/analyze` | POST | 单群聊分析 |
| `/api/v1/chat/batch-analyze` | POST | 批量群聊分析 |

单群聊分析测试示例：

```bash
curl -X POST http://localhost:8000/api/v1/chat/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "room_id": "test_room_001",
    "room_name": "测试群",
    "messages": [
      {
        "msgid": "msg001",
        "seq": 1,
        "roomid": "test_room_001",
        "from": "user001",
        "truename": "张三",
        "job": "销售",
        "position": "销售经理",
        "content": "{\"content\":\"项目什么时候能交付？\"}",
        "msgtime": "2026-04-30 10:00:00",
        "msgtype": "text"
      },
      {
        "msgid": "msg002",
        "seq": 2,
        "roomid": "test_room_001",
        "from": "user002",
        "truename": "李四",
        "job": "售后",
        "position": "售后工程师",
        "content": "{\"content\":\"预计下周完成，请放心。\"}",
        "msgtime": "2026-04-30 10:05:00",
        "msgtype": "text"
      }
    ],
    "analysis_types": ["sentiment", "sensitive", "summary", "highfreq", "unanswered"]
  }'
```

---

## 5. 运维管理

### 5.1 日志管理

日志文件位于容器内 `/app/logs/` 目录，通过 Docker Volume 持久化到宿主机。

```bash
# 查看应用日志
docker compose logs -f wechat-analysis

# 宿主机日志位置
ls -la /opt/wechat-analysis/app-logs/

# 日志默认保留 7 天，由 loguru 自动轮转
```

### 5.2 日志输出配置

| 配置 | 值 | 说明 |
|------|----|------|
| 控制台输出 | 开启 | stdout，颜色日志 |
| 文件输出 | 开启 | `logs/app_YYYY-MM-DD.log`，每天轮转 |
| 保留策略 | 7 天 | 自动删除旧日志 |
| 单文件大小 | 无限制 | 按天轮转 |

### 5.3 服务重启

```bash
# 正常重启（不删除容器）
docker compose restart

# 重新创建并启动
docker compose up -d --force-recreate

# 完全重建（包括重新拉取镜像）
docker compose down && docker compose up -d --build
```

### 5.4 服务更新

```bash
# 1. 拉取新镜像
docker pull 110.1.1.43:5000/wechat-analysis:latest

# 2. 重启服务（自动使用新镜像）
docker compose up -d

# 3. 验证
curl http://localhost:8000/api/v1/health
docker compose logs --tail=20
```

### 5.5 资源限制说明

当前 docker-compose.yml 配置的资源限制：

```yaml
deploy:
  resources:
    limits:
      memory: 2G      # 内存上限
      cpus: "2.0"     # CPU 上限
    reservations:
      memory: 512M    # 内存预留
      cpus: "0.5"     # CPU 预留
```

如需调整，修改 `docker-compose.yml` 后执行 `docker compose up -d` 生效。

### 5.6 性能监控

```bash
# 查看容器资源使用
docker stats

# 查看容器进程
docker top wechat-analysis

# 查看容器详细信息
docker inspect wechat-analysis
```

---

## 6. 安全配置

### 6.1 防火墙配置

```bash
# 开放 8000 端口（仅允许内网访问）
sudo firewall-cmd --permanent --add-port=8000/tcp
sudo firewall-cmd --reload

# 或使用 iptables
sudo iptables -A INPUT -p tcp --dport 8000 -s 10.0.0.0/8 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 8000 -j DROP
```

### 6.2 API 密钥安全

- 生产环境的 `.env` 文件**不要**提交到代码仓库
- 定期轮换 `LLM_API_KEY`
- API Key 泄露后立即更换并重启服务

### 6.3 非 root 运行

容器以 `appuser` 用户运行，非 root 权限，确保运行时安全。

---

## 7. 常见问题排查

### 7.1 服务启动失败

```bash
# 查看详细错误日志
docker compose logs -f wechat-analysis

# 常见原因：
# 1. .env 文件缺少必填配置 → 检查 LLM_API_KEY 是否已填写
# 2. 端口被占用 → 修改 docker-compose.yml 中的端口映射
# 3. 镜像拉取失败 → 检查网络和仓库地址
```

### 7.2 健康检查失败

```bash
# 手动测试
curl -v http://localhost:8000/api/v1/health

# 查看 uvicorn 进程
docker exec wechat-analysis ps aux | grep uvicorn

# 查看应用启动日志
docker logs wechat-analysis 2>&1 | grep -E "(ERROR|startup|started)"
```

### 7.3 LLM 调用超时

```bash
# 检查超时配置
grep "LLM_TIMEOUT" /opt/wechat-analysis/.env

# 建议值：网络稳定时 60-120s，网络较慢时 180-300s
```

### 7.4 内存占用过高

```bash
# 查看内存使用
docker stats --no-stream

# 降低 worker 数量：修改 Dockerfile CMD 中的 --workers 参数
# 或降低 docker-compose.yml 中的内存限制
```

### 7.5 磁盘空间不足

```bash
# 清理 Docker 未使用的资源
docker system prune -a --volumes

# 清理旧日志（如果日志持久化到宿主机）
find /opt/wechat-analysis/app-logs/ -name "*.log" -mtime +7 -delete
```

---

## 8. 服务接口说明

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/v1/chat/analyze` | POST | 单群聊智能分析 |
| `/api/v1/chat/batch-analyze` | POST | 批量群聊分析 |
| `/api/v1/health` | GET | 服务健康检查 |
| `/docs` | GET | Swagger API 文档 |
| `/redoc` | GET | ReDoc API 文档 |

详细接口说明请参考项目中的 `接口交接文档.md`。

---

## 9. 快速命令汇总

```bash
# 启动服务
docker compose up -d

# 停止服务
docker compose down

# 查看状态
docker compose ps

# 查看日志
docker compose logs -f

# 健康检查
curl http://localhost:8000/api/v1/health

# 重启服务
docker compose restart

# 更新服务
docker pull 110.1.1.43:5000/wechat-analysis:latest && docker compose up -d

# 完全重建
docker compose down && docker compose up -d --build
```

## 10. 报告生成服务扩展（2026-07-03 新增）

### 10.1 新增功能概览

在原有分析服务基础上，新增群聊+LIMS 数据统计分析与可视化报告系统。

#### 新增文件

| 文件 | 说明 |
|------|------|
| services/data_collector.py | 数据采集：qxChat + LIMS API 调用，派生字段计算 |
| services/report_aggregator.py | M00-M11 统计聚合 |
| services/qxchat_analyzer.py | M12-M17 qxChat 数据分析 |
| services/report_generator.py | HTML 报告生成（ECharts） |
| services/_report_sections.py | M12-M17 JS 图表代码 |
| api/routes/report.py | 报告 API 路由 |

#### 新增配置项 (.env)

LIMS_API_URL=http://110.1.1.96:8080/unionLims/
LIMS_BASE_DATA_PATH=/base_data/
LIMS_API_TIMEOUT=30
PROJECT_CODE_PATTERN=LC-P\d+
REPORT_OUTPUT_DIR=./reports
REPORT_TITLE=群聊数据统计分析报告

### 10.2 新增 API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| /api/v1/report/generate | POST | 触发完整报告生成 |
| /api/v1/report/view/{filename} | GET | 查看已生成的报告 HTML |
| /api/v1/report/list | GET | 列出所有已生成的报告文件 |

### 10.3 数据依赖验证

报告服务依赖两个内网 API，部署前须确认连通性：

curl http://192.168.0.129:8081/qxChat/

curl -X POST http://110.1.1.96:8080/unionLims/base_data/ -H "Content-Type: application/json" -d '[{"projectCode": "测试"}]'

### 10.4 生成报告

cd /opt/wechat-analysis && docker compose up -d

curl -X POST http://localhost:8000/api/v1/report/generate

curl http://localhost:8000/api/v1/report/list

### 10.5 故障排查

| 问题 | 排查步骤 |
|------|----------|
| 报告生成为空 | 检查 qxChat 和 LIMS API 是否可达，项目号是否匹配 |
| finalAfterSaler 均为空 | 检查 LIMS 中 afterSaler 与 members 精确匹配 |
| 图表不显示 | 确认服务器可访问 cdn.jsdelivr.net，或改为内网镜像 |
| LIMS API 响应慢 | 调整 LIMS_API_TIMEOUT（默认 30 秒） |

# Kimi Work 使用指南：让电脑自己跑项目

> 目标：你关电脑、出门、睡觉——项目该跑的继续跑，该测的自动测，有异常自动提醒你。

---

## 一、架构总览

```
┌─────────────────────────────────────────┐
│            Windows 11                   │
│  ┌─────────────────────────────────┐    │
│  │  WSL (Ubuntu-24.04)             │    │
│  │  ┌─────────────────────────┐    │    │
│  │  │ OpenClaw Gateway        │    │    │
│  │  │  └─→ Kimi Claw (我)     │    │    │
│  │  └─────────────────────────┘    │    │
│  │  ┌─────────────────────────┐    │    │
│  │  │ Shop_Agent Server       │    │    │
│  │  │  └─→ FastAPI + RAG      │    │    │
│  │  └─────────────────────────┘    │    │
│  │  ┌─────────────────────────┐    │    │
│  │  │ 定时任务 (cron/systemd) │    │    │
│  │  │  └─→ 自动测试/报告      │    │    │
│  │  └─────────────────────────┘    │    │
│  └─────────────────────────────────┘    │
│  ┌─────────────────────────────────┐    │
│  │ Windows 任务计划程序            │    │
│  │  └─→ 开机自动启动 WSL + 服务   │    │
│  └─────────────────────────────────┘    │
└─────────────────────────────────────────┘
```

---

## 二、WSL 开机自动启动（Windows 层）

### 2.1 配置 WSL 自动启动

**方式 A：Windows 任务计划程序（推荐，稳定）**

1. `Win + R` → 输入 `taskschd.msc` 回车
2. 右侧「创建基本任务」
3. 名称：`AutoStart WSL ShopAgent`
4. 触发器：`当特定用户登录时`（或`计算机启动时`）
5. 操作：`启动程序`
6. 程序：`C:\Windows\System32\wsl.exe`
7. 参数：`--distribution Ubuntu-24.04 --user root --exec /usr/local/bin/shop-agent-startup.sh`

**方式 B：快捷方式放启动文件夹（简单但不稳）**

创建 `wsl-shopagent.bat`：
```batch
@echo off
wsl --distribution Ubuntu-24.04 --user root --exec /usr/local/bin/shop-agent-startup.sh
```

`Win + R` → `shell:startup` → 把 bat 文件丢进去。

---

## 三、WSL 内部服务自启脚本

创建 `/usr/local/bin/shop-agent-startup.sh`：

```bash
#!/bin/bash
# shop-agent-startup.sh — WSL 启动时自动运行的入口脚本

LOGFILE="/var/log/shop-agent-startup.log"
exec > >(tee -a "$LOGFILE") 2>&1

echo "===== $(date) 启动开始 ====="

# 1. 启动 OpenClaw Gateway（如果配置了 systemd）
if systemctl is-enabled openclaw-gateway.service &>/dev/null; then
    echo "[1/4] 启动 OpenClaw Gateway..."
    systemctl start openclaw-gateway.service
    sleep 2
    if systemctl is-active openclaw-gateway.service &>/dev/null; then
        echo "    ✓ Gateway 已启动 (PID: $(pgrep -f 'openclaw-gateway'))"
    else
        echo "    ✗ Gateway 启动失败，查看日志: journalctl -u openclaw-gateway"
    fi
else
    echo "[1/4] OpenClaw Gateway 未配置 systemd，跳过"
fi

# 2. 启动 Redis（如果用 docker-compose）
if command -v docker &>/dev/null && docker ps &>/dev/null; then
    echo "[2/4] 检查 Redis..."
    if ! docker ps | grep -q redis; then
        cd /root/.openclaw/workspace/Shop_Agent/Shop_Agent/rag-shopping-agent-main || true
        docker-compose up -d redis 2>/dev/null || echo "    ⚠ docker-compose 未找到或 Redis 已运行"
    else
        echo "    ✓ Redis 已在运行"
    fi
else
    echo "[2/4] Docker 未就绪，跳过 Redis 启动"
fi

# 3. 启动 Shop_Agent 服务（后台模式）
PROJECT_DIR="/root/.openclaw/workspace/Shop_Agent/Shop_Agent/rag-shopping-agent-main/server"
if [ -f "$PROJECT_DIR/main.py" ]; then
    echo "[3/4] 启动 Shop_Agent Server..."
    cd "$PROJECT_DIR"
    # 用 nohup 后台启动，避免终端关闭后进程被杀
    nohup python -m uvicorn main:app --host 0.0.0.0 --port 8000 > /var/log/shop-agent-server.log 2>&1 &
    sleep 3
    if curl -sf http://localhost:8000/health &>/dev/null; then
        echo "    ✓ Shop_Agent 已启动 (http://localhost:8000)"
    else
        echo "    ⚠ 健康检查未通过，查看日志: tail -f /var/log/shop-agent-server.log"
    fi
else
    echo "[3/4] Shop_Agent 项目目录不存在，跳过"
fi

# 4. 显示状态摘要
echo "[4/4] 状态摘要:"
echo "    OpenClaw Gateway: $(systemctl is-active openclaw-gateway.service 2>/dev/null || echo 'unknown')"
echo "    Shop_Agent: $(curl -sf -o /dev/null -w '%{http_code}' http://localhost:8000/health 2>/dev/null || echo 'down')"
echo "===== $(date) 启动结束 ====="
```

赋权：
```bash
sudo chmod +x /usr/local/bin/shop-agent-startup.sh
```

---

## 四、定时自动任务（cron）

### 4.1 编辑 crontab

```bash
sudo crontab -e
```

### 4.2 添加定时任务

```cron
# 每分钟检查 Shop_Agent 是否存活，死了自动拉起
*/1 * * * * /usr/local/bin/shop-agent-healthcheck.sh

# 每天凌晨 3 点自动运行检索参数评估
0 3 * * * cd /root/.openclaw/workspace/Shop_Agent/Shop_Agent/rag-shopping-agent-main/server && python -m scripts.eval_retrieval_params >> /var/log/shop-agent-eval.log 2>&1

# 每周一凌晨 4 点跑端到端测试
0 4 * * 1 cd /root/.openclaw/workspace/Shop_Agent/Shop_Agent/rag-shopping-agent-main/server && python -m scripts.run_eval >> /var/log/shop-agent-e2e.log 2>&1

# 每天凌晨 2 点清理旧日志（保留 7 天）
0 2 * * * find /var/log/shop-agent*.log -mtime +7 -delete
```

### 4.3 健康检查脚本

创建 `/usr/local/bin/shop-agent-healthcheck.sh`：

```bash
#!/bin/bash
# 检查 Shop_Agent 是否存活，死了自动重启

HEALTH=$(curl -sf -o /dev/null -w '%{http_code}' http://localhost:8000/health 2>/dev/null || echo "000")

if [ "$HEALTH" != "200" ]; then
    echo "$(date) Shop_Agent 异常 (HTTP $HEALTH)，尝试重启..." >> /var/log/shop-agent-healthcheck.log
    
    # 杀掉旧进程
    pkill -f "uvicorn main:app" || true
    sleep 2
    
    # 重新启动
    cd /root/.openclaw/workspace/Shop_Agent/Shop_Agent/rag-shopping-agent-main/server
    nohup python -m uvicorn main:app --host 0.0.0.0 --port 8000 > /var/log/shop-agent-server.log 2>&1 &
    
    sleep 5
    NEW_HEALTH=$(curl -sf -o /dev/null -w '%{http_code}' http://localhost:8000/health 2>/dev/null || echo "000")
    echo "$(date) 重启后状态: HTTP $NEW_HEALTH" >> /var/log/shop-agent-healthcheck.log
fi
```

赋权：
```bash
sudo chmod +x /usr/local/bin/shop-agent-healthcheck.sh
```

---

## 五、日常使用命令速查

```bash
# 查看所有服务状态
systemctl status openclaw-gateway.service
curl http://localhost:8000/health
docker ps | grep redis

# 查看日志（实时）
tail -f /var/log/shop-agent-server.log
tail -f /var/log/shop-agent-startup.log
tail -f /var/log/shop-agent-healthcheck.log

# 手动重启 Shop_Agent
pkill -f "uvicorn main:app"
cd /root/.openclaw/workspace/Shop_Agent/Shop_Agent/rag-shopping-agent-main/server
nohup python -m uvicorn main:app --host 0.0.0.0 --port 8000 > /var/log/shop-agent-server.log 2>&1 &

# 手动跑评估
python -m scripts.eval_retrieval_params
python -m scripts.run_eval

# 压测（需要 locust）
locust -f scripts/locustfile.py --host=http://localhost:8000
```

---

## 六、异常处理

| 现象 | 排查 | 解决 |
|---|---|---|
| WSL 没自动启动 | 检查 Windows 任务计划程序历史记录 | 手动运行 `wsl` 测试 |
| Shop_Agent 启动失败 | `tail /var/log/shop-agent-server.log` | 检查依赖安装、端口占用 |
| Redis 连不上 | `docker ps` 看容器状态 | `docker-compose up -d redis` |
| 内存占用过高 | `free -h` / `docker stats` | 重启服务或加内存 |
| 定时任务没执行 | `sudo grep CRON /var/log/syslog` | 检查 crontab 语法、脚本权限 |

---

## 七、一键设置脚本（复制粘贴执行）

```bash
#!/bin/bash
# 保存为 setup-autorun.sh，在 WSL 中执行

set -e

echo "=== 设置 Shop_Agent 自动运行 ==="

# 创建日志目录
sudo mkdir -p /var/log

# 创建启动脚本
sudo tee /usr/local/bin/shop-agent-startup.sh > /dev/null <<'EOF'
#!/bin/bash
LOGFILE="/var/log/shop-agent-startup.log"
exec > >(tee -a "$LOGFILE") 2>&1
echo "===== $(date) 启动开始 ====="

if systemctl is-enabled openclaw-gateway.service &>/dev/null; then
    systemctl start openclaw-gateway.service
    echo "Gateway: $(systemctl is-active openclaw-gateway.service)"
fi

PROJECT_DIR="/root/.openclaw/workspace/Shop_Agent/Shop_Agent/rag-shopping-agent-main/server"
if [ -f "$PROJECT_DIR/main.py" ]; then
    cd "$PROJECT_DIR"
    pkill -f "uvicorn main:app" || true
    sleep 1
    nohup python -m uvicorn main:app --host 0.0.0.0 --port 8000 > /var/log/shop-agent-server.log 2>&1 &
    sleep 3
    echo "Shop_Agent: $(curl -sf -o /dev/null -w '%{http_code}' http://localhost:8000/health)"
fi

echo "===== $(date) 启动结束 ====="
EOF
sudo chmod +x /usr/local/bin/shop-agent-startup.sh

# 创建健康检查脚本
sudo tee /usr/local/bin/shop-agent-healthcheck.sh > /dev/null <<'EOF'
#!/bin/bash
HEALTH=$(curl -sf -o /dev/null -w '%{http_code}' http://localhost:8000/health 2>/dev/null || echo "000")
if [ "$HEALTH" != "200" ]; then
    echo "$(date) 异常，重启中..." >> /var/log/shop-agent-healthcheck.log
    pkill -f "uvicorn main:app" || true
    sleep 2
    cd /root/.openclaw/workspace/Shop_Agent/Shop_Agent/rag-shopping-agent-main/server
    nohup python -m uvicorn main:app --host 0.0.0.0 --port 8000 > /var/log/shop-agent-server.log 2>&1 &
fi
EOF
sudo chmod +x /usr/local/bin/shop-agent-healthcheck.sh

# 添加 cron 任务
(crontab -l 2>/dev/null; echo "*/1 * * * * /usr/local/bin/shop-agent-healthcheck.sh") | sudo crontab -

echo "✓ 设置完成。请手动配置 Windows 任务计划程序指向 /usr/local/bin/shop-agent-startup.sh"
```

---

## 八、Windows 任务计划程序配置截图指引

1. **创建任务**
   - 名称：`AutoStart-WSL-ShopAgent`
   - 勾选「使用最高权限运行」
   - 配置：`Windows 10`

2. **触发器**
   - 新建 → 「发生事件时」
   - 日志：`System`
   - 来源：`User32`
   - 事件 ID：`1`（Windows 登录）
   - 或直接用「启动时」

3. **操作**
   - 程序：`C:\Windows\System32\wsl.exe`
   - 参数：`--distribution Ubuntu-24.04 --user root /usr/local/bin/shop-agent-startup.sh`

4. **条件**
   - 取消勾选「只有在计算机使用交流电源时才启动」

5. **设置**
   - 勾选「如果任务失败，每隔 1 分钟重新启动」

---

> 配完这一套，你关机再开机，WSL 和服务都会自己跑起来。有问题看 `/var/log/shop-agent*.log`。

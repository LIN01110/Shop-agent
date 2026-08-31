"""
Locust 压测脚本（Phase 4 模块 5）

安装：
    pip install locust

运行（Web 界面）：
    cd server
    locust -f scripts/locustfile.py --host=http://localhost:8000

运行（无头模式，命令行直接出报告）：
    cd server
    locust -f scripts/locustfile.py \
        --host=http://localhost:8000 \
        --headless \
        -u 10 -r 2 \
        -t 60s \
        --csv=eval/load_test

参数说明：
    -u 10     并发用户数
    -r 2      每秒启动 2 个用户
    -t 60s    持续运行 60 秒
    --csv     输出 CSV 报告到 eval/load_test_*.csv
"""

from locust import HttpUser, task, between
import json


class ChatUser(HttpUser):
    """模拟用户调用 /chat 接口。"""

    wait_time = between(1, 3)  # 每个用户请求间隔 1-3 秒

    @task(3)
    def chat_text(self):
        """纯文本查询（权重 3）。"""
        payload = {
            "message": "推荐一款洁面乳",
            "session_id": f"loadtest_{self.user_id}",
        }
        with self.client.post(
            "/chat",
            json=payload,
            headers={"Content-Type": "application/json"},
            stream=True,  # SSE 流式
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                # 读取完整 SSE 流，统计事件
                events = []
                for line in response.iter_lines(decode_unicode=True):
                    if line.startswith("data:"):
                        try:
                            data = json.loads(line[5:])
                            events.append(data.get("event", ""))
                        except json.JSONDecodeError:
                            pass
                if "done" in events:
                    response.success()
                else:
                    response.failure("Missing done event")
            else:
                response.failure(f"HTTP {response.status_code}")

    @task(1)
    def chat_with_budget(self):
        """带预算查询（权重 1）。"""
        payload = {
            "message": "推荐200元以下的口红",
            "session_id": f"loadtest_budget_{self.user_id}",
        }
        with self.client.post(
            "/chat",
            json=payload,
            headers={"Content-Type": "application/json"},
            stream=True,
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")

    @task(1)
    def health_check(self):
        """健康检查（权重 1）。"""
        self.client.get("/health")

    def on_start(self):
        """每个用户启动时执行一次。"""
        pass

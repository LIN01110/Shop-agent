# Phase 4 实施指南：生产验证与部署就绪

> Phase 4 目标：验证 Phase 3 的参数调优、完善评估体系、完成容器化部署。
> 预计总工作量：你的手动操作约 2-3 小时，代码已全部生成。

---

## 快速开始

```bash
# 1. 安装新增依赖（locust 用于压测）
pip install locust

# 2. 运行检索参数评估（模块 1）
cd server
python -m scripts.eval_retrieval_params

# 3. 准备端到端测试用例（模块 2）
cp eval/end_to_end_cases.template.json eval/end_to_end_cases.json
# 按需补充/修改用例，然后运行：
python -m scripts.run_eval

# 4. 构建 Docker 镜像（模块 4）
cd ..  # 回到项目根目录
docker build -t shop-agent:latest .
docker-compose up -d

# 5. 压测（模块 5）
cd server
locust -f scripts/locustfile.py --host=http://localhost:8000
# 浏览器打开 http://localhost:8089 设置并发数开始测试
```

---

## 模块清单

### ✅ 模块 1：检索参数 Eval 验证

**文件**：`server/scripts/eval_retrieval_params.py`

**功能**：对比三组参数（10/50、5/20、5/50）的覆盖率、Top-1 准确率、延迟。

**运行**：
```bash
cd server
python -m scripts.eval_retrieval_params
```

**输出**：
- 控制台表格
- `server/eval/retrieval_param_report.md`

**你需要做的**：看报告数字，决定用哪组参数（通常选 P95 延迟最低且覆盖率不变的）。

---

### ✅ 模块 2：端到端评估框架

**文件**：
- `server/scripts/run_eval.py` — 评估脚本
- `server/eval/end_to_end_cases.template.json` — 测试用例模板

**运行**：
```bash
# 先复制模板，按需修改/补充用例
cp eval/end_to_end_cases.template.json eval/end_to_end_cases.json
# 编辑 end_to_end_cases.json，加入你的业务场景用例
python -m scripts.run_eval
```

**你需要做的**：
- 补充 10-20 条你实际业务场景的测试用例（比模板里的更贴合你的商品和用户）
- 看 PASS/FAIL 报告，修复失败的 case

---

### 📋 模块 3：vLLM 可行性评估（手动操作）

**文件**：`server/docs/vllm_feasibility_report.md`（调研模板）

**你需要做的**：
1. 查看 Ark 控制台账单，填入月均费用
2. 查看 /metrics 端点，填入日均查询量和延迟
3. 查阿里云/腾讯云 GPU 服务器价格，填入月租金
4. 根据决策树做出选择

**预期产出**：填完的表格 + "继续 Ark" 或 "上 vLLM" 的决策。

---

### ✅ 模块 4：容器化与 CI/CD

**文件**：
- `Dockerfile` — 多阶段构建，非 root 用户
- `docker-compose.yml` — 服务 + Redis + 可选 Milvus
- `.github/workflows/ci.yml` — GitHub Actions：test → build → docker test
- `Makefile` — 常用命令封装

**运行**：
```bash
# 构建镜像
make build

# 启动服务（Redis + App）
make run

# 停止
make stop

# 查看健康状态
curl http://localhost:8000/health

# 查看指标
curl http://localhost:8000/metrics
curl http://localhost:8000/metrics/quality
```

**你需要做的**：
- 把代码 push 到 GitHub，确认 Actions 绿灯
- 如需部署到云服务器，把镜像推送到镜像仓库（阿里云 ACR / Docker Hub）

---

### ✅ 模块 5：压测

**文件**：`server/scripts/locustfile.py`

**运行**：
```bash
# 方式 1：Web 界面（推荐初次使用）
cd server
locust -f scripts/locustfile.py --host=http://localhost:8000
# 浏览器打开 http://localhost:8089

# 方式 2：命令行无头模式（CI/CD 用）
locust -f scripts/locustfile.py \
    --host=http://localhost:8000 \
    --headless -u 10 -r 2 -t 60s \
    --csv=eval/load_test
```

**你需要做的**：
- 根据你的目标并发调整 `-u`（并发用户）和 `-t`（持续时间）
- 查看生成的 `eval/load_test_*.csv` 报告
- 记录 QPS、P99 延迟、错误率到 `server/docs/load_test_report.md`

---

### 📋 模块 6：Milvus SQ8 量化（手动操作）

**文件**：`server/docs/milvus_sq8_guide.md`（操作 checklist）

**你需要做的**：
1. 备份 Milvus 数据
2. 按指南执行索引重建（IVF_SQ8 或 HNSW_SQ8）
3. 重新运行模块 1 的 eval 脚本，验证准确率下降 < 1%
4. 记录存储节省比例

**预期产出**：准确率对比报告 + 存储节省数据。

---

## 验收 checklist

| 模块 | 验收标准 | 状态 |
|---|---|---|
| 模块 1 | `retrieval_param_report.md` 生成，参数已选定 | [ ] |
| 模块 2 | `end_to_end_cases.json` 有 20+ 条用例，通过率 > 90% | [ ] |
| 模块 3 | `vllm_feasibility_report.md` 已填完，有明确决策 | [ ] |
| 模块 4 | `docker build` 成功，`docker-compose up` 服务可访问 | [ ] |
| 模块 5 | 压测报告生成，记录了 QPS / P99 / 错误率 | [ ] |
| 模块 6 | SQ8 索引重建完成，准确率下降 < 1%，存储节省 > 50% | [ ] |

---

## 文件清单

### 新增文件（已生成）
```
Dockerfile
docker-compose.yml
.github/workflows/ci.yml
Makefile
server/scripts/eval_retrieval_params.py
server/scripts/run_eval.py
server/scripts/locustfile.py
server/eval/end_to_end_cases.template.json
server/docs/vllm_feasibility_report.md
server/docs/milvus_sq8_guide.md
```

### 运行后生成的文件
```
server/eval/retrieval_param_report.md
server/eval/end_to_end_report.md
server/eval/end_to_end_cases.json（你复制并编辑的）
server/eval/load_test_*.csv（压测结果）
server/docs/load_test_report.md（你手写的）
```

---

## 下一步

Phase 4 全部完成后，项目达到 **可上线状态**。后续优化取决于业务量，参考 `server/FUTURE_PLAN.md` 的决策树。

如果有面试/答辩需求，可以把 `retrieval_param_report.md` + `end_to_end_report.md` + `load_test_report.md` 整理成一份「系统优化报告」，作为项目亮点。

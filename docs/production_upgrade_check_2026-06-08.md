# 生产级升级前检查

更新时间：2026-06-08

## 检查结论

当前项目已经具备可演示的端到端闭环：Android 真机客户端、FastAPI SSE、商品 RAG 检索、结构化商品卡、商品对比、购物车、轻量图片找货、场景组合推荐、LLM grounded answer、GroundingGuard、trace 和离线评估都已经接入。

但它还不是生产级系统。生产级升级前需要优先补齐状态持久化、真实向量库压测、正式延迟指标、图片上传/视觉检索和结构化事实校验。

## 本轮验证

- `python -m pytest server/tests -q`：78 passed
- `python scripts/evaluate_agent.py`：13/13 passed
- `.\gradlew.bat :app:compileDebugKotlin`：BUILD SUCCESSFUL
- `git diff --check`：无空白错误，仅 Windows LF/CRLF 提示
- `.env`：已被 `.gitignore` 忽略，tracked 文件未检出真实 API key
- `SESSION_BACKEND=sqlite`：已增加单实例 DB 持久化路径
- `SESSION_BACKEND=redis`：已增加多进程共享 session/cart 后端

## 已降级的风险

### Debug API 与 CORS

当前 `APP_ENV=production` 默认不挂载 `/debug`，CORS 来源和 credentials 通过环境变量控制，并避免 wildcard origin 与 credentials 同时开启。

剩余风险：非生产环境显式打开 debug 时仍无鉴权。生产部署应增加 debug token、内网限制或完全禁用。

### 模块臃肿

后端已拆分为 `AgentWorkflow`、handlers、semantic schema/rules/llm、inputs 多模态模块。Android 端已拆出消息、输入栏、商品卡、购物车、对比面板。当前模块规模可维护。

剩余风险：继续堆场景规则时仍会让 `semantic_rules.py` 和 `scenarios.py` 变重。

### 本地内存增长

`SessionStore` 已加入 TTL 和最大 session 数，trace store 也有容量限制。`SESSION_BACKEND=sqlite` 时会把完整 `SessionState` 写入 SQLite，购物车和候选上下文可在服务重启后恢复。`SESSION_BACKEND=redis` 时可以让多个后端进程共享 session/cart。

剩余风险：trace 仍是内存态；Redis session/cart 尚未在真实 Redis 服务上做联调压测。

## 生产级阻塞项

### P0：Trace 持久化与真实 Redis 联调

问题：session/cart 已有 SQLite 和 Redis 后端，但 Redis 尚未连接真实服务压测，trace 持久化仍未完成。

生产升级目标：
- 用真实 Redis 服务回归 session/cart。
- trace 至少写入 Redis Stream、数据库或结构化日志。
- 增加 TTL、容量上限、序列化 schema 版本。
- 多 worker、多实例下同一 session 行为一致。

验收：
- SQLite 单实例下服务重启后购物车可恢复。
- 两个后端进程访问同一 session 得到一致状态。
- TTL 过期后 session 自动清理。

### P0：生产安全边界

问题：debug 已默认关，但未做鉴权；真实部署还缺统一生产启动配置。

生产升级目标：
- 增加生产 profile 示例。
- `/debug` 即使开启也需要 token 或内网限制。
- 明确 CORS 白名单，不允许生产 wildcard。
- 增加启动时安全配置自检。

验收：
- `APP_ENV=production` 下 `/debug/traces` 404。
- 配置 `CORS_ALLOWED_ORIGINS=*` 且 `CORS_ALLOW_CREDENTIALS=true` 时 credentials 自动关闭或启动失败。
- 生产文档包含安全启动命令。

### P1：真实 Chroma + embedding 大库压测

问题：Chroma metadata where 只完成 1k 冒烟，本地 fallback 50k 建索引约 72 秒，不适合生产主路径。

生产升级目标：
- 用 `USE_CHROMA=true` 和 `USE_ARK_EMBEDDING=true` 做真实 embedding 灌库。
- 对 Chroma 10k/50k 运行端到端检索压测。
- 记录 P50/P95/P99、建库耗时、内存、失败率。
- 为 taxonomy/schema 变更建立索引版本和重灌提醒。

验收：
- 50k 商品 Chroma 查询 P95 达到可接受阈值。
- 商品类型 metadata filter 能稳定防止跨类目污染。
- 重启服务不重新全量建库。

### P1：正式延迟指标

问题：当前 first token 是即时安抚 token，证明的是体感启动速度，不证明有效答案速度。

生产升级目标：
- 同时测 first token、first useful token、first product card、done latency。
- `--wait-done` 作为正式压测默认项之一。
- 增加客户端取消流后的后端资源释放检查。

验收：
- 目标服务器上首 token、商品卡、完整回答都有报告。
- LLM 开启和关闭两种模式都有压测结果。
- 取消请求后无明显资源泄漏。

### P1：多模态生产化

问题：Android 端整图 readBytes 后 base64 JSON 上传，后端 12x12 RGB 签名只适合参考图同款 Demo。

生产升级目标：
- 端侧压缩图片，限制尺寸和质量。
- 后端改为 multipart upload 或图片对象存储。
- 视觉检索替换为 CLIP/VLM embedding。
- 图片索引与文本商品索引统一商品 ID。
- 低置信度时主动说明无法确认，不硬推商品。

验收：
- 真实拍照、截图、商品主图三类图片都有评估样例。
- 图片上传大小可控，弱网下不会阻塞 UI。
- 低相似度图片不会编造商品。

### P1：结构化事实校验

问题：`GroundingGuard` 仍依赖词表、价格归一和名称/品牌字符串匹配。

生产升级目标：
- 回答中的商品、价格、库存、优惠、销量等事实必须映射到结构化 citation。
- 对 LLM 输出做 sentence-level fact check。
- 对未出现在 schema 的商业事实直接拦截。

验收：
- LLM 编造价格、优惠、库存、销量时 100% 降级。
- 回答中的每个价格都能定位到商品 metadata。
- 商品卡和自然语言回答一致。

## P2 改造项

### 场景组合配置化

当前三亚、通勤、运动训练等场景写在 Python 分支里。应迁移到 `data/scenario_bundles.json`，由配置定义场景触发词、槽位 query、product_type 和预算策略。

### 评估集扩容

当前离线评估 13/13 通过，但覆盖面仍小。需要增加：
- 长链多轮对话
- 失败/低置信度样例
- 跨类目切换
- 图片找货
- 购物车边界条件
- 场景组合变体

### 运维交付

需要补齐：
- Dockerfile / docker-compose
- 生产 `.env.example`
- systemd 或进程守护说明
- 日志、健康检查、版本信息
- 数据灌库和回滚流程

## 建议升级顺序

1. 先用真实 Redis 回归 session/cart，并补 trace 持久化，解决生产多实例基础。
2. 做生产安全启动 profile，锁住 debug、CORS、密钥和健康检查。
3. 做真实 Chroma + embedding 10k/50k 压测，确认数据层可生产。
4. 做 first useful token、first product card、done latency 压测。
5. 把场景组合迁到配置文件，避免继续堆硬编码。
6. 升级多模态上传链路和视觉 embedding。
7. 把 GroundingGuard 升级为结构化 citation/fact-check。
8. 扩充离线评估集并接入每次改动的回归清单。

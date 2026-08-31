"""
Purpose: 管理后台 API：提供 /admin 管理页面、trace 统计、健康度检查。
"""

import html
import json
from collections import Counter, deque
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from server.agent.orchestrator import Orchestrator
from server.app_container import get_orchestrator
from server.config import Settings, get_settings
from server.nlu.taxonomy_classifier import classify_taxonomy_query
from server.rag.taxonomy_governance import annotate_products, build_taxonomy_manifest


router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("", response_class=HTMLResponse)
async def admin_index() -> HTMLResponse:
    return HTMLResponse(render_admin_index())


@router.get("/", response_class=HTMLResponse)
async def admin_index_slash() -> HTMLResponse:
    return HTMLResponse(render_admin_index())


@router.get("/api/overview")
async def admin_overview(
    settings: Settings = Depends(get_settings),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> dict:
    traces = orchestrator.trace_store.list(limit=100)
    failure_rows = read_jsonl_tail(settings.query_feedback_log_file, limit=200)
    manifest = build_taxonomy_manifest(product_data_path=settings.product_data_file)
    checks = build_readiness_checks(
        settings=settings,
        trace_count=len(traces),
        failure_rows=failure_rows,
        taxonomy=manifest.as_metadata(),
    )
    return {
        "service": {
            "status": "ok",
            "env": settings.app_env,
            "debug_api_enabled": settings.debug_api_enabled,
            "admin_console_enabled": settings.admin_console_enabled,
            "session_backend": settings.normalized_session_backend,
            "product_data": {
                "backend": "local_json",
                "path": safe_display_path(settings.product_data_file),
            },
            "query_feedback_log": {
                "backend": "jsonl",
                "path": safe_display_path(settings.query_feedback_log_file),
            },
            "trace_backend": "in_memory",
        },
        "trace": {
            "recent_count": len(traces),
            "handler_counts": dict(Counter(trace.handler or "unknown" for trace in traces)),
            "avg_duration_ms": average([trace.duration_ms for trace in traces]),
        },
        "query_feedback": {
            "path": safe_display_path(settings.query_feedback_log_file),
            "recent_count": len(failure_rows),
            "reason_counts": count_feedback_reasons(failure_rows),
        },
        "taxonomy": sanitize_manifest(manifest.as_metadata()),
        "readiness_checks": checks,
    }


@router.get("/api/traces")
async def admin_list_traces(
    session_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> list[dict]:
    return [compact_trace(trace.model_dump()) for trace in orchestrator.trace_store.list(session_id=session_id, limit=limit)]


@router.get("/api/traces/{trace_id}")
async def admin_get_trace(
    trace_id: str,
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> dict:
    trace = orchestrator.trace_store.get(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace.model_dump()


@router.get("/api/query-failures")
async def admin_query_failures(
    limit: int = Query(default=50, ge=1, le=500),
    settings: Settings = Depends(get_settings),
) -> dict:
    rows = read_jsonl_tail(settings.query_feedback_log_file, limit=limit)
    return {
        "path": safe_display_path(settings.query_feedback_log_file),
        "items": rows,
        "reason_counts": count_feedback_reasons(rows),
    }


@router.get("/api/taxonomy")
async def admin_taxonomy(settings: Settings = Depends(get_settings)) -> dict:
    products = load_product_rows(settings.product_data_file)
    _, report = annotate_products(products)
    manifest = build_taxonomy_manifest(product_data_path=settings.product_data_file)
    missing_ids = report.get("missing_product_type_ids", [])
    return {
        "manifest": sanitize_manifest(manifest.as_metadata()),
        "annotation": {
            **report,
            "missing_product_type_preview": missing_ids[:50],
        },
    }


@router.get("/api/eval/taxonomy")
async def admin_taxonomy_eval(
    cases_path: str = "",
    settings: Settings = Depends(get_settings),
) -> dict:
    path = Path(cases_path) if cases_path else settings.taxonomy_eval_cases_file
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Taxonomy eval cases not found: {path}")
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise HTTPException(status_code=400, detail="Taxonomy eval cases must be a JSON array")

    failures: list[dict[str, Any]] = []
    for case in cases:
        actual = evaluate_taxonomy_case(case)
        errors = compare_taxonomy_expected(case.get("expected", {}), actual)
        if errors:
            failures.append(
                {
                    "id": case.get("id", ""),
                    "query": case.get("query", ""),
                    "errors": errors,
                    "actual": actual,
                }
            )
    passed = len(cases) - len(failures)
    return {
        "cases_path": str(path),
        "total": len(cases),
        "passed": passed,
        "failed": len(failures),
        "pass_rate": passed / len(cases) if cases else 0.0,
        "failures": failures,
    }


def compact_trace(trace: dict) -> dict:
    metadata = trace.get("metadata", {}) if isinstance(trace.get("metadata"), dict) else {}
    return {
        "trace_id": trace.get("trace_id", ""),
        "session_id": trace.get("session_id", ""),
        "message": trace.get("message", ""),
        "handler": trace.get("handler", ""),
        "duration_ms": trace.get("duration_ms", 0),
        "event_counts": trace.get("event_counts", {}),
        "product_ids": trace.get("product_ids", []),
        "cart_total_quantity": trace.get("cart_total_quantity"),
        "scope_transition": metadata.get("scope_transition", {}),
        "query_feedback": metadata.get("query_feedback", {}),
    }


def build_readiness_checks(
    *,
    settings: Settings,
    trace_count: int,
    failure_rows: list[dict],
    taxonomy: dict,
) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    env = settings.app_env.strip().lower()
    product_type_coverage = float(taxonomy.get("product_type_coverage", 0.0) or 0.0)
    no_retrieval_count = count_feedback_reasons(failure_rows).get("no_retrieval_output", 0)

    add_check(
        checks,
        check_id="taxonomy_product_type_coverage",
        severity="blocker" if product_type_coverage < 0.7 else "warning" if product_type_coverage < 0.9 else "ok",
        message=(
            f"Product Type coverage is {product_type_coverage:.0%}. "
            "MVP target >=70%, production candidate >=90%."
        ),
    )
    add_check(
        checks,
        check_id="retrieval_failure_rate_signal",
        severity="warning" if no_retrieval_count else "ok",
        message=(
            f"Recent no_retrieval_output count is {no_retrieval_count}. "
            "Inspect root-cause reasons before treating answer quality as the main issue."
        ),
    )
    add_check(
        checks,
        check_id="trace_observability",
        severity="warning" if trace_count == 0 else "ok",
        message=(
            "No recent traces. Run at least one chat request through this server process "
            "to validate route, retrieval, rerank, grounding, and latency observability."
            if trace_count == 0
            else f"{trace_count} recent traces available."
        ),
    )
    add_check(
        checks,
        check_id="session_backend",
        severity="blocker"
        if env in {"prod", "production"} and settings.normalized_session_backend == "sqlite"
        else "warning"
        if settings.normalized_session_backend == "sqlite"
        else "ok",
        message=(
            f"Session backend is {settings.normalized_session_backend}. "
            "SQLite is suitable for local development; use Redis/DB for multi-instance production."
        ),
    )
    add_check(
        checks,
        check_id="product_data_backend",
        severity="blocker" if env in {"prod", "production"} else "warning",
        message=(
            "Product data backend is local JSON. This is acceptable for MVP/testing, "
            "but production should use replaceable catalog/pricing/inventory/policy services."
        ),
    )
    add_check(
        checks,
        check_id="debug_admin_exposure",
        severity="blocker"
        if env in {"prod", "production"} and (settings.debug_api_enabled or settings.admin_console_enabled)
        else "ok"
        if env not in {"prod", "production"}
        else "warning",
        message=(
            f"env={settings.app_env}, debug_api_enabled={settings.debug_api_enabled}, "
            f"admin_console_enabled={settings.admin_console_enabled}. Production should disable debug/admin "
            "or put them behind authentication and audit logging."
        ),
    )
    return checks


def add_check(checks: list[dict[str, str]], *, check_id: str, severity: str, message: str) -> None:
    checks.append({"id": check_id, "severity": severity, "message": message})


def safe_display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve())).replace("\\", "/")
    except ValueError:
        return f"<external>/{path.name}"


def sanitize_manifest(manifest: dict) -> dict:
    sanitized = dict(manifest)
    product_path = sanitized.get("product_data_path")
    if product_path:
        sanitized["product_data_path"] = safe_display_path(Path(str(product_path)))
    return sanitized


def read_jsonl_tail(path: Path, limit: int) -> list[dict]:
    if not path.exists():
        return []
    rows: deque[dict] = deque(maxlen=limit)
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return list(rows)


def count_feedback_reasons(rows: list[dict]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for row in rows:
        for reason in row.get("reasons", []) if isinstance(row.get("reasons"), list) else []:
            counter[str(reason)] += 1
    return dict(counter)


def load_product_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def evaluate_taxonomy_case(case: dict[str, Any]) -> dict[str, Any]:
    result = classify_taxonomy_query(str(case.get("query", "")))
    return {
        "product_types": [item.value for item in result.product_types],
        "categories": [item.value for item in result.categories],
        "sources": [item.source for item in (*result.product_types, *result.categories)],
        "used_embedding": result.used_embedding,
    }


def compare_taxonomy_expected(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ["product_types", "categories"]:
        if key not in expected:
            continue
        expected_values = list(expected.get(key) or [])
        actual_values = list(actual.get(key) or [])
        missing = [item for item in expected_values if item not in actual_values]
        unexpected = [item for item in actual_values if item not in expected_values]
        if missing:
            errors.append(f"{key}: missing {missing}, actual={actual_values}")
        if expected.get(f"exact_{key}", True) and unexpected:
            errors.append(f"{key}: unexpected {unexpected}, expected={expected_values}")
    return errors


def average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 2)


def render_admin_index() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>RAG Shopping Agent Admin</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #1e252b;
      background: #f5f7fa;
    }
    header {
      height: 56px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 18px;
      border-bottom: 1px solid #dfe4ea;
      background: #ffffff;
      position: sticky;
      top: 0;
      z-index: 2;
    }
    header h1 { font-size: 17px; margin: 0; }
    header button {
      border: 1px solid #c8d0d9;
      background: #fff;
      border-radius: 6px;
      padding: 8px 12px;
      cursor: pointer;
    }
    main {
      display: grid;
      grid-template-columns: 260px minmax(0, 1fr);
      min-height: calc(100vh - 56px);
    }
    nav {
      border-right: 1px solid #dfe4ea;
      background: #ffffff;
      padding: 14px;
    }
    nav button {
      display: block;
      width: 100%;
      text-align: left;
      border: 0;
      background: transparent;
      border-radius: 6px;
      padding: 10px 12px;
      cursor: pointer;
      font-size: 14px;
      color: #33414d;
    }
    nav button.active {
      background: #e8f0ff;
      color: #174ea6;
      font-weight: 600;
    }
    .content {
      padding: 18px;
      display: grid;
      gap: 14px;
      align-content: start;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }
    .panel, .metric {
      background: #ffffff;
      border: 1px solid #dfe4ea;
      border-radius: 8px;
      padding: 14px;
    }
    .metric b {
      display: block;
      font-size: 24px;
      margin-top: 8px;
    }
    .muted { color: #64717d; font-size: 13px; }
    .toolbar {
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
      margin-bottom: 10px;
    }
    input {
      border: 1px solid #c8d0d9;
      border-radius: 6px;
      padding: 8px 10px;
      min-width: 220px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      border-bottom: 1px solid #edf0f3;
      padding: 9px 8px;
      vertical-align: top;
      text-align: left;
    }
    th { color: #53616d; font-weight: 600; background: #fafbfc; }
    code {
      background: #eef2f7;
      border-radius: 4px;
      padding: 2px 5px;
      word-break: break-all;
    }
    pre {
      white-space: pre-wrap;
      word-break: break-word;
      margin: 0;
      font-size: 12px;
      line-height: 1.5;
    }
    .hidden { display: none; }
    .ok { color: #0b8043; }
    .warn { color: #b06000; }
    .bad { color: #b3261e; }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; }
      nav { display: flex; gap: 8px; overflow-x: auto; border-right: 0; border-bottom: 1px solid #dfe4ea; }
      nav button { width: auto; white-space: nowrap; }
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 560px) {
      .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>RAG Shopping Agent Admin</h1>
    <button onclick="refreshCurrent()">刷新</button>
  </header>
  <main>
    <nav>
      <button class="active" data-view="overview" onclick="showView('overview')">总览</button>
      <button data-view="traces" onclick="showView('traces')">请求 Trace</button>
      <button data-view="failures" onclick="showView('failures')">失败 Query</button>
      <button data-view="taxonomy" onclick="showView('taxonomy')">Taxonomy 治理</button>
      <button data-view="eval" onclick="showView('eval')">离线评测</button>
    </nav>
    <section class="content">
      <div id="view-overview"></div>
      <div id="view-traces" class="hidden"></div>
      <div id="view-failures" class="hidden"></div>
      <div id="view-taxonomy" class="hidden"></div>
      <div id="view-eval" class="hidden"></div>
    </section>
  </main>
  <script>
    let currentView = 'overview';
    const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const asJson = (value) => escapeHtml(JSON.stringify(value ?? {}, null, 2));
    async function api(path) {
      const response = await fetch(path);
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return response.json();
    }
    function showView(name) {
      currentView = name;
      document.querySelectorAll('nav button').forEach(btn => btn.classList.toggle('active', btn.dataset.view === name));
      for (const key of ['overview', 'traces', 'failures', 'taxonomy', 'eval']) {
        document.getElementById(`view-${key}`).classList.toggle('hidden', key !== name);
      }
      refreshCurrent();
    }
    async function refreshCurrent() {
      try {
        if (currentView === 'overview') return renderOverview(await api('/admin/api/overview'));
        if (currentView === 'traces') return renderTraces(await api('/admin/api/traces?limit=50'));
        if (currentView === 'failures') return renderFailures(await api('/admin/api/query-failures?limit=100'));
        if (currentView === 'taxonomy') return renderTaxonomy(await api('/admin/api/taxonomy'));
        if (currentView === 'eval') return renderEval(await api('/admin/api/eval/taxonomy'));
      } catch (error) {
        document.getElementById(`view-${currentView}`).innerHTML = `<div class="panel bad">加载失败：${escapeHtml(error.message)}</div>`;
      }
    }
    function metric(label, value, note='') {
      return `<div class="metric"><span class="muted">${escapeHtml(label)}</span><b>${escapeHtml(value)}</b><span class="muted">${escapeHtml(note)}</span></div>`;
    }
    function renderOverview(data) {
      const checkRows = (data.readiness_checks || []).map(item => `<tr>
        <td><code>${escapeHtml(item.id)}</code></td>
        <td class="${escapeHtml(item.severity === 'ok' ? 'ok' : item.severity === 'blocker' ? 'bad' : 'warn')}">${escapeHtml(item.severity)}</td>
        <td>${escapeHtml(item.message)}</td>
      </tr>`).join('');
      const html = `
        <div class="grid">
          ${metric('服务状态', data.service.status, data.service.env)}
          ${metric('近期 Trace', data.trace.recent_count, `avg ${data.trace.avg_duration_ms} ms`)}
          ${metric('失败 Query', data.query_feedback.recent_count, data.query_feedback.path)}
          ${metric('Product Type 覆盖率', `${Math.round(data.taxonomy.product_type_coverage * 100)}%`, data.taxonomy.fingerprint)}
        </div>
        <div class="panel"><h2>上线就绪检查</h2><table>
          <thead><tr><th>检查项</th><th>级别</th><th>说明</th></tr></thead>
          <tbody>${checkRows}</tbody>
        </table></div>
        <div class="panel"><h2>服务配置</h2><pre>${asJson(data.service)}</pre></div>
        <div class="panel"><h2>Handler 分布</h2><pre>${asJson(data.trace.handler_counts)}</pre></div>
        <div class="panel"><h2>失败原因分布</h2><pre>${asJson(data.query_feedback.reason_counts)}</pre></div>`;
      document.getElementById('view-overview').innerHTML = html;
    }
    function renderTraces(items) {
      const rows = items.map(item => `<tr>
        <td><code>${escapeHtml(item.trace_id)}</code></td>
        <td>${escapeHtml(item.handler)}</td>
        <td>${escapeHtml(item.duration_ms)} ms</td>
        <td>${escapeHtml(item.message)}</td>
        <td><pre>${asJson(item.scope_transition)}</pre></td>
        <td><pre>${asJson(item.product_ids)}</pre></td>
      </tr>`).join('');
      document.getElementById('view-traces').innerHTML = `<div class="panel"><h2>最近请求 Trace</h2><table>
        <thead><tr><th>trace_id</th><th>handler</th><th>耗时</th><th>query</th><th>scope</th><th>products</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="6" class="muted">暂无 trace</td></tr>'}</tbody>
      </table></div>`;
    }
    function renderFailures(data) {
      const rows = data.items.slice().reverse().map(item => `<tr>
        <td><code>${escapeHtml(item.trace_id)}</code></td>
        <td>${escapeHtml((item.reasons || []).join(', '))}</td>
        <td>${escapeHtml(item.message)}</td>
        <td><pre>${asJson(item.filters)}</pre></td>
      </tr>`).join('');
      document.getElementById('view-failures').innerHTML = `<div class="panel"><h2>失败 Query 回流</h2>
        <p class="muted">${escapeHtml(data.path)}</p>
        <pre>${asJson(data.reason_counts)}</pre>
        <table><thead><tr><th>trace_id</th><th>原因</th><th>query</th><th>filters</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="4" class="muted">暂无失败样本</td></tr>'}</tbody></table></div>`;
    }
    function renderTaxonomy(data) {
      const a = data.annotation;
      document.getElementById('view-taxonomy').innerHTML = `<div class="grid">
        ${metric('Product Type 覆盖率', `${Math.round(a.product_type_coverage * 100)}%`, `${a.missing_product_type_ids.length} missing`)}
        ${metric('Category 覆盖率', `${Math.round(a.category_coverage * 100)}%`, `${a.missing_category_ids.length} missing`)}
        ${metric('Taxonomy 指纹', data.manifest.fingerprint, data.manifest.product_taxonomy_version)}
        ${metric('商品数', a.product_count, data.manifest.product_data_path)}
      </div>
      <div class="panel"><h2>Manifest</h2><pre>${asJson(data.manifest)}</pre></div>
      <div class="panel"><h2>未标注 Product Type 商品预览</h2><pre>${asJson(a.missing_product_type_preview)}</pre></div>`;
    }
    function renderEval(data) {
      const rows = data.failures.map(item => `<tr>
        <td>${escapeHtml(item.id)}</td>
        <td>${escapeHtml(item.query)}</td>
        <td><pre>${asJson(item.errors)}</pre></td>
        <td><pre>${asJson(item.actual)}</pre></td>
      </tr>`).join('');
      const cls = data.failed === 0 ? 'ok' : 'bad';
      document.getElementById('view-eval').innerHTML = `<div class="grid">
        ${metric('Taxonomy Eval', `${data.passed}/${data.total}`, data.cases_path)}
        ${metric('Pass Rate', `${Math.round(data.pass_rate * 100)}%`, `${data.failed} failed`)}
      </div>
      <div class="panel"><h2 class="${cls}">失败样例</h2><table>
      <thead><tr><th>id</th><th>query</th><th>errors</th><th>actual</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="4" class="muted">全部通过</td></tr>'}</tbody></table></div>`;
    }
    refreshCurrent();
  </script>
</body>
</html>"""


def escape(value: str) -> str:
    return html.escape(value, quote=True)

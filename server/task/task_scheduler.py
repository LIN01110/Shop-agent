"""
任务调度器：并行执行多个任务，管理超时和失败。

使用 ThreadPoolExecutor 实现并行，未来可替换为 Celery。
"""

from __future__ import annotations

import concurrent.futures
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from server.task.intent_decomposer import Task


@dataclass
class TaskResult:
    task: Task
    success: bool
    result: Any = None
    error: str = ""
    latency_ms: float = 0.0


class TaskScheduler:
    """任务调度器：并行执行多任务。"""

    def __init__(self, max_workers: int = 4, default_timeout: float = 10.0) -> None:
        self.max_workers = max_workers
        self.default_timeout = default_timeout

    def run_parallel(
        self,
        tasks: list[Task],
        executors: dict[str, Callable[[Task], Any]],
    ) -> list[TaskResult]:
        """
        并行执行多个任务。
        
        Args:
            tasks: 任务列表
            executors: 任务类型 → 执行函数的映射
        """
        if not tasks:
            return []

        results: list[TaskResult] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            future_to_task: dict[concurrent.futures.Future, Task] = {}
            for task in tasks:
                executor = executors.get(task.task_type)
                if not executor:
                    results.append(TaskResult(task=task, success=False, error=f"No executor for task type: {task.task_type}"))
                    continue
                future = pool.submit(self._run_single, task, executor)
                future_to_task[future] = task

            for future in concurrent.futures.as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    result = future.result(timeout=self.default_timeout)
                    results.append(result)
                except concurrent.futures.TimeoutError:
                    results.append(TaskResult(task=task, success=False, error="Task timeout", latency_ms=self.default_timeout * 1000))
                except Exception as exc:
                    results.append(TaskResult(task=task, success=False, error=str(exc)))

        return results

    def _run_single(self, task: Task, executor: Callable[[Task], Any]) -> TaskResult:
        started = time.perf_counter()
        try:
            result = executor(task)
            latency = (time.perf_counter() - started) * 1000
            return TaskResult(task=task, success=True, result=result, latency_ms=latency)
        except Exception as exc:
            latency = (time.perf_counter() - started) * 1000
            return TaskResult(task=task, success=False, error=str(exc), latency_ms=latency)

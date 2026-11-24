"""Web入库COPT：每进程一个专属线程、独立模型及有界排队。"""
import atexit
from concurrent.futures import Future
from contextvars import copy_context
import os
from queue import Full, Queue
from threading import Lock, Thread, get_ident
from time import perf_counter

from modules.slotting.inbound_objectives import ExactDistanceCache
from solvers import warehouse_inbound_copt as backend


def optimized_config(config):
    return dict(config, aggregate_full=True, copt_read_mode="bulk", copt_objectives="combined", copt_distance_cache="batch")


class InboundCoptRuntime:
    def __init__(self):
        self.queue = Queue(maxsize=8)
        self.lock = Lock()
        self.closed = False
        self.close_error = None
        # daemon允许解释器进入atexit；atexit及Web shutdown均会显式join并在原线程清理。
        self.thread = Thread(target=self._loop, name="inbound-copt", daemon=True)
        self.thread.start()

    def submit(self, *args):
        if get_ident() == self.thread.ident:
            raise RuntimeError("COPT求解线程不允许递归提交入库任务")
        future = Future()
        with self.lock:
            if self.closed:
                raise RuntimeError("COPT入库服务正在关闭")
            try:
                self.queue.put_nowait((future, copy_context(), args, perf_counter()))
            except Full:
                raise RuntimeError("COPT入库任务繁忙，已有8个请求排队，请稍后重试") from None
        return future

    def _loop(self):
        session = backend.CoptEnvironmentSession()
        cache = ExactDistanceCache()
        try:
            while True:
                item = self.queue.get()
                try:
                    if item is None:
                        break
                    future, context, args, queued_at = item
                    if not future.set_running_or_notify_cancel():
                        continue
                    try:
                        result = context.run(self._execute, session, cache, args, queued_at)
                    except BaseException as error:
                        future.set_exception(error)
                    else:
                        future.set_result(result)
                finally:
                    self.queue.task_done()
        finally:
            try:
                session.close()
            except Exception as error:
                self.close_error = error

    def _execute(self, session, cache, args, queued_at):
        wait = perf_counter() - queued_at
        inventory, orders, capacities, layout, grades, config = args
        try:
            result = backend.solve(inventory, orders, capacities, layout, grades, optimized_config(config),
                                   session=session, distance_cache=cache)
        except Exception as error:
            # 后端通常已废弃失败环境；这里也覆盖调度边界上的异常。
            try:
                session.invalidate()
            except Exception as cleanup:
                error.args = (f"{error}；环境清理失败：{cleanup}",)
            error.copt_runtime = self._metadata(session, cache, wait)
            raise
        result["copt_runtime"] = self._metadata(session, cache, wait)
        return result

    @staticmethod
    def _metadata(session, cache, wait):
        return dict(profile="optimized_inbound", process_id=os.getpid(), worker_thread_id=get_ident(),
                    queue_limit=8, queue_wait_s=wait, environment_created=session.stats["created"],
                    environment_reused_calls=session.stats["reused_calls"], distance_cache=cache.report())

    def close(self):
        if get_ident() == self.thread.ident:
            raise RuntimeError("COPT求解线程不能等待自身关闭")
        with self.lock:
            first = not self.closed
            self.closed = True
        if first:
            # sentinel排在已接收请求之后，不取消正在求解或排队的任务。
            self.queue.put(None)
        self.thread.join()
        if self.close_error is not None:
            raise RuntimeError(f"COPT入库环境关闭失败：{self.close_error}") from self.close_error


_lock = Lock()
_runtime = None
_stopped = False


def start_runtime():
    global _stopped
    with _lock:
        _stopped = False


def solve(*args):
    global _runtime
    with _lock:
        if _stopped:
            raise RuntimeError("COPT入库服务已关闭，请重启服务")
        if _runtime is None:
            _runtime = InboundCoptRuntime()
        future = _runtime.submit(*args)
    return future.result()


def shutdown_runtime():
    global _runtime, _stopped
    with _lock:
        _stopped = True
        runtime, _runtime = _runtime, None
        if runtime is not None:
            runtime.close()


atexit.register(shutdown_runtime)

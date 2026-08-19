"""A minimal in-memory background task queue.

/enqueue drops a task in; a single background worker thread pulls
tasks off one at a time and runs them. This is deliberately simple --
in-memory, single-process only:

- Tasks are lost if the process restarts (nothing is persisted).
- Doesn't scale across multiple server instances -- each process has
  its own queue, so a task enqueued on instance A never runs if the
  request that reads its result lands on instance B.
- One worker thread means tasks run strictly one at a time; a slow
  task delays every task queued after it.

A real system would reach for Redis/RabbitMQ/SQS + a proper worker
pool (Celery, RQ, etc.) for durability and horizontal scaling. This is
the "basic queue mechanism" version the exercise asks for -- good
enough to see the pattern, not to run in production.
"""
import logging
import queue
import threading

logger = logging.getLogger('task_queue')

task_queue: "queue.Queue" = queue.Queue()


def enqueue(func, *args, **kwargs) -> None:
    task_queue.put((func, args, kwargs))


def _worker_loop() -> None:
    while True:
        func, args, kwargs = task_queue.get()
        try:
            func(*args, **kwargs)
        except Exception:
            logger.exception('task %s failed', getattr(func, '__name__', func))
        finally:
            task_queue.task_done()


def start_worker() -> None:
    threading.Thread(target=_worker_loop, daemon=True).start()

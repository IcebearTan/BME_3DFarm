"""Celery 集成：让 task 在 Flask 应用上下文中执行。

启动 worker:  celery -A celery_app.celery worker --loglevel=info
启动 beat:    celery -A celery_app.celery beat --loglevel=info

task 文件示例（tasks/poller.py）:
    from celery_app import celery

    @celery.task(autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
    def sync_printer_statuses():
        ...
"""
from celery import Celery

from app import app


def make_celery(flask_app):
    celery = Celery(
        flask_app.import_name,
        broker=flask_app.config["CELERY_BROKER_URL"],
        backend=flask_app.config["CELERY_RESULT_BACKEND"],
    )
    celery.conf.update(flask_app.config)

    class ContextTask(celery.Task):
        """让每个 task 执行时进入 Flask 应用上下文（可访问 db / config）。"""

        def __call__(self, *args, **kwargs):
            with flask_app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery


celery = make_celery(app)

# Beat 定时任务（Phase 2 Poller 每 30s 同步活跃订单）
celery.conf.beat_schedule = {
    "sync-active-orders": {
        "task": "tasks.poller.sync_active_orders",
        "schedule": 30.0,
    },
}
celery.conf.timezone = "UTC"

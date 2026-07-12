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
        # 显式 include task 模块，否则 worker 进程不会导入它们，@celery.task
        # 不会注册，beat 触发时报 Received unregistered task。
        include=["tasks.poller", "tasks.dispatch"],
    )
    # 不要 celery.conf.update(flask_app.config)：flask config 里的大写
    # CELERY_BROKER_URL / CELERY_RESULT_BACKEND（旧式 key）会和上面构造器设的
    # 新式小写 broker_url / result_backend 冲突，celery 5.x 直接 ImproperlyConfigured。
    # broker/backend 已由构造器设好；task 运行时走 ContextTask 的 Flask 应用上下文，
    # 用 current_app.config 取业务配置即可。

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
    "sync-printers": {
        "task": "tasks.poller.sync_printers",
        "schedule": 30.0,
    },
}
celery.conf.timezone = "UTC"

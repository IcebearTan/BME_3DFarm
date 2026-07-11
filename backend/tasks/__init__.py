"""Celery task 按需增加。

Phase 2: tasks/poller.py   —— 定时同步打印机状态/队列（Celery Beat 触发）
Phase 3: tasks/dispatch.py —— 下发打印（add_to_queue + 上传文件）

每个 task 文件示例:
    from celery_app import celery

    @celery.task(autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
    def sync_printer_statuses():
        ...
"""

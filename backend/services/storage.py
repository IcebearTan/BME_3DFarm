"""MinIO 对象存储封装 —— 存 3D 模型文件（.3mf / .gcode.3mf）。

薄封装 minio Python 客户端（requirements 已有 minio>=7.2）。
单例 `storage`，app.py 启动时 `storage.init_app(app)` 注入配置；
bucket 懒建（首次 put_object 时 ensure，幂等），MinIO 未起时 app 仍可启动，
仅文件接口报错，不影响订单/credit 等核心流程。

文件约束（类型/大小/sha256）在订单蓝图层（orders.py）做，本模块只做通用对象操作。
"""
from minio import Minio
from datetime import timedelta


class Storage:
    """MinIO 封装单例。"""

    def __init__(self):
        self._client = None
        self.endpoint = None
        self.bucket = None
        self.secure = False
        self._bucket_ready = False

    def init_app(self, app):
        """从 app.config 读 MINIO_* 配置，建客户端（不连，懒连接）。"""
        self.endpoint = app.config["MINIO_ENDPOINT"]
        self.bucket = app.config["MINIO_BUCKET"]
        self.secure = app.config["MINIO_SECURE"]
        self._client = Minio(
            self.endpoint,
            access_key=app.config["MINIO_ACCESS_KEY"],
            secret_key=app.config["MINIO_SECRET_KEY"],
            secure=self.secure,
        )
        self._bucket_ready = False

    @property
    def client(self):
        if self._client is None:
            raise RuntimeError("Storage 未初始化（未调 init_app）")
        return self._client

    def ensure_bucket(self):
        """幂等建 bucket。MinIO 未起时抛异常由调用方处理。"""
        if self._bucket_ready:
            return
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)
        self._bucket_ready = True

    def reset_bucket_flag(self):
        """测试用：切 bucket 后重置懒建标志。"""
        self._bucket_ready = False

    # ── 对象操作 ──
    def put_object(self, key, stream, length, content_type="application/octet-stream"):
        """上传对象。stream 是可读二进制流，length 是字节数。"""
        self.ensure_bucket()
        return self.client.put_object(
            self.bucket, key, stream, length, content_type=content_type
        )

    def stat_object(self, key):
        return self.client.stat_object(self.bucket, key)

    def get_object(self, key):
        """返回可读响应流（调用方负责关闭）。"""
        return self.client.get_object(self.bucket, key)

    def remove_object(self, key):
        self.client.remove_object(self.bucket, key)

    def presigned_get_object(self, key, expires_hours=1):
        """生成临时下载 URL（给客户下载自己的文件）。"""
        return self.client.presigned_get_object(
            self.bucket, key, expires=timedelta(hours=expires_hours)
        )


storage = Storage()

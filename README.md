# BME_3DFarm

3D 打印农场管理系统（独立项目）。**credit 预付制、仅内网、7 台 P1S 打印机**，底层通过 Bambuddy 管理真实打印机。

详细方案见 [docs/打印农场-实施方案.md](docs/打印农场-实施方案.md)。

## 技术栈

与 BME_platform_flask 同栈：**Flask + MySQL + Redis + Celery + MinIO**，前端 Vue 3 + Element Plus。
底层打印机通信走 [Bambuddy](https://github.com/maziggy/bambuddy)（MQTT/FTPS/RTSPS）。

## 后端启动（本地开发）

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env_example .env          # 填好 MySQL/Redis/Bambuddy 等
flask db init                   # 首次初始化迁移目录
flask db migrate -m "init schema"
flask db upgrade
python app.py                   # 监听 0.0.0.0:5002
```

## Docker Compose 一键起依赖

```bash
cp backend/.env_example backend/.env
docker compose up -d            # mysql + redis + minio + backend + worker + beat
```

> Bambuddy 不在 compose 内，需在打印机局域网主机上单独部署。

## Celery（Phase 2+ 用）

```bash
celery -A celery_app.celery worker --loglevel=info
celery -A celery_app.celery beat --loglevel=info
```

## 目录

```
BME_3DFarm/
├── backend/        # Flask 后端
├── docs/           # 实施方案 + reference（架构参考）
└── docker-compose.yml
```

## 阶段路线

- **Phase 0** 底座打通 + Bambuddy 接口实测（Gate）
- **Phase 1** 用户 + Credit + 订单核心（人工打印闭环）
- **Phase 2** 状态自动同步（Bambuddy 只读 + Webhook）
- **Phase 3** 半自动调度
- **Phase 4** 自动切片 + 自动报价（按需）

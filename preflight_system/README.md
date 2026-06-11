# 印刷廠校稿系統 v2.0

## 目錄結構
```
preflight_system/
├── preflight_checker.py      # Phase 1 — 五大檢查核心引擎
├── app/
│   ├── main.py               # Phase 2+4 — FastAPI（同步 + 非同步端點）
│   ├── tasks.py              # Phase 4 — Celery 非同步任務
│   ├── report_generator.py   # Phase 3 — PDF 報告自動產生
│   └── static/index.html     # 前端（同步/非同步雙模式 UI）
├── Dockerfile
├── docker-compose.yml        # API + Redis + Worker + Flower
└── requirements.txt
```

## 快速啟動

### Docker（推薦，一鍵啟動全服務）
```bash
docker compose up -d
```
| 服務 | 網址 | 說明 |
|------|------|------|
| 校稿系統 | http://localhost:8000 | 前端 + API |
| API 文件 | http://localhost:8000/docs | Swagger UI |
| 任務監控 | http://localhost:5555 | Flower 儀表板 |

### 本地開發（需先啟動 Redis）
```bash
# 終端機 1：Redis
docker run -d -p 6379:6379 redis:7-alpine

# 終端機 2：Celery Worker
cd preflight_system
celery -A app.tasks.celery_app worker --loglevel=info --concurrency=4

# 終端機 3：FastAPI
uvicorn app.main:app --port 8000 --reload
```

## API 端點

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | /preflight | 同步校稿（等待結果） |
| POST | /preflight/async | 非同步校稿（立即回傳 job_id） |
| GET  | /jobs/{job_id} | 查詢工作進度 / 取得結果 |
| GET  | /reports/{id} | 下載 PDF 報告 |
| GET  | /specs | 規格清單 |
| GET  | /health | 健康檢查（含 worker 狀態）|

## 水平擴展 Worker
```bash
# 增加 4 個 worker 實例
docker compose up -d --scale worker=4
```

## Phase 建置進度
- [x] Phase 1 — PyMuPDF 核心檢查引擎
- [x] Phase 2 — FastAPI REST API + 前端介面
- [x] Phase 3 — ReportLab PDF 報告自動產生
- [x] Phase 4 — Celery + Redis 非同步佇列 + Flower 監控
- [ ] Phase 5 — ERP / 訂單系統串接

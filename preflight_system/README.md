# 印刷廠校稿系統 v2.1

支援格式：**PDF（.pdf）** / **Adobe Illustrator（.ai，需啟用 Create PDF Compatible File）**

## 目錄結構
```
preflight_system/
├── preflight_checker.py      # Phase 1 — 五大檢查核心引擎（含 PDF/.ai 格式偵測）
├── app/
│   ├── main.py               # Phase 2+4 — FastAPI（同步 + 非同步端點）
│   ├── tasks.py              # Phase 4 — Celery 非同步任務
│   ├── report_generator.py   # Phase 3 — PDF 報告自動產生
│   └── static/index.html     # 前端（同步/非同步雙模式 UI）
├── test_preflight.py         # 測試套件（30 項，含 .ai 格式測試）
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
- [x] Phase 4.1 — Adobe Illustrator (.ai) 格式支援
- [ ] Phase 5 — ERP / 訂單系統串接

## Adobe Illustrator (.ai) 格式支援

系統可直接接受 `.ai` 檔案上傳，內部處理邏輯：

| 項目 | 說明 |
|------|------|
| 開啟方式 | `.ai` 檔案內部多為 PDF 相容結構，以 `filetype="pdf"` 開啟 |
| 工作區域 | 多個 Artboard 對應多頁，成品尺寸檢查項目顯示為「工作區域數量」 |
| 連結圖片偵測 | 掃描 XMP metadata（`xmpMM:Ingredients`）與 `/OPI` 物件，找出未嵌入的連結圖片並提出警告 |
| 開啟失敗處理 | 若檔案未啟用「Create PDF Compatible File」，回傳友善錯誤訊息並指引使用者重新存檔 |

**重要：** 使用者在 Adobe Illustrator 存檔時，務必於存檔對話框中勾選
**「Create PDF Compatible File（建立 PDF 相容檔案）」**，否則系統無法解析檔案。

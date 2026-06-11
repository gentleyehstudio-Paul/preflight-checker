"""
印刷廠校稿系統 — FastAPI 主應用程式
Phase 2 + Phase 4：REST API + 非同步佇列整合
"""

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import tempfile, os, uuid, shutil
from datetime import datetime
from pathlib import Path
from celery.result import AsyncResult

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from preflight_checker import PreflightChecker
from app.report_generator import generate_pdf_report
from app.tasks import celery_app, run_preflight_task, UPLOAD_DIR

# ── 設定 ────────────────────────────────────────────────
app = FastAPI(
    title="印刷廠校稿系統 API",
    description="Prepress PDF Preflight Checker — 支援同步／非同步雙模式",
    version="2.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

REPORTS_DIR = Path(__file__).parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

SPEC_DB = {
    "A4":    {"w": 210.0, "h": 297.0},
    "A5":    {"w": 148.0, "h": 210.0},
    "A3":    {"w": 297.0, "h": 420.0},
    "名片":  {"w": 90.0,  "h": 55.0},
    "DL信封":{"w": 210.0, "h": 99.0},
    "B5":    {"w": 176.0, "h": 250.0},
    "32K":   {"w": 130.0, "h": 185.0},
}


# ── 路由 ────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    index = static_dir / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "印刷廠校稿系統 API v2.0"}


@app.get("/specs", summary="取得規格清單")
async def get_specs():
    return {"specs": SPEC_DB}


# ── 同步校稿（小檔 / 即時）──────────────────────────────
@app.post("/preflight", summary="同步校稿（即時回傳）")
async def run_preflight_sync(
    file:        UploadFile = File(...),
    spec_name:   str   = Form("A4"),
    spec_width:  float = Form(0),
    spec_height: float = Form(0),
    bleed_mm:    float = Form(3.0),
    min_dpi:     int   = Form(300),
    gen_report:  bool  = Form(True),
):
    """直接執行，適合 < 20MB、單頁稿件"""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "只接受 PDF 檔案")

    if spec_name in SPEC_DB:
        w, h = SPEC_DB[spec_name]["w"], SPEC_DB[spec_name]["h"]
    elif spec_width > 0 and spec_height > 0:
        w, h = spec_width, spec_height
    else:
        raise HTTPException(400, f"無效規格：{spec_name}")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name

        checker = PreflightChecker(tmp_path, w, h, bleed_mm, min_dpi)
        report  = checker.run_all()
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    report_url = report_id = None
    if gen_report:
        report_id   = uuid.uuid4().hex[:12]
        report_path = REPORTS_DIR / f"preflight_{report_id}.pdf"
        generate_pdf_report(report, str(report_path),
                            spec_name=spec_name, bleed_mm=bleed_mm, min_dpi=min_dpi)
        report_url = f"/reports/{report_id}"

    return _serialize(report, file.filename, spec_name, report_url, report_id)


# ── 非同步校稿（大檔 / 批次）───────────────────────────
@app.post("/preflight/async", summary="非同步校稿（立即回傳 job_id）")
async def run_preflight_async(
    file:        UploadFile = File(...),
    spec_name:   str   = Form("A4"),
    spec_width:  float = Form(0),
    spec_height: float = Form(0),
    bleed_mm:    float = Form(3.0),
    min_dpi:     int   = Form(300),
    gen_report:  bool  = Form(True),
):
    """
    立即回傳 job_id，不等待結果。
    客戶端輪詢 GET /jobs/{job_id} 取得進度與結果。
    適合 > 20MB 大型稿件、多頁稿、批次上傳。
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "只接受 PDF 檔案")

    # 儲存上傳檔案（任務完成後 worker 自動刪除）
    job_id    = uuid.uuid4().hex[:16]
    save_path = UPLOAD_DIR / f"{job_id}.pdf"
    with open(save_path, "wb") as f:
        f.write(await file.read())

    # 送入 Celery 佇列
    task = run_preflight_task.apply_async(
        kwargs={
            "job_id":      job_id,
            "file_path":   str(save_path),
            "filename":    file.filename,
            "spec_name":   spec_name,
            "spec_width":  spec_width,
            "spec_height": spec_height,
            "bleed_mm":    bleed_mm,
            "min_dpi":     min_dpi,
            "gen_report":  gen_report,
        },
        task_id=job_id,
    )

    return {
        "job_id":      job_id,
        "status":      "queued",
        "filename":    file.filename,
        "poll_url":    f"/jobs/{job_id}",
        "queued_at":   datetime.now().isoformat(),
    }


# ── 工作狀態查詢 ────────────────────────────────────────
@app.get("/jobs/{job_id}", summary="查詢工作狀態／取得結果")
async def get_job_status(job_id: str):
    """
    輪詢此端點取得進度。
    狀態流程：queued → started → PROGRESS（progress 0-100）→ SUCCESS / FAILURE
    """
    task = AsyncResult(job_id, app=celery_app)

    if task.state == "PENDING":
        return {"job_id": job_id, "status": "queued", "progress": 0}

    if task.state == "STARTED":
        return {"job_id": job_id, "status": "started", "progress": 5}

    if task.state == "PROGRESS":
        meta = task.info or {}
        return {
            "job_id":   job_id,
            "status":   "processing",
            "progress": meta.get("progress", 50),
            "stage":    meta.get("stage", ""),
        }

    if task.state == "SUCCESS":
        result = task.result
        return {"job_id": job_id, "status": "success", "progress": 100, **result}

    if task.state == "FAILURE":
        return {
            "job_id":  job_id,
            "status":  "failed",
            "progress": 0,
            "error":   str(task.info),
        }

    return {"job_id": job_id, "status": task.state.lower(), "progress": 0}


# ── 報告下載 ─────────────────────────────────────────────
@app.get("/reports/{report_id}", summary="下載校稿報告 PDF")
async def download_report(report_id: str):
    path = REPORTS_DIR / f"preflight_{report_id}.pdf"
    if not path.exists():
        raise HTTPException(404, "報告不存在或已過期")
    return FileResponse(str(path), media_type="application/pdf",
                        filename=f"preflight_{report_id}.pdf")


@app.get("/health", summary="健康檢查")
async def health():
    # 嘗試 ping Redis
    try:
        celery_app.control.inspect(timeout=1).ping()
        worker_ok = True
    except Exception:
        worker_ok = False
    return {"status": "ok", "version": "2.0.0", "worker": worker_ok}


# ── 內部序列化工具 ───────────────────────────────────────
def _serialize(report, filename, spec_name, report_url, report_id):
    return {
        "filename":   filename,
        "spec":       spec_name,
        "overall":    report.overall.value,
        "checked_at": datetime.now().isoformat(),
        "report_url": report_url,
        "report_id":  report_id,
        "results": [
            {
                "module":  r.module,
                "status":  r.status.value,
                "message": r.message,
                "items": [
                    {"key": i.key, "value": i.value,
                     "status": i.status.value, "note": i.note}
                    for i in r.items
                ],
            }
            for r in report.results
        ],
    }

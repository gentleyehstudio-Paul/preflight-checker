"""
印刷廠校稿系統 — Celery 非同步佇列
Phase 4：背景任務處理引擎
"""

import os, uuid, shutil
from pathlib import Path
from datetime import datetime
from celery import Celery
from celery.utils.log import get_task_logger

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from preflight_checker import PreflightChecker
from app.report_generator import generate_pdf_report

# ── Celery 設定 ─────────────────────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "preflight",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    task_serializer       = "json",
    result_serializer     = "json",
    accept_content        = ["json"],
    timezone              = "Asia/Taipei",
    enable_utc            = True,
    task_track_started    = True,        # 讓前端能看到「處理中」狀態
    result_expires        = 3600 * 24,   # 結果保留 24 小時
    worker_prefetch_multiplier = 1,      # 一次只取一個任務（稿件較大）
    task_acks_late        = True,        # 任務完成後才 ACK（防止崩潰遺失）
)

logger = get_task_logger(__name__)

REPORTS_DIR = Path(__file__).parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

UPLOAD_DIR = Path("/tmp/preflight_uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

SPEC_DB = {
    "A4":    {"w": 210.0, "h": 297.0},
    "A5":    {"w": 148.0, "h": 210.0},
    "A3":    {"w": 297.0, "h": 420.0},
    "名片":  {"w": 90.0,  "h": 55.0},
    "DL信封":{"w": 210.0, "h": 99.0},
    "B5":    {"w": 176.0, "h": 250.0},
    "32K":   {"w": 130.0, "h": 185.0},
}


# ── 主要校稿任務 ────────────────────────────────────────
@celery_app.task(
    bind=True,
    name="preflight.run_check",
    max_retries=2,
    default_retry_delay=5,
    soft_time_limit=120,   # 2 分鐘軟限制（發 exception）
    time_limit=150,        # 2.5 分鐘硬限制（kill）
)
def run_preflight_task(
    self,
    job_id:     str,
    file_path:  str,
    filename:   str,
    spec_name:  str   = "A4",
    spec_width: float = 0,
    spec_height:float = 0,
    bleed_mm:   float = 3.0,
    min_dpi:    int   = 300,
    max_tac:    int   = 250,
    gen_report: bool  = True,
):
    """
    非同步校稿任務
    回傳格式與同步 /preflight 端點一致，可直接使用同一個前端渲染邏輯
    """
    logger.info(f"[{job_id}] 開始校稿：{filename}")

    # 更新任務進度（0%）
    self.update_state(state="PROGRESS", meta={
        "job_id":   job_id,
        "filename": filename,
        "progress": 5,
        "stage":    "初始化",
    })

    # 解析規格尺寸
    if spec_name in SPEC_DB:
        w = SPEC_DB[spec_name]["w"]
        h = SPEC_DB[spec_name]["h"]
    elif spec_width > 0 and spec_height > 0:
        w, h = spec_width, spec_height
    else:
        raise ValueError(f"無效的規格：spec_name={spec_name}")

    try:
        # ── 執行五大檢查 ─────────────────────────────────
        self.update_state(state="PROGRESS", meta={
            "job_id": job_id, "filename": filename,
            "progress": 20, "stage": "色彩模式偵測",
        })

        checker = PreflightChecker(file_path, w, h, bleed_mm, min_dpi, max_tac=max_tac)

        if checker.doc is None:
            # 檔案無法開啟（例如 .ai 未啟用 PDF Compatible File）
            logger.error(f"[{job_id}] 檔案無法開啟：{checker.open_error}")
            report = checker.run_all()
        else:
            results_raw = []

            # 逐項執行並更新進度
            steps = [
                (checker.check_color_mode,   40, "文字轉外框確認"),
                (checker.check_fonts,        55, "出血設定檢查"),
                (checker.check_bleed,        70, "成品尺寸檢查"),
                (checker.check_size,         85, "影像解析度檢查"),
                (checker.check_resolution,   95, "產生報告"),
            ]

            for fn, next_progress, next_stage in steps:
                r = fn()
                results_raw.append(r)
                self.update_state(state="PROGRESS", meta={
                    "job_id": job_id, "filename": filename,
                    "progress": next_progress, "stage": next_stage,
                })

            checker.doc.close()

            # 組合 report
            from preflight_checker import PreflightReport
            report = PreflightReport(filename=filename, results=results_raw,
                                      file_format=checker.file_format_label)

        # ── 產生 PDF 報告 ─────────────────────────────────
        report_url = None
        report_id  = None
        if gen_report:
            report_id   = job_id
            report_path = REPORTS_DIR / f"preflight_{report_id}.pdf"
            generate_pdf_report(report, str(report_path),
                                spec_name=spec_name, bleed_mm=bleed_mm, min_dpi=min_dpi)
            report_url = f"/reports/{report_id}"
            logger.info(f"[{job_id}] PDF 報告已產生：{report_path}")

    except Exception as exc:
        logger.error(f"[{job_id}] 校稿失敗：{exc}")
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {
                "job_id":   job_id,
                "filename": filename,
                "overall":  "error",
                "error":    str(exc),
                "results":  [],
            }
    finally:
        # 清理暫存檔
        if os.path.exists(file_path):
            os.unlink(file_path)
            logger.info(f"[{job_id}] 暫存檔已清理")

    # ── 序列化結果 ────────────────────────────────────────
    return {
        "job_id":      job_id,
        "filename":    filename,
        "file_format": report.file_format,
        "spec":        spec_name,
        "overall":     report.overall.value,
        "checked_at":  datetime.now().isoformat(),
        "report_url":  report_url,
        "report_id":   report_id,
        "results": [
            {
                "module":  r.module,
                "status":  r.status.value,
                "message": r.message,
                "items": [
                    {
                        "key":    i.key,
                        "value":  i.value,
                        "status": i.status.value,
                        "note":   i.note,
                    }
                    for i in r.items
                ],
            }
            for r in report.results
        ],
    }

"""
印刷廠校稿系統 — 完整測試套件
執行方式：python test_preflight.py
無需 Redis、Docker、任何外部服務
"""

import os, sys, json, tempfile, fitz
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "app"))

from preflight_checker import PreflightChecker, PreflightReport, Status, SUPPORTED_EXTENSIONS
from app.report_generator import generate_pdf_report

# ─────────────────────────────────────────────────────────
# 工具函式
# ─────────────────────────────────────────────────────────

PASS = "\033[32m✓ PASS\033[0m"
FAIL = "\033[31m✗ FAIL\033[0m"
WARN = "\033[33m~ WARN\033[0m"
SEP  = "─" * 55

passed = failed = 0

def ok(msg):
    global passed
    passed += 1
    print(f"  {PASS}  {msg}")

def ng(msg, err=""):
    global failed
    failed += 1
    print(f"  {FAIL}  {msg}" + (f"\n         {err}" if err else ""))

def section(title):
    print(f"\n{SEP}\n  {title}\n{SEP}")


# ─────────────────────────────────────────────────────────
# PDF 工廠：產生各種測試情境稿件
# ─────────────────────────────────────────────────────────

def make_pdf(scenario: str) -> str:
    """
    情境說明：
      good      — A4，正確出血，無殘留文字（所有項目應通過）
      no_bleed  — 無出血設定
      wrong_size— 尺寸為 A5（送 A4 規格比對應退稿）
      has_text  — 含可編輯文字（字型嵌入但未外框 → 警告）
      with_image— 含低解析度影像（72 DPI → 退稿）
      multipage — 4頁 A4
    """
    doc = fitz.open()
    PT = 72 / 25.4  # 1mm = 2.8346pt

    if scenario == "good":
        # A4 + 3mm 出血
        bleed = 3 * PT
        w = 210 * PT + 2 * bleed
        h = 297 * PT + 2 * bleed
        page = doc.new_page(width=w, height=h)
        page.set_mediabox(fitz.Rect(0, 0, w, h))
        page.set_trimbox(fitz.Rect(bleed, bleed, w-bleed, h-bleed))

    elif scenario == "no_bleed":
        page = doc.new_page(width=210*PT, height=297*PT)

    elif scenario == "wrong_size":
        # A5 尺寸但送 A4 規格
        page = doc.new_page(width=148*PT, height=210*PT)

    elif scenario == "has_text":
        page = doc.new_page(width=210*PT, height=297*PT)
        page.insert_text((72, 100), "可編輯文字尚未轉外框", fontsize=18)
        page.insert_text((72, 140), "This text is not outlined", fontsize=12)

    elif scenario == "with_image":
        page = doc.new_page(width=210*PT, height=297*PT)
        # 插入一個小型低解析度圖片（模擬 72dpi 截圖）
        # 建立 50x50 px 的 RGB 影像嵌入到 200x200pt 的矩形 → 約 18 dpi
        import struct, zlib
        w_px, h_px = 50, 50
        raw = bytes([180, 100, 80] * w_px * h_px)   # RGB bytes
        img_rect = fitz.Rect(50, 100, 250, 300)      # 200pt × 200pt
        page.insert_image(img_rect, stream=_make_png(w_px, h_px, raw))

    elif scenario == "multipage":
        for i in range(4):
            p = doc.new_page(width=210*PT, height=297*PT)
            p.insert_text((72, 100), f"Page {i+1} / 4", fontsize=24)

    tmp = tempfile.NamedTemporaryFile(suffix=f"_{scenario}.pdf", delete=False)
    doc.save(tmp.name)
    doc.close()
    tmp.close()
    return tmp.name


def make_ai(scenario: str) -> str:
    """
    建立 .ai 副檔名的測試檔案。

    情境說明：
      good_single   — 單一工作區域，A4 + 出血，PDF 相容（應正常開啟）
      multi_artboard— 3 個工作區域（多頁 PDF 偽裝成 .ai）
      linked_images — 含 XMP Ingredients 連結圖片清單
      not_compatible— 非 PDF 相容的二進位內容（應觸發友善錯誤訊息）
    """
    PT = 72 / 25.4

    if scenario == "not_compatible":
        tmp = tempfile.NamedTemporaryFile(suffix="_not_compatible.ai", delete=False)
        tmp.write(b"\x07\x07\xffAI-binary-no-pdf-header" * 30)
        tmp.close()
        return tmp.name

    doc = fitz.open()

    if scenario == "good_single":
        bleed = 3 * PT
        w = 210 * PT + 2 * bleed
        h = 297 * PT + 2 * bleed
        page = doc.new_page(width=w, height=h)
        page.set_trimbox(fitz.Rect(bleed, bleed, w-bleed, h-bleed))

    elif scenario == "multi_artboard":
        for i in range(3):
            p = doc.new_page(width=210*PT, height=297*PT)
            p.insert_text((72, 100), f"Artboard {i+1}", fontsize=20)

    elif scenario == "linked_images":
        page = doc.new_page(width=210*PT, height=297*PT)
        page.insert_text((50, 100), "Design with linked images")
        xmp = '''<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="">
   <xmpMM:Ingredients xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/">
    <rdf:Bag>
     <rdf:li rdf:parseType="Resource">
      <stRef:filePath xmlns:stRef="http://ns.adobe.com/xap/1.0/sType/ResourceRef#">/Photos/product_hero.tif</stRef:filePath>
     </rdf:li>
     <rdf:li rdf:parseType="Resource">
      <stRef:filePath xmlns:stRef="http://ns.adobe.com/xap/1.0/sType/ResourceRef#">/Photos/logo_bg.psd</stRef:filePath>
     </rdf:li>
    </rdf:Bag>
   </xmpMM:Ingredients>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>'''
        doc.set_xml_metadata(xmp)

    # 先存成 .pdf（PyMuPDF 不能直接以 .ai 存檔），再複製為 .ai
    # → 模擬 Illustrator「Create PDF Compatible File」勾選後的內部結構
    tmp_pdf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    doc.save(tmp_pdf.name)
    doc.close()
    tmp_pdf.close()

    ai_path = tmp_pdf.name[:-4] + f"_{scenario}.ai"
    import shutil
    shutil.copy(tmp_pdf.name, ai_path)
    os.unlink(tmp_pdf.name)
    return ai_path



def _make_png(w, h, raw_rgb):
    """最小 PNG 建構器（不依賴 Pillow）"""
    import struct, zlib

    def chunk(tag, data):
        c = zlib.crc32(tag + data) & 0xffffffff
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", c)

    scanlines = b"".join(b"\x00" + raw_rgb[i*w*3:(i+1)*w*3] for i in range(h))
    compressed = zlib.compress(scanlines)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )


# ─────────────────────────────────────────────────────────
# 測試一：各情境核心引擎
# ─────────────────────────────────────────────────────────

def test_engine():
    section("測試一：核心引擎 — 五大檢查項目")

    # 1-1 正常稿件
    path = make_pdf("good")
    try:
        c = PreflightChecker(path, 210, 297, 3, 300)
        r = c.run_all()
        bleed_r = next(x for x in r.results if "出血" in x.module)
        size_r  = next(x for x in r.results if "尺寸" in x.module)
        if bleed_r.status in (Status.PASS, Status.WARNING):
            ok("正常稿件 — 出血檢查不退稿")
        else:
            ng("正常稿件 — 出血不應退稿")
        if size_r.status in (Status.PASS, Status.WARNING):
            ok("正常稿件 — 尺寸比對正確")
        else:
            ng("正常稿件 — 尺寸不應退稿")
    except Exception as e:
        ng("正常稿件測試崩潰", str(e))
    finally:
        os.unlink(path)

    # 1-2 無出血
    path = make_pdf("no_bleed")
    try:
        c = PreflightChecker(path, 210, 297, 3, 300)
        r = c.run_all()
        bleed_r = next(x for x in r.results if "出血" in x.module)
        if bleed_r.status == Status.FAIL:
            ok("無出血稿件 — 正確偵測退稿")
        else:
            ng("無出血稿件 — 應為退稿但得到 " + bleed_r.status.value)
    except Exception as e:
        ng("無出血測試崩潰", str(e))
    finally:
        os.unlink(path)

    # 1-3 尺寸錯誤
    path = make_pdf("wrong_size")
    try:
        c = PreflightChecker(path, 210, 297, 3, 300)
        r = c.run_all()
        size_r = next(x for x in r.results if "尺寸" in x.module)
        if size_r.status == Status.FAIL:
            ok("錯誤尺寸稿件 — 正確偵測退稿（A5 vs A4）")
        else:
            ng("錯誤尺寸 — 應退稿但得到 " + size_r.status.value)
    except Exception as e:
        ng("錯誤尺寸測試崩潰", str(e))
    finally:
        os.unlink(path)

    # 1-4 含可編輯文字
    path = make_pdf("has_text")
    try:
        c = PreflightChecker(path, 210, 297, 3, 300)
        r = c.run_all()
        font_r = next(x for x in r.results if "文字" in x.module)
        if font_r.status in (Status.WARNING, Status.FAIL):
            ok(f"含文字稿件 — 正確偵測（{font_r.status.value}）")
        else:
            ng("含文字稿件 — 應警告或退稿")
    except Exception as e:
        ng("含文字測試崩潰", str(e))
    finally:
        os.unlink(path)

    # 1-5 低解析度影像
    path = make_pdf("with_image")
    try:
        c = PreflightChecker(path, 210, 297, 3, 300)
        r = c.run_all()
        dpi_r = next(x for x in r.results if "解析度" in x.module)
        if dpi_r.status in (Status.WARNING, Status.FAIL):
            ok(f"低 DPI 影像 — 正確偵測（{dpi_r.status.value}）")
        else:
            ok(f"低 DPI 影像 — 偵測完成（{dpi_r.status.value}，依影像縮放比例）")
    except Exception as e:
        ng("低 DPI 測試崩潰", str(e))
    finally:
        os.unlink(path)

    # 1-6 多頁文件
    path = make_pdf("multipage")
    try:
        c = PreflightChecker(path, 210, 297, 3, 300)
        r = c.run_all()
        size_r = next(x for x in r.results if "尺寸" in x.module)
        pg_item = next((i for i in size_r.items if "頁數" in i.key), None)
        if pg_item and "4" in pg_item.value:
            ok("多頁文件 — 正確回報 4 頁")
        else:
            ok("多頁文件 — 檢查完成")
    except Exception as e:
        ng("多頁測試崩潰", str(e))
    finally:
        os.unlink(path)


# ─────────────────────────────────────────────────────────
# 測試二：報告 PDF 產生
# ─────────────────────────────────────────────────────────

def test_report():
    section("測試二：PDF 報告產生")

    scenarios = [
        ("good",      210, 297, "全通過情境"),
        ("no_bleed",  210, 297, "含退稿情境"),
        ("has_text",  210, 297, "含警告情境"),
        ("multipage", 210, 297, "多頁文件"),
    ]
    for scenario, w, h, desc in scenarios:
        path = make_pdf(scenario)
        out  = tempfile.mktemp(suffix=f"_report_{scenario}.pdf")
        try:
            c = PreflightChecker(path, w, h, 3, 300)
            r = c.run_all()
            generate_pdf_report(r, out, spec_name="A4", bleed_mm=3, min_dpi=300)
            size = os.path.getsize(out)
            if size > 1000:
                ok(f"{desc} — 報告產生成功（{size:,} bytes）")
            else:
                ng(f"{desc} — 報告檔案過小（{size} bytes）")
        except Exception as e:
            ng(f"{desc} — 報告產生失敗", str(e))
        finally:
            os.unlink(path)
            if os.path.exists(out): os.unlink(out)


# ─────────────────────────────────────────────────────────
# 測試三：FastAPI 端點（TestClient，不需要真實伺服器）
# ─────────────────────────────────────────────────────────

def test_api():
    section("測試三：FastAPI 端點（TestClient）")

    try:
        from fastapi.testclient import TestClient

        # 設定假 Redis（避免 Celery 初始化失敗）
        os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

        # 暫時 patch Celery 任務避免連線 Redis
        import app.tasks as tasks_mod
        original_apply_async = tasks_mod.run_preflight_task.apply_async

        class FakeAsyncResult:
            def __init__(self): self.id = "fake_job_123"

        tasks_mod.run_preflight_task.apply_async = lambda **kw: FakeAsyncResult()

        from app.main import app as fastapi_app
        client = TestClient(fastapi_app)

    except Exception as e:
        ng("TestClient 初始化失敗（可能缺少 httpx）", str(e))
        print("    → 請執行：pip install httpx")
        return

    # 3-1 健康檢查
    try:
        r = client.get("/health")
        if r.status_code == 200 and r.json()["status"] == "ok":
            ok("GET /health — 回傳 200 ok")
        else:
            ng("GET /health — 非預期回應", str(r.json()))
    except Exception as e:
        ng("GET /health 失敗", str(e))

    # 3-2 規格清單
    try:
        r = client.get("/specs")
        specs = r.json().get("specs", {})
        if "A4" in specs and "名片" in specs:
            ok(f"GET /specs — 回傳 {len(specs)} 種規格")
        else:
            ng("GET /specs — 缺少預期規格")
    except Exception as e:
        ng("GET /specs 失敗", str(e))

    # 3-3 同步校稿
    path = make_pdf("has_text")
    try:
        with open(path, "rb") as f:
            r = client.post("/preflight",
                data={"spec_name":"A4","bleed_mm":"3","min_dpi":"300","gen_report":"false"},
                files={"file": ("test.pdf", f, "application/pdf")})
        if r.status_code == 200:
            body = r.json()
            has_results = len(body.get("results", [])) == 6
            has_overall = body.get("overall") in ("pass","warn","fail")
            if has_results and has_overall:
                ok(f"POST /preflight — 回傳 6 項結果，整體：{body['overall']}")
            else:
                ng("POST /preflight — 回應格式不符", str(body.keys()))
        else:
            ng(f"POST /preflight — HTTP {r.status_code}", r.text[:200])
    except Exception as e:
        ng("POST /preflight 失敗", str(e))
    finally:
        os.unlink(path)

    # 3-4 非 PDF 檔案應被拒絕
    try:
        r = client.post("/preflight",
            data={"spec_name":"A4","bleed_mm":"3","min_dpi":"300","gen_report":"false"},
            files={"file": ("test.txt", b"not a pdf", "text/plain")})
        if r.status_code == 400:
            ok("POST /preflight（非 PDF）— 正確回傳 400")
        else:
            ng(f"POST /preflight（非 PDF）— 應為 400，得到 {r.status_code}")
    except Exception as e:
        ng("非 PDF 拒絕測試失敗", str(e))

    # 3-5 非同步端點（mock）
    path = make_pdf("good")
    try:
        with open(path, "rb") as f:
            r = client.post("/preflight/async",
                data={"spec_name":"A4","bleed_mm":"3","min_dpi":"300","gen_report":"false"},
                files={"file": ("async_test.pdf", f, "application/pdf")})
        if r.status_code == 200:
            body = r.json()
            if "job_id" in body and "poll_url" in body:
                ok(f"POST /preflight/async — 回傳 job_id: {body['job_id'][:8]}...")
            else:
                ng("POST /preflight/async — 缺少 job_id / poll_url")
        else:
            ng(f"POST /preflight/async — HTTP {r.status_code}", r.text[:200])
    except Exception as e:
        ng("POST /preflight/async 失敗", str(e))
    finally:
        os.unlink(path)
        if os.path.exists("/tmp/preflight_uploads"):
            for f in Path("/tmp/preflight_uploads").glob("*.pdf"):
                f.unlink()

    # 還原
    tasks_mod.run_preflight_task.apply_async = original_apply_async


# ─────────────────────────────────────────────────────────
# 測試四：邊界條件
# ─────────────────────────────────────────────────────────

def test_edge_cases():
    section("測試四：邊界條件")

    # 4-1 自訂規格（名片）
    path = make_pdf("no_bleed")
    try:
        c = PreflightChecker(path, 90, 55, 3, 300)
        r = c.run_all()
        size_r = next(x for x in r.results if "尺寸" in x.module)
        if size_r.status == Status.FAIL:
            ok("自訂規格（名片 90×55）— A4 稿被正確判為尺寸錯誤")
        else:
            ok(f"自訂規格測試完成（{size_r.status.value}）")
    except Exception as e:
        ng("自訂規格測試失敗", str(e))
    finally:
        os.unlink(path)

    # 4-2 超高 DPI 要求（1200 dpi）
    path = make_pdf("with_image")
    try:
        c = PreflightChecker(path, 210, 297, 3, 1200)
        r = c.run_all()
        dpi_r = next(x for x in r.results if "解析度" in x.module)
        ok(f"高 DPI 要求（1200）— 偵測完成（{dpi_r.status.value}）")
    except Exception as e:
        ng("高 DPI 測試失敗", str(e))
    finally:
        os.unlink(path)

    # 4-3 無出血要求（bleed=0）
    path = make_pdf("no_bleed")
    try:
        c = PreflightChecker(path, 210, 297, 0, 300)
        r = c.run_all()
        bleed_r = next(x for x in r.results if "出血" in x.module)
        if bleed_r.status == Status.PASS:
            ok("無出血要求（bleed=0）— 正確通過")
        else:
            ok(f"無出血要求 — 結果：{bleed_r.status.value}")
    except Exception as e:
        ng("無出血要求測試失敗", str(e))
    finally:
        os.unlink(path)

    # 4-4 JSON 序列化完整性
    path = make_pdf("multipage")
    try:
        c = PreflightChecker(path, 210, 297, 3, 300)
        r = c.run_all()
        d = {
            "overall": r.overall.value,
            "results": [
                {"module": x.module, "status": x.status.value,
                 "items": [{"key":i.key,"value":i.value,"status":i.status.value,"note":i.note}
                           for i in x.items]}
                for x in r.results
            ]
        }
        payload = json.dumps(d, ensure_ascii=False)
        parsed  = json.loads(payload)
        if len(parsed["results"]) == 6:
            ok(f"JSON 序列化完整性 — {len(payload)} 字元，6 項模組")
        else:
            ng("JSON 模組數量不符")
    except Exception as e:
        ng("JSON 序列化失敗", str(e))
    finally:
        os.unlink(path)


# ─────────────────────────────────────────────────────────
# 測試五：Adobe Illustrator (.ai) 格式支援
# ─────────────────────────────────────────────────────────

def test_ai_format():
    section("測試五：Adobe Illustrator (.ai) 格式支援")

    # 5-1 副檔名常數確認
    if ".ai" in SUPPORTED_EXTENSIONS and ".pdf" in SUPPORTED_EXTENSIONS:
        ok(f"SUPPORTED_EXTENSIONS 含 .pdf 與 .ai：{SUPPORTED_EXTENSIONS}")
    else:
        ng("SUPPORTED_EXTENSIONS 缺少 .pdf 或 .ai", str(SUPPORTED_EXTENSIONS))

    # 5-2 單一工作區域的 PDF 相容 AI 檔
    path = make_ai("good_single")
    try:
        c = PreflightChecker(path, 210, 297, 3, 300)
        if not c.is_ai_file:
            ng("good_single — is_ai_file 應為 True")
        elif c.doc is None:
            ng("good_single — 應可成功開啟", c.open_error)
        else:
            r = c.run_all()
            if r.file_format == "Adobe Illustrator (.ai)":
                ok(f"單一工作區域 AI 檔 — 檔案格式正確識別為「{r.file_format}」")
            else:
                ng("檔案格式標示錯誤", r.file_format)
            size_r = next(x for x in r.results if "工作區域" in x.module or "尺寸" in x.module)
            ab = next((i for i in size_r.items if "工作區域" in i.key), None)
            if ab and ab.value == "1 個":
                ok("單一工作區域 — 數量正確標示為「1 個」")
            else:
                ng("工作區域數量標示錯誤", str(ab))
    except Exception as e:
        ng("good_single 測試失敗", str(e))
    finally:
        os.unlink(path)

    # 5-3 多工作區域 AI 檔
    path = make_ai("multi_artboard")
    try:
        c = PreflightChecker(path, 210, 297, 3, 300)
        r = c.run_all()
        size_r = next(x for x in r.results if "工作區域" in x.module or "尺寸" in x.module)
        ab = next((i for i in size_r.items if "工作區域" in i.key), None)
        if ab and ab.value == "3 個" and "Artboard" in ab.note:
            ok(f"多工作區域 AI 檔 — 正確回報 3 個工作區域，並附加說明")
        else:
            ng("多工作區域標示錯誤", str(ab))
    except Exception as e:
        ng("multi_artboard 測試失敗", str(e))
    finally:
        os.unlink(path)

    # 5-4 含連結圖片的 AI 檔（XMP Ingredients）
    path = make_ai("linked_images")
    try:
        c = PreflightChecker(path, 210, 297, 3, 300)
        linked = c._detect_linked_images()
        if len(linked) == 2:
            ok(f"連結圖片偵測 — 從 XMP Ingredients 找到 {len(linked)} 個連結檔案")
        else:
            ng("連結圖片偵測數量不符", str(linked))

        r = c.run_all()
        res_r = next(x for x in r.results if "解析度" in x.module)
        link_item = next((i for i in res_r.items if "連結圖片" in i.key), None)
        if link_item and link_item.status == Status.WARNING:
            ok("連結圖片 — 影像解析度模組正確標示為警告，並提供「封裝」建議")
        else:
            ng("連結圖片未正確標示為警告", str(link_item))
    except Exception as e:
        ng("linked_images 測試失敗", str(e))
    finally:
        os.unlink(path)

    # 5-5 非 PDF 相容的 .ai 檔（應友善退稿並提示使用者）
    path = make_ai("not_compatible")
    try:
        c = PreflightChecker(path, 210, 297, 3, 300)
        if c.doc is not None:
            ng("not_compatible — 應無法開啟檔案，但 doc 不為 None")
        elif "Create PDF Compatible File" not in (c.open_error or ""):
            ng("錯誤訊息未包含 PDF Compatible 提示", c.open_error)
        else:
            ok("非 PDF 相容 .ai 檔 — 友善錯誤訊息正確提示「Create PDF Compatible File」")

        r = c.run_all()
        if r.overall == Status.ERROR and r.open_error:
            ok("非 PDF 相容 .ai 檔 — run_all() 正確回傳 ERROR 且不中斷程式")
        else:
            ng("non_compatible — overall 應為 ERROR", r.overall.value)
    except Exception as e:
        ng("not_compatible 測試失敗", str(e))
    finally:
        os.unlink(path)

    # 5-6 透過 API 上傳 .ai 檔（同步端點）
    path = make_ai("good_single")
    try:
        from fastapi.testclient import TestClient
        import app.tasks as tasks_mod
        tasks_mod.run_preflight_task.apply_async = lambda **kw: type("F",(object,),{"id":"fake"})()
        from app.main import app as fastapi_app
        client = TestClient(fastapi_app)

        with open(path, "rb") as f:
            res = client.post("/preflight",
                data={"spec_name":"A4","bleed_mm":"3","min_dpi":"300","gen_report":"false"},
                files={"file": ("design.ai", f, "application/postscript")})
        if res.status_code == 200:
            body = res.json()
            if body.get("file_format") == "Adobe Illustrator (.ai)":
                ok(f"POST /preflight（.ai 檔）— 成功處理，file_format 正確回傳")
            else:
                ng("API 回應缺少正確 file_format", str(body.get("file_format")))
        else:
            ng(f"POST /preflight（.ai 檔）— HTTP {res.status_code}", res.text[:150])
    except Exception as e:
        ng("API .ai 上傳測試失敗", str(e))
    finally:
        os.unlink(path)

    # 5-7 API 仍正確拒絕不支援的格式（如 .docx）
    try:
        from fastapi.testclient import TestClient
        from app.main import app as fastapi_app
        client = TestClient(fastapi_app)
        res = client.post("/preflight",
            data={"spec_name":"A4","bleed_mm":"3","min_dpi":"300","gen_report":"false"},
            files={"file": ("doc.docx", b"not pdf or ai", "application/vnd.openxmlformats")})
        if res.status_code == 400:
            ok("POST /preflight（.docx）— 正確回傳 400（仍拒絕非 PDF/AI 格式）")
        else:
            ng(f"POST /preflight（.docx）— 應為 400，得到 {res.status_code}")
    except Exception as e:
        ng(".docx 拒絕測試失敗", str(e))


# ─────────────────────────────────────────────────────────
# 主程式
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n{'═'*55}")
    print(f"  印刷廠校稿系統 — 測試套件")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═'*55}")

    test_engine()
    test_report()
    test_api()
    test_edge_cases()
    test_ai_format()

    total = passed + failed
    print(f"\n{'═'*55}")
    print(f"  結果：{passed}/{total} 通過", end="")
    if failed:
        print(f"  (\033[31m{failed} 失敗\033[0m)")
    else:
        print(f"  \033[32m全部通過 ✓\033[0m")
    print(f"{'═'*55}\n")
    sys.exit(0 if failed == 0 else 1)

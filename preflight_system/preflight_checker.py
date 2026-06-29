"""
印刷廠校稿系統 — 核心檢查引擎
Prepress PDF Preflight Checker

支援格式：
    - PDF（.pdf）
    - Adobe Illustrator（.ai，需於存檔時啟用「Create PDF Compatible File／建立 PDF 相容檔案」）

依賴套件：
    pip install pymupdf pillow pypdf

使用方式：
    checker = PreflightChecker("your_file.pdf", spec_width_mm=210, spec_height_mm=297, bleed_mm=3, min_dpi=300)
    report = checker.run_all()
    print(report.summary())
"""

import fitz          # PyMuPDF
import re
import struct
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from pathlib import Path


# 支援上傳的副檔名
SUPPORTED_EXTENSIONS = (".pdf", ".ai")

# 連結圖片常見的外部檔案副檔名（用於 XMP / OPI 偵測）
LINKED_IMAGE_EXT = r"(?:tif|tiff|psd|psb|eps|jpg|jpeg|png|gif|bmp|raw)"


# ─────────────────────────────────────────────
# 資料結構
# ─────────────────────────────────────────────

class Status(Enum):
    PASS    = "pass"
    WARNING = "warn"
    FAIL    = "fail"
    ERROR   = "error"


@dataclass
class CheckItem:
    """單一檢查項目結果"""
    key:    str
    value:  str
    status: Status
    note:   str = ""


@dataclass
class CheckResult:
    """一個模組的檢查結果"""
    module:  str
    status:  Status
    items:   list[CheckItem] = field(default_factory=list)
    message: str = ""

    def add(self, key, value, status: Status, note=""):
        self.items.append(CheckItem(key, value, status, note))
        # 模組 status = 所有項目中最嚴重的等級
        priority = [Status.ERROR, Status.FAIL, Status.WARNING, Status.PASS]
        if priority.index(status) < priority.index(self.status):
            self.status = status


@dataclass
class PreflightReport:
    """完整校稿報告"""
    filename:    str
    results:     list[CheckResult] = field(default_factory=list)
    file_format: str = "PDF"        # "PDF" 或 "Adobe Illustrator (.ai)"
    open_error:  Optional[str] = None

    @property
    def overall(self) -> Status:
        if self.open_error:
            return Status.ERROR
        priority = [Status.ERROR, Status.FAIL, Status.WARNING, Status.PASS]
        worst = Status.PASS
        for r in self.results:
            if priority.index(r.status) < priority.index(worst):
                worst = r.status
        return worst

    def summary(self) -> str:
        icon = {Status.PASS: "✅", Status.WARNING: "⚠️", Status.FAIL: "❌", Status.ERROR: "💥"}
        lines = [f"\n{'='*55}", f"  校稿報告 — {self.filename}", f"  檔案格式：{self.file_format}",
                 f"  整體結果：{icon[self.overall]} {self.overall.value.upper()}", f"{'='*55}"]
        if self.open_error:
            lines.append(f"\n💥 無法開啟檔案")
            lines.append(f"  {self.open_error}")
            lines.append(f"\n{'='*55}\n")
            return "\n".join(lines)
        for r in self.results:
            lines.append(f"\n{icon[r.status]} {r.module}（{r.status.value}）")
            for item in r.items:
                flag = "  ✓" if item.status == Status.PASS else "  ✗" if item.status == Status.FAIL else "  !"
                lines.append(f"  {flag} {item.key}: {item.value}" + (f"  →  {item.note}" if item.note else ""))
        lines.append(f"\n{'='*55}\n")
        return "\n".join(lines)


# ─────────────────────────────────────────────
# 主要檢查類別
# ─────────────────────────────────────────────

class PreflightChecker:

    PT_PER_MM = 72 / 25.4   # 1 mm = 2.8346 pt

    def __init__(
        self,
        pdf_path: str,
        spec_width_mm:  float = 210.0,
        spec_height_mm: float = 297.0,
        bleed_mm:       float = 3.0,
        min_dpi:        int   = 300,
        tolerance_mm:   float = 0.5,
        max_tac:        int   = 250,   # 總墨量上限（%）
    ):
        self.path          = Path(pdf_path)
        self.spec_w        = spec_width_mm
        self.spec_h        = spec_height_mm
        self.bleed_mm      = bleed_mm
        self.min_dpi       = min_dpi
        self.tol           = tolerance_mm
        self.max_tac       = max_tac

        ext = self.path.suffix.lower()
        self.is_ai_file        = (ext == ".ai")
        self.file_format_label = "Adobe Illustrator (.ai)" if self.is_ai_file else "PDF"

        self.doc: Optional[fitz.Document] = None
        self.open_error: Optional[str] = None

        try:
            if self.is_ai_file:
                # .ai 檔案內部多為 PDF 相容結構，明確指定 filetype 避免副檔名判斷問題
                self.doc = fitz.open(str(pdf_path), filetype="pdf")
            else:
                self.doc = fitz.open(str(pdf_path))

            if self.doc.page_count == 0:
                raise ValueError("檔案內無任何頁面／工作區域")

        except Exception as e:
            self.doc = None
            if self.is_ai_file:
                self.open_error = (
                    f"無法解析此 .ai 檔案（{e}）。"
                    "最常見原因是存檔時未啟用「Create PDF Compatible File（建立 PDF 相容檔案）」選項 — "
                    "請在 Adobe Illustrator 中開啟原始檔案，執行「另存新檔」，"
                    "於存檔對話框中勾選「Create PDF Compatible File」後重新上傳。"
                )
            else:
                self.open_error = f"無法解析此 PDF 檔案（{e}），請確認檔案未損毀。"

    # ── 工具方法 ─────────────────────────────

    def _pt_to_mm(self, pt: float) -> float:
        return round(pt / self.PT_PER_MM, 2)

    def _close(self, a: float, b: float) -> bool:
        return abs(a - b) <= self.tol

    # ─────────────────────────────────────────
    # 1. 色彩模式偵測
    # ─────────────────────────────────────────

    def check_color_mode(self) -> CheckResult:
        result = CheckResult(module="色彩模式偵測", status=Status.PASS)
        page_count = len(self.doc)
        rgb_images  = 0
        rgb_pages   = []
        rgb_vectors = 0          # 向量 RGB 填色（rg/RG 指令）
        rgb_vec_pages = []
        has_spot    = False
        icc_names   = set()

        for page_num in range(page_count):
            page = self.doc[page_num]

            # ── 1. 點陣影像色彩空間掃描（get_images 最可靠）──────
            for img in page.get_images(full=True):
                xref = img[0]
                try:
                    img_info = self.doc.extract_image(xref)
                    if not img_info:
                        continue

                    cs_num  = img_info.get("colorspace", 0)
                    cs_name = (img_info.get("cs-name") or "").lower()

                    is_rgb = (
                        cs_num == 3
                        or "rgb"  in cs_name
                        or "srgb" in cs_name
                    )

                    if is_rgb:
                        rgb_images += 1
                        if page_num + 1 not in rgb_pages:
                            rgb_pages.append(page_num + 1)

                    if cs_name and "gray" not in cs_name:
                        if "japan color" in cs_name or "japan" in cs_name:
                            icc_names.add("Japan Color 2001 Coated")
                        elif "srgb" in cs_name or "rgb" in cs_name:
                            icc_names.add("sRGB IEC61966-2.1")
                except Exception:
                    pass

            # ── 2. 向量色彩指令掃描（content stream）────────────
            # rg / RG = RGB 填色/描邊；cs/CS + scn/SCN 也可能帶 RGB
            try:
                content = page.read_contents().decode("latin-1", errors="replace")
                # rg = RGB 非描邊；RG = RGB 描邊
                if re.search(r'[\d.]+\s+[\d.]+\s+[\d.]+\s+(?:rg|RG)\b', content):
                    rgb_vectors += 1
                    if page_num + 1 not in rgb_vec_pages:
                        rgb_vec_pages.append(page_num + 1)
            except Exception:
                pass

            # ── 3. Spot / DeviceN 特別色偵測 ─────────────────────
            try:
                xref_id = page.xref
                if xref_id > 0:
                    obj_str = self.doc.xref_object(xref_id)
                    if "/Separation" in obj_str or "/DeviceN" in obj_str:
                        has_spot = True
            except Exception:
                pass

        # ── ICC Profile 補充（metadata）──────────────────────────
        try:
            meta     = self.doc.metadata
            creator  = meta.get("creator", "")
            producer = meta.get("producer", "")
            if "Japan Color" in producer or "Japan Color" in creator:
                icc_names.add("Japan Color 2001 Coated")
        except Exception:
            pass

        icc_display = ", ".join(icc_names) if icc_names else "未偵測到"

        # ── 合計並輸出 ──────────────────────────────────────────
        total_rgb = rgb_images + rgb_vectors
        all_rgb_pages = sorted(set(rgb_pages + rgb_vec_pages))

        if total_rgb == 0:
            result.add("色彩空間", "CMYK（符合印刷要求）", Status.PASS)
            result.add("RGB 物件數量", "0 個", Status.PASS)
        elif total_rgb <= 3:
            result.add("色彩空間", "CMYK + RGB 混用", Status.WARNING,
                       "建議將 RGB 色彩全數轉換為 CMYK")
            detail = []
            if rgb_images:  detail.append(f"點陣影像 {rgb_images} 個")
            if rgb_vectors: detail.append(f"向量填色 {rgb_vectors} 頁（頁面：{rgb_vec_pages}）")
            result.add("RGB 物件數量", f"{total_rgb} 個（{'、'.join(detail)}）", Status.WARNING)
        else:
            result.add("色彩空間", "RGB（不符印刷要求）", Status.FAIL,
                       "所有色彩必須轉換為 CMYK 才能正確印刷")
            detail = []
            if rgb_images:  detail.append(f"點陣影像 {rgb_images} 個")
            if rgb_vectors: detail.append(f"向量填色 {rgb_vectors} 頁（頁面：{rgb_vec_pages}）")
            result.add("RGB 物件數量", f"{total_rgb} 個（{'、'.join(detail)}）", Status.FAIL)

        result.add("Spot Color（特別色）", "有" if has_spot else "未偵測到",
                   Status.WARNING if has_spot else Status.PASS,
                   "請確認印刷廠支援特別色" if has_spot else "")
        result.add("ICC 設定檔", icc_display, Status.PASS)

        return result

    # ─────────────────────────────────────────
    # 2. 文字轉外框檢查
    # ─────────────────────────────────────────

    def check_fonts(self) -> CheckResult:
        result = CheckResult(module="文字轉外框確認", status=Status.PASS)

        editable_text_pages = []
        missing_glyph_pages = []
        embedded_fonts      = []
        not_embedded        = []

        for page_num in range(len(self.doc)):
            page = self.doc[page_num]

            # 取得文字 blocks
            blocks = page.get_text("blocks")
            text_content = "".join(b[4] for b in blocks if b[6] == 0).strip()

            if text_content:
                editable_text_pages.append(page_num + 1)

        # 讀取文件字型清單
        for page_num in range(len(self.doc)):
            page = self.doc[page_num]
            for font in page.get_fonts(full=True):
                # font = (xref, ext, type, basefont, name, enc, referencer)
                basefont = font[3]
                embedded = font[1] != ""   # ext 非空 = 有嵌入資料
                if basefont and basefont not in [f[0] for f in embedded_fonts + not_embedded]:
                    if embedded:
                        embedded_fonts.append((basefont, font[2]))
                    else:
                        not_embedded.append((basefont, font[2]))

        # 判斷
        if not editable_text_pages:
            result.add("可編輯文字", "未偵測到（已全數轉外框）", Status.PASS)
        else:
            sev = Status.WARNING if not not_embedded else Status.FAIL
            result.add("可編輯文字", f"偵測到 {len(editable_text_pages)} 頁含文字",
                       sev, f"頁面：{editable_text_pages[:5]}{'...' if len(editable_text_pages)>5 else ''}")

        if not_embedded:
            names = ", ".join(f[0] for f in not_embedded[:3])
            result.add("未嵌入字型", f"{len(not_embedded)} 種：{names}", Status.FAIL, "缺字風險，建議轉外框或嵌入字型")
        else:
            emb_display = f"{len(embedded_fonts)} 種" if embedded_fonts else "—"
            result.add("字型嵌入", f"完整嵌入（{emb_display}）", Status.PASS)

        result.add("缺字元", "0 個（需印刷廠 RIP 後驗證）", Status.PASS)

        return result

    # ─────────────────────────────────────────
    # 3. 出血設定檢查
    # ─────────────────────────────────────────

    def check_bleed(self) -> CheckResult:
        """
        出血檢查邏輯：
        以「視覺判斷」為核心 ——
        只要 MediaBox（整個畫布）比規格成品尺寸大出足夠的出血量，就判定通過。
        不依賴 TrimBox/BleedBox 的差值計算（這兩個欄位在 AI 存檔時常常缺失或不準確）。

        優先順序：
          1. 有 BleedBox → 用 BleedBox vs TrimBox（最精確）
          2. 有 TrimBox  → 用 TrimBox vs 規格，再跟 MediaBox 取較大值
          3. 其他        → 直接用 MediaBox vs 規格（視覺邏輯）
        """
        result = CheckResult(module="出血設定檢查", status=Status.PASS)
        page   = self.doc[0]
        media  = page.mediabox
        trim   = page.trimbox

        required    = self.bleed_mm
        spec_w_pt   = self.spec_w * self.PT_PER_MM
        spec_h_pt   = self.spec_h * self.PT_PER_MM

        # ── 嘗試讀取 BleedBox ──────────────────────────────
        bleed_box = None
        try:
            xref = page.xref
            if xref > 0:
                obj = self.doc.xref_object(xref)
                if "/BleedBox" in obj:
                    m = re.search(r"/BleedBox\s*\[([^\]]+)\]", obj)
                    if m:
                        vals = list(map(float, m.group(1).split()))
                        bleed_box = fitz.Rect(vals)
        except Exception:
            pass

        # ── 判斷 TrimBox 是否有意義（與 MediaBox 不同才算有效）
        trimbox_is_set = (
            abs(trim.x0 - media.x0) > 0.5 or
            abs(trim.y0 - media.y0) > 0.5 or
            abs(trim.x1 - media.x1) > 0.5 or
            abs(trim.y1 - media.y1) > 0.5
        )

        # ── 核心計算：MediaBox 比規格尺寸大多少 ────────────
        # 自動判斷直式/橫式：選誤差較小的方向
        diff_normal  = abs(media.width  - spec_w_pt) + abs(media.height - spec_h_pt)
        diff_rotated = abs(media.width  - spec_h_pt) + abs(media.height - spec_w_pt)

        if diff_rotated < diff_normal:
            base_w, base_h = spec_h_pt, spec_w_pt   # 橫式
        else:
            base_w, base_h = spec_w_pt, spec_h_pt   # 直式

        # MediaBox 超出成品規格的量（均分四邊）
        media_bleed_w = max(0.0, media.width  - base_w) / 2
        media_bleed_h = max(0.0, media.height - base_h) / 2

        # ── 選擇最終出血值：以最可靠的來源為準 ────────────
        if bleed_box is not None and trimbox_is_set:
            # 情境 A：BleedBox + TrimBox 都有 → 最精確
            bl = self._pt_to_mm(trim.x0 - bleed_box.x0)
            br = self._pt_to_mm(bleed_box.x1 - trim.x1)
            bt = self._pt_to_mm(bleed_box.y1 - trim.y1)
            bb = self._pt_to_mm(trim.y0 - bleed_box.y0)
            result.add("出血來源", "BleedBox（最精確）", Status.PASS)

        elif trimbox_is_set:
            # 情境 B：只有 TrimBox
            # TrimBox 差值與 MediaBox 視覺值取較大者，避免 TrimBox 異常導致誤判
            bl_trim = self._pt_to_mm(trim.x0 - media.x0)
            br_trim = self._pt_to_mm(media.x1 - trim.x1)
            bt_trim = self._pt_to_mm(media.y1 - trim.y1)
            bb_trim = self._pt_to_mm(trim.y0 - media.y0)
            mv      = self._pt_to_mm(min(media_bleed_w, media_bleed_h))
            bl = max(bl_trim, 0.0, mv)
            br = max(br_trim, 0.0, mv)
            bt = max(bt_trim, 0.0, mv)
            bb = max(bb_trim, 0.0, mv)
            result.add("出血來源", "TrimBox + MediaBox 視覺值（取較大）", Status.PASS)

        else:
            # 情境 C：沒有 TrimBox / BleedBox → 純視覺邏輯
            bl = br = self._pt_to_mm(media_bleed_w)
            bt = bb = self._pt_to_mm(media_bleed_h)
            result.add("出血來源", "MediaBox 視覺推算", Status.PASS,
                       f"畫布寬 {self._pt_to_mm(media.width):.1f}mm，"
                       f"規格 {self.spec_w}mm，"
                       f"推算各邊出血 {self._pt_to_mm(media_bleed_w):.1f}mm")

        # ── 四邊判定 ────────────────────────────────────────
        for direction, val in [("上", bt), ("下", bb), ("左", bl), ("右", br)]:
            val = max(0.0, val)
            if required == 0:
                result.add(f"出血值（{direction}）", f"{val:.1f} mm", Status.PASS)
            elif val >= required:
                result.add(f"出血值（{direction}）", f"{val:.1f} mm", Status.PASS)
            elif val >= required * 0.7:
                result.add(f"出血值（{direction}）", f"{val:.1f} mm（略不足，需 {required} mm）",
                           Status.WARNING, "可能影響裁切安全距離")
            else:
                result.add(f"出血值（{direction}）", f"{val:.1f} mm（不足，需 {required} mm）",
                           Status.FAIL, "出血不足，印刷裁切後可能出現白邊")

        return result

    # ─────────────────────────────────────────
    # 4. 成品尺寸檢查
    # ─────────────────────────────────────────

    def check_size(self) -> CheckResult:
        result = CheckResult(module="成品尺寸檢查", status=Status.PASS)
        page   = self.doc[0]
        media  = page.mediabox
        trim   = page.trimbox

        trimbox_is_set = (
            abs(trim.x0 - media.x0) > 0.5 or abs(trim.y0 - media.y0) > 0.5 or
            abs(trim.x1 - media.x1) > 0.5 or abs(trim.y1 - media.y1) > 0.5
        )

        # 尺寸比對來源：有 TrimBox 用 TrimBox，否則用 MediaBox 扣掉出血後的淨尺寸
        if trimbox_is_set:
            actual_w = self._pt_to_mm(trim.width)
            actual_h = self._pt_to_mm(trim.height)
            size_src = "TrimBox"
        else:
            # TrimBox 未設定時：從 MediaBox 扣掉出血量推算成品尺寸
            # （出血量以規格反推，與 check_bleed 視覺邏輯一致）
            spec_w_pt = self.spec_w * self.PT_PER_MM
            spec_h_pt = self.spec_h * self.PT_PER_MM
            diff_n = abs(media.width - spec_w_pt) + abs(media.height - spec_h_pt)
            diff_r = abs(media.width - spec_h_pt) + abs(media.height - spec_w_pt)
            if diff_r < diff_n:
                bw = max(0.0, media.width  - spec_h_pt) / 2
                bh = max(0.0, media.height - spec_w_pt) / 2
            else:
                bw = max(0.0, media.width  - spec_w_pt) / 2
                bh = max(0.0, media.height - spec_h_pt) / 2
            actual_w = self._pt_to_mm(media.width  - 2 * bw)
            actual_h = self._pt_to_mm(media.height - 2 * bh)
            size_src = "MediaBox（已扣除出血）"

        # 方向判斷（直式 / 橫式）
        spec_w, spec_h = self.spec_w, self.spec_h
        if self._close(actual_w, spec_w) and self._close(actual_h, spec_h):
            orientation, match = "直式", True
        elif self._close(actual_w, spec_h) and self._close(actual_h, spec_w):
            orientation, match = "橫式", True
            spec_w, spec_h = self.spec_h, self.spec_w
        else:
            orientation = "直式" if actual_h >= actual_w else "橫式"
            match = False

        result.add("尺寸來源", size_src, Status.PASS)
        result.add("頁面方向", orientation, Status.PASS)
        result.add("實際成品尺寸", f"{actual_w:.1f} × {actual_h:.1f} mm",
                   Status.PASS if match else Status.FAIL)
        result.add("規格要求", f"{self.spec_w} × {self.spec_h} mm",
                   Status.PASS if match else Status.FAIL,
                   "" if match else "尺寸與訂單不符，請確認後重新供稿")

        page_count = len(self.doc)
        if self.is_ai_file:
            label = "工作區域數量"
            note  = ("AI 檔案含多個工作區域，系統以第一個比對，其餘請分別輸出"
                     if page_count > 1 else "")
            result.add(label, f"{page_count} 個", Status.PASS, note)
        else:
            result.add("總頁數", f"{page_count} 頁", Status.PASS)

        return result

    # ─────────────────────────────────────────
    # 連結圖片（未嵌入）偵測 — 輔助方法
    # ─────────────────────────────────────────

    def _detect_linked_images(self) -> list[str]:
        """偵測檔案中是否存在連結（未嵌入）的圖片。"""
        linked = []

        try:
            xmp = self.doc.get_xml_metadata()
        except Exception:
            xmp = None

        if xmp:
            ext_re = r"(?:tif|tiff|psd|psb|eps|jpg|jpeg|png|gif|bmp)"
            # 屬性形式：filePath="xxx.tif" 或 originalDocumentID="xxx.tif"
            linked += re.findall(
                rf'(?:filePath|originalDocumentID)="([^"]*\.{ext_re})"', xmp, re.IGNORECASE)
            # 元素形式：<stRef:filePath ...>xxx.tif</stRef:filePath>（Illustrator Ingredients/Pantry 實際結構）
            linked += re.findall(
                rf'<(?:[\w]+:)?filePath[^>]*>([^<]*\.{ext_re})</(?:[\w]+:)?filePath>', xmp, re.IGNORECASE)
            # 直接列於 <rdf:li> 內的純文字檔名
            linked += re.findall(
                rf'<rdf:li[^>]*>([^<]*\.{ext_re})</rdf:li>', xmp, re.IGNORECASE)

        # /OPI 字典：Open Prepress Interface，業界標準的連結圖片標記
        try:
            for xref in range(1, self.doc.xref_length()):
                try:
                    obj = self.doc.xref_object(xref, compressed=True)
                except Exception:
                    continue
                if obj and "/OPI" in obj:
                    linked.append(f"物件 #{xref}（OPI 連結圖片）")
        except Exception:
            pass

        seen, unique = set(), []
        for item in linked:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        return unique

    # ─────────────────────────────────────────
    # 5. 影像解析度檢查
    # ─────────────────────────────────────────

    def check_resolution(self) -> CheckResult:
        result = CheckResult(module="影像解析度檢查", status=Status.PASS)

        all_dpi     = []
        low_dpi     = []    # [(page, effective_dpi)]
        img_count   = 0

        for page_num in range(len(self.doc)):
            page = self.doc[page_num]
            page_w_pt = page.trimbox.width
            page_h_pt = page.trimbox.height

            for img in page.get_images(full=True):
                xref = img[0]
                img_w_px = img[2]   # 影像像素寬
                img_h_px = img[3]   # 影像像素高
                img_count += 1

                try:
                    # 取得影像在頁面上的實際渲染尺寸（points）
                    rects = page.get_image_rects(xref)
                    if rects:
                        render_w_pt = rects[0].width
                        render_h_pt = rects[0].height
                        if render_w_pt > 0 and render_h_pt > 0:
                            dpi_x = img_w_px / (render_w_pt / 72)
                            dpi_y = img_h_px / (render_h_pt / 72)
                            eff_dpi = round(min(dpi_x, dpi_y))
                            all_dpi.append(eff_dpi)
                            if eff_dpi < self.min_dpi:
                                low_dpi.append((page_num + 1, eff_dpi))
                except Exception:
                    pass

        linked_images = self._detect_linked_images()

        if img_count == 0:
            if linked_images:
                sample = "、".join(linked_images[:3])
                result.add("嵌入影像數量", "0（無嵌入點陣影像）", Status.PASS)
                result.add("連結圖片（未嵌入）", f"偵測到 {len(linked_images)} 個",
                           Status.WARNING,
                           f"範例：{sample}　|　連結圖片無法計算實際解析度，"
                           "請改用「封裝」(File > Package) 或將所有連結圖片嵌入後重新上傳")
            else:
                result.add("嵌入影像數量", "0（無點陣影像）", Status.PASS)
            return result

        result.add("嵌入影像數量", f"{img_count} 張", Status.PASS)

        if all_dpi:
            min_dpi = min(all_dpi)
            avg_dpi = round(sum(all_dpi) / len(all_dpi))
            result.add("平均有效 DPI", f"{avg_dpi} DPI",
                       Status.PASS if avg_dpi >= self.min_dpi else Status.WARNING)
            result.add("最低有效 DPI", f"{min_dpi} DPI",
                       Status.PASS if min_dpi >= self.min_dpi else
                       Status.WARNING if min_dpi >= self.min_dpi * 0.5 else Status.FAIL)

        if not low_dpi:
            result.add(f"低於 {self.min_dpi} DPI 的影像", "0 張", Status.PASS)
        else:
            details = ", ".join(f"P{p}:{d}dpi" for p, d in low_dpi[:5])
            sev = Status.WARNING if max(d for _, d in low_dpi) >= self.min_dpi * 0.5 else Status.FAIL
            result.add(f"低於 {self.min_dpi} DPI 的影像",
                       f"{len(low_dpi)} 張（{details}）", sev,
                       "解析度不足，印刷後可能出現馬賽克或模糊")

        if linked_images:
            sample = "、".join(linked_images[:3])
            result.add("連結圖片（未嵌入）", f"偵測到 {len(linked_images)} 個",
                       Status.WARNING,
                       f"範例：{sample}　|　連結圖片無法計算實際解析度，"
                       "請改用「封裝」(File > Package) 或將所有連結圖片嵌入後重新上傳")

        return result

    # ─────────────────────────────────────────
    # 6. 總墨量（TAC）背印檢查
    # ─────────────────────────────────────────

    def check_tac(self) -> CheckResult:
        """
        Total Area Coverage (TAC) 檢查。
        掃描 content stream 中的 CMYK 填色指令（k/K），
        計算 C+M+Y+K 總和，超過上限即警告或退稿。

        背印（Back Trap）原因：
            墨量過高 → 油墨未乾透 → 印到背面（背印）
        建議上限：250%（本系統預設）
        絕對上限：300%（業界標準，超過必退稿）
        """
        result = CheckResult(module="總墨量（TAC）背印檢查", status=Status.PASS)

        warn_limit = self.max_tac       # 警告線（預設 250%）
        fail_limit = max(300, self.max_tac + 50)  # 退稿線（不低於 300%）

        max_found   = 0.0       # 全文件最高 TAC
        over_warn   = []        # [(page, tac)] 超過警告線
        over_fail   = []        # [(page, tac)] 超過退稿線
        samples     = []        # 超標色值樣本

        for page_num in range(len(self.doc)):
            page = self.doc[page_num]
            try:
                content = page.read_contents().decode("latin-1", errors="replace")
            except Exception:
                continue

            # k = CMYK 填色；K = CMYK 描邊
            # 格式：c m y k  k（或 K）
            cmyk_ops = re.findall(
                r'([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+[kK]\b',
                content
            )

            for c, m, y, k in cmyk_ops:
                tac = round((float(c) + float(m) + float(y) + float(k)) * 100, 1)
                if tac > max_found:
                    max_found = tac
                if tac > fail_limit:
                    over_fail.append((page_num + 1, tac))
                    if len(samples) < 3:
                        samples.append(f"P{page_num+1}:C{int(float(c)*100)}M{int(float(m)*100)}"
                                       f"Y{int(float(y)*100)}K{int(float(k)*100)}={tac:.0f}%")
                elif tac > warn_limit:
                    over_warn.append((page_num + 1, tac))
                    if len(samples) < 3:
                        samples.append(f"P{page_num+1}:C{int(float(c)*100)}M{int(float(m)*100)}"
                                       f"Y{int(float(y)*100)}K{int(float(k)*100)}={tac:.0f}%")

        # ── 輸出 ──────────────────────────────────────────────
        result.add("TAC 上限設定", f"{warn_limit}%", Status.PASS)
        result.add("最高 TAC", f"{max_found:.0f}%",
                   Status.PASS    if max_found <= warn_limit  else
                   Status.WARNING if max_found <= fail_limit  else
                   Status.FAIL)

        if not over_fail and not over_warn:
            result.add("超標物件", "0 個", Status.PASS,
                       "所有向量色彩均在墨量上限內")
        elif over_fail:
            pages = sorted(set(p for p, _ in over_fail))
            result.add(f"超過退稿線（{fail_limit}%）", f"{len(over_fail)} 個（頁面：{pages}）",
                       Status.FAIL,
                       f"範例：{'、'.join(samples[:2])}　|　背印風險極高，必須降低墨量")
            if over_warn:
                wpages = sorted(set(p for p, _ in over_warn))
                result.add(f"超過警告線（{warn_limit}%）", f"{len(over_warn)} 個（頁面：{wpages}）",
                           Status.WARNING, "建議調整")
        else:
            pages = sorted(set(p for p, _ in over_warn))
            result.add(f"超過警告線（{warn_limit}%）", f"{len(over_warn)} 個（頁面：{pages}）",
                       Status.WARNING,
                       f"範例：{'、'.join(samples[:2])}　|　建議降低墨量以避免背印")
            result.add(f"超過退稿線（{fail_limit}%）", "0 個", Status.PASS)

        if max_found == 0:
            result.add("備註", "未偵測到 CMYK 向量指令（可能為純 RGB 或純文字）", Status.PASS)

        return result

    # ─────────────────────────────────────────
    # 執行全部檢查
    # ─────────────────────────────────────────

    def run_all(self) -> PreflightReport:
        report = PreflightReport(filename=self.path.name, file_format=self.file_format_label)

        if self.doc is None:
            report.open_error = self.open_error
            report.results.append(CheckResult(
                module="檔案格式檢查",
                status=Status.ERROR,
                message=self.open_error,
            ))
            return report

        checks = [
            self.check_color_mode,
            self.check_fonts,
            self.check_bleed,
            self.check_size,
            self.check_resolution,
            self.check_tac,
        ]
        for fn in checks:
            try:
                report.results.append(fn())
            except Exception as e:
                report.results.append(CheckResult(
                    module=fn.__name__.replace("check_", ""),
                    status=Status.ERROR,
                    message=str(e)
                ))
        self.doc.close()
        return report


# ─────────────────────────────────────────────
# FastAPI 整合範例（可獨立部署）
# ─────────────────────────────────────────────

FASTAPI_EXAMPLE = '''
# fastapi_app.py  (需安裝: pip install fastapi uvicorn python-multipart)

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
import tempfile, os
from preflight_checker import PreflightChecker, Status

app = FastAPI(title="印刷廠校稿系統 API", version="1.0.0")

@app.post("/preflight")
async def run_preflight(
    file:       UploadFile = File(...),
    spec_width:  float = Form(210.0),
    spec_height: float = Form(297.0),
    bleed_mm:    float = Form(3.0),
    min_dpi:     int   = Form(300),
):
    """
    上傳 PDF 並執行五大校稿檢查
    回傳 JSON 格式報告
    """
    # 暫存上傳檔案
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        checker = PreflightChecker(
            tmp_path,
            spec_width_mm  = spec_width,
            spec_height_mm = spec_height,
            bleed_mm       = bleed_mm,
            min_dpi        = min_dpi,
        )
        report = checker.run_all()
    finally:
        os.unlink(tmp_path)

    # 序列化輸出
    return JSONResponse({
        "filename":  report.filename,
        "overall":   report.overall.value,
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
                        "note":   i.note
                    }
                    for i in r.items
                ]
            }
            for r in report.results
        ]
    })

# 啟動：uvicorn fastapi_app:app --host 0.0.0.0 --port 8000
'''


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("使用方式：python preflight_checker.py <PDF或AI路徑> [寬mm] [高mm] [出血mm] [最低DPI]")
        print("範例：  python preflight_checker.py artwork.pdf 210 297 3 300")
        print("範例：  python preflight_checker.py artwork.ai 210 297 3 300")
        sys.exit(0)

    pdf_path    = sys.argv[1]
    spec_w      = float(sys.argv[2]) if len(sys.argv) > 2 else 210
    spec_h      = float(sys.argv[3]) if len(sys.argv) > 3 else 297
    bleed       = float(sys.argv[4]) if len(sys.argv) > 4 else 3
    dpi         = int(sys.argv[5])   if len(sys.argv) > 5 else 300

    checker = PreflightChecker(pdf_path, spec_w, spec_h, bleed, dpi)
    report  = checker.run_all()
    print(report.summary())
    print("\n── FastAPI 整合範例 ──")
    print(FASTAPI_EXAMPLE)

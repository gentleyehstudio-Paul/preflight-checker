"""
印刷廠校稿系統 — PDF 報告產生器
Phase 3：自動生成校稿報告 PDF
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus.flowables import Flowable
from datetime import datetime
import os, subprocess

from preflight_checker import PreflightReport, Status


# ── 色彩定義 ───────────────────────────────────────────
C_BLACK   = colors.HexColor("#1A1A1A")
C_GRAY    = colors.HexColor("#666666")
C_LGRAY   = colors.HexColor("#F4F4F4")
C_BORDER  = colors.HexColor("#E0E0E0")
C_GREEN   = colors.HexColor("#2D7D46")
C_GREEN_L = colors.HexColor("#EBF7EF")
C_ORANGE  = colors.HexColor("#C05C0A")
C_ORANGE_L= colors.HexColor("#FFF3E9")
C_RED     = colors.HexColor("#B91C1C")
C_RED_L   = colors.HexColor("#FEF2F2")
C_ACCENT  = colors.HexColor("#1A1A1A")

STATUS_COLOR = {
    "pass":  (C_GREEN,  C_GREEN_L,  "通過"),
    "warn":  (C_ORANGE, C_ORANGE_L, "警告"),
    "fail":  (C_RED,    C_RED_L,    "退稿"),
    "error": (C_GRAY,   C_LGRAY,    "錯誤"),
}

ICON = {
    "pass":  "✓",
    "warn":  "!",
    "fail":  "✗",
    "error": "?",
}


# ── 字型設定（使用系統可用字型）────────────────────────
def _setup_fonts():
    """嘗試找到可用的中文字型"""
    candidates = [
        # Linux
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.otf",
        # macOS
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode MS.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont("ZH", path))
                return "ZH"
            except Exception:
                continue
    # 回退：使用 ReportLab 內建 Helvetica（不支援中文，但不崩潰）
    return "Helvetica"


FONT = _setup_fonts()
FONT_BOLD = FONT  # 部分字型沒有 Bold 變體，直接用同一個


# ── 自訂 Flowable：色塊標題橫幅 ────────────────────────
class SectionHeader(Flowable):
    def __init__(self, text, status="pass", width=0):
        super().__init__()
        self.text   = text
        self.status = status
        self._width = width or (A4[0] - 40*mm)
        self.height = 22

    def draw(self):
        fg, bg, _ = STATUS_COLOR.get(self.status, STATUS_COLOR["pass"])
        c = self.canv
        # 背景條
        c.setFillColor(bg)
        c.rect(0, 0, self._width, self.height, stroke=0, fill=1)
        # 左側色條
        c.setFillColor(fg)
        c.rect(0, 0, 4, self.height, stroke=0, fill=1)
        # 圖示
        c.setFillColor(fg)
        c.setFont(FONT, 11)
        c.drawString(10, 6, ICON.get(self.status, ""))
        # 文字
        c.setFillColor(C_BLACK)
        c.setFont(FONT, 10)
        c.drawString(24, 6, self.text)
        # 狀態標籤
        _, _, label = STATUS_COLOR.get(self.status, STATUS_COLOR["pass"])
        label_w = 36
        c.setFillColor(fg)
        c.roundRect(self._width - label_w - 4, 3, label_w, 16, 3, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont(FONT, 8)
        c.drawCentredString(self._width - label_w/2 - 4, 7, label)


# ── 主要產生函式 ────────────────────────────────────────
def generate_pdf_report(
    report: PreflightReport,
    output_path: str,
    spec_name:  str   = "A4",
    bleed_mm:   float = 3.0,
    min_dpi:    int   = 300,
):
    page_w, page_h = A4
    margin = 20 * mm

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=margin, rightMargin=margin,
        topMargin=margin,  bottomMargin=margin,
        title=f"校稿報告 — {report.filename}",
        author="印刷廠校稿系統 v1.0",
    )

    story = []
    usable_w = page_w - 2 * margin

    # ── 封面標題區 ──────────────────────────────────────
    overall_status = report.overall.value
    fg, bg, overall_label = STATUS_COLOR.get(overall_status, STATUS_COLOR["pass"])

    # 系統名稱
    story.append(Paragraph(
        "印刷廠校稿系統",
        ParagraphStyle("sys", fontName=FONT, fontSize=9, textColor=C_GRAY,
                       spaceBefore=0, spaceAfter=2)
    ))
    # 主標題
    story.append(Paragraph(
        "PDF 校稿報告",
        ParagraphStyle("title", fontName=FONT_BOLD, fontSize=22,
                       textColor=C_BLACK, spaceBefore=0, spaceAfter=6)
    ))

    # 整體結論橫幅
    verdict_data = [[
        Paragraph(f"{ICON[overall_status]}  整體結論：{overall_label}",
                  ParagraphStyle("v", fontName=FONT_BOLD, fontSize=13,
                                 textColor=fg, spaceBefore=0, spaceAfter=0)),
    ]]
    verdict_table = Table(verdict_data, colWidths=[usable_w])
    verdict_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), bg),
        ("LEFTPADDING",  (0,0), (-1,-1), 12),
        ("RIGHTPADDING", (0,0), (-1,-1), 12),
        ("TOPPADDING",   (0,0), (-1,-1), 10),
        ("BOTTOMPADDING",(0,0), (-1,-1), 10),
        ("LINEAFTER",  (0,0), (0,-1), 4, fg),
    ]))
    story.append(verdict_table)
    story.append(Spacer(1, 6*mm))

    # 基本資訊表
    info_data = [
        ["稿件檔名", report.filename,  "校稿時間", datetime.now().strftime("%Y-%m-%d  %H:%M")],
        ["檔案格式", report.file_format, "成品規格", spec_name],
        ["要求出血", f"{bleed_mm} mm",   "最低解析度", f"{min_dpi} DPI"],
    ]
    info_table = Table(
        info_data,
        colWidths=[30*mm, usable_w*0.38, 30*mm, usable_w*0.27],
    )
    info_table.setStyle(TableStyle([
        ("FONT",       (0,0), (-1,-1), FONT, 9),
        ("FONT",       (0,0), (0,-1), FONT_BOLD, 9),
        ("FONT",       (2,0), (2,-1), FONT_BOLD, 9),
        ("TEXTCOLOR",  (0,0), (0,-1), C_GRAY),
        ("TEXTCOLOR",  (2,0), (2,-1), C_GRAY),
        ("BACKGROUND", (0,0), (-1,-1), C_LGRAY),
        ("GRID",       (0,0), (-1,-1), 0.3, C_BORDER),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0), (-1,-1), 5),
        ("LEFTPADDING",(0,0), (-1,-1), 8),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 6*mm))

    # ── 摘要計數列 ──────────────────────────────────────
    counts = {"pass": 0, "warn": 0, "fail": 0}
    for r in report.results:
        counts[r.status.value] = counts.get(r.status.value, 0) + 1

    summary_data = [[
        Paragraph(f"<para align='center'><font size=18>{counts['pass']}</font><br/>"
                  f"<font size=8 color='#2D7D46'>項通過</font></para>",
                  ParagraphStyle("sc", fontName=FONT, fontSize=8, textColor=C_GREEN)),
        Paragraph(f"<para align='center'><font size=18>{counts['warn']}</font><br/>"
                  f"<font size=8 color='#C05C0A'>項警告</font></para>",
                  ParagraphStyle("sw", fontName=FONT, fontSize=8, textColor=C_ORANGE)),
        Paragraph(f"<para align='center'><font size=18>{counts['fail']}</font><br/>"
                  f"<font size=8 color='#B91C1C'>項退稿</font></para>",
                  ParagraphStyle("sf", fontName=FONT, fontSize=8, textColor=C_RED)),
        Paragraph(f"<para align='center'><font size=18>{len(report.results)}</font><br/>"
                  f"<font size=8 color='#666666'>項總計</font></para>",
                  ParagraphStyle("st", fontName=FONT, fontSize=8, textColor=C_GRAY)),
    ]]
    col = usable_w / 4
    summary_table = Table(summary_data, colWidths=[col]*4)
    summary_table.setStyle(TableStyle([
        ("ALIGN",       (0,0), (-1,-1), "CENTER"),
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
        ("GRID",        (0,0), (-1,-1), 0.3, C_BORDER),
        ("TOPPADDING",  (0,0), (-1,-1), 8),
        ("BOTTOMPADDING",(0,0),(-1,-1), 8),
        ("BACKGROUND",  (0,0), (0,-1), C_GREEN_L),
        ("BACKGROUND",  (1,0), (1,-1), C_ORANGE_L),
        ("BACKGROUND",  (2,0), (2,-1), C_RED_L),
        ("BACKGROUND",  (3,0), (3,-1), C_LGRAY),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 8*mm))

    # ── 分隔線 ──────────────────────────────────────────
    story.append(HRFlowable(width=usable_w, thickness=0.5, color=C_BORDER))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph(
        "詳細檢查結果",
        ParagraphStyle("section_title", fontName=FONT_BOLD, fontSize=11,
                       textColor=C_BLACK, spaceBefore=0, spaceAfter=6)
    ))

    # ── 各模組詳細結果 ───────────────────────────────────
    for result in report.results:
        status = result.status.value
        fg_c, bg_c, label = STATUS_COLOR.get(status, STATUS_COLOR["pass"])

        module_block = []

        # 模組標題
        module_block.append(SectionHeader(
            f"{result.module}",
            status=status,
            width=usable_w,
        ))
        module_block.append(Spacer(1, 2*mm))

        # 檢查項目表格
        if result.items:
            item_rows = [["檢查項目", "結果", "狀態", "說明"]]
            for item in result.items:
                item_status = item.status.value
                item_fg, _, item_label = STATUS_COLOR.get(item_status, STATUS_COLOR["pass"])
                item_rows.append([
                    Paragraph(item.key, ParagraphStyle("ik", fontName=FONT, fontSize=8, textColor=C_GRAY)),
                    Paragraph(item.value, ParagraphStyle("iv", fontName=FONT, fontSize=8, textColor=C_BLACK)),
                    Paragraph(
                        f"{ICON[item_status]} {item_label}",
                        ParagraphStyle("is", fontName=FONT_BOLD, fontSize=8, textColor=item_fg)
                    ),
                    Paragraph(item.note or "—", ParagraphStyle("in", fontName=FONT, fontSize=8, textColor=C_GRAY)),
                ])

            col_widths = [42*mm, 62*mm, 22*mm, usable_w - 126*mm]
            item_table = Table(item_rows, colWidths=col_widths)
            ts = TableStyle([
                # 標頭
                ("BACKGROUND",   (0,0), (-1,0), C_ACCENT),
                ("TEXTCOLOR",    (0,0), (-1,0), colors.white),
                ("FONT",         (0,0), (-1,0), FONT_BOLD, 8),
                ("TOPPADDING",   (0,0), (-1,0), 5),
                ("BOTTOMPADDING",(0,0), (-1,0), 5),
                ("LEFTPADDING",  (0,0), (-1,-1), 7),
                # 內容列
                ("FONT",         (0,1), (-1,-1), FONT, 8),
                ("TOPPADDING",   (0,1), (-1,-1), 4),
                ("BOTTOMPADDING",(0,1), (-1,-1), 4),
                ("GRID",         (0,0), (-1,-1), 0.3, C_BORDER),
                ("ROWBACKGROUNDS",(0,1),(-1,-1), [colors.white, C_LGRAY]),
                ("VALIGN",       (0,0), (-1,-1), "MIDDLE"),
            ])
            item_table.setStyle(ts)
            module_block.append(item_table)

        if result.message:
            module_block.append(Spacer(1, 2*mm))
            module_block.append(Paragraph(
                f"備註：{result.message}",
                ParagraphStyle("msg", fontName=FONT, fontSize=8, textColor=C_GRAY)
            ))

        module_block.append(Spacer(1, 5*mm))
        story.append(KeepTogether(module_block))

    # ── 頁尾說明 ────────────────────────────────────────
    story.append(HRFlowable(width=usable_w, thickness=0.3, color=C_BORDER))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        "本報告由印刷廠自動校稿系統產生，僅供初步篩查參考。最終品質判定以印刷廠 RIP 輸出為準。",
        ParagraphStyle("footer", fontName=FONT, fontSize=7, textColor=C_GRAY, alignment=TA_CENTER)
    ))

    # ── 頁碼回呼 ────────────────────────────────────────
    def add_page_number(canvas, doc):
        canvas.saveState()
        canvas.setFont(FONT, 7)
        canvas.setFillColor(C_GRAY)
        canvas.drawRightString(
            page_w - margin,
            10*mm,
            f"第 {doc.page} 頁  |  {report.filename}  |  校稿系統 v1.0"
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
    return output_path

#!/usr/bin/env python3
"""
IFRA Certificate of Conformity Generator

Usage:
  # Single supplier PDF reformat:
  python generate_ifra.py --input supplier.pdf --product-name "Rose Absolute" --sku "FO1234" --output output.pdf

  # Blend from multiple supplier PDFs with blend percentages:
  python generate_ifra.py \
    --blend "Rose Absolute:supplier1.pdf:40" "Vanilla CO2:supplier2.pdf:60" \
    --product-name "My Blend" --sku "BL001" --output output.pdf
"""

import sys
import json
import argparse
import re
import os
from pathlib import Path
from datetime import date

import pdfplumber
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, Image as RLImage, KeepTogether
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

# ── Paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CLASSES_PATH = PROJECT_DIR / "references" / "ifra-classes.json"

# ── Load config ──────────────────────────────────────────────────────────────
CONFIG_PATH = PROJECT_DIR / "config.json"
if not CONFIG_PATH.exists():
    print(f"ERROR: config.json not found at {CONFIG_PATH}")
    print("Copy config.example.json to config.json and fill in your company details.")
    sys.exit(1)

with open(CONFIG_PATH) as _f:
    _config = json.load(_f)

COMPANY_NAME = _config["company_name"]
COMPANY_PHONE = _config["phone"]
COMPANY_EMAIL = _config["email"]
LOGO_PATH = PROJECT_DIR / _config.get("logo", "assets/logo.png")
AMENDMENT = _config.get("amendment", "51st")

# ── Brand colours ─────────────────────────────────────────────────────────────
DARK = colors.HexColor("#1a1a1a")
TABLE_HEADER_BG = colors.HexColor("#2c2c2c")
TABLE_ROW_ALT = colors.HexColor("#f5f5f5")
TABLE_BORDER = colors.HexColor("#cccccc")
DIVIDER = colors.HexColor("#aaaaaa")

# ── Styles ────────────────────────────────────────────────────────────────────
def make_styles():
    return {
        "title": ParagraphStyle(
            "title",
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=28,
            alignment=TA_CENTER,
            textColor=DARK,
            spaceAfter=6,
        ),
        "section_heading": ParagraphStyle(
            "section_heading",
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            textColor=DARK,
            spaceAfter=4,
        ),
        "label": ParagraphStyle(
            "label",
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            textColor=DARK,
        ),
        "value": ParagraphStyle(
            "value",
            fontName="Helvetica",
            fontSize=10,
            leading=13,
            textColor=DARK,
        ),
        "body": ParagraphStyle(
            "body",
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            textColor=DARK,
            spaceAfter=6,
        ),
        "small": ParagraphStyle(
            "small",
            fontName="Helvetica",
            fontSize=8,
            leading=11,
            textColor=colors.HexColor("#444444"),
        ),
        "footer": ParagraphStyle(
            "footer",
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#666666"),
            alignment=TA_LEFT,
        ),
        "table_header": ParagraphStyle(
            "table_header",
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            textColor=colors.white,
            alignment=TA_LEFT,
        ),
        "table_header_right": ParagraphStyle(
            "table_header_right",
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            textColor=colors.white,
            alignment=TA_CENTER,
        ),
        "table_cell": ParagraphStyle(
            "table_cell",
            fontName="Helvetica",
            fontSize=9.5,
            leading=12,
            textColor=DARK,
        ),
        "table_cell_center": ParagraphStyle(
            "table_cell_center",
            fontName="Helvetica",
            fontSize=9.5,
            leading=12,
            textColor=DARK,
            alignment=TA_CENTER,
        ),
        "reg_label": ParagraphStyle(
            "reg_label",
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            textColor=DARK,
            alignment=TA_RIGHT,
        ),
        "reg_value": ParagraphStyle(
            "reg_value",
            fontSize=9,
            leading=12,
            textColor=DARK,
            alignment=TA_RIGHT,
        ),
        "class_def_header": ParagraphStyle(
            "class_def_header",
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            textColor=colors.white,
        ),
        "class_def_cell": ParagraphStyle(
            "class_def_cell",
            fontName="Helvetica",
            fontSize=8.5,
            leading=12,
            textColor=DARK,
        ),
        "disclaimer": ParagraphStyle(
            "disclaimer",
            fontName="Helvetica",
            fontSize=8.5,
            leading=13,
            textColor=DARK,
        ),
    }


# ── Ordered category keys for 51st Amendment ─────────────────────────────────
CATEGORY_ORDER = [
    "1", "2", "3", "4", "5A", "5B", "5C", "5D",
    "6", "7A", "7B", "8", "9", "10A", "10B", "11A", "11B", "12",
]


def _normalize_category_key(raw: str) -> str | None:
    """Normalize a parsed category/class identifier to a standard key.

    Handles formats like: '5A', '5.A', '5 A', '5.a', '10A', '10.A',
    '11B', '11.B', as well as plain numbers '1' through '12'.
    Returns None if the key isn't a recognised IFRA category.
    """
    s = raw.strip().upper().replace(".", "").replace(" ", "")
    if s in CATEGORY_ORDER:
        return s
    if s.isdigit() and s in CATEGORY_ORDER:
        return s
    return None


# ── PDF parsing ───────────────────────────────────────────────────────────────
def extract_usage_levels(pdf_path: str) -> dict[str, float]:
    """Parse a supplier IFRA PDF and return {category_key: max_usage_%}.

    Supports both 50th-Amendment 'Class N' and 51st-Amendment 'Category N'
    formats, including subcategory letters (e.g. 5A, 5.A, 10A, 11B).
    """
    levels = {}
    # Match "Class 5A  12.345" or "Category 10.B  0.500" etc.
    pattern = re.compile(
        r"[Cc](?:lass|ategory)\s+(\d+(?:[.\s]?[A-Da-d])?)\s+([\d.]+)"
    )
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for m in pattern.finditer(text):
                key = _normalize_category_key(m.group(1))
                if key is None:
                    continue
                val = float(m.group(2).strip())
                if key not in levels:
                    levels[key] = val
            # Also try table extraction
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if not row or len(row) < 2:
                        continue
                    cell0 = str(row[0] or "").strip()
                    cell1 = str(row[-1] or "").strip()  # value may be in last column
                    # Try to find a category identifier at the end of the cell
                    m2 = re.search(r"(\d+(?:[.\s]?[A-Da-d])?)$", cell0)
                    if m2:
                        key = _normalize_category_key(m2.group(1))
                        if key is None:
                            continue
                        try:
                            val = float(cell1.replace(",", ".").replace("%", "").strip())
                            if key not in levels:
                                levels[key] = val
                        except ValueError:
                            pass
    return levels


def blend_usage_levels(ingredients: list[tuple[str, str, float]]) -> dict[str, float]:
    """
    Compute blended IFRA limits for a finished fragrance blend.
    ingredients: list of (name, pdf_path, pct_in_blend)
    
    Formula: blended_limit[category] = min over each ingredient of:
        supplier_limit[category] / (ingredient_pct / 100)
    This gives the max % of finished blend that can be used in a product.
    """
    all_limits = []
    for name, pdf_path, pct in ingredients:
        limits = extract_usage_levels(pdf_path)
        all_limits.append((name, pct / 100.0, limits))

    classes = set()
    for _, _, lims in all_limits:
        classes.update(lims.keys())

    blended = {}
    for cls in classes:
        effective_limits = []
        for name, frac, lims in all_limits:
            if cls in lims:
                if frac > 0:
                    effective_limits.append(lims[cls] / frac)
                else:
                    effective_limits.append(lims[cls])
        if effective_limits:
            blended[cls] = round(min(effective_limits), 3)

    return blended


# ── PDF builder ───────────────────────────────────────────────────────────────
def load_classes() -> list[dict]:
    with open(CLASSES_PATH) as f:
        data = json.load(f)
    # Support both old key ("classes") and new key ("categories")
    return data.get("categories", data.get("classes", []))


def build_header(styles) -> list:
    """Logo left, regulatory info right — matching the template."""
    logo = RLImage(str(LOGO_PATH), width=2.1 * inch, height=0.55 * inch)

    reg_block = [
        Paragraph("Regulatory", styles["reg_label"]),
        Paragraph(COMPANY_PHONE, styles["reg_value"]),
        Paragraph(COMPANY_EMAIL, styles["reg_value"]),
    ]

    header_data = [[logo, reg_block]]
    header_table = Table(
        header_data,
        colWidths=[3.5 * inch, 3.5 * inch],
    )
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        # Vertical divider before reg block
        ("LINEAFTER", (0, 0), (0, 0), 1, colors.HexColor("#cccccc")),
    ]))
    return [header_table, Spacer(1, 0.15 * inch)]


def build_page1(styles, product_name, sku, usage_levels, is_blend=False, blend_info=None) -> list:
    """Page 1: title, product details, usage table, boilerplate."""
    story = []

    # Title
    story.append(Paragraph("IFRA Certificate of Conformity", styles["title"]))
    story.append(HRFlowable(width="100%", thickness=1, color=DIVIDER, spaceAfter=10, spaceBefore=4))

    # Product details
    story.append(Paragraph("Product Details", styles["section_heading"]))
    product_rows = [
        [Paragraph("Name :", styles["label"]),
         Paragraph(product_name, styles["value"])],
        [Paragraph("SKU :", styles["label"]),
         Paragraph(sku, styles["value"])],
    ]
    if is_blend and blend_info:
        comp_text = ", ".join([f"{name} ({pct}%)" for name, _, pct in blend_info])
        product_rows.append([
            Paragraph("Composition :", styles["label"]),
            Paragraph(comp_text, styles["value"]),
        ])
    product_rows.append([
        Paragraph("Date :", styles["label"]),
        Paragraph(date.today().strftime("%B %d, %Y"), styles["value"]),
    ])

    prod_table = Table(product_rows, colWidths=[1.2 * inch, 5.8 * inch])
    prod_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(prod_table)
    story.append(Spacer(1, 0.18 * inch))

    # Certification text
    blend_note = " (calculated for the finished blend)" if is_blend else ""
    cert_text = (
        f"{COMPANY_NAME} certifies that the above mentioned product is in compliance with the "
        f"{AMENDMENT} Amendment of the IFRA safety guidelines. Below are the maximum usage "
        f"dosages in fragrance{blend_note} for the categories:"
    )
    story.append(Paragraph(cert_text, styles["body"]))
    story.append(Spacer(1, 0.1 * inch))

    # Usage table — all 18 IFRA categories (51st Amendment)
    table_data = [
        [Paragraph("Category", styles["table_header"]),
         Paragraph("Maximum Usage Level %", styles["table_header_right"])]
    ]
    for idx, cat in enumerate(CATEGORY_ORDER):
        val = usage_levels.get(cat, 0.0)
        table_data.append([
            Paragraph(f"Category {cat}", styles["table_cell"]),
            Paragraph(f"{val:.3f}", styles["table_cell_center"]),
        ])

    col_widths = [2.5 * inch, 2.5 * inch]
    usage_table = Table(table_data, colWidths=col_widths)
    row_styles = [
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
        ("GRID", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, TABLE_ROW_ALT]),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    usage_table.setStyle(TableStyle(row_styles))

    # Centre the table on the page
    centred = Table([[usage_table]], colWidths=[7 * inch])
    centred.setStyle(TableStyle([
        ("ALIGN", (0, 0), (0, 0), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(centred)
    story.append(Spacer(1, 0.2 * inch))

    # Boilerplate paragraphs
    boilerplate = [
        ("For use in other applications or use at higher concentration levels, a new safety evaluation "
         "may be needed. The IFRA Standards regarding use restrictions are based on safety assessments "
         "by the Panel of Experts of the Research Institute for Fragrance Materials (RIFM) and are "
         "enforced by the IFRA Scientific Committee."),
        ("Evaluation of individual fragrance ingredients is made according to the safety standards "
         "contained in the relevant section of the IFRA Code of Practice."),
        ("It is the ultimate responsibility of our customer to ensure the safety of the final product "
         "(containing the fragrance) by further testing if needed."),
    ]
    for para in boilerplate:
        story.append(Paragraph(para, styles["body"]))

    return story


def build_class_def_pages(styles, classes_data) -> list:
    """Pages 2+: IFRA category definitions table."""
    story = []
    story.append(PageBreak())
    story.append(Paragraph("IFRA Category Definitions", styles["title"]))
    story.append(HRFlowable(width="100%", thickness=1, color=DIVIDER, spaceAfter=10, spaceBefore=4))

    table_data = [
        [Paragraph("Category", styles["table_header"]),
         Paragraph("Finished Product Type", styles["table_header"])]
    ]
    for cls_info in classes_data:
        products_text = "\n".join(f"• {p}" for p in cls_info["products"])
        # Use the "category" key if present, fall back to "class" for backwards compat
        cat_key = cls_info.get("category", cls_info.get("class", ""))
        cat_label = cls_info.get("label", f"Category {cat_key}")
        table_data.append([
            Paragraph(cat_label, styles["table_cell"]),
            Paragraph(products_text, styles["class_def_cell"]),
        ])

    def_table = Table(table_data, colWidths=[1.1 * inch, 5.9 * inch], repeatRows=1)
    def_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
        ("GRID", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, TABLE_ROW_ALT]),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(def_table)
    return story


def build_disclaimer_page(styles) -> list:
    """Final page: disclaimer."""
    story = []
    story.append(PageBreak())
    story.append(Paragraph("Disclaimer", styles["title"]))
    story.append(HRFlowable(width="100%", thickness=1, color=DIVIDER, spaceAfter=14, spaceBefore=4))

    disclaimer_text = (
        f"The information contained in these documents is confidential, privileged and only for the "
        f"information of the intended recipient and may not be used, published or redistributed without "
        f"the prior written consent of {COMPANY_NAME}.\n\n"
        f"The opinions expressed are in good faith and while every care has been taken in preparing "
        f"these documents, {COMPANY_NAME} makes no representations and gives no warranties of whatever "
        f"nature in respect of these documents, including but not limited to the accuracy or completeness "
        f"of any information, facts and/or opinions contained therein.\n\n"
        f"{COMPANY_NAME}, its subsidiaries, the directors, employees and agents cannot be held liable "
        f"for the use of and reliance of the opinions, estimates, forecasts and findings in these documents."
    )
    story.append(Paragraph(disclaimer_text, styles["disclaimer"]))
    return story


def add_footer(canvas_obj, doc):
    """Draw footer on every page: company name left, page number right."""
    canvas_obj.saveState()
    canvas_obj.setFont("Helvetica", 8)
    canvas_obj.setFillColor(colors.HexColor("#666666"))
    page_width = letter[0]
    y = 0.45 * inch
    canvas_obj.drawString(0.75 * inch, y, COMPANY_NAME)
    page_str = f"Page {doc.page} / {doc._pageCount if hasattr(doc, '_pageCount') else '?'}"
    canvas_obj.drawRightString(page_width - 0.75 * inch, y, page_str)
    canvas_obj.restoreState()


def generate_certificate(
    output_path: str,
    product_name: str,
    sku: str,
    usage_levels: dict[str, float],
    is_blend: bool = False,
    blend_info=None,
):
    classes_data = load_classes()
    styles = make_styles()

    # We need to know page count for footer; build twice is complex so we
    # just track manually.
    total_pages = 4  # page1 + class defs (2 pages typically) + disclaimer

    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.75 * inch,
    )

    story = []
    story += build_header(styles)
    story += build_page1(styles, product_name, sku, usage_levels, is_blend, blend_info)
    story += build_class_def_pages(styles, classes_data)
    story += build_disclaimer_page(styles)

    # We'll use a closure to inject correct total pages
    page_counts = [0]

    def on_page(canvas_obj, doc):
        page_counts[0] = max(page_counts[0], doc.page)
        canvas_obj.saveState()
        canvas_obj.setFont("Helvetica", 8)
        canvas_obj.setFillColor(colors.HexColor("#666666"))
        pw = letter[0]
        y = 0.45 * inch
        canvas_obj.drawString(0.75 * inch, y, COMPANY_NAME)
        canvas_obj.restoreState()

    # Build once to get page count, then rebuild with correct count
    import io
    buf = io.BytesIO()
    doc2 = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.75 * inch,
    )

    story2 = []
    story2 += build_header(styles)
    story2 += build_page1(styles, product_name, sku, usage_levels, is_blend, blend_info)
    story2 += build_class_def_pages(styles, classes_data)
    story2 += build_disclaimer_page(styles)
    doc2.build(story2, onFirstPage=on_page, onLaterPages=on_page)
    total_pages = page_counts[0]

    # Real build with footer
    story3 = []
    story3 += build_header(styles)
    story3 += build_page1(styles, product_name, sku, usage_levels, is_blend, blend_info)
    story3 += build_class_def_pages(styles, classes_data)
    story3 += build_disclaimer_page(styles)

    def footer_with_total(canvas_obj, doc):
        canvas_obj.saveState()
        canvas_obj.setFont("Helvetica", 8)
        canvas_obj.setFillColor(colors.HexColor("#666666"))
        pw = letter[0]
        y = 0.45 * inch
        canvas_obj.drawString(0.75 * inch, y, COMPANY_NAME)
        canvas_obj.drawRightString(pw - 0.75 * inch, y, f"Page {doc.page} / {total_pages}")
        canvas_obj.restoreState()

    doc3 = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.75 * inch,
    )
    doc3.build(story3, onFirstPage=footer_with_total, onLaterPages=footer_with_total)
    print(f"Certificate saved: {output_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Generate IFRA Certificate of Conformity")
    parser.add_argument("--product-name", required=True, help="Product name")
    parser.add_argument("--sku", required=True, help="Product SKU")
    parser.add_argument("--output", required=True, help="Output PDF path")
    parser.add_argument("--input", help="Single supplier IFRA PDF to reformat")
    parser.add_argument(
        "--blend",
        nargs="+",
        metavar="NAME:PDF:PCT",
        help='Blend components as "Name:path/to.pdf:percentage"',
    )
    args = parser.parse_args()

    if args.input:
        levels = extract_usage_levels(args.input)
        generate_certificate(args.output, args.product_name, args.sku, levels)

    elif args.blend:
        ingredients = []
        for item in args.blend:
            parts = item.split(":")
            if len(parts) != 3:
                print(f"ERROR: blend item must be Name:pdf_path:percentage, got: {item}")
                sys.exit(1)
            name, pdf_path, pct = parts[0], parts[1], float(parts[2])
            ingredients.append((name, pdf_path, pct))
        total_pct = sum(p for _, _, p in ingredients)
        if abs(total_pct - 100.0) > 0.01:
            print(f"WARNING: blend percentages sum to {total_pct}%, not 100%. Continuing anyway.")
        levels = blend_usage_levels(ingredients)
        generate_certificate(
            args.output, args.product_name, args.sku, levels,
            is_blend=True, blend_info=ingredients
        )
    else:
        parser.error("Provide either --input or --blend")


if __name__ == "__main__":
    main()

import io
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle


def generate_document_pdf(
    filename_prefix: str,
    title: str,
    doc_number: str,
    doc_date: str,
    extra_meta: list[tuple[str, str]],  # e.g. [("Due Date", "2026-05-16"), ("Status", "Sent")]
    company: dict,                     # name, legal_name, email, phone, address, tax_id
    partner: dict,                     # name, email, phone, address (Customer or Vendor)
    lines: list[dict],                 # description, qty, unit_price, total
    totals: list[tuple[str, str]],     # [("Subtotal", "$100.00"), ("Total", "$110.00")]
    notes: str = ""
) -> bytes:
    buffer = io.BytesIO()

    # Page size: Letter (612 x 792 pt). Margin: 0.5 inch (36 pt). Printable width = 540 pt.
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "DocTitleStyle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=24,
        leading=28,
        textColor=colors.HexColor("#1e3a8a"),
        spaceAfter=6
    )
    meta_label_style = ParagraphStyle(
        "MetaLabelStyle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#4b5563")
    )
    meta_val_style = ParagraphStyle(
        "MetaValStyle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#1f2937")
    )
    company_title_style = ParagraphStyle(
        "CompanyTitleStyle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        textColor=colors.HexColor("#1f2937"),
        alignment=2  # Right-aligned
    )
    company_text_style = ParagraphStyle(
        "CompanyTextStyle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#4b5563"),
        alignment=2  # Right-aligned
    )
    section_heading_style = ParagraphStyle(
        "SectionHeadingStyle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#1e3a8a"),
        spaceAfter=4
    )
    body_text_style = ParagraphStyle(
        "BodyTextStyle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#1f2937")
    )
    table_hdr_style = ParagraphStyle(
        "TableHdrStyle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=colors.white
    )
    table_hdr_style_right = ParagraphStyle(
        "TableHdrStyleRight",
        parent=table_hdr_style,
        alignment=2
    )
    table_cell_style = ParagraphStyle(
        "TableCellStyle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#1f2937")
    )
    table_cell_style_right = ParagraphStyle(
        "TableCellStyleRight",
        parent=table_cell_style,
        alignment=2
    )

    story = []

    # 1. HEADER ROW (Document Title on left, Company Logo / info on right)
    header_left = [
        Paragraph(title.upper(), title_style),
        Paragraph(f"<b>Number:</b> {doc_number}", meta_val_style),
        Paragraph(f"<b>Date:</b> {doc_date}", meta_val_style),
    ]
    for label, val in extra_meta:
        header_left.append(Paragraph(f"<b>{label}:</b> {val}", meta_val_style))

    company_lines = [
        Paragraph(company.get("legal_name") or company.get("name") or "MY COMPANY", company_title_style),
    ]
    if company.get("address"):
        addr = company.get("address").replace("\n", ", ")
        company_lines.append(Paragraph(addr, company_text_style))
    if company.get("email"):
        company_lines.append(Paragraph(f"Email: {company.get('email')}", company_text_style))
    if company.get("phone"):
        company_lines.append(Paragraph(f"Phone: {company.get('phone')}", company_text_style))
    if company.get("tax_id"):
        company_lines.append(Paragraph(f"Tax ID: {company.get('tax_id')}", company_text_style))

    header_table = Table(
        [[header_left, company_lines]],
        colWidths=[270, 270]
    )
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(header_table)

    # Decorative horizontal line
    divider = Table([[""]], colWidths=[540])
    divider.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, -1), 1, colors.HexColor("#1e3a8a")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(divider)
    story.append(Spacer(1, 10))

    # 2. PARTNER / BILLING INFO
    partner_title = "VENDOR DETAILS" if filename_prefix == "po" else "BILL / SHIP TO"
    partner_lines = [
        Paragraph(partner_title, section_heading_style),
        Paragraph(f"<b>Name:</b> {partner.get('name')}", body_text_style),
    ]
    if partner.get("address"):
        addr_fmt = partner.get("address").replace("\r\n", "<br/>").replace("\n", "<br/>")
        partner_lines.append(Paragraph(f"<b>Address:</b><br/>{addr_fmt}", body_text_style))
    if partner.get("email"):
        partner_lines.append(Paragraph(f"<b>Email:</b> {partner.get('email')}", body_text_style))
    if partner.get("phone"):
        partner_lines.append(Paragraph(f"<b>Phone:</b> {partner.get('phone')}", body_text_style))

    company_billing = [
        Paragraph("OUR DETAILS", section_heading_style),
        Paragraph(f"<b>Company:</b> {company.get('name')}", body_text_style),
    ]
    if company.get("email"):
        company_billing.append(Paragraph(f"<b>Email:</b> {company.get('email')}", body_text_style))
    if company.get("phone"):
        company_billing.append(Paragraph(f"<b>Phone:</b> {company.get('phone')}", body_text_style))

    details_table = Table(
        [[company_billing, partner_lines]],
        colWidths=[270, 270]
    )
    details_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 15),
    ]))
    story.append(details_table)
    story.append(Spacer(1, 15))

    # 3. LINE ITEMS TABLE
    # Columns: Description (300 pt), Qty (70 pt), Unit Price (80 pt), Total (90 pt)
    table_data = [[
        Paragraph("Item / Description", table_hdr_style),
        Paragraph("Qty", table_hdr_style_right),
        Paragraph("Unit Price", table_hdr_style_right) if filename_prefix != "po" else Paragraph("Unit Cost", table_hdr_style_right),
        Paragraph("Total", table_hdr_style_right)
    ]]

    for line in lines:
        table_data.append([
            Paragraph(line.get("description", ""), table_cell_style),
            Paragraph(str(line.get("qty", "")), table_cell_style_right),
            Paragraph(str(line.get("unit_price", "")), table_cell_style_right),
            Paragraph(str(line.get("total", "")), table_cell_style_right)
        ])

    items_table = Table(table_data, colWidths=[300, 70, 80, 90])
    items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a8a")),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 15))

    # 4. TOTALS ROW (Right-aligned)
    totals_lines = []
    for label, val in totals:
        totals_lines.append([
            Paragraph(f"<b>{label}:</b>", table_cell_style_right),
            Paragraph(val, table_cell_style_right)
        ])

    totals_table = Table(totals_lines, colWidths=[120, 90])
    totals_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))

    # Wrap in two column structure to keep it pushed to the right
    totals_row = Table(
        [["", totals_table]],
        colWidths=[330, 210]
    )
    totals_row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(totals_row)

    # 5. NOTES (If any, wrapped in KeepTogether)
    if notes:
        story.append(Spacer(1, 20))
        notes_block = [
            Paragraph("NOTES / TERMS", section_heading_style),
            Paragraph(notes.replace("\n", "<br/>"), body_text_style),
        ]
        story.append(KeepTogether(notes_block))

    # Build the document
    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes

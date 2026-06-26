"""
api/export.py — Excel and PDF export builders.

Provides:
  - ExcelBuilder: generates .xlsx with KPI sheets + Issues sheet
  - (future) PdfBuilder: generates executive PDF report
"""
from __future__ import annotations

import io
from datetime import date

import structlog
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, numbers
from openpyxl.utils import get_column_letter

from storage.models import KPIResult, FactIssue

logger = structlog.get_logger(__name__)

HEADER_FILL = PatternFill(start_color="2e3248", end_color="2e3248", fill_type="solid")
HEADER_FONT = Font(bold=True, color="ffffff", size=11)
CATEGORY_FILLS = {
    "delivery": PatternFill(start_color="1a3a5c", end_color="1a3a5c", fill_type="solid"),
    "quality": PatternFill(start_color="3d1a1a", end_color="3d1a1a", fill_type="solid"),
    "risk": PatternFill(start_color="3d2e1a", end_color="3d2e1a", fill_type="solid"),
    "risk_control": PatternFill(start_color="3d2e1a", end_color="3d2e1a", fill_type="solid"),
    "data_quality": PatternFill(start_color="1a3d2e", end_color="1a3d2e", fill_type="solid"),
    "team": PatternFill(start_color="2e1a3d", end_color="2e1a3d", fill_type="solid"),
}

SHEET_CONFIG = [
    ("Delivery KPIs", "delivery"),
    ("Quality KPIs", "quality"),
    ("Risk KPIs", ("risk", "risk_control")),
    ("Data Quality KPIs", "data_quality"),
    ("Team KPIs", "team"),
]


class ExcelBuilder:
    """Builds an .xlsx workbook from KPI results and issues."""

    def __init__(self, project_key: str):
        self.project_key = project_key
        self.wb = Workbook()

    def add_kpi_sheet(self, sheet_name: str, kpis: list[KPIResult]) -> None:
        ws = self.wb.create_sheet(title=sheet_name)
        headers = ["KPI", "Period", "Value", "Previous", "Delta", "Trend", "Risk Level", "Interpretation"]
        ws.append(headers)
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col)
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = Alignment(horizontal="center")

        for i, k in enumerate(kpis, start=2):
            ws.cell(row=i, column=1, value=k.kpi_name)
            ws.cell(row=i, column=2, value=k.period_label)
            val_cell = ws.cell(row=i, column=3)
            val_cell.value = round(k.current_value, 1) if k.current_value is not None else ""
            val_cell.number_format = "#,##0.0"
            prev_cell = ws.cell(row=i, column=4)
            prev_cell.value = round(k.previous_value, 1) if k.previous_value is not None else ""
            prev_cell.number_format = "#,##0.0"
            delta_cell = ws.cell(row=i, column=5)
            delta_cell.value = round(k.delta, 2) if k.delta is not None else ""
            delta_cell.number_format = "+#,##0.00;-#,##0.00"
            ws.cell(row=i, column=6, value=k.trend.value if k.trend else "")
            ws.cell(row=i, column=7, value=k.risk_level.value if k.risk_level else "")
            ws.cell(row=i, column=8, value=k.interpretation or "")

        self._auto_width(ws)

    def add_issues_sheet(self, issues: list[FactIssue]) -> None:
        ws = self.wb.create_sheet(title="Issues")
        headers = [
            "Key", "Summary", "Type", "Status", "Priority", "Assignee",
            "Created", "Resolved", "Age (d)", "Resolution (d)",
            "Overdue", "Reopened", "Missing Assignee", "Missing Priority",
        ]
        ws.append(headers)
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col)
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = Alignment(horizontal="center")

        for i, r in enumerate(issues, start=2):
            ws.cell(row=i, column=1, value=r.jira_key)
            ws.cell(row=i, column=2, value=r.summary or "")
            ws.cell(row=i, column=3, value=r.issue_type or "")
            ws.cell(row=i, column=4, value=r.status or "")
            ws.cell(row=i, column=5, value=r.priority or "")
            ws.cell(row=i, column=6, value=r.assignee_id or "")
            ws.cell(row=i, column=7, value=r.created_date.isoformat() if r.created_date else "")
            ws.cell(row=i, column=8, value=r.resolved_date.isoformat() if r.resolved_date else "")
            ws.cell(row=i, column=9, value=r.age_days or "")
            res_cell = ws.cell(row=i, column=10)
            res_cell.value = round(r.resolution_time_days, 1) if r.resolution_time_days else ""
            res_cell.number_format = "#,##0.0"
            ws.cell(row=i, column=11, value="Yes" if r.is_overdue else "No")
            ws.cell(row=i, column=12, value=r.times_reopened or 0)
            ws.cell(row=i, column=13, value="Yes" if r.dq_missing_assignee else "No")
            ws.cell(row=i, column=14, value="Yes" if r.dq_missing_priority else "No")

        self._auto_width(ws)

    @staticmethod
    def _auto_width(ws) -> None:
        for col_cells in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col_cells[0].column)
            for cell in col_cells:
                val = str(cell.value or "")
                max_len = max(max_len, len(val))
            ws.column_dimensions[col_letter].width = min(max_len + 3, 50)

    def build(self) -> io.BytesIO:
        # Remove default sheet
        if "Sheet" in self.wb.sheetnames:
            del self.wb["Sheet"]
        buf = io.BytesIO()
        self.wb.save(buf)
        buf.seek(0)
        return buf


async def build_xlsx(
    project_key: str,
    kpis: list[KPIResult],
    issues: list[FactIssue],
) -> io.BytesIO:
    """Build an .xlsx workbook with KPI category sheets + Issues sheet."""
    builder = ExcelBuilder(project_key)

    # Group KPIs by category
    from collections import defaultdict
    by_cat: dict[str, list[KPIResult]] = defaultdict(list)
    for k in kpis:
        by_cat[k.kpi_category].append(k)

    for sheet_name, cat_key in SHEET_CONFIG:
        if isinstance(cat_key, tuple):
            sheet_kpis = []
            for ck in cat_key:
                sheet_kpis.extend(by_cat.get(ck, []))
        else:
            sheet_kpis = by_cat.get(cat_key, [])
        if sheet_kpis:
            builder.add_kpi_sheet(sheet_name, sheet_kpis)

    if issues:
        builder.add_issues_sheet(issues)

    return builder.build()

from __future__ import annotations

from typing import Any

from .documents import parse_pdf
from .financials import FinancialRecord
from .valuation import ValuationAssumptions, run_valuation_review


def run_agent(
    records: list[FinancialRecord],
    assumptions: ValuationAssumptions,
    pdf_files: list[tuple[str, bytes]],
    paddle_token: str | None = None,
    paddle_model: str = "PaddleOCR-VL-1.6",
) -> dict[str, Any]:
    """One bounded orchestrator: parse evidence, validate, value, and assemble review notes."""
    document_results = [
        parse_pdf(name, content, paddle_token=paddle_token, paddle_model=paddle_model)
        for name, content in pdf_files
    ]
    analysis = run_valuation_review(records, assumptions)
    analysis["documents"] = document_results
    analysis["agent_steps"] = [
        "1. 按文件类型分流：结构化表格进入财务字段解析；PDF 进入文本层读取或 PaddleOCR API。",
        "2. 运行必填项、期间、利润率、现金流和估值参数校验；保留工作表/单元格来源。",
        "3. 仅在关键输入通过检查后运行 DCF 与 EV/EBITDA；计算完全由本地确定性函数完成。",
        "4. 汇总异常和待复核事项，输出可下载的初步估值工作底稿。",
    ]
    analysis["scope_note"] = "该智能体只做估值预审与工作底稿草稿；不会作出投资决策或出具正式评估报告。"
    if document_results and any(item.get("text") for item in document_results):
        analysis["warnings"].append({
            "severity": "信息",
            "message": "PDF 文本是材料证据，不会直接覆盖结构化财务表；请人工把确认后的数字录入财务表并核对页码。",
        })
    return analysis

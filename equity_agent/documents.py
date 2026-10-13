from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any


def _plain(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _plain(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item, depth + 1) for item in value]
    for method_name in ("model_dump", "to_dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                return _plain(method(), depth + 1)
            except Exception:
                pass
    if hasattr(value, "__dict__"):
        return _plain(vars(value), depth + 1)
    return str(value)


def _collect_text(value: Any, key: str = "") -> list[str]:
    chunks: list[str] = []
    if isinstance(value, dict):
        for child_key, child in value.items():
            if isinstance(child, str) and any(word in child_key.lower() for word in ("markdown", "text", "content", "html")):
                if child.strip():
                    chunks.append(child.strip())
            else:
                chunks.extend(_collect_text(child, str(child_key)))
    elif isinstance(value, list):
        for child in value:
            chunks.extend(_collect_text(child, key))
    elif isinstance(value, str) and any(word in key.lower() for word in ("markdown", "text", "content", "html")):
        chunks.append(value.strip())
    return chunks


def parse_pdf(
    filename: str,
    content: bytes,
    paddle_token: str | None = None,
    paddle_model: str = "PaddleOCR-VL-1.6",
) -> dict[str, Any]:
    """Read text locally first, falling back to the hosted PaddleOCR API for scans."""
    try:
        import fitz

        with fitz.open(stream=content, filetype="pdf") as document:
            pages = [page.get_text("text").strip() for page in document]
            page_count = len(pages)
        native_text = "\n\n".join(f"[第 {index + 1} 页]\n{text}" for index, text in enumerate(pages) if text)
    except Exception as exc:
        return {"name": filename, "method": "本地 PDF 读取失败", "text": "", "warning": str(exc)}

    if len(native_text.strip()) >= 80:
        return {"name": filename, "method": "本地 PDF 文本层（PyMuPDF）", "text": native_text[:30000], "pages": page_count}

    if page_count > 1000:
        return {
            "name": filename,
            "method": "PDF 超出单文件处理上限",
            "text": native_text[:30000],
            "pages": page_count,
            "warning": "为遵守 PaddleOCR 官方 API 的单文件页数限制，本文件不会发送到远端；请拆分后再处理。",
        }

    # Remote parsing is opt-in at the UI for each run; never read a process token here.
    token = (paddle_token or "").strip()
    if not token:
        return {
            "name": filename,
            "method": "扫描件待 OCR",
            "text": native_text,
            "pages": page_count,
            "warning": "当前 PDF 没有可用文本层。本次未获授权或未配置 PADDLEOCR_ACCESS_TOKEN；文件未发送。",
        }

    temp_path = None
    client = None
    try:
        from paddleocr import PaddleOCRClient

        with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix or ".pdf", delete=False) as temp_file:
            temp_file.write(content)
            temp_path = temp_file.name
        client = PaddleOCRClient(token=token, request_timeout=300.0, poll_timeout=600.0)
        result = client.parse_document(file_path=temp_path, model=paddle_model)
        raw = _plain(result)
        text = "\n\n".join(dict.fromkeys(item for item in _collect_text(raw) if item))
        if not text:
            text = json.dumps(raw, ensure_ascii=False, indent=2)[:30000]
        return {
            "name": filename,
            "method": f"PaddleOCR 官方托管 API / {paddle_model}",
            "text": text[:30000],
            "pages": page_count,
            "job_id": getattr(result, "job_id", None),
        }
    except ImportError:
        return {
            "name": filename,
            "method": "PaddleOCR API 客户端未安装",
            "text": native_text,
            "warning": "请安装 requirements-ocr.txt 中的可选客户端后重启服务；文件未发送到远端。",
        }
    except Exception as exc:
        return {
            "name": filename,
            "method": "PaddleOCR API 调用失败",
            "text": native_text,
            "warning": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                pass

"""Explicit local OCR. Raw text is evidence to review, never a CAD constraint."""

import csv
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image


def inspect_region(image_bytes, box, *, psm=11, executable=None):
    """Read original pixels using Tesseract; return report and lossless crop bytes.

    Coordinates use the original file's top-left origin and exclusive right/bottom.
    No supplied annotation can bypass this subprocess. Failures raise explicitly.
    """
    if psm not in (6, 11):
        raise ValueError("psm must be 6 (text block) or 11 (sparse drawing text)")
    binary = executable or shutil.which("tesseract")
    if not binary:
        raise RuntimeError("找不到 Tesseract。macOS 可執行 brew install tesseract，然後重新啟動 Streamlit。")
    box = tuple(box)
    if len(box) != 4 or any(type(v) is not int for v in box):
        raise ValueError("裁切座標必須是四個整數")
    with Image.open(io.BytesIO(image_bytes)) as image:
        width, height = image.size
        x0, y0, x1, y1 = box
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
            raise ValueError("裁切範圍超出原圖，或寬高為零")
        crop = image.crop(box).convert("RGB")
    buffer = io.BytesIO()
    crop.save(buffer, format="PNG")
    crop_bytes = buffer.getvalue()
    with tempfile.TemporaryDirectory(prefix="cad3dify_ocr_") as directory:
        path = Path(directory) / "crop.png"
        path.write_bytes(crop_bytes)
        command = [str(binary), str(path), "stdout", "-l", "eng", "--psm", str(psm), "tsv"]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=True)
            version = subprocess.run([str(binary), "--version"], capture_output=True, text=True,
                                     timeout=10, check=True).stdout.splitlines()[0]
        except (OSError, subprocess.SubprocessError) as exc:
            detail = getattr(exc, "stderr", None) or str(exc)
            raise RuntimeError(f"Tesseract OCR 執行失敗：{detail}") from exc
    lines = {}
    reader = csv.DictReader(io.StringIO(result.stdout), delimiter="\t", quoting=csv.QUOTE_NONE)
    if not reader.fieldnames or "text" not in reader.fieldnames:
        raise RuntimeError("Tesseract 未傳回有效 TSV")
    for row in reader:
        text = row["text"].strip()
        if not text or int(row["level"]) != 5:
            continue
        left, top, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
        key = tuple(row[k] for k in ("page_num", "block_num", "par_num", "line_num"))
        lines.setdefault(key, []).append({"text": text, "confidence": float(row["conf"]),
                                         "source_box": [x0+left, y0+top, x0+left+w, y0+top+h]})
    observations = []
    for words in lines.values():
        boxes = [word["source_box"] for word in words]
        observations.append({
            "id": len(observations), "raw_text": " ".join(word["text"] for word in words),
            "confidence": min(word["confidence"] for word in words),
            "source_box": [min(b[0] for b in boxes), min(b[1] for b in boxes),
                           max(b[2] for b in boxes), max(b[3] for b in boxes)],
            "words": words,
        })
    return {"source_sha256": hashlib.sha256(image_bytes).hexdigest(),
            "source_size": [width, height], "crop_box": list(box),
            "crop_sha256": hashlib.sha256(crop_bytes).hexdigest(),
            "engine": version, "psm": psm, "language": "eng", "ocr_executed": True,
            "status": "text_found" if observations else "no_text",
            "stderr": result.stderr.strip(), "observations": observations,
            "note": "OCR 信心值不是尺寸正確率；原文可能漏掉直徑符號、角度或上下公差，須對照原圖確認。"}, crop_bytes


def reviewed_context(report, reviews, image_bytes):
    """Only explicitly confirmed, source-matched rows reach the original generator."""
    if report["source_sha256"] != hashlib.sha256(image_bytes).hexdigest():
        raise ValueError("OCR 來源與目前圖片不一致，請重新辨識")
    originals = {item["id"]: item for item in report["observations"]}
    accepted = []
    for review in reviews:
        if review.get("confirmed") is not True:
            continue
        original = originals[review["id"]]
        text = str(review["reviewed_text"]).strip()
        if not text:
            raise ValueError("已確認的文字不可為空")
        accepted.append({"raw_text": original["raw_text"], "reviewed_text": text,
                         "source_box": original["source_box"]})
    if not accepted:
        return ""
    return json.dumps({"source_sha256": report["source_sha256"], "source_size": report["source_size"],
                       "crop_box": report["crop_box"],
                       "coordinate_system": "original image pixels, top-left origin",
                       "user_reviewed_local_text": accepted}, ensure_ascii=False)

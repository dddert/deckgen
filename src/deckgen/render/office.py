"""pptx -> pdf -> png. Сервер: LibreOffice headless (свой профиль на вызов — параллельные конвертации не
конфликтуют). macOS без LibreOffice: Microsoft PowerPoint через AppleScript (только для локальной отладки).
PNG — через pypdfium2 (pip), без системного poppler.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

_PP_LOCK = threading.Lock()          # PowerPoint — одно окно, конвертации строго по очереди
_PP_SANDBOX = Path.home() / "Library/Containers/com.microsoft.Powerpoint/Data/deckgen"


class RenderUnavailable(RuntimeError):
    pass


def backend(preferred: str = "auto", soffice_bin: str = "soffice") -> str:
    if preferred in ("none", "off"):
        return "none"
    if preferred in ("auto", "soffice") and (shutil.which(soffice_bin) or Path(soffice_bin).exists()):
        return "soffice"
    if preferred in ("auto", "powerpoint") and platform.system() == "Darwin" and Path("/Applications/Microsoft PowerPoint.app").exists():
        return "powerpoint"
    return "none"


def to_pdf(pptx: Path, out_dir: Path, preferred: str = "auto", soffice_bin: str = "soffice", timeout: int = 300) -> Path:
    b = backend(preferred, soffice_bin)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = out_dir / (pptx.stem + ".pdf")
    if b == "soffice":
        base = os.environ.get("DECKGEN_LO_PROFILE")
        if base:
            Path(base).mkdir(parents=True, exist_ok=True)
        profile = Path(tempfile.mkdtemp(prefix="lo-", dir=base or None))
        try:
            subprocess.run([soffice_bin, "--headless", "--norestore", "--nologo",
                            f"-env:UserInstallation={profile.resolve().as_uri()}",
                            "--convert-to", "pdf", "--outdir", str(out_dir), str(pptx)],
                           check=True, capture_output=True, timeout=timeout)
        finally:
            shutil.rmtree(profile, ignore_errors=True)
    elif b == "powerpoint":
        _powerpoint_pdf(pptx, pdf, timeout)
    else:
        raise RenderUnavailable("нет LibreOffice (soffice) — PDF/PNG не строятся")
    if not pdf.exists():
        raise RenderUnavailable(f"конвертер не создал {pdf}")
    return pdf


def _powerpoint_pdf(pptx: Path, pdf: Path, timeout: int) -> None:
    with _PP_LOCK:
        _PP_SANDBOX.mkdir(parents=True, exist_ok=True)
        src = _PP_SANDBOX / f"in_{os.getpid()}.pptx"
        dst = _PP_SANDBOX / f"out_{os.getpid()}.pdf"
        shutil.copy(pptx, src)
        dst.unlink(missing_ok=True)
        script = f'''
with timeout of {timeout} seconds
tell application "Microsoft PowerPoint"
  open POSIX file "{src}"
  delay 1
  save active presentation in POSIX file "{dst}" as save as PDF
  close active presentation saving no
end tell
end timeout'''
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=timeout + 30)
        shutil.move(str(dst), pdf)
        src.unlink(missing_ok=True)


def pdf_to_pngs(pdf: Path, out_dir: Path, dpi: int = 110, prefix: str | None = None) -> list[Path]:
    import pypdfium2 as pdfium
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdf))
    paths = []
    for i, page in enumerate(doc, start=1):
        p = out_dir / f"{prefix or pdf.stem}-{i:02d}.png"
        page.render(scale=dpi / 72).to_pil().save(p)
        paths.append(p)
    doc.close()
    return paths


def render_pngs(pptx: Path, out_dir: Path, preferred: str = "auto", soffice_bin: str = "soffice", dpi: int = 110) -> list[Path]:
    return pdf_to_pngs(to_pdf(pptx, out_dir, preferred, soffice_bin), out_dir, dpi, prefix="slide")

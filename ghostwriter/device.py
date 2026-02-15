"""Thin wrapper around dpt-rp1-py for pushing PDFs to a Sony DPT-RP1.

The device must already be registered (``dptrp1 register``).  This module
shells out to the ``dptrp1`` CLI so that connection / credential discovery
works the same way as the standalone tool.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


REMOTE_ROOT = "Document/Ghostwriter"


class DeviceError(Exception):
    """Raised when a device operation fails."""


def _dptrp1_bin() -> str:
    """Return the path to the dptrp1 binary, or raise if missing."""
    path = shutil.which("dptrp1")
    if path is None:
        raise DeviceError(
            "dptrp1 not found on PATH.  Install with: pip install dpt-rp1-py"
        )
    return path


def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a dptrp1 command and return the result."""
    bin_path = _dptrp1_bin()
    cmd = [bin_path, *args]
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=check,
        )
    except subprocess.TimeoutExpired as exc:
        raise DeviceError(f"dptrp1 timed out: {' '.join(cmd)}") from exc
    except subprocess.CalledProcessError as exc:
        raise DeviceError(
            f"dptrp1 failed (exit {exc.returncode}): {exc.stderr.strip()}"
        ) from exc


# ------------------------------------------------------------------
# Public helpers
# ------------------------------------------------------------------


def is_available() -> bool:
    """Return True if dptrp1 is installed and a device responds."""
    try:
        result = _run(["list-documents"], check=False)
        return result.returncode == 0
    except DeviceError:
        return False


def upload(pdf_path: str | Path, remote_dir: str = REMOTE_ROOT) -> str:
    """Upload a PDF to the reader.

    Parameters
    ----------
    pdf_path:
        Local path to the PDF file.
    remote_dir:
        Folder on the device (created if it doesn't exist).

    Returns
    -------
    The remote path of the uploaded file.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise DeviceError(f"File not found: {pdf_path}")

    remote_path = f"{remote_dir}/{pdf_path.name}"

    # Ensure the target directory exists (mkdir is idempotent).
    _run(["mkdir", remote_dir], check=False)

    _run(["upload", str(pdf_path), remote_path])
    return remote_path


def list_poems(remote_dir: str = REMOTE_ROOT) -> list[str]:
    """List PDFs in the Ghostwriter folder on the device."""
    try:
        result = _run(["list-documents"])
    except DeviceError:
        return []

    prefix = remote_dir + "/"
    return [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().startswith(prefix)
    ]


def delete(remote_path: str) -> None:
    """Delete a document from the reader."""
    _run(["delete-document", remote_path])

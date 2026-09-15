"""Runtime patch for the graphon ``document_extractor`` registry.

graphon 0.4.0 (the release pinned by ``pyproject.toml``) ships a
``DocumentExtractorNode`` whose text-extractor registry is missing ``.wps``
and ``.et`` (modern WPS Office) entries. When a workflow hits the document
extractor node with one of these extensions it raises
``UnsupportedFileTypeError: Unsupported Extension Type: .wps`` and the node
fails.

Earlier graphon releases (<0.4) shipped a ``_convert_office_file_via_libreoffice``
helper plus matching ``.wps``/``.et`` registrations; 0.4.0 dropped both in
favour of ``pypandoc``. We re-add ``.wps``/``.et`` support at runtime by:

* registering a new ``_ExtractorRegistration`` for each extension,
* routing the registered extractor through LibreOffice (verified working
  with ``LibreOffice 7.4.7.2`` against real ``.wps`` payloads in the
  deployment image — ``--convert-to docx`` auto-detects WPS Office as
  ``MS Word 2007 XML`` via content sniffing),
* delegating to graphon's existing ``_extract_text_from_docx`` /
  ``_extract_text_from_excel`` so we don't reimplement parsing.

LibreOffice's dconf-based profile path requires an explicit per-call profile
directory and ``$HOME`` override because the dify Docker user has no
writable home. Without these, LibreOffice fails with the misleading
"User installation could not be completed" + "Unsupported Extension Type"
pair.

The patch is idempotent: ``install()`` is a no-op on subsequent calls.
"""
from __future__ import annotations

import logging
import os
import pathlib
import shutil
import subprocess
import tempfile
from collections.abc import Callable

logger = logging.getLogger(__name__)

# How long a single LibreOffice invocation may run before we abort.
_TIMEOUT_SECONDS = 30


class _MissingSentinel:
    """Sentinel distinct from ``None`` so callers can request "look up the
    default infilter for this suffix" vs "explicitly use no infilter"."""

    pass


_MISSING: _MissingSentinel = _MissingSentinel()


def _build_command(
    *,
    source_suffix: str,
    target_format: str,
    profile_dir: str,
    input_path: str,
    temp_dir: str,
    infilter: str | None | _MissingSentinel = _MISSING,
) -> tuple[list[str], str | None]:
    """Compose the libreoffice argv and return ``(cmd, infilter_or_none)``.

    For ``.wps``/``.et`` we deliberately do NOT pass an infilter — modern
    WPS Office documents are ZIP/OXML containers that LibreOffice
    auto-detects by magic bytes. The old ``MS_Works`` filter (for the
    long-discontinued Microsoft Works word processor, 1986–2007) rejects
    them. The auto-detection picks the right filter (``MS Word 2007 XML``
    for ``.wps``, ``Calc Office Open XML`` for ``.et``).

    The infilter argument is kept for symmetry with the legacy path —
    ``.doc`` still benefits from ``MS Word 97`` because Word 97/95/Works
    are genuinely ambiguous.
    """
    if isinstance(infilter, _MissingSentinel):
        # Default: only pin `.doc`. `.wps` / `.et` rely on content sniffing.
        infilter = {".doc": "MS Word 97"}.get(source_suffix)
    cmd = [
        "libreoffice",
        f"-env:UserInstallation=file://{profile_dir}",
        "--headless",
        "--convert-to",
        target_format,
    ]
    if infilter is not None:
        cmd.append(f"--infilter={infilter}")
    cmd.extend(["--outdir", temp_dir, input_path])
    return cmd, infilter


def _convert_via_libreoffice(
    file_content: bytes,
    *,
    source_suffix: str,
    target_format: str,
) -> bytes:
    """Convert ``file_content`` from ``source_suffix`` to ``target_format`` via headless LibreOffice.

    Raises:
        RuntimeError: ``libreoffice`` is missing from PATH.
        ValueError: conversion timed out, LibreOffice returned non-zero,
            or no output file was produced.

    Returns the converted bytes.
    """
    profile_dir = tempfile.mkdtemp(prefix="lo_profile_graphon_")
    env = os.environ.copy()
    env["HOME"] = profile_dir
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = os.path.join(temp_dir, f"input{source_suffix}")
            pathlib.Path(input_path).write_bytes(file_content)

            cmd, _ = _build_command(
                source_suffix=source_suffix,
                target_format=target_format,
                profile_dir=profile_dir,
                input_path=input_path,
                temp_dir=temp_dir,
            )

            try:
                subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    timeout=_TIMEOUT_SECONDS,
                    env=env,
                )
            except subprocess.CalledProcessError as e:
                stderr = e.stderr.decode("utf-8", errors="ignore") if e.stderr else "no error details"
                raise ValueError(
                    f"LibreOffice failed to convert {source_suffix} to .{target_format}: {stderr}"
                ) from e
            except subprocess.TimeoutExpired as e:
                raise ValueError(
                    f"LibreOffice conversion to .{target_format} timed out after {_TIMEOUT_SECONDS}s "
                    f"({source_suffix})"
                ) from e
            except FileNotFoundError as e:
                raise RuntimeError(
                    "libreoffice binary not found; install libreoffice-writer/libreoffice-calc "
                    "in api/Dockerfile to enable .wps/.et/.doc support"
                ) from e

            # Prefer the expected name; fall back to a glob if LibreOffice
            # renamed the output (some versions follow the source stem).
            expected = os.path.join(temp_dir, f"input.{target_format}")
            if os.path.exists(expected):
                return pathlib.Path(expected).read_bytes()
            for candidate in os.listdir(temp_dir):
                if candidate.endswith(f".{target_format}"):
                    return pathlib.Path(os.path.join(temp_dir, candidate)).read_bytes()
            raise ValueError(
                f"LibreOffice produced no .{target_format} output for {source_suffix} input"
            )
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)


def _make_extractors() -> tuple[Callable[[bytes], str], Callable[[bytes], str]]:
    """Build the ``.wps`` and ``.et`` extractor callables.

    Imports graphon's parse helpers lazily so this module can be imported
    even if graphon's document_extractor module is missing.
    """
    from graphon.nodes.document_extractor import node as gn

    def _extract_text_from_wps(file_content: bytes) -> str:
        """Convert ``.wps`` → ``.docx`` via LibreOffice, then reuse graphon's docx parser."""
        docx_bytes = _convert_via_libreoffice(
            file_content, source_suffix=".wps", target_format="docx"
        )
        return gn._extract_text_from_docx(docx_bytes)

    def _extract_text_from_et(file_content: bytes) -> str:
        """Convert ``.et`` → ``.xlsx`` via LibreOffice, then reuse graphon's excel parser."""
        xlsx_bytes = _convert_via_libreoffice(
            file_content, source_suffix=".et", target_format="xlsx"
        )
        return gn._extract_text_from_excel(xlsx_bytes)

    return _extract_text_from_wps, _extract_text_from_et


def install() -> None:
    """Register ``.wps`` / ``.et`` handlers in graphon's document_extractor registry.

    Safe to call multiple times: ``_dify_wps_patch_installed`` short-circuits
    subsequent calls.
    """
    from graphon.nodes.document_extractor import node as _graphon_node

    if getattr(_graphon_node, "_dify_wps_patch_installed", False):
        return

    _wps_extractor, _et_extractor = _make_extractors()

    registry = _graphon_node._TEXT_EXTRACTOR_REGISTRY
    ext_dict = registry._file_extension_extractors
    mime_dict = registry._mime_type_extractors

    if ".wps" in ext_dict and ".et" in ext_dict:
        # Upstream may have re-added support in a future release; no work to do.
        _graphon_node._dify_wps_patch_installed = True
        logger.info(
            "graphon document_extractor already supports .wps/.et; skipping patch install."
        )
        return

    from graphon.nodes.document_extractor.node import _ExtractorRegistration

    if ".wps" not in ext_dict:
        wps_reg = _ExtractorRegistration(
            name="wps",
            extractor=_wps_extractor,
            mime_types=frozenset({"application/wps-office.wps", "application/kswps"}),
            file_extensions=frozenset({".wps"}),
        )
        ext_dict[".wps"] = wps_reg
        mime_dict["application/wps-office.wps"] = wps_reg
        mime_dict["application/kswps"] = wps_reg

    if ".et" not in ext_dict:
        et_reg = _ExtractorRegistration(
            name="et",
            extractor=_et_extractor,
            mime_types=frozenset({"application/wps-office.et", "application/kset"}),
            file_extensions=frozenset({".et"}),
        )
        ext_dict[".et"] = et_reg
        mime_dict["application/wps-office.et"] = et_reg
        mime_dict["application/kset"] = et_reg

    _graphon_node._dify_wps_patch_installed = True
    logger.info(
        "graphon WPS/LibreOffice patch installed: registered .wps (-> docx via LibreOffice) "
        "and .et (-> xlsx via LibreOffice) handlers."
    )

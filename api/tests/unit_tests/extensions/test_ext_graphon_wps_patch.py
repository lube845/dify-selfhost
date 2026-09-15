from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from extensions.ext_graphon_wps_patch import (
    _MISSING,
    _build_command,
    _convert_via_libreoffice,
    _MissingSentinel,
    install,
)


class _CompletedProcess:
    def __init__(self, *, returncode: int = 0, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stderr = stderr


@pytest.fixture
def registry_with_wps_removed():
    """Snapshot the upstream graphon registry, drop ``.wps``/``.et``, yield, then restore.

    graphon 0.4.0 already lacks these entries, but if a future release adds them
    back the patch should be a no-op. This fixture simulates the
    "patch needs to do work" state without depending on the upstream version.
    """
    from graphon.nodes.document_extractor import node as gn

    saved_ext = dict(gn._TEXT_EXTRACTOR_REGISTRY._file_extension_extractors)
    saved_mime = dict(gn._TEXT_EXTRACTOR_REGISTRY._mime_type_extractors)
    # Ensure the patch will run by stripping .wps/.et if upstream provides them.
    for ext in (".wps", ".et"):
        if ext in saved_ext:
            del gn._TEXT_EXTRACTOR_REGISTRY._file_extension_extractors[ext]
    # Restore the patch flag too so ``install()`` re-runs.
    if hasattr(gn, "_dify_wps_patch_installed"):
        del gn._dify_wps_patch_installed
    try:
        yield gn
    finally:
        gn._TEXT_EXTRACTOR_REGISTRY._file_extension_extractors.clear()
        gn._TEXT_EXTRACTOR_REGISTRY._file_extension_extractors.update(saved_ext)
        gn._TEXT_EXTRACTOR_REGISTRY._mime_type_extractors.clear()
        gn._TEXT_EXTRACTOR_REGISTRY._mime_type_extractors.update(saved_mime)
        if hasattr(gn, "_dify_wps_patch_installed"):
            del gn._dify_wps_patch_installed


def test_missing_sentinel_is_distinct_from_none():
    """The sentinel must not equal ``None`` so ``_build_command`` can tell
    "no preference" apart from "explicitly skip the infilter"."""
    assert _MISSING is not None
    assert not isinstance(None, _MissingSentinel)
    assert isinstance(_MISSING, _MissingSentinel)


def test_build_command_pins_doc_infilter_by_default():
    cmd, infilter = _build_command(
        source_suffix=".doc",
        target_format="docx",
        profile_dir="/tmp/profile",
        input_path="/tmp/x.doc",
        temp_dir="/tmp/x",
    )
    assert "--infilter=MS Word 97" in cmd
    assert infilter == "MS Word 97"


def test_build_command_omits_infilter_for_wps_by_default():
    cmd, infilter = _build_command(
        source_suffix=".wps",
        target_format="docx",
        profile_dir="/tmp/profile",
        input_path="/tmp/x.wps",
        temp_dir="/tmp/x",
    )
    assert not any(arg.startswith("--infilter") for arg in cmd)
    assert infilter is None


def test_build_command_omits_infilter_for_et_by_default():
    cmd, infilter = _build_command(
        source_suffix=".et",
        target_format="xlsx",
        profile_dir="/tmp/profile",
        input_path="/tmp/x.et",
        temp_dir="/tmp/x",
    )
    assert not any(arg.startswith("--infilter") for arg in cmd)
    assert infilter is None


def test_build_command_explicit_none_overrides_default_lookup():
    """Passing ``infilter=None`` explicitly must produce no infilter even for
    extensions that would otherwise get one (e.g. ``.doc``)."""
    cmd, infilter = _build_command(
        source_suffix=".doc",
        target_format="docx",
        profile_dir="/tmp/profile",
        input_path="/tmp/x.doc",
        temp_dir="/tmp/x",
        infilter=None,
    )
    assert not any(arg.startswith("--infilter") for arg in cmd)
    assert infilter is None


def test_convert_via_libreoffice_happy_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    captured: dict[str, list[str]] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        out_dir = cmd[cmd.index("--outdir") + 1]
        Path(out_dir, "input.docx").write_bytes(b"converted-docx")
        return _CompletedProcess()

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = _convert_via_libreoffice(b"fake-wps", source_suffix=".wps", target_format="docx")
    assert result == b"converted-docx"
    cmd = captured["cmd"]
    assert "libreoffice" in cmd[0]
    assert "--headless" in cmd
    assert "--convert-to" in cmd
    assert "docx" in cmd
    # ``.wps`` must not get an infilter — content sniffing handles it.
    assert not any(arg.startswith("--infilter") for arg in cmd)


def test_convert_via_libreoffice_falls_back_to_glob_when_output_renamed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Some LibreOffice versions name the output after the source stem rather
    than the literal ``input`` we wrote. The glob fallback must catch that."""

    def fake_run(cmd, **kwargs):
        out_dir = cmd[cmd.index("--outdir") + 1]
        Path(out_dir, "renamed.docx").write_bytes(b"converted")
        return _CompletedProcess()

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = _convert_via_libreoffice(b"fake", source_suffix=".wps", target_format="docx")
    assert result == b"converted"


def test_convert_via_libreoffice_wraps_called_process_error(
    monkeypatch: pytest.MonkeyPatch,
):
    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(
            returncode=1,
            cmd=cmd,
            stderr=b"unsupported file format",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ValueError, match=r"LibreOffice failed to convert \.wps to \.docx"):
        _convert_via_libreoffice(b"fake", source_suffix=".wps", target_format="docx")


def test_convert_via_libreoffice_wraps_timeout(monkeypatch: pytest.MonkeyPatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ValueError, match=r"timed out after 30s \(\.wps\)"):
        _convert_via_libreoffice(b"fake", source_suffix=".wps", target_format="docx")


def test_convert_via_libreoffice_wraps_missing_binary(monkeypatch: pytest.MonkeyPatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="libreoffice binary not found"):
        _convert_via_libreoffice(b"fake", source_suffix=".wps", target_format="docx")


def test_convert_via_libreoffice_raises_when_no_output(monkeypatch: pytest.MonkeyPatch):
    def fake_run(cmd, **kwargs):
        # Succeed but produce no output file.
        return _CompletedProcess()

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ValueError, match=r"produced no \.docx output"):
        _convert_via_libreoffice(b"fake", source_suffix=".wps", target_format="docx")


def test_install_registers_wps_and_et_in_registry(registry_with_wps_removed):
    """The core behaviour: ``install()`` must register ``.wps`` and ``.et``
    extractors in graphon's document_extractor registry."""
    from graphon.nodes.document_extractor import node as gn

    install()

    ext_dict = gn._TEXT_EXTRACTOR_REGISTRY._file_extension_extractors
    mime_dict = gn._TEXT_EXTRACTOR_REGISTRY._mime_type_extractors
    assert ".wps" in ext_dict
    assert ".et" in ext_dict
    assert "application/wps-office.wps" in mime_dict
    assert "application/wps-office.et" in mime_dict
    assert getattr(gn, "_dify_wps_patch_installed", False) is True


def test_install_is_idempotent(registry_with_wps_removed, caplog: pytest.LogCaptureFixture):
    """Calling ``install()`` twice must not double-register."""
    from graphon.nodes.document_extractor import node as gn

    with caplog.at_level(logging.INFO):
        install()
        first_wps_extractor = gn._TEXT_EXTRACTOR_REGISTRY._file_extension_extractors[".wps"].extractor
        install()
        second_wps_extractor = gn._TEXT_EXTRACTOR_REGISTRY._file_extension_extractors[".wps"].extractor

    assert first_wps_extractor is second_wps_extractor


def test_install_is_noop_when_upstream_already_supports_wps(
    monkeypatch: pytest.MonkeyPatch,
):
    """If a future graphon release adds ``.wps`` back, ``install()`` must
    detect that and skip work rather than re-registering."""
    from graphon.nodes.document_extractor import node as gn

    # Pretend upstream already has .wps/.et by injecting sentinel registrations.
    class _NoopExtractor:
        def __call__(self, file_content: bytes) -> str:
            return "noop"

    sentinel = type(
        "_Reg",
        (),
        {
            "name": "wps",
            "extractor": _NoopExtractor(),
            "mime_types": frozenset(),
            "file_extensions": frozenset({".wps"}),
        },
    )()
    gn._TEXT_EXTRACTOR_REGISTRY._file_extension_extractors[".wps"] = sentinel
    gn._TEXT_EXTRACTOR_REGISTRY._file_extension_extractors[".et"] = sentinel
    if hasattr(gn, "_dify_wps_patch_installed"):
        del gn._dify_wps_patch_installed

    install()

    # Should still be the noop sentinel (not our LibreOffice extractor).
    assert (
        gn._TEXT_EXTRACTOR_REGISTRY._file_extension_extractors[".wps"] is sentinel
    )
    assert getattr(gn, "_dify_wps_patch_installed", False) is True

    # Cleanup
    del gn._TEXT_EXTRACTOR_REGISTRY._file_extension_extractors[".wps"]
    del gn._TEXT_EXTRACTOR_REGISTRY._file_extension_extractors[".et"]


def test_registered_wps_extractor_runs_libreoffice_then_docx_parser(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    registry_with_wps_removed,
):
    """End-to-end through the registry: the registered ``.wps`` extractor
    must invoke LibreOffice on the input bytes and pass the converted
    ``.docx`` bytes to graphon's docx parser."""
    from graphon.nodes.document_extractor import node as gn
    from graphon.nodes.document_extractor.entities import UnstructuredApiConfig

    calls: list[tuple[bytes, str]] = []

    def fake_run(cmd, **kwargs):
        out_dir = cmd[cmd.index("--outdir") + 1]
        Path(out_dir, "input.docx").write_bytes(b"converted-docx-bytes")
        return _CompletedProcess()

    def fake_docx_parser(file_content: bytes) -> str:
        calls.append((file_content, "docx"))
        return f"parsed({file_content.decode()})"

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(gn, "_extract_text_from_docx", fake_docx_parser)

    install()

    wps_ext = gn._TEXT_EXTRACTOR_REGISTRY._file_extension_extractors[".wps"]
    text = wps_ext.extract(
        file_content=b"wps-bytes",
        unstructured_api_config=UnstructuredApiConfig(),
    )

    assert calls == [(b"converted-docx-bytes", "docx")]
    assert text == "parsed(converted-docx-bytes)"


def test_registered_et_extractor_runs_libreoffice_then_excel_parser(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    registry_with_wps_removed,
):
    """Same end-to-end check for ``.et`` → ``.xlsx``."""
    from graphon.nodes.document_extractor import node as gn
    from graphon.nodes.document_extractor.entities import UnstructuredApiConfig

    def fake_run(cmd, **kwargs):
        out_dir = cmd[cmd.index("--outdir") + 1]
        Path(out_dir, "input.xlsx").write_bytes(b"converted-xlsx-bytes")
        return _CompletedProcess()

    def fake_excel_parser(file_content: bytes) -> str:
        return f"excel({file_content.decode()})"

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(gn, "_extract_text_from_excel", fake_excel_parser)

    install()

    et_ext = gn._TEXT_EXTRACTOR_REGISTRY._file_extension_extractors[".et"]
    text = et_ext.extract(
        file_content=b"et-bytes",
        unstructured_api_config=UnstructuredApiConfig(),
    )

    assert text == "excel(converted-xlsx-bytes)"

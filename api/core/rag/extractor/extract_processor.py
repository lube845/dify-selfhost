import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Union
from urllib.parse import unquote

logger = logging.getLogger(__name__)

from configs import dify_config
from core.helper import ssrf_proxy
from core.rag.extractor.csv_extractor import CSVExtractor
from core.rag.extractor.entity.datasource_type import DatasourceType
from core.rag.extractor.entity.extract_setting import ExtractSetting
from core.rag.extractor.excel_extractor import ExcelExtractor
from core.rag.extractor.extractor_base import BaseExtractor
from core.rag.extractor.firecrawl.firecrawl_web_extractor import FirecrawlWebExtractor
from core.rag.extractor.html_extractor import HtmlExtractor
from core.rag.extractor.jina_reader_extractor import JinaReaderWebExtractor
from core.rag.extractor.markdown_extractor import MarkdownExtractor
from core.rag.extractor.notion_extractor import NotionExtractor
from core.rag.extractor.pdf_extractor import PdfExtractor
from core.rag.extractor.text_extractor import TextExtractor
from core.rag.extractor.unstructured.unstructured_eml_extractor import UnstructuredEmailExtractor
from core.rag.extractor.unstructured.unstructured_epub_extractor import UnstructuredEpubExtractor
from core.rag.extractor.unstructured.unstructured_markdown_extractor import UnstructuredMarkdownExtractor
from core.rag.extractor.unstructured.unstructured_msg_extractor import UnstructuredMsgExtractor
from core.rag.extractor.unstructured.unstructured_ppt_extractor import UnstructuredPPTExtractor
from core.rag.extractor.unstructured.unstructured_pptx_extractor import UnstructuredPPTXExtractor
from core.rag.extractor.unstructured.unstructured_xml_extractor import UnstructuredXmlExtractor
from core.rag.extractor.watercrawl.extractor import WaterCrawlWebExtractor
from core.rag.extractor.word_extractor import WordExtractor
from core.rag.models.document import Document
from extensions.ext_storage import storage
from models.model import UploadFile

SUPPORT_URL_CONTENT_TYPES = ["application/pdf", "text/plain", "application/json"]
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124"
    " Safari/537.36"
)


def _run_libreoffice_subprocess(
    file_path: str,
    convert_dir: str,
    target_ext: str,
    file_extension: str,
) -> None:
    """Run `libreoffice --headless --convert-to` and surface a clean error.

    The caller owns ``convert_dir`` (a temporary directory it has created) and is
    expected to read the produced ``<basename>.<target_ext>`` afterwards.

    The dify Docker image runs as user ``dify`` whose ``$HOME=/home/dify`` does
    not exist (the Dockerfile uses ``useradd -r`` without ``-m``). Without an
    explicit profile, LibreOffice attempts ``$HOME/.cache/dconf`, fails with
    "User installation could not be completed", and then surfaces a misleading
    "Unsupported Extension Type" error for every input. We pin LibreOffice to
    a fresh, writable, per-call profile directory and override ``$HOME`` for
    the subprocess to bypass that.

    For ambiguous extensions like ``.doc`` (Word 97 vs Word 95 vs Works) we
    pass ``--infilter=MS Word 97``; if the hinted conversion fails, we retry
    once without the infilter so LibreOffice can fall back to content-based
    auto-detection. ``.wps`` and ``.et`` (modern WPS Office) are NOT pinned —
    ``MS_Works`` only handles the old Microsoft Works format (1986–2007) and
    rejects modern WPS Office files, which are ZIP/OXML containers LibreOffice
    detects by magic bytes.

    Raises:
        ValueError: conversion timed out, the LibreOffice CLI returned non-zero,
            or the expected output file is missing.
        RuntimeError: the ``libreoffice`` binary is missing from PATH/image.
    """
    import shutil as _shutil

    # Only `.doc` is genuinely ambiguous; `.wps` and `.et` should rely on
    # content sniffing rather than a forced (and wrong) infilter.
    infilter_for: dict[str, str] = {
        ".doc": "MS Word 97",
    }
    infilter = infilter_for.get(file_extension)

    profile_dir = tempfile.mkdtemp(prefix="lo_profile_")
    env = os.environ.copy()
    env["HOME"] = profile_dir
    try:
        cmd = [
            "libreoffice",
            f"-env:UserInstallation=file://{profile_dir}",
            "--headless",
            "--convert-to",
            target_ext,
        ]
        if infilter is not None:
            cmd.append(f"--infilter={infilter}")
        cmd.extend(["--outdir", convert_dir, file_path])

        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=30, env=env)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode("utf-8", errors="ignore") if e.stderr else ""
            if infilter is not None:
                # Retry without the infilter; LibreOffice's content sniffer is
                # more reliable for ambiguous inputs than its extension-driven
                # dispatch.
                logger.info(
                    "LibreOffice %s -> .%s failed with infilter=%r (%s); retrying without infilter",
                    file_extension,
                    target_ext,
                    infilter,
                    stderr.strip(),
                )
                cmd_no_infilter = [
                    "libreoffice",
                    f"-env:UserInstallation=file://{profile_dir}",
                    "--headless",
                    "--convert-to",
                    target_ext,
                    "--outdir",
                    convert_dir,
                    file_path,
                ]
                try:
                    subprocess.run(
                        cmd_no_infilter, check=True, capture_output=True, timeout=30, env=env
                    )
                except subprocess.CalledProcessError as retry_err:
                    retry_stderr = (
                        retry_err.stderr.decode("utf-8", errors="ignore") if retry_err.stderr else ""
                    )
                    raise ValueError(
                        f"LibreOffice failed to convert {file_extension} to .{target_ext} "
                        f"(with and without infilter={infilter!r}): "
                        f"first attempt: {stderr}; retry: {retry_stderr}"
                    ) from retry_err
            else:
                raise ValueError(
                    f"LibreOffice failed to convert {file_extension} to .{target_ext}: {stderr}"
                ) from e
        except subprocess.TimeoutExpired as e:
            raise ValueError(
                f"LibreOffice conversion to .{target_ext} timed out after 30s ({file_extension})"
            ) from e
    except FileNotFoundError as e:
        raise RuntimeError(
            "libreoffice binary not found in container; install libreoffice-writer/libreoffice-calc in api/Dockerfile"
        ) from e
    finally:
        _shutil.rmtree(profile_dir, ignore_errors=True)
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    produced = os.path.join(convert_dir, f"{base_name}.{target_ext}")
    if not os.path.exists(produced):
        # Some LibreOffice versions name the output after a different stem.
        # Try one glob fallback before giving up.
        for candidate in os.listdir(convert_dir):
            if candidate.endswith(f".{target_ext}"):
                return
        raise ValueError(
            f"LibreOffice did not produce .{target_ext} output for {file_path} ({file_extension})"
        )


class ExtractProcessor:
    @classmethod
    def load_from_upload_file(
        cls, upload_file: UploadFile, return_text: bool = False, is_automatic: bool = False
    ) -> Union[list[Document], str]:
        extract_setting = ExtractSetting(
            datasource_type=DatasourceType.FILE, upload_file=upload_file, document_model="text_model"
        )
        if return_text:
            delimiter = "\n"
            return delimiter.join([document.page_content for document in cls.extract(extract_setting, is_automatic)])
        else:
            return cls.extract(extract_setting, is_automatic)

    @classmethod
    def load_from_url(cls, url: str, return_text: bool = False) -> Union[list[Document], str]:
        response = ssrf_proxy.get(url, headers={"User-Agent": USER_AGENT})

        with tempfile.TemporaryDirectory() as temp_dir:
            suffix = Path(url).suffix
            if not suffix and suffix != ".":
                # get content-type
                if response.headers.get("Content-Type"):
                    suffix = "." + response.headers.get("Content-Type").split("/")[-1]
                else:
                    content_disposition = response.headers.get("Content-Disposition")
                    filename_match = re.search(r'filename="([^"]+)"', content_disposition)
                    if filename_match:
                        filename = unquote(filename_match.group(1))
                        match = re.search(r"\.(\w+)$", filename)
                        if match:
                            suffix = "." + match.group(1)
                        else:
                            suffix = ""
            # https://stackoverflow.com/questions/26541416/generate-temporary-file-names-without-creating-actual-file-in-python#comment90414256_26541521
            # Generate a temporary filename under the created temp_dir and ensure the directory exists
            file_path = f"{temp_dir}/{next(tempfile._get_candidate_names())}{suffix}"  # type: ignore
            Path(file_path).write_bytes(response.content)
            extract_setting = ExtractSetting(datasource_type=DatasourceType.FILE, document_model="text_model")
            if return_text:
                delimiter = "\n"
                return delimiter.join(
                    [
                        document.page_content
                        for document in cls.extract(extract_setting=extract_setting, file_path=file_path)
                    ]
                )
            else:
                return cls.extract(extract_setting=extract_setting, file_path=file_path)

    @classmethod
    def extract(
        cls, extract_setting: ExtractSetting, is_automatic: bool = False, file_path: str | None = None
    ) -> list[Document]:
        if extract_setting.datasource_type == DatasourceType.FILE:
            upload_file = extract_setting.upload_file
            with tempfile.TemporaryDirectory() as temp_dir:
                upload_file = extract_setting.upload_file
                if not file_path:
                    assert upload_file is not None, "upload_file is required"
                    suffix = Path(upload_file.key).suffix
                    # FIXME mypy: Cannot determine type of 'tempfile._get_candidate_names' better not use it here
                    file_path = f"{temp_dir}/{next(tempfile._get_candidate_names())}{suffix}"  # type: ignore
                    storage.download(upload_file.key, file_path)
                input_file = Path(file_path)
                file_extension = input_file.suffix.lower()
                assert upload_file is not None, "upload_file is required"
                etl_type = dify_config.ETL_TYPE
                extractor: BaseExtractor | None = None

                # Normalize legacy Office / WPS formats (.doc, .wps, .et) to OOXML via
                # LibreOffice so the dispatcher below only deals with .docx / .xlsx.
                # ``legacy_tmpdir`` is held until ``extractor.extract()`` returns so the
                # converted output file remains on disk while WordExtractor/ExcelExtractor
                # reads it.
                legacy_tmpdir: tempfile.TemporaryDirectory | None = None
                try:
                    if file_extension in {".doc", ".wps", ".et"}:
                        target_ext = "docx" if file_extension in {".doc", ".wps"} else "xlsx"
                        legacy_tmpdir = tempfile.TemporaryDirectory()
                        convert_dir = legacy_tmpdir.__enter__()
                        try:
                            _run_libreoffice_subprocess(file_path, convert_dir, target_ext, file_extension)
                        except BaseException:
                            legacy_tmpdir.__exit__(*sys.exc_info())
                            legacy_tmpdir = None
                            raise
                        base_name = os.path.splitext(os.path.basename(file_path))[0]
                        file_path = os.path.join(convert_dir, f"{base_name}.{target_ext}")
                        file_extension = f".{target_ext}"

                    if etl_type == "Unstructured":
                        unstructured_api_url = dify_config.UNSTRUCTURED_API_URL or ""
                        unstructured_api_key = dify_config.UNSTRUCTURED_API_KEY or ""

                        if file_extension in {".xlsx", ".xls"}:
                            extractor = ExcelExtractor(file_path)
                        elif file_extension == ".pdf":
                            assert upload_file is not None
                            extractor = PdfExtractor(file_path, upload_file.tenant_id, upload_file.created_by)
                        elif file_extension in {".md", ".markdown", ".mdx"}:
                            extractor = (
                                UnstructuredMarkdownExtractor(file_path, unstructured_api_url, unstructured_api_key)
                                if is_automatic
                                else MarkdownExtractor(file_path, autodetect_encoding=True)
                            )
                        elif file_extension in {".htm", ".html"}:
                            extractor = HtmlExtractor(file_path)
                        elif file_extension == ".docx":
                            assert upload_file is not None
                            extractor = WordExtractor(file_path, upload_file.tenant_id, upload_file.created_by)
                        elif file_extension == ".csv":
                            extractor = CSVExtractor(file_path, autodetect_encoding=True)
                        elif file_extension == ".msg":
                            extractor = UnstructuredMsgExtractor(file_path, unstructured_api_url, unstructured_api_key)
                        elif file_extension == ".eml":
                            extractor = UnstructuredEmailExtractor(
                                file_path, unstructured_api_url, unstructured_api_key
                            )
                        elif file_extension == ".ppt":
                            extractor = UnstructuredPPTExtractor(file_path, unstructured_api_url, unstructured_api_key)
                            # You must first specify the API key
                            # because unstructured_api_key is necessary to parse .ppt documents
                        elif file_extension == ".pptx":
                            extractor = UnstructuredPPTXExtractor(file_path, unstructured_api_url, unstructured_api_key)
                        elif file_extension == ".xml":
                            extractor = UnstructuredXmlExtractor(file_path, unstructured_api_url, unstructured_api_key)
                        elif file_extension == ".epub":
                            extractor = UnstructuredEpubExtractor(file_path, unstructured_api_url, unstructured_api_key)
                        else:
                            # txt
                            extractor = TextExtractor(file_path, autodetect_encoding=True)
                    else:
                        if file_extension in {".xlsx", ".xls"}:
                            extractor = ExcelExtractor(file_path)
                        elif file_extension == ".pdf":
                            assert upload_file is not None
                            extractor = PdfExtractor(file_path, upload_file.tenant_id, upload_file.created_by)
                        elif file_extension in {".md", ".markdown", ".mdx"}:
                            extractor = MarkdownExtractor(file_path, autodetect_encoding=True)
                        elif file_extension in {".htm", ".html"}:
                            extractor = HtmlExtractor(file_path)
                        elif file_extension == ".docx":
                            assert upload_file is not None
                            extractor = WordExtractor(file_path, upload_file.tenant_id, upload_file.created_by)
                        elif file_extension == ".csv":
                            extractor = CSVExtractor(file_path, autodetect_encoding=True)
                        elif file_extension == ".epub":
                            extractor = UnstructuredEpubExtractor(file_path)
                        else:
                            # txt
                            extractor = TextExtractor(file_path, autodetect_encoding=True)

                    if extractor is None:
                        raise ValueError(f"No extractor matched for file_extension={file_extension!r}")
                    return extractor.extract()
                finally:
                    if legacy_tmpdir is not None:
                        legacy_tmpdir.cleanup()
        elif extract_setting.datasource_type == DatasourceType.NOTION:
            assert extract_setting.notion_info is not None, "notion_info is required"
            extractor = NotionExtractor(
                notion_workspace_id=extract_setting.notion_info.notion_workspace_id or "",
                notion_obj_id=extract_setting.notion_info.notion_obj_id,
                notion_page_type=extract_setting.notion_info.notion_page_type,
                document_model=extract_setting.notion_info.document,
                tenant_id=extract_setting.notion_info.tenant_id,
                credential_id=extract_setting.notion_info.credential_id,
            )
            return extractor.extract()
        elif extract_setting.datasource_type == DatasourceType.WEBSITE:
            assert extract_setting.website_info is not None, "website_info is required"
            match extract_setting.website_info.provider:
                case "firecrawl":
                    extractor = FirecrawlWebExtractor(
                        url=extract_setting.website_info.url,
                        job_id=extract_setting.website_info.job_id,
                        tenant_id=extract_setting.website_info.tenant_id,
                        mode=extract_setting.website_info.mode,
                        only_main_content=extract_setting.website_info.only_main_content,
                    )
                    return extractor.extract()
                case "watercrawl":
                    extractor = WaterCrawlWebExtractor(
                        url=extract_setting.website_info.url,
                        job_id=extract_setting.website_info.job_id,
                        tenant_id=extract_setting.website_info.tenant_id,
                        mode=extract_setting.website_info.mode,
                        only_main_content=extract_setting.website_info.only_main_content,
                    )
                    return extractor.extract()
                case "jinareader":
                    extractor = JinaReaderWebExtractor(
                        url=extract_setting.website_info.url,
                        job_id=extract_setting.website_info.job_id,
                        tenant_id=extract_setting.website_info.tenant_id,
                        mode=extract_setting.website_info.mode,
                        only_main_content=extract_setting.website_info.only_main_content,
                    )
                    return extractor.extract()
                case _:
                    raise ValueError(f"Unsupported website provider: {extract_setting.website_info.provider}")
        else:
            raise ValueError(f"Unsupported datasource type: {extract_setting.datasource_type}")

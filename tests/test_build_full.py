from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import tomllib
import zipfile
from pathlib import Path
from types import ModuleType

import httpx
import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_full.py"


def _load_build_full() -> ModuleType:
    """scripts/ 不是一个包（没有 __init__.py，就一个打包用的独立脚本），按路径
    动态加载，不用为了测试专门把它改造成包结构。"""
    spec = importlib.util.spec_from_file_location("build_full", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_full = _load_build_full()


def test_sha256_of_matches_hashlib(tmp_path: Path):
    content = b"hello rpg translator"
    path = tmp_path / "f.bin"
    path.write_bytes(content)

    assert build_full.sha256_of(path) == hashlib.sha256(content).hexdigest()


def _mock_client(content: bytes, status_code: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=content)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_writes_content_when_no_expected_checksum(tmp_path: Path):
    content = b"fake zip bytes"
    dest = tmp_path / "out.zip"

    with _mock_client(content) as client:
        build_full.download("https://example.invalid/f.zip", dest, client=client)

    assert dest.read_bytes() == content


def test_download_succeeds_when_checksum_matches(tmp_path: Path):
    content = b"fake zip bytes"
    dest = tmp_path / "out.zip"
    expected = hashlib.sha256(content).hexdigest()

    with _mock_client(content) as client:
        build_full.download("https://example.invalid/f.zip", dest, expected_sha256=expected, client=client)

    assert dest.read_bytes() == content


def test_download_raises_and_removes_file_on_checksum_mismatch(tmp_path: Path):
    content = b"fake zip bytes"
    dest = tmp_path / "out.zip"

    with _mock_client(content) as client:
        with pytest.raises(build_full.ChecksumMismatchError):
            build_full.download(
                "https://example.invalid/f.zip", dest, expected_sha256="0" * 64, client=client
            )

    assert not dest.exists()  # 校验失败不能留一份坏文件在磁盘上


def test_download_raises_on_http_error_status(tmp_path: Path):
    dest = tmp_path / "out.zip"

    with _mock_client(b"not found", status_code=404) as client:
        with pytest.raises(httpx.HTTPStatusError):
            # retries=1：第一次尝试就是最后一次，不用等真实的指数退避 sleep
            build_full.download("https://example.invalid/missing.zip", dest, client=client, retries=1)


def test_download_skips_when_cache_hit_with_matching_checksum(tmp_path: Path):
    content = b"already downloaded"
    dest = tmp_path / "out.zip"
    dest.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("命中本地缓存应该直接跳过，不该发起网络请求")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        build_full.download("https://example.invalid/f.zip", dest, expected_sha256=expected, client=client)

    assert dest.read_bytes() == content


def test_download_skips_when_cache_hit_without_expected_checksum(tmp_path: Path):
    """没给校验值（比如首次拉一个新版本还没回填 sha256 常量）时，已存在的文件
    直接信任，不重新下载——校验值本来就是"下载完之后回填"的，不能反过来因为
    没校验值就每次都重下。"""
    content = b"trusted local file"
    dest = tmp_path / "out.zip"
    dest.write_bytes(content)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("没给校验值也应该信任已存在的缓存文件，不该发起网络请求")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        build_full.download("https://example.invalid/f.zip", dest, client=client)

    assert dest.read_bytes() == content


def test_download_redownloads_when_cache_checksum_mismatches(tmp_path: Path):
    dest = tmp_path / "out.zip"
    dest.write_bytes(b"stale content")
    new_content = b"fresh content"
    expected = hashlib.sha256(new_content).hexdigest()

    with _mock_client(new_content) as client:
        build_full.download("https://example.invalid/f.zip", dest, expected_sha256=expected, client=client)

    assert dest.read_bytes() == new_content


def test_download_force_redownload_ignores_valid_cache(tmp_path: Path):
    dest = tmp_path / "out.zip"
    dest.write_bytes(b"old but still valid")
    new_content = b"forced fresh content"

    with _mock_client(new_content) as client:
        build_full.download(
            "https://example.invalid/f.zip",
            dest,
            expected_sha256=hashlib.sha256(new_content).hexdigest(),
            client=client,
            force=True,
        )

    assert dest.read_bytes() == new_content


def test_download_resumes_from_partial_part_file_via_range_header(tmp_path: Path):
    dest = tmp_path / "out.zip"
    part_path = dest.with_name(dest.name + ".part")
    part_path.write_bytes(b"AAAA")  # 模拟上次下载中断，已经落盘 4 字节

    seen_ranges: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_ranges.append(request.headers.get("range"))
        return httpx.Response(206, content=b"BBBB")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        build_full.download("https://example.invalid/f.zip", dest, client=client)

    assert seen_ranges == ["bytes=4-"]
    assert dest.read_bytes() == b"AAAABBBB"
    assert not part_path.exists()


def test_download_retries_transient_errors_then_succeeds(tmp_path: Path):
    dest = tmp_path / "out.zip"
    content = b"succeeded on third try"
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("temporary network blip", request=request)
        return httpx.Response(200, content=content)

    sleeps: list[float] = []
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        build_full.download(
            "https://example.invalid/f.zip", dest, client=client, retries=5, sleep=sleeps.append
        )

    assert dest.read_bytes() == content
    assert calls["n"] == 3
    assert len(sleeps) == 2  # 前两次失败各触发一次退避等待


def test_download_raises_after_exhausting_retries(tmp_path: Path):
    dest = tmp_path / "out.zip"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("always fails", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ConnectError):
            build_full.download(
                "https://example.invalid/f.zip", dest, client=client, retries=2, sleep=lambda s: None
            )

    assert not dest.exists()


def _make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_extract_members_flattens_only_matching_suffixes(tmp_path: Path):
    zip_bytes = _make_zip(
        {
            "build/bin/llama-server.exe": b"exe-bytes",
            "build/bin/ggml.dll": b"dll-bytes",
            "build/README.txt": b"not wanted",
            "build/nested/dir/": b"",  # 目录条目，不该被当成文件处理
        }
    )
    zip_path = tmp_path / "src.zip"
    zip_path.write_bytes(zip_bytes)
    dest_dir = tmp_path / "out"

    extracted = build_full.extract_members(zip_path, dest_dir, (".exe", ".dll"))

    names = {p.name for p in extracted}
    assert names == {"llama-server.exe", "ggml.dll"}
    assert (dest_dir / "llama-server.exe").read_bytes() == b"exe-bytes"
    assert (dest_dir / "ggml.dll").read_bytes() == b"dll-bytes"
    assert not (dest_dir / "README.txt").exists()
    assert not (dest_dir / "build").exists()  # 摊平，不保留 zip 内的子目录结构


def test_split_archive_produces_numbered_volumes_covering_source_size(tmp_path: Path):
    source_dir = tmp_path / "app"
    source_dir.mkdir()
    # 随机字节不可压缩，7z 不会把它们压没——保证 50KB payload 配 10KB 卷真的
    # 会切成多卷，而不是被 LZMA 压成一卷。
    payload = os.urandom(50 * 1024)
    (source_dir / "big.bin").write_bytes(payload)

    archive_path = tmp_path / "out.7z"
    parts = build_full.split_archive(source_dir, archive_path, volume_size_bytes=10 * 1024)

    assert len(parts) > 1
    for i, part in enumerate(parts, start=1):
        assert part.name == f"out.7z.{i:03d}"
        assert part.is_file()
    # 每一卷（除了可能更小的最后一卷）都不超过设定的卷大小
    assert all(p.stat().st_size <= 10 * 1024 for p in parts)


def test_read_app_version_matches_pyproject():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    expected = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]

    assert build_full.read_app_version() == expected

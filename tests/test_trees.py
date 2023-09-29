import pytest

from typing import Any
from pathlib import Path

from upyt.upy_fs import FilesystemAPI

from trees import (
    write_device_tree,
    read_device_tree,
    write_local_tree,
    read_local_tree,
)


@pytest.mark.parametrize(
    "tree",
    [
        # Empty
        {},
        # Files
        {"a.txt": b"I am a"},
        {"a.txt": b"I am a", "b.txt": b"I am b"},
        # Directories
        {"a": {}},
        {"a": {"b": {}}},
    ],
)
def test_read_write_device_tree_roundtrip(
    fs: FilesystemAPI,
    dev_tmpdir: str,
    tree: dict[str, Any] | bytes,
) -> None:
    write_device_tree(fs, dev_tmpdir, tree)
    assert read_device_tree(fs, dev_tmpdir) == tree


@pytest.mark.parametrize(
    "tree",
    [
        # Empty
        {},
        # Files
        {"a.txt": b"I am a"},
        {"a.txt": b"I am a", "b.txt": b"I am b"},
        # Directories
        {"a": {}},
        {"a": {"b": {}}},
    ],
)
def test_read_write_local_tree_roundtrip(
    tmp_path: Path,
    tree: dict[str, Any] | bytes,
) -> None:
    write_local_tree(tmp_path, tree)
    assert read_local_tree(tmp_path) == tree

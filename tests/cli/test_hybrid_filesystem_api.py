import pytest

from typing import Any

from pathlib import Path

from upyt.upy_fs import FilesystemAPI, PathType
from upyt.cli.hybrid_filesystem_api import HybridFilesystemAPI

from trees import (
    write_device_tree,
    write_local_tree,
    read_device_tree,
    read_local_tree,
)


def test_get_type(fs: FilesystemAPI, tmp_path: Path, dev_tmpdir: str) -> None:
    (tmp_path / "loc_file").touch()
    (tmp_path / "loc_dir").mkdir()

    fs.write_file(f"{dev_tmpdir}/dev_file", b"")
    fs.mkdir(f"{dev_tmpdir}/dev_dir")

    hfs = HybridFilesystemAPI(fs)

    assert hfs.get_type(str(tmp_path / "loc_file")) == PathType.file
    assert hfs.get_type(str(tmp_path / "loc_dir")) == PathType.dir
    assert hfs.get_type(str(tmp_path / "loc_absent")) == PathType.absent

    assert hfs.get_type(f":{dev_tmpdir}/dev_file") == PathType.file
    assert hfs.get_type(f":{dev_tmpdir}/dev_dir") == PathType.dir
    assert hfs.get_type(f":{dev_tmpdir}/dev_absent") == PathType.absent


@pytest.mark.parametrize("device", [False, True])
def test_mkdir(
    fs: FilesystemAPI, tmp_path: Path, dev_tmpdir: str, device: bool
) -> None:
    hfs = HybridFilesystemAPI(fs)

    root = f":{dev_tmpdir}" if device else str(tmp_path)

    hfs.mkdir(f"{root}/foo")

    # Already exists
    with pytest.raises(OSError):
        hfs.mkdir(f":{root}/foo")
    hfs.mkdir(f"{root}/foo", exist_ok=True)

    # Multiple levels
    with pytest.raises(OSError):
        hfs.mkdir(f":{root}/qux/quo")
    hfs.mkdir(f"{root}/qux/quo", parents=True)

    tree = read_device_tree(fs, dev_tmpdir) if device else read_local_tree(tmp_path)
    assert tree == {
        "foo": {},
        "qux": {"quo": {}},
    }


@pytest.mark.parametrize("device", [False, True])
def test_remove_recursive(
    fs: FilesystemAPI, tmp_path: Path, dev_tmpdir: str, device: bool
) -> None:
    hfs = HybridFilesystemAPI(fs)

    tree: dict[str, Any] | bytes = {
        "file": b"I am a file",
        "empty_dir": {},
        "full_dir": {"foo": b"I am foo", "bar": {}},
        "left_behind": b"I will be spared!",
    }
    if device:
        root = f":{dev_tmpdir}"
        write_device_tree(fs, dev_tmpdir, tree)
    else:
        root = str(tmp_path)
        write_local_tree(tmp_path, tree)

    hfs.remove_recursive(f"{root}/file")
    hfs.remove_recursive(f"{root}/empty_dir")
    hfs.remove_recursive(f"{root}/full_dir")
    with pytest.raises(OSError):
        hfs.remove_recursive(f"{root}/not_exist")

    tree = read_device_tree(fs, dev_tmpdir) if device else read_local_tree(tmp_path)
    assert tree == {
        "left_behind": b"I will be spared!",
    }


@pytest.mark.parametrize("device", [False, True])
def test_ls(fs: FilesystemAPI, tmp_path: Path, dev_tmpdir: str, device: bool) -> None:
    hfs = HybridFilesystemAPI(fs)

    tree = {
        "file": b"I am a file",
        "dir": {"foo": b"I am foo", "bar": {}},
    }
    if device:
        root = f":{dev_tmpdir}"
        write_device_tree(fs, dev_tmpdir, tree)
    else:
        root = str(tmp_path)
        write_local_tree(tmp_path, tree)

    assert hfs.ls(root) == (["dir"], ["file"])

    with pytest.raises(OSError):
        hfs.ls(f"{root}/file")

    with pytest.raises(OSError):
        hfs.ls(f"{root}/not_exist")


@pytest.mark.parametrize("device", [False, True])
def test_rename(
    fs: FilesystemAPI, tmp_path: Path, dev_tmpdir: str, device: bool
) -> None:
    hfs = HybridFilesystemAPI(fs)

    tree: dict[str, Any] | bytes = {
        "a": b"I am A",
        "overwrite_with_a": b"I am to be overwritten",
        "b": b"I am B",
        "c": {"foo": b"I am foo in C"},
        "not_moved_file": b"I am a non moved file",
        "not_moved_dir": {"foo": b"I am in a non moved directory"},
        "dir": {"foo": b"I won't be overwritten because I'm a directory!"},
    }
    if device:
        root = f":{dev_tmpdir}"
        write_device_tree(fs, dev_tmpdir, tree)
    else:
        root = str(tmp_path)
        write_local_tree(tmp_path, tree)

    hfs.rename(f"{root}/a", f"{root}/overwrite_with_a")
    hfs.rename(f"{root}/b", f"{root}/new_name_for_b")
    hfs.rename(f"{root}/c", f"{root}/new_name_for_c")

    with pytest.raises(OSError):
        hfs.rename(f"{root}/not_moved_file", f"{root}/dir")

    with pytest.raises(OSError):
        hfs.rename(f"{root}/not_moved_file", f"{root}/non-existing-intermediate/dir")

    with pytest.raises(OSError):
        hfs.rename(f"{root}/not_moved_dir", f"{root}/dir")

    with pytest.raises(OSError):
        hfs.rename(f"{root}/not_moved_dir", f"{root}/non-existing-intermediate/dir")

    tree = read_device_tree(fs, dev_tmpdir) if device else read_local_tree(tmp_path)
    assert tree == {
        "overwrite_with_a": b"I am A",
        "new_name_for_b": b"I am B",
        "new_name_for_c": {"foo": b"I am foo in C"},
        "not_moved_file": b"I am a non moved file",
        "not_moved_dir": {"foo": b"I am in a non moved directory"},
        "dir": {"foo": b"I won't be overwritten because I'm a directory!"},
    }


@pytest.mark.parametrize("device", [False, True])
def test_write_file(
    fs: FilesystemAPI, tmp_path: Path, dev_tmpdir: str, device: bool
) -> None:
    hfs = HybridFilesystemAPI(fs)

    root = f":{dev_tmpdir}" if device else str(tmp_path)

    hfs.write_file(f"{root}/foo", b"I am foo!")

    tree = read_device_tree(fs, dev_tmpdir) if device else read_local_tree(tmp_path)
    assert tree == {"foo": b"I am foo!"}


@pytest.mark.parametrize("device", [False, True])
def test_read_file(
    fs: FilesystemAPI, tmp_path: Path, dev_tmpdir: str, device: bool
) -> None:
    hfs = HybridFilesystemAPI(fs)

    tree = {
        "foo": b"I am foo",
    }
    if device:
        root = f":{dev_tmpdir}"
        write_device_tree(fs, dev_tmpdir, tree)
    else:
        root = str(tmp_path)
        write_local_tree(tmp_path, tree)

    assert hfs.read_file(f"{root}/foo") == b"I am foo"


@pytest.mark.parametrize("device", [False, True])
def test_file_len(
    fs: FilesystemAPI, tmp_path: Path, dev_tmpdir: str, device: bool
) -> None:
    hfs = HybridFilesystemAPI(fs)

    tree = {
        "foo": b"I'm twenty bytes!!!!",
    }
    if device:
        root = f":{dev_tmpdir}"
        write_device_tree(fs, dev_tmpdir, tree)
    else:
        root = str(tmp_path)
        write_local_tree(tmp_path, tree)

    assert hfs.file_len(f"{root}/foo") == 20


def test_sync(fs: FilesystemAPI) -> None:
    hfs = HybridFilesystemAPI(fs)
    hfs.sync()  # Just make sure it doesn't crash...

import pytest

from unittest.mock import Mock, call

from typing import Any

from pathlib import Path

import shutil

from trees import (
    write_device_tree,
    read_device_tree,
    write_local_tree,
    read_local_tree,
)

from upyt.connection import Connection
from upyt.upy_fs import FilesystemAPI

from upyt.sync import (
    decode_upyt_id,
    encode_upyt_id,
    get_upyt_id,
    enumerate_local_files,
    sync_to_device,
)


class TestUPyTIDCodecs:

    @pytest.mark.parametrize(
        "version, device_id",
        [
            (0, "000000000000"),
            (1, "000000000000"),
            (123, "1234567890aB"),
        ],
    )
    def test_roundtrip(self, version: int, device_id: str) -> None:
        encoded = encode_upyt_id(version, device_id)
        assert decode_upyt_id(encoded) == (version, device_id)

    @pytest.mark.parametrize(
        "value",
        [
            # Missing fields
            b"",
            b"1",
            # Extra fields
            b"1 2 3",
            # Invalid version
            b"nope 000000000000",
            # Invalid device ID (not ASCII)
            b"nope \xFF00000000000",
        ],
    )
    def test_invalid(self, value: bytes) -> None:
        with pytest.raises(ValueError):
            decode_upyt_id(value)


class TestGetUpytId:
    
    def test_create_file_if_absent(self, fs: FilesystemAPI, dev_tmpdir: str) -> None:
        fs.mkdir(f"{dev_tmpdir}/a")
        fs.mkdir(f"{dev_tmpdir}/b")
        
        version_a, device_id_a = get_upyt_id(fs, f"{dev_tmpdir}/a")
        version_b, device_id_b = get_upyt_id(fs, f"{dev_tmpdir}/b")
        
        assert device_id_a != device_id_b
    
    def test_read_file_if_exists(self, fs: FilesystemAPI, dev_tmpdir: str) -> None:
        version = 123
        device_id = "1234567890aB"
        fs.write_file(f"{dev_tmpdir}/.upyt_id.txt", encode_upyt_id(version, device_id))
        assert get_upyt_id(fs, dev_tmpdir) == (version, device_id)


class TestEnumerateLocalFiles:

    def test_empty(self, tmp_path: Path) -> None:
        assert list(enumerate_local_files(tmp_path)) == []

    def test_empty_with_exclusions(self, tmp_path: Path) -> None:
        assert list(enumerate_local_files(tmp_path, ["foo", "*", "**"])) == []

    def test_flat_files(self, tmp_path: Path) -> None:
        exp = [
            tmp_path / "foo.txt",
            tmp_path / "bar.baz",
        ]
        for file in exp:
            file.touch()
        
        assert set(enumerate_local_files(tmp_path)) == set(exp)

    def test_nested_files(self, tmp_path: Path) -> None:
        dirs = [
            tmp_path / "foo",
            tmp_path / "foo/bar/",
        ]
        files = [
            tmp_path / "a",
            tmp_path / "foo" / "b",
            tmp_path / "foo" / "bar" / "c",
        ]
        for dir in dirs:
            dir.mkdir()
        for file in files:
            file.touch()
        
        assert set(enumerate_local_files(tmp_path)) == set(files + dirs)
    
    def test_exclusions(self, tmp_path: Path) -> None:
        dirs = [
            tmp_path / "foo",
            tmp_path / "foo" / "bar",
            tmp_path / "foo" / "bar" / "exclude_dir",
            tmp_path / "foo" / "bar" / "exclude_txt",
            tmp_path / "foo" / "exclude_in_root",
            tmp_path / "exclude_in_root",
            tmp_path / "exclude_when_directory",
        ]
        files = [
            tmp_path / "a",
            tmp_path / "b.exclude",
            tmp_path / "foo/c.exclude",
            tmp_path / "foo/bar/exclude_dir/d",
            tmp_path / "foo/bar/exclude_txt/e.not_txt",
            tmp_path / "foo/bar/exclude_txt/f.txt",
            tmp_path / "foo/exclude_when_directory",
        ]
        for dir in dirs:
            dir.mkdir()
        for file in files:
            file.touch()
        
        exclusions = [
            "*.exclude",
            "exclude_dir",
            "exclude_txt/*.txt",
            "/exclude_in_root",
            "exclude_when_directory/",
        ]
        
        exp_absent = {
            tmp_path / "b.exclude",
            tmp_path / "foo/c.exclude",
            tmp_path / "foo/bar/exclude_dir",
            tmp_path / "foo/bar/exclude_dir/d",
            tmp_path / "foo/bar/exclude_txt/f.txt",
            tmp_path / "exclude_in_root",
            tmp_path / "exclude_when_directory",
        }
        
        exp = set(files + dirs) - exp_absent
        
        assert set(enumerate_local_files(tmp_path, exclusions)) == exp


class TestSyncToDevice:
    
    @pytest.fixture
    def dev_tmpdir_with_id(self, fs: FilesystemAPI, dev_tmpdir: str) -> str:
        """
        A temporary dir initialised with a UPyT ID with a fixed device ID of
        'DEVICEXXXX' and version 500.
        """
        fs.write_file(f"{dev_tmpdir}/.upyt_id.txt", b"500 DEVICEXXXX")
        return dev_tmpdir
    
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
    def test_create_from_scatch(
        self,
        fs: FilesystemAPI,
        dev_tmpdir_with_id: str,
        tmp_path: Path,
        tree: dict[str, Any] | bytes,
    ) -> None:
        write_local_tree(tmp_path, tree)
        sync_to_device(fs, tmp_path, dev_tmpdir_with_id)
        device_tree = read_device_tree(fs, dev_tmpdir_with_id)
        
        # Check that the cache exactly reflects the on-device state
        assert read_local_tree(tmp_path) == dict(
            tree,
            **{
                ".upyt_cache": {
                    "DEVICEXXXX": device_tree
                },
            },
        )
        
        # Check an incremented device ID was written back
        assert device_tree.pop(".upyt_id.txt") == b"501 DEVICEXXXX"
        
        # Check the input tree has been written exactly
        assert device_tree == tree
    
    def test_exclusions(
        self,
        fs: FilesystemAPI,
        dev_tmpdir_with_id: str,
        tmp_path: Path,
    ) -> None:
        write_local_tree(tmp_path, {
            "a": b"file a",
            "b": b"file b",
            "c.exclude": b"file c (excluded)",
        })
        sync_to_device(fs, tmp_path, dev_tmpdir_with_id, exclude=["*.exclude"])
        device_tree = read_device_tree(fs, dev_tmpdir_with_id)
        
        # Check the input tree has been written exactly
        device_tree.pop(".upyt_id.txt")
        assert device_tree == {
            "a": b"file a",
            "b": b"file b",
        }
    
    def test_add_then_remove(
        self,
        fs: FilesystemAPI,
        dev_tmpdir_with_id: str,
        tmp_path: Path,
    ) -> None:
        tree = {
            "a": {"subdir": {}},
            "b": {"subfile": b"I am a subfile"},
            "c": b"top level file",
        }
        
        write_local_tree(tmp_path, tree)
        sync_to_device(fs, tmp_path, dev_tmpdir_with_id)
        device_tree = read_device_tree(fs, dev_tmpdir_with_id)
        
        # Check that the cache exactly reflects the on-device state
        assert read_local_tree(tmp_path) == dict(
            tree,
            **{
                ".upyt_cache": {
                    "DEVICEXXXX": device_tree
                },
            },
        )
        
        # Check an incremented device ID was written back
        assert device_tree.pop(".upyt_id.txt") == b"501 DEVICEXXXX"
        
        # Check the input tree has been written exactly
        assert device_tree == tree
        
        # Remove files
        shutil.rmtree(tmp_path / "a")
        shutil.rmtree(tmp_path / "b")
        (tmp_path / "c").unlink()
        
        sync_to_device(fs, tmp_path, dev_tmpdir_with_id)
        device_tree = read_device_tree(fs, dev_tmpdir_with_id)
        
        # Check that the cache reflects the values we just uploaded (i.e.
        # nothing!)
        assert read_local_tree(tmp_path) == {
            ".upyt_cache": {
                "DEVICEXXXX": {
                    ".upyt_id.txt": b"502 DEVICEXXXX",
                }
            },
        }
        
        # Check an incremented device ID was written back
        assert device_tree.pop(".upyt_id.txt") == b"502 DEVICEXXXX"
        
        # The extra files should still remain on the device
        assert device_tree == tree
    
    
    def test_switch_between_file_and_dir(
        self,
        fs: FilesystemAPI,
        dev_tmpdir_with_id: str,
        tmp_path: Path,
    ) -> None:
        tree = {
            "was_dir": {},
            "was_file": b"I am a file for now",
        }
        write_local_tree(tmp_path, tree)
        sync_to_device(fs, tmp_path, dev_tmpdir_with_id)
        
        # Check as expected
        device_tree = read_device_tree(fs, dev_tmpdir_with_id)
        device_tree.pop(".upyt_id.txt")
        assert device_tree == tree
        
        new_tree = {
            "was_dir": b"I am a file for now",
            "was_file": {},
        }
        write_local_tree(tmp_path, new_tree)
        sync_to_device(fs, tmp_path, dev_tmpdir_with_id)
        
        # Check types changed
        device_tree = read_device_tree(fs, dev_tmpdir_with_id)
        device_tree.pop(".upyt_id.txt")
        assert device_tree == new_tree
    
    def test_update_content(
        self,
        fs: FilesystemAPI,
        dev_tmpdir_with_id: str,
        tmp_path: Path,
    ) -> None:
        write_local_tree(tmp_path, {"file": b"Foo" + (b"X" * 1024)})
        sync_to_device(fs, tmp_path, dev_tmpdir_with_id)
        
        write_local_tree(tmp_path, {"file": b"Bar" + (b"X" * 1024)})
        sync_to_device(fs, tmp_path, dev_tmpdir_with_id)
        
        assert read_device_tree(fs, dev_tmpdir_with_id).pop("file") == (
            b"Bar" + (b"X" * 1024)
        )
    
    def test_update_content_catch_stale_cache_file(
        self,
        fs: FilesystemAPI,
        dev_tmpdir_with_id: str,
        tmp_path: Path,
    ) -> None:
        # Deliberately Corrupt the cache add a file which isn't yet on the
        # device (i.e. which hypothetically was deleted earlier on)
        cache_dir = tmp_path / ".upyt_cache" / "DEVICEXXXX"
        cache_dir.mkdir(parents=True)
        (cache_dir / ".upyt_id.txt").write_bytes(b"400 DEVICEXXXX")
        (cache_dir / "file").write_bytes(b"Something")
        
        # Should still overwrite file even though the (out-of-date) cache
        # suggests it is already on the device
        write_local_tree(tmp_path, {"file": b"Something"})
        sync_to_device(fs, tmp_path, dev_tmpdir_with_id)
        
        assert read_device_tree(fs, dev_tmpdir_with_id).pop("file") == b"Something"
    
    def test_force_enumerate_files(
        self,
        fs: FilesystemAPI,
        dev_tmpdir_with_id: str,
        tmp_path: Path,
    ) -> None:
        # Deliberately create a corrupt cache which would suggest a file is on
        # the device but isn't in reality to verify the force-mechanism works
        cache_dir = tmp_path / ".upyt_cache" / "DEVICEXXXX"
        cache_dir.mkdir(parents=True)
        (cache_dir / ".upyt_id.txt").write_bytes(b"500 DEVICEXXXX")
        (cache_dir / "file").write_bytes(b"Something")
        
        write_local_tree(tmp_path, {"file": b"Something"})
        
        # No force, no file
        sync_to_device(fs, tmp_path, dev_tmpdir_with_id)
        assert "file" not in read_device_tree(fs, dev_tmpdir_with_id)
        
        # Force leads to file existing
        sync_to_device(fs, tmp_path, dev_tmpdir_with_id, force_enumerate_files=True)
        assert read_device_tree(fs, dev_tmpdir_with_id).pop("file") == b"Something"
    
    def test_update_content_catch_stale_cache_value(
        self,
        fs: FilesystemAPI,
        dev_tmpdir_with_id: str,
        tmp_path: Path,
    ) -> None:
        write_local_tree(tmp_path, {"file": b"Foo" + (b"X" * 1024)})
        sync_to_device(fs, tmp_path, dev_tmpdir_with_id)

        # Deliberately Corrupt the cache (different version and different file
        # content)
        cache_dir = tmp_path / ".upyt_cache" / "DEVICEXXXX"
        (cache_dir / ".upyt_id.txt").write_bytes(b"500 DEVICEXXXX")
        (cache_dir / "file").write_bytes(b"Something else")
        
        # Should silently catch issue and fall back on writing file directly
        # leading to correct outcome
        write_local_tree(tmp_path, {"file": b"Something different"})
        sync_to_device(fs, tmp_path, dev_tmpdir_with_id)
        
        assert read_device_tree(fs, dev_tmpdir_with_id).pop("file") == (
            b"Something different"
        )
    
    def test_force_safe_update(
        self,
        fs: FilesystemAPI,
        dev_tmpdir_with_id: str,
        tmp_path: Path,
    ) -> None:
        # Deliberately create a corrupt cache which would suggest a file is on
        # the device with a particular value which doesn't match reality.
        cache_dir = tmp_path / ".upyt_cache" / "DEVICEXXXX"
        cache_dir.mkdir(parents=True)
        (cache_dir / ".upyt_id.txt").write_bytes(b"500 DEVICEXXXX")
        (cache_dir / "file").write_bytes(b"Something else")
        
        fs.write_file(f"{dev_tmpdir_with_id}/file", b"Entirely unrelated")
        
        write_local_tree(tmp_path, {"file": b"Something different"})
        
        # Without safe mode, the corrupt cache should lead to a corrupt file
        sync_to_device(fs, tmp_path, dev_tmpdir_with_id)
        assert read_device_tree(fs, dev_tmpdir_with_id).pop("file") == (
            b"Entirely udifferent"  # Corrupt!
        )
        
        # With safe mode, the corrupt cache should be caught and the correct
        # file written
        (cache_dir / "file").write_bytes(b"Something else")
        sync_to_device(fs, tmp_path, dev_tmpdir_with_id, force_safe_update=True)
        assert read_device_tree(fs, dev_tmpdir_with_id).pop("file") == (
            b"Something different"
        )
    
    def test_progress_reporting(
        self,
        fs: FilesystemAPI,
        dev_tmpdir_with_id: str,
        tmp_path: Path,
    ) -> None:
        write_local_tree(tmp_path, {"a": b"I am 'a'!", "b": b"I am 'b'!"})
        
        mock = Mock()
        sync_to_device(fs, tmp_path, dev_tmpdir_with_id, progress_callback=mock)
        
        # Should have notified on every change
        assert len(mock.mock_calls) == 2
        mock.assert_has_calls(
            call(
                Path(name),
                {Path("a"), Path("b")},
                {Path("a"), Path("b")},
            )
            for name in ["a", "b"]
        )
        
        # Minor change
        write_local_tree(tmp_path, {"b": b"I am a changed 'b'!", "c": b"I am 'c'"})
        
        mock = Mock()
        sync_to_device(fs, tmp_path, dev_tmpdir_with_id, progress_callback=mock)
        
        # Should have notified on only differences
        assert len(mock.mock_calls) == 2
        mock.assert_has_calls(
            call(
                Path(name),
                {Path("b"), Path("c")},
                {Path("a"), Path("b"), Path("c")},
            )
            for name in ["b", "c"]
        )

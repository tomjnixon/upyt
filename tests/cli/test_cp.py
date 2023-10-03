import pytest
from unittest.mock import Mock

from typing import Any

import sys

from io import BytesIO
from pathlib import Path
from subprocess import run

from trees import (
    write_device_tree,
    write_local_tree,
    read_device_tree,
    read_local_tree,
)

from upyt.connection import Connection
from upyt.upy_repl import raw_paste_exec
from upyt.upy_fs import FilesystemAPI

from upyt.cli.hybrid_filesystem_api import HybridFilesystemAPI

from upyt.cli.cp import (
    split_source,
    RecursionNotAllowedError,
    read_sources,
    write_single_file_to_destination,
    write_multiple_files_to_existing_directory,
    write_single_directory_to_non_existing_destination,
    cp,
)


@pytest.fixture
def hfs(fs: FilesystemAPI) -> HybridFilesystemAPI:
    return HybridFilesystemAPI(fs)


@pytest.mark.parametrize(
    "source, exp",
    [
        # Files relative to CWD
        ("foo", ("", "foo")),
        ("foo/bar", ("foo/", "bar")),
        ("foo/bar/baz", ("foo/bar/", "baz")),
        # CWD
        ("", ("", ".")),
        # Trailing slash normalized out
        ("foo/bar/baz/", ("foo/bar/", "baz")),
        # Root directory
        ("/", ("/", "")),
        # Absolute paths
        ("/", ("/", "")),
        ("/foo", ("/", "foo")),
        ("/foo/bar", ("/foo/", "bar")),
        ("/foo/bar/baz", ("/foo/bar/", "baz")),
        # Backslashes become forward slashes
        ("\\foo\\bar", ("/foo/", "bar")),
        # Colon prefix preserved
        (":", (":", ".")),
        (":foo", (":", "foo")),
        (":/foo", (":/", "foo")),
        (":/foo/bar", (":/foo/", "bar")),
        (":/foo/bar/baz", (":/foo/bar/", "baz")),
        (":/foo/bar/baz/", (":/foo/bar/", "baz")),
    ],
)
def test_split_source(source: str, exp: tuple[str, str]) -> None:
    assert split_source(source) == exp


class TestReadSources:
    def test_host_files(self, hfs: HybridFilesystemAPI, tmp_path: Path) -> None:
        a = tmp_path / "a"
        a.write_bytes(b"I am A")

        (tmp_path / "foo").mkdir()
        b = tmp_path / "foo" / "b"
        b.write_bytes(b"I am B")

        out = list(read_sources(hfs, [str(a), str(b)]))
        assert len(out) == 2
        assert set(out) == {
            ("a", b"I am A"),
            ("b", b"I am B"),
        }

    def test_host_directory_not_allowed(
        self, hfs: HybridFilesystemAPI, tmp_path: Path
    ) -> None:
        with pytest.raises(RecursionNotAllowedError) as excinfo:
            list(read_sources(hfs, [str(tmp_path)], False))
        assert str(excinfo.value) == str(tmp_path)

    @pytest.mark.parametrize("trailing_slash", [False, True])
    def test_host_recursive(
        self,
        hfs: HybridFilesystemAPI,
        tmp_path: Path,
        trailing_slash: bool,
    ) -> None:
        dir = tmp_path / "dir"
        dir.mkdir()
        write_local_tree(
            dir,
            {
                "foo": b"I am foo",
                "bar": {
                    "baz": b"I am baz",
                    "qux": {
                        "quo": b"I am quo",
                    },
                },
            },
        )

        out = list(
            read_sources(hfs, [str(dir) + ("/" if trailing_slash else "")], True)
        )
        assert len(out) == 6
        assert set(out) == {
            ("dir", None),
            ("dir/foo", b"I am foo"),
            ("dir/bar", None),
            ("dir/bar/baz", b"I am baz"),
            ("dir/bar/qux", None),
            ("dir/bar/qux/quo", b"I am quo"),
        }

    @pytest.mark.parametrize("cwd_spec", [".", "./", ""])
    def test_host_recursive_cwd(
        self,
        hfs: HybridFilesystemAPI,
        tmp_path: Path,
        monkeypatch: Any,
        cwd_spec: str,
    ) -> None:
        dir = tmp_path / "dir"
        dir.mkdir()
        write_local_tree(
            dir,
            {
                "foo": b"I am foo",
                "bar": {
                    "baz": b"I am baz",
                    "qux": {
                        "quo": b"I am quo",
                    },
                },
            },
        )
        monkeypatch.chdir(dir)
        out = list(read_sources(hfs, [cwd_spec], True))
        assert len(out) == 6
        assert set(out) == {
            (".", None),
            ("./foo", b"I am foo"),
            ("./bar", None),
            ("./bar/baz", b"I am baz"),
            ("./bar/qux", None),
            ("./bar/qux/quo", b"I am quo"),
        }

    def test_device_files(
        self, fs: FilesystemAPI, hfs: HybridFilesystemAPI, dev_tmpdir: str
    ) -> None:
        a = f"{dev_tmpdir}/a"
        fs.write_file(a, b"I am A")

        fs.mkdir(f"{dev_tmpdir}/foo")
        b = f"{dev_tmpdir}/foo/b"
        fs.write_file(b, b"I am B")

        out = list(read_sources(hfs, [f":{a}", f":{b}"]))
        assert len(out) == 2
        assert set(out) == {
            ("a", b"I am A"),
            ("b", b"I am B"),
        }

    def test_device_directory_not_allowed(
        self, hfs: HybridFilesystemAPI, dev_tmpdir: str
    ) -> None:
        with pytest.raises(RecursionNotAllowedError) as excinfo:
            list(read_sources(hfs, [f":{dev_tmpdir}"], False))
        assert str(excinfo.value) == f":{dev_tmpdir}"

    @pytest.mark.parametrize("trailing_slash", [False, True])
    def test_device_recursive(
        self,
        fs: FilesystemAPI,
        hfs: HybridFilesystemAPI,
        dev_tmpdir: Path,
        trailing_slash: bool,
    ) -> None:
        dir = f"{dev_tmpdir}/dir"
        fs.mkdir(dir)
        write_device_tree(
            fs,
            dir,
            {
                "foo": b"I am foo",
                "bar": {
                    "baz": b"I am baz",
                    "qux": {
                        "quo": b"I am quo",
                    },
                },
            },
        )

        out = list(
            read_sources(hfs, [f":{dir}" + ("/" if trailing_slash else "")], True)
        )
        assert len(out) == 6
        assert set(out) == {
            ("dir", None),
            ("dir/foo", b"I am foo"),
            ("dir/bar", None),
            ("dir/bar/baz", b"I am baz"),
            ("dir/bar/qux", None),
            ("dir/bar/qux/quo", b"I am quo"),
        }

    @pytest.mark.parametrize("cwd_spec", [":.", ":./", ":"])
    def test_device_recursive_cwd(
        self,
        ser: Connection,
        fs: FilesystemAPI,
        hfs: HybridFilesystemAPI,
        dev_tmpdir: Path,
        cwd_spec: str,
    ) -> None:
        dir = f"{dev_tmpdir}/dir"
        fs.mkdir(dir)
        write_device_tree(
            fs,
            dir,
            {
                "foo": b"I am foo",
                "bar": {
                    "baz": b"I am baz",
                    "qux": {
                        "quo": b"I am quo",
                    },
                },
            },
        )
        assert raw_paste_exec(ser, f"import os; os.chdir({dir!r})") == ("", "")
        try:
            out = list(read_sources(hfs, [cwd_spec], True))
            assert len(out) == 6
            assert set(out) == {
                (".", None),
                ("./foo", b"I am foo"),
                ("./bar", None),
                ("./bar/baz", b"I am baz"),
                ("./bar/qux", None),
                ("./bar/qux/quo", b"I am quo"),
            }
        finally:
            assert raw_paste_exec(ser, "import os; os.chdir('/')") == ("", "")


class TestWriteSingleFileToDestination:
    def test_local_dest_dir(self, hfs: HybridFilesystemAPI, tmp_path: Path) -> None:
        write_single_file_to_destination(hfs, ("foo", b"I am foo"), str(tmp_path))
        assert (tmp_path / "foo").read_bytes() == b"I am foo"

    @pytest.mark.parametrize("exist_already", [False, True])
    def test_local_dest_file(
        self, hfs: HybridFilesystemAPI, tmp_path: Path, exist_already: bool
    ) -> None:
        if exist_already:
            (tmp_path / "bar").write_bytes(b"Old contents")

        write_single_file_to_destination(
            hfs, ("foo", b"I am foo"), str(tmp_path / "bar")
        )

        assert (tmp_path / "bar").read_bytes() == b"I am foo"

    def test_device_dest_dir(
        self, fs: FilesystemAPI, hfs: HybridFilesystemAPI, dev_tmpdir: str
    ) -> None:
        write_single_file_to_destination(
            hfs,
            ("foo", b"I am foo"),
            f":{dev_tmpdir}",
        )
        assert fs.read_file(f"{dev_tmpdir}/foo") == b"I am foo"

    @pytest.mark.parametrize("exist_already", [False, True])
    def test_device_dest_file(
        self,
        fs: FilesystemAPI,
        hfs: HybridFilesystemAPI,
        dev_tmpdir: str,
        exist_already: bool,
    ) -> None:
        if exist_already:
            fs.write_file(f"{dev_tmpdir}/bar", b"Old contents")

        write_single_file_to_destination(
            hfs,
            ("foo", b"I am foo"),
            f":{dev_tmpdir}/bar",
        )

        assert fs.read_file(f"{dev_tmpdir}/bar") == b"I am foo"


class TestWriteMultipleFilesToExistingDirectory:
    def test_local(self, hfs: HybridFilesystemAPI, tmp_path: Path) -> None:
        write_multiple_files_to_existing_directory(
            hfs,
            [
                ("foo", b"I am foo"),
                ("bar", None),
                ("bar/baz", b"I am baz"),
            ],
            str(tmp_path),
        )
        assert read_local_tree(tmp_path) == {
            "foo": b"I am foo",
            "bar": {
                "baz": b"I am baz",
            },
        }

    def test_device(
        self, fs: FilesystemAPI, hfs: HybridFilesystemAPI, dev_tmpdir: str
    ) -> None:
        write_multiple_files_to_existing_directory(
            hfs,
            [
                ("foo", b"I am foo"),
                ("bar", None),
                ("bar/baz", b"I am baz"),
            ],
            f":{dev_tmpdir}",
        )
        assert read_device_tree(fs, dev_tmpdir) == {
            "foo": b"I am foo",
            "bar": {
                "baz": b"I am baz",
            },
        }


class TestWriteSingleDirectoryToNonExistingDestination:
    def test_local(self, hfs: HybridFilesystemAPI, tmp_path: Path) -> None:
        write_single_directory_to_non_existing_destination(
            hfs,
            [
                ("bar", None),
                ("bar/baz", b"I am baz"),
            ],
            str(tmp_path / "foo"),
        )
        assert read_local_tree(tmp_path) == {
            "foo": {
                "baz": b"I am baz",
            },
        }

    def test_device(
        self, fs: FilesystemAPI, hfs: HybridFilesystemAPI, dev_tmpdir: str
    ) -> None:
        write_single_directory_to_non_existing_destination(
            hfs,
            [
                ("bar", None),
                ("bar/baz", b"I am baz"),
            ],
            f":{dev_tmpdir}/foo",
        )
        assert read_device_tree(fs, dev_tmpdir) == {
            "foo": {
                "baz": b"I am baz",
            },
        }


class TestCp:
    @pytest.mark.parametrize("device_source", [False, True])
    @pytest.mark.parametrize("device_destination", [False, True])
    @pytest.mark.parametrize("destination_exists", [False, True])
    @pytest.mark.parametrize("recursive", [False, True])
    def test_single_file(
        self,
        fs: FilesystemAPI,
        hfs: HybridFilesystemAPI,
        tmp_path: Path,
        dev_tmpdir: str,
        device_source: bool,
        device_destination: bool,
        destination_exists: bool,
        recursive: bool,
    ):
        if device_source:
            fs.write_file(f"{dev_tmpdir}/foo", b"I am foo")
            sources = [f":{dev_tmpdir}/foo"]
        else:
            (tmp_path / "foo").write_bytes(b"I am foo")
            sources = [str(tmp_path / "foo")]

        if device_destination:
            if destination_exists:
                fs.write_file(f"{dev_tmpdir}/bar", b"Old content...")
            destination = f":{dev_tmpdir}/bar"
        else:
            if destination_exists:
                (tmp_path / "bar").write_bytes(b"Old content...")
            destination = str(tmp_path / "bar")

        cp(hfs, sources, destination, recursive)

        if device_destination:
            assert fs.read_file(f"{dev_tmpdir}/bar") == b"I am foo"
        else:
            assert (tmp_path / "bar").read_bytes() == b"I am foo"

    @pytest.mark.parametrize("device_source", [False, True])
    @pytest.mark.parametrize("device_destination", [False, True])
    def test_dir_to_non_existant_location(
        self,
        fs: FilesystemAPI,
        hfs: HybridFilesystemAPI,
        tmp_path: Path,
        dev_tmpdir: str,
        device_source: bool,
        device_destination: bool,
    ):
        if device_source:
            write_device_tree(fs, dev_tmpdir, {"foo": {"bar": b"I am bar"}})
            sources = [f":{dev_tmpdir}/foo"]
        else:
            write_local_tree(tmp_path, {"foo": {"bar": b"I am bar"}})
            sources = [str(tmp_path / "foo")]

        if device_destination:
            destination = f":{dev_tmpdir}/baz"
        else:
            destination = str(tmp_path / "baz")

        cp(hfs, sources, destination, recursive=True)

        if device_destination:
            tree = read_device_tree(fs, dev_tmpdir)
        else:
            tree = read_local_tree(tmp_path)
        assert tree["baz"] == {"bar": b"I am bar"}

    @pytest.mark.parametrize("device_destination", [False, True])
    def test_multiple_sources(
        self,
        fs: FilesystemAPI,
        hfs: HybridFilesystemAPI,
        tmp_path: Path,
        dev_tmpdir: str,
        device_destination: bool,
    ):
        write_device_tree(
            fs,
            dev_tmpdir,
            {
                "dev_file": b"I am dev_file",
                "dev_dir": {"bar": b"I am dev_dir bar"},
            },
        )
        write_local_tree(
            tmp_path,
            {
                "loc_file": b"I am loc_file",
                "loc_dir": {"bar": b"I am loc_dir bar"},
            },
        )
        sources = [
            str(tmp_path / "loc_file"),
            str(tmp_path / "loc_dir"),
            f":{dev_tmpdir}/dev_file",
            f":{dev_tmpdir}/dev_dir",
        ]

        if device_destination:
            fs.mkdir(f"{dev_tmpdir}/baz")
            destination = f":{dev_tmpdir}/baz"
        else:
            (tmp_path / "baz").mkdir()
            destination = str(tmp_path / "baz")

        cp(hfs, sources, destination, recursive=True)

        if device_destination:
            tree = read_device_tree(fs, f"{dev_tmpdir}/baz")
        else:
            tree = read_local_tree(tmp_path / "baz")
        assert tree == {
            "dev_file": b"I am dev_file",
            "dev_dir": {"bar": b"I am dev_dir bar"},
            "loc_file": b"I am loc_file",
            "loc_dir": {"bar": b"I am loc_dir bar"},
        }


@pytest.mark.parametrize(
    "sources, destination, recursive, exp_fail",
    [
        # Copy single file
        (["$/file"], "$/file_2", False, False),
        (["$/file"], "$/empty_dir", False, False),
        (["$/file"], "$/empty_dir/", False, False),
        # Copy file on top of another file
        (["$/file"], "$/other_file", False, False),
        # Copy multiple named files
        (["$/file", "$/other_file"], "$/empty_dir/", False, False),
        # Copy multiple named files to non-directory
        (["$/file", "$/other_file"], "$/foo", False, True),
        (["$/file", "$/other_file"], "$/foo", True, True),
        # Copy directory recursively (all possible trailing ends)
        (["$/dir"], "$/foo", True, False),
        (["$/dir/"], "$/foo", True, False),
        (["$/dir"], "$/foo/", True, False),
        (["$/dir/"], "$/foo/", True, False),
        (["$/dir"], "$/empty_dir", True, False),
        (["$/dir/"], "$/empty_dir", True, False),
        (["$/dir"], "$/empty_dir/", True, False),
        (["$/dir/"], "$/empty_dir/", True, False),
        # Copy directory without recursive fails
        (["$/dir"], "$/foo", False, True),
        (["$/dir"], "$/empty_dir/", False, True),
        # Copy non-existing
        (["$/foo"], "$/file_2", False, True),
    ],
)
def test_equivalence_with_posix_cp(
    tmp_path: Path,
    sources: list[str],
    destination: str,
    recursive: bool,
    exp_fail: bool,
) -> None:
    hfs = HybridFilesystemAPI(Mock())

    tree = {
        "file": b"I am a file",
        "other_file": b"I am another file",
        "dir": {"foo": b"File in dir", "bar": {}},
        "empty_dir": {},
    }

    # Part one: See what this library does
    write_local_tree(tmp_path, tree)
    try:
        cp(
            hfs,
            [s.replace("$", str(tmp_path)) for s in sources],
            destination.replace("$", str(tmp_path)),
            recursive,
        )
        this_success = True
        this_tree = read_local_tree(tmp_path)
    except Exception as exc:
        print(exc)
        this_success = False

    # Part two: See what cp does
    posix_dir = tmp_path / "posix_dir"
    write_local_tree(posix_dir, tree)
    posix_success = (
        run(
            (
                ["cp"]
                + (["-r"] if recursive else [])
                + [s.replace("$", str(posix_dir)) for s in sources]
                + [destination.replace("$", str(posix_dir))]
            ),
        ).returncode
        == 0
    )
    posix_tree = read_local_tree(posix_dir)

    # Part three: check for equivalence
    assert this_success is posix_success
    assert this_success is (not exp_fail)
    if this_success:
        assert this_tree == posix_tree

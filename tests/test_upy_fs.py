import pytest

import random

from upyt.upy_fs import (
    upy_filesystem,
    FilesystemAPI,
)


class TestPrimitives:
    
    def test_create_delete_empty_directory(self, ser) -> None:
        with upy_filesystem(ser) as fs:
            tmpdir = f"/d{random.randint(0, 10000)}"
            fs.mkdir(tmpdir)
            
            # Check created
            dirs, files = fs.ls("/")
            assert tmpdir[1:] in dirs
            
            fs.remove_recursive(tmpdir)
            
            # Check deleted
            dirs, files = fs.ls("/")
            assert tmpdir[1:] not in dirs
    
    def test_mkdir_features(self, ser) -> None:
        with upy_filesystem(ser) as fs:
            tmpdir = f"/d{random.randint(0, 10000)}"
            fs.mkdir(tmpdir)
            
            # Exists OK
            with pytest.raises(OSError):
                fs.mkdir(tmpdir, exist_ok=False)
            fs.mkdir(tmpdir, exist_ok=True)
            
            # Parents
            # NB: Parents existing (or not) is OK
            with pytest.raises(OSError):
                fs.mkdir(tmpdir + "/a/bc/defg", parents=False)
            fs.mkdir(tmpdir + "/a/bc/defg", parents=True)
            
            # Parents doesn't allow existance of the final directory
            with pytest.raises(OSError):
                fs.mkdir(tmpdir + "/a/bc/defg", parents=True, exist_ok=False)
            fs.mkdir(tmpdir + "/a/bc/defg", parents=True, exist_ok=True)
            
            # Check created
            assert fs.ls(tmpdir) == (["a"], [])
            assert fs.ls(f"{tmpdir}/a") == (["bc"], [])
            assert fs.ls(f"{tmpdir}/a/bc") == (["defg"], [])
            assert fs.ls(f"{tmpdir}/a/bc/defg") == ([], [])
            
            fs.remove_recursive(tmpdir)
            
            # Check deleted
            dirs, files = fs.ls("/")
            assert tmpdir[1:] not in dirs
    
    def test_read_and_write_file_raw(self, ser) -> None:
        with upy_filesystem(ser) as fs:
            tmpdir = f"/d{random.randint(0, 10000)}"
            fs.mkdir(tmpdir)
            
            # Bytes literal mode
            fs.write_file_raw(f"{tmpdir}/foo", b"Hello, world!\n")
            assert fs.read_file_raw(f"{tmpdir}/foo") == b"Hello, world!\n"
            
            # Hex mode
            fs.write_file_raw(f"{tmpdir}/foo", b"\xFF" * 10)
            assert fs.read_file_raw(f"{tmpdir}/foo") == b"\xFF" * 10
            
            # Chop into blocks (multiple of size)
            fs.write_file_raw(f"{tmpdir}/foo", b"Hello, world!\n", block_size=2)
            assert fs.read_file_raw(f"{tmpdir}/foo", block_size=2) == b"Hello, world!\n"
            
            # Chop into blocks (not a multiple of size)
            fs.write_file_raw(f"{tmpdir}/foo", b"Hello, world!\n", block_size=3)
            assert fs.read_file_raw(f"{tmpdir}/foo", block_size=3) == b"Hello, world!\n"
            
            # Run on something large with default block size
            fs.write_file_raw(f"{tmpdir}/foo", b"X"*1024*4)
            assert fs.read_file_raw(f"{tmpdir}/foo") == b"X"*1024*4
            
            # Run on something large in hex mode with default block size
            fs.write_file_raw(f"{tmpdir}/foo", b"\xFF"*1024*2)
            assert fs.read_file_raw(f"{tmpdir}/foo") == b"\xFF"*1024*2
            
            fs.remove_recursive(tmpdir)
            
            # Check deleted
            dirs, files = fs.ls("/")
            assert tmpdir[1:] not in dirs
    
    def test_ls_and_remove_recursive(self, ser) -> None:
        with upy_filesystem(ser) as fs:
            tmpdir = f"/d{random.randint(0, 10000)}"
            fs.mkdir(tmpdir)
            
            # Make a large number of files and directories (to make sure we
            # don't encounter any timeout related issues with such large
            # numbers of files
            exp_dirs = {f"subdir_with_a_really_quite_long_name_you_know{i}" for i in range(30)}
            exp_files = {f"file_with_a_really_quite_long_name_you_know{i}" for i in range(30)}
            for dir in exp_dirs:
                fs.mkdir(f"{tmpdir}/{dir}")
            for file in exp_files:
                fs.write_file_raw(f"{tmpdir}/{file}", "")
            
            dirs, files = fs.ls(tmpdir)
            
            assert set(dirs) == exp_dirs
            assert set(files) == exp_files
            
            # Now remove (without timing out!)
            fs.remove_recursive(tmpdir)
            
            # Check deleted
            dirs, files = fs.ls("/")
            assert tmpdir[1:] not in dirs
    
    def test_sync(self, ser) -> None:
        with upy_filesystem(ser) as fs:
            # Pretty much all we can do is check this doesn't crash...
            fs.sync()

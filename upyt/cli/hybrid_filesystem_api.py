"""
This module implements a wrapper around :py:class:`upyt.upy_fs.FilesystemAPI`
which accesses files on the host when they do not start with a colon (':') and
on the device when they do.
"""

from upyt.upy_fs import FilesystemAPI, PathType

import os
from pathlib import Path
from shutil import rmtree


class HybridFilesystemAPI:
    
    def __init__(self, fs: FilesystemAPI) -> None:
        self._fs = fs
    
    def get_type(self, path: str) -> PathType:
        if path.startswith(":"):
            return self._fs.get_type(path[1:])
        else:
            if Path(path).exists():
                if Path(path).is_dir():
                    return PathType.dir
                else:
                    return PathType.file
            else:
                return PathType.absent
    
    def mkdir(self, path: str, parents: bool = False, exist_ok: bool = False) -> None:
        if path.startswith(":"):
            self._fs.mkdir(path[1:], exist_ok=exist_ok, parents=parents)
        else:
            Path(path).mkdir(exist_ok=exist_ok, parents=parents)
    
    def remove_recursive(self, path: str) -> None:
        if path.startswith(":"):
            self._fs.remove_recursive(path[1:])
        else:
            if Path(path).is_file():
                Path(path).unlink()
            else:
                rmtree(Path(path))
    
    def ls(self, path: str) -> tuple[list[str], list[str]]:
        if path.startswith(":"):
            return self._fs.ls(path[1:])
        else:
            dirs = []
            files = []
            for entry in Path(path).iterdir():
                if entry.is_dir():
                    dirs.append(entry.name)
                else:
                    files.append(entry.name)
            return (dirs, files)
    
    def rename(self, old_path: str, new_path: str) -> None:
        assert old_path.startswith(":") is new_path.startswith(":")
        
        if old_path.startswith(":"):
            self._fs.rename(old_path[1:], new_path[1:])
        else:
            Path(old_path).replace(new_path)
    
    def write_file(self, path: str, content: bytes) -> None:
        if path.startswith(":"):
            self._fs.write_file(path[1:], content)
        else:
            Path(path).write_bytes(content)
    
    def read_file(self, path: str) -> bytes:
        if path.startswith(":"):
            return self._fs.read_file(path[1:])
        else:
            return Path(path).read_bytes()
    
    def file_len(self, path: str) -> int:
        if path.startswith(":"):
            return self._fs.file_len(path[1:])
        else:
            return Path(path).stat().st_size
    
    def sync(self) -> None:
        self._fs.sync()
        os.sync()
    
    def update_file(self, *_, **__) -> None:
        raise NotImplementedError()

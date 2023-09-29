"""
Test utilities for creating defined file/directory structures and reading them
back again.
"""

from typing import Any

from pathlib import Path

import shutil

from upyt.upy_fs import FilesystemAPI


def write_device_tree(fs: FilesystemAPI, root: str, tree: dict[str, Any] | bytes) -> None:
    """
    Produce a directory hierarchy based on a simple description, e.g.
    
        >>> write_device_tree(fs, "/", {"a.txt": b"I am a.txt", "b": {"c.txt": b"I am b/c.txt"}})
        
        Produces a tree:
        
        /
            a.txt  -> I am a.txt
            b/
                c.txt -> I am b/c.txt
    """
    if isinstance(tree, bytes):
        fs.write_file(root, tree)
    else:
        fs.mkdir(root, exist_ok=True)
        for name, child in tree.items():
            write_device_tree(fs, f"{root}/{name}", child)

def read_device_tree(fs: FilesystemAPI, root: str) -> dict[str, Any] | bytes:
    """
    The inverse of write_device_tree.
    
        Given a file tree
        
        /
            a.txt  -> I am a.txt
            b/
                c.txt -> I am b/c.txt
        
        >>> read_device_tree(fs, "/")
        {"a.txt": b"I am a.txt", "b": {"c.txt": b"I am b/c.txt"}}
        
    """
    if fs.get_type(root).is_dir():
        directories, files = fs.ls(root)
        return {
            path: read_device_tree(fs, f"{root}/{path}")
            for path in directories + files
        }
    else:
        return fs.read_file(root)

def write_local_tree(root: Path, tree: dict[str, Any] | bytes) -> None:
    """
    Produce a directory hierarchy based on a simple description, e.g.
    
        >>> write_local_tree(Path(...), {"a.txt": b"I am a.txt", "b": {"c.txt": b"I am b/c.txt"}})
        
        Produces a tree:
        
        .../
            a.txt  -> I am a.txt
            b/
                c.txt -> I am b/c.txt
    """
    if isinstance(tree, bytes):
        if root.is_dir():
            shutil.rmtree(root)
        root.write_bytes(tree)
    else:
        if root.is_file():
            root.unlink()
        root.mkdir(exist_ok=True)
        for name, child in tree.items():
            write_local_tree(root / name, child)


def read_local_tree(root: Path) -> dict[str, Any] | bytes:
    """
    The inverse of write_local_tree.
    
        Given a file tree
        
        .../
            a.txt  -> I am a.txt
            b/
                c.txt -> I am b/c.txt
        
        >>> read_local_tree(Path(...))
        {"a.txt": b"I am a.txt", "b": {"c.txt": b"I am b/c.txt"}}
        
    """
    if root.is_dir():
        return {
            path.name: read_local_tree(path)
            for path in root.iterdir()
        }
    else:
        return root.read_bytes()

"""
We load all paths from the zipfile into a tree structure
so that we can quickly find files and directories.
"""

from pathlib import Path
from edit_data.zip_edits import FileTree, FileNode, is_dir, build_file_tree


def test_build_empty_file_tree():
    assert build_file_tree([]) == FileNode(".", {})


def test_build_file_tree():
    tree = build_file_tree(
        [
            Path("a/b/c.txt"),
            Path("a/b/d.txt"),
            Path("a/e/f.txt"),
            Path("g/h.txt"),
        ]
    )

    a_dir = tree.get_dir(Path("a"))
    assert set(a_dir.iterdir()) == {"b", "e"}
    assert isinstance(a_dir, FileNode)
    b_dir = tree.get_dir(Path("a/b"))
    assert b_dir is a_dir.get_dir(Path("b"))
    assert set(b_dir.iterdir()) == {"c.txt", "d.txt"}
    assert set(b_dir.children.values()) == {Path("a/b/c.txt"), Path("a/b/d.txt")}

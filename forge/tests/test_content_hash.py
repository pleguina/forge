"""
Tests for forge.core.utils.content_hash — the generic file-content hashing
primitive (release-plan §12/§5.1: staleness must not be mtime-only).
"""

from __future__ import annotations

import time

from forge.core.utils.content_hash import hash_bytes, hash_file, hash_files


def test_hash_bytes_deterministic():
    assert hash_bytes(b"hello") == hash_bytes(b"hello")
    assert hash_bytes(b"hello") != hash_bytes(b"world")


def test_hash_file_reflects_content_not_mtime(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("v1")
    h1 = hash_file(f)

    # Touch (change mtime) without changing content — hash must be identical.
    time.sleep(0.01)
    f.touch()
    h2 = hash_file(f)
    assert h1 == h2

    # Change content — hash must change.
    f.write_text("v2")
    h3 = hash_file(f)
    assert h3 != h1


def test_hash_files_order_independent(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("A")
    b.write_text("B")

    assert hash_files([a, b]) == hash_files([b, a])


def test_hash_files_changes_on_rename(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("same content")
    h_before = hash_files([a])

    renamed = tmp_path / "renamed.txt"
    a.rename(renamed)
    h_after = hash_files([renamed])

    assert h_before != h_after


def test_hash_files_changes_on_content_edit(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("original")
    h_before = hash_files([a])

    a.write_text("edited")
    h_after = hash_files([a])

    assert h_before != h_after

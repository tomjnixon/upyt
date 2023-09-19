import pytest

from unittest.mock import Mock

import random

from difflib import SequenceMatcher

from upyt.upy_fs import (
    upy_filesystem,
    data_to_writes,
    combine_sm_operations,
    data_to_update_commands,
    batch_commands,
    UpdateError,
    FilesystemAPI,
    PathType,
)


class TestDataToWrites:
    
    def test_empty(self) -> None:
        assert list(data_to_writes(b"", block_size=2)) == []
    
    def test_less_than_a_block(self) -> None:
        assert list(data_to_writes(b"1234", block_size=10)) == [("w(b'1234')", 4)]
        assert list(data_to_writes(b"1234", block_size=4)) == [("w(b'1234')", 4)]
    
    def test_multiple_blocks(self) -> None:
        assert list(data_to_writes(b"1234", block_size=2)) == [
            ("w(b'12')", 2),
            ("w(b'34')", 2),
        ]
        assert list(data_to_writes(b"1234", block_size=3)) == [
            ("w(b'123')", 3),
            ("w(b'4')", 1),
        ]
    
    def test_use_hex_if_smaller(self) -> None:
        assert list(data_to_writes(b"\xFF\xFF\xFF\xFF", block_size=4)) == [
            ("w(uh(b'ffffffff'))", 4),
        ]
    
    def test_decide_block_by_block(self) -> None:
        assert list(data_to_writes(b"hi\xFF\xFF", block_size=2)) == [
            ("w(b'hi')", 2),
            ("w(uh(b'ffff'))", 2),
        ]


class TestCombineSMOperations:
    
    def test_empty(self) -> None:
        assert list(combine_sm_operations([])) == []
    
    def test_single_insert_operation(self) -> None:
        assert list(
            combine_sm_operations(
                SequenceMatcher(None, "", "insert-me").get_opcodes(),
            )
        ) == [
            ("insert", None, None, 0, 9),
        ]
    
    def test_single_replace_operation(self) -> None:
        assert list(
            combine_sm_operations(
                SequenceMatcher(None, "REPLACE_ME", "replace-me").get_opcodes(),
            )
        ) == [
            ("insert", None, None, 0, 10),
        ]
    
    def test_single_delete_operation(self) -> None:
        assert list(
            combine_sm_operations(
                SequenceMatcher(None, "delete-me", "").get_opcodes(),
            )
        ) == []
    
    def test_single_equal_operation(self) -> None:
        assert list(
            combine_sm_operations(
                SequenceMatcher(None, "equal-me", "equal-me").get_opcodes(),
            )
        ) == [
            ("equal", 0, 8, None, None),
        ]
    
    def test_single_equal_operation_turned_into_insert_if_short(self) -> None:
        assert list(
            combine_sm_operations(
                SequenceMatcher(None, "e", "e").get_opcodes(),
                equal_overhead=10,
            )
        ) == [
            ("insert", None, None, 0, 1),
        ]

    def test_merge_insert_and_replace_operations(self) -> None:
        # NB: Insert and replace operations are never emitted sequentially by
        # the current version of sequence matcher but this is not a guarantee
        # so we explicitly test this here.
        assert list(
            combine_sm_operations(
                [
                    ("insert", 0, 0, 0, 5),
                    ("replace", 0, 3, 5, 8),
                    ("equal", 3, 5, 8, 10),
                ]
            )
        ) == [
              ("insert", None, None, 0, 8),
              ("equal", 3, 5, None, None),
        ]

    def test_merge_short_equal_with_previous_insert(self) -> None:
        # NB: These operations are never emitted by the current version of
        # sequence matcher but this is not a guarantee so we explicitly test
        # this here.
        assert list(
            combine_sm_operations(
                [
                    ("insert", 0, 0, 0, 5),
                    # Shorter than overhead, should be merged
                    ("equal", 0, 4, 5, 9),
                    # Multiple short equals should be merged
                    ("equal", 4, 5, 9, 10),
                    # Even if interspersed with deletions -- though in such
                    # cases we'll inevetably need a seek so should be allowed
                    # to go for longer
                    ("delete", 5, 9, 10, 10),
                    ("equal", 9, 16, 10, 17),
                    # NB: we should be able to do this twice since the previous
                    # euqal should be omitted and therefore a seek should still
                    # be needed!
                    ("replace", 16, 17, 17, 18),
                    ("equal", 17, 24, 18, 25),
                    # Longer than overhead, should be inserted as an equal
                    ("equal", 24, 34, 25, 35),
                    # Check that when no seek is needed, the threshold is lower
                    # (and so the equal below should be kept as an equal)
                    ("insert", 34, 34, 35, 40),
                    ("equal", 34, 39, 40, 45),
                ],
                equal_overhead=4,
                seek_overhead=3,
            )
        ) == [
              ("insert", None, None, 0, 25),
              ("equal", 24, 34, None, None),
              ("insert", None, None, 35, 40),
              ("equal", 34, 39, None, None),
        ]

    def test_merge_initial_short_equal_into_following_insert(self) -> None:
        assert list(
            combine_sm_operations(
                [
                    ("equal", 0, 1, 0, 1),
                    ("insert", 1, 1, 1, 3),
                ],
                equal_overhead=2,
            )
        ) == [
              ("insert", None, None, 0, 3),
        ]

    def test_do_not_merge_initial_long_equal_into_following_insert(self) -> None:
        assert list(
            combine_sm_operations(
                [
                    ("equal", 0, 3, 0, 3),
                    ("insert", 3, 3, 3, 4),
                ],
                equal_overhead=2,
            )
        ) == [
              ("equal", 0, 3, None, None),
              ("insert", None, None, 3, 4),
        ]

    @pytest.mark.parametrize(
        "before, after, equal_overhead, exp_operations",
        [
            # Trivial cases
            ("", "", 0, 0),
            ("already-equal", "already-equal", 0, 1),
            ("", "all-insertion", 0, 1),
            ("all-deletion", "", 0, 0),
            ("some-REPLACEMENT", "some-replacement", 0, 2),
            # Equality merged with insertions
            ("aa-bb-cc", "AA-BB-CC", 0, 5),  # No overhead so will use insert and equal
            ("aa-bb-cc", "AA-BB-CC", 2, 1),  # Should turn into a big insertion
            # Deletions between merged-in equal blocks
            ("WHAT-was-HERE", "what--here", 0, 4),  # No overhead so will use insert and equal
            ("WHAT-was-HERE", "what--here", 2, 1),  # Should turn into big insertion
        ],
    )
    def test_correctness(
        self,
        before: str,
        after: str,
        equal_overhead: int,
        exp_operations: int,
    ) -> None:
        out = []
        for opcode, i1, i2, j1, j2 in combine_sm_operations(
            SequenceMatcher(None, before, after).get_opcodes(),
            equal_overhead=equal_overhead,
        ):
            if opcode == "insert":
                out.append(after[j1:j2])
                assert i1 is None
                assert i2 is None
            elif opcode == "equal":
                out.append(before[i1:i2])
                assert j1 is None
                assert j2 is None
            else:
                assert False, opcode
        
        # Check the substitutions actually do what they're supposed to
        assert "".join(out) == after
        assert len(out) == exp_operations

class TestDataToUpdateCommands:

    def test_empty(self) -> None:
        assert list(data_to_update_commands(b"", b"")) == []

    def test_empty_afterwards(self) -> None:
        assert list(data_to_update_commands(b"1234", b"")) == []

    def test_equal_blocking(self) -> None:
        assert list(data_to_update_commands(b"12345678910", b"12345678910", block_size=20)) == [
            ("w(r(11))", 11),
        ]
        assert list(data_to_update_commands(b"12345678910", b"12345678910", block_size=11)) == [
            ("w(r(11))", 11),
        ]
        assert list(data_to_update_commands(b"12345678910", b"12345678910", block_size=8)) == [
            ("w(r(8))", 8),
            ("w(r(3))", 3),
        ]
    
    def test_insertion_blocking(self) -> None:
        assert list(data_to_update_commands(b"12345678910", b"XXX12345678910", block_size=2)) == [
            ("w(b'XX')", 2),
            ("w(b'X')", 1),
            ("w(r(2))", 2),
            ("w(r(2))", 2),
            ("w(r(2))", 2),
            ("w(r(2))", 2),
            ("w(r(2))", 2),
            ("w(r(1))", 1),
        ]
    
    def test_seeking_after_deletion(self) -> None:
        assert list(data_to_update_commands(b"123456789101112", b"23456789101112")) == [
            ("s(1)", 0),
            ("w(r(14))", 14),
        ]
    
    def test_seeking_after_replacement(self) -> None:
        # NB: Note that the extra here has to be quite long to force the seek
        # to be worth it!
        assert list(data_to_update_commands(b"1234567891011121314", b"XX34567891011121314")) == [
            ("w(b'XX')", 2),
            ("s(2)", 0),
            ("w(r(17))", 17),
        ]
    
    def test_no_seeking_after_insertion(self) -> None:
        assert list(data_to_update_commands(
            b"12345678910111213141516",
            b"XXX12345678910YYY111213141516",
        )) == [
            ("w(b'XXX')", 3),
            ("w(r(11))", 11),
            ("w(b'YYY')", 3),
            ("w(r(12))", 12),
        ]
    
    def test_hasher(self) -> None:
        hashed = []
        
        assert list(
            data_to_update_commands(
                b"1234567891011121314151617181920212223",
                b"XXX34567891011121314Y1617181920212223",
                block_size=2,
                hasher=hashed.append,
            )
        ) == [
            ("w(b'XX')", 2),
            ("w(b'X')", 1),
            ("s(2)", 0),
            ("w(r(2))", 2),
            ("w(r(2))", 2),
            ("w(r(2))", 2),
            ("w(r(2))", 2),
            ("w(r(2))", 2),
            ("w(r(2))", 2),
            ("w(r(2))", 2),
            ("w(r(2))", 2),
            ("w(r(1))", 1),
            ("w(b'Y')", 1),
            ("s(21)", 0),
            ("w(r(2))", 2),
            ("w(r(2))", 2),
            ("w(r(2))", 2),
            ("w(r(2))", 2),
            ("w(r(2))", 2),
            ("w(r(2))", 2),
            ("w(r(2))", 2),
            ("w(r(2))", 2),
        ]
        
        # Should only hash input components which are used.
        assert hashed == [
            # NB: Implementation detial which could validly be done
            # differently: always hashes full region at once even when written
            # out in blocks
            b"34567891011121314",
            b"1617181920212223",
        ]
    
    @pytest.mark.parametrize(
        "old, new, exp_seek_overhead, exp_equal_overhead",
        [
            # NB: The 'old' value is what determines the seek length
            (b"X", b"Y", len("s(1)\n"), len("w(r(9))\n")),
            (b"X"*1024, b"Y", len("s(512)\n"), len("w(r(9))\n")),
            # NB: The 'new' value is what determines whether we should assume
            # hex or not.
            (b"X"*1024, b"not-hex", len("s(512)\n"), len("w(r(9))\n")),
            (b"X"*1024, b"\xFF"*10, len("s(512)\n") // 2, len("w(r(9))\n") // 2),
        ],
    )
    def test_overhead_values(
        self,
        monkeypatch,
        old: bytes,
        new: bytes,
        exp_seek_overhead: int,
        exp_equal_overhead: int,
    ) -> None:
        from upyt import upy_fs
        mock_combine_sm_operations = Mock(side_effect=combine_sm_operations)
        monkeypatch.setattr(upy_fs, "combine_sm_operations", mock_combine_sm_operations)
        
        list(data_to_update_commands(old, new))
        
        assert len(mock_combine_sm_operations.mock_calls) == 1
        call = mock_combine_sm_operations.mock_calls[0]
        
        assert call[2]["seek_overhead"] == exp_seek_overhead
        assert call[2]["equal_overhead"] == exp_equal_overhead

class TestBatchCommands:
    
    def test_empty(self) -> None:
        assert list(batch_commands([])) == []
    
    def test_fit_in_single_batch(self) -> None:
        assert list(batch_commands([("a", 1), ("b", 10)])) == [("a\nb", 11)]
        
        assert list(batch_commands(
            [("a", 1), ("b", 10)],
            bytes_per_batch=11,
            commands_per_batch=2,
        )) == [("a\nb", 11)]
    
    def test_split_on_bytes(self) -> None:
        assert list(batch_commands(
            [("a", 51), ("b", 49)],
            bytes_per_batch=99,
        )) == [("a", 51), ("b", 49)]
    
    def test_split_on_commands(self) -> None:
        assert list(batch_commands(
            [("a", 10), ("b", 20), ("c", 30)],
            commands_per_batch=2,
        )) == [
            ("a\nb", 30),
            ("c", 30),
        ]


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
    
    def test_read_and_write_file(self, ser) -> None:
        with upy_filesystem(ser) as fs:
            tmpdir = f"/d{random.randint(0, 10000)}"
            fs.mkdir(tmpdir)
            
            # Bytes literal mode
            fs.write_file(f"{tmpdir}/foo", b"Hello, world!\n")
            assert fs.read_file(f"{tmpdir}/foo") == b"Hello, world!\n"
            
            # Hex mode
            fs.write_file(f"{tmpdir}/foo", b"\xFF" * 10)
            assert fs.read_file(f"{tmpdir}/foo") == b"\xFF" * 10
            
            # Chop into blocks (multiple of size)
            fs.write_file(f"{tmpdir}/foo", b"Hello, world!\n", block_size=2)
            assert fs.read_file(f"{tmpdir}/foo", block_size=2) == b"Hello, world!\n"
            
            # Chop into blocks (not a multiple of size)
            fs.write_file(f"{tmpdir}/foo", b"Hello, world!\n", block_size=3)
            assert fs.read_file(f"{tmpdir}/foo", block_size=3) == b"Hello, world!\n"
            
            # Run on something large with default block size
            fs.write_file(f"{tmpdir}/foo", b"X"*1024*4)
            assert fs.read_file(f"{tmpdir}/foo") == b"X"*1024*4
            
            # Run on something large in hex mode with default block size
            fs.write_file(f"{tmpdir}/foo", b"\xFF"*1024*2)
            assert fs.read_file(f"{tmpdir}/foo") == b"\xFF"*1024*2
            
            fs.remove_recursive(tmpdir)
            
            # Check deleted
            dirs, files = fs.ls("/")
            assert tmpdir[1:] not in dirs
    
    def test_rename_files(self, ser) -> None:
        with upy_filesystem(ser) as fs:
            tmpdir = f"/d{random.randint(0, 10000)}"
            fs.mkdir(tmpdir)
            
            fs.write_file(f"{tmpdir}/foo", b"Hello, world!\n")
            
            fs.rename(f"{tmpdir}/foo", f"{tmpdir}/bar")
            assert fs.ls(tmpdir)[1] == ["bar"]
            assert fs.read_file(f"{tmpdir}/bar") == b"Hello, world!\n"
            
            fs.remove_recursive(tmpdir)
            
            # Check deleted
            dirs, files = fs.ls("/")
            assert tmpdir[1:] not in dirs
    
    def test_rename_directories(self, ser) -> None:
        with upy_filesystem(ser) as fs:
            tmpdir = f"/d{random.randint(0, 10000)}"
            fs.mkdir(tmpdir)
            fs.mkdir(f"{tmpdir}/foo")
            fs.write_file(f"{tmpdir}/foo/a", b"I'm a")
            fs.write_file(f"{tmpdir}/foo/b", b"I'm b")
            
            fs.rename(f"{tmpdir}/foo", f"{tmpdir}/bar")
            assert fs.ls(tmpdir)[0] == ["bar"]
            assert set(fs.ls(f"{tmpdir}/bar")[1]) == {"a", "b"}
            assert fs.read_file(f"{tmpdir}/bar/a") == b"I'm a"
            assert fs.read_file(f"{tmpdir}/bar/b") == b"I'm b"
            
            fs.remove_recursive(tmpdir)
            
            # Check deleted
            dirs, files = fs.ls("/")
            assert tmpdir[1:] not in dirs
    
    def test_rename_bad_source(self, ser) -> None:
        with upy_filesystem(ser) as fs:
            tmpdir = f"/d{random.randint(0, 10000)}"
            fs.mkdir(tmpdir)
            
            with pytest.raises(OSError):
                fs.rename(f"{tmpdir}/foo", f"{tmpdir}/bar")
            
            fs.remove_recursive(tmpdir)
            
            # Check deleted
            dirs, files = fs.ls("/")
            assert tmpdir[1:] not in dirs
    
    def test_rename_bad_destination(self, ser) -> None:
        with upy_filesystem(ser) as fs:
            tmpdir = f"/d{random.randint(0, 10000)}"
            fs.mkdir(tmpdir)
            
            fs.write_file(f"{tmpdir}/foo", b"Hello, world!\n")
            
            with pytest.raises(OSError):
                fs.rename(f"{tmpdir}/foo", f"{tmpdir}/bar/baz")
            
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
                fs.write_file(f"{tmpdir}/{file}", b"")
            
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
    
    def test_get_type(self, ser) -> None:
        with upy_filesystem(ser) as fs:
            tmpdir = f"/d{random.randint(0, 10000)}"
            fs.mkdir(tmpdir)
            
            fs.write_file(f"{tmpdir}/a_file", b"hello")
            fs.mkdir(f"{tmpdir}/a_dir")
            
            assert fs.get_type(f"{tmpdir}/a_file") == PathType.file
            assert fs.get_type(f"{tmpdir}/a_dir") == PathType.dir
            with pytest.raises(OSError):
                fs.get_type(f"{tmpdir}/absent")
            
            fs.remove_recursive(tmpdir)
    
    def test_update_file_basic_case(self, ser) -> None:
        with upy_filesystem(ser) as fs:
            tmpdir = f"/d{random.randint(0, 10000)}"
            fs.mkdir(tmpdir)
            
            old_content = b"Hello there!"
            new_content = b"Hello, world!"
            fs.write_file(f"{tmpdir}/foo", old_content)
            fs.update_file(f"{tmpdir}/foo", old_content, new_content)
            assert fs.read_file(f"{tmpdir}/foo") == new_content
            
            fs.remove_recursive(tmpdir)
            
            # Check deleted
            dirs, files = fs.ls("/")
            assert tmpdir[1:] not in dirs
    
    def test_update_file_large_edit(self, ser) -> None:
        with upy_filesystem(ser) as fs:
            tmpdir = f"/d{random.randint(0, 10000)}"
            fs.mkdir(tmpdir)
            
            old_content = b"\xFF"
            new_content = b"\xFF" * 4 * 1024
            fs.write_file(f"{tmpdir}/foo", old_content)
            fs.update_file(f"{tmpdir}/foo", old_content, new_content)
            assert fs.read_file(f"{tmpdir}/foo") == new_content
            
            fs.remove_recursive(tmpdir)
            
            # Check deleted
            dirs, files = fs.ls("/")
            assert tmpdir[1:] not in dirs
    
    def test_update_file_many_edits(self, ser) -> None:
        with upy_filesystem(ser) as fs:
            tmpdir = f"/d{random.randint(0, 10000)}"
            fs.mkdir(tmpdir)
            
            old_content = b"\xFF" * 4 * 1024
            new_content = b"".join(
                bytes([c]) if (i % 2) else b"\00"
                for i, c in enumerate(old_content)
            )
            fs.write_file(f"{tmpdir}/foo", old_content)
            fs.update_file(f"{tmpdir}/foo", old_content, new_content)
            assert fs.read_file(f"{tmpdir}/foo") == new_content
            
            fs.remove_recursive(tmpdir)
            
            # Check deleted
            dirs, files = fs.ls("/")
            assert tmpdir[1:] not in dirs
    
    def test_update_safe_mode_off(self, ser) -> None:
        with upy_filesystem(ser) as fs:
            tmpdir = f"/d{random.randint(0, 10000)}"
            fs.mkdir(tmpdir)
            
            fs.write_file(f"{tmpdir}/foo", b"HELLO?")
            # Should get a mangled file but shouldn't crash when safe-mode
            # disabled.
            fs.update_file(
                f"{tmpdir}/foo",
                b"Hello there how are you?",
                b"Hello there how are we?!",
                safe=False,
            )
            assert fs.read_file(f"{tmpdir}/foo") != b"HELLO?"
            
            fs.remove_recursive(tmpdir)
            
            # Check deleted
            dirs, files = fs.ls("/")
            assert tmpdir[1:] not in dirs
    
    def test_update_wrong_old_content(self, ser) -> None:
        with upy_filesystem(ser) as fs:
            tmpdir = f"/d{random.randint(0, 10000)}"
            fs.mkdir(tmpdir)
            
            fs.write_file(f"{tmpdir}/foo", b"NOPE!")
            with pytest.raises(UpdateError):
                fs.update_file(
                    f"{tmpdir}/foo",
                    b"Hello there how are you?",
                    b"Hello there how are we?!",
                    safe=True,
                )
            
            # Shouldn't have changed the file on failure
            assert fs.read_file(f"{tmpdir}/foo") == b"NOPE!"
            
            # Shouldn't have left any temporary files behind
            assert fs.ls(tmpdir) == ([], ["foo"])
            
            fs.remove_recursive(tmpdir)
            
            # Check deleted
            dirs, files = fs.ls("/")
            assert tmpdir[1:] not in dirs

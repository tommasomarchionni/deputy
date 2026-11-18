# pyright: basic
from pathlib import Path

import pytest

from deputy.shellreads import ShellRead, shell_edits, shell_reads, working_folder


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("cat a.md", [ShellRead("a.md")]),
        ("cat a.md b.md", [ShellRead("a.md"), ShellRead("b.md")]),
        ("/bin/cat a.md", [ShellRead("a.md")]),
        ("nl a.md", [ShellRead("a.md")]),
        ("sed -n 10,20p a.md", [ShellRead("a.md", first=10, last=20)]),
        ("sed -n '7p' a.md", [ShellRead("a.md", first=7, last=7)]),
        ("head -n 50 a.md", [ShellRead("a.md", first=1, last=50)]),
        ("head -50 a.md", [ShellRead("a.md", first=1, last=50)]),
        ("head a.md", [ShellRead("a.md", first=1, last=10)]),
        ("tail -n 30 a.md", [ShellRead("a.md", from_end=30)]),
        ("cd sub && cat a.md", [ShellRead("a.md")]),
        ("LANG=C cat a.md", [ShellRead("a.md")]),
        ("cat a.md 2>/dev/null", [ShellRead("a.md")]),
        ("cat a.md; cat b.md", [ShellRead("a.md"), ShellRead("b.md")]),
        ("cat a.md | grep x", [ShellRead("a.md", filtered=True)]),
        ("cat -n a.md", [ShellRead("a.md")]),
        ("nl -ba a.md", [ShellRead("a.md")]),
        ("nl -b a a.md", [ShellRead("a.md")]),
        ("head -n50 a.md", [ShellRead("a.md", first=1, last=50)]),
        ("tail -n +100 a.md", [ShellRead("a.md", first=100)]),
        ("tail -20 a.md", [ShellRead("a.md", from_end=20)]),
        ("sed -n '/^def a/,/^def b/p' a.py", [ShellRead("a.py", pattern="/^def a/,/^def b/p")]),
        ("sed -n '1,5p;9p' a.py", [ShellRead("a.py", pattern="1,5p;9p")]),
        ("sed -n '$p' a.py", [ShellRead("a.py", pattern="$p")]),
        ("cat 'a b.md'", [ShellRead("a b.md")]),
        ("set -e\ncat a.md", [ShellRead("a.md")]),
        ("cat a.md \\\n  b.md", [ShellRead("a.md"), ShellRead("b.md")]),
        ("grep x y; cat a.md", [ShellRead("a.md")]),
    ],
)
def test_plain_reads_are_recognised(command: str, expected: list[ShellRead]) -> None:
    assert shell_reads(command) == expected


@pytest.mark.parametrize(
    "command",
    [
        "sed -i s/a/b/ a.md",
        "sed -n s/a/bp a.md",
        "sed s/a/b/ a.md",
        "tail -f a.log",
        "tail -F a.log",
        "head -c 100 a.md",
        "cat -A a.md",
        "cat a.md > b.md",
        "cat a.md >> b.md",
        "cat < a.md",
        "cat $(ls)",
        "cat `ls`",
        "git log",
        "grep x a.md",
        "cat 'unterminated",
        "cat $f",
        "f=a.md; sed -n 1,5p $f",
        "cat docs/*.md",
        "sed -n 's/a/b/p' a.md",
        "sed -n '1w out' a.md",
        "sed -i '' 's/a;b/c/' a.md",
        "sed -i '' 's/a/b/' a.md && sed -n 1,5p a.md",
        "sed --in-place s/a/b/ a.md",
        "cat <<EOF\nhello\nEOF",
        "echo 'cat a.md'",
        "for f in a b; do cat x; done",
        "cat a.md; for f in a b; do sed -i s/x/y/ $f; done",
        "if true; then cat a.md; fi",
        "",
    ],
)
def test_anything_but_a_plain_read_is_no_read(command: str) -> None:
    assert shell_reads(command) == []


@pytest.mark.parametrize(
    ("command", "folder"),
    [("cd sub && cat a", "sub"), ("cd '/a b'; cat a", "/a b"), ("cat a", None), ("cd", None)],
)
def test_the_leading_folder_is_found(command: str, folder: str | None) -> None:
    assert working_folder(command) == folder


@pytest.mark.parametrize(
    ("command", "edited"),
    [
        ("sed -i '' 's/a;b/c/' a.md", ["a.md"]),
        ("sed -i s/a/b/ a.md", ["a.md"]),
        ("cd x && sed -i '' 's/a/b/' a.md && cat b.md", ["a.md"]),
        ("sed -i s/a/b/ $f", []),
        ("sed -n 1,5p a.md", []),
        ("cat a.md", []),
    ],
)
def test_in_place_edits_are_found(command: str, edited: list[str]) -> None:
    assert shell_edits(command) == edited


def test_the_home_folder_is_expanded() -> None:
    assert shell_reads("cat ~/a.md") == [ShellRead(str(Path.home() / "a.md"))]
    assert working_folder("cd ~/x && cat a") == str(Path.home() / "x")

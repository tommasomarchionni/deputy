# pyright: basic
import pytest

from deputy.shellreads import ShellRead, shell_reads, working_folder


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

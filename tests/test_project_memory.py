# pyright: basic
import asyncio
import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from deputy import cli, server
from deputy import project_memory as pm
from deputy.config import state_dir
from deputy.sandbox import Sandbox, SandboxError
from deputy.server import build_server
from helpers import message


def make_repo(tmp_path: Path, name: str = "repo") -> Path:
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    (root / "AGENTS.md").write_text("rules\n")
    (root / ".github" / "instructions").mkdir(parents=True)
    (root / ".github" / "instructions" / "tests.instructions.md").write_text("tests\n")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x = 1\n")
    (root / ".env").write_text("TOKEN=1\n")
    return root


def test_a_folder_inside_a_repository_belongs_to_the_repository_root(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    assert pm.repo_root(root / "src") == root.resolve()
    assert pm.repo_root(root) == root.resolve()
    loose = tmp_path / "loose"
    loose.mkdir()
    assert pm.repo_root(loose) == loose.resolve()  # not in a repository: the folder itself


def test_two_repositories_never_share_a_key_and_the_same_one_always_does(tmp_path: Path) -> None:
    a, b = make_repo(tmp_path, "a"), make_repo(tmp_path, "b")
    assert pm.repo_id(a) != pm.repo_id(b)
    assert pm.repo_id(a) == pm.repo_id(a / "src" / "..")


def test_a_pointer_must_be_a_real_file_inside_the_repository_and_not_a_secret(
    tmp_path: Path,
) -> None:
    root = make_repo(tmp_path)
    memory = pm.ProjectMemory()
    added = pm.add_pointer(memory, root, "AGENTS.md", "the rules")
    assert added.pointers[0].path == "AGENTS.md"
    for bad in ("../outside.md", "missing.md", "src", ".env", str(tmp_path / "x")):
        with pytest.raises(pm.ProjectMemoryError):
            pm.add_pointer(memory, root, bad, "why")
    with pytest.raises(pm.ProjectMemoryError, match="why"):
        pm.add_pointer(memory, root, "AGENTS.md", "")
    with pytest.raises(pm.ProjectMemoryError, match="why"):
        pm.add_pointer(memory, root, "AGENTS.md", "x" * 500)


def test_adding_again_replaces_and_the_limits_hold(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    memory = pm.add_pointer(pm.ProjectMemory(), root, "AGENTS.md", "first")
    memory = pm.add_pointer(memory, root, "AGENTS.md", "second")
    assert [p.why for p in memory.pointers] == ["second"]
    full = pm.ProjectMemory(terms=[pm.Term(term=f"t{i}", meaning="m") for i in range(pm.MAX_TERMS)])
    with pytest.raises(pm.ProjectMemoryError, match="at most"):
        pm.add_term(full, "one more", "m")
    assert (
        pm.add_term(full, "t3", "new meaning").terms[-1].meaning == "new meaning"
    )  # replace is fine
    with pytest.raises(pm.ProjectMemoryError):
        pm.add_term(pm.ProjectMemory(), "", "m")


def test_forgetting_works_by_path_or_term_and_says_when_there_is_nothing(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    memory = pm.add_term(
        pm.add_pointer(pm.ProjectMemory(), root, "AGENTS.md", "rules"), "Lease", "a hold"
    )
    assert pm.remove(memory, "AGENTS.md").pointers == []
    assert pm.remove(memory, "lease").terms == []
    with pytest.raises(pm.ProjectMemoryError, match="nothing remembered"):
        pm.remove(memory, "nope")


def test_scan_finds_the_rule_files_of_a_repository_and_never_secrets(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    found = [p.path for p in pm.candidates(root)]
    assert found == ["AGENTS.md", ".github/instructions/tests.instructions.md"]
    assert pm.candidates(tmp_path / "empty-nothing") == []


def test_what_is_added_to_a_prompt_is_bounded_and_empty_when_there_is_nothing(
    tmp_path: Path,
) -> None:
    assert pm.prompt_notes(pm.ProjectMemory()) == ""
    memory = pm.ProjectMemory(
        pointers=[pm.Pointer(path=f"docs/{i}.md", why="w" * 100) for i in range(pm.MAX_POINTERS)],
        terms=[pm.Term(term="Lease", meaning="m" * 190)] * pm.MAX_TERMS,
    )
    assert len(pm.prompt_notes(memory)) <= pm.PROMPT_CHARS
    small = pm.ProjectMemory(
        pointers=[pm.Pointer(path="AGENTS.md", why="rules")],
        terms=[pm.Term(term="Lease", meaning="a hold")],
    )
    text = pm.prompt_notes(small)
    assert "- AGENTS.md: rules" in text
    assert "- Lease: a hold" in text


def test_the_markdown_export_is_reviewable_and_says_where_it_comes_from(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    memory = pm.add_term(
        pm.add_pointer(pm.ProjectMemory(repo=str(root)), root, "AGENTS.md", "the rules"),
        "Lease",
        "a hold",
    )
    text = pm.to_markdown(memory)
    assert text.startswith(f"# deputy memory for {root}")
    assert "- `AGENTS.md`: the rules" in text
    assert "- **Lease**: a hold" in text
    assert "(nothing yet)" in pm.to_markdown(pm.ProjectMemory())


def test_the_memory_is_stored_outside_the_repository_and_survives_a_round_trip(
    tmp_path: Path,
) -> None:
    root = make_repo(tmp_path)
    assert pm.load(root).pointers == []
    pm.save(root, pm.add_pointer(pm.ProjectMemory(), root, "AGENTS.md", "rules"))
    assert pm.load(root).pointers[0].path == "AGENTS.md"
    assert pm.load(root).repo == str(root.resolve())
    assert state_dir() in pm._file(root).parents  # in the state directory...
    assert root not in pm._file(root).parents  # ...never in the repository
    pm._file(root).write_text("{broken")
    assert pm.load(root).pointers == []


@given(st.lists(st.text(alphabet="abc xyz", min_size=1, max_size=30), max_size=40))
def test_whatever_is_remembered_a_prompt_never_exceeds_its_size(words: list[str]) -> None:
    memory = pm.ProjectMemory(terms=[pm.Term(term=w, meaning=w * 6) for w in words[: pm.MAX_TERMS]])
    assert len(pm.prompt_notes(memory)) <= pm.PROMPT_CHARS


def delegate_in(root: Path, fake_client, monkeypatch, workdir: Path | None = None) -> str:  # type: ignore[no-untyped-def]
    """Run one delegation in `workdir` and return the system prompt the worker received."""
    sent: list[str] = []
    client = fake_client([message("done")])
    original = client.chat

    async def spy(messages, tools=None):  # type: ignore[no-untyped-def]
        sent.append(messages[0]["content"])
        return await original(messages, tools)

    client.chat = spy  # type: ignore[method-assign]
    monkeypatch.setattr(server, "ChatClient", lambda backend: client)
    asyncio.run(
        build_server().call_tool("delegate", {"task": "go", "workdir": str(workdir or root)})
    )
    return sent[0]


def test_a_repository_memory_reaches_only_tasks_in_that_repository(
    tmp_path: Path, fake_client, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    cli.main(["init", "--yes", "--url", "http://x", "--model", "m"])
    a, b = make_repo(tmp_path, "a"), make_repo(tmp_path, "b")
    pm.save(
        a,
        pm.add_term(
            pm.add_pointer(pm.ProjectMemory(), a, "AGENTS.md", "rules of A"),
            "Lease",
            "holds a stage",
        ),
    )
    in_a = delegate_in(a, fake_client, monkeypatch)
    in_a_subfolder = delegate_in(a, fake_client, monkeypatch, workdir=a / "src")
    in_b = delegate_in(b, fake_client, monkeypatch)
    assert "rules of A" in in_a
    assert "Lease: holds a stage" in in_a
    assert "rules of A" in in_a_subfolder  # a folder inside the repository is the same repository
    assert "rules of A" not in in_b  # never another repository's task
    assert "Notes about the repository" not in in_b


def test_a_worker_cannot_reach_the_memory_it_is_given(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    pm.save(root, pm.add_pointer(pm.ProjectMemory(), root, "AGENTS.md", "rules"))
    box = Sandbox(root)
    for path in (str(pm._file(root)), str(state_dir() / "projects")):
        with pytest.raises(SandboxError):
            box.read_file(path)
        with pytest.raises(SandboxError):
            box.write_file(path, "{}")


def run_cli(*argv: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = cli.main(["memory", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_the_command_scans_adds_shows_exports_and_forgets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(tmp_path)
    repo = ["--repo", str(root / "src")]
    code, out, _ = run_cli("scan", *repo, capsys=capsys)
    assert code == 0
    assert "AGENTS.md" in out
    assert "report only" in out
    assert pm.load(root).pointers == []
    run_cli("scan", "--apply", *repo, capsys=capsys)
    assert len(pm.load(root).pointers) == 2
    assert "no new rule files" in run_cli("scan", *repo, capsys=capsys)[1]
    assert run_cli("term", "Lease", "holds a stage", *repo, capsys=capsys)[0] == 0
    shown = run_cli("show", *repo, capsys=capsys)[1]
    assert "- **Lease**: holds a stage" in shown
    assert run_cli("export", *repo, capsys=capsys)[1] == shown
    assert run_cli("remove", "Lease", *repo, capsys=capsys)[0] == 0
    code, _, err = run_cli("remove", "Lease", *repo, capsys=capsys)
    assert code == 1
    assert "nothing remembered" in err
    assert run_cli("pointer", ".env", "x", *repo, capsys=capsys)[0] == 1
    assert run_cli("pointer", *repo, capsys=capsys)[0] == 1  # needs a path
    assert json.loads(pm._file(root).read_text())["repo"] == str(root.resolve())

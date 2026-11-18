@AGENTS.md

## Claude Code

- The per-area rules in `.github/instructions/` are linked from `.claude/rules/`: they load when you
  open a file of the area. They are the same files GitHub Copilot reads; edit them only there.
- To use deputy itself from Claude Code, install the delegation skill in a project with
  `deputy instructions --for claude --apply` (a skill loads on demand; a `CLAUDE.md` block would cost
  tokens on every turn).
- Prefer focused commands while editing; run the whole suite once before pushing.

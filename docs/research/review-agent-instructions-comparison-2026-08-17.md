# Code review agent instructions: first-party comparison

Date: 2026-08-17

Status: Historical research supporting the repository's compact,
packet-driven reviewer workflow. First-party products and prompts may change;
the active skill contract remains authoritative.

## Conclusion

The current local `reviewer.toml` is more complex than it needs to be. First-party examples converge on a smaller reviewer contract:

1. identify the exact change to review;
2. report only concrete, introduced, actionable defects;
3. inspect tests and call sites to validate findings;
4. stay non-mutating;
5. return severity-ordered findings with precise locations, or `No findings.`

Context isolation, review-target resolution, packet construction, tool permissions, and workflow status belong primarily in the orchestrating skill or runtime. They do not need to be restated as a state machine inside `developer_instructions`.

## Comparison

| Implementation | Instruction size and placement | Context isolation | Review scope | Validation / tests | Mutation permission | Output contract |
|---|---|---|---|---|---|---|
| OpenAI Codex sample `review-agent` | 57 lines total; compact standalone skill | The sample says not to delegate. Codex review mode itself starts a one-shot sub-agent with `initial_history: None`, disables collaboration, and never asks for approval. | Complete requested diff, surrounding code, repository instructions, tests, and call sites; only introduced, demonstrable defects | Check relevant tests and call sites; it does **not** require a general test-suite rerun | Explicitly forbids modifying files, commits, pushes, and review comments | Findings first; P0-P3; precise diff-overlapping location; one short paragraph; `No findings.`; brief residual risks |
| OpenAI Cookbook Codex reviewer | Core behavioral prompt is only seven sentences; diff metadata and JSON schema are built outside the prompt | CI job provides a fresh checkout and an explicit prompt payload | Actionable issues introduced by the PR, focused on correctness, performance, security, maintainability, and developer experience | Verify file citations with available tools; no blanket test-running requirement | Codex action runs with `sandbox: read-only`; publishing comments is a later workflow step | External JSON schema supplies findings, priority, locations, overall verdict, and confidence |
| Anthropic Claude Code `code-reviewer` agent | 38 lines total; responsibilities, confidence threshold, and output guidance | Claude subagents have their own context window, prompt, tools, and permissions; the official minimal example uses a two-line prompt body | Default is unstaged `git diff`, overridable by the caller; project-rule compliance plus actual bugs and significant quality issues | Test coverage is review evidence; the agent prompt does not mandate executing tests | The feature-dev agent exposes read/search-oriented tools and no edit tool; the general docs recommend restricting tools in frontmatter | Only confidence >=80; file and line; rule or bug explanation; fix suggestion; Critical / Important; brief clean result |
| GitHub Copilot code review | Built-in reviewer prompt is not published; official customization guidance recommends short, specific instructions and starting with 10-20 rules | Review runs in an ephemeral environment and gathers repository context | PR changes plus repository-wide, path-specific, `AGENTS.md`, skill, and optional MCP context | Environment can be configured for tools and dependencies; public guidance does not make a test command part of every instruction | Suggested fixes are handed to a separate cloud agent; customization should not try to change Copilot's core function | Formatting and UX are owned by the product; custom instructions that try to change review-comment format are unsupported |
| Google Gemini CLI `code-reviewer` skill | 58 lines total; a workflow skill rather than a dedicated isolated agent | No separate-context contract in the skill | Local staged/unstaged changes or remote PR, with broad correctness, maintainability, readability, efficiency, security, edge-case, and testability checks | Remote PR: run `npm run preflight`; local substantial change: ask before preflight | Remote flow checks out the PR and later offers to switch branches, so this is not a read-only reviewer | Summary; Critical / Improvements / optional Nitpicks; Approved / Request Changes |

## Primary-source evidence

### OpenAI Codex

The strongest direct analogue is OpenAI's current sample delegated reviewer:

- [Review-agent sample, pinned source](https://github.com/openai/codex/blob/21cfd369efca2df70c904c580b2e7e2e3eddb3c3/codex-rs/skills/src/assets/samples/review-agent/SKILL.md#L8-L57)
- [Codex review task isolation, pinned source](https://github.com/openai/codex/blob/21cfd369efca2df70c904c580b2e7e2e3eddb3c3/codex-rs/core/src/tasks/review.rs#L97-L140)
- [Built-in review rubric, pinned source](https://github.com/openai/codex/blob/21cfd369efca2df70c904c580b2e7e2e3eddb3c3/codex-rs/prompts/templates/review/rubric.md#L1-L95)

Short excerpt: “Inspect the requested target directly and return every finding that the author would likely fix.”

The sample keeps target inspection, defect criteria, non-mutation, and output shape in the reviewer instructions. The runtime supplies the stronger isolation guarantee: it launches a separate one-shot review conversation with no initial history and disables nested collaboration. This means a custom reviewer prompt need not narrate how context isolation works.

The full built-in rubric is longer (95 lines), but most of that length defines a strict JSON-facing product contract and inline-comment behavior. It is not evidence that a user-level reviewer agent should repeat task-packet parsing and workflow failure states.

OpenAI's first-party CI tutorial makes that separation especially clear:

- [Build code review with the Codex SDK, pinned source](https://github.com/openai/openai-cookbook/blob/aa2c1e8867b83f73cdf8eedf94ebb6db41d69402/examples/codex/build_code_review_with_codex_sdk.md#L15-L43)
- [Diff construction outside the behavioral prompt](https://github.com/openai/openai-cookbook/blob/aa2c1e8867b83f73cdf8eedf94ebb6db41d69402/examples/codex/build_code_review_with_codex_sdk.md#L198-L249)

Its behavioral prompt is seven sentences. The workflow separately injects repository metadata and the diff, applies a JSON schema, and runs Codex read-only. This is the cleanest first-party evidence for keeping orchestration out of `developer_instructions`.

### Anthropic Claude Code

- [Feature-dev `code-reviewer`, pinned source](https://github.com/anthropics/claude-code/blob/ae58f7a0a23f0fddedd668de74622ff46646526b/plugins/feature-dev/agents/code-reviewer.md#L1-L38)
- [Official subagent documentation](https://code.claude.com/docs/en/sub-agents#write-subagent-files)
- [Multi-agent code-review command, pinned source](https://github.com/anthropics/claude-code/blob/ae58f7a0a23f0fddedd668de74622ff46646526b/plugins/code-review/commands/code-review.md#L20-L72)

Short excerpt: “Only report issues with confidence ≥ 80.”

Anthropic's dedicated reviewer is just 38 lines. The official subagent documentation shows an even smaller two-line reviewer body and states that subagents receive their own system prompt and basic environment details rather than the parent system prompt. Tool and permission restrictions are frontmatter/runtime concerns.

Anthropic does also ship a much more complex PR-review command. Its complexity lives in the **orchestrator**: collect scoped `CLAUDE.md` files, run four review agents, validate candidate findings with additional agents, filter false positives, and optionally publish comments. That design supports keeping a single reviewer agent narrow rather than duplicating orchestration logic inside it.

### GitHub Copilot

- [About Copilot code review](https://docs.github.com/en/copilot/concepts/agents/code-review)
- [Customizing Copilot code review](https://docs.github.com/en/copilot/tutorials/customize-code-review)
- [Using Copilot code review](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/request-a-code-review/use-code-review)

Short excerpt: “Begin with 10–20 specific instructions.”

GitHub explicitly recommends starting small and iterating. It treats repository rules, path-specific rules, `AGENTS.md`, and task-specific skills as distinct context layers. Its guidance also says attempts to change review-comment formatting or Copilot's core function are unsupported, reinforcing that product/runtime behavior should not be recreated in custom reviewer prose.

### Google Gemini CLI

- [Official `code-reviewer` skill, pinned source](https://github.com/google-gemini/gemini-cli/blob/9a15c45fbfc9f36a9817e0113dbd4fc1138840f0/.gemini/skills/code-reviewer/SKILL.md#L1-L58)

Short excerpt: “Execute the project's standard verification suite to catch automated failures early.”

Gemini's example is useful as a contrasting workflow skill: it performs checkout and preflight operations and has a broad review taxonomy. It is not a context-isolated, read-only reviewer agent, so its operational steps should not be copied into the local `developer_instructions` unless that behavior is actually desired.

## Implications for the local reviewer

Keep:

- independent judgment from the supplied target and repository evidence;
- exact diff scope and applicable repository rules;
- concrete, introduced, actionable defect threshold;
- tests and call sites as confirmation evidence;
- a one-line non-mutation rule, because the configured sandbox currently permits workspace writes;
- severity, location, scenario, and `No findings.`

Move to the calling `code-review` skill or runtime:

- how the review packet is assembled;
- required packet fields and missing-field policy;
- context-isolation mechanics;
- exact base/merge-base resolution;
- generic workspace-preservation mechanics;
- the `Review blocked:` / `Review incomplete:` state taxonomy.

The last two statuses are defensible for a larger orchestration protocol, but they are not common in the first-party reviewer prompts inspected here. A reviewer can simply mention a material limitation after its findings or clean result.

## Suggested compact `developer_instructions`

```text
You are an independent code reviewer. Review only the supplied change against
the supplied task and applicable repository instructions.

Inspect the complete diff and enough surrounding code, tests, and call sites
to understand the changed behavior. Report only concrete, actionable defects
introduced by the change. Do not report speculation, pre-existing issues, or
style nits unless they violate an applicable repository rule.

Use the smallest targeted validation needed to confirm a finding. Do not
modify tracked files, commit, push, post comments, or delegate.

Return findings first, ordered by severity. Each finding must include a
P0-P3 priority, file and line, affected scenario, and why it is wrong. If
there are no findings, return `No findings.` Briefly note any material test
gap or review limitation.
```

This is about 100 words. It retains the safeguards that change reviewer behavior while removing packet parsing and workflow-state machinery already owned by the caller.

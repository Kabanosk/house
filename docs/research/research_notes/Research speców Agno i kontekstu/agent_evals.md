# Evaluating coding agents and agent instructions/skills (state as of 2026-11-17)

Scope: how practitioners evaluate the behaviour of CLI coding agents (Claude Code, Codex CLI) and of the instructions that drive them (skills, playbooks, subagent profiles). Each finding is checked against `house eval` / `/house-improve` (spec `docs/specs/2026-11-16-agno-adoptions-design.md`, section 9, A3).

Method: web search and fetch, the local `claude plugin eval --help` on Claude Code 2.1.326, the local copy of Anthropic's `skill-creator` skill, and a delegated read of https://code.claude.com/docs/en/plugin-evals.md (fetched 2026-11-17). Material from before 2026 is marked **[older]**.

---

## 1. Anthropic guidance; can `claude plugin eval` replace our own runner?

### Takeaway
Anthropic's January 2026 guidance matches most of A3:
- deterministic graders first;
- several trials per task;
- pass^k when consistency matters;
- a clean environment per trial;
- tasks drawn from real failures.

It adds two things A3 lacks: **grade the outcome (final state), not only the path**, and **balanced positive/negative cases**.

`claude plugin eval` is real and mature (case folders, six grader types, 3 runs per case, a with/without-plugin baseline, JSON output, exit codes, a cost ceiling). It is **not usable as our main runner**, for four reasons:
- it loads only a plugin and never loads the project's CLAUDE.md or `.claude/`;
- it has no custom-code graders;
- it cannot run Codex;
- each run starts from an empty workspace that a bash scaffold has to fill.

We should copy its grader vocabulary, not the tool.

### Cited Findings

**"Demystifying evals for AI agents"** (Anthropic Engineering, 9 Jan 2026) — [Anthropic](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)

Graders:
- There are three grader types: code-based, model-based and human.
- Recommendation: "prefer deterministic graders where possible, LLM graders where needed, and human graders sparingly for validation."
- Scoring can be weighted, binary (all must pass) or hybrid.

Trials:
- Run multiple trials because outputs vary between runs.
- pass@k means at least one success in k tries. pass^k means all k succeed; use it "when consistency is essential".
- Worked example: a 75% per-trial rate gives about 42% for pass^3.

What to grade:
- Grade the outcome (final environment state) where possible: "A booking agent's claim of success matters less than whether the reservation exists."
- Rigid step-sequence checks are called "too rigid" and "overly brittle". Grade what the agent produced, not the exact path.

Isolation:
- Each trial must start from a clean environment. Shared state such as leftover files and cache causes correlated failures.
- In one case Claude gained an unfair advantage by reading **git history left by earlier trials**.

Building tasks:
- "20-50 simple tasks drawn from real failures is a great start."
- A good task is one on which two experts would agree pass/fail.
- Write a reference solution per task to prove the task is solvable and the grader works.
- A task at 0% across many trials suggests a broken task or grader.
- Balance the set: test cases where a behaviour should occur and cases where it should not, so the agent is not optimised one-sidedly.

Capability vs regression evals:
- Capability evals start low and give the team something to improve.
- Regression evals should sit near 100% and guard against backsliding.
- Capability tasks that saturate "graduate" into the regression suite.

LLM judges:
- Calibrate them against human experts.
- Give the judge a way out ("Unknown").
- Use a structured rubric and a separate isolated judge per dimension.

Reading transcripts:
- Read transcripts to verify graders: "Failures should seem fair".
- CORE-Bench example: Opus 4.5 scored 42% because of grading bugs and ambiguous specs, and about 95% after the fixes.

Gaming:
- Graders must resist bypasses, so that passing requires actually solving the problem.
- Agents can also find valid solutions the eval did not anticipate. In a τ2-bench case Opus 4.5 found a policy loophole that "failed" the eval as written but gave the user a better outcome.

**`claude plugin eval`: what it is**
- Exists in the local CLI (Claude Code 2.1.326). Help text: "Run eval cases (<eval dir>/**/case.yaml or prompt.md + graders/*.md …) against a plugin and report scored results. Target is a path, a plugin name, or a plugin@marketplace id." — local `claude plugin eval --help`
- Docs give Claude Code v2.1.269+ as the requirement. The embedded reference cites 2.1.198/2.1.210 for earlier versions; the conflict is unresolved. — [Claude Code docs: plugin evals](https://code.claude.com/docs/en/plugin-evals.md)

**Case format**
- Each case is a folder `evals/<case>/` containing:
  - `prompt.md`: YAML frontmatter plus the prompt as the body. Frontmatter fields: `tags`, `runs` (default 3, range 1–50), `model`, `max_turns` (default 10, max 200), `timeout_seconds` (default 300, max 3600), `allowed_tools`, `append_system_prompt`, `env` (keys must match `EVAL_*`).
  - `case.yaml`: `schema_version: "1.1"`, `name`, `expected_outcome`, `context.scaffold_script`, `context.history_file` (a .jsonl transcript to resume), `context.add_dirs` (read-only).
  - `graders/NN-*.md`: frontmatter `type`, `weight`, `arm`.

  — [plugin-evals docs](https://code.claude.com/docs/en/plugin-evals.md); confirmed by the local skill-creator `references/schemas.md`, which says new skill-creator tests are written "in the layout `claude plugin eval` reads".
- Grader types — [plugin-evals docs, grader types](https://code.claude.com/docs/en/plugin-evals.md#grader-types):
  - `regex`: `pattern`, `flags`, `match` with `not_contains` or `count:N`, and a `target`;
  - `tool_used`: `tool`, `input_match`, `min` (default 1), `max`;
  - `tool_order`: `before`, `after`;
  - `file_exists`: path glob; only files created during the run count;
  - `llm`: `criteria`, `focus`; the judge votes and the grader **passes on at least 2 of 3 votes**;
  - `baseline`: compares against a reference .jsonl transcript.
- Grader targets: `last_message`, `trace`, `files`, `{source: file, path}`, `mock_calls`. — same page
- "There are no custom-code graders." — [plugin-evals docs](https://code.claude.com/docs/en/plugin-evals.md#choose-and-weight-graders)

**Isolation**
- Each run is a fresh `claude -p` child with a temporary home, a temporary working directory and its own config. Only the plugin under test is loaded: no user settings, **no project CLAUDE.md, no `.claude/`, no `.mcp.json`**. — [plugin-evals docs, isolation](https://code.claude.com/docs/en/plugin-evals.md#how-runs-are-isolated)
- Tool access:
  - Read-only tools are allowed by default.
  - `Bash`, `Write`, `Edit`, `WebFetch` and `WebSearch` are removed unless granted with `--allow-tools`.
  - Granted Bash runs under Claude Code's OS sandbox: writes limited to the workspace, network limited to granted domains.
  - Hooks, real MCP servers and `--scaffold` scripts run outside the sandbox. The docs say to treat scores as advisory unless the run is in a container or CI runner.

  — same page
- The help text says the sandboxing "limits what a malicious plugin can reach but is not a guarantee". — local `--help`

**Scoring and output**
- A run's score is the fraction of passing graders, weighted if weights are set. A case's score is the mean of its runs. A case passes when its with-plugin score is ≥ `--threshold` (default 1.0). — [plugin-evals docs, scoring](https://code.claude.com/docs/en/plugin-evals.md#how-a-case-is-scored)
- Baseline arm:
  - By default each case also runs without the plugin; the report shows `WITH`, `W/OUT` and `Δ`.
  - `--ablation with-without` controls this.
  - Graders marked with-only (including `tool_used: Skill`) indicate that the plugin fired and do not count toward the score.

  — local `--help`; [docs](https://code.claude.com/docs/en/plugin-evals.md#the-no-plugin-baseline)
- No pass@k/pass^k and no stddev or confidence intervals are documented. Per-run scores are in the JSON. — [plugin-evals docs](https://code.claude.com/docs/en/plugin-evals.md) (absence noted by the delegated reader)
- `--json` output:
  - `schemaVersion: 1`, additive-only changes;
  - `partial` and `partialReason` (`cost_ceiling` | `interrupted` | `auth_failed`);
  - `aggregates.overallScore`, `casesPassed`, `meanDelta`;
  - per-run `error` and `aborted`;
  - `costUsd`, `claudeVersion`.

  Exit codes: 0 = pass, 1 = below threshold or error, 2 = partial (cost ceiling or credential), 130 = interrupted. — [docs, JSON result / CI](https://code.claude.com/docs/en/plugin-evals.md#json-result)
- Cost and parallelism:
  - `--max-cost-usd` is checked before each run launches; when it is hit, paid graders are skipped and free graders still score.
  - `-j/--concurrency` runs 1–8 children "on your own credential, so they share one rate limit".
  - The default judge is `--judge-model` haiku per the local help. The docs say "the model Claude Code uses for background tasks"; the two sources disagree slightly.

  — local `--help`; [docs](https://code.claude.com/docs/en/plugin-evals.md#command-options)
- Runs use the normal Claude Code credentials and "count against your plan's usage limits or your API bill", so subscription auth works. — [docs, requirements](https://code.claude.com/docs/en/plugin-evals.md#requirements)

**skill-creator** (the local copy of Anthropic's skill)
- Each test case runs as two arms: with the skill, and a baseline. The baseline is no skill for a new skill, or a `cp -r` snapshot of the old version when improving one.
- A grader subagent writes `grading.json` with `expectations[]` of {text, passed, evidence}.
- `aggregate_benchmark` reports pass_rate, time and tokens as mean ± stddev, plus the delta.
- Description optimisation splits cases **60% train / 40% held-out test**, stratified by should_trigger, runs each query 3 times, iterates up to 5 times, and picks `best_description` "by test score rather than train score to avoid overfitting".

— local `skill-creator/SKILL.md`, `scripts/run_loop.py`; upstream [anthropics/skills skill-creator](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md)

### Inferences
- `claude plugin eval` fails on the things that define `house`:
  - Our agents depend on CLAUDE.md, `.claude/agents` profiles, the `house` CLI on PATH, and a prepared contest repo with `.house/`.
  - The plugin eval runner drops the project's `.claude/` and CLAUDE.md, starts from an empty workspace, and only runs Codex-free Claude.
  - We would have to wrap the profiles in a plugin, recreate the fixture via `scaffold_script` with `--scaffold`, and grant `Bash`. Even then we could not test CLAUDE.md effects or Codex, and could not write Python graders (for example "`.house/state` says task X is in `review`").
  - Conclusion: keep our own runner.
- Its design is still the best available reference for our case schema. Worth copying:
  - grader names: `tool_used` with `input_match`/`min`/`max`, `tool_order`, `regex` with `not_contains`/`count`, `file_exists` (created during the run);
  - the 2-of-3 LLM vote;
  - `partialReason` that separates infra failure from task failure;
  - a with/without baseline arm for skills.
- A cheap periodic cross-check: wrap one house skill as a plugin and run `claude plugin eval` with `--runs 1`. It confirms our parser and results agree with Anthropic's runner after Claude Code updates change the transcript format.

### Gaps
- I could not confirm whether `claude plugin eval` honours `--settings`, or whether a plugin can bring CLAUDE.md-equivalent context via `append_system_prompt` (documented field) in a way that faithfully reproduces house's CLAUDE.md.
- The docs do not say whether subscription rate limits are surfaced separately from `auth_failed`.
- `/skill-doctor` is documented only in the embedded reference, not on a public page. Reportedly it shows skill usage and context cost and is not a behavioural test. Unverified.

---

## 2. Trajectory and tool-call evals: which match modes and assertion types to copy

### Takeaway
Industry practice has converged on three ideas:
- **superset-style "must include" checks plus explicit negative checks**, not strict sequence equality;
- **per-tool argument matchers**, not exact argument equality;
- **ordering only where order is the rule**.

Add **outcome/state checks** (files, git, exit codes) alongside the trajectory, and **efficiency counts** (calls, tokens).

### Cited Findings

**LangChain agentevals: `create_trajectory_match_evaluator`, four `trajectory_match_mode` values**
- `strict`: same messages, same order, same tool calls;
- `unordered`: same tool calls in any order;
- `superset`: the agent called at least the reference tools, extras allowed;
- `subset`: no tools beyond the reference.
- Tool calls count as equal only with the same name and the same arguments by default. This is configurable via `tool_args_match_mode` and `tool_args_match_overrides`.

— [LangChain docs: evals](https://docs.langchain.com/oss/python/langchain/test/evals); [agentevals on PyPI](https://pypi.org/project/agentevals/) (the PyPI page did not render for me; details come from the docs and search snippet).

**promptfoo agent assertions**
- Providers: `anthropic:claude-agent-sdk` (alias `anthropic:claude-code`), `openai:codex-sdk`, `opencode:sdk`.
- Skill assertions: `skill-used` and `not-skill-used` (the "near-miss prompt should route to a sibling skill" pattern).
- For Codex, skill use is inferred from a read of the matching `SKILL.md`.
- `trajectory:step-count` takes `type`, `pattern` and `min`.
- Also available: `cost` and `latency` thresholds, `--repeat 3` for nondeterminism, `max-score` weighting.
- Quote: "The better skill is the one that holds up across the tasks you care about, not the one that wins a single lucky run."

— [promptfoo: Test agent skills](https://www.promptfoo.dev/docs/guides/test-agent-skills/) (last updated 17 Nov 2026)

- Deterministic trajectory assertions: `trajectory:tool-used`, `trajectory:tool-args-match`, `trajectory:tool-sequence`. They need trace spans. — [promptfoo: evaluate coding agents](https://promptfoo.dev/docs/guides/evaluate-coding-agents/); [promptfoo OpenAI Agents provider](https://www.promptfoo.dev/docs/providers/openai-agents/)
- A third-party post reports that `tool-used` became flaky when the agent called tools in parallel, because array order varied. Anecdotal. — [Daniel Vaughan, Codex + promptfoo](https://codex.danielvaughan.com/2026/04/11/evaluating-codex-agents-promptfoo-trajectory-assertions/)

**OpenAI, "Testing Agent Skills Systematically with Evals"** (22 Jan 2026) — [OpenAI Developers](https://developers.openai.com/blog/eval-skills)

Success is defined in four categories:
- outcome;
- process (did it invoke the skill and follow its steps);
- style;
- efficiency ("avoid thrashing").

Case set:
- Start with 10–20 prompts in a CSV with columns `id, should_trigger, prompt`.
- Cover explicit, implicit and contextual invocations.
- Include negative controls.

Deterministic checks on the trace:
- Run `codex exec --json` and parse `item.completed` events of type `command_execution` (status, `exit_code`, command text).
- Check files on disk.
- Repo cleanliness: `git status --porcelain` empty or on an allow list.
- Permission regressions: the skill must not need more access than intended.
- Efficiency: count command executions and read `usage` from `turn.completed`.

Rubric grading:
- Comes after the deterministic checks, as a read-only `codex exec` with `--output-schema`.
- Schema: `overall_pass`, `score`, `checks[{id, pass, notes}]`.

**Inspect AI** (UK AISI)
- Agent evals run tools in a `sandbox="docker"` container.
- Repeats are set with `--epochs` (default 1) and combined with `--epochs-reducer`: mean, median, mode, max, `at_least_{n}`. I found no built-in pass^k reducer.

— [Inspect reference](https://inspect.aisi.org.uk/reference/inspect_eval.html); [Inspect tutorial](https://inspect.aisi.org.uk/tutorial.html); [AISI sandboxing toolkit](https://aisi.gov.uk/blog/the-inspect-sandboxing-toolkit-scalable-and-secure-ai-agent-evaluations)

**Claude plugin eval grader vocabulary**
- `tool_used` (with an `input_match` regex and `min`/`max` counts), `tool_order` (before/after), `regex` with `not_contains`/`count:N` on `trace` or `last_message`, `file_exists`. — [plugin-evals docs](https://code.claude.com/docs/en/plugin-evals.md#grader-types)

### Inferences
- Mapping onto A3:
  - `expect` = superset semantics (each listed call must appear, extras allowed);
  - `forbid` = not_contains;
  - `max_calls` = efficiency.

  That is the right core. Strict or unordered full-trajectory equality is brittle for coding agents (Anthropic) and should not be added.
- Missing pieces:
  - per-expectation counts (`min`/`max`: for example `house merge` exactly once);
  - optional ordering pairs (`before`/`after`: for example `house merge` before `house status … review`);
  - matching on any tool input, not only Bash commands and paths (for example `Skill` with name X, or `Agent` with subagent_type Y);
  - outcome checks on the fixture's final state (git refs, files, `.house` status) via small stdlib Python predicates. Plugin eval cannot do these, and they are our main advantage.
- Parallel tool calls make order-dependent assertions flaky (promptfoo anecdote). Ordering should compare the first occurrence of A and B in transcript order, not array positions within one message.

### Gaps
- I could not fetch the full agentevals `tool_args_match_mode` value list. The docs show it exists, but the mode names were not captured.
- Braintrust's agent-eval specifics were not researched (budget). Its general model of scorers over traces is assumed similar.

---

## 3. Non-determinism: pass@k vs pass^k, repeats, small-n statistics, flaky cases, smoke vs release gate

### Takeaway
pass^k is the right gate for "the agent must always do X". But with n=3 it is a noisy instrument.
- A behaviour that is truly 90% reliable passes 3/3 only 73% of the time.
- A 3/3 pass is consistent with a true rate as low as about 37% (95% one-sided).

So:
- keep pass^k for **regression** cases that should be ~100%;
- report per-case k/n with an exact or Wilson interval, not a CLT ± interval;
- compare prompt versions **paired, case by case**;
- treat 0<k<n as "flaky / under investigation", not "rerun until green".

### Cited Findings
- Anthropic definitions:
  - pass@k = at least one of k trials succeeds; pass^k = all k succeed, for when "consistency is essential";
  - at k=1 they coincide;
  - 75% per trial gives ≈42% pass^3.

  — [Anthropic, Demystifying evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- τ-bench introduced pass^k for agent reliability, with the unbiased estimator E_task[C(c,k)/C(n,k)] from n trials with c successes. **[older, June 2024; not re-fetched in this session]** — [Yao et al., τ-bench, arXiv:2406.12045](https://arxiv.org/abs/2406.12045)
- Evan Miller (Anthropic), "Adding Error Bars to Evals":
  - report standard errors;
  - cluster on the unit of randomisation; clustered SEs can be 3× naive ones;
  - compare two models with **paired per-question differences**, which removes question-difficulty variance;
  - Inspect's epochs support this.

  **[older, Nov 2024]** — [arXiv:2411.00640](https://arxiv.org/abs/2411.00640); [Anthropic blog](https://www.anthropic.com/research/statistical-approach-to-model-evals)
- Bowyer, Aitchison, Ivanova (ICML 2025 spotlight): CLT-based intervals are too narrow on small evals (fewer than a few hundred points). Use frequentist alternatives such as Wilson or Clopper–Pearson, or Bayesian (Beta) intervals. — [arXiv:2503.01747](https://arxiv.org/abs/2503.01747)
- How others set the repeat count:
  - Default runs per case: `claude plugin eval` uses 3 (`--runs` overrides, 1–50); the case score is the mean of runs; the threshold defaults to 1.0. — [plugin-evals docs](https://code.claude.com/docs/en/plugin-evals.md)
  - Explicit repeat advice: promptfoo suggests `--repeat 3` [promptfoo](https://www.promptfoo.dev/docs/guides/test-agent-skills/), and skill-creator runs each trigger query 3 times (local `skill-creator/SKILL.md`).
  - OpenAI's skill-eval post gives no repeat guidance. — [OpenAI](https://developers.openai.com/blog/eval-skills)
- Anthropic separates near-100% regression suites from low-starting capability suites, and graduates saturated capability tasks into regression. — [Anthropic](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)

### Inferences
Arithmetic (mine, exact binomial):

| True per-run pass rate p | P(3/3 pass) | P(1/1 pass) |
|---|---|---|
| 0.95 | 0.86 | 0.95 |
| 0.90 | 0.73 | 0.90 |
| 0.80 | 0.51 | 0.80 |
| 0.50 | 0.125 | 0.50 |

- Clopper–Pearson 95% lower bound after 3/3 is about 0.29 (two-sided); the one-sided bound is about 0.37.
- A `--repeat 1` smoke run therefore only catches gross breakage (p ≲ 0.5).
- `--repeat 3` before a core change catches a drop from about 1.0 to about 0.5 with roughly 87% probability. It reliably catches nothing subtler.
- With a fixed budget, **more distinct cases beat more repeats of the same case** for detecting regressions. Pairing old vs new prompt on the same cases reduces noise further (Miller).

Flaky handling (practitioner synthesis, no single source):
- A case with 0<k<n is labelled flaky in `house eval status`.
- It stays in the gate, but its failure is not hidden by retries.
- If a "regression" case is chronically flaky, either the behaviour is not reliable (then it is a capability case) or the case is ambiguous (Anthropic: 0% or erratic suggests a broken task).

Gate policy that follows:
- smoke: n=1 on every PR touching `core/`;
- release gate before changing core prompts: n=3, pass^3 per regression case, plus a paired comparison against the baseline commit's stored results;
- capability cases: report the pass rate with a Wilson interval and never gate on them.

### Gaps
- I found no published guidance specific to CLI coding agents on the optimal n under a hard subscription budget. The numbers above are my binomial arithmetic.
- No source covers how to budget runs against Claude subscription rate limits specifically. Plugin eval notes only that concurrent runs share one rate limit.

---

## 4. LLM-as-judge on agent transcripts

### Takeaway
Use the judge only for what code cannot check. Make it:
- binary, with a written critique and an "unknown" exit;
- one criterion per judge call;
- majority-of-3 votes;
- the strongest affordable model.

Validate it against a small set of human-labelled transcripts, reporting TPR/TNR rather than raw agreement. Until a criterion's judge is validated, its verdict should not gate.

### Cited Findings
- Hamel Husain, "Using LLM-as-a-Judge" (Oct 2024, updated 1 Sep 2026) — [hamel.dev](https://hamel.dev/blog/posts/llm-judge/):
  - binary pass/fail plus a detailed critique instead of 1–5 scales;
  - one principal domain expert;
  - start with about 30 examples and keep going until no new failure modes appear;
  - for judge validation, about 100 examples per failure mode, since fewer than about 60 gives intervals that are too wide;
  - split labelled data train/dev/test and run the test set once after the prompt is frozen;
  - report TPR/TNR, because "Agreement can be misleading when failures are rare";
  - fix obvious errors with code before building judges;
  - prefer code assertions where possible;
  - use the most capable judge the budget allows;
  - rerun the review after a model update.
- Anthropic:
  - calibrate judges against human experts;
  - give an "Unknown" option;
  - use a structured rubric and an isolated judge per dimension;
  - occasional human review once the setup is robust.

  — [Anthropic](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- Claude plugin eval:
  - the `llm` grader passes on ≥2 of 3 votes;
  - `focus: trace` lets the judge see actions, not just the final message;
  - the judge model is configurable;
  - default is haiku per the local help, or the background-task model per the docs.

  — [plugin-evals docs](https://code.claude.com/docs/en/plugin-evals.md#grader-types); local `--help`
- OpenAI: rubric grading runs after deterministic checks, with output constrained by a JSON schema (`overall_pass`, `checks[{id, pass, notes}]`). — [OpenAI](https://developers.openai.com/blog/eval-skills)
- Secondary sources:
  - an always-"Pass" judge scores 95% agreement when failures are 5%;
  - suggested target 75–90% judge–human agreement (third-party, not Husain).

  — [Arize, human–LLM judge alignment](https://arize.com/blog/measuring-human-llm-judge-alignment/); [Label Studio](https://labelstud.io/learningcenter/how-to-use-llm-as-judge-for-agent-evaluation) (secondary)

### Inferences
- A3's `criteria` field is right to be optional. The spec should add:
  - each sentence becomes a separate binary judge call;
  - the judge returns `{pass|fail|unknown, critique}` as JSON (`claude -p --json-schema` or `codex exec --output-schema`);
  - majority of 3;
  - `unknown` counts as not-passed but is reported separately.
- Which model:
  - The plugin eval default (haiku) is cheap. Husain argues for the strongest affordable judge.
  - On a subscription, run the judge on the production model only when the deterministic checks already passed. That saves runs.
- The judge's input should be the A0-normalised transcript (tool calls + results + final message), not the raw jsonl. This reduces tokens and stops the judge from being confused by format noise.
- For `house`, nearly all target behaviours in the acceptance list are deterministic: merge via `house merge`, frozen criteria, coder ends in `review`, Q-### instead of waiting, protected files untouched. Judges should be rare.

### Gaps
- No source gives judge-variance numbers specifically for Claude judges on coding-agent transcripts.
- I found no published agreement rates for plugin eval's 2-of-3 haiku judge.

---

## 5. Isolation, side effects, cost control and anti-gaming

### Takeaway
Per trial, use a fresh copy of the fixture with a **fresh git repo** (no history from other trials) and a **local bare remote**. Run Claude with:
- a per-run `--settings` that enables the OS sandbox (bubblewrap on Linux) with an empty network allowlist and `failIfUnavailable`;
- `permissions.deny` for `git push`/notification commands;
- stubbed notification hooks.

Codex needs `--sandbox workspace-write`.

Against gaming:
- held-out cases or variants that the improving agent never sees;
- a baseline arm;
- selection by held-out score, as skill-creator does.

### Cited Findings
- Claude Code sandbox:
  - Linux uses **bubblewrap** plus a socat proxy, with a separate network namespace for sandboxed commands.
  - Writes are allowed to the cwd, a per-user temp dir and `--add-dir` paths.
  - **Reads are allowed across most of the machine including `~/.ssh` and `~/.aws/credentials` unless restricted** (`sandbox.filesystem.denyRead`/`allowRead`).
  - `sandbox.network.allowedDomains` starts empty; `strictAllowlist` refuses unlisted hosts.
  - `sandbox.failIfUnavailable: true` exits instead of running unsandboxed.
  - `sandbox.allowUnsandboxedCommands: false` disables the `dangerouslyDisableSandbox` retry.
  - `sandbox.credentials` can deny or mask env vars and files.
  - Bash deny rules still apply in auto-allow mode.

  — [Claude Code docs: sandboxing](https://code.claude.com/docs/en/sandboxing)
- Plugin eval is a reference design for isolation:
  - temporary HOME and config per run;
  - Bash/Write/Edit/Web tools removed unless granted;
  - hooks and real MCP servers run outside the sandbox, so scores are "advisory" unless the run is in a container or CI runner.

  — [plugin-evals docs](https://code.claude.com/docs/en/plugin-evals.md#what-a-run-can-access)
- Anthropic:
  - each trial in a clean environment;
  - Claude exploited git history from earlier trials;
  - graders must resist bypasses.

  — [Anthropic](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- Codex:
  - default `codex exec` is a restricted sandbox;
  - use the least permissions that work (`--sandbox workspace-write` / `--full-auto`);
  - check `git status --porcelain` against an allow list;
  - test for "permission regressions".

  — [OpenAI](https://developers.openai.com/blog/eval-skills)
- promptfoo uses a separate fixture `working_dir` per skill version, Codex `sandbox_mode: read-only`, and tool allow lists for Claude. — [promptfoo](https://www.promptfoo.dev/docs/guides/test-agent-skills/)
- Inspect runs agent tools in Docker sandboxes by default for agent tasks. — [Inspect tutorial](https://inspect.aisi.org.uk/tutorial.html); [AISI sandboxing toolkit](https://aisi.gov.uk/blog/the-inspect-sandboxing-toolkit-scalable-and-secure-ai-agent-evaluations)
- Anti-overfitting:
  - skill-creator: 60/40 train/held-out split, choose by test score [local skill-creator `scripts/run_loop.py`, `SKILL.md`];
  - Husain: dev/test sets kept out of the prompt, test run once [hamel.dev](https://hamel.dev/blog/posts/llm-judge/);
  - Anthropic: balanced should/shouldn't cases, to avoid one-sided optimisation [Anthropic](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
- Cost controls in other tools:
  - plugin eval: per-case `max_turns`/`timeout_seconds`, `--max-cost-usd` checked before each run, and `partialReason` `cost_ceiling`/`auth_failed` [plugin-evals docs](https://code.claude.com/docs/en/plugin-evals.md#json-result);
  - promptfoo: `cost`/`latency` assertions [promptfoo](https://www.promptfoo.dev/docs/guides/test-agent-skills/).

### Inferences
- Concrete house risks:
  - **`git push`** from the fixture: the fixture's `origin` must be a local bare repo in the temp dir, so a push cannot leave the machine even if a deny rule is bypassed.
  - **Signal / notifications**: house's notify path must honour an env flag (for example `HOUSE_EVAL=1`) that turns notification into a no-op logged call.
  - **Reads of `~/.ssh`/secrets**: the sandbox allows these by default, so add `denyRead` for home with `allowRead` for the temp dir and the house repo.
  - **Memory / auto-memory pollution**: runs in a temp cwd write their sessions and auto-memory under `~/.claude/projects/-tmp-…`. The runner should delete them after parsing, or set a per-run config dir if subscription auth still works with it (unverified).
  - **`.house` state of real contests**: the fixture must be a copy, and `house` must resolve the contest from cwd, not from a global registry.
- The plugin eval approach of a temporary HOME is the strongest isolation, but it may break subscription login for a hand-built runner. Unverified; plugin eval handles credentials internally.
- Gaming defence for `/house-improve`:
  - keep a small set of held-out regression cases that the improve loop is told not to read and that is only run at the verification step;
  - require the paired baseline comparison;
  - require at least one fresh variant per changed behaviour.

  The spec already has "fresh variants". Making the held-out set explicit and recording which cases the change was developed against closes the loop.

### Gaps
- Not verified whether `claude -p` with a custom `CLAUDE_CONFIG_DIR`/temp HOME keeps subscription OAuth credentials. Needs a local experiment.
- I found no practitioner source on containerising Claude Code under subscription auth specifically.

---

## 6. Is there a standard, portable case format?

### Takeaway
No cross-vendor standard exists. The de-facto formats are:
- Anthropic's `claude plugin eval` folders (`prompt.md` frontmatter + `case.yaml` + `graders/*.md`), now also the write format of skill-creator;
- promptfoo YAML;
- Inspect Python datasets;
- OpenAI's simple `id,should_trigger,prompt` CSV.

All except the CSV need a YAML parser. House is stdlib-only, so its own JSON is justified. It should **reuse plugin-eval grader names and semantics**, so that cases are mentally portable and could be exported later.

### Cited Findings
- Plugin eval format:
  - folders with `prompt.md` (YAML frontmatter), `case.yaml` (`schema_version: "1.1"`) and `graders/*.md`;
  - an unknown frontmatter key makes the case fail to load;
  - "neither reads the other's case files" regarding the older skill-creator evals.json.

  — [plugin-evals docs](https://code.claude.com/docs/en/plugin-evals.md); local skill-creator `references/schemas.md`
- Skill-creator older format: `evals/evals.json` with `skill_name` and `evals[{id, prompt, expected_output, files, expectations[]}]`. It is now "read it, don't write it"; new tests are folders in the plugin-eval layout. — local skill-creator `references/schemas.md`; [anthropics/skills](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md)
- OpenAI: CSV `id, should_trigger, prompt`. — [OpenAI](https://developers.openai.com/blog/eval-skills)
- promptfoo: YAML `providers`, `tests` with `assert` lists (`skill-used`, `not-skill-used`, `trajectory:*`, `cost`, `latency`, `javascript`). — [promptfoo](https://www.promptfoo.dev/docs/guides/test-agent-skills/)
- Inspect: Python `Task(dataset, solver, scorer, sandbox)` with epochs. — [Inspect reference](https://inspect.aisi.org.uk/reference/inspect_ai.html)

### Inferences
- Keep JSON. Name the fields after plugin eval to ease a later export (`house eval export --plugin-eval`):
  - `tool_used` with `tool`/`input_match`/`min`/`max`;
  - `tool_order` with `before`/`after`;
  - `regex` with `match: not_contains`;
  - `file_exists`;
  - `llm` with `criteria`;
  - `tags`, `runs`, `max_turns`, `timeout_seconds`, `expected_outcome`.
- House-only extensions that plugin eval lacks (outcome predicates over fixture state, Codex target, CLAUDE.md-dependent agents) stay namespaced, for example `house_state`.

### Gaps
- I did not find an emerging standard (OpenTelemetry GenAI semantic conventions for agent traces exist, but I did not research them here). Worth a separate look if the transcript format matters more than the case format.

---

## Rekomendacje dla speca

Zmiany do A3 (sekcja 9 speca `2026-11-16-agno-adoptions-design.md`). Priorytety: **must** = bez tego eval jest mylący albo niebezpieczny, **should** = wyraźnie lepsza praktyka, **could** = miłe, tanie.

### 9a. `house eval`

1. **must: zostajemy przy własnym runnerze. `claude plugin eval` traktujemy jako wzorzec, nie zależność.** Do sekcji 14 („Sprawdzone i odrzucone”) dopisać uzasadnienie:
   - nie ładuje CLAUDE.md ani `.claude/` projektu;
   - testuje tylko pluginy;
   - startuje z pustego katalogu, fixture odtwarza dopiero skrypt bash;
   - nie ma graderów w kodzie;
   - nie obsługuje Codexa.

   Źródło: [plugin-evals docs](https://code.claude.com/docs/en/plugin-evals.md), lokalne `claude plugin eval --help` (2.1.326).
2. **must: sprawdzenia stanu końcowego obok sprawdzeń wywołań.** Pole `outcome` z predykatami po przebiegu w kopii fixture, w stdlib:
   - `git` (gałąź, czy merge commit istnieje, `git status --porcelain` pusty albo z listy dozwolonych);
   - `file_exists` (pliki utworzone w przebiegu) i `file_unchanged` (np. `.house/checks/`);
   - `house_state` (status zadania w `.house`).

   Ocena wyniku jest mocniejsza niż ocena ścieżki, a sztywne sekwencje są kruche. Źródła: [Anthropic](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents), [OpenAI](https://developers.openai.com/blog/eval-skills).
3. **must: semantyka `expect`/`forbid` nazwana wprost i bogatsza.**
   - `expect` działa jak superset: każde oczekiwane wywołanie musi wystąpić, dodatkowe są dozwolone.
   - Każde oczekiwanie dostaje `min`/`max` (np. `house merge` dokładnie raz).
   - Opcjonalne `order: [{before, after}]` porównuje pierwsze wystąpienia w kolejności zapisu, nie pozycje w jednej wiadomości (wywołania równoległe).
   - Dopasowanie po dowolnym narzędziu i jego wejściu (`tool` + `input_match` jako regex na JSON wejścia), nie tylko Bash i ścieżki. Dzięki temu da się sprawdzić `Skill`/`Agent` o konkretnej nazwie.
   - Nie dodajemy trybów strict ani unordered dla całej trajektorii.

   Nazwy pól jak w plugin eval (`tool_used`, `tool_order`, `regex` + `not_contains`). Źródła: [agentevals/LangChain](https://docs.langchain.com/oss/python/langchain/test/evals), [plugin-evals grader types](https://code.claude.com/docs/en/plugin-evals.md#grader-types), [promptfoo](https://www.promptfoo.dev/docs/guides/test-agent-skills/).
4. **must: izolacja przebiegu.**
   - Każdy przebieg dostaje świeżą kopię fixture **z nowym repo git**, bez historii innych prób.
   - `origin` wskazuje lokalne bare repo w katalogu tymczasowym.
   - `claude -p` dostaje `--settings` z:
     - `sandbox.enabled: true`, `failIfUnavailable: true`, `allowUnsandboxedCommands: false`;
     - pustym `network.allowedDomains`;
     - `filesystem.denyRead` dla `~/` z wyjątkami dla katalogu przebiegu i repo `house`;
     - `permissions.deny` dla `Bash(git push *)` i komend powiadomień.
   - `HOUSE_EVAL=1` wyłącza Signal i inne powiadomienia: zamiast wysyłki zapisuje wywołanie, które eval może sprawdzić.
   - Codex: `--sandbox workspace-write`.
   - Po przebiegu runner usuwa zapisy sesji i auto-memory z `~/.claude/projects/-tmp-…`, po ich odczytaniu przez A0.

   Źródła: [sandboxing docs](https://code.claude.com/docs/en/sandboxing), [Anthropic: historia git z poprzednich prób](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents), [OpenAI](https://developers.openai.com/blog/eval-skills).
5. **must: rozdzielić typy wyników.** Wiersz w `results.jsonl` ma `outcome ∈ {pass, fail, paused, infra_error}`:
   - `infra_error` obejmuje limit subskrypcji, timeout, błąd autoryzacji i niedostępny sandbox, i nie liczy się do k/n;
   - do wiersza dopisać `claude_version`/`codex_version`, liczbę tur, tokeny (jeśli są w zapisie), czas i `case_hash` (hash promptu, fixture i oczekiwań).

   Status agreguje tylko wiersze z tym samym `case_hash`, więc wyniki starej wersji przypadku nie mieszają się z nową. Źródła: [plugin-evals `partialReason`](https://code.claude.com/docs/en/plugin-evals.md#json-result), [OpenAI: usage z `turn.completed`](https://developers.openai.com/blog/eval-skills).
6. **must: przypadki regresyjne i przypadki zdolności to dwie klasy.** Pole `kind: regression|capability`:
   - pass^k jako bramka tylko dla `regression`, czyli zachowań, które mają działać ~100%;
   - `capability` raportuje odsetek zaliczeń i nigdy nie blokuje.

   Spec powinien wprost napisać, że przy n=3 zachowanie skuteczne w 90% przechodzi pass^3 tylko w 73% przebiegów, a 3/3 nie wyklucza skuteczności ~37%. Bramka n=3 łapie więc tylko duże regresje. Źródła: [Anthropic: regression vs capability](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents), [τ-bench pass^k](https://arxiv.org/abs/2406.12045); arytmetyka dwumianowa własna.
7. **should: statystyka uczciwa dla małego n.**
   - `house eval status` pokazuje per przypadek k/n z przedziałem Wilsona (stdlib, kilka linii), a nie ±SE z CLT.
   - Porównanie dwóch commitów idzie **parami po przypadkach** (te same przypadki, różnica per przypadek), nie przez porównanie dwóch średnich.

   Źródła: [Bowyer i in. 2025](https://arxiv.org/abs/2503.01747), [Miller 2024](https://arxiv.org/abs/2411.00640).
8. **should: obsługa niestabilnych przypadków.**
   - Przypadek z 0<k<n dostaje w statusie znacznik `flaky`.
   - Zakaz „powtarzaj, aż przejdzie”.
   - Przypadek z 0/n przez kilka przebiegów oznacza najpierw podejrzenie wadliwego przypadku lub fixture (przeczytać zapis), a dopiero potem wadę agenta.
   - Każdy nowy przypadek ma wzorcowy przebieg (ręczny albo z dobrego commita), który pokazuje, że da się go zaliczyć.

   Źródło: [Anthropic](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
9. **should: przypadki negatywne obowiązkowo.** Dla każdej zasady co najmniej jeden przypadek, w którym zachowanie **nie** powinno wystąpić. Przykłady:
   - agent nie pisze Q-###, gdy ma dane;
   - skill nie odpala się na sąsiednie polecenie.

   Akceptację w 9a rozszerzyć o ≥1 przypadek negatywny. Źródła: [OpenAI `should_trigger=false`](https://developers.openai.com/blog/eval-skills), [promptfoo `not-skill-used`](https://www.promptfoo.dev/docs/guides/test-agent-skills/), [Anthropic: balanced sets](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
10. **should: `criteria` (sędzia LLM) według sprawdzonego wzorca.**
    - Każde zdanie to osobne wywołanie.
    - Odpowiedź binarna w JSON `{verdict: pass|fail|unknown, critique}` (`--json-schema` / `--output-schema`).
    - Większość z 3 głosów.
    - Wejściem jest zapis znormalizowany przez A0 (wywołania + wyniki + ostatnia wiadomość).
    - Sędzia uruchamia się tylko wtedy, gdy przeszły sprawdzenia deterministyczne.
    - Werdykt sędziego **nie blokuje**, dopóki kryterium nie zostało sprawdzone na ~20–30 ręcznie oznaczonych zapisach z raportem TPR/TNR. Do tego czasu `criteria` daje tylko ostrzeżenie.

    Źródła: [Husain](https://hamel.dev/blog/posts/llm-judge/), [Anthropic: „Unknown”, osobny sędzia na wymiar](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents), [plugin eval: 2 z 3](https://code.claude.com/docs/en/plugin-evals.md#grader-types), [OpenAI: najpierw deterministyczne](https://developers.openai.com/blog/eval-skills).
11. **should: limity per przypadek.** `max_turns` i `timeout_seconds` w przypadku (domyślnie np. 30 tur, 600 s), obok `max_calls` i `--max-runs`. Przekroczenie to `fail`, jeśli przypadek tego dotyczy (pętlenie), a nie `infra_error`. Źródło: [plugin-evals prompt.md fields](https://code.claude.com/docs/en/plugin-evals.md#prompt-md-fields).
12. **should: cel na zestaw.** Akceptacja zostaje przy ≥5 smoke. Cel średnioterminowy: 20–30 przypadków z prawdziwych porażek (retro, `DECISIONS.md`). Lepiej więcej różnych przypadków przy n=1–3 niż mało przypadków z dużym n. Źródła: [Anthropic: 20–50 zadań z realnych porażek](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents), [OpenAI: 10–20 promptów](https://developers.openai.com/blog/eval-skills).
13. **could: zgodność z formatem plugin eval.**
    - Polecenie `house eval export --plugin-eval <skill>` zapisuje przypadki skilla jako `evals/<case>/{prompt.md, case.yaml, graders/}`.
    - Raz na jakiś czas (np. po aktualizacji Claude Code) jeden skill przechodzi przez `claude plugin eval --runs 1`. To sprawdza, czy nasz parser A0 i oficjalny runner zgadzają się co do wywołań.

    Źródło: [plugin-evals docs](https://code.claude.com/docs/en/plugin-evals.md).
14. **could: ramię bazowe dla skilli.** `house eval run --baseline <commit>` uruchamia te same przypadki na profilu lub skillu z innego commita (snapshot), a status pokazuje różnicę parami. Źródła: [plugin eval `--ablation with-without`](https://code.claude.com/docs/en/plugin-evals.md#the-no-plugin-baseline), [skill-creator `old_skill` baseline](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md).

### 9b. `/house-improve`

15. **must: zbiór wstrzymany (held-out).**
    - Część przypadków (np. `evals/holdout/`, ~1/3) skill ma zakaz czytać w krokach 2–4.
    - Uruchamia się je dopiero w kroku 5.
    - Kandydat jest wybierany po wyniku na held-out, nie na scenariuszach, na których go poprawiano.
    - Raport zapisuje, na których przypadkach rozwijano zmianę.

    Źródła: [skill-creator 60/40, wybór po wyniku testowym](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md), [Husain: test set raz, po zamrożeniu](https://hamel.dev/blog/posts/llm-judge/). Wprost realizuje anty-cel „evale nie do zazielenienia promptem”.
16. **should: weryfikacja parami względem snapshotu.**
    - Krok 4 robi kopię starej wersji (jak `skill-snapshot/` w skill-creator).
    - Krok 5 uruchamia stare i nowe na tych samych przypadkach (ten sam model, te same n) i raportuje różnice per przypadek.

    Źródła: [skill-creator](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md), [Miller](https://arxiv.org/abs/2411.00640).
17. **should: krok „czytaj zapisy” przed diagnozą.**
    - Krok 3 zaczyna się od przeczytania co najmniej jednego pełnego zapisu porażki i jednego zaliczenia, przez relację A1 i surowy zapis.
    - Klasa „rozrzut oceny” obejmuje też wadliwy przypadek lub grader. Przypadek 0/n najpierw podejrzewamy o błąd.

    Źródło: [Anthropic: CORE-Bench 42%→95% po naprawie graderów](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
18. **should: promocja przypadków.**
    - Zachowany scenariusz trafia jako `kind: capability`.
    - Do `regression` przechodzi dopiero po ≥3 kolejnych przebiegach 3/3.
    - Każdy nowy przypadek pozytywny ma parę negatywną.

    Źródło: [Anthropic: graduation, balanced sets](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
19. **could: ten sam sędzia, co w 9a.** Ocena symulacji w kroku 2 idzie przez ten sam binarny format z krytyką (pkt 10). Krytyki z ręcznego przeglądu Wojtka stają się few-shotami sędziego. Źródło: [Husain](https://hamel.dev/blog/posts/llm-judge/).

### Do sprawdzenia przed planem A3 (eksperyment lokalny, nie research)
- Czy `claude -p` z tymczasowym `HOME`/`CLAUDE_CONFIG_DIR` zachowuje logowanie subskrypcyjne. Jeśli tak, to najsilniejsza izolacja, jak w plugin eval.
- Czy sandbox (bubblewrap + socat) jest zainstalowany na maszynie nocnej. Bez tego `failIfUnavailable` zablokuje evale.
- Czy w zapisie Codexa (`codex exec --json`) pola `command_execution`/`exit_code`/`turn.completed.usage` są stabilne w wersji, której używamy.

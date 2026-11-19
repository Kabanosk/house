# Mini research: zapisy sesji, koszty, evale (2026-11-16)

Krótki przegląd przed implementacją A0/K0/A3 (specy `docs/specs/2026-11-16-agno-adoptions-design.md`
i `2026-11-16-short-context-design.md`). Pełny research jest odłożony na później
(`docs/research/research_notes/Specy Agno a najlepsze praktyki/`, na razie pusty).

## Zapis Claude Code (`~/.claude/projects/*/*.jsonl`)

- Jedno wywołanie API to kilka rekordów `assistant` z tym samym `message.id` i `requestId`, każdy z pełnym
  obiektem `usage`. Sumowanie wszystkich rekordów liczy koszt wielokrotnie.
- ccusage deduplikuje po `message.id` + `requestId` i zostawia **pierwszy** rekord. Zgłoszono, że pierwszy bywa
  częściowy i zaniża `output_tokens`; inne narzędzia biorą **ostatni** rekord albo ten z `stop_reason`.
  ([ccusage data loader](https://www.mintlify.com/ryoppippi/ccusage/api/library/data-loader),
  [DEV: parsing Claude Code and Codex logs without double counting](https://dev.to/agentis/parsing-claude-code-and-codex-logs-without-double-counting-a-week-9m9),
  [claude-code#87303](https://claudeissues.com/issue/87303-bug-session-transcript-repeats-the-full-usage-object-for-every-content-block-of))
- Pomiar lokalny (15 ostatnich sesji, 1408 wywołań): 75% wywołań ma >1 rekord. W bieżącej wersji pierwszy
  i ostatni rekord mają te same `output_tokens`, ale ostatni jest i tak bezpieczniejszy.
- `input_tokens` jest mały (≤3 w 78% wywołań). To niebuforowana część wejścia, a nie błąd: reszta jest
  w `cache_read_input_tokens` i `cache_creation_input_tokens`. Zgłoszenie
  [claude-code#28197](https://github.com/anthropics/claude-code/issues/28197) twierdzi, że to wartość zastępcza
  ze strumienia, więc K0 trzeba sprawdzić porównaniem z innym źródłem (np. `/cost` albo OTel).
- `usage.cache_creation` ma podział `ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens` (sprawdzone
  lokalnie). Inne pola: `service_tier`, `server_tool_use`, `iterations`, `speed`.

## Telemetria Claude Code (OpenTelemetry)

- `CLAUDE_CODE_ENABLE_TELEMETRY=1` + `OTEL_LOGS_EXPORTER=otlp` daje zdarzenia `api_request` (model,
  `cost_usd`, `duration_ms`, tokeny wejścia, wyjścia, odczytu i zapisu cache) i `tool_result` (narzędzie,
  sukces, czas). Treść narzędzi jest domyślnie wyłączona. `prompt.id` łączy zdarzenia jednego polecenia.
  Koszt to przybliżenie, nie rachunek.
  ([Claude Code monitoring w Cowork](https://claude.com/docs/cowork/monitoring),
  [Agent SDK observability](https://code.claude.com/docs/en/agent-sdk/observability.md),
  [Better Stack: Observe Claude Code with OpenTelemetry](https://betterstack.com/docs/logs/claude-code/))
- Wymaga odbiornika OTLP. Dla nas to opcja na później (K0/K4 w `night`): mały odbiornik OTLP/HTTP-JSON
  w stdlib dla sesji workerów. Zapis jsonl zostaje źródłem podstawowym, bo działa wstecz i bez usług.

## Zapis Codexa (`~/.codex/sessions/RRRR/MM/DD/rollout-*.jsonl`)

- Brak oficjalnej dokumentacji. Źródłem prawdy jest kod `openai/codex` (`codex-rs/protocol`). Rekord:
  `{timestamp, type, payload}`; typy `session_meta`, `turn_context`, `response_item`, `event_msg`,
  `compacted` (i inne); w `response_item`/`event_msg` rodzaj w `payload.type`.
- Tokeny: `event_msg` z `payload.type = token_count`, **narastająco** dla sesji, więc koszt wywołania to
  różnica kolejnych wartości. Wywołania narzędzi i ich wyniki łączy `call_id`.
- Schemat zmienia się między wersjami CLI. Pliki potrafią mieć setki MB (kompakcje, surowe wyniki narzędzi),
  więc czytanie musi iść strumieniowo, linia po linii.
  ([Codex session file schema](https://lin-guanguo.github.io/llm-memory-research/agent-cli/codex-session-file-schema/),
  [Codex CLI session analytics](https://codex.danielvaughan.com/2026/03/30/codex-cli-session-analytics-jsonl-rollout),
  [openai/codex#24948](https://upd.dev/openai/codex/issues/24948))

## Evale: pass^k

- τ-bench: pass^k to szansa, że **wszystkie** k niezależnych prób się udadzą (średnia po zadaniach), w odróżnieniu
  od pass@k (co najmniej jedna). Wyniki spadają mocno z k (gpt-4o: pass^8 < 25% w retail). Raportuje się pass^1
  i pass^k; dla n prób z c sukcesami jest nieobciążony estymator.
  ([τ-bench, arXiv 2406.12045](https://arxiv.org/abs/2406.12045),
  [Sierra: benchmarking AI agents](https://sierra.ai/blog/benchmarking-ai-agents),
  [ReliabilityBench, arXiv 2601.06112](https://arxiv.org/pdf/2601.06112))
- Nasza reguła z A3 („przypadek przechodzi przy k = n”) to właśnie pass^k. Dobrze raportować obok pass^1.

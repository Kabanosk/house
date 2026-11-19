# Zapisy sesji Claude Code i Codex CLI: czytanie i rachunek tokenów (stan na 2026-11-17)

Zakres: czytnik A0 (`houselib/transcript.py`) i `house cost` (K0). Pogłębienie notatki
`docs/research/2026-11-16-mini-research-transcripts.md`. Źródła to dokumentacja, kod `openai/codex`, repozytoria
narzędzi i zgłoszenia. Do tego pomiar lokalny 2026-11-17 na maszynie Wojtka: Claude Code 2.1.309–2.1.326 (33 sesje
z rekordem `cost-state`) i Codex CLI 0.166.0 (12 rolloutów). Pomiar liczył wyłącznie klucze, typy i liczby, bez
czytania treści. Skrypty leżą w scratchpadzie sesji i nie trafiły do repo.

## 1. Narzędzia open source, typy rekordów i przypadki brzegowe (Claude Code)

### Takeaway
Najdojrzalsze parsery to ccusage (przepisany na Rust, organizacja `ccusage/ccusage`) i agentsview. Kluczowe wnioski
z ich kodu i dokumentacji:
- trzeba czytać rekurencyjnie, łącznie z `<session>/subagents/`;
- deduplikacja `message.id + requestId` musi działać także między plikami (repliki w sidechainach `/btw`);
- część wywołań w ogóle nie trafia do zapisu: boczne wywołania Haiku (tytuł, WebSearch) i kompakcja.

Bieżące Claude Code dopisuje do zapisu rekord `cost-state` z sumami sesji per model. To gotowe źródło walidacji K0.

### Cited Findings
- Format zapisu jest oficjalnie wewnętrzny: „The entry format is internal to Claude Code and changes between versions,
  so scripts that parse these files directly can break on any release”. Ścieżka to
  `~/.claude/projects/<project>/<session-id>.jsonl`. `<project>` to cwd ze znakami niealfanumerycznymi zamienionymi
  na `-`, a nazwy powyżej 200 znaków są przycinane i dostają hash. Konfiguracja:
  - lokalizacja: `CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_PROJECT_DIR_NAME` (od v2.1.234);
  - retencja: `cleanupPeriodDays`, domyślnie 30 dni;
  - wyłączenie zapisu: `--no-session-persistence`, `CLAUDE_CODE_SKIP_PROMPT_HISTORY`.
  — [Manage sessions](https://code.claude.com/docs/en/sessions.md)
- Zapisy podagentów leżą w `~/.claude/projects/<project>/<session>/subagents/agent-<agentId>.jsonl` obok
  `agent-<agentId>.meta.json`. Często jest też katalog `tool-results/` z pełnymi wynikami narzędzi.
  ccusage w starszych wersjach pomijał podagentów. — [Inside the subagent trace file](https://frederick-douglas-pearce.github.io/blog/2026/inside-the-subagent-trace-file/);
  [claude-code#58154: cleanupPeriodDays nie usuwa zapisów podagentów](https://claudeissues.com/issue/58154-bug-cleanupperioddays-does-not-delete-subagent-transcripts-2-388-files-dating-ba)
- Pomiar lokalny (2.1.3xx) potwierdza ten układ: `<session>/subagents/agent-*.jsonl`, `*.meta.json` i
  `<session>/tool-results/*.txt`.
  - Klucze `meta.json`: `agentType, isBuiltIn, description, toolUseId, spawnDepth, requestShape,
    requestNonInteractive, model, pickedEffortModel, finished`. `toolUseId` wiąże podagenta z wywołaniem narzędzia
    Agent/Task w sesji rodzica.
  - Rekordy podagenta mają `agentId` i `isSidechain`. — pomiar lokalny 2026-11-17
- ccusage czyta `projects/` rekurencyjnie (`{sessionId}.jsonl` i `{sessionId}/{file}.jsonl`) i przyjmuje kilka
  ścieżek w `CLAUDE_CONFIG_DIR` po przecinku. Uszkodzone linie pomija. Jego reguły deduplikacji:
  - przy `requestId` deduplikuje parę message+request **także między sesjami**, bo „Claude Code can copy one response
    into multiple session transcripts”;
  - bez `requestId` używa `message.id` + sesja + timestamp;
  - sidechainy `/btw` (`aside_question` w `subagents/`) „can replay parent conversation messages with the same message
    ID but a different request ID, including the parent cache-read usage”. ccusage odrzuca kopię z
    `isSidechain: true` (poprawka [ccusage#913](https://net.nhd.workers.dev/ryoppippi/ccusage/issues/913));
  - zużycie doradcy (advisor) leży w `message.usage.iterations` z `type: advisor_message` i jest liczone osobno pod
    modelem doradcy.

  — [ccusage: Claude adapter README](https://github.com/ccusage/ccusage/blob/main/rust/adapters/claude/src/README.md)
- agentsview robi dwie rzeczy, ważne dla K0:
  - wybiera „complete Claude snapshots before generic deduplication” (kontrakt v5), czyli kompletny rekord zamiast
    pierwszego;
  - osobno pokazuje sumę sesji zapisywaną przez Claude Code („its own cost estimate and token counts per model,
    including subagents and side calls such as title generation that never reach the transcript”) i nie dodaje jej do
    sum, bo pokrywa się z wierszami wywołań.

  Dla WebSearch Claude Code robi osobne wywołanie Haiku („tens of thousands of input tokens … per search”), którego
  tokeny nie trafiają do zapisu. — [agentsview: Token Usage & Costs](https://github.com/kenn-io/agentsview/blob/main/docs/token-usage.md)
- Pomiar lokalny, typy rekordów w 40 najnowszych sesjach (2.1.3xx):
  - poza `user`/`assistant`/`system`: `attachment`, `api-request`, `api-request-blob`, `api-request-shape`,
    `cost-state`, `file-history-snapshot`, `file-history-delta`, `queue-operation`, `ai-title`, `last-prompt`, `mode`,
    `permission-mode`, `session-owner`, `bridge-session`, `pr-link`, `tools-used`, `agent-setting`, `agent-name`,
    `continued-in`, `atis-latch`;
  - typ `summary` ze starszych wersji nie występuje;
  - podtypy `system`: `compact_boundary`, `turn_duration`, `away_summary`, `local_command`, `informational`,
    `agents_killed`, `scheduled_task_fire`, `bridge_status`;
  - bloki assistanta: `thinking`, `text`, `tool_use`;
  - `toolUseResult`: dict 2522, str 111, list 13, czyli trzy warianty;
  - flagi `user`: `isMeta` (109), `isCompactSummary` (6). — pomiar lokalny 2026-11-17
- Kompakcja w bieżącym formacie:
  - rekord `system` z `subtype: compact_boundary` i `compactMetadata` o kluczach `trigger, preTokens, postTokens,
    cumulativeDroppedTokens, durationMs, preCompactDiscoveredTools, preservedSegment{headUuid,anchorUuid,tailUuid},
    preservedMessages{…}`;
  - zaraz po nim rekord `user` z `isCompactSummary: true`;
  - `logicalParentUuid` na rekordzie systemowym łączy z gałęzią sprzed kompakcji. — pomiar lokalny 2026-11-17
- Rekordy `assistant` z `message.model == "<synthetic>"` mają zerowe `usage` (komunikaty błędów i przerwań).
  Dodatkowe flagi: `isApiErrorMessage`, `isAbortedMidStream`, `apiErrorStatus`. — pomiar lokalny 2026-11-17
- Kształt `message.usage` (2.1.3xx):
  - tokeny: `input_tokens, cache_creation_input_tokens, cache_read_input_tokens, output_tokens`,
    `cache_creation{ephemeral_5m_input_tokens, ephemeral_1h_input_tokens}`,
    `output_tokens_details{thinking_tokens}`;
  - pozostałe pola: `server_tool_use{web_search_requests, web_fetch_requests}`, `service_tier`, `inference_geo`,
    `iterations[]`, `speed`, `fallback_credit`. — pomiar lokalny 2026-11-17
- Inne parsery:
  - **claude-code-log** (Python 3.12+, JSONL → HTML/TUI) — [sourcepulse](https://www.sourcepulse.org/projects/11717812);
  - **cc-transcript** (PyPI) deklaruje, że „never silently discards sidechains, synthetic turns, tool results, or
    unrecognized entries” — [PyPI](https://pypi.org/project/cc-transcript/7.1.0/);
  - **claude-code-transcripts** (Rust) ma walidator, który porównuje ponownie zserializowany JSON z oryginałem, żeby
    wyłapać nieznane pola — [docs.rs](https://docs.rs/crate/claude-code-transcripts/0.1.8/source/README.md);
  - **clawgs** normalizuje Claude Code i Codex do jednego formatu — [docs.rs](https://docs.rs/crate/clawgs/0.2.0);
  - **abtop** (graykode, ~3,9k gwiazdek, „htop for AI coding agents”) pokazuje tokeny, okno kontekstu i limity;
  - **agentsview** (~6,5k gwiazdek) to lokalne wyszukiwanie i analityka wielu agentów;
  - **splitrail** to tracker kosztów Claude/Codex.

  Trzy ostatnie znalezione w GitHub search 2026-11-17.

### Inferences
- Deduplikacja w A0 musi być **globalna dla przebiegu** (sesja + jej podagenci + kopie między sesjami), a nie
  per plik. Przy powtórzeniu wygrywa rekord kompletny: ostatni albo ten z `stop_reason`, a przy remisie wyższe
  `output_tokens`. Kopia z `isSidechain: true` przegrywa z oryginałem.
- Lista typów rekordów jest długa i rośnie co kilka wersji. Polityka „nieznane liczymy i raportujemy” jest słuszna,
  ale whitelistą powinny być **typy zdarzeń, które emitujemy**, a nie typy, które znamy. Rekordy metadanych
  (`ai-title`, `mode`, `bridge-session` itp.) trzeba pomijać jawnie, bez ostrzeżeń.
- `api-request` i `api-request-blob` to nowe rekordy z kopią żądania. Blob zawiera treść, więc nie wolno go
  traktować jako źródła metadanych bez filtrowania.

### Gaps
- Nie znalazłem repozytoriów o nazwach **cass**, **codex-trace** ani **toolpath-codex** (wyszukiwarka i GitHub search
  nic nie zwróciły). Nie mogę ocenić ich jakości.
- Brak oficjalnego opisu rekordów `cost-state`, `api-request*` i `compact_boundary`. Wszystko wyżej pochodzi z pomiaru
  i kodu narzędzi trzecich.
- Nie sprawdziłem, czy starsze zapisy (typ `summary` z `leafUuid`, sprzed 2.1) jeszcze istnieją na dysku. Przy
  30-dniowej retencji raczej nie.

## 2. Schemat rolloutu Codex (openai/codex, CLI 0.166)

### Takeaway
- Rollout to `{timestamp, type, payload}` z dodanym `ordinal`.
- Bieżący Codex ma per-odpowiedź rekord `token_usage_record` (z `response_id`), a `token_count` niesie i sumę
  (`total_token_usage`), i deltę (`last_token_usage`). Różnicowanie sum nie jest już potrzebne. Wystarczy
  `last_token_usage` albo `token_usage_record`, z deduplikacją po `response_id`.
- `input_tokens` Codexa **zawiera** `cached_input_tokens`, inaczej niż u Claude.
- Wynik narzędzia nie ma serializowanego `success`, więc `is_error` bywa nieznane.

### Cited Findings
- `RolloutItemWire` (tag `type`, snake_case) ma warianty `session_meta`, `dynamic_tools_updated`, `response_item`
  (+`metadata`), `inter_agent_communication`, `inter_agent_communication_metadata`, `compacted`, `turn_context`,
  `token_usage_record`, `world_state`, `retained_context`, `security_risk_score`, `event_msg`, `realtime_item`.
  Typ kompakcji `CompactedItemWire` ma `message`, opcjonalne `replacement_history`, `retained_context` itd.
  — [openai/codex: codex-rs/history/src/rollout_payload.rs](https://github.com/openai/codex/blob/main/codex-rs/history/src/rollout_payload.rs)
- `TokenUsage { input_tokens, cached_input_tokens, cache_write_input_tokens (serde default), output_tokens,
  reasoning_output_tokens, total_tokens }`.
  - `TokenUsageInfo { total_token_usage, last_token_usage, last_usage_includes_reasoning?, model_context_window }`.
  - `TokenUsageRecord { thread_id, turn_id, session_id, root_turn_id, response_id, usage, turn_token_usage,
    thread_token_usage }` jest opisany jako „Best-effort Responses API usage observed for one completed response”.
  - `SessionMeta` ma `session_id` („equal to the root thread's ID”), `id` (thread), `forked_from_id`,
    `parent_thread_id`, `timestamp`, `cwd` i inne.
  - `EventMsg::ContextCompacted` oznacza kompakcję automatyczną lub ręczną.
  - `ExecCommandEndEvent` ma `call_id, command, cwd, exit_code, duration, status, aggregated_output`;
    `PatchApplyEnd` istnieje.

  — [openai/codex: codex-rs/protocol/src/protocol.rs](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/protocol.rs)
- `ResponseItem` ma warianty `AdditionalTools, Message, AgentMessage, Reasoning, LocalShellCall, FunctionCall,
  ToolSearchCall, FunctionCallOutput, CustomToolCall, CustomToolCallOutput, ToolSearchOutput, …`.
  `FunctionCallOutputPayload.success` jest „internal metadata for downstream handling” i nie trafia do zapisu.
  — [openai/codex: codex-rs/protocol/src/models.rs](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/models.rs)
- `codex exec --json` emituje `thread.started {thread_id}` jako pierwsze zdarzenie („Can be used to resume the
  thread later”). Potem idą `turn.started`, `turn.completed {usage{input_tokens, cached_input_tokens,
  cache_write_input_tokens, output_tokens, reasoning_output_tokens}}`, `turn.failed`, `item.*` i `error`.
  — [openai/codex: codex-rs/exec/src/exec_events.rs](https://github.com/openai/codex/blob/main/codex-rs/exec/src/exec_events.rs)
- ccusage (adapter Codex) opisuje logikę, którą trzeba powtórzyć:
  - czyta `sessions/` i `archived_sessions/`, także `.jsonl.zst`;
  - `last_token_usage` to delta, a różnicowanie sum jest tylko fallbackiem;
  - zużycie kompakcji jest w `token_usage_record` dopasowanym do `compacted.payload.compaction_response_id`;
  - rollout podagenta (MultiAgent V2) „replays its parent's history before the child's own turn”. Granica to
    `task_started` … `inter_agent_communication_metadata{trigger_turn:true}`;
  - fork użytkownika (`forked_from_id`) kopiuje rekordy rodzica;
  - przy wznowieniu rollout ponownie zapisuje oryginalne `cli_version`, więc „that field cannot be used to gate
    behavior”;
  - model brać z `turn_context`.

  — [ccusage: Codex adapter README](https://github.com/ccusage/ccusage/blob/main/rust/adapters/codex/src/README.md);
  sforkowane sesje liczone wielokrotnie: [ccusage#897](https://worker-github.zxx930427.workers.dev/ryoppippi/ccusage/issues/897)
- agentsview: „Codex also records prompt-cache writes inside its input count”, dlatego przenosi je do koszyka zapisu
  do cache. — [agentsview: Token Usage & Costs](https://github.com/kenn-io/agentsview/blob/main/docs/token-usage.md)
- Pomiar lokalny (0.166.0, 12 rolloutów, 61 `token_count`):
  - typy najwyższego poziomu: `session_meta, turn_context, world_state, response_item, event_msg,
    token_usage_record`, każdy z `ordinal`;
  - `session_meta` ma `session_id, id, cwd, cli_version, originator, source, model_provider, git{commit_hash, branch,
    repository_url}, base_instructions{…}, creator_user_id, creator_account_id`;
  - model jest w `turn_context.model`;
  - zgodność trzech źródeł dla każdej sesji: suma `last_token_usage` = końcowe `total_token_usage` = suma
    `token_usage_record.usage`;
  - zawsze `cached_input_tokens ≤ input_tokens` i `total_tokens = input_tokens + output_tokens`, a
    `cache_write_input_tokens` było 0;
  - jedyne narzędzie to `custom_tool_call` o nazwie `exec` z `custom_tool_call_output`: brak pola `success`, w nagłówku
    wyniku „Wall time”, bez kodu wyjścia;
  - brak `exec_command_end` w zapisie;
  - `token_count` niesie też `rate_limits{primary, secondary: {used_percent, window_minutes, resets_at}}`.
  — pomiar lokalny 2026-11-17

### Inferences
- Normalizacja Codexa do schematu A0: `cache_read = cached_input_tokens`,
  `input = input_tokens − cached_input_tokens − cache_write_input_tokens`, `cache_write = cache_write_input_tokens`,
  `output = output_tokens` (reasoning już w środku). Bez tego wagi K0 policzą buforowane wejście Codexa jak pełne.
- Kolejność źródeł per wywołanie: `token_usage_record` (dedup po `response_id`), potem `token_count.last_token_usage`
  (dedup powtórzonych snapshotów: identyczne `total` pod rząd), na końcu różnica `total`.
- `is_error` dla Codexa: `exec_command_end.exit_code`, jeśli jest w zapisie, w przeciwnym razie `None`.
  Nie zgadywać z tekstu wyjścia.
- Wersji CLI z `session_meta` nie można używać do przełączania logiki parsera (wznowienie ją kopiuje). Rozpoznanie
  musi iść po kształcie rekordów.

### Gaps
- Nie ustaliłem, które `EventMsg` są utrwalane w rolloucie w 0.166 (polityka utrwalania nie wyszła w code search).
  Lokalnie nie ma `exec_command_end` ani `patch_apply_end`, ale lokalne sesje używały tylko narzędzia `exec`.
- Nie widziałem lokalnie rekordu `compacted` ani `context_compacted`, bo żadna sesja nie była kompaktowana. Kształt
  znam tylko z kodu.

## 3. Czy `usage` z JSONL jest wiarygodne i jak je walidować

### Takeaway
- W bieżącym Claude Code (2.1.3xx) zdeduplikowane `usage` z JSONL jest dokładne dla wywołań, które w zapisie są.
- Brakuje jednak wywołań bocznych: Haiku (tytuły, WebSearch, podsumowania) i kompakcji.
- Claude Code sam zapisuje w zapisie rekord `cost-state` z sumami per model i kosztem `totalCostUSD`. To najtańsze
  źródło walidacji, a `result` z `stream-json` to samo dla `-p`.
- Zgłoszenie #28197 (luty 2026) opisywało starsze wersje.

### Cited Findings
- claude-code#28197 (otwarte 2026-02-24, zamknięte przez bota jako „not planned” 2026-03-24, bez odpowiedzi
  Anthropic). Autor porównał zdeduplikowany JSONL z sumami ze statusline:
  - `input_tokens` miały wartości zastępcze (0–1 w ~75% wpisów);
  - wejście było zaniżone 100–174×, wyjście 11–17×;
  - odczyt i zapis cache wyszły zgodne w granicach 0,7–1,1×;
  - obejście: sumy `context_window.total_*` ze statusline. — [claude-code#28197](https://github.com/anthropics/claude-code/issues/28197)
- Dokumentacja Agent SDK (bieżąca):
  - „Per-step `output_tokens` is a placeholder” z `message_start`. Prawdziwe wyjście jest w `result.usage` /
    `modelUsage`;
  - `usage` w `result` pomija podagentów, a `total_cost_usd` i `modelUsage` je zawierają;
  - „Claude Code saves the session's totals to its transcript when the process exits normally and restores them when
    a later call resumes or forks the session” (od v2.1.277);
  - po crashu `result` może mieć wyzerowane koszty;
  - `total_cost_usd`/`costUSD` „are client-side estimates, not authoritative billing data”;
  - `modelUsage[*].costBasis` (`list`/`managed`/`unknown`) od v2.1.246.

  — [Track cost and usage](https://code.claude.com/docs/en/agent-sdk/cost-tracking.md)
- Pomiar lokalny (33 sesje 2.1.309–2.1.326, rodzic + podagenci): zdeduplikowany JSONL (ostatni rekord per
  `message.id`+`requestId`) porównany z ostatnim `cost-state.modelUsage`
  (`inputTokens, outputTokens, thinkingTokens, cacheReadInputTokens, cacheCreationInputTokens, webSearchRequests,
  costUSD`):
  - w 13 sesjach wszystkie cztery sumy zgadzają się co do tokena, np. 171 wywołań:
    wyjście 100 793 = 100 793, odczyt cache 8 189 342 = 8 189 342;
  - w pozostałych `cost-state` ma dodatkowy model `claude-haiku-*`, którego wywołań nie ma w JSONL (od kilkunastu do
    ~80K tokenów wyjścia na sesję) i nieco więcej tokenów głównego modelu w sesjach z kompakcją (np. 209 789 vs
    221 871 wyjścia przy jednej kompakcji);
  - `input_tokens` z JSONL jest mały, bo to niebuforowana reszta. W `cost-state` bywa o rzędy większy, głównie przez
    boczne wywołania bez cache;
  - w jednej sesji (616 wywołań, 2 kompakcje) JSONL > `cost-state` (odczyt cache 120M vs 98M). Najpewniej
    `cost-state` jest nieaktualny, bo zapisuje się przy normalnym wyjściu.
  — pomiar lokalny 2026-11-17
- Telemetria: `claude_code.api_request` ma `cost_usd`, tokeny i `request_id`, czyli wywołania boczne też. Pole
  `query_source` pozwala je odróżnić. — [Monitoring](https://code.claude.com/docs/en/monitoring-usage)
- Rozliczenie autorytatywne to wyłącznie konsola i Usage & Cost API (organizacja API). Dla subskrypcji nie ma
  rachunku per token. — [Track cost and usage](https://code.claude.com/docs/en/agent-sdk/cost-tracking.md)

### Inferences
- K0 powinien mieć dwa poziomy:
  - per wywołanie z JSONL, do profilu kontekstu i kompakcji;
  - per sesja z `cost-state` (ostatni rekord) albo z `result` stream-json, do sum i walidacji.

  Rozjazd powyżej progu raportować jako „wywołania poza zapisem”, a nie jako błąd.
- Placeholder `output_tokens` z dokumentacji SDK dotyczy wiadomości w strumieniu. W bieżących plikach JSONL
  wyjście jest finalne, co potwierdzają równe sumy. Wersje się różnią, więc test zgodności z `cost-state` trzeba
  uruchamiać na każdej nowej wersji CLI.
- Wybór „ostatni rekord” jest poprawny, ale nie wystarcza do pełnej sumy sesji. Do sumy potrzebny jest `cost-state`.

### Gaps
- Nie wiem, od której wersji Claude Code pisze `cost-state`. Lokalnie jest od 2.1.309, a starszych sesji nie ma.
- Nie zweryfikowałem dokładności `costUSD` względem cen API. To estymata z cennika wbudowanego w klienta.

## 4. OpenTelemetry: Claude Code i Codex, mały odbiornik w stdlib

### Takeaway
- Claude Code eksportuje OTLP w protokołach `grpc`, `http/json` i `http/protobuf`. Od v2.1.323 domyślny jest
  `http/protobuf`, więc odbiornik w stdlib wymaga jawnego `OTEL_EXPORTER_OTLP_PROTOCOL=http/json`.
- Odbiornik `POST /v1/logs` odpowiadający `200 {}` to kilkadziesiąt linii `http.server`.
- Codex dokumentuje tylko `binary` dla `otlp-http`.
- Ponieważ `cost-state` i `result` dają sumy bez usług, OTel jest dla nas opcją dodatkową, nie koniecznością.

### Cited Findings
- Zdarzenia Claude Code:
  - `claude_code.api_request`: `model, cost_usd, cost_usd_micros, duration_ms, input_tokens, output_tokens,
    cache_read_tokens, cache_creation_tokens, cache_creation_5m_tokens, cache_creation_1h_tokens, request_id,
    client_request_id, speed, query_source, effort` oraz atrybucja `agent.name, skill.name, plugin.name,
    mcp_server.name, mcp_tool.name`;
  - `claude_code.api_error`;
  - `claude_code.tool_result`: `tool_name, tool_use_id, success, duration_ms, tool_input_size_bytes,
    tool_result_size_bytes`, a `error` i `tool_input` tylko z `OTEL_LOG_TOOL_DETAILS=1`;
  - `claude_code.tool_decision`, `claude_code.user_prompt`, `subagent_completed` i inne.

  Atrybuty wspólne to m.in. `session.id`, `user.email`, `user.account_uuid`, `organization.id`. Atrybuty zdarzeń:
  `prompt.id`, `event.sequence`, `agent_id`, `parent_agent_id`, `workspace.host_paths`.

  Metryki: `claude_code.cost.usage`, `claude_code.token.usage` (`type` = `input|output|cacheRead|cacheCreation`).

  Interwały: logi co 5000 ms, metryki co 60000 ms. Treść jest domyślnie wyłączona (`OTEL_LOG_USER_PROMPTS`,
  `OTEL_LOG_TOOL_DETAILS`, `OTEL_LOG_RAW_API_BODIES` itd.).

  W `-p` czytany jest przychodzący `TRACEPARENT`. — [Monitoring usage](https://code.claude.com/docs/en/monitoring-usage)
- Wymagania OTLP/HTTP:
  - port 4318, ścieżka `/v1/logs`, `Content-Type: application/json`, klucze lowerCamelCase;
  - int64 (`timeUnixNano`) zapisane jako string;
  - odbiornik musi ignorować nieznane pola i obsługiwać `gzip` w `Content-Encoding`;
  - sukces to HTTP 200 z `ExportLogsServiceResponse` bez `partial_success`.

  — [OTLP specification](https://opentelemetry.io/docs/specs/otlp/)
- Codex: sekcja `[otel]` w `config.toml` z `exporter = none|otlp-http|otlp-grpc`, przy czym dla `otlp-http` przykład
  ma tylko `protocol = "binary"`. Pozostałe ustawienia:
  - `log_user_prompt = false` domyślnie;
  - zdarzenia `codex.conversation_starts, codex.api_request, codex.sse_event` (tokeny tylko na `response.completed`),
    `codex.user_prompt, codex.tool_decision, codex.tool_result` (z fragmentem wyniku);
  - metryka `turn.token_usage`;
  - eksport „flush on shutdown”. — [Codex advanced config](https://learn.chatgpt.com/docs/config-file/config-advanced)

### Inferences
- Odbiornik dla `night` na hoście jest praktyczny: wątek z `http.server.ThreadingHTTPServer`, dekodowanie gzip,
  zapis `api_request` do jsonl.
- Dla zadań Signal w Dockerze jest gorzej. Kontener jest w sieci `--internal` z jednym proxy, więc trzeba by
  udostępnić odbiornik w tej sieci. Wymaga to dodatkowej dziury w izolacji tylko po to, żeby zdublować dane,
  które `result` już daje.
- Ryzyko krótkich przebiegów `-p`: logi wychodzą co 5 s. Dokumentacja nie mówi, czy przy wyjściu jest flush, więc
  przed oparciem na OTel trzeba to sprawdzić empirycznie.

### Gaps
- Brak dokumentacji, czy Claude Code robi flush eksportera przy zakończeniu `-p`, i czy domyślnie kompresuje gzipem.
- Nie ustaliłem, czy Codex `otlp-http` przyjmuje `protocol = "json"` (dokumentacja pokazuje tylko `binary`) ani czy
  `codex exec` eksportuje telemetrię.

## 5. Deterministyczne identyfikatory sesji i zapisy w Dockerze

### Takeaway
- `claude -p --session-id <uuid>` jest oficjalną flagą (wymaga poprawnego UUID).
- `stream-json`/`json` zwraca `session_id`, `total_cost_usd`, `usage` i `modelUsage`.
- Codex: `thread_id` z pierwszego zdarzenia `thread.started` w `codex exec --json`. Jest równy `session_meta.id`
  i stoi w nazwie `rollout-<ts>-<id>.jsonl`.
- **Zadania Signal w Dockerze dziś tracą zapis**: `HOME=/home/agent` to tmpfs. Zostaje tylko log stream-json.

### Cited Findings
- `--session-id`: „Use a specific session ID for the conversation (must be a valid UUID)”. Pozostałe flagi:
  - `--fork-session` tworzy nowy ID przy wznowieniu;
  - `--no-session-persistence` (tylko `-p`) wyłącza zapis;
  - `--max-budget-usd` liczy się według estymaty klienta, łącznie z podagentami;
  - `--forward-subagent-text` emituje tekst podagentów z `parent_tool_use_id`. — [CLI reference](https://code.claude.com/docs/en/cli-reference)
- Sesje `-p` nie pojawiają się w pickerze, ale ich zapis istnieje. `transcript_path` dostają hooki, np. `SessionEnd`
  może zarchiwizować zapis. `CLAUDE_CONFIG_DIR` + `CLAUDE_CODE_PROJECT_DIR_NAME` (v2.1.234+) przenoszą zapisy do
  `<CLAUDE_CONFIG_DIR>/projects/<nazwa>/`. Dwa równoległe `-p` na tej samej sesji piszą do jednego pliku i mogą
  zgubić wiadomości. — [Manage sessions](https://code.claude.com/docs/en/sessions.md)
- Codex `exec`: pierwsze zdarzenie to `ThreadStarted { thread_id }`. — [exec_events.rs](https://github.com/openai/codex/blob/main/codex-rs/exec/src/exec_events.rs).
  `SessionMeta.session_id` „is equal to the root thread's ID”. — [protocol.rs](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/protocol.rs).
  `CODEX_HOME` domyślnie wskazuje `~/.codex`, a `[history] persistence = "none"` wyłącza zapis.
  — [Codex advanced config](https://learn.chatgpt.com/docs/config-file/config-advanced)
- Repo `house`:
  - `houselib/agentdocker.py` uruchamia kontener z `--read-only`, `--tmpfs /home/agent`, `HOME=/home/agent` i
    `CLAUDE_CODE_TMPDIR=/home/agent`. Zamontowane są tylko worktree, `.git` (ro), `/house` (ro), prompt i settings;
  - `houselib/signalagent.py:_claude_argv` wywołuje `claude -p … --output-format stream-json --verbose` bez
    `--session-id`, a koszt bierze z `total_cost_usd` ostatniej linii `result`;
  - `night` uruchamia `claude -p --agent coder`. — pomiar lokalny 2026-11-17 (kod repo)

### Inferences
- Signal/Docker: najprostsze i zgodne z izolacją rozwiązanie składa się z trzech kroków:
  1. nadać `--session-id <uuid>`;
  2. ustawić `CLAUDE_CONFIG_DIR=/job/claude` i `CLAUDE_CODE_PROJECT_DIR_NAME=<job-id>` na dodatkowym montażu rw
     katalogu `signal/agent/<id>/claude/`, żeby zapis wraz z podagentami przeżył kontener;
  3. A0 czyta wtedy `signal/agent/<id>/claude/projects/<job-id>/<uuid>.jsonl`.

  Alternatywa bez zmiany montaży: K0 dla zadań Signal tylko z `result` (suma, bez profilu kontekstu).
  `CLAUDE_CONFIG_DIR` nie zawiera tokenu, bo ten idzie przez `CLAUDE_CODE_OAUTH_TOKEN` w env, ale trzeba to
  sprawdzić.
- `night`: `--session-id` od razu zastępuje heurystykę „najnowszy plik w oknie czasu”.

### Gaps
- Nie sprawdziłem, co dokładnie Claude Code zapisuje do `CLAUDE_CONFIG_DIR` w trybie `-p` z tokenem OAuth (czy
  pojawia się tam plik z poświadczeniami).

## 6. Limity subskrypcji (Pro/Max) a wagi cen API

### Takeaway
Anthropic nie publikuje, jak tokeny (wejście, odczyt i zapis cache, wyjście) ważą się w limitach 5-godzinnym
i tygodniowym. Zgłoszenia użytkowników sugerują, że odczyty cache ważą w limicie tygodniowym więcej niż 0,1 z cennika
API. Wagi K0 to więc wyłącznie estymata kosztu API, nie limitu. Codex jako jedyny zapisuje procent zużycia limitu
w zapisie.

### Cited Findings
- „Pro and Max plans have a five-hour session limit and a weekly limit.” Limity są wspólne dla Claude i Claude Code,
  Max/Ultra mają osobny tygodniowy limit dla Fable, a artykuł nie opisuje wag tokenów.
  — [Using Claude Code with your Pro or Max plan](https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan)
- Dialog „Resume this conversation?” podaje „the share of your 5-hour usage limit that the saved conversation alone
  would use”. Oznacza to, że długość historii (ponowny zapis i odczyt kontekstu) liczy się do limitu.
  — [Manage sessions](https://code.claude.com/docs/en/sessions.md)
- Na subskrypcji w ramach planu główne tury dostają TTL cache 1 h bez konfiguracji. Przy usage credits Claude Code
  schodzi do 5 min. — [Track cost and usage](https://code.claude.com/docs/en/agent-sdk/cost-tracking.md)
- Zgłoszenia o odczytach cache w limicie tygodniowym liczonych z wagą bliską pełnej
  ([#58786](https://claudeissues.com/issue/58786-bug-weekly-all-models-cap-metering-cache-read-input-tokens-at-full-weight-vs-5h),
  [#59872](https://claudeissues.com/issue/59872-opus-4-6-cache-read-tokens-counted-at-full-rate-despite-cache-hits-on-max-20x),
  [#75290](https://claudeissues.com/issue/75290-bug-max-5x-plan-70m-cache-read-tokens-counted-against-quota-62-day-on-a-100-mont))
  to anegdoty bez potwierdzenia Anthropic. Ich autorzy przyznają, że licznika serwera nie widać.
- Codex: `token_count.rate_limits.primary/secondary` z `used_percent`, `window_minutes`, `resets_at` w każdym
  zdarzeniu. — pomiar lokalny 2026-11-17 (0.166)

### Inferences
- Dla Claude jedyny wiarygodny pomiar limitu to przyrost `/usage` przed i po przebiegu (ręcznie). Dla Codexa
  przyrost `used_percent` w zapisie można zestawić z tokenami i wyestymować wagi empirycznie.
- Rachunek opłacalności kompakcji z §4 speca short-context jest poprawny dla kosztu API. Dla limitu tygodniowego,
  jeśli zgłoszenia są prawdziwe, kompakcja opłaca się **szybciej**, bo odczyt cache waży więcej.

### Gaps
- Brak oficjalnych wag limitów dla jakichkolwiek rodzajów tokenów. Nie sprawdziłem, czy wejście statusline Claude
  Code zawiera dziś pola limitów (`rate_limits`).

## 7. Prywatność: co jest w zapisach i jak redagować

### Takeaway
Zapisy obu narzędzi zawierają pełną treść promptów, wyników narzędzi (w tym sekrety z `cat .env`, `env`), kopie plików
i żądań API, identyfikatory konta i organizacji oraz URL repozytorium. Wbudowanej redakcji w Claude Code nie
potwierdziłem. Narzędzia trzecie redagują po fakcie albo wcale nie ruszają surowych zapisów.

### Cited Findings
- Audyt jednego dewelopera znalazł w zapisach ~600 sekretów, w tym klucze Azure. Zgłoszenia o wycieku kluczy przez
  `Read`/`cat` do zapisu i o prośbie o redakcję przy zapisie:
  [claude-code#58043](https://claudeissues.com/issue/58043-read-cat-on-config-files-leaks-api-keys-into-conversation-transcript),
  [#86707](https://claudeissues.com/issue/86707-feature-redact-credential-shaped-values-when-writing-session-transcripts),
  [#63593](https://claudeissues.com/issue/63593-security-built-in-secret-redaction-at-jsonl-write-time-in-claude-projects).
  — [DEV: Claude Code stored 600 of my secrets](https://dev.to/rentierdigital/i-spent-25-years-avoiding-malware-claude-code-stored-600-of-my-secrets-anyway-3e2j).
  Status tych zgłoszeń jest znany tylko ze snippetów wyszukiwarki.
- Narzędzia redakcji: `claude-code-scrubber` (PyPI) i `shhh` (Claude/Codex/opencode/Gemini). Entire nie redaguje
  surowych plików sesji. — [claude-code-scrubber](https://pypi.org/project/claude-code-scrubber/),
  [shhh](https://github.com/trananhhh/shhh), [Entire security](https://docs.entire.io/security.md)
- Wrażliwe pola w Claude Code, z pomiaru lokalnego:
  - treść: `message.content` (tekst, `thinking`, `tool_use.input`, `tool_result`), `toolUseResult`,
    `api-request-blob.message` (kopia żądania), `file-history-snapshot`/`file-history-delta` (kopie plików),
    `tool-results/*.txt`, `last-prompt`, `ai-title`, `queue-operation.content`;
  - identyfikatory: `session-owner` (`accountUuid`, `organizationUuid`), `bridge-session`, `pr-link`, `cwd`,
    `gitBranch`.

  Codex: `session_meta.creator_user_id/creator_account_id`, `git.repository_url`, `base_instructions`, `world_state`
  (instrukcje, AGENTS.md), `reasoning.encrypted_content`, wyniki `exec`. — pomiar lokalny 2026-11-17
- OTel Claude Code domyślnie nie wysyła treści, ale atrybuty standardowe obejmują `user.email` i `user.account_uuid`.
  — [Monitoring usage](https://code.claude.com/docs/en/monitoring-usage)

### Inferences
- Granica A0 („treść nie wychodzi poza czytnik”) jest słuszna. Widok bez treści powinien być **whitelistą pól**
  (liczby, nazwy narzędzi, identyfikatory wywołań, timestampy), a nie usuwaniem znanych pól treści, bo nowe typy
  rekordów (`api-request-blob`, `file-history-*`) niosą treść pod nowymi kluczami.
- `tool_call.input` z §6 speca A0 („np. komenda Bash, ścieżka Edit/Write”) to treść i miejsce typowego wycieku
  sekretów (`curl -H "Authorization: …"`, `export X=…`). Komenda Bash też powinna przechodzić przez skaner `guard`
  albo widok powinien trzymać tylko pierwszy token komendy.

### Gaps
- Nie sprawdziłem, czy któraś wersja Claude Code wprowadziła redakcję przy zapisie (#63593 oznaczono jako
  rozwiązane, ale bez szczegółów).

## Rekomendacje dla speca

Zmiany w A0 (`docs/specs/2026-11-16-agno-adoptions-design.md` §6) i K0
(`docs/specs/2026-11-16-short-context-design.md` §7):

1. **MUST: odkrywanie plików podagentów.** Sesja Claude Code to `<session>.jsonl` + `<session>/subagents/agent-*.jsonl`
   (+ `.meta.json` z `toolUseId`, `agentType`, `model`, `spawnDepth`). Czytnik zwraca podagentów jako zdarzenia
   z `agent_id` i wiązaniem do `tool_use_id` rodzica, a K0 domyślnie sumuje drzewo.
   Źródła: [Inside the subagent trace file](https://frederick-douglas-pearce.github.io/blog/2026/inside-the-subagent-trace-file/),
   [ccusage Claude README](https://github.com/ccusage/ccusage/blob/main/rust/adapters/claude/src/README.md),
   pomiar lokalny.
2. **MUST: deduplikacja globalna w przebiegu, nie per plik.** Klucz `message.id`+`requestId` działa między plikami.
   Przy powtórzeniu wygrywa rekord kompletny: ostatni lub ten z `stop_reason`, a przy niezgodności wyższe
   `output_tokens`. Kopia z `isSidechain: true` i tym samym `message.id` przegrywa z oryginałem (repliki `/btw`).
   Pomijać `model == "<synthetic>"`. Fixture'y akceptacji: replika sidechaina i ta sama odpowiedź w dwóch sesjach.
   Źródła: [ccusage Claude README / #913](https://github.com/ccusage/ccusage/blob/main/rust/adapters/claude/src/README.md),
   [agentsview token usage](https://github.com/kenn-io/agentsview/blob/main/docs/token-usage.md).
3. **MUST: zdarzenie `session_total` z rekordu `cost-state`** (ostatni w pliku: `modelUsage` per model,
   `totalCostUSD`) i z linii `result` stream-json (`total_cost_usd`, `modelUsage`, `session_id`). Wymaga dopisania do
   listy zdarzeń A0. K0 pokazuje obok sumy z wywołań kolumnę „poza zapisem” = `session_total − Σ usage` per model.
   To zastępuje plan „sprawdzić K0 przez `/cost` albo OTel”, bo walidacja jest wtedy automatyczna i bez usług.
   Źródła: [Track cost and usage](https://code.claude.com/docs/en/agent-sdk/cost-tracking.md), pomiar lokalny
   (13 z 33 sesji co do tokena, reszta różni się o Haiku i kompakcję).
4. **MUST: normalizacja tokenów Codexa.** `cache_read = cached_input_tokens`,
   `input = input_tokens − cached_input_tokens − cache_write_input_tokens`, `cache_write = cache_write_input_tokens`,
   `output = output_tokens` (reasoning zawarty). Spec mówi dziś tylko „różnica narastających”, a bez tej normalizacji
   wagi K0 policzą buforowane wejście Codexa ×1.
   Źródła: [protocol.rs TokenUsage](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/protocol.rs),
   [ccusage Codex README](https://github.com/ccusage/ccusage/blob/main/rust/adapters/codex/src/README.md),
   [agentsview](https://github.com/kenn-io/agentsview/blob/main/docs/token-usage.md), pomiar lokalny
   (`cached ≤ input` w 61/61).
5. **MUST: źródło `usage` Codexa według kolejności.** Najpierw `token_usage_record` (dedup po `response_id`), potem
   `event_msg/token_count.info.last_token_usage` (pomijać `info: null` i powtórzone identyczne `total`), na końcu
   różnica `total_token_usage`. Zastąpić zdanie „usage wywołania to różnica kolejnych wartości”.
   Źródła: [protocol.rs](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/protocol.rs),
   [ccusage Codex README](https://github.com/ccusage/ccusage/blob/main/rust/adapters/codex/src/README.md).
6. **MUST: Signal w Dockerze traci zapis (tmpfs `/home/agent`).** Do A0 (znajdowanie sesji) dopisać:
   - zadania Signal dostają `--session-id <uuid>`;
   - zadania Signal dostają `CLAUDE_CONFIG_DIR` na montażu rw w `signal/agent/<id>/claude/` z
     `CLAUDE_CODE_PROJECT_DIR_NAME=<job-id>`;
   - albo jawnie: dla zadań Signal K0 ma tylko sumę z `result`.

   Bez tego „sesje workerów” w K0 nie obejmują Signal. Źródła: [Manage sessions](https://code.claude.com/docs/en/sessions.md),
   `houselib/agentdocker.py`, `houselib/signalagent.py:_claude_argv`.
7. **MUST: `night` używa `claude -p --session-id <uuid>` i `codex exec --json` → `thread.started.thread_id`.**
   Zamienić „do sprawdzenia” na fakt i usunąć fallback „najnowszy rollout w oknie czasu” jako ścieżkę główną.
   Źródła: [CLI reference](https://code.claude.com/docs/en/cli-reference),
   [exec_events.rs](https://github.com/openai/codex/blob/main/codex-rs/exec/src/exec_events.rs).
8. **MUST: kompakcja jako zdarzenie z liczbami.** Claude: `system/compact_boundary` z
   `compactMetadata.trigger/preTokens/postTokens/durationMs` (+ następny `user.isCompactSummary`). Codex: rekord
   `compacted` lub `event_msg/context_compacted`, z zużyciem kompakcji w `token_usage_record` o
   `response_id == compacted.compaction_response_id`. K0 dostaje wtedy L (preTokens) i P+R (postTokens) wprost do
   wzoru n* z §4. Źródła: pomiar lokalny,
   [rollout_payload.rs](https://github.com/openai/codex/blob/main/codex-rs/history/src/rollout_payload.rs),
   [ccusage Codex README](https://github.com/ccusage/ccusage/blob/main/rust/adapters/codex/src/README.md).
9. **SHOULD: widok bez treści jako whitelista pól, a `tool_call.input` przez `guard`.** Nowe rekordy z treścią
   (`api-request-blob`, `file-history-*`, `tool-results/`) mają być pomijane domyślnie. Komenda Bash to treść, więc
   w widoku zostaje pierwszy token komendy i ścieżki po skanerze sekretów. Źródła:
   [claude-code#58043](https://claudeissues.com/issue/58043-read-cat-on-config-files-leaks-api-keys-into-conversation-transcript),
   [DEV: 600 secrets](https://dev.to/rentierdigital/i-spent-25-years-avoiding-malware-claude-code-stored-600-of-my-secrets-anyway-3e2j),
   pomiar lokalny.
10. **SHOULD: rozpoznawanie formatu i gałęzi parsera po kształcie, nie po `version`/`cli_version`.** Codex przepisuje
    `cli_version` przy wznowieniu. Wersję nadal dołączać do zdarzeń (do raportu), ale nie używać jej do logiki.
    Źródło: [ccusage Codex README](https://github.com/ccusage/ccusage/blob/main/rust/adapters/codex/src/README.md).
11. **SHOULD: forki i podagenci Codexa.** Pomijać zreplikowany prefiks historii rodzica: granica `task_started` …
    `inter_agent_communication_metadata{trigger_turn:true}`. Przy `forked_from_id` nie liczyć skopiowanych rekordów
    drugi raz. Fixture akceptacji: sforkowany rollout. Źródła:
    [ccusage Codex README](https://github.com/ccusage/ccusage/blob/main/rust/adapters/codex/src/README.md),
    [ccusage#897](https://worker-github.zxx930427.workers.dev/ryoppippi/ccusage/issues/897).
12. **SHOULD: `is_error` trójwartościowe (`True/False/None`).** Claude: `tool_result.is_error`, a `toolUseResult` ma
    trzy warianty (dict/str/list). Codex: `exec_command_end.exit_code`, jeśli jest w zapisie, w przeciwnym razie
    `None` (pole `success` nie jest serializowane). A1 nie może traktować `None` jako sukcesu. Źródła:
    [models.rs](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/models.rs), pomiar lokalny.
13. **SHOULD: odczyt także `archived_sessions/` i `.jsonl.zst` Codexa.** `.zst` nie da się zdekompresować w stdlib
    3.11, więc liczyć je jako „pominięte (zst)” zamiast milczeć. Źródło:
    [ccusage Codex README](https://github.com/ccusage/ccusage/blob/main/rust/adapters/codex/src/README.md).
14. **SHOULD: K0 nie nazywa wag „kosztem limitu”.** Dopisek zostaje („oszacowanie kosztu API, nie rachunek i nie
    zużycie limitu subskrypcji”). Pytanie 1 z §4 short-context zamknąć jako „nieznane, Anthropic nie publikuje”.
    Dla Codexa dodać kolumnę przyrostu `rate_limits.*.used_percent` z zapisu. Źródła:
    [support: Pro/Max plan](https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan),
    [#58786](https://claudeissues.com/issue/58786-bug-weekly-all-models-cap-metering-cache-read-input-tokens-at-full-weight-vs-5h),
    pomiar lokalny.
15. **COULD: odbiornik OTLP/HTTP-JSON tylko dla `night` na hoście**, i to dopiero gdy kolumna „poza zapisem” z pkt 3
    okaże się niewystarczająca. Wymaga `OTEL_EXPORTER_OTLP_PROTOCOL=http/json` (od 2.1.323 domyślny jest protobuf),
    obsługi gzip i odpowiedzi 200 `{}`, a przed użyciem w krótkich `-p` empirycznej próby flushu. Dla Dockera nie
    budować. Źródła: [Monitoring usage](https://code.claude.com/docs/en/monitoring-usage),
    [OTLP spec](https://opentelemetry.io/docs/specs/otlp/).
16. **COULD: pola dodatkowe w `usage`.** `output_tokens_details.thinking_tokens`, `server_tool_use.web_search_requests`
    (opłata za wyszukiwanie liczona osobno, tokeny bocznego Haiku są poza zapisem), `iterations[type=advisor_message]`
    pod modelem doradcy, `speed`/`service_tier`. Źródła:
    [ccusage Claude README](https://github.com/ccusage/ccusage/blob/main/rust/adapters/claude/src/README.md),
    [agentsview](https://github.com/kenn-io/agentsview/blob/main/docs/token-usage.md), pomiar lokalny.
17. **COULD: test regresji formatu.** Na każdą nową wersję CLI uruchamiać lokalnie (nie w CI, bez commitowania
    danych) porównanie `Σ usage` z `cost-state` dla głównego modelu na ostatnich N sesjach. Rozjazd w sesjach bez
    kompakcji oznacza zmianę formatu. Źródło: pomiar lokalny, [Manage sessions: format wewnętrzny](https://code.claude.com/docs/en/sessions.md).

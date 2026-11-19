# Poprawki systemu po przejrzeniu Agno (2026-11-16)

_Stan: zrewidowany 2026-11-17 po researchu i recenzji, **zaakceptowany ponownie przez Wojtka 2026-11-17**. Pierwotnie
zaakceptowany 2026-11-16, tego samego dnia dopisane wymagania ze speca `2026-11-16-short-context-design.md`
(K1 w A2, zdarzenia `usage`/`compaction` w A0). Źródła: repozytorium `agno-agi/agno` (Apache 2.0, stan
z 2026-11-16), szablon `agno-agi/agentos-railway` (skille `.agents/skills/improve`, `create-evals`), research
`docs/research/reports/Research speców Agno i kontekstu.md` (odwołania „[R A0.3]” to wiersze jego tabeli,
tam są linki) i recenzja `docs/research/reports/Recenzja Fable speców Agno i kontekstu.md` (odwołania „[Rec W6]”).
Oznaczenia decyzji: **[moja decyzja]** podjął Claude 2026-11-16, **[decyzja Claude]** podjął Claude 2026-11-17
(trzeba je sprawdzić przy czytaniu), „(Wojtek, 2026-11-17)” to decyzje Wojtka. Pytania do Wojtka są w §15._

## 1. Po co

Agno to ciężka platforma agentów: serwer, PostgreSQL, UI. Całości nie bierzemy. Kilka rozwiązań pasuje jednak
do `house` prawie 1:1, bo rozwiązują problemy, które my też mamy:

- **Deklaracja agenta to nie dowód.** Agno w `company/evidence.py` składa relację z tego, co próba zadania
  faktycznie zrobiła. Bierze ją z zapisu przebiegu, nie z raportu workera. `house merge` już sam uruchamia
  kryteria, ale poza bramką nadal wierzymy raportom. Dotyczy to porannego raportu `house night`, briefu dla kolejnej
  próby (dziś 600 znaków komunikatu o porażce) i `/house-retro`, który ręcznie grzebie w plikach jsonl. Research
  potwierdza kierunek: fałszywy sukces to 75,8% porażek agentów kodujących, a detektory z trajektorii biją
  sędziów LLM (AUROC 0,83–0,95 wobec ≤0,65) [R A1.2].
- **Skille i profile agentów nie mają testów zachowania.** Testy w `tests/` sprawdzają kod `houselib`, ale nikt
  nie sprawdza, czy koder rzeczywiście scala przez `house merge` albo czy coordinator zamraża kryteria. Agno ma tani
  eval „reliability” (oczekiwane, brakujące i nadmiarowe wywołania narzędzi oraz ich argumenty) i skill `/improve`.
  To jest punkt 3 roadmapy.
- **Długie sesje gubią kontekst przy streszczaniu.** Kompakcja w Agno streszcza zawsze w tych samych sekcjach,
  ułożonych od najważniejszej, a oryginał zostaje w archiwum. Streszczenie jest wtedy indeksem do pełnego zapisu.
  Ustalenia człowieka giną w streszczeniach mierzalnie (30% naruszeń polityki po kompakcji, 0% po przypięciu jej
  poza streszczeniem) [R A2.2], więc te przywraca kod, nie streszczenie.

Zasada z 2026-11-12 („co da się sprawdzić kodem, sprawdza kod”) dostaje drugą połowę: **co da się odczytać z zapisu
przebiegu, odczytuje kod, nie model z pamięci.**

## 2. Cele, nie-cele, anty-cele

**Cele**
- Każda próba zadania ma zwięzłą relację z zapisu sesji. Relacja trafia do raportu nocy (krótka forma), do briefu
  kolejnej próby i do retro.
- Zachowanie agentów `house` da się sprawdzić powtarzalnym, tanim evalem, a regresje skilli wychodzą przed
  konkursem, nie w trakcie.
- Kolejna sesja lub próba dostaje przekazanie w stałym formacie. Format zachowuje identyfikatory, ścieżki i błędy
  dosłownie i wskazuje pełny zapis. Ustalenia człowieka po kompakcji przywraca kod.
- Pełny wynik komendy zakończonej błędem (czerwone testy, wywrotka treningu) jest dostępny w całości, a do
  kontekstu trafia ograniczona koperta.

**Nie-cele**
- Serwer, baza danych, UI i API (AgentOS). `house` zostaje przy plikach i bibliotece standardowej.
- Silnik wyszukiwania (FTS5, Qdrant, inny). To osobny temat, prowadzony w innym czacie. Wyszukiwanie użyje potem
  czytnika A0, ale A0 nie jest pod nie projektowany **[decyzja Claude]**.
- Zamiana DAG-u zadań na workflowy Agno (router, warunki, pętle).
- Pełny benchmark z roadmapy pkt 3 (symulowany 6-godzinny hackathon, failure injection). A3 daje mu narzędzie,
  samego benchmarku nie buduje.

**Anty-cele** (porażka, nawet jeśli działa)
- Relacja z przebiegu, która coś wnioskuje albo ocenia („agent prawdopodobnie…”). Ma zawierać tylko fakty z zapisu.
- Prywatna treść sesji (fragmenty rozmów, sekrety, dane z Signala) w plikach, które widzi drużyna albo które
  trafiają do eksportu.
- Evale, które da się „zazielenić” poprawianiem promptów pod test, zamiast naprawą przyczyny.
- Nowa zależność spoza biblioteki standardowej.

## 3. Ograniczenia

- Python 3.11+, tylko biblioteka standardowa, testy `python3 -m unittest discover -s tests`.
- Formaty zapisów nie są publicznym kontraktem. Dokumentacja Claude Code mówi wprost, że format „changes between
  versions” [R A0.1]. Parser jest tolerancyjny: nieznany rekord liczy i pomija, uszkodzoną linię pomija, nigdy się
  nie wywraca. Format rozpoznaje po kształcie rekordów, a pole wersji dołącza tylko informacyjnie (Codex
  przepisuje `cli_version` przy wznowieniu) [R A0.10].
- Zapisy sesji interaktywnych Claude Code żyją domyślnie 30 dni (`cleanupPeriodDays`). Relacja i koszt „na
  żądanie” dla starszej sesji nie istnieją [Rec W1].
- Kod Agno jest na Apache 2.0. Bierzemy pomysły. Tekst przeniesiony prawie dosłownie (np. sekcje streszczenia)
  dostaje w docstringu źródło i licencję, tak jak `house-retro` przy skillu Matta Pococka.
- `.house/` nie trafia do eksportu (`export.EXCLUDED_DIRS`), ale jest w repo konkursu, które widzi drużyna,
  a `merge._commit_house` robi `git add -A -- .house` przy każdym scaleniu (`houselib/merge.py:60-64`). Każdy nowy
  plik w `.house/` trafia więc do gita, chyba że leży w `.house/tmp/` (jedyny wpis szablonu `.house/.gitignore`;
  istniejące konkursy nie dostają nowych wpisów ignorowania) [Rec W7].

**Polityka gita dla nowych plików** **[decyzja Claude]**. Jedno źródło prawdy dla wszystkich pakietów:

| Plik | W gicie | Uwagi |
|---|---|---|
| `.house/attempts.jsonl` (A1) | śledzony | bez treści sesji; ścieżki względne do korzenia konkursu; worker go nie rusza (guard) |
| `.house/reports/T###-attempt-N.md` (A1/A2) | śledzony | kopia raportu workera po próbie N, jak inne raporty |
| relacja `house evidence` (A1) | nie zapisywana | liczona na żądanie; do raportu nocy idzie tylko krótka forma |
| log strumienia workera `.house/tmp/night/<data>/T###-<czas>-N.jsonl` (A0) | ignorowany (`tmp/`) | |
| `.house/tmp/out/<id>.log` (A4) | ignorowany (`tmp/`) | |
| `.house/tmp/compactions.jsonl` (A2) | ignorowany (`tmp/`) | |
| `.house/sources/*` i `.house/sources/index.json` (K3) | śledzone | dokumenty konkursu, widzi je drużyna |
| `evals/results.jsonl` w repo `house` (A3) | ignorowany | lokalna historia przebiegów |
| blokady `night.lock`, `eval.lock`, `upkeep.lock` | poza repo | w katalogu danych (`$HOUSE_DATA_DIR`, domyślnie `~/.local/share/house`) |

## 4. Podejścia

Rozważone sposoby rozpisania:

- **A. Jeden spec zbiorczy** ze wspólnym fundamentem i sekcją na każdy pomysł, potem osobny plan na każdą część.
  Ten sam wzór co spec z 2026-11-12.
- **B. Osobny spec na każdy pomysł.** Każdy da się przerobić niezależnie, ale fundament (czytnik zapisów) trzeba
  by powtarzać w kilku dokumentach i siedem razy akceptować.

Wojtek wybrał **A** (rekomendacja), bo pomysły dzielą fundament, a spec z 2026-11-12 już się sprawdził.

## 5. Pakiety i kolejność

| Pakiet | Co | Zależy od | Priorytet |
|---|---|---|---|
| A0 | `houselib/transcript.py`: czytnik logów strumienia i zapisów sesji | — | fundament |
| A1 | Rejestr prób i `house evidence`: relacja z próby w raporcie nocy, briefie i retro | A0 | wysoki |
| A2a | Kompakcja w sesjach konkursu: `PreCompact`, tryb `compact` w `SessionStart`, licznik `PostCompact` | — | wysoki |
| A2b | Brief kolejnej próby w `night` (Claude i Codex) i szablon raportu | A1 | wysoki |
| A3 (9a) | `house eval`: reliability, bez sędziego LLM | A0 | wysoki |
| A4 | `house out` w wersji minimalnej, wspólny czytnik z logami `house exp` | — | niski |
| A5 | `house decisions`: dziennik decyzji przez konkursy | — | średni |
| A7 | `house upkeep`: jedna komenda i timer systemd | A3 | niski |

Przeniesione do §17 „Na później” (Wojtek, 2026-11-17): A6 (`house memory lint`), skill `/house-improve` (9b)
i sędzia LLM (`criteria`) w `house eval`. Oba ostatnie wracają dopiero, gdy 9a działa.

Podział A2 na A2a i A2b **[decyzja Claude]**: hooki kompakcji nie potrzebują czytnika zapisów ani rejestru prób,
a obniżony próg automatu (K2a) bez nich oznacza częstsze kompakcje domyślnym streszczeniem Claude Code.

**Kolejność łączna dla obu speców** (jedyna obowiązująca; spec short-context do niej odsyła). Krok kończy się,
gdy jego akceptacja przeszła:

0. **Noc bazowa** (Wojtek, 2026-11-17): jedno prawdziwe `house night` na konkursie testowym, na obecnym kodzie,
   przed wdrożeniem A1, A2 i K2a. Daje K4 stan „przed” (short-context §10). Od razu po nocy jednorazowy zrzut
   liczb (skrypt z short-context §4, same liczby, bez treści) z sesji workerów i sesji interaktywnych konkursu,
   bo zapisy znikają po `cleanupPeriodDays` (od 2026-11-17: 90 dni). Konkurs: kopia `grok-bot-wro-2026`
   z 3–5 zadaniami `todo`, w tym jednym `coder-codex` (Wojtek, 2026-11-17).
1. **Bez zależności:** K2a (próg automatu kompakcji w `house adapt`), A2a, K3 część dokumentowa. K2a zmienia też
   sesje workerów `night` (używają `settings.local.json` konkursu), dlatego wchodzi po kroku 0.
2. **A0** w wersji wąskiej + przełączenie `night` i `bin/codex-task` na log strumienia.
3. **A1**, potem **A2b**.
4. **A3 9a** z przypadkami smoke. Chroni wszystkie późniejsze zmiany w `core/`, więc idzie przed K0, A4 i A5.
5. **K0**, **A5**, **A4**, w dowolnej kolejności.
6. Po pierwszym konkursie lub nocy z A1, A2 i K2a: **K4**, decyzja o **K2b**, **A7**, potem pozycje z §17.

Każdy pakiet dostaje osobny plan w `docs/plans/`. Ryzyko poprzedniej kolejności (A0 → A1 → K0 → A2 → K2): pięć
pakietów na najmniej stabilnym elemencie, zanim cokolwiek zmieni zachowanie agenta w konkursie [Rec §6].

## 6. A0: czytnik zapisów sesji (`houselib/transcript.py`)

**Problem.** Kilka miejsc potrzebuje tego samego: co sesja zrobiła. Dziś nikt w `houselib` zapisów nie czyta,
a `/house-retro` robi to ręcznie (wg ROADMAP `grep` po jsonl pada na limitach ugrep i escapowanym unicode).

**Źródła** **[decyzja Claude]**. Sesje, które startuje `house`, dają własny log, więc A0 niczego nie szuka
w katalogu domowym:
- **Sesje bez człowieka** (workery `night`, przebiegi `house eval`, `bin/codex-task`, zadania Signal, `house
  upkeep`): log strumienia jest źródłem głównym dla relacji i kosztu.
  - Claude: `claude -p --session-id <uuid> --output-format stream-json --verbose`, wyjście do pliku. Tak już działa
    `signalagent._claude_argv` (`houselib/signalagent.py:505-510`). `night.worker_command`
    (`houselib/night.py:286-289`) dostaje te flagi, a log workera staje się `….jsonl`.
  - Codex: `codex exec --json`; pierwsze zdarzenie `thread.started` niesie `thread_id` (to samo ID co w nazwie
    pliku rolloutu) [R A0.7]. `bin/codex-task` dostaje `--json` i zapis strumienia do pliku podanego przez
    wołającego.
- **Sesje interaktywne** (coordinator, podagenci, retro): zapis Claude Code w `~/.claude/projects/`. Sesja to
  `<session>.jsonl` **razem z** `<session>/subagents/agent-<agentId>.jsonl`; `agent-<agentId>.meta.json` niesie
  `toolUseId` (wiąże podagenta z wywołaniem Agent/Task u rodzica), `agentType` i `model` [R A0.1]. Rekord
  `cost-state` niesie sumy sesji per model i `totalCostUSD`.
- **Rollout Codexa** (`~/.codex/sessions/…`): tylko jako fallback z kroku „Najpierw sprawdź”. Praca w Codexie
  idzie dziś wyłącznie przez `bin/codex-task` i `review.py`, więc adapter rolloutu jest w §17 [Rec W1].
- Znika heurystyka „najnowszy plik w oknie czasu”. ID sesji jest zawsze znane: z `--session-id`, z `thread_id`
  albo z nazwy pliku sesji interaktywnej.

**Najpierw sprawdź** (przed planem A0, wynik wpisać do planu). Jedno krótkie uruchomienie z jednym wywołaniem
narzędzia i jednym podagentem:
1. `claude -p --session-id <uuid> --output-format stream-json --verbose`: czy zdarzenia `assistant` niosą
   `message.usage` **per wywołanie modelu**, czy zdarzenia podagenta są w strumieniu (z powiązaniem z rodzicem)
   i czy linia `result` ma `total_cost_usd` i `modelUsage`.
2. `codex exec --json`: czy jest `turn.completed.usage` (per wywołanie czy per tura) i czy `command_execution`
   niesie `exit_code` [R A3.8].
3. Rozstrzygnięcie: jeśli strumień Claude niesie `usage` per wywołanie, jest jedynym źródłem dla sesji bez
   człowieka. Jeśli nie, strumień daje wywołania narzędzi i sumy, a `usage` per wywołanie pochodzi z zapisu
   sesji znalezionego po `--session-id` (`~/.claude/projects/*/<uuid>.jsonl`). Dla Codexa sumy na turę
   wystarczają w v1; profil kontekstu Codexa czeka na adapter rolloutu (§17).

Krok jest zakończony, gdy w planie A0 stoi, które pola daje strumień w zainstalowanych wersjach (Claude Code
2.1.326, Codex 0.166 na dzień rewizji), a które idą z fallbacku.

**Zmiana.**
- Jedna funkcja wejściowa: źródło sesji → lista zdarzeń w jednym formacie, niezależnym od narzędzia. Zdarzenia
  trzymają w pamięci dostęp do surowego rekordu; co z nich wychodzi poza proces, decyduje widok z punktu
  „Granica prywatności” niżej.
  - `tool_call`: nazwa narzędzia, identyfikator, wejście;
  - `tool_result`: identyfikator wywołania, `is_error` trójwartościowe (`True`/`False`/`None` = nieznane;
    Claude: `tool_result.is_error` przy trzech kształtach `toolUseResult`; Codex: `exit_code`, jeśli jest),
    rozmiar wyniku [R A0.12];
  - `message`: rola i długość;
  - `usage` (dla K0): jedno zdarzenie na wywołanie modelu, cztery koszyki: wejście niebuforowane, odczyt z cache,
    zapis do cache (z podziałem `ephemeral_5m/1h`, jeśli jest) i wyjście;
  - `compaction`: Claude `system` z `subtype: compact_boundary` → `compactMetadata.trigger`, `preTokens`,
    `postTokens`, `durationMs`; Codex `compacted`/`context_compacted` [R A0.6];
  - `session_total`: sumy sesji per model i koszt, z ostatniego rekordu `cost-state` (zapis) albo z linii `result`
    (strumień). Zawiera wywołania boczne (Haiku do tytułów i WebSearch, część kompakcji), których w zapisie nie ma
    [R A0.3];
  - wspólne pola: znacznik czasu, model, `cwd`, gałąź git, `agent_id` (pusty dla rodzica), `tool_use_id` rodzica
    dla podagenta, `isSidechain`, źródło (`stream`/`jsonl`), wersja CLI (informacyjnie).
- **Deduplikacja globalna dla przebiegu** (rodzic, podagenci, kopie między sesjami) po parze `message.id` +
  `requestId`. Wygrywa rekord kompletny: ten ze `stop_reason` albo ostatni, przy remisie wyższe `output_tokens`.
  Kopia z `isSidechain: true` przegrywa z oryginałem (sidechainy `/btw` powtarzają cache-read rodzica). Rekordy
  z `model == "<synthetic>"` wypadają [R A0.2]. Sesja znana ze strumienia nie jest liczona drugi raz z zapisu
  o tym samym ID.
- **Normalizacja tokenów Codexa**: `input_tokens` Codexa zawiera cache, więc `wejście = input_tokens −
  cached_input_tokens − cache_write_input_tokens`, `odczyt z cache = cached_input_tokens`, `zapis do cache =
  cache_write_input_tokens`, `wyjście = output_tokens` (z rozumowaniem) [R A0.4]. Dotyczy też
  `turn.completed.usage`.
- Rekordy niosące treść pomijane domyślnie: `api-request-blob`, `file-history-snapshot`, `file-history-delta`,
  pliki `<session>/tool-results/` [R A0.9]. Znane rekordy metadanych (`ai-title`, `mode`, `bridge-session`…)
  pomijane jawnie, bez ostrzeżeń; nieznane rekordy, które mogłyby nieść zdarzenia, są liczone i raportowane.
- Czytanie zawsze strumieniowe, linia po linii.
- **Przypisanie sesji interaktywnej do konkursu** [Rec S9]: sesja należy do konkursu, gdy `cwd` z jej rekordów,
  po rozwiązaniu symlinków, leży pod rozwiązanym korzeniem konkursu (także `.worktrees/…`). Kandydatów szuka się
  tylko w katalogach `~/.claude/projects/`, których nazwa koduje ścieżkę konkursu albo jej `realpath`
  (konkurs adoptowany bywa symlinkiem, np. `contests/grok-bot-wro-2026`).
- **Zadania Signal**: log strumienia (`log.jsonl` zadania) jest jedynym źródłem **[decyzja Claude]**. Kontener ma
  `--tmpfs /home/agent` (`houselib/agentdocker.py:138`), więc zapis sesji ginie z nim. Zapisywalnego montażu
  `CLAUDE_CONFIG_DIR` nie dokładamy, bo powiększa powierzchnię piaskownicy dla liczby, którą linia `result`
  już daje [Rec §7]. Jeśli strumień nie niesie `usage` per wywołanie, zadanie Signal ma tylko sumę.
- **Granica prywatności przy zapisie** **[decyzja Claude]** [Rec W6]. Wszystko, co trafia do `.house/`, na stdout
  komend `house` albo do `evals/`, przechodzi przez jedną funkcję widoku. Widok to biała lista pól per narzędzie:
  - `Bash`: `command`, przycięta do ~300 znaków i przepuszczona przez `redact`. Komenda zostaje w całości, nie
    tylko pierwszy token, bo bez argumentów (`pytest tests/x.py -k y`) relacja traci wartość dla briefu i retro;
  - `Edit`/`Write`/`MultiEdit`/`NotebookEdit`/`Read`: tylko `file_path`; `Read` dodatkowo `offset`, `limit` (liczby)
    i `allow_large` (flaga), gdy podane (K3: eval `sources-read-by-range`);
  - `Grep`/`Glob`: `pattern` i `path`;
  - `Agent`/`Task`: `subagent_type` i `description` oraz `prompt_chars`, długość briefu jako liczba (treść nigdy);
  - `Skill`: nazwa skilla;
  - każde inne narzędzie: tylko nazwa;
  - wynik narzędzia: `is_error`, rozmiar; dla błędów ostatnie ~5 linii, przycięte i po `redact` (A1).

  Liczby, nazwy narzędzi, identyfikatory i czasy przechodzą zawsze. `redact(text)` to nowa mała funkcja
  w `guard` na wzorcach `export.SECRET_RES` (`houselib/export.py:19-31`): zamienia trafienie na `[REDACTED]`,
  nie odrzuca fragmentu. Dziś skanera na tekście nie ma (`guard._secret_file` działa na pliku).
- Telemetria OTel zostaje na później (§17).

**Akceptacja.** Testy na syntetycznych zapisach i strumieniach (bez prawdziwych sesji w repo):
- strumień Claude (`assistant` z `usage`, linia `result`) i strumień `codex exec --json`;
- zapis Claude z katalogiem `subagents/` i `.meta.json`: sumy obejmują podagentów, zdarzenia podagenta mają
  `agent_id` i `tool_use_id` rodzica;
- deduplikacja: kilka rekordów jednego wywołania (liczone raz, z kompletnego), replika sidechaina, ta sama
  odpowiedź w dwóch sesjach, rekord `<synthetic>` pominięty, sesja ze strumienia i z zapisu liczona raz;
- `cost-state` i `result` → `session_total`; `compact_boundary` → `compaction` z liczbami;
- Codex: `cached_input_tokens ≤ input_tokens`, wejście znormalizowane;
- nieznane rekordy policzone, uszkodzona linia, pusta sesja, `is_error = None`;
- **test kanarków**: zapis z „kanarkami” w treści plików (`Write`), promptach podagentów, wynikach narzędzi
  i w komendzie Bash z tokenem; żaden kanarek z treści nie pojawia się w wyjściu A1, K0 ani A3, a token z komendy
  wychodzi jako `[REDACTED]`;
- ręcznie, na zainstalowanej wersji CLI, bez commitowania danych: Σ `usage` z zapisu zgadza się z `cost-state`
  dla głównego modelu w sesji bez kompakcji. Rozjazd oznacza zmianę formatu [R A0.15].

## 7. A1: rejestr prób i relacja z przebiegu (`house evidence`)

**Problem.** Raport nocy zawiera wynik bramki i komunikat, ale nie mówi, co worker robił: czy uruchomił testy,
na czym utknął, ile razy powtarzał tę samą komendę. Druga próba zaczyna prawie od zera.

**Zasada.** Według Agno (`company/evidence.py`): relacja jest wyliczana na żądanie, nic nie jest wnioskowane.
**Bramka `house check` pozostaje jedynym źródłem wyniku** i przy braku danych zamyka się (fail closed). Relacja
tylko wyjaśnia [R A1.3].

**Zmiana.**
- **Trwały rejestr prób** `.house/attempts.jsonl` (polityka gita w §3). Wiersz dopisuje jedna wspólna funkcja
  wołana z **bramki**: `night._finish` (`houselib/night.py:613-657`) i `house merge` (scalenie i odmowa), bo
  odmowa `house merge` w sesji interaktywnej to też próba **[decyzja Claude]** [Rec W4]. Wiersz zawiera:
  - zadanie i numer próby `N`, liczony dla zadania przez wszystkie noce i sesje;
  - źródło (`night` | `merge`), agent i narzędzie;
  - ID sesji (z `night`; puste dla `house merge`) i ścieżkę logu strumienia względną do korzenia konkursu
    (bez ścieżek bezwzględnych, które zdradzają katalog domowy);
  - start i koniec, commit bazowy i końcowy;
  - wynik bramki i komunikat (przycięty, po `redact`);
  - `report`: względna ścieżka kopii raportu workera z tej próby (`reports/T###-attempt-N.md`) albo puste, gdy
    raportu nie było (A2b).

  Rejestr nie zawiera treści sesji. Worker go nie rusza: `attempts.jsonl` dochodzi do ścieżek chronionych w guard.
- **Jeden numer próby** [Rec N2]: raport nocy, nazwa logu (`night.py:510`) i kopia raportu używają `N` z rejestru.
  `MAX_ATTEMPTS` dalej liczy próby w obrębie jednej nocy (wewnętrznie), więc zachowanie `blocked` się nie zmienia.
  Zadanie zablokowane po 3 próbach wraca do `night` dopiero po ręcznym odblokowaniu, więc próby między nocami to
  głównie timeouty i przerwania.
- **Fakty najpierw ze stanu, potem z zapisu** [Rec S1]. Ze stanu, bez parsera zapisów:
  - czy worker pisał Q-###: różnica `QUESTIONS.md` między startem a końcem próby;
  - zmienione pliki i commit: `git diff --stat` próby;
  - ostatni wynik `house check` z `.house/checks/T###.json`;
  - naruszenia: `guard.diff_violations` na gałęzi.

  Z zapisu (A0, po widoku prywatności):
  - **check po ostatniej zmianie**: czy `house check` padł po ostatnim udanym `Edit`/`Write` (albo ostatniej
    zmianie w `git diff`), tak/nie i wynik. Ten sam fakt zasila status „sprawdzona” w sekcji Zrobione (A2) [R A1.1];
  - **twierdzenie bez pokrycia**: gdy raport workera („Result vs acceptance”) twierdzi, że testy lub check
    przeszły, a w zapisie nie ma GREEN po ostatniej zmianie, relacja wypisuje linię „twierdzenie bez pokrycia
    w zapisie”. Bez sędziego LLM [R A1.2];
  - **udane edycje plików testowych i konfiguracji sprawdzeń** (`tests/`, `*_test.*`, `test_*.*`, pliki z
    `protected` zadania), obok odmów guarda [R A1.4];
  - odmowy guarda w trakcie: tylko Claude Code. Codex nie ma hooków (`houselib/guard.py:1-5`), więc dla niego pole
    ma wartość „nie dotyczy”, nie „0”;
  - powtórzenia: ta sama komenda ≥3 razy z błędem;
  - **powtórzenie ślepej uliczki**: przy `N ≥ 2` komendy z błędem, które identycznie padły w jednej z poprzednich
    prób zadania (logi z rejestru). Metryka dla K4 [R A1.5];
  - `is_error = None` nie liczy się jako sukces.

  Relacja zbudowana w tej kolejności działa częściowo nawet wtedy, gdy format zapisu się zmieni.
- Pełna relacja wypisuje też komendy i inne wywołania w kolejności (maks. 20 pozycji, „i jeszcze K”), z błędów
  ostatnie ~5 linii komunikatu, czas, model i liczbę wywołań.
- Wykrywanie `house check` w zapisie: segmenty komendy przez `guard._segments`, nazwa pliku `house` i podkomenda
  `check`; wynik z kodu wyjścia (0 = GREEN, `houselib/cli.py:162`), nie z tekstu [Rec N6].
- `house evidence <konkurs> T### [--attempt N]` liczy relację próby z rejestru. `house evidence <konkurs>
  --session <id>` liczy relację sesji interaktywnej i pisze **tylko na stdout**, bo taka sesja zawiera rozmowę
  z człowiekiem **[decyzja Claude]**. Relacja nie jest nigdzie zapisywana (§3).
- Gdzie trafia:
  - `house night`: każda próba w raporcie nocy dostaje **krótką formę** (≤5 linii: check po ostatniej zmianie
    i wynik, twierdzenia bez pokrycia, odmowy guarda, powtórzenia, edycje testów) i komendę `house evidence
    <konkurs> T### --attempt N` **[decyzja Claude]**;
  - brief kolejnej próby: A2b;
  - `/house-retro`: część „co agent robił” bierze z `house evidence`. Treść rozmów (poprawki człowieka, pytania)
    czyta jak dziś, bezpośrednio z zapisów; granica prywatności się nie przesuwa;
  - judge (`/house-judge`) może z niej korzystać.
- **Nie** trafia do `house review`. Recenzja jest ślepa z założenia (cel, kryteria, diff) i tak zostaje.

**Akceptacja.**
- Testy na syntetycznych logach: wiersz rejestru z `night._finish` i z `house merge` (scalenie i odmowa); numer
  próby ciągły przez dwie noce; ścieżki w wierszu względne.
- Relacja próby, która nie uruchomiła `house check` po ostatniej edycji, mówi to wprost.
- Raport z „Result vs acceptance: pass” bez GREEN w zapisie daje linię „twierdzenie bez pokrycia w zapisie”.
- Próba Codexa ma „odmowy guarda: nie dotyczy”.
- Raport nocy ze sztucznego konkursu (istniejące testy `night`) zawiera krótką formę i komendę `house evidence`.
- Test kanarków z A0 przechodzi dla wyjścia `house evidence` i raportu nocy.

## 8. A2: przekazanie w stałym formacie

**Problem.** Wiedza z próby ginie: brief kolejnej próby to 600 znaków komunikatu porażki, a worker Codexa nie
dostaje nawet tego (`night.py:283-284` nie przekazuje `previous`). Kompakcja Claude Code w długiej sesji
coordinatora streszcza po swojemu i gubi identyfikatory, ścieżki i ustalenia człowieka.

### Szablon (wspólny dla A2a i A2b)

Pochodzi z promptu kompakcji Agno, poprawiony według paperu NVIDIA „You don't need long context” (K1, uzasadnienie
w short-context §6). Przekazanie to **brief do dalszej pracy, nie streszczenie historii**. Sekcje idą w kolejności
ważności. Każda sekcja jest wypełniona albo jawnie oznaczona „brak” [R A2.5]. W nawiasie nagłówek angielski, którego
używają raporty i instrukcje (raporty są po angielsku z D21) **[decyzja Claude]**:

1. **Cel** (`Goal`): po co jest zadanie lub sesja.
2. **W toku / następne kroki** (`Next`). Kończy się dwiema wymaganymi liniami:
   - *następna komenda lub edycja*, konkretna, do wykonania od razu;
   - *warunek ukończenia*. W zadaniu wpisuje go kod (patrz A2b).
3. **Krytyczny kontekst, dosłownie** (`Critical context`: ścieżki, identyfikatory T###/Q-###/E-###, komunikaty
   błędów, liczby; nigdy parafrazowany), w dwóch częściach:
   - **Zaobserwowane** (`Observed`): każdy wpis ze źródłem, czyli komendą albo ścieżką i linią;
   - **Niesprawdzone** (`Unverified`): przypuszczenia i hipotezy. Wpis przechodzi do „Zaobserwowane” dopiero po
     sprawdzeniu.
4. **Ograniczenia i ustalenia człowieka** (`Constraints and human decisions`), które nadal obowiązują.
5. **Ślepe uliczki** (`Dead ends`): porzucone podejścia, każde z powodem i liczbą prób, z adnotacją „nie
   powtarzać”. Błąd mógł zostać naprawiony w ramach dobrego podejścia; ślepa uliczka to całe złe podejście.
6. **Decyzje i fakty** (`Decisions and facts`), z odnośnikami D-###.
7. **Błędy i poprawki** (`Errors and fixes`), rozwiązane i otwarte.
8. **Zrobione** (`Done`): każdy zmieniony plik z informacją, czy zmiana jest **sprawdzona**, czyli czy po niej
   przeszło uruchomione sprawdzenie.

Na końcu wskaźnik do pełnego zapisu: identyfikator sesji, `transcript_path` (hook `PreCompact` go dostaje) albo
log próby, i komenda `house evidence …` [R A2.6]. To jest pomysł „archiwum” z Agno: przekazanie jest indeksem.

**Reguły instrukcji przekazania** (tekst generują `house adapt` i `night.brief`):
- „Przerwij pracę. Napisz tylko przekazanie, nie uruchamiaj komend ani narzędzi.”
- Powiedz wprost, co dostanie czytelnik: rozmowa zresetuje się do instrukcji, zadania i tego przekazania.
- Wymień, co przetrwa reset: repo, `.house/`, plik zadania, raporty, logi `house out`. Na trwałe rzeczy wskazuj
  ścieżką, nie przepisuj ich.
- Ucięty wynik: zamiast go przepisywać, podaj wąską komendę do odzyskania (`house out show <id> --grep …`,
  `sed -n A,Bp <plik>`).
- „Nie wymyślaj obserwacji. Nie przepisuj zadania.” Zadanie zawsze wstawia kod.
- **Długość**: instrukcja każe pisać jak najkrócej, z celem ~2–3K tokenów (punkty odniesienia Anthropic:
  streszczenia podagentów 1–2K, przykładowy `/compact` ~1,7K) [R A2.7]. Sufity (ustalenie Wojtka z 2026-11-16):
  - brief kolejnej próby w `night`: **8 000 znaków** (~2K tokenów), liczone w znakach, bez
    `context.estimate_tokens`. Ten sufit **egzekwuje kod**: przycina od końca, Cel i następny krok zawsze
    przetrwają [Rec §8 pkt 13];
  - kompakcja sesji interaktywnej (coordinator): **~8K tokenów** jako **wskazówka w instrukcji**. Streszczenie
    pisze Claude Code, więc `house` go nie przycina; długość mierzy po fakcie licznik `PostCompact` (A2a)
    [R A2.1] [Rec S4].

### A2a: kompakcja w sesjach konkursu

Mechanizm według dokumentacji Claude Code (sprawdzone 2026-11-16 i w researchu 2026-11-17):
- **Instrukcje**: hook `PreCompact` rejestrowany przez `house adapt`. Jego stdout (≤10 000 znaków; dłuższy Claude
  Code ucina) Claude Code bierze jako instrukcje streszczenia przy kompakcji ręcznej, automatycznej i przy
  bezczynności. Wypisuje szablon i reguły powyżej, z `transcript_path` ze zdarzenia. Sekcja „# Compact
  instructions” w `CLAUDE.md` **jest** udokumentowanym mechanizmem, ale nie wiadomo, jak łączy się ze stdout
  `PreCompact`; zostajemy przy hooku i nie wpisujemy obu, dopóki próba tego nie pokaże [R A2.8].
- **Stan po kompakcji: osobny tryb `SessionStart`** [Rec S3]. `hook.session_start` rozgałęzia się po polu
  `source` zdarzenia.
  - `source == "compact"`: bez odbioru Signala (sesja trwa, timer odbiera co 5 minut), bez linii o briefingu,
    własny budżet **10 000 znaków**, ustalona kolejność przycinania (pierwsze przetrwa najdłużej):
    1. **pełny tekst** obowiązujących ustaleń człowieka: wpisy D-### z `Kto: człowiek`, które nie są zastąpione
       (`Zastępuje:`, A5), oraz odpowiedzi na Q-### z `QUESTIONS.md` [R A2.2];
    2. otwarte Q-###;
    3. statusy zadań w toku z ostatnim wynikiem `house check` z `.house/checks/` (to jest „Zrobione” liczone kodem;
       coordinator nie edytuje kodu, robią to podagenci w worktree) **[decyzja Claude]**;
    4. czerwone sprawdzenia i stan eksperymentów;
    5. nadmiar: ścieżki (`DECISIONS.md`, `QUESTIONS.md`) zamiast treści.
  - każde inne `source`: bez zmian (kontrakt „najwyżej 25 linii”, `hook.py:15`, `hook.py:270`).

  Parser `DECISIONS.md` (bloki `## D-###`, pole `Zastępuje:`) powstaje tutaj jako `houselib/decisions.py`,
  a A5 dokłada do niego komendę. Zdanie z wersji 2026-11-16 o czekaniu na spec grup Signala jest nieaktualne:
  PR #8–#11 są scalone, `hook.py` zawiera już `signal_summary`.
- **Licznik `PostCompact`**: hook dostaje `compact_summary`, nie może go zmienić. Dopisuje do
  `.house/tmp/compactions.jsonl` wiersz bez treści: czas, ID sesji, `trigger`, długość w znakach, które z ośmiu
  sekcji są obecne, które oznaczone „brak” [R A2.1]. Liczenie wpisów „Zaobserwowane” bez źródła jest w §17:
  to heurystyka na tekście modelu, która dawałaby fałszywe alarmy [Rec S4] **[decyzja Claude]**.
- Stałe reguły pracy leżą w głównym `CLAUDE.md`/`AGENTS.md`, bo Claude Code wczytuje je z dysku po kompakcji,
  a reguły z `paths:` są streszczane [R A2.2].
- `house_settings` (`houselib/adapters.py:307-313`) wybiera hooki po nazwie, więc `PreCompact` i `PostCompact` nie
  trafiają do repo `house`.
- **Moment kompakcji**: pakiet K2 w specu short-context (K2a: obniżony próg automatu; K2b: przypomnienia).

**Akceptacja A2a.**
- Test hooka: `pre-compact` wypisuje instrukcje ≤10 000 znaków z ośmioma sekcjami i `transcript_path`.
- Test `session-start` z `source: compact`: najpierw pełny tekst D-### człowieka, wpis zastąpiony pominięty,
  linia z szablonu nagłówka `DECISIONS.md` nie jest wpisem, całość ≤10 000 znaków, brak wywołania Signala.
  Przy `source: startup` wyjście jak dziś.
- Test `post-compact`: wiersz w `.house/tmp/compactions.jsonl` bez treści streszczenia.
- Ręcznie, na zainstalowanej wersji: `PreCompact` odpala przy `/compact`, przy automacie i przy kompakcji
  w bezczynności (był błąd w v2.1.105–2.1.114) [R A2.10]; przekazanie ma sekcje i nie zawiera treści plików,
  które leżą na dysku.

### A2b: brief kolejnej próby w `night` i szablon raportu

- **Brief kolejnej próby** (Claude i Codex). Brief składa się z:
  - części z kodu (A1): Zaobserwowane, Błędy i poprawki, Zrobione ze statusem „sprawdzona” (check po ostatniej
    zmianie);
  - części z raportów workera: Cel, Następne kroki, Ograniczenia, Decyzje z ostatniej próby z raportem.
    Gdy raportu nie ma albo ma stare nagłówki, sekcje z kodu wchodzą zawsze, a w miejscu sekcji z raportu stoi
    „brak raportu z próby N” [Rec §8 pkt 10];
  - **Ślepych uliczek** ze **wszystkich** wcześniejszych prób zadania: z kopii `reports/T###-attempt-N.md`
    wskazanych w rejestrze, każdy wpis z dopiskiem „według raportu workera, próba N” (to deklaracja, nie
    obserwacja). Duplikaty usuwane tylko dosłownie, po normalizacji białych znaków [Rec W2] [Rec §8 pkt 11];
  - warunku ukończenia i ścieżki pliku zadania, które wstawia kod.
- **Trwałe źródło ślepych uliczek** [Rec W2]:
  - `coder.md` (krok 3–5) i instrukcja w `bin/codex-task`: worker dopisuje ślepą uliczkę do raportu **w chwili
    porzucenia podejścia**, nie na końcu (wzorzec pliku postępu). Próba zakończona timeoutem albo zabiciem ma
    wtedy raport;
  - `night` po każdej próbie, także po timeoucie, kopiuje `reports/T###.md` do `reports/T###-attempt-N.md`,
    jeśli raport zmienił się w trakcie próby, i wpisuje ścieżkę do wiersza rejestru.
- **Worker Codexa dostaje brief** [Rec S2]. `bin/codex-task` dostaje `--brief-file <plik>`; `night` zapisuje brief
  do `.house/tmp/night/<data>/T###-<czas>-N.brief.md` i przekazuje ścieżkę. Warunek ukończenia dla Codexa jest
  inny: Codex nie commituje i nie ustawia `status: review` (robi to `night._finish`), więc brzmi „kryteria
  akceptacji przechodzą w katalogu roboczym, raport zapisany”. Dla Claude: `house check <konkurs> T###
  --worktree <ścieżka>` drukuje GREEN, potem `status: review`.
- **Szablon raportu** `core/templates/house/reports/TEMPLATE.md` zostaje jednym szablonem dla wszystkich zadań,
  w tym interaktywnych, czytanym przez judge'a i człowieka. Nagłówki angielskie z listy szablonu; mapowanie
  starych: `What was done` → `Done`, `Tested and did not work` → `Dead ends`, `Next` i `Goal` bez zmian. Dochodzą
  `Critical context` (`Observed` / `Unverified`), `Constraints and human decisions`, `Decisions and facts`,
  `Errors and fixes`. Los dwóch obecnych sekcji **[decyzja Claude]** [Rec S5]:
  - `Result vs acceptance` zostaje: to twierdzenie workera, które A1 zestawia z zapisem;
  - `Evidence` znika; jej miejsce zajmuje linia z komendą `house evidence`, bo dowód z zapisu liczy kod.
- Istniejące konkursy mają stary szablon (`house adapt` go nie odświeża), więc parser sekcji w `night` przyjmuje
  stare i nowe nagłówki.
- **Koniec dnia i przejęcie zadania przez innego agenta** (roadmapa pkt 2) użyje tego samego szablonu. Buduje to
  spec trybu dziennego, nie ten (Wojtek).

**Akceptacja A2b.**
- Test szablonu: osiem sekcji w podanej kolejności, „Observed” i „Unverified” rozdzielone, plus `Result vs
  acceptance`.
- Test briefu: trzecia próba zawiera ślepe uliczki z prób 1 i 2 z dopiskiem numeru próby, także z próby 2
  zakończonej timeoutem (raport dopisany w trakcie), dosłowny komunikat błędu i warunek ukończenia.
- Test braku raportu: brief zawiera sekcje z kodu i „brak raportu z próby N”.
- Test limitu: brief dłuższy niż 8 000 znaków jest przycięty od końca, Cel i następny krok przetrwały.
- Test Codexa: `worker_command` dla `coder-codex` przekazuje `--brief-file`, a brief ma warunek ukończenia Codexa.
- Parser sekcji czyta raport w starym i nowym szablonie.
- Profil `coder.md` i `bin/codex-task` opisują szablon i dopisywanie ślepych uliczek w trakcie pracy.

## 9. A3: evale zachowania agentów

### 9a. `house eval`: reliability

**Problem.** Zmiana w playbooku albo profilu agenta może po cichu zepsuć zachowanie, a wychodzi to dopiero na
konkursie.

**Dlaczego własny runner, a nie `claude plugin eval`** [R A3.1]. Oficjalny runner jest dojrzały (foldery
przypadków, sześć typów graderów, ramię z pluginem i bez), ale nie ładuje `CLAUDE.md` ani `.claude/` projektu,
startuje z pustego katalogu, nie ma graderów w kodzie i nie uruchamia Codexa. Bierzemy od niego słownik pól.

**Zmiana.**
- Przypadki w `evals/cases/<nazwa>.json` w repo `house` **[moja decyzja]**. Pola:
  - `agent` (profil z `core/agents/`) albo `skill`;
  - `model`: domyślnie z `model_tier` profilu (koder: `night.model`, coordinator: opus); przypadek skilla musi go
    podać **[decyzja Claude]** [Rec S7]. Eval na innym modelu niż konkursowy prowadziłby do poprawiania promptów
    pod ten model, czyli do anty-celu;
  - `fixture`: przygotowany sztuczny konkurs (ten sam mechanizm co testy e2e);
  - `prompt`;
  - `expect`: oczekiwane wywołania, semantyka **superset** (inne wywołania są dozwolone). Każde oczekiwanie to
    `tool` + `input_match` (regex na JSON wejścia) z licznikami `min`/`max` (np. `house merge` dokładnie raz);
  - `forbid`: zabronione wywołania w tej samej postaci (np. Bash pasujący do `git merge`, `Edit` w
    `.house/checks/`, `Read` z `allow_large` na `.house/sources/`);
  - opcjonalnie `order: [{before, after}]`, liczone po **pierwszym wystąpieniu** w zapisie [R A3.3];
  - `outcome`: predykaty **stanu końcowego** w stdlib, sprawdzane po przebiegu [R A3.2]: `git` (gałąź, commit
    scalenia, `status --porcelain`), `file_exists`, `file_unchanged` (np. `.house/checks/`), `house_state`
    (status zadania, otwarte Q-###);
  - `kind`: `regression` | `capability`;
  - `max_turns`, `timeout_seconds`; przekroczenie to `fail` (pętlenie), nie `infra_error`;
  - `tags` (np. `smoke`).
- **Izolacja** (warunek bezpieczeństwa) [R A3.4] [Rec W3]. Runner dla każdego przebiegu:
  - tworzy **tymczasowy korzeń `house`**: `git worktree` repo `house` na badanym commicie, z fixture'em w jego
    `contests/` i własnym `contests.yaml`. `cli.contest_dir` (`houselib/cli.py:42-56`) rozwiązuje nazwy tylko pod
    `HOUSE_ROOT/contests/`, więc agent woła `house check <konkurs>` normalnie, a eval bada profil z tego commita,
    nie z katalogu roboczego. Prawdziwe konkursy są poza zasięgiem nazwy;
  - fixture dostaje świeże repo git, a `origin` jest lokalnym bare repo (bez historii z poprzednich prób);
  - ustawia `HOUSE_EVAL=1`, przy którym powiadomienia, odbiór Signala i `inbox` są no-opem dopisującym do logu
    przebiegu, a do tego `HOUSE_NOTIFY_CONFIG`, `HOUSE_SIGNAL_CONFIG` i `HOUSE_DATA_DIR` na ścieżki tymczasowe, jak
    `tests/helpers.py`. Dwa zabezpieczenia, bo hooki kopii konkursu wołają `hook._signal_lines`
    (`hook.py:147-166`) i `notify.ask_new` (`hook.py:80-93`), a przypadek „pytanie zamiast czekania” wysłałby
    sztuczne Q-### do Wojtka;
  - uruchamia Claude z `--settings`: `sandbox.enabled`, `failIfUnavailable: true`, `allowUnsandboxedCommands:
    false`, puste `allowedDomains`, `denyRead` dla katalogu domowego poza potrzebnymi ścieżkami,
    `permissions.deny` dla `git push`; Codex z `--sandbox workspace-write`;
  - zapisuje strumień (A0) i usuwa po przebiegu zapisy i auto-memory, które Claude Code założył dla katalogu
    tymczasowego w `~/.claude/projects/`.
- **Blokady** [Rec W3]. `house night` trzyma `flock` na `night.lock` w katalogu danych przez cały przebieg (dziś
  nie zapisuje żadnej blokady). `house eval run` trzyma `eval.lock` i odmawia startu, gdy `night.lock` jest zajęty
  albo gdy któryś konkurs z `contests.yaml` jest **w toku**, czyli ma ustawione oba `timeline.start` i
  `timeline.end` oraz `start ≤ teraz ≤ end`. To nie jest `inbox.running` (`houselib/inbox.py:90-107`), które
  uznaje pusty `end` za trwający konkurs. `--force` to obchodzi. Limit w komendzie to liczba przebiegów
  (`--max-runs`), nie dolary (Wojtek, 2026-11-16: subskrypcja).
- `house eval run [--tag T | --name N] [--model M] [--repeat K] [--commit C]` odpala przebiegi, czyta strumień
  przez A0, sprawdza `expect`/`forbid`/`order` i `outcome`.
- **Wynik przebiegu**: `pass` | `fail` | `paused` (agent napisał Q-### zamiast działać; jeśli przypadek tego
  oczekuje, mówi to `expect`) | `infra_error` (limit subskrypcji, autoryzacja, brak sandboxa, awaria CLI).
  `infra_error` nie wchodzi do k/n [R A3.5]. Wiersz w `evals/results.jsonl`: data, commit `house`, wersja Claude
  Code lub Codexa, model, przypadek, `case_hash` (skrót pliku przypadku; status agreguje tylko ten sam
  `case_hash`), wynik, brakujące / nadmiarowe / zabronione wywołania, niespełnione predykaty, tury, tokeny, czas.
  Bez treści sesji.
- **Zaliczenie** **[moja decyzja]**, doprecyzowane [R A3.6]: wynik przypadku to `k/n` (domyślnie `--repeat 1` dla
  `smoke`, `--repeat 3` przed zmianą w `core/`). Bramką jest pass^k (wszystkie próby udane, τ-bench), ale
  **blokuje tylko `kind: regression`**; `capability` startuje nisko i nie blokuje nigdy. Przy n = 3 to miara
  zgrubna: zachowanie skuteczne w 90% przechodzi 3/3 tylko w 73% przebiegów, a 3/3 nie wyklucza prawdziwej
  skuteczności ~37%. Wynik 0 < k < n oznacza przypadek `flaky`, a 0/n najpierw podejrzenie wadliwego przypadku;
  „powtarzaj aż przejdzie” nie jest naprawą. Nowy przypadek wchodzi jako `capability`, do `regression` po ≥3
  kolejnych przebiegach 3/3. `house eval status` pokazuje pass^1 i pass^k per przypadek.
- Ocena przez model (`criteria`) jest w §17 (Wojtek, 2026-11-17). Wszystkie przypadki z akceptacji da się
  sprawdzić deterministycznie.

**Najpierw sprawdź** (przed planem A3) [R A3.8]: czy `claude -p` z ustawieniami sandboxa powyżej zachowuje
logowanie subskrypcyjne; czy bubblewrap jest na maszynie, na której chodzi `night`; czy pola `command_execution`,
`exit_code` i `turn.completed.usage` w `codex exec --json` są stabilne (wspólne z „Najpierw sprawdź” A0).

**Akceptacja.**
- Co najmniej 5 przypadków `smoke` (`kind: regression`) dla zasad, które już raz zawiodły albo są krytyczne:
  scalanie tylko przez `house merge`, zamrażanie kryteriów w `/house-plan`, koder kończy na `review`, pytanie
  zamiast czekania w `night`, brak zmian w chronionych plikach. Każdy ma `outcome` (np. commit scalenia na
  `main`, `.house/checks/` nietknięte, status zadania).
- Co najmniej jeden przypadek negatywny: zachowanie, którego agent **nie** powinien mieć w danej sytuacji (np.
  koder nie pisze Q-###, gdy wszystko jest jasne) [R A3.11].
- Przebieg na obecnym `main` jest zielony, a celowo zepsuty profil daje czerwony wynik.
- Przebieg przypadku „pytanie zamiast czekania” nie wysyła nic na Signal: log `HOUSE_EVAL` zawiera próbę
  powiadomienia, a dane `notify` w prawdziwym katalogu danych się nie zmieniły.
- Brak sandboxa daje `infra_error`, nie `fail`. Przekroczenie `timeout_seconds` daje `fail`.
- `house eval run` odmawia startu przy zajętym `night.lock` i przy konkursie w toku; konkurs z pustym
  `timeline.end` nie blokuje.

### 9b. Skill `/house-improve`: później

Przeniesiony za 9a (Wojtek, 2026-11-17). Ma sens, gdy istnieje kilkanaście przypadków i co najmniej jeden cykl
„regresja wykryta → naprawiona”. Opis z 2026-11-16 (symulacje → diagnoza z dowodów → poprawka w odpowiedzialnej
warstwie → weryfikacja → zachowane scenariusze) i wymagania z researchu są w §17.

## 10. A4: pełny wynik w pliku, w kontekście koperta (`house out`), wersja minimalna

**Problem** (przepisany 2026-11-17) [R A4.1]. Claude Code sam zapisuje do pliku wynik **udanej** komendy powyżej
~30 000 znaków i pokazuje ścieżkę oraz podgląd pierwszych 2 000 znaków. Wynik komendy **z błędem** powyżej
~10 000 znaków dostaje tylko głowę i ogon, bez pliku. Czerwone testy i wywrotka treningu, czyli najważniejsze
przypadki, tracą środek, a podgląd udanej komendy pokazuje początek, gdy metryki są na końcu. Codex tnie tak samo.
`house exp run` trzyma już log eksperymentu (`houselib/exp.py:52`, ostatni 1 MB), ale bez czytnika.

**Zmiana** (Wojtek, 2026-11-17: wersja minimalna, wspólny czytnik z logami `house exp`). Według Agno
(`offload/store.py`): zapis bez strat, bez wywołania modelu, odczyt zawsze ograniczony.
- `house out -- <komenda>` uruchamia komendę (z `PYTHONUNBUFFERED=1`) i zapisuje pełny wynik do
  `.house/tmp/out/<id>.log` (ignorowane przez git bez migracji, §3). Konkurs wynika z `HOUSE_DIR` albo
  z katalogu (`guard.contest_root`), także w worktree workera. Ograniczenie rozmiaru pliku: ten sam limit
  i ten sam kod co log `house exp` (ostatni `LOG_CAP`).
- **Koperta** to jedyna strona, jaką agent zwykle przeczyta (agenci prawie nie proszą o drugą stronę wyniku)
  [R A4.3]: kod wyjścia (128+n przy sygnale), bajty, linie, czas, ostatnie ~40 linii i gotowa komenda
  `house out show <id> --grep '<wzorzec>'`. Linie przycięte do ~500 znaków, `\r` zwinięte do ostatniego stanu
  (paski postępu), cała koperta ≤ ~4 000 znaków [R A4.2].
- `house out show <id|E-###> --grep WZORZEC` czyta log `out` albo log eksperymentu tym samym kodem: maks. 20
  trafień z kontekstem, linie ≤500 znaków, całość ≤16 000 znaków, dopisek „są dalsze trafienia”. Wzorzec
  domyślnie dosłowny. Zakres linii czyta się po ścieżce z koperty (`sed -n A,Bp`).
- `house out prune [--days N]` usuwa logi `out` starsze niż N dni (domyślnie 7). `house exp prune` usuwa
  worktree, nie logi (`exp.py:773`), i tak zostaje.
- Profil kodera i `playbooks/optimize.md`: długie komendy, które mogą paść (pełne testy, budowanie, trening poza
  `house exp`), idą przez `house out`.
- Granice: `house` jest zablokowane w zadaniach Signal (`guard.py:582-583`), więc `house out` tam nie działa
  i nie ma działać. `guard.check_bash` patrzy na pierwszy token segmentu, więc `out` dochodzi do rozpoznawanych
  opakowań: guard sprawdza komendę po `--` (IMPL, `guard.py:286-316`).

**Akceptacja.** Testy: koperta dla krótkiego i długiego wyniku, kod wyjścia przekazany dalej, linia 1 MB
przycięta, `\r` zwinięte, `--grep` na logu `out` i na logu `E-###`, plik po przerwaniu (Ctrl-C) zawiera wynik do
tego momentu, `prune` po wieku, `house out -- rm -rf …` zatrzymany przez guard jak samo `rm -rf …`.

## 11. A5: dziennik decyzji przez konkursy (`house decisions`)

**Problem.** `DECISIONS.md` żyje w jednym konkursie. Ustalenie z poprzedniego konkursu (np. „na tym sprzęcie nie
trenujemy dużych modeli”) trzeba pamiętać samemu. Agno trzyma decyzje w jednym dzienniku przeszukiwanym przez
wszystkich agentów.

**Zmiana.**
- Parser z A2a (`houselib/decisions.py`) czyta pola **tylko wewnątrz bloków `## D-###`**. Szablon `DECISIONS.md`
  ma w nagłówku linię `- **Kto:** człowiek | judge | coordinator`, którą naiwny parser odczyta jako wpis [R A5.1].
- `protocols/decisions.md` dostaje opcjonalne pole `Zastępuje: D-###` dla decyzji odwróconej (dziś protokół mówi
  tylko „new entry referencing the old”, czego kod nie odczyta) [R A5.2]. `house decisions` domyślnie ukrywa
  wpisy zastąpione, `--all` pokazuje je z dopiskiem `[zastąpiona przez D-###]`.
- `house decisions [--contest C] [--by human|agent] [--since DATA] [--grep WZORZEC] [--all]` czyta
  `DECISIONS.md` wszystkich konkursów z `contests.yaml` i wypisuje **jedną linię na wpis**: `konkurs D-### data
  kto: tytuł — ścieżka:linia`. Pełna treść na żądanie (`--full`) [R A5.3].
- Autor to pole `Kto`: `human` = „człowiek”; `agent` = „judge” albo „coordinator”, w tym wpisy, które
  `merge._review_gate` dopisuje sam (`houselib/merge.py:158-164`); brak pola = „nieznany”.
- `/house-ideate` i `/house-start` sprawdzają decyzje człowieka z poprzednich konkursów (`--by human`) jako
  ograniczenia do potwierdzenia, nie do przyjęcia z automatu **[moja decyzja]**.
- To jest proste filtrowanie po polach. Wyszukiwanie pełnotekstowe przychodzi z osobnego projektu.

**Akceptacja.** Testy na dwóch sztucznych konkursach: filtry, brak pliku, stare wpisy bez autora, linia szablonu
w nagłówku nie jest wpisem, wpis zastąpiony ukryty i widoczny z `--all`, wpis z `_review_gate` jako `agent`.

## 12. A6: porządek w pamięci (`house memory lint`)

Przeniesiony do §17 (Wojtek, 2026-11-17). Pamięć projektu `house` ma dziś dwa wpisy, problem jest hipotetyczny,
a pamięć na tej maszynie dotyczy głównie projektów służbowych [Rec S11].

## 13. A7: `house upkeep` (jedna komenda i timer)

**Problem.** Część pracy wraca regularnie: przegląd nowych wersji narzędzi i vendorowanych skilli, sprawdzenie
evali po zmianach. Agno ma „routines”: zadanie według harmonogramu, którego wynikiem jest propozycja do przeglądu,
nigdy samodzielna zmiana.

**Zakres** (Wojtek, 2026-11-17; zastępuje odpowiedź 2 z §15 w części o `house routine due`): jedna komenda i jeden
timer systemd. Bez formatu `routines/*.json`, bez `house routine due` i bez własnego stanu zaległych przebiegów.
Format rutyn zaprojektuje spec trybu dziennego, gdy będą co najmniej dwie. Rutyny w konkursie też należą do niego.

**Zmiana.**
- `house upkeep` w repo `house`, wynik to wyłącznie raport w `docs/research/routines/` (ignorowany przez git)
  i opcjonalnie jedna linia na Signal. Komenda nie tworzy gałęzi, nie pushuje i nie zmienia plików repo.
- Treść raportu: część deterministyczna w Pythonie (wersje Claude Code, Codexa i superpowers, `git ls-remote`
  vendorowanych skilli wobec przypiętych wersji, wynik `house eval run --tag smoke`) i krótkie streszczenie
  changelogów przez `claude -p` z narzędziami tylko do odczytu, bez Edit/Write [R A7.4]. Raport podaje czas
  przebiegu i okno „od ostatniego udanego przebiegu”.
- **Timer** instalowany przez `house setup --upkeep` (tylko na żądanie) [R A7.1] [R A7.3]:
  - `OnCalendar=weekly`, `Persistent=true` (jeden przebieg zaległy po wybudzeniu), `RandomizedDelaySec=`;
    wzorca `TIMER_UNIT` z `houselib/setup.py:144-154` nie kopiujemy, bo `Persistent=` działa tylko z
    `OnCalendar=`;
  - usługa z niskim priorytetem: `Nice=19`, `IOSchedulingClass=idle`, `RuntimeMaxSec=`, `MemoryMax=`,
    `KillMode=control-group`;
  - `house setup --upkeep` ostrzega, gdy `loginctl enable-linger` jest wyłączone (timery użytkownika nie działają
    wtedy po wylogowaniu).
- **Bez nakładania**: `house upkeep` bierze `upkeep.lock` przez `flock` bez czekania i odmawia startu, gdy
  zajęty jest `night.lock` albo `eval.lock` albo konkurs jest w toku (definicja z 9a). Odmowa zapisuje do logu
  jedną linię z powodem i kończy się kodem 0.

**Akceptacja.** Testy: odmowa przy każdej z trzech blokad i przy konkursie w toku, raport z częścią
deterministyczną na atrapach komend, wygenerowane jednostki mają `OnCalendar` i `Persistent=true`. Jeden ręczny
przebieg przez `systemctl --user start`.

## 14. Sprawdzone i odrzucone

- **AgentOS, PostgreSQL, UI, API, MCP-serwer**: sprzeczne z zasadą plików i stdlib. Za duży koszt na hackathon.
- **SDK agentów i zespołów Agno, workflowy z CEL**: DAG w `.house/tasks/` z bramką scalania robi to, czego
  potrzebujemy, a sesje Claude Code i Codexa są naszymi agentami.
- **Zatwierdzanie przez człowieka z pauzą i wznowieniem**: mamy `QUESTIONS.md`, poziomy, terminy z domyślną
  odpowiedzią i `blocked`. Trwała pauza runu wymaga serwera, a praca i tak nie czeka na człowieka.
- **Guardraile PII i prompt injection**: `guard` i filtr prywatności w `inbox` pokrywają nasze ścieżki. Otwarty
  temat na później: treść stron z researchu (`researcher`, karty prior-art) jako źródło wstrzyknięć.
- **Pamięć profilu użytkownika**: jeden użytkownik, a pamięć Claude Code już jest.
- **Telemetria Agno**: nie dotyczy, niczego z Agno nie instalujemy.
- **`claude plugin eval` jako runner** (2026-11-17): nie ładuje `CLAUDE.md`/`.claude/`, startuje z pustego katalogu,
  nie ma graderów w kodzie, nie uruchamia Codexa (§9a) [R A3.1].
- **Szukanie zapisu sesji bez człowieka w `~/.claude/projects`** (2026-11-17): log strumienia jest prostszy
  i przeżywa tmpfs Dockera oraz `cleanupPeriodDays` (§6) **[decyzja Claude]**.
- **Zapisywalny `CLAUDE_CONFIG_DIR` w kontenerze zadania Signal** (2026-11-17): większa powierzchnia piaskownicy
  dla liczby, którą daje linia `result` **[decyzja Claude]**.
- **Komenda Bash zredukowana do pierwszego tokenu** (2026-11-17): relacja traci wartość; zostaje komenda przycięta
  i po `redact` **[decyzja Claude]**.
- **Osobna warstwa parsowania „dla wyszukiwania”** (2026-11-17): jej odbiorca nie istnieje; zdefiniuje ją jego
  własny spec **[decyzja Claude]**.
- **Kompakcja kotwiczona** („przenieś sekcje z poprzedniego przekazania bez zmian”, Factory, OpenHands)
  (2026-11-17): streszczenie pisze Claude Code, a `house` nie widzi poprzedniego streszczenia i nie sprawdzi
  wykonania. Ustalenia człowieka przywraca kod (A2a) **[decyzja Claude]**.
- **Chmurowe scheduled tasks Claude Code dla A7** (2026-11-17): świeży klon bez lokalnych plików i evali,
  domyślny push gałęzi `claude/` [R A7.7].

## 15. Pytania do Wojtka

Z recenzji speca (2026-11-16). Odpowiedzi wpisujemy tutaj i w odpowiednich sekcjach.

1. **Budżet evali.** Night zakłada logowanie subskrypcją. Czy `house eval` też jedzie na subskrypcji, czyli tej
   samej puli co konkursy i noce, czy na kluczu API z limitem w dolarach? I czy eval może się uruchomić, kiedy
   trwa noc albo konkurs? _Odpowiedź (Wojtek, 2026-11-16): subskrypcja, ale `house eval` odmawia startu, gdy trwa
   noc albo konkurs._
2. **Ile z A2/A7 teraz, skoro trybu dziennego (roadmapa pkt 2) jeszcze nie ma.** Do tego: czy rutyna tygodniowa
   w `house` może sama tworzyć gałąź, pushować i otwierać PR (repo `house` nie ma hooków guard), czy tylko pisze
   raport? _Odpowiedź (Wojtek, 2026-11-16): tylko fundament. Teraz powstaje szablon przekazania, harmonogram
   `house routine due` i rutyna tygodniowa w `house`, która pisze wyłącznie raport. Rutyny w konkursie
   i przekazanie na koniec dnia przechodzą do speca trybu dziennego._ Zmienione 2026-11-17 (Wojtek): zamiast
   `house routine due` jedna komenda i timer (§13).

### Pytania do Wojtka (2026-11-17)

3. **Noc bazowa (krok 0).** Na którym konkursie i ilu zadaniach? Propozycja: kopia jednego z dwóch
   zarejestrowanych konkursów (np. `grok-bot-wro-2026`) z 3–5 zadaniami `todo`, w tym jednym `coder-codex`, żeby
   „przed” obejmowało też Codexa. Koszt to jedna noc subskrypcji. Od tego zależy, czy krok 0 może ruszyć.
   _Odpowiedź (Wojtek, 2026-11-17): kopia `grok-bot-wro-2026` z 3–5 nowymi zadaniami `todo`, w tym jednym
   `coder-codex`._
4. **Pytania o kompakcję przy bezczynności i o przechowywanie zapisów** są w specu short-context §12 (dotyczą K2a
   i K4).

## 16. Co zostaje dla wykonawcy

- Wynik kroków „Najpierw sprawdź” z A0 i A3, wpisany do planów, zanim powstanie kod.
- Dokładne pola zdarzeń w A0 dla bieżących wersji (po próbkach z Claude Code i Codexa), lista jawnie pomijanych
  rekordów metadanych.
- Wzorce plików testowych w A1 poza podanymi.
- Dokładny tekst instrukcji `PreCompact` i briefu (angielski, w ramach reguł z §8).
- Nazwy plików i układ `evals/`, fixture'y konkursów dla `house eval` (ponowne użycie tych z testów e2e).
- Jak `HOUSE_EVAL=1` wyłącza efekty uboczne w `notify`, `signal` i `inbox` (jedno miejsce, test, że żaden kanał
  nie wysyła).
- Czy `house out` dostaje podpowiedź w hooku (np. przy komendach trwających długo), czy tylko regułę w profilach.
- `merge_settings` (`houselib/adapters.py:345-377`) scala dziś tylko `hooks` i `permissions`; nowe klucze
  (K2a) wymagają rozszerzenia z zasadą „wartość użytkownika wygrywa”.

## 17. Na później

Każda pozycja wraca dopiero wtedy, gdy wskazany warunek jest spełniony; do tego czasu nikt jej nie buduje.

- **A6 `house memory lint`** (Wojtek, 2026-11-17), gdy pamięć projektu `house` urośnie albo pojawi się pierwszy
  zdublowany wpis. Wtedy w wersji wąskiej: tylko indeks projektu `house` (wpis bez pliku, plik bez wpisu, limit
  200 linii / 25 KB, powyżej którego Claude Code nie wczytuje wpisów), raport ze ścieżkami i numerami linii, bez
  treści, bez wykrywania „prawie duplikatów”, bez usuwania [R A6.1] [R A6.2].
- **9b `/house-improve`** (Wojtek, 2026-11-17), po 9a i pierwszym konkursie. Wymagania, gdy wróci: kroki
  z 2026-11-16 (cel → 4–6 scenariuszy → diagnoza z relacji A1 z rozdzieleniem przyczyn, w tym awarii
  infrastruktury → poprawka w odpowiedzialnej warstwie → weryfikacja → przypadki do `evals/cases/`, zmiany
  w `core/` za akceptacją Wojtka); **zbiór wstrzymany** (~1/3 przypadków, którego skill nie czyta w krokach 2–4,
  kandydat wybierany po wyniku na nim) [R A3.7]; snapshot starej wersji i porównanie parami [R A3.15].
- **Sędzia LLM (`criteria`) w `house eval`** (Wojtek, 2026-11-17), po 9a. Wtedy: jedno kryterium na wywołanie,
  werdykt `pass|fail|unknown` z krytyką, większość z 3, tylko po zaliczonych sprawdzeniach deterministycznych,
  bez blokowania do walidacji na ~20–30 ręcznie oznaczonych zapisach (TPR/TNR) [R A3.12].
- **Statystyka evali**: przedział Wilsona, porównanie commitów parami, ramię bazowe `--baseline` (tymczasowy
  korzeń na commicie już to umożliwia), eksport do `claude plugin eval`, cel 20–30 przypadków z prawdziwych
  porażek [R A3.9] [R A3.14] [R A3.19] [R A3.20]; po pierwszych 15–20 przypadkach.
- **Sonda po kompakcji** jako przypadek A3 (pytania o pliki, ostatni błąd, obowiązujące ograniczenia i ślepe
  uliczki, ocena dopasowaniem do faktów z A0/A1) [R A3.18]; razem z K4.
- **Adapter rolloutu Codexa** (`~/.codex/sessions/`, `archived_sessions/`, forki, `.zst`, kolejność źródeł
  `usage`: `token_usage_record` → `last_token_usage` → różnica sum) [R A0.5] [R A0.11] [R A0.13], gdy ktoś zacznie
  pracować w Codexie interaktywnie albo gdy K0 będzie potrzebował profilu kontekstu Codexa.
- **Szablon kompakcji dla Codexa** (`experimental_compact_prompt_file` lub `compact_prompt`) [R A2.9], razem
  z adapterem rolloutu; zadania `bin/codex-task` mieszczą się w ~10 minutach.
- **Lint „Zaobserwowane bez źródła”** w `PostCompact` [R A2.1], gdy licznik pokaże, że sekcje w ogóle są obecne.
- **Telemetria OTel** [R K0.6], tylko gdy kolumna „poza zapisem” w K0 okaże się niewystarczająca. Od Claude Code
  2.1.323 domyślny protokół to `http/protobuf`, więc odbiornik w stdlib wymaga `OTEL_EXPORTER_OTLP_PROTOCOL=
  http/json`.
- **A4 stronicowanie** (`--page`, dzielenie zbyt długiej linii), gdy ktoś użyje `show` poza `--grep`.
- **A5 pola `Odrzucone opcje`, `Wynik`, `Zakres`** [R A5.4] [R A5.5], gdy `/house-retro` zacznie je wypełniać.

## 18. Zmiany 2026-11-17

- Stan: zrewidowany po researchu i recenzji, czeka na ponowną akceptację; nowe oznaczenia decyzji.
- §3: polityka gita dla każdego nowego pliku w `.house/`, bo bramka robi `git add -A .house` [Rec W7].
- §5: A2 podzielone na A2a/A2b, nowa kolejność łączna z nocą bazową jako krokiem 0 (Wojtek) i K2a na początku [Rec W5].
- A0: log strumienia jako źródło główne dla sesji bez człowieka, zapis JSONL tylko dla interaktywnych, bez heurystyki okna czasu [decyzja Claude].
- A0: podagenci (`subagents/`), deduplikacja globalna, `cost-state`/`result` jako `session_total`, normalizacja tokenów Codexa, `compaction` z liczbami [R A0.1–A0.6].
- A0: granica prywatności jako biała lista przy zapisie, komenda Bash po `redact`, test kanarków; bez warstwy dla wyszukiwania [decyzja Claude].
- A0: zadania Signal tylko z logu strumienia, bez montażu `CLAUDE_CONFIG_DIR` [decyzja Claude].
- A1: rejestr zapisywany z bramki (`night` i `house merge`), jeden numer próby, fakty najpierw ze stanu [Rec W4, S1, N2].
- A1: check po ostatniej zmianie, twierdzenie bez pokrycia, edycje testów, powtórzenie ślepej uliczki, bramka jako jedyne źródło wyniku [R A1.1–A1.5].
- A1: relacja liczona na żądanie, do raportu nocy tylko krótka forma, `--session` tylko na stdout [decyzja Claude].
- A2: nagłówki angielskie, „Evidence” znika, „Result vs acceptance” zostaje jako twierdzenie do sprawdzenia [Rec S5].
- A2: limit 8K w sesji interaktywnej tylko jako wskazówka, cel ~2–3K, sufit briefu `night` w znakach [R A2.1, A2.7].
- A2a: tryb `compact` w `SessionStart` z pełnym tekstem ustaleń człowieka, licznik `PostCompact`, poprawka o „# Compact instructions” [Rec S3, R A2.2, A2.8].
- A2b: trwałe źródło ślepych uliczek (dopisywane w trakcie pracy, kopie raportu per próba), brief dla Codexa przez `--brief-file` [Rec W2, S2].
- A3: 9b i sędzia LLM przeniesione na później (Wojtek); izolacja z tymczasowym korzeniem, `HOUSE_EVAL`, sandbox, blokady i definicja „konkursu w toku” [Rec W3, R A3.4].
- A3: `outcome`, `expect` jako superset z `min`/`max`/`order`, `kind`, `infra_error`, `case_hash`, uczciwy opis n=3, przypadek negatywny [R A3.2–A3.6, A3.11].
- A4: problem przepisany, wersja minimalna w `.house/tmp/out/` ze wspólnym czytnikiem logów `house exp` (Wojtek) [R A4.1, Rec S8].
- A5: parser tylko w blokach `## D-###`, pole `Zastępuje:`, jedna linia na wpis [R A5.1–A5.3].
- A6 przeniesiony do „Na później” (Wojtek) [Rec S11].
- A7: jedna komenda `house upkeep` i timer `OnCalendar` + `Persistent`, `flock`, odmowa przy nocy/konkursie/evalu, niski priorytet (Wojtek) [R A7.1–A7.4, Rec S10].
- §14 i §17: nowe pozycje odrzucone i odłożone, każda z warunkiem powrotu.

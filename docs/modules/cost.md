# Koszt sesji (`house cost`)

`house cost <konkurs> [--since RRRR-MM-DD]`: jedna tabela, jeden wiersz na sesję konkursu, tylko liczby (bez treści
sesji), na standardowe wyjście. Służy do decyzji o skracaniu kontekstu i do porównań przed/po.

**Skąd wiersze** (każde wywołanie modelu liczone raz, także gdy ten sam zapis leży w kilku plikach):
- sesje interaktywne z `~/.claude/projects` (przypisane po `cwd`, także z `.worktrees/` i konkurs pod symlinkiem),
  razem z podagentami: wiersz to drzewo sesji;
- workery `night` z logów w rejestrze prób `.house/attempts.jsonl` (log Claude `stream-json` albo Codex `--json-log`;
  rodzaj wybiera pole `tool` wiersza, a gdy go brak, pierwszy zapis logu). Sesja nocna, którą widać też jako plik
  sesji, to jeden wiersz. Log `stream-json` zastępuje drzewo plików sesji o tym samym id: strumień niesie też zdarzenia
  podagentów (`parent_tool_use_id`, sprawdzone w A0), więc jest kompletny i zużycie podagentów jest liczone z niego;
- zadania Signal (`@claude`) z repo konkursu, odczyt tylko do odczytu, nic nie jest tworzone. Wiersze Signal istnieją
  tylko na hoście, na którym działa agent Signal (jego `jobs.json` leży tam).

**Kolumny** (liczby tokenów w K): `date` (lokalna data najwcześniejszego zdarzenia, liczona po czasie epoki, nie po
tekście), `kind` (`interactive`, `night`, `signal`, `worker?`), `model` (najczęstszy; nazwa wyglądająca na ścieżkę to
`?`), `calls`;
`ctx_first` / `ctx_med` / `ctx_peak`: kontekst wywołania głównego wątku (wejście + odczyt i zapisy cache), pierwszy to
„podłoże” sesji; `input`, `cache_r`, `cache_w5`, `cache_w1h`, `output`; `compact`: liczba kompakcji i największe
`pre_tokens`; `outside`: tokeny z sumy sesji (`cost-state` albo linia `result`), których nie ma w zapisach wywołań
(wywołania boczne, np. Haiku do tytułów); `weighted`: koszt ważony; `note`. Pod tabelą linia `total`, użyte wagi
i stopka.

- Codex raportuje zużycie na turę, nie na wywołanie: wiersz Codex ma `calls` = tury, a kontekst i `outside` jako `—`.
- `outside` sesji wznawianej liczy się tylko od startu ostatniego przebiegu procesu (`cost-state` obejmuje tylko go),
  nigdy nie jest ujemne. Liczy się z wywołań własnych sesji, odduplikowanych w jej obrębie przed globalnym przypisaniem,
  więc fork, który „wygrywa” wspólne wywołanie, nie zawyża `outside` oryginału.
- `kind`: `night`, gdy wiersz rejestru prób wskazuje sesję (także gdy plik logu zniknął); `worker?`, gdy sesja zaczęła
  się w `<konkurs>/.worktrees/`, a rejestr jej nie zna (pomaga porównać stan sprzed A1).
- Wiersze bez zapisów wywołań: `total only` (tylko suma sesji; zapisy do cache rozbite na 5 min / 1 h, gdy `result`
  strumienia podaje ten podział, inaczej liczone jak 5-minutowe), `no data`, `sub-agents only`. Brak sesji:
  `no sessions`.
- Sumy strumienia (K5, C1): wywołania podsumowania kompakcji (i inne wywołania boczne) nie mają zdarzenia
  `assistant`, więc suma po wywołaniach zaniża koszt. Wiersz logu `stream-json` z zapisem `result` bierze pięć
  kubełków (`input`, `cache_r`, `cache_w5`, `cache_w1h`, `output`) z `modelUsage` OSTATNIEGO `result`, zsumowanego po
  modelach (`modelUsage` jest narastające dla całej sesji, sprawdzone na CLI 2.1.327; `result.usage` jest na turę);
  `note` to `totals from result`. Gdy model nie ma w sumie podziału TTL, zapisy do cache dzielą się w proporcji 5 min
  : 1 h jego wywołań (wszystkich wywołań, gdy on sam nie ma; całość jako 5 min, gdy żadne nie pisało do cache),
  z notatką `TTL split from calls`. `calls` i `ctx_*` zostają z wywołań. Strumień bez `result` zostaje przy sumach
  z wywołań (z częściowymi `output_tokens` z początku wiadomości), z notatką `no result totals`. Dotyczy wierszy
  `night`, `signal` i przebiegów `house eval`.
- Ograniczenie: plik sesji interaktywnej nie widzi wywołań podsumowania kompakcji ani innych wywołań bez zapisu
  `assistant` (jego `cost-state` obejmuje tylko ostatni przebieg procesu), więc kubełki wiersza `interactive` to
  sumy z wywołań i mogą zaniżać koszt sesji z kompakcją; różnicę z sumą ostatniego przebiegu pokazuje `outside`.
- `--since` odcina sesje, które zaczęły się przed datą; sesja bez znanej daty zostaje.

**Wagi** (`cost.weights` w `.house/spec.yaml`, wszystkie opcjonalne; nieznane klucze są pomijane, zła wartość daje
domyślną i jedną linię ostrzeżenia na stderr):

```yaml
cost:
  weights:
    input: 1            # domyślne: względne ceny API
    cache_read: 0.1
    cache_write_5m: 1.25
    cache_write_1h: 2
    output: 5
```

Stopka zawsze brzmi `estimate of API cost (relative prices), not a bill and not subscription usage`: to oszacowanie
kosztu API, nie rachunek i nie zużycie limitu subskrypcji. Kod: `houselib/cost.py`. Spec: K0 w
`docs/specs/2026-11-16-short-context-design.md` §7.

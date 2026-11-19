# K4: zadania nocy „po” (do akceptacji właściciela)

Konkurs `contests/grok-bot-after` (kopia `grok-bot-baseline` @ `a4bbe5d`, gałąź `main`, bez zdalnego `origin`). Cztery nowe
zadania T010–T013 z testami akceptacyjnymi napisanymi z góry i zamrożonymi (`.house/checks/T010..T013.json`).
Konkurs **nie jest** zarejestrowany w `contests.yaml` (zrobi to kontroler po akceptacji). Nic nie uruchamiałem
(`house night`, Claude, Codex); w repo `house` nic nie zacommitowano.

Commity w konkursie: `2c8ee0a` (zadania, testy, wiersze w `docs/CONTRACT.md`), potem cztery automatyczne commity
`house check --freeze` (`dd5c3cc`, `b7eb0e8`, `5c3ddf0`, `91edc93` = HEAD).

## Co zostało sprawdzone (bez rozwiązywania w konkursie)

- Na niezmienionym kodzie testy nowych zadań są czerwone: T010 56/56, T011 16/16, T012 15/15, T013 59/59 nie przechodzi.
  Istniejące 101 testów (pliki z AC-2) przechodzi.
- Wykonalność: rozwiązania referencyjne napisałem **poza konkursem** (kopia w katalogu tymczasowym sesji, nigdy w
  konkursie), żeby sprawdzić, że testy nie są sprzeczne ani niemożliwe: wszystkie 247 testów zielone, 3 uruchomienia pod rząd.
  Mutacje rozwiązania T013 (brak `first_offer`, `turns`, `accepted`, `finalized`, brak zmiany fazy na `failed`) są wykrywane.
- Przy okazji znalazłem fakt, który wpisałem do zadania T011 i CONTRACT: seed `fleet` zawiera dokładnie jeden outlier
  (139 900 za sztukę); seed `car` żadnego.
- Wymagania są w celu zadania albo w `docs/CONTRACT.md` (dopisałem tam wiersze HTTP i sekcje „Deal card”, „Market statistics”,
  „Persistence”). Dzięki temu workery mają **zakaz edycji CONTRACT.md**, więc równoległe zadania nie konfliktują na tym pliku.
- `AC-2` każdego zadania: siedem istniejących plików testowych (nie `tests/` w całości, bo w worktree leżą też czerwone
  testy pozostałych zadań).
- Uwaga: `.venv` w `grok-bot-after` to symlink do `.venv` konkursu bazowego (`scripts/test.sh` szuka go przy głównym checkoucie).
  Nie kasować `grok-bot-baseline/.venv`. `AGENTS.md` i `CLAUDE.md` w kopii wskazują jeszcze ścieżkę `grok-bot-baseline`
  (pliki generowane); po rejestracji warto uruchomić `bin/house adapt grok-bot-after`.

## Zadania

### T010: paginowany endpoint `events.json` (coder, Sonnet), średnie

- **Cel:** `GET /api/sessions/{sid}/events.json?role=&after=&limit=`: zapasowe odpytywanie, gdy SSE jest blokowane. Zwraca
  `{events, next_after, has_more}`, tylko zdarzenia widoczne dla roli, `id > after`, najwyżej `limit` (domyślnie 50, 1..200).
  `next_after` = id ostatniego zwróconego zdarzenia albo `after` przy pustej stronie; `has_more` liczy tylko zdarzenia widoczne dla roli.
- **Kryteria:** AC-1 `tests/test_events_page.py` (56 testów, w tym 40 losowych ziaren: stronicowanie dowolnym `limit`
  odtwarza `Session.events_for(role)`); AC-2 istniejące testy.
- **Dlaczego średnie:** prosty endpoint, ale pułapki na brzegach: ogon niewidocznych zdarzeń nie może dawać `has_more`,
  `after` w „dziurze” prywatnych zdarzeń, 422 dla `limit` 0/201 i `after` < 0, pusta sesja.
- **Oczekiwane próby:** 1 (czasem 2 przez `has_more`).

### T011: statystyki rynku odporne na outliery (coder, Sonnet), średnie

- **Cel:** `market.trim_outliers(comps) -> (kept, dropped)` i użycie w `_build_report` i `lookup`: ceny niepoprawne (nie-skończone,
  <= 0) zawsze odpadają; poniżej 5 użytecznych nic więcej nie odpada; potem płotki Tukeya 1,5·IQR na `market._quantile`
  (ostro, cena dokładnie na płotku zostaje; IQR = 0 nic nie tnie). Statystyki i lista `comparables` z samych zachowanych;
  podsumowanie: `1 outlier excluded.` / `N outliers excluded.`; `MIN_LIVE_RESULTS` liczone od zachowanych.
- **Kryteria:** AC-1 `tests/test_market_outliers.py` (16 testów: brzegi płotków, kolejność, brak mutacji wejścia, scenariusz
  `fleet` w cenach za sztukę, ścieżka live z atrapą); AC-2 istniejące testy (m.in. `test_market.py`).
- **Dlaczego średnie:** jedna funkcja i kilka wskazanych miejsc integracji, ale wiele precyzyjnych reguł; seed `fleet`
  zmienia się (odpada 139 900), a stare testy rynku muszą dalej przechodzić.
- **Oczekiwane próby:** 1–2 (typowa wpadka: nan/inf, ostrość płotka, liczenie `MIN_LIVE_RESULTS` przed filtrem).

### T012: karta umowy `deal-card` (coder-codex), średnie

- **Cel:** `GET /api/sessions/{sid}/deal-card?role=` zwraca `{status, terms, accepted_by, price_per_unit, you}`:
  `status` = `none` / `proposed` / `accepted`; `price_per_unit` tylko gdy `items["units"]` jest liczbą (nie bool, nie napis) > 0;
  `you` = własny wpis `gains_detail` z `deal_accepted`, tylko przy `accepted` i podanej roli, nigdy liczby drugiej strony.
- **Kryteria:** AC-1 `tests/test_deal_card.py` (15 testów, w tym przebieg demo `car`/`fleet` do `deal` bez akceptacji
  ludzi); AC-2 istniejące testy.
- **Dlaczego średnie:** logika jest mała, ale łączy stan silnika (`deal`, `accepted`, `finalized`) ze zdarzeniami i
  prywatnością; pułapki: `True` jako `units`, napis `"4"`, sesja `failed`.
- **Oczekiwane próby:** 1 (ewentualnie 2).

### T013: trwałość sesji po restarcie (coder, Sonnet), **trudne**

- **Cel:** `Session.to_dict()/from_dict()`, `SessionStore.save(path)` (atomowo: plik tymczasowy + `os.replace`, sprzątanie i
  nietknięty stary plik przy każdym błędzie), `SessionStore.load(path)` (nigdy nie rzuca, pomija uszkodzone wpisy, nie nadpisuje
  żywych sesji), `SESSIONS_FILE` w aplikacji (load na starcie, save na zamknięciu). Sesje w locie (`intake`, `market`,
  `negotiating`, `awaiting_human`) wracają jako `failed` z jednym publicznym zdarzeniem o ustalonym tekście; `lobby`/`deal`/`failed` bez zmian.
- **Kryteria:** AC-1 `tests/test_persistence.py` (59 testów: własność na 30 losowych negocjacjach z mock-LLM, wiele błędów
  zapisu, start aplikacji z uszkodzonym plikiem); AC-2 istniejące testy.
- **Dlaczego trudne:** patrz niżej.
- **Oczekiwane próby:** 2–3 (pierwsza próba prawdopodobnie nie przejdzie wszystkich 59; po wskazaniu czerwonych testów
  poprawka jest mała). To jedyne zadanie, które ma szansę dać próbę ≥ 2.

#### Dlaczego T013 zwykle nie przejdzie za pierwszym razem (3 linie)

1. Wierność stanu: test własności po 30 losowych sesjach wymaga, żeby po odtworzeniu `accept_deal` dawał identyczne
   `deal_accepted` i `contract`; to wymaga stanu ukrytego w `Session.__init__` (`first_offer`, `turns`, `accepted`, `last_offer`, `finalized`, zbiory zamiast list), a nie tylko zdarzeń i uczestników.
2. Interakcje: sesja `lobby` po odtworzeniu musi wystartować przy dołączeniu drugiej strony (`task is None`), sesje w locie
   muszą wrócić jako `failed` z dopisanym zdarzeniem (id = poprzednia liczba + 1), a `main.py` musi mieć lifespan odporny na uszkodzony plik.
3. Staranne rozwiązanie: zserializować cały stan przez modele pydantic, `from_dict` jako jedyne miejsce zmiany fazy, `save` serializuje
   wszystko przed otwarciem pliku i sprząta `*.tmp` przy każdym wyjątku, `load` łapie błędy per wpis i omija id już obecne w sklepie.

## Ryzyka pomiaru

- Zadania T010, T012, T013 dotykają `app/main.py` (w różnych miejscach: po `summary`, przed `join`, nagłówek `app = FastAPI`),
  T013 także `app/engine.py`, T011 tylko `app/market.py`. Przy równoległych workerach możliwy drobny konflikt scalania w
  `main.py`; jest to realistyczne, ale gdyby przeszkadzało w pomiarze, kolejność `--max-tasks` można zawęzić.
- T011 zmienia dane rynku `fleet` (odpada jeden outlier); istniejące testy rynku to wytrzymują (zweryfikowane).
- Skala: T010 i T012 to dolna granica „średnich” (jeden endpoint), T011 średnie, T013 trudne; trudność wynika z samego problemu, nie z ukrytych wymagań.

## Jak wystartować noc

Te same ustawienia co w nocy bazowej (z `2026-11-17-baseline.md`):

1. Po akceptacji kontroler dopisuje `grok-bot-after` do `contests.yaml` (i uruchamia `bin/house adapt grok-bot-after`).
2. `night.model: sonnet`, `task_minutes: 30`.
3. Zmienna środowiska `CLAUDE_CODE_SUBAGENT_MODEL=sonnet`.
4. Start: `house night grok-bot-after --hours 3 --max-tasks 6` (do 3 workerów naraz, jak w bazie). Cztery zadania zmieszczą się w limicie 6.
5. Konkurs musi być bez niezapisanych zmian w kodzie (obecnie jest czysty, HEAD `91edc93`).
6. Po nocy: zrzut liczb tym samym jednorazowym skryptem co w bazie (sesje Claude Code po `cwd` konkursu i rollouty Codexa).

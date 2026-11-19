# Ewaluacje agentów (`house eval`)

`house eval` sprawdza, czy profile agentów (`core/agents/`) i skille (`adapters/claude-code/skills/`) nadal
robią to, co powinny, po zmianie promptu, modelu albo kodu `house`. Przypadek (`evals/cases/<nazwa>.json`, format
w [`evals/cases/README.md`](../../evals/cases/README.md)) opisuje scenariusz: fixture konkursu, prompt, wywołania
narzędzi, których oczekujemy i które są zakazane, oraz warunki na stan końcowy. Ocenia kod (log sesji i stan
repo), nie model.

## Izolacja

Sesja agenta nigdy nie dotyka prawdziwych konkursów, danych ani repo właściciela:

- **Tymczasowy root.** Commit pod testem jest eksportowany przez `git archive` do katalogu tymczasowego i
  zamieniany w nowe repo git (bez remote'a, nie worktree prawdziwego repo). W środku jest konkurs z fixture'a i
  lokalny bare `origin`. Liczy się commit, nie working tree: niezacommitowane zmiany nie wchodzą do testu.
- **Claude** działa w kontenerze Docker (`house-agent`): ograniczone uprawnienia, system plików tylko do odczytu,
  sieć tylko przez proxy z listą dozwolonych adresów, kod pod testem zamontowany tylko do odczytu.
- **Codex** działa lokalnie z `--sandbox workspace-write` i wyłączoną siecią sandboxa
  (`sandbox_workspace_write.network_access=false`). Jego środowisko to **lista dozwolonych zmiennych** (`PATH`,
  `LANG`, `LC_*`, `TERM`, `TMPDIR`, potrzebne `HOUSE_*`, tożsamość gita ewaluacji), a nie środowisko hosta: żadnych
  kluczy API ani `GIT_CONFIG_*`. `HOME` to jednorazowy katalog w katalogu tymczasowym, a `CODEX_HOME` to jednorazowy
  katalog zawierający wyłącznie prywatną (0600) kopię `auth.json` z prawdziwego `~/.codex` (samo to wystarcza do
  logowania; `config.toml`, historia i sesje nie są kopiowane). Brak `auth.json` to `infra_error`.
  **Ograniczenie:** `workspace-write` nadal pozwala Codexowi *czytać* resztę dysku, więc przypadki z `tool: codex`
  uruchamiają się tylko z `--allow-codex`; bez flagi są pomijane z linią `skipped <przypadek>: ...` (na stderr), a
  przypadek wskazany przez `--name` kończy się kodem 2.
- Log sesji trafia do katalogu poza montowaniem agenta i jest usuwany zaraz po ocenie.
- Polecenie w kontenerze jest owinięte w `timeout -k 10 <timeout_seconds + 30>`, więc agent kończy się sam, nawet
  gdy host go zgubi. Po przekroczeniu limitu runner usuwa kontener tym samym `docker rm -f` co przy przerwaniu.

Brak Dockera, obrazu, tokenu albo `codex` to `infra_error`, nie porażka agenta. Błąd w samym przypadku albo w
commicie pod testem to natomiast **porażka** (`fail`, notatka `setup: ...`), a nie `infra_error`: niezainstalowany
profil (`profile not installed`), nierozwiązywalny model, fixture, którego nie da się zbudować (błąd tworzenia
katalogu tymczasowego). Pozostałe przebiegi wsadu są kontynuowane.

### Sprzątanie po przerwaniu

- `SIGTERM` i `SIGHUP` podczas `house eval run` działają jak Ctrl-C (`KeyboardInterrupt`): runner zabija grupę
  procesów agenta, usuwa kontener i katalogi tymczasowe. Obsługa sygnałów jest włączona tylko na czas przebiegu.
- Na początku przebiegu, już z `eval.lock`, `run` usuwa to, co zostawiło twarde zabicie (`SIGKILL`, utrata zasilania):
  kontenery `house-eval-*`, katalogi `eval-out/run-*` i katalogi tymczasowe `house-eval-*` starsze niż godzina. Linie
  o usuniętych rzeczach idą na stderr.
- Katalogi usuwane są z `chmod` i ponowną próbą (agent mógł odebrać uprawnienia); gdy coś mimo to zostaje, jest
  linia `warning: could not remove <ścieżka>`.

## Jak uruchomić

```bash
bin/house eval run --name merge-ok            # jeden przypadek
bin/house eval run --tag smoke --repeat 3     # przypadki z tagiem, każdy 3 razy
bin/house eval run --commit abc1234 --model opus --max-runs 5
bin/house eval run --allow-codex              # także przypadki Codexa
bin/house eval run --name long-chain --variant control --variant house --repeat 3 --parallel 3 --cohort k5
bin/house eval status                         # ostatnia wersja i ostatni commit każdego przypadku
bin/house eval status --all                   # wszystkie commity i wersje; też --commit, --model, --all-hashes
```

`run` najpierw bierze blokadę `eval.lock` (dwa przebiegi naraz się nie uruchomią; tego `--force` nie omija), a dopiero
potem, już pod blokadą, sprawdza, czy nie trwa noc (`night.lock`) i czy któryś konkurs nie jest w toku
(`timeline.start` i `timeline.end` ustawione, teraz między nimi); `--force` pomija te dwa sprawdzenia. Kolejność
jest jednostronna: **noc nie sprawdza `eval.lock`** (zgodnie ze specyfikacją, patrz [night.md](night.md)).
Nieznana nazwa przypadku, pusty wybór, agent lub skill, którego nie ma w repo, fixture spoza
`fixtures.BUILDERS` albo przypadek Codexa wskazany bez `--allow-codex` kończą się kodem 2 przed jakimkolwiek
uruchomieniem. `--commit` domyślnie to `HEAD`, rozwiązany raz do sha; **niezacommitowane zmiany w `core`,
`adapters`, `houselib` i `bin` nie są testowane**, o czym `run` ostrzega na stderr. `--max-runs N` kończy po N
uruchomieniach, wliczając `infra_error`.

`--parallel N` (1-4, domyślnie 1 = dotychczasowe działanie) uruchamia do N przebiegów naraz w jednym procesie, który
bierze `eval.lock` raz (wątki; każdy przebieg ma własny `TempRoot`, kontener i out-dir). `--variant` można powtórzyć:
lista przebiegów to każdy wybrany przypadek x każdy wariant x `--repeat`, przeplatana wariant po wariancie (control,
house, control, house, ...), więc przerwana seria zostaje zbilansowana; `--max-runs` tnie tę listę. Wiersze trafiają do
`results.jsonl` pod jednym zamkiem (jedna pełna linia na zapis), a linia wyniku pojawia się, gdy przebieg się skończy
(przy kilku wariantach lub `--parallel` zawiera też `variant=`, `score=` i `weighted=`; podsumowanie i pass^k liczone są
osobno dla każdego wariantu). Wyjątek lub `infra_error` jednego przebiegu nie zatrzymuje pozostałych (przebieg, który
rzucił wyjątek, nie dostaje wiersza, liczy się jak `infra_error`, a kod wyjścia to 1). Ctrl-C lub SIGTERM przerywa
wszystkie trwające przebiegi: każdy przechodzi własną ścieżką sprzątania (kill grupy procesów, `docker rm -f`), nowe się
nie zaczynają, a proces kończy się wyjątkiem `KeyboardInterrupt` (kod 130). Przed startem wątków proces raz tworzy sieć
i proxy Dockera, żeby przebiegi nie ścigały się o `house-agent-proxy`.

Każde uruchomienie to jedna linia: `<przypadek> <i>/<k> <wynik> <czas>s` i dalej to, co zawiodło (`missing`,
`forbidden`, `order`, `failed`) oraz krótka notatka. Na końcu jest podsumowanie na przypadek.

Kod wyjścia `run`:

| Kod | Znaczenie |
|---|---|
| 0 | każdy przypadek `kind: regression` policzono co najmniej `--repeat` razy (bez `infra_error`) i wszystko przeszło |
| 1 | odmowa (noc, konkurs, `eval.lock`) albo któryś przypadek regresyjny nie jest pass^k: policzone uruchomienie nie przeszło |
| 2 | błędny wybór, nieznany agent, skill lub fixture, Codex bez `--allow-codex`, złe liczby |
| 3 | `inconclusive`: żaden przypadek regresyjny nie padł, ale któryś policzono mniej razy niż `--repeat` (uruchomienia `infra_error`, `--max-runs`, przypadek nieuruchomiony), więc nic nie zmierzono |

Kod 1 wygrywa z 3. Przypadek `capability` nigdy nie zmienia kodu wyjścia. Przypadki Codexa pominięte bez
`--allow-codex` nie liczą się do bramki (pominięcie jest wypisane). Każde uruchomienie `infra_error` daje ostrzeżenie
na stderr.

## Materiał wariantów

Dokument, który pokazują tylko niektóre warianty (np. ramię `pasted` wkleja go do promptu, `sources` kładzie do
`.house/sources/`), nie leży w bazowym fixturze: fixture zapisuje go przez `fixtures.write_material` do
`<tmp>/material/<nazwa>`, poza konkursem i jego historią gita, a kontener agenta zasłania ten katalog pustym tmpfs
(jak ukryte testy). Wariant pokazuje go przez `{material:<nazwa>}` w `prompt` albo `copy: [{"material", "to"}]`
(kopia do konkursu w commicie `chore: setup`). Kolejność łatki: `remove`, `copy`, `edit`, `settings`, `env`, prompt.

## Tury i kompletność

Każdy przebieg Claude dostaje prompt przez stdin w formacie `stream-json` (`--input-format stream-json`), nigdy
w argv: prompt rzędu 500 000 znaków przekroczyłby limit długości argumentu (`E2BIG`). Przypadek jednoturowy to taki
sam strumień bez dalszych tur. Kolejną turę (`followups`) runner wysyła dopiero po zdarzeniu `result` poprzedniej
i tylko gdy to był `success`. Wiersz wyników zapisuje `turns_sent` (wysłane tury) i `turns_ok` (wyniki `success`
w logu). Przebieg jest ukończony tylko wtedy, gdy proces się zakończył, a log ma dokładnie jeden `success` na każdą
zaplanowaną turę (`1 + len(followups)`); inaczej to `incomplete` (bez oceny i bez `score`), więc odpowiedź z pierwszej
tury nie może zaliczyć rozmowy, która urwała się w drugiej.

Wariant może podmienić jedną kolejną turę kluczem `followup_text` (`{"index": i, "text": "..."}`, `i` liczone od 0 w `followups`), więc liczba tur każdego ramienia zostaje taka sama (w `long-chain` tura 10 to `/compact` w ramionach `house` i `default-compact`, a w `control` neutralny tekst).

Wywołanie w `expect`/`forbid` może mieć `from_turn` (liczba >= 1): liczą się wtedy tylko wywołania z tej tury
użytkownika lub późniejszych (tura k to wywołania po (k−1)-tym zdarzeniu `result`). Etykiety nie mogą zaczynać się od
`outcome[<cyfry>]`, bo tak wyglądają nazwy generowane dla predykatów.

Ograniczenie: podajnik tur, sprawdzenie kompletności, `final_answer` i koszt czytają stdout CLI. Narzędzia agenta
działają w kontenerze jako ten sam użytkownik co CLI, więc podproces mógłby w zasadzie dopisać do jego stdout
(`/proc/<pid>/fd/1`) fałszywy zapis `result`. To ta sama klasa ograniczeń co dopasowanie wywołań po tekście: agent
nie jest traktowany jak przeciwnik, a dowodem są predykaty stanu końcowego (ukryte testy w osobnym kontenerze, pliki
i git czytane przez hosta). Jeśli to założenie zawiedzie, kosztem jest przebieg ze sfałszowaną odpowiedzią końcową.

## Wyniki i pass^k

Każde uruchomienie dopisuje wiersz do `evals/results.jsonl` (plik poza gitem): data, commit, wersja CLI, model,
przypadek i jego hash, `kind`, wynik, listy `missing` / `forbidden` / `order_violations` / `failed_predicates`,
liczba tur (głównych i sub-agentów), tokeny, czas. Bez treści sesji: ani promptów, ani wejść narzędzi.

Wyniki są liczone osobno dla każdego hasha pliku przypadku, więc każda edycja przypadku zaczyna nową historię.
`house eval status` pokazuje domyślnie tylko najnowszy hash (`--all-hashes` pokazuje starsze), a dla każdego
przypadku:

| Kolumna | Znaczenie |
|---|---|
| `runs` | uruchomienia bez `infra_error` |
| `pass^1` | odsetek uruchomień, które przeszły |
| `pass^k` | czy ostatnie k uruchomień (domyślnie 3, `--k`) przeszło, przy co najmniej k uruchomieniach |
| `streak` | ile ostatnich uruchomień (bez `infra_error`) z rzędu przeszło, dla tego hasha, commitu i modelu |
| `flaky` | wśród ostatnich k są i przejścia, i porażki: przypadek albo agent jest niestabilny |
| `0/n` | żadne z n uruchomień nie przeszło: najpierw podejrzewaj sam przypadek (zbyt ostre warunki, błąd fixture'a) |

`paused` (agent zadał nowe pytanie) liczy się jako brak przejścia.

## Porównanie wariantów (`house eval compare`)

`house eval compare <przypadek> [--since RRRR-MM-DD]` pokazuje po jednym wierszu na wariant przypadku, z wierszy
aktualnego hasha pliku przypadku (starsze wersje przypadku się nie mieszają; `--since` to lokalna data wiersza). Kolumny:
`n` i `pass` (uruchomienia ocenione), `infra` (`infra_error`, `incomplete` i `setup`: liczone tutaj, poza średnimi),
`score` (średnia) z `score_min–max`, `weighted` (średni koszt, w K jak w `house cost`) z `weighted_min–max`, `peak`
(mediana największego kontekstu), `compactions` (średnio na uruchomienie) i `flags`: `control compacted` (wariant o
nazwie `control*` miał kompaktowanie, więc nie jest kontrolą) oraz `n<3` (mniej niż trzy uruchomienia). Pod tabelą jest
liczba przejść każdego predykatu per wariant (`przeszło/uruchomień`), żeby było widać, które fakty lub decyzje
wariant gubi; wiersz zawiera w tym celu `predicates` (`{nazwa: bool}`: etykieta wywołania z `expect`/`forbid` albo
`outcome[i] typ`). Same liczby, bez treści sesji. Nieznany przypadek: kod 2; brak pasujących wierszy: kod 0.

Wiersze różnych commitów, modeli albo wersji CLI (`cli`: wersja z obrazu `house-agent`) nigdy nie są uśredniane
razem: grupa to `(wariant, commit, model, cli)`, a gdy wybrane wiersze mają więcej niż jedną taką trójkę, każda grupa
dostaje flagę `mixed` i nazwę `wariant@<commit7>/<model>/<cli>`. Eksperyment nazywa swoją partię przebiegów:
`house eval run --cohort NAZWA` zapisuje `cohort` w każdym wierszu, a `house eval compare <przypadek> --cohort NAZWA`
bierze tylko ją, więc przebieg próbny ani starsza partia nie wchodzą do średnich.

## Zasada awansu

Nowy przypadek ma `kind: capability`: mierzy, ale niczego nie blokuje. Po trzech kolejnych przebiegach 3/3
(pass^3 przy niezmienionym hashu) dostaje `kind: regression` i od tej pory każda zmiana, która go psuje, kończy
`house eval run` kodem 1. Edycja pliku przypadku zmienia hash, więc historia i licznik 3/3 zaczynają się od nowa.

## `infra_error` nie liczy się

Uruchomienie, które nic nie mówi o agencie (brak Dockera lub tokenu, limit użycia, błąd uwierzytelnienia, sesja bez
żadnego zdarzenia agenta), ma wynik `infra_error`. Trafia do pliku wyników, ale nie wchodzi do `runs`, `pass^1`,
`pass^k` ani do kodu wyjścia, więc awaria środowiska nigdy nie zmienia oceny agenta.

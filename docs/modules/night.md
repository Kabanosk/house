# Tryb nocny

`house night <konkurs> [--hours 8] [--max-tasks N] [--dry-run]`:

1. **Preflight:** czysty katalog konkursu, wszystkie scalone zadania zielone, wolne miejsce na dysku, logowanie
   Claude ważne do końca nocy, hooki z `house adapt`.
2. **Pętla:** każde gotowe zadanie dostaje **świeżą** sesję `claude -p --agent coder` (albo Codex, gdy zadanie ma
   `agent: coder-codex`) w swoim worktree, z limitem `night.task_minutes`; potem `house merge` decyduje.
3. **Wyłączniki:** 3 nieudane próby jednego zadania → `blocked`; 5 z rzędu → koniec nocy; plik `.house/STOP`
   zatrzymuje nowe sesje.
4. **Rano:** raport `reports/night-*.md`, linia w PROGRESS i podsumowanie na Signalu.

`--max-tasks` liczy uruchomione sesje (także powtórki). Noc commituje tylko własne pliki; to, co worker zapisał
w `.house/`, przechodzi przez bramkę `house merge` (ręczna zmiana kryteriów albo statusu `done` jest cofana).

Każda zakończona próba (scalona, nieudana, niestartująca, przerwana) dostaje wiersz w rejestrze `.house/attempts.jsonl`,
commitowany od razu, a numery prób rosną przez kolejne noce i `house merge`. Raport poranny pokazuje pod tabelą dla
każdej próby krótką formę dowodów (`check after last edit` itd.) i polecenie `house evidence <konkurs> T### --attempt N`.

Od drugiej próby zadania (numeracja ciągnie się przez noce, więc także pierwsza próba kolejnej nocy) worker dostaje
zamiast 600 znaków tekstu błędu **brief przekazania**: fakty policzone przez kod (co się zmieniło, co pokazał
ostatni `house check`, końcówki błędów), sekcje z ostatniego raportu samego workera (Goal, Next, ograniczenia,
decyzje), ślepe zaułki ze wszystkich wcześniejszych prób (oznaczone jako twierdzenia workera, do sprawdzenia),
warunek „done when” napisany przez kod (dla narzędzia, które faktycznie startuje) i ścieżkę pliku zadania; do 8000
znaków. Claude dostaje go w prompcie, Codex przez `bin/codex-task --brief-file`. Plik leży w
`.house/tmp/night/<data>/T###-<HHMMSS>-N.brief.md` (poza gitem); gdy jego zbudowanie się nie uda, próba rusza ze
starym tekstem błędu.

**Kolejna próba po konflikcie scalania.** Gdy bramka odrzuca próbę, bo `git merge` do `main` dał konflikt, wiersz
rejestru ma `reason: conflict`, a raport pokazuje `merge-refused (conflict)`. Przed kolejną próbą driver robi
w worktree zadania `git merge --no-ff --no-commit main` (gałąź zadania tylko dostaje `main`, nigdy odwrotnie poza
`house merge`); wiersz tej próby ma `main_merged`: `clean`, `conflict in N files`, `up to date` (main nie wniósł nic
poza `.house/`) albo `in progress` (scalenie z poprzedniej próby trwa dalej). Ścieżki z konfliktem i skrót treści
każdej z nich zaraz po scaleniu trafiają do `.house/tmp/night/T###.merge.json`. Brief wymienia pliki do
rozwiązania (rozwiązać znaczniki tak, by działało to, co wniosły obie strony, oznaczyć przez `git add`, uruchomić
sprawdzenie, nie commitować) albo mówi, że wystarczy ponownie uruchomić sprawdzenie.

Driver commituje scalenie na końcu próby tylko wtedy, gdy każdy plik z konfliktem jest naprawdę rozwiązany. Plik
nadal nierozwiązany w indeksie liczy się jako rozwiązany, gdy jego treść zmieniła się od scalenia (nietknięty plik
binarny nie przechodzi, bo driver po cichu zostawiłby „naszą” wersję). Plik, który worker sam dodał przez `git add`
(także świadome „zostaw naszą wersję”), liczy się jako rozwiązany. W obu przypadkach plik nie może mieć linii
znaczników `<<<<<<<` ani `>>>>>>>` (sama linia `=======` znacznikiem nie jest, bywa podkreśleniem nagłówka) i nie
może być plikiem chronionym, plikiem `.house/` ani plikiem wyglądającym na sekret; dodane linie `git diff --cached
HEAD` też nie mogą mieć znaczników. Linia znacznika liczy się tylko wtedy, gdy nie ma jej w wersji pliku z `main`
(main może celowo zawierać taki przykład). Gdy coś zostaje nierozwiązane albo commit się nie uda (np. hook odrzuci
sekret z `main`), próba kończy się `no-commits` z powodem `conflict`, a scalenie **zostaje w toku** z pracą workera
(nigdy `git merge --abort`); tak samo po timeoucie, przerwaniu czy błędzie (`main_merged: in progress`). Następna
próba kontynuuje to samo scalenie.

Jeśli worker sam zacommitował scalenie ze znacznikami, driver po próbie skanuje dodane linie od rozwidlenia z `main`
oraz drzewo robocze. Przy trafieniu cofa commity próby (`git reset --soft` do głowicy sprzed próby, praca zostaje
w indeksie), przywraca scalenie w toku (`MERGE_HEAD`) i kończy próbę `no-commits` z powodem `conflict`, więc bramka
niczego nie widzi. Hook `pre-commit` w trakcie scalania odrzuca nowe linie znaczników w indeksie oraz plik chroniony
albo `.house/`, którego treść nie pochodzi z żadnej ze stron (ani z HEAD, ani z MERGE_HEAD).

Scalenie, które się nie udaje (`failed: ...`), kończy próbę jako `error` bez workera. Konflikt w pliku chronionym,
w `.house/` albo w pliku wyglądającym na sekret nie trafia do workera: zadanie dostaje `blocked`, a wpis w PROGRESS
wymienia pliki i gałąź, na której człowiek ma go rozwiązać. Zadanie cofnięte po regresji, którego gałąź nie daje się
przenieść na nowe `main`, też dostaje `blocked` dla człowieka (to nie jest konflikt do rozwiązania przez workera).

Uprawnienia sesji: tryb `auto` + lista dozwolonych komend + hooki (bez `--dangerously-skip-permissions`).

**Noc a `house eval`.** Noc bierze tylko `night.lock` i **nie sprawdza `eval.lock`** (zgodnie ze specyfikacją).
Wykluczenie jest jednostronne: `house eval run` najpierw bierze `eval.lock`, a dopiero potem odmawia, gdy trzyma się
`night.lock` albo trwa jakiś konkurs (`--force` pomija te dwa sprawdzenia, ale nie `eval.lock`). Ewaluacja nigdy
nie startuje na działającej nocy; noc uruchomiona w trakcie ewaluacji jest decyzją właściciela (ewaluacja pracuje na
własnych katalogach tymczasowych i niczego z nocą nie współdzieli).

## `house evidence`

`house evidence <konkurs> T### [--attempt N]` wypisuje na stdout **same fakty** o jednej próbie zadania (domyślnie
ostatniej w rejestrze `.house/attempts.jsonl`): nagłówek `T### attempt N · źródło · narzędzie · wynik`, potem m.in.
czy po ostatniej edycji był `house check`, twierdzenie raportu bez pokrycia w logu, edycje plików testowych, odmowy
guarda, powtarzane i ślepe powtórzenia, dodane pytania, ostatni wynik bramki, diffstat, naruszenia, wywołania,
końcówki błędów, model, czas i liczbę wywołań. Bez ocen i domysłów: brakująca wartość to `unknown`.

`house evidence <konkurs> --session <id>` robi to samo dla sesji interaktywnej tego konkursu (id lub jednoznaczny
początek id; sesje znajduje `transcript.contest_sessions`), ale tylko dla faktów, które sesja ma: odmowy guarda,
powtórzenia, wywołania, końcówki błędów, model, liczba wywołań. `T###` i `--session` wykluczają się (błąd użycia,
kod 2); nieznane zadanie, próba albo sesja kończą się kodem 1.

Nic nie jest zapisywane: fakty liczą się na żądanie z rejestru, logu i stanu konkursu, a każdy napis przechodzi
przez `guard.redact`. Treść sesji i wyników narzędzi nie jest drukowana (poza zredagowaną końcówką błędów).

Powiązane: [Sprawdzenia i bramka scalania](checks-and-merge.md), [Ochrona](guard.md), [Signal](signal.md).
Plan: [`docs/plans/2026-11-13-night.md`](../plans/2026-11-13-night.md).

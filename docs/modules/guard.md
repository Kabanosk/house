# Ochrona (guard i hooki)

Pierwszą linią obrony są hooki Claude Code (`bin/house-hook`), a w worktree zadań także hooki git
(`bin/house-githook`, niżej). Twardą granicą jest
`house merge`, który sprawdza diff gałęzi niezależnie od tego, jak plik został zmieniony
(zob. [Sprawdzenia i bramka scalania](checks-and-merge.md)).

Plan: [`guard-hooks`](../superpowers/plans/2026-11-13-guard-hooks.md).
Założenia: [`system-hardening`](../superpowers/specs/2026-11-12-system-hardening-design.md).

## Hooki Claude Code

`house adapt` dopisuje do `.claude/settings.local.json` (nie trafia do gita, wpis w `.git/info/exclude`, więc
drużyny nie dotyczy) hooki `bin/house-hook` i listę dozwolonych komend. Przed każdą edycją i komendą hook blokuje:
zmianę chronionych plików (testy akceptacyjne z `protected`, lokalna sprawdzarka z `spec.scoring.local_scorer`),
pisanie do `.house/checks/`, commit plików `.env*` i plików z kluczami, push na remote spoza
`spec.guard.push_allow` (domyślnie `origin`) i force push na główną gałąź. Na start sesji podaje stan konkursu
(pytania, zadania gotowe do pracy, czerwone sprawdzenia). Codex nie ma hooków, więc te same reguły sprawdza po fakcie
`house guard diff` (sterownik Codexa i `house merge`). Awaryjnie: `HOUSE_GUARD=off claude` (zapis w PROGRESS.md).
Hook to pierwsza linia obrony (łapie typowe komendy: przekierowania, `sed -i`, `rm`, `git -C`, wiele linii),
ale nie każdą sztuczkę (np. `python3 -c "open(...,'w')"`); twardą granicą jest `house merge`, który sprawdza
diff gałęzi niezależnie od tego, jak plik został zmieniony.

## Hooki git

Logika w `houselib/githook.py`, wejście `bin/house-githook`. `house adapt` wpisuje do `.git/hooks/` repo konkursu
dwa małe skrypty (`pre-commit`, `pre-push`). Git uruchamia je przy każdym commicie i pushu, więc reguły działają dla
Claude Code, Codexa i zwykłej powłoki, bez zgadywania, co robi komenda.

Sprawdzają **tylko worktree zadań** (linked worktree konkursu). Główny checkout, w którym commitujesz Ty, i repo
spoza house są pominięte.

- `pre-commit` odrzuca sekrety (ocena po treści z indeksu), pliki `.house/` i zmiany chronionych plików. Dwa ostatnie
  pomija w trakcie merge, rebase i cherry-pick; sekrety sprawdza zawsze.
- `pre-push` odrzuca remote spoza `spec.guard.push_allow` (adres URL zamiast nazwy też), usunięcie głównej gałęzi
  i force push na nią. Force push na gałąź zadania przechodzi.

Błąd samego hooka nie blokuje pracy (jedna linia ostrzeżenia, exit 0). Nie ma przełącznika środowiskowego (także
`HOUSE_GUARD=off` nie działa), bo agent kontroluje środowisko własnych komend. Hook Claude Code blokuje agentowi
`--no-verify`, `git commit -n`, `core.hooksPath` (poza odczytem) oraz zapis do `.git/hooks` i `.git/config`.

Jeśli `core.hooksPath` jest już ustawione (np. husky) albo istnieje cudzy hook, house go nie nadpisuje; `house adapt`
i `house doctor <konkurs>` to zgłaszają. Obejście dla człowieka: commit w głównym checkoucie. Istniejące konkursy
dostają hooki po `house adapt <konkurs>`.

# Sprawdzenia i bramka scalania

Kryteria akceptacji zadania to komendy (`run`, opcjonalnie `expect`, np. `score >= 0.71`); `manual` tylko dla
tego, czego nie sprawdzi komenda. Na końcu planowania są zamrażane (`.house/checks/T###.json`), więc koder ich nie
poluzuje; zmiana tylko z powodem (`--refreeze --reason`, wpis D-###).

Powiązane: [Ochrona (guard)](guard.md), [Recenzja krzyżowa](review.md).
Plan: [`docs/plans/2026-11-13-house-check.md`](../plans/2026-11-13-house-check.md).

## Komendy

| Komenda | Co robi |
|---|---|
| `house check <konkurs> T### [--worktree P]` | uruchamia zamrożone kryteria (najpierw `.house/init.sh`, jeśli jest), GREEN/RED |
| `house check <konkurs> --all` | to samo dla wszystkich zadań `merged`/`done` na głównej gałęzi |
| `house check <konkurs> T### --freeze` / `--refreeze --reason …` | zamraża / zmienia kryteria |
| `house check <konkurs> T### --manual AC-n --evidence …` | judge zapisuje kryterium ręczne |
| `house merge <konkurs> T###` | sprawdza gałąź → scala → sprawdza wszystkie scalone zadania → przy regresji cofa |
| `house guard diff <konkurs> T### [--worktree P]` | czy gałąź zadania zmieniła chronione pliki, `.house/` albo dodała sekrety |

## Statusy zadania

`todo` → `doing` → `review` (tu kończy koder) → `merged` (bramka przeszła) → `done` (także kryteria
ręczne); `blocked`, gdy utknęło albo scalenie zostało cofnięte. `house merge` odmawia, gdy w katalogu konkursu są
niezapisane zmiany w kodzie.

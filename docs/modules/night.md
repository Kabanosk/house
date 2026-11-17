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

Uprawnienia sesji: tryb `auto` + lista dozwolonych komend + hooki (bez `--dangerously-skip-permissions`).

Powiązane: [Sprawdzenia i bramka scalania](checks-and-merge.md), [Ochrona](guard.md), [Signal](signal.md).
Plan: [`docs/superpowers/plans/2026-11-13-night.md`](../superpowers/plans/2026-11-13-night.md).

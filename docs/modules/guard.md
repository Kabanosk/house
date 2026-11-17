# Ochrona (guard i hooki)

Pierwszą linią obrony są hooki Claude Code (`bin/house-hook`). Twardą granicą jest
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

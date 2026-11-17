# Konkurs krok po kroku

Jak przeprowadzić konkurs przez system: od instalacji na maszynie po zgłoszenie. Komendy slash działają w Claude Code
w katalogu konkursu (`contests/<nazwa>`).

Powiązane: [Sprawdzenia i bramka scalania](checks-and-merge.md), [Tryb nocny](night.md),
[Pętla eksperymentów](experiments.md), [Signal](signal.md).

## Komendy w Claude Code

| Komenda | Co robi |
|---|---|
| `/house-start` + wklejony regulamin, zadanie, kryteria | uzupełnia `.house/spec.yaml`, sprawdza regulamin, zadaje pytania o luki |
| `/house-ideate` | pomysły od ideatora, ocena sędziego według kryteriów, wybór (Ty albo sędzia) |
| `/house-plan` | rozpisuje zadania (DAG) w `.house/tasks/` z kryteriami jako komendami i je zamraża |
| `/house-run` | odpala koderów w osobnych worktree, scala przez `house merge` (bramka) |
| `/house-judge` | ocena według rubryki/metryki z dowodami |
| `/house-ship` | deploy, pitch, slajdy, README zgłoszenia, czysty eksport |
| `/house-submit` | zgłoszenie w konkursie optymalizacyjnym |
| „jak tam” / `/house-status` | briefing: co się działo, potem pytania po kolei |
| `/house-retro` | po konkursie: co poprawić w samym systemie (raport z propozycjami, zmiany dopiero po Twojej akceptacji) |

## Raz na maszynę

```bash
git clone git@github.com:Kabanosk/house.git ~/house && cd ~/house
git config user.name "Wojciech Fiołka"; git config user.email "56364007+Kabanosk@users.noreply.github.com"
bin/house setup       # skille użytkownika (grill-me, retro, …) + remotion-best-practices do ~/.claude/skills, sprawdzenie narzędzi, co doinstalować
echo 'export PATH="$HOME/house/bin:$PATH"' >> ~/.bashrc   # potem `house …` z dowolnego miejsca
house sync            # odtwórz konkursy zapisane w contests.yaml
house adapt <nazwa>   # ścieżki i agenci pod ten komputer
```
Tożsamość git ustawiona w repo systemu jest kopiowana do każdego nowego konkursu. Skille użytkownika żyją w
`user-skills/`; `house setup` instaluje je ponownie po zmianach (lokalnie zmienioną kopię odkłada jako `*.bak-*`).

## Konkurs, który już trwa (istniejące repo)

`house adopt <nazwa> <ścieżka-do-repo>` podpina je w miejscu (odnośnik w `contests/`, bez drugiej kopii) i dodaje
`.house/`, niczego nie commitując. Wybierz gałąź, potem `house adapt <nazwa>` i `/house-start` z treścią zadania,
a do `ideas.md` niech trafi wszystko, co już sprawdzono.
Uwaga: `AGENTS.md`/`CLAUDE.md` na wspólnej gałęzi czytają też narzędzia reszty drużyny.

## Przed startem konkursu

1. `house new <nazwa>` (albo `--repo <url>`, jeśli repo konkursu już istnieje), potem `house adapt <nazwa>`.
2. `cd contests/<nazwa> && claude`
3. `/house-start` i wklej wszystko, co masz: treść zadania, link/tekst regulaminu, kryteria oceny, harmonogram.
   Dopisz tryb (`copilot`/`autopilot`) i limity, jeśli chcesz inne niż domyślne (3 agentów, 5 pomysłów).
4. Odpowiedz na pytania z `QUESTIONS.md` (przez „jak tam”). Dopóki regulamin jest „UNCLEAR”, dopytaj organizatorów.

## W trakcie: hackathon budowlany

1. `/house-ideate` → pomysły, do każdego karta „co już istnieje” (katalog YC, YC Requests for Startups,
   zwycięzcy hackathonów, Product Hunt), ocena sędziego na podstawie kart → wybierasz Ty (`copilot`) albo sędzia
   (`autopilot`). Bez karty pomysł nie może zostać wybrany.
2. `/house-plan` → zadania w `.house/tasks/`. Przejrzyj je, zwłaszcza kryteria akceptacji (komendy), zanim
   zostaną zamrożone; to najtańszy moment na korektę.
3. `/house-run` → koderzy (Claude i Codex) w osobnych worktree w `.worktrees/`, scalanie do `main` na bieżąco
   przez `house merge` (patrz [Sprawdzenia i bramka scalania](checks-and-merge.md)).
4. `/house-judge` przed każdym kamieniem milowym → oceny 1–5 według kryteriów i jedna najtańsza poprawa.
5. `/house-ship` → demo, pitch (`pitch/script.md`, `pitch/slides.html`), README zgłoszenia, czysty eksport.

## W trakcie: konkurs optymalizacyjny

To samo do `/house-run`, potem pętla eksperymentów `house exp` (patrz [Pętla eksperymentów](experiments.md)) →
`/house-judge` → `/house-submit`. Każde zgłoszenie ląduje w `.house/submissions.csv` z wynikiem lokalnym
i z leaderboardu.

## Po powrocie od komputera

Napisz „jak tam”. Najpierw dostajesz briefing (co się zmieniło, wynik, decyzje podjęte bez Ciebie, ryzyka),
potem pytania po jednym. Twoje odpowiedzi trafiają do `DECISIONS.md`.

**Drużyna** czyta `.house/PROGRESS.md` (po polsku) i `.house/reports/` (po angielsku).

## Zgłoszenie publiczne

`house export <nazwa> <katalog>` → sprawdź zawartość → wypchnij **eksport** na publiczne repo. Samego repo
konkursu nigdy nie upubliczniaj.

## Sprawdzenie pomysłu w katalogu YC

Też ręcznie: `house yc AI agents that negotiate prices` → najbliższe firmy YC z edycją, statusem i linkiem.
Dane: codzienny zrzut katalogu YC ([yc-oss](https://github.com/yc-oss/api)), cache w `~/.cache/house/`, działa
offline na starym zrzucie.

## Tryby

Tryby (`mode` w `spec.yaml`): `copilot` pyta o wybór pomysłu, zmianę kierunku i duże koszty;
`autopilot` decyduje sam i loguje decyzje. W końcówce konkursu oba pytają przed zgłoszeniem.

## Stan konkursu (`contests/<nazwa>/.house/`)

| Plik | Dla kogo | Po co |
|---|---|---|
| `spec.yaml` | wszyscy | centralna specyfikacja: wynik, kryteria, harmonogram, limity, tryb, autonomia |
| `QUESTIONS.md` | Ty | pytania o trudne decyzje, ze statusem |
| `DECISIONS.md` | wszyscy | dlaczego coś zostało zdecydowane |
| `PROGRESS.md` | drużyna | krótki ślad postępu (PL) |
| `tasks/`, `reports/` | agenci, drużyna | zadania (DAG) i raporty (EN) |
| `ideas.md`, `submissions.csv` | agenci | archiwum pomysłów, zgłoszenia lokalnie vs leaderboard |
| `experiments.csv` | `house exp` | drzewo eksperymentów: rodzic, commit, budżet, wynik, keep/revert |

## Historia projektowa

Plan: [`docs/superpowers/plans/2026-11-05-house-core.md`](../superpowers/plans/2026-11-05-house-core.md). Założenia: [`docs/SPEC.md`](../SPEC.md).

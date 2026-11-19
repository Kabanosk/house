# AI Software House

Osobisty zestaw do hackathonów: budowlanych (MVP + demo + pitch) i optymalizacyjnych (wynik, leaderboard).
Na podstawie specyfikacji konkursu system dobiera tryb pracy i agentów, deleguje zadania, sprawdza wynik
według kryteriów konkursu i zostawia ślad dla drużyny.

Założenia i decyzje: [`docs/SPEC.md`](docs/SPEC.md). Tło naukowe: [`docs/research/`](docs/research/).
Pełny spis dokumentacji: [`docs/README.md`](docs/README.md).

## Co to daje

- **Agenci i playbooki** dla Claude Code (i Codexa): ideator, coordinator, koderzy, sędzia, od pomysłu po pitch.
- **Zamrożone kryteria i bramka scalania:** zadanie wchodzi do `main` dopiero po zielonych sprawdzeniach.
- **Ochrona:** hooki blokują zmianę testów akceptacyjnych, sekrety i niebezpieczne `git push`.
- **Pętla eksperymentów** w konkursach optymalizacyjnych: o `keep`/`revert` decyduje kod, nie agent.
- **Recenzja krzyżowa:** kod Claude'a recenzuje Codex i odwrotnie.
- **Tryb nocny:** kolejka zadań, które agenci robią sami, z raportem rano.
- **Signal:** pytania i odpowiedzi z telefonu oraz agent `@claude` w sandboxie Dockera.

## Wymagania

- Python 3.11+ (tylko biblioteka standardowa), git
- Claude Code (domyślne narzędzie) + plugin `superpowers`
- Opcjonalnie: Codex CLI (druga pula limitów), ollama z małym modelem (streszczenia), Cursor, Grok Bot

## Szybki start

```bash
bin/house doctor                      # co jest na tej maszynie
bin/house new grok-bot-wro-2026       # nowe repo konkursu w contests/ (+ .house/)
bin/house adapt grok-bot-wro-2026     # agenci i skille dla Claude Code, AGENTS.md dla Codex
cd contests/grok-bot-wro-2026 && claude
```

W Claude Code: `/house-start` (regulamin i kryteria) → `/house-ideate` → `/house-plan` → `/house-run` →
`/house-judge` → `/house-ship` (albo `/house-submit` w konkursie optymalizacyjnym). Odpowiedzi na pytania agentów:
napisz „jak tam”. Pełna lista komend i kroków: [Konkurs krok po kroku](docs/modules/contest-workflow.md).

## Moduły

| Moduł | O czym | Dokument |
|---|---|---|
| Konkurs krok po kroku | instalacja, komendy `/house-*`, stan `.house/`, eksport, `house decisions` (decyzje z innych konkursów) | [`contest-workflow.md`](docs/modules/contest-workflow.md) |
| Sprawdzenia i bramka scalania | `house check`, `house merge`, statusy zadań | [`checks-and-merge.md`](docs/modules/checks-and-merge.md) |
| Ochrona | hooki Claude Code, reguły guarda, kompakcja kontekstu | [`guard.md`](docs/modules/guard.md) |
| Recenzja krzyżowa | `house review`, Claude ↔ Codex | [`review.md`](docs/modules/review.md) |
| Pętla eksperymentów | `house exp` w konkursach optymalizacyjnych | [`experiments.md`](docs/modules/experiments.md) |
| Dokumenty konkursu (`house sources`) | regulamin i zadanie bajt w bajt w `.house/sources/`, spis treści z numerami linii, czytanie po zakresie | [`contest-workflow.md`](docs/modules/contest-workflow.md) |
| Długie komendy (`house out`) | pełny wynik komendy do logu, agent widzi krótką kopertę, `show --grep` | [`out.md`](docs/modules/out.md) |
| Tryb nocny | `house night`, wyłączniki, raport | [`night.md`](docs/modules/night.md) |
| Koszt sesji | `house cost`: wiersz na sesję, kontekst, tokeny, kompakcje | [`cost.md`](docs/modules/cost.md) |
| Ewaluacje agentów | `house eval run`, `house eval status`, pass^k, izolacja | [`evals.md`](docs/modules/evals.md) |
| Signal | pytania z telefonu, `house signal` | [`signal.md`](docs/modules/signal.md) |
| Agent z Signala | `@claude`, sandbox, Docker | [`signal-agent.md`](docs/modules/signal-agent.md) |
| Skille zewnętrzne | `vendor/skills/`, licencje | [`vendor-skills.md`](docs/modules/vendor-skills.md) |
| Utrzymanie | `house adapt`, rozwiązywanie problemów, zasady, testy | [`maintenance.md`](docs/modules/maintenance.md) |
| Architektura | układ repo, moduły `houselib/`, planowany refactor | [`architecture.md`](docs/modules/architecture.md) |

## Jak to jest zbudowane

```text
core/            neutralny rdzeń (Markdown/YAML): agenci, playbooki, zasady, szablon .house/
adapters/        nakładki: claude-code (skille), codex (AGENTS.md)
bin/house        CLI
houselib/        kod CLI i logiki
vendor/skills/   skille zewnętrzne
contests/        repozytoria konkursów (każde osobne, poza gitem systemu)
contests.yaml    spis konkursów → `bin/house sync` odtwarza je na nowej maszynie
```

Szczegóły i plan podziału `houselib/` na pakiety: [`architecture.md`](docs/modules/architecture.md).

## Zasady

- Repo konkursu jest zawsze prywatne. Publicznie tylko przez `bin/house export <nazwa> <katalog>`.
- Git: tożsamość ustawiana lokalnie w każdym repo, nigdy globalnie.
- Pozostałe zasady: [`maintenance.md`](docs/modules/maintenance.md#zasady).

## Testy

```bash
python3 -m unittest discover -s tests -v
```

## Projekt i historia

Specyfikacje zmian: [`docs/specs/`](docs/specs/). Plany wykonania: [`docs/plans/`](docs/plans/).

# AI Software House

Osobisty zestaw do hackathonów: budowlanych (MVP + demo + pitch) i optymalizacyjnych (wynik, leaderboard).
Na podstawie specyfikacji konkursu system dobiera tryb pracy i agentów, deleguje zadania, sprawdza wynik
według kryteriów konkursu i zostawia ślad dla drużyny.

Założenia i decyzje: [`docs/SPEC.md`](docs/SPEC.md). Tło naukowe: [`docs/research/`](docs/research/).

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

W Claude Code:

| Komenda | Co robi |
|---|---|
| `/house-start` + wklejony regulamin, zadanie, kryteria | uzupełnia `.house/spec.yaml`, sprawdza regulamin, zadaje pytania o luki |
| `/house-ideate` | pomysły od ideatora, ocena sędziego według kryteriów, wybór (Ty albo sędzia) |
| `/house-plan` | rozpisuje zadania (DAG) w `.house/tasks/` |
| `/house-run` | odpala koderów w osobnych worktree, scala wyniki |
| `/house-judge` | ocena według rubryki/metryki z dowodami |
| `/house-ship` | deploy, pitch, slajdy, README zgłoszenia, czysty eksport |
| `/house-submit` | zgłoszenie w konkursie optymalizacyjnym |
| „jak tam” / `/house-status` | briefing: co się działo, potem pytania po kolei |

## Jak to jest zbudowane

```text
core/            neutralny rdzeń (Markdown/YAML): agenci, playbooki, zasady, szablon .house/
adapters/        nakładki: claude-code (skille), codex (AGENTS.md)
bin/house        CLI: doctor, new, adapt, sync, export
bin/codex-task   uruchamia zadanie przez Codex CLI
contests/        repozytoria konkursów (każde osobne, poza gitem systemu)
contests.yaml    spis konkursów → `bin/house sync` odtwarza je na nowej maszynie
```

Stan konkursu żyje w `contests/<nazwa>/.house/`:

| Plik | Dla kogo | Po co |
|---|---|---|
| `spec.yaml` | wszyscy | centralna specyfikacja: wynik, kryteria, harmonogram, limity, tryb, autonomia |
| `QUESTIONS.md` | Ty | pytania o trudne decyzje, ze statusem |
| `DECISIONS.md` | wszyscy | dlaczego coś zostało zdecydowane |
| `PROGRESS.md` | drużyna | krótki ślad postępu (PL) |
| `tasks/`, `reports/` | agenci, drużyna | zadania (DAG) i raporty (EN) |
| `ideas.md`, `submissions.csv` | agenci | archiwum pomysłów, zgłoszenia lokalnie vs leaderboard |

Tryby (`mode` w `spec.yaml`): `copilot` pyta o wybór pomysłu, zmianę kierunku i duże koszty;
`autopilot` decyduje sam i loguje decyzje. W końcówce konkursu oba pytają przed zgłoszeniem.

## Zasady

- Repo konkursu jest zawsze prywatne. Publicznie tylko przez `bin/house export <nazwa> <katalog>`
  (bez `.house/`, configów agentów i sekretów, ze skanem kluczy API).
- Nazwy konkursów: małe litery i cyfry rozdzielone pojedynczym myślnikiem, np. `techarena-finland-2026`.
- Git: tożsamość ustawiana lokalnie w każdym repo (kopiowana z repo systemu), nigdy globalnie.
- Zmiana narzędzia (np. z Claude Code na inne) = nowa nakładka w `adapters/`, rdzeń zostaje.

## Testy

```bash
python3 -m unittest discover -s tests -v
```

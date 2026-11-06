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
| `/house-retro` | po konkursie: co poprawić w samym systemie (raport z propozycjami, zmiany dopiero po Twojej akceptacji) |

## Jak tego używać na konkursie (krok po kroku)

**Raz na maszynę**
```bash
git clone git@github.com:Kabanosk/house.git ~/house && cd ~/house
git config user.name "Wojciech Fiołka"; git config user.email "56364007+Kabanosk@users.noreply.github.com"
bin/house setup       # skille użytkownika (grill-me, retro, …) do ~/.claude/skills, sprawdzenie narzędzi, co doinstalować
echo 'export PATH="$HOME/house/bin:$PATH"' >> ~/.bashrc   # potem `house …` z dowolnego miejsca
house sync            # odtwórz konkursy zapisane w contests.yaml
house adapt <nazwa>   # ścieżki i agenci pod ten komputer
```
Tożsamość git ustawiona w repo systemu jest kopiowana do każdego nowego konkursu. Skille użytkownika żyją w
`user-skills/`; `house setup` instaluje je ponownie po zmianach (lokalnie zmienioną kopię odkłada jako `*.bak-*`).

**Konkurs, który już trwa (istniejące repo)**: `house adopt <nazwa> <ścieżka-do-repo>` podpina je w miejscu
(odnośnik w `contests/`, bez drugiej kopii) i dodaje `.house/`, niczego nie commitując. Wybierz gałąź, potem
`house adapt <nazwa>` i `/house-start` z treścią zadania, a do `ideas.md` niech trafi wszystko, co już sprawdzono.
Uwaga: `AGENTS.md`/`CLAUDE.md` na wspólnej gałęzi czytają też narzędzia reszty drużyny.

**Przed startem konkursu**
1. `house new <nazwa>` (albo `--repo <url>`, jeśli repo konkursu już istnieje), potem `house adapt <nazwa>`.
2. `cd contests/<nazwa> && claude`
3. `/house-start` i wklej wszystko, co masz: treść zadania, link/tekst regulaminu, kryteria oceny, harmonogram.
   Dopisz tryb (`copilot`/`autopilot`) i limity, jeśli chcesz inne niż domyślne (3 agentów, 5 pomysłów).
4. Odpowiedz na pytania z `QUESTIONS.md` (przez „jak tam”). Dopóki regulamin jest „UNCLEAR”, dopytaj organizatorów.

**W trakcie (hackathon budowlany)**
1. `/house-ideate` → pomysły, do każdego karta „co już istnieje” (katalog YC, YC Requests for Startups,
   zwycięzcy hackathonów, Product Hunt), ocena sędziego na podstawie kart → wybierasz Ty (`copilot`) albo sędzia
   (`autopilot`). Bez karty pomysł nie może zostać wybrany.
2. `/house-plan` → zadania w `.house/tasks/`. Przejrzyj je; to najtańszy moment na korektę.
3. `/house-run` → koderzy (Claude i Codex) w osobnych worktree w `.worktrees/`, scalanie do `main` na bieżąco.
4. `/house-judge` przed każdym kamieniem milowym → oceny 1–5 według kryteriów i jedna najtańsza poprawa.
5. `/house-ship` → demo, pitch (`pitch/script.md`, `pitch/slides.html`), README zgłoszenia, czysty eksport.

**W trakcie (konkurs optymalizacyjny)**: to samo do `/house-run`, potem pętla eksperyment → `/house-judge` →
`/house-submit`. Każde zgłoszenie ląduje w `.house/submissions.csv` z wynikiem lokalnym i z leaderboardu.

**Po powrocie od komputera**: napisz „jak tam”. Najpierw dostajesz briefing (co się zmieniło, wynik, decyzje
podjęte bez Ciebie, ryzyka), potem pytania po jednym. Twoje odpowiedzi trafiają do `DECISIONS.md`.

**Drużyna** czyta `.house/PROGRESS.md` (po polsku) i `.house/reports/` (po angielsku).

**Zgłoszenie publiczne**: `house export <nazwa> <katalog>` → sprawdź zawartość → wypchnij **eksport**
na publiczne repo. Samego repo konkursu nigdy nie upubliczniaj.

**Sprawdzenie pomysłu w katalogu YC** (też ręcznie): `house yc AI agents that negotiate prices` → najbliższe
firmy YC z edycją, statusem i linkiem. Dane: codzienny zrzut katalogu YC ([yc-oss](https://github.com/yc-oss/api)),
cache w `~/.cache/house/`, działa offline na starym zrzucie.

## Aktualizacja systemu

Zmiany w `core/` albo `adapters/` (np. poprawiony playbook) przenosisz do konkursu przez `house adapt <nazwa>`.
Nadpisuje to wygenerowane pliki (`AGENTS.md`, `CLAUDE.md`, `.claude/`, `.house/playbooks/`, `.house/protocols/`),
ale nigdy specyfikacji, decyzji, pytań, zadań ani raportów. Własny `AGENTS.md`/`CLAUDE.md` z repo konkursu
zostaje zachowany jako `*.orig`.

## Gdy coś nie działa

| Objaw | Co zrobić |
|---|---|
| `coder-codex` nie istnieje albo kończy się kodem 3 | `house doctor`: brak Codex CLI → `npm i -g @openai/codex && codex login`, potem `house adapt` |
| Agent pisze „$HOUSE_DIR” zamiast ścieżki | `house adapt <nazwa>` (stare wygenerowane pliki) |
| `house export` zatrzymuje się na „possible secrets” | usuń klucz z pliku (i z historii, jeśli trzeba); `--force` tylko przy fałszywym alarmie |
| Agent wraca do sprawdzonego pomysłu | sprawdź, czy `ideas.md` jest uzupełniane; ideator musi je czytać |
| Briefing nie pokazuje zmian | `.house/state.json` → `last_briefing` ustaw na `null`, żeby zebrać wszystko od początku |

## Skille zewnętrzne (`vendor/skills/`)

Skopiowane bez zmian, z licencją i `ATTRIBUTION.md`; `house adapt` instaluje je w każdym konkursie.

| Skill | Źródło | Kto używa |
|---|---|---|
| `codebase-design` | Matt Pocock (MIT) | coordinator przy planowaniu modułów, judge przy review struktury |
| `frontend-design` | Anthropic (Apache-2.0) | coder przy zadaniach z UI |
| `webapp-testing` | Anthropic (Apache-2.0) | judge: Playwright, zrzuty ekranu, błędy konsoli w demo |
| `no-ai-slop` | Peter Yang (MIT) | pitch: skrypt, slajdy, README bez „AI slop” |

Aktualizacja: sklonuj źródło na nowo i podmień folder (nie edytuj w miejscu).

## Jak to jest zbudowane

```text
core/            neutralny rdzeń (Markdown/YAML): agenci, playbooki, zasady, szablon .house/
adapters/        nakładki: claude-code (skille), codex (AGENTS.md)
bin/house        CLI: setup, doctor, new, adopt, adapt, sync, export, yc
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

# AI Software House: specyfikacja systemu

_Stan: 2026-11-05. Źródło: sesja grill-me (decyzje D1–D26 poniżej) + [raport](research/Raport_systemy_wieloagentowe_AI_2026.md)._

## 1. Cel

Osobiste narzędzie Wojtka na hackathony **budowlane** (MVP + demo + pitch, np. Grok Bot Hackathon) i **optymalizacyjne**
(wynik liczbowy, leaderboard, np. CCC, Huawei TechArena). System działa jak software house: na podstawie
specyfikacji konkursu dobiera tryb pracy, agentów i proces, deleguje pracę, weryfikuje wynik i zostawia ślad dla drużyny.

**Walidacja:** Grok Bot Hackathon Wrocław (5.11.2026, tryb budowlany), potem TechArena Finland 2026
(klasyfikacja stresu/wysiłku z danych ze smartwatchy, tryb optymalizacyjny). Benchmark: powtórki własnych
konkursów (Optical, CCC 2025) **bez dostępu do starych rozwiązań**.

## 2. Zasady architektoniczne

1. **Rdzeń neutralny, nakładki wymienne.** Wszystko, co jest „wiedzą systemu” (profile agentów, playbooki, protokoły,
   szablony), to zwykły Markdown/YAML w `core/`. Nakładka (`adapters/claude-code`, `adapters/codex`) tłumaczy rdzeń na
   format konkretnego narzędzia. Zmiana narzędzia = nowa nakładka, nie nowy system. Domyślne narzędzie: Claude Code.
2. **Gotowe klocki, nie własny orchestrator na API.** Subskrypcje (Claude Max, Codex, Cursor Pro), nie płatne API.
3. **Pliki zamiast czatu.** Agenci komunikują się przez pliki w `.house/`. Surowe dane zawsze zostają w plikach;
   streszczenie nigdy nie jest jedynym źródłem.
4. **Model według roli.** Opus tylko do planu, decyzji i sędziowania; research i streszczenia tanio.
5. **Narzędzia wykrywane, nie zakładane.** `house doctor` sprawdza, co jest na maszynie, i zapisuje to w specyfikacji;
   każdy agent ma wariant zastępczy.
6. **Superpowers jako klocki** w nakładce Claude Code (worktrees, plany, TDD, weryfikacja), dopóki działają.
7. **Jutro to nie cel ostateczny:** fabryka agentów (hiring agent) będzie później, ale profile już teraz są w formacie,
   który taki agent może generować.

## 3. Układ repozytoriów

```text
house/                          # repo systemu (GitHub, prywatne)
├── core/
│   ├── agents/*.md             # neutralne profile agentów (YAML frontmatter + instrukcje)
│   ├── playbooks/*.md          # przebieg pracy: lifecycle, build, optimize
│   ├── protocols/*.md          # pytania, briefing, decyzje, zgłoszenia, stagnacja, eksport
│   └── templates/house/        # szablon katalogu .house/ dla nowego konkursu
├── adapters/
│   ├── claude-code/            # generuje .claude/agents + .claude/skills/house-* w repo konkursu
│   └── codex/                  # AGENTS.md + wrapper `codex exec`
├── bin/house                   # CLI (Python, tylko stdlib)
├── contests.yaml               # spis konkursów: nazwa → URL repo
├── contests/                   # w .gitignore; każdy konkurs to NIEZALEŻNY klon
└── docs/
```

**Repo konkursu** (`contests/<nazwa>/`, osobne git repo, zawsze prywatne):

```text
contests/<nazwa>/
├── .house/                     # stan konkursu, wersjonowany razem z kodem
│   ├── spec.yaml               # specyfikacja konkursu (centralny dokument)
│   ├── DECISIONS.md            # dziennik decyzji (D-001…)
│   ├── QUESTIONS.md            # pytania do człowieka ze statusem
│   ├── PROGRESS.md             # ślad dla drużyny (PL)
│   ├── tasks/T###-slug.md      # zadania = DAG; jeden plik na zadanie (brak konfliktów zapisu)
│   ├── ideas.md                # archiwum pomysłów: wypróbowane / odrzucone / czekające
│   ├── reports/                # raporty z eksperymentów i zadań (EN)
│   ├── submissions.csv         # log zgłoszeń: wynik lokalny vs leaderboard
│   ├── state.json              # techniczny stan (np. czas ostatniego briefingu)
│   └── playbooks/, protocols/  # kopia z core (żeby repo było samowystarczalne)
├── AGENTS.md, CLAUDE.md        # generowane przez nakładki
├── .claude/agents, .claude/skills  # generowane przez nakładkę Claude Code (spis: .house/generated.json)
└── … kod konkursowy …
```

**Zgłoszenie publiczne** powstaje wyłącznie przez `house export`: czysta kopia bez `.house/`, configów agentów
(`.claude/`, `AGENTS.md`, `CLAUDE.md`, `.cursor/`, `.codex/`) i sekretów (`.env*`), z prostym skanem sekretów.

**Worktree a `.house/`:** źródłem prawdy jest `.house/` w głównym checkoucie. Agenci w worktree dostają jego
ścieżkę bezwzględną (`HOUSE_DIR`) i tam zapisują raporty/status zadań.

## 4. Specyfikacja konkursu (`.house/spec.yaml`)

Część pól wypełnia człowiek; brakujące uzupełnia agent-intake (z regulaminu/treści zadania) i oznacza `proposed: true`
do zatwierdzenia. Pola:

| Pole | Znaczenie | Domyślnie |
|---|---|---|
| `contest` | nazwa, URL, organizator, język konkursu | — |
| `kind` | `build` / `optimize` | — |
| `goal` | co jest produktem / co optymalizujemy | — |
| `scoring` | `build`: rubryka (kryteria, skala, opisy poziomów); `optimize`: metryka, komenda lokalnej sprawdzarki, czy jest leaderboard | — |
| `timeline` | start, koniec, kamienie milowe (np. feature freeze) | — |
| `rules` | link + wynik sprawdzenia (`allowed_ai`, `allowed_autonomous_agents`, uwagi) | sprawdza agent przed startem |
| `mode` | `autopilot` / `copilot` | `copilot` |
| `limits` | `max_parallel_agents`, `ideas_per_round`, `stagnation_n` | 3, 5, 3 |
| `submissions` | `auto_submit`, `slots_total`, `explore_share`, `endgame_share` | true, —, 0.25, 0.20 |
| `deploy` | czy wolno samemu wystawić publiczne demo | true |
| `languages` | `team` / `public` | `pl` / język konkursu |
| `tools` | wykryte przez `house doctor`: claude, codex, cursor, ollama(+modele), gpu, grok-bot | auto |
| `team` | ludzie i za co odpowiadają (system zajmuje się tylko tematami właściciela) | — |

## 5. Agenci (`core/agents/`)

| Agent | Model | Wariant zastępczy | Rola |
|---|---|---|---|
| `coordinator` | Opus | — | Główna sesja. Prowadzi lifecycle, planuje DAG, deleguje, pilnuje limitów, pisze briefing |
| `researcher` | Haiku (Sonnet dla głębokich) | — | Regulamin, API, dziedzina, prior art, poprzedni zwycięzcy |
| `ideator` | **inny model niż koder** (Codex, gdy dostępny) | Opus | Pomysły, a przy stagnacji radykalnie inne podejścia |
| `coder` | Sonnet | — | Implementacja zadania w worktree, TDD, raport |
| `coder-codex` | Codex (`codex exec`) | `coder` | Drugi wykonawca z osobną pulą limitów |
| `judge` | Opus | — | Ocena według rubryki/metryki z dowodami; drobne decyzje sam, trudne → pytanie |
| `pitch` | Sonnet | — | Slajdy, skrypt pitchu, README zgłoszenia, wideo-plan |
| `summarizer` | qwen3:8b (ollama) | Haiku | Streszczenia logów/wyników; surowe dane zostają |

Format profilu (neutralny, pod przyszłego hiring agenta):

```yaml
---
id: coder
model_tier: sonnet          # opus | sonnet | haiku | codex | local
fallback: null
description: …              # kiedy delegować
tools: [read, edit, bash, test]
inputs: [task file, spec.yaml]
outputs: [git commits on task branch, report, task status]
acceptance: [task acceptance checks pass, report written]
max_turns: 40
---
<instrukcje po angielsku>
```

## 6. Przebieg pracy (`core/playbooks/`)

**Lifecycle (wspólny):**
1. **Intake:** `house new` → człowiek wkleja treść/regulamin/kryteria → coordinator uzupełnia `spec.yaml`, researcher
   sprawdza regulamin (czy AI/agenci są dozwoleni) → braki jako pytania.
2. **Ideacja:** ideator daje `ideas_per_round` pomysłów → judge ocenia według rubryki → w `copilot` wybór należy do człowieka.
3. **Plan:** coordinator rozpisuje DAG w `.house/tasks/` (id, cel, zależności, wymagane kompetencje, kryteria akceptacji,
   właściciel, branch). Równolegle tylko zadania niezależne, maks. `max_parallel_agents`.
4. **Wykonanie:** workery w osobnych worktree; każde zadanie kończy się raportem i zieloną weryfikacją.
5. **Ocena:** judge według rubryki (build) albo metryki + leaderboard (optimize).
6. **Wydanie:** build → deploy demo, pitch, slajdy, README, `house export`; optimize → polityka zgłoszeń.

**Build (hackathon):** golden path najpierw, integracja wcześnie (nie na koniec), feature freeze z `timeline`,
fallback demo (nagranie/tryb offline), rubryka jako sprawdzarka przed każdym kamieniem milowym.

**Optimize:**
- **Zgodność lokalna vs leaderboard:** każde zgłoszenie loguje oba wyniki w `submissions.csv`; agent raportuje zgodność
  liczbowo. **Zakaz** stwierdzeń typu „testy lokalne nie mają sensu” bez liczb. Słaba zgodność → budowa lepszego
  lokalnego odpowiednika sędziego (generator syntetyczny, sondy).
- **Eksploracja:** `explore_share` slotów na podejścia, które lokalnie wypadają słabo lub są nieprzetestowane.
  Słaby wynik lokalny nie zabija nowego podejścia.
- **Stagnacja:** `stagnation_n` iteracji bez poprawy → ideator (inny model) musi dać radykalnie inne kierunki;
  `ideas.md` blokuje powroty do wypróbowanych pomysłów. W `copilot` zmianę kierunku zatwierdza człowiek.
- **Zgłoszenia:** wysyła sam, gdy ma podstawy sądzić, że poprawi wynik; w ostatnich `endgame_share` czasu pyta przed
  wysłaniem. Sondowanie leaderboardu dozwolone, jeśli regulamin nie zabrania.

## 7. Człowiek w pętli (`core/protocols/`)

- **Pytania:** `QUESTIONS.md`, wpisy `Q-###` ze statusem `open/answered`, kontekstem, opcjami i rekomendacją.
  Trudne decyzje = wybór pomysłu, zmiana kierunku, koszt > ~20% pozostałego czasu/limitu, zgłoszenia w końcówce,
  wszystko nieodwracalne poza tym, co spec pozwala.
- **Briefing** po powrocie człowieka („jak tam”, `/house-status`): **najpierw** co się działo od ostatniego briefingu
  (bez limitu długości, ma oddać skalę), najlepszy wynik i trend, decyzje podjęte samodzielnie, **potem** otwarte pytania
  po kolei. Odpowiedzi → `QUESTIONS.md` (answered) i `DECISIONS.md`.
- **Decyzje:** `DECISIONS.md`, wpisy `D-###`: decyzja, uzasadnienie, kto (człowiek/judge/coordinator), data.

## 8. Języki

Dla Wojtka i drużyny po polsku (`PROGRESS.md`, `QUESTIONS.md`, `DECISIONS.md`, briefing). Po angielsku: instrukcje
agentów, raporty (`reports/`), lista przetestowanych rzeczy, pitch, README zgłoszenia (lub język konkursu).

## 9. Zakres

**Teraz (przed Grok Botem):** rdzeń, nakładka Claude Code, nakładka Codex (z wariantem zastępczym), `house`
CLI (`doctor`, `new`, `adapt`, `sync`, `export`), playbook build kompletny, playbook optimize spisany.

**Po Grok Bocie / przed TechAreną:** `house corr` (zgodność lokal–leaderboard), automaty optimize, poprawki z walidacji.

**Poza zakresem:** fabryka agentów / hiring agent, wspólny bus z agentami drużyny, integracja branchy drużyny,
własny orchestrator na API, kod z `~/studia/hackathony/grok-bot`.

## 10. Decyzje z sesji grill-me

D1 Cel: narzędzie na hackathony budowlane i optymalizacyjne · D2 Zleca właściciel; drużyna ma swoich agentów ·
D3 Człowiek: rzadkie, krytyczne decyzje · D4 Gotowe klocki, nie API (limity subskrypcji) · D5 Rdzeń neutralny +
nakładki · D6 Superpowers jako klocki · D7 Najpierw zestaw, fabryka później · D8 `contests/` jako niezależne klony +
`contests.yaml` (submoduły źle działają z agentami) · D9 Stan w `.house/` · D10 Specyfikacja jako centralny dokument,
luki uzupełnia agent do zatwierdzenia · D11 Limity 3/5 konfigurowalne · D12 Tryby autopilot/copilot · D13 Agenci i modele
jak w §5 · D14 Pliki zamiast czatu · D15 Judge + pytania · D16 Briefing przed pytaniami, bez limitu długości ·
D17 Zgodność lokal–leaderboard mierzona, 25% slotów na eksplorację · D18 Wykrywacz stagnacji (N w spec) ·
D19 Zgłoszenia auto, pytanie w ostatnich 20% · D20 Demo auto, sondowanie OK, regulamin sprawdzany przed startem ·
D21 PROGRESS (PL) + reports (EN) · D22 Języki jak §8 · D23 GPU: trening > ollama · D24 System ogólny, konkurs przez spec ·
D25 Walidacja: Grok Bot → TechArena Finland → powtórki · D26 Git lokalnie: Wojciech Fiołka / noreply Kabanosk ·
D27 Repo konkursu prywatne, publicznie tylko przez `house export`.

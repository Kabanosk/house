# Architektura

## Układ repozytorium

```text
core/            neutralny rdzeń (Markdown/YAML): agenci, playbooki, zasady, szablon .house/
adapters/        nakładki: claude-code (skille), codex (AGENTS.md)
bin/house        CLI: setup, doctor, new, adopt, adapt, sync, export, yc
bin/codex-task   uruchamia zadanie przez Codex CLI
houselib/        kod CLI i logiki (Python 3.11+, tylko biblioteka standardowa)
tests/           testy (unittest)
vendor/skills/   skille zewnętrzne, skopiowane z licencją
contests/        repozytoria konkursów (każde osobne, poza gitem systemu)
contests.yaml    spis konkursów → `bin/house sync` odtwarza je na nowej maszynie
```

Stan jednego konkursu żyje w `contests/<nazwa>/.house/` (tabela plików w
[Konkurs krok po kroku](contest-workflow.md#stan-konkursu-contestsnazwahouse)).

Zmiana narzędzia (np. z Claude Code na inne) to nowa nakładka w `adapters/`, rdzeń zostaje.

## Moduły `houselib/` (stan obecny, płaski katalog)

| Obszar | Moduły | Opis |
|---|---|---|
| Model konkursu | `spec`, `registry`, `questions`, `manifest`, `fm`, `gitutil` | pliki `.house/`, spis konkursów |
| Praca nad konkursem | `checks`, `merge`, `review`, `exp`, `night`, `export` | [bramka](checks-and-merge.md), [recenzja](review.md), [eksperymenty](experiments.md), [noc](night.md) |
| Ochrona | `guard`, `hook` | [guard i hooki](guard.md) |
| Signal | `signal`, `signalstore`, `signalparse`, `signalread`, `signalchats`, `signalcli`, `notify`, `inbox` | [transport, magazyn, pytania](signal.md) |
| Agent z Signala | `signalagent`, `agentdocker`, `agentproxy` | [`@claude` w Dockerze](signal-agent.md) |
| Ta maszyna | `doctor`, `setup`, `adapters`, `scaffold`, `context` | [utrzymanie](maintenance.md) |
| Punkt wejścia | `cli`, `yc` | parser komend, katalog YC |

## Planowany podział na pakiety

Refactor układu pakietów i warstw zależności (`common` → `contest`/`signal` → `guard`/`bridge` → `workflow`/`agent`
→ `toolchain` → `cli`) jest zaplanowany, ale jeszcze niewykonany (start po Collabothonie, 20–22.11). Spec i plan
(`docs/specs/2026-11-17-package-layout-design.md`, `docs/plans/2026-11-17-package-layout.md`) trafią do repo osobno.

Po refaktorze tabela powyżej zastąpi się układem pakietów ze specu (wymaganie R12: dokumentacja podaje nowe
ścieżki).

# Dokumentacja

Główny opis repo: [`../README.md`](../README.md).

## Moduły (jak tego używać)

| Dokument | O czym |
|---|---|
| [`modules/contest-workflow.md`](modules/contest-workflow.md) | konkurs od instalacji do zgłoszenia, komendy `/house-*`, stan `.house/`, `house decisions` |
| [`modules/checks-and-merge.md`](modules/checks-and-merge.md) | zamrożone kryteria, `house check`, `house merge` |
| [`modules/guard.md`](modules/guard.md) | hooki Claude Code, reguły ochrony |
| [`modules/review.md`](modules/review.md) | recenzja krzyżowa Claude ↔ Codex |
| [`modules/experiments.md`](modules/experiments.md) | pętla `house exp` (konkursy optymalizacyjne) |
| [`modules/out.md`](modules/out.md) | `house out`: długie komendy do logu, krótka koperta, `show --grep` |
| [`modules/night.md`](modules/night.md) | tryb nocny |
| [`modules/cost.md`](modules/cost.md) | `house cost`: koszt i kontekst sesji konkursu |
| [`modules/evals.md`](modules/evals.md) | `house eval`: ewaluacje agentów, wyniki, pass^k |
| [`modules/signal.md`](modules/signal.md) | pytania i odpowiedzi z telefonu, `house signal` |
| [`modules/signal-agent.md`](modules/signal-agent.md) | `@claude` z Signala, sandbox i Docker |
| [`modules/vendor-skills.md`](modules/vendor-skills.md) | skille zewnętrzne |
| [`modules/maintenance.md`](modules/maintenance.md) | aktualizacja, rozwiązywanie problemów, zasady, testy |
| [`modules/architecture.md`](modules/architecture.md) | układ repo i moduły `houselib/`, planowany refactor |

## Projekt i historia

| Gdzie | Co |
|---|---|
| [`SPEC.md`](SPEC.md) | założenia i decyzje |
| [`ROADMAP.md`](ROADMAP.md) | kierunki rozwoju |
| [`context-budget.md`](context-budget.md) | pomiary stałego kontekstu sesji |
| [`dry-run-2026-11-05.md`](dry-run-2026-11-05.md) | próba na sucho |
| [`research/`](research/) | tło naukowe |
| [`specs/`](specs/) | projekty zmian (`YYYY-MM-DD-<temat>-design.md`) |
| [`plans/`](plans/) | plany wykonania (`YYYY-MM-DD-<temat>.md`) |

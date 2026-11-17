# Pętla eksperymentów (optimize)

Każda próba to jedna mała zmiana rodzica, uruchomiona ze stałym limitem czasu i oceniona lokalną sprawdzarką.
O tym, czy zmiana zostaje, decyduje kod, nie agent:

```bash
house exp new <konkurs> --from best --idea I-003 --note "lgbm zamiast rf"   # → E-007 .worktrees/exp-E-007
# agent zmienia kod w tym worktree
house exp run <konkurs> E-007 --budget 5m -- python train.py                  # → E-007 keep 0.873
house exp run <konkurs> --from best -- python train.py --lr 0.03               # zmiana = sama komenda
house exp status <konkurs>                                                     # najlepszy, drzewo, stagnacja
```

- Lepszy od rodzica → `keep` (worktree zostaje, bo są w nim predykcje i wagi do zgłoszenia), inaczej `revert`
  (worktree znika). `crash`/`timeout` naprawia się jako dziecko: `--from E-###`. Remis to `revert`.
- Wszystko trafia do `.house/experiments.csv` (`id, parent, commit, idea_id, budget, duration, score, status, …`),
  a każdy commit zostaje jako `refs/exp/E-###`. Stagnację liczy `house exp status` z tego pliku.
- W `spec.yaml` ustaw `scoring.local_scorer` (drukuje `score: <liczba>`) i `scoring.direction: max|min`. Dane
  spoza gita wpisz w `exp.shared` (np. `[data]`), wtedy każdy worktree dostaje do nich link.
- Sprawdzarka to pliki z gita wymienione w komendzie, moduły, które importują, oraz `scoring.scorer_files`
  (globy, np. dla skryptu shell). Dane i predykcje z komendy nie są jej częścią.
- Eksperyment, który zmienia sprawdzarkę albo zamrożony test, kończy się `crash` („tampered”). Ocena zawsze
  używa aktualnej sprawdzarki z konkursu. Po poprawce sprawdzarki starsze wyniki nie są porównywane z nowymi.
- `house exp prune <konkurs> --keep 3` usuwa worktree słabszych `keep` (commity zostają).

Powiązane: [Konkurs krok po kroku](contest-workflow.md).
Plan: [`docs/superpowers/plans/2026-11-13-experiments.md`](../superpowers/plans/2026-11-13-experiments.md).

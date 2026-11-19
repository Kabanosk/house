# Recenzja krzyżowa

`house review <konkurs> T###` (gałąź zadania) albo `house review <konkurs> --since last` (wszystko od poprzedniej
recenzji, na kamień milowy). Kod napisany przez Claude'a recenzuje Codex, a kod Codexa recenzuje Claude.
Codex ma mniejsze limity, więc obowiązuje `limits.codex_reviews_per_day` (domyślnie 4); potem recenzuje świeża
sesja Claude'a, a raport zaznacza słabszą niezależność. Brief jest „ślepy”: cel, kryteria i dokładny diff, bez
raportu autora. Recenzja, która nie wróciła (zawieszenie, pusta odpowiedź, brak `VERDICT:`), jest porażką, a nie
„brakiem uwag”. Zadania `review: always` (sprawdzarka, potok zgłoszeń, kontrakty) przejdą `house merge` dopiero z
recenzją aktualnego commita i bez BLOCKER ani MAJOR, albo z `--accept-review "powód"` (wpis D-###).

Powiązane: [Sprawdzenia i bramka scalania](checks-and-merge.md).
Plan: [`docs/plans/2026-11-13-cross-review.md`](../plans/2026-11-13-cross-review.md).

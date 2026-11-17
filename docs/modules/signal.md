# Signal: pytania i odpowiedzi z telefonu

Konto: `signal-cli` jako urządzenie połączone z Twoim numerem (`~/.config/house/notify.env`:
`HOUSE_NOTIFY_CHANNEL=signal`, `HOUSE_SIGNAL_ACCOUNT=+48…`). W `spec.yaml` konkursu: `notify.alias` (np. `fr`) i
`notify.signal_group` (nazwa grupy drużyny). Każde nowe pytanie z `QUESTIONS.md` idzie jako osobna wiadomość
`🤖 [fr] Q-007 …` do Twojej „Notatki do siebie” i do grupy. **Odpowiedź: przeciągnij wiadomość w prawo i napisz
decyzję** (np. `B`). Odpowiadać może każdy z grupy; liczy się pierwsza odpowiedź, `house` potwierdza
`✓ [fr] Q-007: B (Kuba)` i zapisuje ją w `QUESTIONS.md` + `DECISIONS.md`. Bez cytatu: `Q-007: B` albo
`fr Q-007: B`. Pytania krytyczne mają `Termin` i `Domyślnie`: przypomnienie w połowie czasu, po terminie
judge przyjmuje domyślną odpowiedź (z powiadomieniem). House zapisuje tylko „Notatkę do siebie” i grupy
konkursów; inne rozmowy odrzuca bez zapisu.

Powiązane: [Agent `@claude` z Signala](signal-agent.md).
Plany i specyfikacje: [`signal-notify`](../superpowers/plans/2026-11-13-signal-notify.md),
[`signal-groups` (spec)](../specs/2026-11-16-signal-groups-design.md),
[`signal-groups` (plan)](../plans/2026-11-16-signal-groups.md).

## Powiadomienia i pytania

| Komenda | Co robi |
|---|---|
| `house notify <konkurs> "tekst"` | wiadomość do Ciebie i grupy |
| `house ask <konkurs> [Q-###]` | wyślij nowe otwarte pytania (albo jedno ponownie) |
| `house inbox [konkurs]` | odbierz odpowiedzi (robi to też start każdej sesji) |
| `house deadlines [konkurs]` | przypomnienia i domyślne odpowiedzi po terminie |

Odpowiedzią jest też **reakcja** pod pytaniem: 👍 / 👎 = tak / nie (tylko pytanie bez opcji A/B/C), każde serce =
„leć z domyślną odpowiedzią”. Cofnięcie reakcji przed zapisaniem ją anuluje.

## Rozmowy drużyny (`house signal`)

Każdy odbiór i każda wysyłka trafia do jednego magazynu (`~/.local/share/house/signal/messages.jsonl`, tylko
dozwolone rozmowy, pliki 0600). Czytanie idzie zawsze z magazynu, więc nic nie ginie, nawet gdy odbiór zostanie
przerwany. Dozwolone rozmowy to „Notatka do siebie”, grupy trwających konkursów i grupy z
`~/.config/house/signal.toml`. Prywatne rozmowy (DM) są odrzucane przed zapisem.

| Komenda | Co robi |
|---|---|
| `house signal read [czat] [-n 20] [--since 2h] [--from Kuba] [--grep x] [--thread TS]` | historia rozmowy (z tekstem cytatów i ścieżkami plików) |
| `house signal unread [czat] [--peek]` | nowe od ostatniego odczytu w tym projekcie |
| `house signal show TS` | jedna wiadomość w całości: cytat, reakcje, odpowiedzi |
| `house signal send <czat> "tekst" [--reply-to TS] [--file plik]` | wiadomość z prefiksem 🤖, zapisana w historii |
| `house signal groups [--all]` | dozwolone rozmowy, liczba wiadomości |
| `house signal allow "<nazwa grupy>" --alias krotka` / `deny krotka` | **tylko człowiek w terminalu**: dopisz / usuń grupę |
| `house signal pull` / `status` / `migrate` | odbierz teraz / stan odbioru i magazynu / przenieś stare pliki |
| `house signal compact` / `reparse` | sprzątanie: usunięte, znikające, starsze niż `retention_days`; ponowne parsowanie |
| `house signal archive [czat] [--summarize]` | archiwa (zip + streszczenie dla agentów); grupa bez wiadomości od 180 dni trafia tam sama |
| `house setup --signal-timer` | timer systemd: odbiór co 5 min bez sesji, raz dziennie `compact` |

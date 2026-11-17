# Śledzenie oficjalnego leaderboardu i `/lb` jako skrót zmian

Data: 2026-11-17 · rozmiar: mała zmiana (krótki projekt)

## Po co

`/lb` w czacie Signala pokazuje dziś tylko najlepszy lokalny eksperyment (`exp.summary_line`). Oficjalny ranking
pobiera wyłącznie ręcznie odpalany skrypt w repo konkursu (`claude/validator/leaderboard.py` w techarenie).
Wojtek chce, żeby ranking zbierał się sam w tle, a `/lb` mówił krótko, co się zmieniło od poprzedniego pytania
i gdzie jesteśmy.

## Cele

- Proces w tle co `every` pobiera oficjalny ranking każdego konkursu, który go ma, i zapisuje go do pliku.
- `/lb` nic nie pobiera: czyta zapisany plik i porównuje z rankingiem, który ta rozmowa widziała przy poprzednim `/lb`.
- Odpowiedź `/lb`:
  - **mało zmian** → cytat poprzedniej odpowiedzi `/lb` w tej rozmowie i zdania o zmianach, np.
    „PolyPath poprawił na 0.55 i wszedł na #3 · Climber z 0.54 wskoczył na #5 · my bez zmian, #6 → #7”;
  - **dużo zmian, ponad 8 h od poprzedniego `/lb` albo pierwsze `/lb` w rozmowie** → tabela
    `miejsce | team | wynik`, top 10 plus nasz wiersz, jeśli nas tam nie ma;
  - w obu przypadkach na końcu wiek danych („ranking z 14:05”), a gdy ostatnie pobranie się nie udało, informacja
    od kiedy dane są nieświeże.

## Poza zakresem

- Proces w tle sam nic nie wysyła na Signala (żadnych powiadomień typu „ktoś nas wyprzedził”).
- Bez wykresów i analizy kierunków drużyn (to robi skrypt konkursu i jego `leaderboard_log.csv`).
- Lokalna linia eksperymentów znika z `/lb` (zostaje w hooku startu sesji).

## Podejścia

1. **Ogólnie w house (wybrane).** Konkurs w `spec.yaml` podaje komendę wypisującą ranking jako JSON; house ma
   timer, zapis migawek, porównanie i `/lb`. Następny konkurs z rankingiem potrzebuje tylko jednej komendy.
2. Na skróty: timer odpala istniejący `leaderboard.py`, `/lb` czyta jego `leaderboard_snapshots.csv`. Mniej pracy
   dziś, ale `/lb` związany z formatem jednego skryptu. Odrzucone.
3. Pobieranie w ticku agenta Signala. Zawieszony fetch blokuje obsługę wiadomości. Odrzucone.

## Projekt

**Konfiguracja** w `.house/spec.yaml` konkursu (pole `leaderboard` już istnieje jako `null`):

```yaml
leaderboard:
  cmd: ".venv/bin/python claude/validator/leaderboard.py --json"   # uruchamiane w katalogu konkursu
  every: 15min
  team: UwUr2
```

**Kontrakt komendy:** wypisuje na stdout listę JSON `[{"team": str, "score": float}, ...]` (opcjonalnie `"id"`,
`"filename"`); kolejność = kolejność rankingu. Kod ≠ 0 lub zły JSON = nieudane pobranie.

**Pobieranie:** `house lb poll` przechodzi po konkursach z `contests.yaml`, które mają `leaderboard.cmd` i którym
minął `every`; uruchamia komendę z limitem czasu (2 min) i dopisuje migawkę do `.house/leaderboard.jsonl`
(`{"at": iso, "rows": [...]}`) tylko gdy ranking różni się od ostatniej. Każdą próbę (czas, ok/błąd) zapisuje
w `.house/leaderboard.state.json`, żeby `/lb` mógł powiedzieć o nieświeżych danych. Timer systemd
`house-lb.timer` co 5 min, instalowany obok `house-signal-pull` (`houselib/setup.py`); właściwy interwał
pilnuje `every` z konkursu.

**`/lb`:** dla każdego konkursu rozmowy bierze ostatnią migawkę i tę, którą ta rozmowa widziała przy poprzednim
`/lb` (w stanie agenta: `lb_seen[chat][contest] = {"at": czas migawki, "ts": znacznik naszej odpowiedzi,
"asked": czas pytania}`). Zmiana = drużyna, która zmieniła wynik, weszła lub wypadła albo zmieniła miejsce,
licząc tylko top 10 i nasze sąsiedztwo (±2 miejsca). Liczba zmian > 3, odstęp > 8 h albo brak poprzedniego `/lb`
→ tabela; w przeciwnym razie zdania z cytatem `ts`. Zero zmian → „bez zmian od <czas>, my #7 (0.53)” z cytatem.
Same przesunięcia miejsc wynikające z czyjegoś awansu nie są osobnymi zdaniami (opisuje się tego, kto awansował,
plus nasze miejsce).

**Techarena:** `leaderboard.py` dostaje flagę `--json` (wypisuje kontrakt, zachowuje swoje CSV), a repo konkursu
dostaje `.house/spec.yaml` z sekcją powyżej.

## Ograniczenia

- Skrypt techareny potrzebuje ciasteczka Agorize (`~/.agorize_curl_dc.txt`) i `tools/agorize_cli.py`; na tej
  maszynie nie ma ani jednego, ani drugiego, ani `.venv`. Bez nich poll zapisuje błędy, a `/lb` mówi „brak danych”.
- Rozpoznanie 17.11 (Finland Tech Arena): strona `…/pages/leaderboard` odpowiada 200 także bez ciasteczka, a jej
  JS zawiera jawnie `const url = 'https://techarena2026.xianti.fr/api/leaderboard/tap26-<konkurs>?best=true'` i
  `const token = '…'` (nagłówek `Authorization: Bearer`); odpowiedź to `{"data": [{"team__name", "score", …}]}`,
  strona wyświetla wiersze w kolejności API. Ciasteczko nie jest więc potrzebne do czytania rankingu. Kształtu
  odpowiedzi API z Finland nie potwierdzono (wywołanie zablokowane w sesji) — testy jadą na stałym JSON-ie.
- Testy bez sieci: komenda konkursu w testach to skrypt wypisujący stały JSON.

## Zostawione budującemu

Dokładne brzmienie zdań, wyrównanie tabeli (blok monospace w Signalu nie istnieje — zwykłe wiersze
`3 | PolyPath | 0.55`), nazwy funkcji i modułu (`houselib/leaderboard.py`).

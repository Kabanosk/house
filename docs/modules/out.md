# Długie komendy (`house out`)

Pełne uruchomienie testów, build czy trening potrafią wypisać więcej, niż model powinien czytać. `house out`
uruchamia komendę tak, że cały wynik trafia do pliku, a agent widzi tylko krótką kopertę:

```bash
house out -- python3 -m pytest -q          # → koperta: exit, bajty, linie, czas, ostatnie ~40 linii
house out show 20261117-100000-abcd --grep 'FAILED'      # tylko pasujące linie z kontekstem
house out show E-007 --grep loss --regex   # log eksperymentu `house exp`
house out prune --days 7                   # kasuje stare logi (domyślnie starsze niż 7 dni)
```

- Log leży w `.house/tmp/out/<id>.log` (poza gitem), id to `YYYYMMDD-HHMMSS-<4 znaki hex>`. Z worktree workera log
  trafia do głównego konkursu, a `show` znajduje go stamtąd (kontekst z `HOUSE_DIR`, inaczej z bieżącego katalogu).
- stderr jest wmieszany do stdout w kolejności. Log trzyma ostatnie 1 000 000 znaków (ten sam limit co log `house exp`).
- Koperta ma co najwyżej ok. 4 000 znaków: kod wyjścia, bajty, linie, czas, ostatnie ok. 40 linii (każda do ok. 500
  znaków, pasek postępu z `\r` zwija się do ostatniego stanu), ścieżkę logu `log: $HOUSE_DIR/tmp/out/<id>.log` i gotową
  komendę `house out show <id> --grep '<wzorzec>'`. Zakres linii czytasz po tej ścieżce (np. `sed -n 100,200p
  "$HOUSE_DIR/tmp/out/<id>.log"`): `$HOUSE_DIR` ma każda sesja agenta, działa też z worktree i nie pokazuje ścieżki domowej.
- Kod wyjścia `house out` to kod komendy (128+n po sygnale, 127 gdy komendy nie ma, 126 gdy nie da się jej uruchomić).
- Gdy komenda się skończy, a coś z jej grupy procesów (np. serwer w tle) dalej działa: wynik jest zbierany jeszcze
  najwyżej 1 s (albo do końca strumienia), potem reszta grupy dostaje SIGTERM, a po 2 s SIGKILL. Kod wyjścia zostaje
  kodem komendy, a koperta ma linię `background processes stopped`. Nic z grupy nie zostaje po `house out`.
- Ctrl-C, SIGTERM i SIGHUP (np. limit czasu narzędzia agenta) wysyłają ten sam sygnał całej grupie procesów komendy.
  Przez 5 s wynik jest dalej zbierany (długi raport przy zamykaniu trafia do logu w całości), potem SIGKILL. Log
  zachowuje to, co komenda napisała, koperta i tak się drukuje, a kod to 128+n: 130 (Ctrl-C), 143 (SIGTERM),
  129 (SIGHUP). Na czas zapisywania logu i koperty te sygnały są ignorowane, a na czas startu komendy zablokowane
  (przerwanie w tej chwili zadziała zaraz po starcie, więc komenda nie zostaje sierotą). Ograniczenie: SIGKILL samego
  `house out` (np. `kill -9`) nie daje szansy na sprzątanie; grupa procesów komendy (osobna sesja) działa wtedy dalej
  i trzeba ją zatrzymać ręcznie.
- Logi nie wychodzą poza konkurs: `run`, `show` i `prune` odmawiają, gdy `.house/tmp` albo `.house/tmp/out` jest
  dowiązaniem symbolicznym. `show` czyta tylko zwykły plik (nie dowiązanie, nie FIFO). Błąd przy braku logu podaje
  sam id, bez ścieżki. Pisarz trzyma na logu blokadę `flock` przez cały czas działania komendy; `prune` pomija pliki,
  których nie może zablokować na wyłączność, więc nie skasuje logu trwającego treningu. Plik tymczasowy przy
  przycinaniu logu ma losową nazwę (`mkstemp`) w tym samym katalogu.
- `show`: wzorzec jest dosłowny (`--regex` włącza wyrażenie regularne, bez limitu czasu dopasowania); najwyżej 20
  trafień po 2 linie kontekstu, linie do 500 znaków, razem do 16 000 znaków, na końcu uwaga `more hits`. Błędny id,
  brak logu lub zły regex to błąd (kod 2); brak konkursu to kod 1.
- Guard traktuje `house out -- KOMENDA` jak samą `KOMENDĘ`: `house out -- rm .house/attempts.jsonl` (albo chroniony
  test akceptacyjny) jest odrzucone tak samo jak `rm .house/attempts.jsonl`, także z przekierowaniem między słowami
  (`house >/dev/null out -- …`), za opakowaniem (`timeout 60 …`, `env X=1 …`) i w `bash -c '…'`. `house out show`
  i `prune` nie są opakowaniem.
- W zadaniach z Signala `house` jest zablokowany, więc `house out` tam nie działa.
- Zgoda na uruchomienie: `house adapt` dopuszcza bez pytania tylko `house out show`, `house out prune` i
  `house out -- <komenda>` dla komend z listy dozwolonych konkursu (`git status`, `pytest`, `npm run` …). Inna komenda
  w `house out --` wymaga zgody jak bez opakowania. Konkursy przygotowane wcześniej dostają te reguły po
  `house adapt <konkurs>` (zob. [Ochrona](guard.md)).

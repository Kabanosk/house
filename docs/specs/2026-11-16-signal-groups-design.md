# Czytanie grup Signala (2026-11-16)

_Stan: szkic do akceptacji. Źródła: dzisiejsza sesja z grupą `techarena_claude` (ręczny skrypt na `inbox.extract`,
szukanie cytowanej wiadomości w logach sesji), kod `houselib/signal.py`, `houselib/inbox.py`, `houselib/notify.py`,
`houselib/hook.py`, plan `docs/plans/2026-11-13-signal-notify.md`, spec P3
(`2026-11-12-system-hardening-design.md` §7), `signal-cli 0.14.10 --help`. Decyzje oznaczone **[moja decyzja]**
podjął Claude, nie Wojtek — do sprawdzenia przy czytaniu. Pytania do Wojtka: sekcja 14._

## 1. Po co

P3 zrobił z Signala kanał na pytania i odpowiedzi Q-###. Do tego wystarczało: `house inbox` odbiera, zostawia
„Notatkę do siebie” i grupy trwających konkursów, dopasowuje odpowiedzi i resztę ignoruje. Dziś okazało się, że
Signal służy też do zwykłej rozmowy z drużyną, a wtedy ten model nie działa:

1. **Grupa spoza konkursu ginie.** `techarena_claude` nie jest `notify.signal_group` żadnego trwającego konkursu,
   więc filtr prywatności w `pull` odrzuciłby odpowiedź kolegi. `receive` ją przy tym zużywa, więc nie dałoby się
   jej odczytać drugi raz. Trzeba było napisać ręczny skrypt z `inbox.extract` i własnym zbiorem grup.
2. **Nie wiadomo, na co ktoś odpowiedział.** `extract` zapisuje tylko `quote_ts` i gubi `quote.text`, chociaż
   signal-cli go podaje. Treść cytowanej wiadomości trzeba było odszukać w logach sesji Claude.
3. **Wysłane wiadomości nie są zapisywane.** Powitanie wysłane przez `sig.send` z pominięciem `notify.send` nie
   trafiło do `sent.jsonl` (ten plik w ogóle jeszcze nie istnieje). Potwierdzenia z `inbox._reply` też idą obok
   logu. Cytatu na taką wiadomość nie da się dopasować, a w historii rozmowy widać tylko połowę.
4. **Odbiór zużywa wiadomości.** Każdy odczyt, który nie zapisze wyniku od razu, traci dane. Dziś `pull` zapisuje
   dopiero po zakończeniu `signal-cli`, więc przerwanie procesu (np. timeout hooka) w tym momencie gubi całą paczkę.
5. **Brak czytania historii.** Nie ma komendy „ostatnie 20 w grupie”, „od 10:00”, „co napisał Kuba”, a start sesji
   nie mówi, że w grupie jest coś nowego.

Przy okazji wyszły dwa mniejsze błędy: `inbox.process` kończy bez odbioru, gdy nie trwa żaden konkurs (urządzenie
nic nie odbiera, a Signal może odłączyć bezczynne urządzenie), a `doctor` ocenia „ostatni odbiór” po czasie
modyfikacji `inbox_done.json`. Ten plik zmienia się tylko wtedy, gdy jakaś odpowiedź zostanie obsłużona (dziś to
`[]` z 13.11).

Kierunek: **jeden trwały magazyn wiadomości z dozwolonych rozmów, do którego dopisuje każdy odbiór i każda
wysyłka. Czytanie odbywa się zawsze z magazynu, nigdy przez `receive`.** `house inbox` staje się jednym
z czytelników tego magazynu.

## 2. Cele, nie-cele, anty-cele

**Cele**
- Agent w dowolnej sesji (Claude Code, Codex, `claude -p`) czyta grupę albo „Notatkę do siebie” jedną komendą
  z Basha: ostatnie N, od czasu, od osoby, wątek cytatów, nowe od ostatniego razu.
- Żadna wiadomość z dozwolonej rozmowy nie ginie: każda odebrana jest zapisana, zanim kod zajmie się następną.
- Każda wiadomość wysłana przez `house` (także „ręcznie” z Pythona) jest w magazynie z timestampem Signala.
- Odpowiedź z cytatem pokazuje, na co odpowiada (tekst cytatu z samej wiadomości), bez szukania gdziekolwiek.
- Start sesji mówi w 1–4 liniach, ile jest nowych wiadomości i gdzie.
- Format magazynu łatwy do zaindeksowania przez `house search` (osobny spec).
- Członkowie dozwolonych grup to zaufana drużyna (decyzja Wojtka, 2026-11-16): wiedzą, że grupę czyta Claude,
  i mogą pisać do niego prośby. Agent traktuje je jak prośby człowieka z drużyny i może odpisać w grupie.
- Pliki w obie strony: agent dostaje załączniki z dozwolonych rozmów jako pliki na dysku i może wysłać plik do
  grupy (decyzja Wojtka, 2026-11-16).

**Nie-cele**
- Prywatne rozmowy konta (DM). Nie są zapisywane, nie są liczone po nadawcy i nie da się ich włączyć.
- Pełny klient Signala: połączenia, ankiety, stories, administrowanie grupą.
- Analiza treści załączników. Agent dostaje ścieżkę do pliku i sam decyduje, czy go otworzyć.
- Wyszukiwanie pełnotekstowe i rankingowe (to `house search`, osobny spec). Tutaj wystarczy prosty filtr
  `--grep`.
- Wiele kont Signala.

**Anty-cele (porażka, nawet jeśli działa)**
- Zapis czegokolwiek z prywatnej rozmowy, choćby do logu błędów.
- Agent, który sam dopisuje grupę do dozwolonych.
- Start sesji, który zwalnia o kilka sekund albo zapycha kontekst cudzą rozmową.
- Dwa źródła prawdy o tym, co przyszło (`inbox.jsonl` i nowy magazyn żyjące obok siebie).

## 3. Użytkownicy i ich potrzeby

| Kto | Potrzeba | Jak |
|---|---|---|
| Agent w sesji (coordinator, worker, interaktywna) | przeczytać, co drużyna napisała; sprawdzić, na co ktoś odpowiedział; czasem odpisać | `house signal read/unread/show/send` z Basha, wyjście tekstowe albo `--json` |
| Wojtek w terminalu | to samo, plus dopisanie lub usunięcie grupy z dozwolonych | te same komendy, plus `allow`/`deny` (tylko z TTY) |
| Hook `SessionStart` | szybko odebrać i pokazać licznik nowych | `pull` z krótkim limitem + 1–4 linie podsumowania |
| `house night` | odbierać co iterację (odpowiedzi Q-###, urządzenie aktywne), w raporcie porannym policzyć wiadomości | `pull` + `inbox.process`, licznik w `reports/night-*.md` |
| `house inbox` / `house deadlines` | odpowiedzi na Q-### jak dziś | czytelnik magazynu z własnym znacznikiem obsłużonych |
| Timer systemd (opcjonalnie) | odbiór co kilka minut bez sesji | `house signal pull` |
| `house search` (przyszły) | przyrostowo indeksować wiadomości | `messages.jsonl` po `seq` (§12) |

## 4. Pakiety i kolejność

| # | Pakiet | Zależy od | Rozmiar |
|---|---|---|---|
| S1 | Magazyn: format, zapis strumieniowy przy odbiorze, locki, migracja `inbox.jsonl` | — | średni |
| S2 | Konfiguracja dozwolonych grup (`signal.toml`, `allow`/`deny`/`groups`) | S1 | mały |
| S3 | Log wysłanych w `Signal.send` + `house signal send --reply-to` | S1 | mały |
| S4 | Czytanie: `read`, `unread`, `show`, `status`; kursory | S1 | średni |
| S5 | Integracja: `house inbox` na magazynie, hook startu sesji, `night`, `doctor` | S1–S4 | mały |
| S6 | Timer systemd + `compact` (usunięte, znikające, `deny --purge`) | S1 | mały |
| S7 | Agent uruchamiany z Signala (§15) | S1–S6 | duży, osobny plan |
| S8 | Archiwum nieaktywnych rozmów: zip + streszczenie (§16) | S1, S6 | mały |

Kolejność **[moja decyzja]**: S1 → S3 → S2 → S4 → S5 **przed Collabothonem (20–22.11)**, bo przez 48 h drużyna
będzie pisać głównie w grupie, a odpowiedzi nie mogą ginąć. S6 może przyjść po Collabothonie. Do tego czasu
odbiór idzie na żądanie (hook, `night`, każda komenda czytająca, §8). S3 jest przed S2, bo log wysłanych to
najtańsza poprawka, a bez niego nowe cytaty od razu tracą dopasowanie.

## 5. Konfiguracja dozwolonych rozmów (S2)

**Zasada.** Zbiór dozwolonych rozmów to suma trzech części:
1. „Notatka do siebie” (zawsze, jak dziś);
2. grupy z `notify.signal_group` **trwających** konkursów (jak dziś);
3. grupy z jawnej listy w `~/.config/house/signal.toml` (nowe, bez związku z konkursami).

Cała reszta jest odrzucana w pamięci przed jakimkolwiek zapisem. Dotyczy to też grup, w których konto jest, ale
których nie ma na liście.

**Plik** **[moja decyzja]**: osobny `~/.config/house/signal.toml`, obok `notify.env`, poza każdym repo.
Czytany przez `tomllib` (stdlib). `notify.env` zostaje na kanał i numer, bo jest w formacie shellowym, a lista
grup z polami się do niego nie mieści.

```toml
# Grupy Signala, które house zapisuje i pozwala czytać agentom. Zmieniaj przez `house signal allow/deny`.
[groups.techarena]                  # alias: krótka nazwa w komendach (`house signal read techarena`)
id = "JsNv4TnmFZy5bsRClyblxKkuIWvEGeNmKaqPbJuYM5M="
name = "techarena_claude"           # nazwa w chwili dodania, tylko informacyjnie
added = "2026-11-16T12:40:00+01:00"
# retention_days = 90               # opcjonalnie: compact usuwa starsze (§11)
```

- **Klucz to `id`, a nie nazwa** **[moja decyzja]**. Zmiana nazwy grupy przez kogoś z drużyny nie może po cichu
  przenieść zgody na inną grupę o tej samej nazwie. `groups.json` zostaje cache'em nazwa → id dla
  `notify.signal_group` konkursów, jak dziś.
- Alias to małe litery, cyfry i `-`, unikalny w pliku. Nie może kolidować z aliasem trwającego konkursu, chyba że
  oba wskazują ten sam `id` (wtedy to ta sama rozmowa). `self` i `all` są zarezerwowane.
- Grupy konkursów dostają alias z `notify.alias` (albo nazwy konkursu), więc `house signal read fr` działa bez
  wpisu w `signal.toml`.

**Komendy**
```
$ house signal groups
alias       grupa               źródło                 wiadomości  ostatnia
self        Notatka do siebie   zawsze                         41  dziś 12:31
techarena   techarena_claude    signal.toml                    23  dziś 12:18
fr          TechArena FR team   konkurs techarena-fr          112  wczoraj 23:02

$ house signal groups --all          # tylko dla człowieka: wszystkie grupy konta, z oznaczeniem dozwolonych
$ house signal allow "techarena_claude" --alias techarena
✓ techarena → techarena_claude (JsNv4Tnm…). Od teraz zapisywane i czytelne dla agentów.
$ house signal deny techarena [--purge]
```

- **`allow` i `deny` wymagają terminala (TTY) i potwierdzenia `[t/N]`** **[moja decyzja]**. Sesje agentów nie mają
  TTY w Bashu, więc agent nie może sam poszerzyć zbioru czytanych rozmów. To ograniczenie pilnuje kod, a nie
  prompt. Bez TTY komenda kończy się kodem 2 i komunikatem „dodanie grupy to decyzja człowieka: poproś Wojtka
  o `house signal allow …`”.
- `groups --all` wypisuje nazwy wszystkich grup konta. Nazwy prywatnych grup to też informacja, więc bez TTY
  komenda pokazuje tylko liczbę niedozwolonych grup, bez nazw **[moja decyzja]**.
- `allow` dla grupy, której nie ma w `listGroups`, albo dla niejednoznacznej nazwy: błąd z listą pasujących id do
  wyboru (`--id`).
- `deny` usuwa wpis. Historia zostaje, chyba że podano `--purge` (§11). Grupy konkursu nie da się zablokować
  przez `deny`: komunikat każe zmienić `notify.signal_group` w specu albo zakończyć konkurs.
- Po zakończeniu konkursu jego grupa wypada ze zbioru, jak dziś. Kto chce czytać ją dalej, dodaje ją przez `allow`.

## 6. Odbiór i trwały zapis (S1)

**Jedna funkcja odbioru** (np. `signalstore.pull(sig)`). Używają jej wszyscy: `house signal pull`, każda komenda
czytająca (§8), `house inbox`, hook, `night`, timer. Nigdzie poza nią nie wolno wołać `Signal.receive`. Zasada idzie
do docstringu `signal.py`, a test grepuje `houselib/` w poszukiwaniu innych wywołań.

**Zapis strumieniowy** **[moja decyzja]**. `receive` czyta stdout `signal-cli` linia po linii (`Popen`, a nie
`run`). Każda zaakceptowana linia jest od razu dopisywana do magazynu (`write` + `flush` + `os.fsync`), zanim kod
przeczyta następną. Powód: signal-cli potwierdza wiadomość serwerowi w chwili jej wypisania, więc każdy bufor
w pamięci to okno utraty, a hook Claude Code może zabić proces po swoim timeoucie. Przy przerwaniu ginie
najwyżej linia w trakcie parsowania. `SIGTERM` jest łapany: kod dokańcza bieżącą linię i kończy.

**Parametry** `signal-cli -a <acct> -o json receive --timeout <t> [--max-messages <n>] --ignore-stories`:
- `--timeout`: 1 s w hooku i w komendach czytających, 5 s w `house signal pull` i timerze. Krótki timeout ma
  znaczenie, bo signal-cli trzyma blokadę konta i równoległy `send` czeka.
- `--max-messages 200` w hooku, żeby po tygodniu przerwy start sesji nie trwał minutę. Reszta zostaje na
  serwerze do następnego odbioru i nic nie ginie.
- `--ignore-stories`: stories nie są w zakresie.
- Załączniki: signal-cli dalej je pobiera, bo to jego zachowanie domyślne i tak jest dziś. Bez tego agent nie
  dostałby plików z grup (pytanie 7 dotyczy tylko plików z DM).

**Co zapisujemy z dozwolonej rozmowy** (decyzja o zakresie **[moja decyzja]**):

| Rodzaj (`kind`) | Źródło w JSON signal-cli | Zapis | Pokazywanie |
|---|---|---|---|
| `message` | `dataMessage` / `syncMessage.sentMessage` z `message` lub załącznikiem | tekst, nadawca, **cytat z tekstem**, wzmianki, załączniki (metadane), `expiresInSeconds` | zwykła linia |
| `reaction` | `dataMessage.reaction` | emoji, cel (`targetSentTimestamp`, autor), `isRemove` | pod wiadomością: `👍 Ania, Kuba` |
| `edit` | `editMessage` (`targetSentTimestamp` + nowy `dataMessage`) | nowa treść | treść po edycji + `(edytowano)` |
| `delete` | `dataMessage.remoteDelete` | cel | `[usunięta przez autora]`; `compact` czyści tekst |
| `other` | naklejka, ankieta, zmiana grupy, inne z treścią | tylko `raw` | `[naklejka]`, `[ankieta]`, `[zmiana grupy]` |
| — | potwierdzenia odczytu/doręczenia, pisanie, sync kontaktów, puste sync | nie zapisujemy | — |

- **Tekst cytatu** (`quote.text`) i autor cytatu (`quote.author`/`authorNumber`/`authorUuid`) są zapisywane zawsze,
  także gdy cytowanej wiadomości nie ma w magazynie. To bezpośrednio naprawia brak 2.
- **Wzmianki**: signal-cli w tekście wstawia `U+FFFC`, a lista `mentions` niesie zakres i nazwę. Przy zapisie
  zamieniamy znak na `@Imię`, a oryginał zostaje w `raw`.
- **Załączniki**: zapisujemy metadane (`contentType`, `filename`, `size`, `id`), a plik pobrany przez signal-cli
  (`~/.local/share/signal-cli/attachments/<id>`) **kopiujemy** do `signal/files/<alias>/<data>_<ts>_<nazwa>`
  i zapisujemy tę ścieżkę jako `path` **[moja decyzja]**. Powód: nazwa z `id` nic agentowi nie mówi, a katalog
  signal-cli miesza pliki z grup i z DM. Kopia leży w katalogu, który przeszedł filtr prywatności, więc agent może
  go czytać bez ryzyka trafienia na plik z DM. Brak pliku u signal-cli (np. za duży, błąd pobierania) daje
  `path: null` i `[📎 nazwa, nie pobrano]` przy wyświetlaniu. Wiadomość z samym załącznikiem, bez tekstu, też jest
  zapisywana. Dziś `extract` ją gubi, bo wymaga `message`.
- **Pliki z prywatnych rozmów** (Wojtek, 2026-11-16: agent ma nie widzieć prywatnych rozmów ani plików). signal-cli
  nie umie pobierać załączników tylko z wybranych czatów (`--ignore-attachments` wyłącza wszystkie, a
  `getAttachment` zwraca tylko już pobrane). Pliki z DM zostają w `~/.local/share/signal-cli/attachments/`
  i house ich nie usuwa (F7, Wojtek: nie trzeba). `house-hook pre-tool` blokuje agentom odczyt
  `~/.local/share/signal-cli/` **[moja decyzja]**, więc agent widzi tylko kopie z dozwolonych czatów
  w `signal/files/`.
- **`raw`**: przy każdym zapisanym zdarzeniu trzymamy pełną `envelope` z signal-cli **[moja decyzja]**. Gdyby `raw`
  istniał wcześniej, brak 2 dałoby się naprawić wstecz, przez ponowne sparsowanie zapisanych wiadomości. Wolumen
  jest mały (setki wiadomości dziennie w szczycie), a `raw` pochodzi z rozmowy, która już przeszła filtr. Nowe pola
  i błędy parsera naprawia `house signal reparse`, bez ponownego `receive`.
- **Nadawca**: `sourceName`, `sourceNumber` (bywa `null` przy ukrytym numerze), `sourceUuid`. Własne wiadomości
  (sync) mają `dir: "own"`. Nazwę z profilu bierzemy z chwili odbioru.
- **Edycja odpowiedzi Q-###** **[moja decyzja]**: jeśli oryginał nie był jeszcze obsłużony, `inbox` bierze tekst po
  edycji. Jeśli już był, edycja nie zmienia decyzji (pierwsza odpowiedź wygrywa, jak w P3) i trafia do logu
  `house inbox` jako „Q-007: odpowiedź edytowana po zapisaniu, decyzja bez zmian”.
- **Reakcje jako odpowiedzi** na Q-### (Wojtek, 2026-11-16): reakcja na wiadomość house z pytaniem:
  - 👍 (z dowolnym odcieniem skóry) = „tak”, 👎 = „nie”, ale tylko dla pytania tak/nie. Przy pytaniu z opcjami
    A/B/C kciuk nie jest odpowiedzią, a house odpisuje „odpowiedz literą albo ❤ = domyślna” **[moja decyzja]**;
  - **każde serce = „leć z domyślną odpowiedzią”**. Rozpoznawanie po zbiorze, nie po jednym znaku: emoji po
    usunięciu selektorów wariantu (U+FE0F) i łącznika ZWJ zawiera któryś znak serca: ❤ ❣ 💔 💓 💕 💖 💗 💘 💙 💚
    💛 💜 💝 💞 💟 🖤 🤍 🤎 🧡 🩵 🩶 🩷 (obejmuje też ❤️‍🔥 i ❤️‍🩹, a 💔 też liczy się jako „domyślna”, bo jest na
    liście Wojtka). Pytanie bez domyślnej odpowiedzi: house odpisuje „brak domyślnej, odpowiedz tekstem”;
  - cofnięcie reakcji (`isRemove`) przed obsłużeniem anuluje ją; po obsłużeniu nic nie zmienia (pierwsza wygrywa);
  - reakcja w grupie konkursu liczy się tak samo jak w Note to Self (drużyna jest zaufana).
- **Znikające wiadomości** (`expiresInSeconds` > 0): po upływie czasu od odczytu nie są pokazywane, a `compact`
  usuwa ich tekst i `raw` **[moja decyzja]**. Drużyna, która włącza znikanie, ma prawo oczekiwać, że wiadomości
  znikną także u nas.

**Odrzucone** wiadomości (DM, niedozwolone grupy) nie zostawiają śladu po nadawcy, grupie ani treści. Wolno tylko
zwiększyć licznik `dropped` w `state.json` dla bieżącego odbioru, żeby `status` mógł powiedzieć „odrzucono 4
(spoza dozwolonych)”.

## 7. Log wszystkich wysłanych (S3)

**Zapis w najniższej warstwie** **[moja decyzja]**: `Signal.send` sam dopisuje zdarzenie `dir: "out"` do magazynu,
zarówno przy sukcesie (z timestampem z signal-cli), jak i przy porażce (`status: "failed"`, `ts: null`).
`notify.send` przekazuje tylko metadane (`contest`, `question`, `origin`). Dzięki temu ręczne `sig.send` z sesji,
potwierdzenia z `inbox._reply` i każdy przyszły kod są logowane bez pamiętania o tym. Ominąć log można tylko,
wołając `signal-cli` bezpośrednio. Tego zabrania docstring, a `house-hook pre-tool` w repo konkursów może to wyłapać
(pytanie 8).

- Wysłana wiadomość trafia do historii rozmowy. signal-cli nie odbiera własnych wysłanych jako sync, więc bez tego
  w `read` brakowałoby połowy rozmowy (brak 3).
- `origin`: `notify`, `ask`, `inbox-reply`, `deadline`, `night`, `cli` (`house signal send`), `python` (domyślnie,
  gdy nikt nie podał).
- Cytaty dopasowujemy po `quote.ts` do zdarzeń `out` z `meta.question` w magazynie. `sent.jsonl` przestaje być
  potrzebny (§10).

**`house signal send`** (dla człowieka i agenta):
```
$ house signal send techarena "Wrzuciłem nowy baseline, LB 0.806" --reply-to 1794821855568
✓ techarena 1794822101442 (cytat: Kuba Nowak 12:18 „Spoko, a jaki wynik…”)
```
- Treść zawsze dostaje prefiks `🤖 [house]` albo `🤖 [<alias konkursu>]`, gdy komenda jest uruchomiona w repo
  konkursu **[moja decyzja]**. Wiadomości wychodzą z numeru Wojtka, więc drużyna musi widzieć, że pisze system
  (zasada z P3).
- `--reply-to <ts>` szuka zdarzenia w magazynie i wysyła z `--quote-timestamp <ts> --quote-author <autor>`
  (signal-cli wymaga obu). Nieznany `ts` daje błąd i nic nie zostaje wysłane.
- Odbiorca to tylko alias z dozwolonych rozmów albo `self`. Numer telefonu (DM) jest odrzucany.
- **Agent może sam pisać do dozwolonej grupy** (decyzja Wojtka, 2026-11-16), zawsze z prefiksem i logiem. Gołe
  `signal-cli … send|receive` w Bashu agentów blokuje `house-hook pre-tool` **[moja decyzja]**, bo omija log
  i zapis odebranych.
- **Pliki**: `--file PATH` (można powtórzyć, przekazywane jako `--attachment` do signal-cli), też bez tekstu:
  ```
  $ house signal send techarena --file reports/lb.png "Wykres LB po nocy"
  ✓ techarena 1794822301442 (📎 lb.png, 84 KB)
  ```
  Zdarzenie `out` ma `attachments` z oryginalną ścieżką. Odrzucane są pliki większe niż 100 MB (limit Signala) oraz
  ścieżki z `~/.ssh`, `~/.config`, `~/.local/share/signal-cli` i pliki `.env`/`*.pem`/`*.key` **[moja decyzja]**:
  drużyna jest zaufana, ale agent może się pomylić, a wysłanego pliku nie da się cofnąć.

## 8. Czytanie (S4)

**Zasada: czytanie nigdy nie zależy od `receive`.** Komenda czytająca najpierw robi szybki `pull` (timeout 1 s), ale
tylko wtedy, gdy ostatni udany odbiór był ponad 60 s temu, Signal jest skonfigurowany i nikt inny akurat nie
odbiera. Potem czyta wyłącznie magazyn. `--offline` pomija `pull`. Gdy Signal nie jest skonfigurowany, ma błąd
albo odbiór jest zablokowany przez inny proces, komenda i tak pokazuje zapisaną historię, a w nagłówku podaje
powód **[moja decyzja]**.

Przekazanie wyniku dalej jest bezpieczne, bo `pull` zapisuje, zanim cokolwiek zostanie pokazane. Odczyt nie jest
więc „konsumpcją” w sensie braku 4.

**Komendy**
```
house signal read [CHAT] [-n 20] [--since 2h|30m|1d|10:00|2026-11-16T10:00] [--until …]
                  [--from NAME|me] [--grep TEKST] [--thread TS] [--json] [--offline]
house signal unread [CHAT] [--peek] [--as NAZWA] [--json]
house signal show TS [--chat CHAT] [--raw]
house signal status
house signal pull [--timeout 5]
house signal reparse | compact | migrate
```
- `CHAT`: alias, `self` albo `all` (domyślnie `all`, czyli wszystkie dozwolone, posortowane po czasie).
- `-n`: ostatnie N po filtrach (domyślnie 20, `-n 0` = wszystkie). `--since`/`--until` w czasie lokalnym.
- `--from`: dopasowanie bez rozróżniania wielkości liter do nazwy nadawcy, numeru albo `me` (własne i wysłane).
- `--thread TS`: wiadomość, łańcuch cytatów w górę i wszystkie odpowiedzi cytatem na nią.
- `--json`: jedno zdarzenie na linię, w formacie magazynu bez `raw`. Dla agentów, które chcą coś policzyć.

**Wyjście** (wzór; tekst dosłowny, bez skracania w `read`, skracany do 100 znaków tylko w hooku):
```
$ house signal read techarena -n 4
techarena · techarena_claude · 4 z 23 · odebrano 12:31
[12:02] 1794820000000 🤖 house: 🤖 [house] Cześć! Jestem Claude Wojtka, będę tu wrzucał…
[12:15] 1794821000000 Ania K.: super, to ja wrzucę dane z wczoraj  [📎 wyniki.csv, 4 KB]
[12:18] 1794821855568 Kuba Nowak ↩ 🤖 house 12:02 „Cześć! Jestem Claude Wojtka…”:
        Spoko, a jaki wynik ma teraz baseline?
        👍 Wojciech Fiołka
[12:31] 1794822101442 🤖 house ↩ Kuba Nowak 12:18 „Spoko, a jaki wynik…”:
        🤖 [house] Wrzuciłem nowy baseline, LB 0.806

$ house signal unread
3 nowe od 2026-11-16 09:40 (dla: house)
techarena · 2
  [12:15] Ania K.: super, to ja wrzucę dane z wczoraj  [📎 wyniki.csv]
  [12:18] Kuba Nowak ↩ 🤖 house „Cześć! Jestem…”: Spoko, a jaki wynik ma teraz baseline?
self · 1
  [11:58] Wojciech Fiołka: fr Q-012: B
(oznaczone jako przeczytane; `--peek` nie oznacza)

$ house signal show 1794821855568 --chat techarena
Kuba Nowak · techarena · 2026-11-16 12:18:03 · odebrano 12:18:41
↩ cytuje 1794820000000 (🤖 house, 12:02): „Cześć! Jestem Claude Wojtka, będę tu wrzucał…”
Spoko, a jaki wynik ma teraz baseline?
reakcje: 👍 Wojciech Fiołka · odpowiedzi cytatem: 1794822101442
```
- Identyfikator wiadomości to timestamp Signala, ten sam, którego używa cytat. Wewnątrz jednej rozmowy prawie
  zawsze jest unikalny. Gdy nie jest (dwóch autorów w tej samej milisekundzie), `show` wypisuje obie i prosi
  o `--from`.
- Czas wyświetlany lokalnie: `HH:MM` dla dziś, `wczoraj HH:MM`, a starsze z datą.
- Wiadomości `out` ze `status: failed` są pokazywane jako `✗ nie wysłano`, żeby agent wiedział, że drużyna jej nie
  dostała.

**Kursory „nowe”** **[moja decyzja]**: `cursors.json` trzyma `{nazwa: ostatni seq}`. Nazwa domyślnie to katalog
projektu (git toplevel cwd, a dla repo konkursu nazwa konkursu); `--as` ją zmienia. `unread` pokazuje zdarzenia
`in`/`own` z `seq` > kursor i przesuwa kursor (chyba że podano `--peek`). Własne wysłane przez house nie są
„nowe”. Kursor per projekt, a nie jeden globalny, bo dwie równoległe sesje w różnych repo mają widzieć to samo,
a nie podbierać sobie wiadomości. Nowy kursor zaczyna od wiadomości z ostatnich 24 h, a nie od początku historii.

**Exit codes**: 0 (także przy pustym wyniku), 2 (zły argument, nieznany alias, niejednoznaczny `ts`). Błąd
signal-cli przy szybkim `pull` nie zmienia kodu wyjścia komendy czytającej.

## 9. Integracja (S5)

**`house inbox`** zostaje komendą od odpowiedzi Q-###, ale:
- odbiera przez wspólny `pull` z pełnym zbiorem dozwolonych (§5), **także gdy nie trwa żaden konkurs**. Naprawia to
  błąd z §1: dziś nie ma wtedy odbioru, a urządzenie jest bezczynne;
- routuje tylko zdarzenia `message`/`edit` z czatów konkursów i z `self`, w kolejności `ts` (pierwsza wygrywa,
  bez zmian). Wiadomości z grup spoza konkursów nigdy nie są odpowiedziami;
- znacznik obsłużonych przenosi się do `signal/inbox_done.json` (klucz `chat:author:ts`, a nie samo `ts`);
- na końcu dopisuje linię `signal: 3 nowe w grupach (house signal unread)`, jeśli są nieprzeczytane dla projektu.

**Hook `SessionStart`** (`hook._signal_lines`):
- dziś działa tylko w repo konkursów, bo hook generuje `house adapt`. Zmiana: `house adapt` dla repo `house` też
  wpisuje hook do `.claude/settings.local.json` (opt-in przy `house setup`). Hook globalny w
  `~/.claude/settings.json` nie jest domyślny **[moja decyzja]**: na tej maszynie są też sesje służbowe,
  a podsumowanie rozmów drużyny w ich kontekście to wyciek. Pytanie 3;
- kolejność: `pull` (timeout 1 s, `--max-messages 200`, cały hook do 10 s jak dziś) → `inbox.process` → podsumowanie;
- podsumowanie to **1–4 linie** w ramach obecnego limitu 25 linii **[moja decyzja]**:
  ```
  signal: 3 nowe (techarena 2, self 1) · `house signal unread` · ostatni odbiór 12:31
  signal:   techarena [12:18] Kuba Nowak ↩ house: Spoko, a jaki wynik ma teraz baseline?
  signal:   self [11:58] Wojciech Fiołka: fr Q-012: B
  ```
  Najwyżej 3 najnowsze wiadomości, każda skrócona do 100 znaków. **Hook nie przesuwa kursora**: agent ma
  przeczytać całość przez `unread`, a podgląd to tylko zachęta. Gdy nowych nie ma: jedna linia `signal: brak
  nowych · ostatni odbiór 12:31` albo nic, jeśli Signal nie jest skonfigurowany;
- fail open jak dziś: wyjątek albo timeout daje jedną linię `signal: pominięto (…)`.

**`house night`**: `_signal_round` co iterację bez zmian w logice (już woła `inbox.process`, które teraz odbiera
wszystko). Raport poranny dostaje linię „Signal: N nowych wiadomości w M rozmowach, nieprzeczytane:
`house signal unread`”. Treści rozmów nie ma w raporcie, bo raport jest w repo konkursu, a repo może być wspólne
z drużyną albo publiczne. Noc nie reaguje na treść rozmów (nie-cel).

**`house doctor`** (`signal_status`):
- „ostatni odbiór” liczy z `signal/state.json` → `last_pull_ok` (zamiast mtime `inbox_done.json`);
- ostrzega przy `last_pull_error` (np. „urządzenie odłączone, `house setup` → ponowne linkowanie”), przy
  uszkodzonych liniach magazynu, przy kolizji aliasów `signal.toml` z konkursami, przy uprawnieniach plików innych
  niż 0600/0700 i przy timerze zainstalowanym, ale nieaktywnym.

**Instrukcje dla agentów** **[moja decyzja]**: jedna linia w `core/protocols/questions.md` i w profilu coordinatora
(„rozmowa z drużyną: `house signal read|unread|show`; nie wołaj `signal-cli` ani `Signal.receive`”). Hook sam
podsuwa komendę, więc nie potrzeba skilla ani więcej stałego kontekstu (zgodnie z dietą z P0).

## 10. Odbiór na żądanie, demon czy timer

| Wariant | Za | Przeciw |
|---|---|---|
| Tylko na żądanie (hook, `night`, komendy) | zero procesów w tle; tak jest dziś | bez sesji nikt nie odbiera; wiadomości czekają na serwerze, urządzenie może zostać odłączone; pierwszy odczyt po przerwie jest wolny |
| `signal-cli daemon` (JSON-RPC, socket) | natychmiastowy odbiór, jedno połączenie | demon trzyma blokadę konta, więc **każde** wywołanie `signal-cli` w `house` (send, listGroups) musi przejść na JSON-RPC; stały proces do pilnowania; wiadomości wypisane, gdy nikt nie słucha, giną |
| **Timer systemd użytkownika co 5 min + na żądanie** | prosty; magazyn zawsze świeży; urządzenie regularnie aktywne; reszta kodu bez zmian | opóźnienie do 5 min bez sesji; krótka blokada konta co 5 min (`send` czeka ≤ kilka s) |

**Rekomendacja** **[moja decyzja]**: **timer + odbiór na żądanie**. `house setup --signal-timer` instaluje
`~/.config/systemd/user/house-signal-pull.{service,timer}` (`OnUnitActiveSec=5min`, `Persistent=true`,
`house signal pull --timeout 5`), a `house doctor` sprawdza, czy timer działa. Bez timera wszystko działa jak dziś,
tylko rzadziej odbiera. Demona odkładamy do chwili, gdy opóźnienie 5 min okaże się realnym problemem. Wtedy
osobny spec zmieni klienta `Signal` na JSON-RPC.

Ile serwer Signala trzyma nieodebrane wiadomości dla urządzenia połączonego (z pamięci ok. 30 dni), trzeba
sprawdzić przed opisaniem tego w `doctor`.

## 11. Format danych, współbieżność, migracja (S1, S6)

**Układ** (katalog `~/.local/share/house/signal/`, albo `$HOUSE_DATA_DIR/signal/`; katalog 0700, pliki 0600):

| Plik | Zawartość |
|---|---|
| `messages.jsonl` | magazyn: jedno zdarzenie na linię, tylko dopisywanie (oprócz `compact`) |
| `state.json` | `next_seq`, `last_pull_ok`, `last_pull_error`, `dropped_last_pull`, `generation`, `migrated` |
| `cursors.json` | `{nazwa czytelnika: seq}` |
| `inbox_done.json` | klucze zdarzeń obsłużonych przez `house inbox` |
| `receive.lock` | jeden odbiór naraz (blokada długa, nieblokująca: zajęta = pomiń) |
| `store.lock` | dopisanie, nadanie `seq`, `compact` (blokada krótka, blokująca, timeout 5 s) |
| `inbox.lock` | jak dziś: jedno `house inbox` naraz |

**Zdarzenie** (`v: 1`; JSON z `ensure_ascii=False`, czyli polskie znaki i emoji jako UTF-8, a nie `\uXXXX`):
```json
{"v": 1, "seq": 124, "key": "JsNv4T…=:2f1c…:1794821855568", "kind": "message", "dir": "in",
 "chat": "JsNv4TnmFZy5bsRClyblxKkuIWvEGeNmKaqPbJuYM5M=", "chat_alias": "techarena",
 "ts": 1794821855568, "time": "2026-11-16T12:18:03+01:00", "received_at": "2026-11-16T12:18:41+01:00",
 "sender": {"name": "Kuba Nowak", "number": "+48…", "uuid": "2f1c…"},
 "text": "Spoko, a jaki wynik ma teraz baseline?",
 "quote": {"ts": 1794820000000, "author": "+48…", "text": "🤖 [house] Cześć! Jestem Claude Wojtka…"},
 "mentions": [], "attachments": [], "expires_in": 0, "target": null,
 "meta": null, "status": "ok", "raw": {"…": "pełna envelope z signal-cli"}}
```
- `chat`: `"self"` albo id grupy, jak dziś w `inbox.jsonl`. `chat_alias` jest zdenormalizowany na chwilę zapisu,
  żeby indeks wyszukiwania i `grep` nie musiały czytać konfiguracji.
- `key = chat:autor:ts` (autor: uuid, a bez niego numer). Tak Signal identyfikuje wiadomość (autor + timestamp).
  Czytelnicy deduplikują po `key`: przy dwóch liniach z tym samym kluczem wygrywa późniejsza.
- `seq`: rosnący numer nadawany pod `store.lock`. Kolejność dopisywania nie jest kolejnością `ts`, bo wiadomości
  opóźnione przychodzą później. Dlatego kursory i indeksowanie idą po `seq`, a wyświetlanie po `ts`.
- `target` (dla `reaction`/`edit`/`delete`): `{"ts": …, "author": …}`.
- `meta` (dla `out`): `{"origin": "ask", "contest": "techarena-fr", "question": "Q-012"}`.
- `raw` jest `null` w zdarzeniach `out` i migrowanych.

**Jeden plik, a nie plik na rozmowę** **[moja decyzja]**: „nowe ze wszystkich” i indeksowanie to jedno czytanie
po `seq`. Wolumen (tysiące linii na konkurs) nie wymaga podziału. Jeśli plik przekroczy ~50 MB, `compact` może
później dzielić go na miesiące; czytelnicy dostaną to przez jedną funkcję `iter_events()`.

**Współbieżność**
- `pull` bierze `receive.lock` bez czekania. Gdy jest zajęty, pomija odbiór („inny odbiór trwa”) i czyta magazyn.
  Dwa równoległe `signal-cli receive` i tak by się zablokowały na koncie.
- Każde dopisanie (z `pull` i z `Signal.send`) bierze `store.lock` na czas jednej linii. `send` nie czeka więc na
  cały odbiór, tylko na jedną linię.
- Czytelnicy nie biorą blokady: plik tylko rośnie, a niepełną ostatnią linię (bez `\n`) pomijają.
- `compact` pisze nowy plik obok, robi `fsync`, potem `rename` pod `store.lock`, a na końcu podbija `generation`
  w `state.json`. Czytelnik z otwartym starym plikiem kończy na starej wersji; indeks wyszukiwania po zmianie
  `generation` przebudowuje się.

**`compact`** (ręcznie albo z timera raz dziennie): czyści `text`/`raw`/`attachments` (razem z kopiami plików
w `signal/files/`) w wiadomościach z `delete`,
po czasie znikania i po `retention_days`. Usuwa zdarzenia rozmowy po `deny --purge`. Zostawia szkielet (`key`, `ts`,
`kind`), żeby cytaty i odpowiedzi Q-### dalej się dopasowywały. Decyzje już zapisane w `QUESTIONS.md`/`DECISIONS.md`
zostają (to repo konkursu, nie magazyn).

**Migracja** (`house signal migrate`, wywoływana też automatycznie przez pierwszą komendę `house signal`/`inbox`
po aktualizacji, pod `store.lock`, idempotentna dzięki `state.json` → `migrated`):
1. `inbox.jsonl` → zdarzenia `v: 1`, `kind: message`, `dir: in` (albo `own`, gdy `sender` = właściciel w `self`),
   `quote: {"ts": quote_ts, "text": null}`, `raw: null`, `meta: {"migrated": true}`, `seq` w kolejności `ts`.
   Oryginał zmienia nazwę na `inbox.jsonl.migrated-2026-11-16` i zostaje (nie kasujemy danych). Dziś to 3 linie.
2. `inbox_done.json` (lista `ts`) → `signal/inbox_done.json` z kluczami. Każdy stary `ts` mapuje się na klucz
   zmigrowanego zdarzenia o tym `ts`.
3. `sent.jsonl` (jeśli istnieje, a dziś nie istnieje) → zdarzenia `out` z `meta`. Do czasu migracji `inbox` czyta
   oba źródła, a potem tylko magazyn.
4. `groups.json` zostaje bez zmian jako cache dla konkursów. Migracja **nie** dopisuje grup do `signal.toml`.
   Zgoda to decyzja człowieka, więc `techarena_claude` Wojtek dodaje przez `allow` (pytanie 1).
5. Powitania wysłanego dziś ręcznie (brak 3) nie da się odtworzyć z danych, a odpowiedź kolegi na nie została
   odebrana starym kodem, więc nie ma `quote.text`. Dla tej jednej wiadomości `show` napisze „treść cytatu
   nieznana, odebrano przed migracją”. Od S1 problem nie wraca, bo każda nowa odpowiedź niesie tekst cytatu sama.

## 12. Punkty styku z `house search` (osobny spec)

- `messages.jsonl` to źródło dla indeksu. Indeks czyta przyrostowo po `seq`, zapamiętuje `generation`
  i przebudowuje się po `compact`.
- Pola do indeksu: `text`, `quote.text`, `sender.name`, `chat_alias`, `time`, `kind`. `raw` nie jest indeksowany.
- UTF-8 bez escapowania (`ensure_ascii=False`), czyli dokładnie to, czego brakuje w logach sesji Claude według
  roadmapy.
- Wynik wyszukiwania wskazuje `chat_alias` + `ts`, a agent czyta całość przez `house signal show <ts>`. To ta sama
  zasada co w P10: wynik prowadzi do źródła.
- Indeks dziedziczy prywatność magazynu: nic spoza dozwolonych rozmów, usunięte i wygasłe znikają po `compact`.
  Indeks leży lokalnie, w `~/.local/share/house/`, z tymi samymi uprawnieniami.

## 13. Błędy, bezpieczeństwo, testy, akceptacja

**Obsługa błędów**

| Sytuacja | Zachowanie |
|---|---|
| Signal nieskonfigurowany (`from_config` → None) | `pull` nic nie robi, kod 0; czytanie działa z magazynu, nagłówek „Signal wyłączony, pokazuję zapisane” |
| brak binarki / `OSError` przy starcie | jak wyżej + `last_pull_error`; `doctor` ✗ |
| timeout signal-cli | linie wypisane do tej pory są już zapisane (strumień); `last_pull_error: timeout`; następny odbiór dokończy |
| kod ≠ 0, stderr | `last_pull_error` = pierwsza linia stderr przycięta do 200 znaków (stderr signal-cli to komunikaty klienta, nie treść wiadomości); rozpoznane „not registered / unlinked” → `doctor` każe linkować |
| linia stdout, która nie jest JSON albo nie jest obiektem | pomijana; licznik `bad_lines` w `state.json`; **treść linii nie trafia do żadnego pliku** (mogła należeć do DM) |
| poprawny JSON, nieznany kształt w dozwolonej rozmowie | zdarzenie `kind: other` z `raw`; nic nie ginie, `reparse` dopisze obsługę |
| wyjątek w `extract` dla jednej envelope | pomijana tylko ta envelope (jak dziś `test_bad_envelope_does_not_lose_the_batch`), licznik |
| uszkodzona linia magazynu | czytelnicy pomijają; `doctor` podaje numer linii |
| `store.lock` niedostępny > 5 s | `send` i tak wysyła; zdarzenie trafia do `signal/pending-out.jsonl` (bez blokady, `O_APPEND`) i jest wciągane do magazynu przy najbliższym `pull` **[moja decyzja]** (log wysłanych nie może zależeć od blokady) |
| SIGTERM/SIGINT w trakcie `pull` | dokończenie bieżącej linii, zamknięcie signal-cli, wyjście |

**Bezpieczeństwo i prywatność**
- Filtr działa w pamięci, na sparsowanej envelope, **przed** zapisem do pliku, logu, stdout i licznika
  z nazwą. Z DM i niedozwolonych grup zostaje najwyżej liczba.
- Zbiór dozwolonych poszerza tylko człowiek: `allow` wymaga TTY (§5). Agent nie może też edytować
  `~/.config/house/signal.toml`, bo plik leży poza repo. Spróbowałby `Write`/`Edit` z absolutną ścieżką, więc
  `house-hook pre-tool` dostaje regułę blokującą zapis do `~/.config/house/` **[moja decyzja]**.
- Uprawnienia 0700/0600 ustawiane przy każdym tworzeniu i sprawdzane przez `doctor`.
- Treść rozmów nie trafia do repo konkursu (raporty nocne tylko z licznikami, §9) ani do commitów. Wyjątek to
  odpowiedzi Q-###, zapisywane w `QUESTIONS.md` jak dziś.
- Treść czytana przez agenta idzie do kontekstu modelu, czyli do API dostawcy. Członkowie dozwolonych grup o tym
  wiedzą (Wojtek, 2026-11-16), więc `allow` nie wysyła do grupy żadnej informacji.
- **Zaufanie do autora, nie do treści.** Prośba napisana w dozwolonej grupie przez jej członka to prośba człowieka
  z drużyny: agent może ją wykonać i odnotowuje ją w `PROGRESS.md`. Nie dotyczy to treści, którą członek tylko
  przekazał (przekazana wiadomość, plik, wklejony tekst ze strony): to dane, a nie polecenia. Prośby rozszerzające
  dostęp (dodanie grupy, wysłanie sekretów, zmiana konfiguracji poza repo) agent odrzuca niezależnie od autora,
  bo to decyzje Wojtka.
- Znikające i usunięte wiadomości są respektowane (§6, `compact`).

**Testy** (fałszywy `run`/`Popen` jak w `tests/test_inbox.py`; nigdy prawdziwe konto, `tests.helpers` jak dziś):
- Fixture envelope dla każdego rodzaju z §6: cytat z `quote.text`, sam załącznik bez tekstu, wzmianka `U+FFFC`,
  reakcja i jej cofnięcie, edycja, `remoteDelete`, znikająca, naklejka (`other`), własna w grupie (sync), od kolegi
  (`dataMessage`), „Notatka do siebie”.
- **Prywatność**: DM i wiadomość z niedozwolonej grupy o unikalnym tekście → po `pull`, `read --all`, `status`
  i `doctor` żaden plik w `$HOUSE_DATA_DIR` ani stdout nie zawiera tego tekstu, numeru nadawcy ani id grupy.
- **Strumień**: fałszywy proces wypisuje 3 linie i „umiera” (wyjątek / timeout) → 3 zdarzenia w magazynie.
- Niepoprawne linie stdout pomijane, licznik rośnie, treść nie jest zapisana.
- **Log wysłanych**: `Signal.send` wywołane bezpośrednio (z pominięciem `notify`) → zdarzenie `out`, `origin: python`.
  Porażka wysyłki → `status: failed`. Potwierdzenie z `inbox._reply` jest w magazynie.
- Dopasowanie cytatu do pytania przez zdarzenie `out` z `meta.question` (zastępuje `sent.jsonl`). Wszystkie
  obecne testy `test_inbox.py` przechodzą po przepięciu na magazyn.
- `inbox.process` odbiera także bez trwającego konkursu. Grupa z `signal.toml` jest zapisana, ale nie jest
  routowana jako odpowiedź.
- `read`: `-n`, `--since` (wszystkie formaty), `--from me`, `--grep`, `--thread`, `--json`, `--offline`;
  deduplikacja po `key`; złote wyjście dla przykładu z §8.
- Kursory: `unread` przesuwa, `--peek` nie przesuwa, dwa projekty mają niezależne kursory, a hook nie przesuwa.
- Locki: równoległe `pull` → drugi pomija odbiór; `send` w trakcie `pull` dopisuje się po jednej linii;
  `pending-out.jsonl` jest wciągany.
- `allow`/`deny` bez TTY → kod 2, plik bez zmian; z TTY i potwierdzeniem → wpis z `id`.
- Migracja: obecny kształt `inbox.jsonl` i `inbox_done.json`; drugie uruchomienie nic nie zmienia; kopia oryginału
  zostaje.
- `compact`: usunięta/wygasła/`--purge` traci treść, szkielet zostaje, `generation` rośnie.
- Pliki: załącznik z grupy jest kopiowany do `signal/files/<alias>/`, załącznik z DM nie; brak pliku u signal-cli
  → `path: null`; `send --file` przekazuje `--attachment`, odrzuca plik > 100 MB i ścieżki z listy zakazanych.
- `house-hook pre-tool` blokuje gołe `signal-cli … send|receive` w Bashu agenta.
- Hook: ≤ 4 linie Signala, linie ≤ 100 znaków, całość ≤ 25 linii, wyjątek → jedna linia „pominięto”.
- Test statyczny: poza modułem magazynu nikt w `houselib/` nie woła `.receive(`.

**Kryteria akceptacji** (każde odpowiada jednemu brakowi z §1):
- AC-1 (brak 1): po `house signal allow techarena_claude` wiadomość kolegi z tej grupy jest w `house signal read
  techarena` bez żadnego skryptu, także gdy żaden konkurs nie trwa.
- AC-2 (brak 2): `house signal show <ts>` dla odpowiedzi z cytatem pokazuje tekst cytowanej wiadomości,
  także gdy tej wiadomości nie ma w magazynie.
- AC-3 (brak 3): każde `send` (`notify`, `ask`, potwierdzenie `inbox`, `house signal send`, gołe `Signal.send`)
  jest w magazynie, a cytat na nie jest dopasowywany.
- AC-4 (brak 4): przerwanie `pull` po k wypisanych liniach zostawia k zdarzeń; żadna komenda czytająca nie wymaga
  ponownego `receive`.
- AC-5 (brak 5): `read` z filtrami i `unread` działają jak w §8; start sesji w repo konkursu i w repo `house`
  pokazuje licznik nowych.
- AC-6: test prywatności zielony; `allow` bez TTY odrzucone.
- AC-7: próba na żywo z Wojtkiem (jak Task 6 w planie P3). Kolega odpowiada cytatem w `techarena_claude`,
  `house signal read techarena` pokazuje cytat z tekstem, a `doctor` pokazuje świeży `last_pull_ok`.
- AC-8: zdjęcie wysłane przez kolegę do grupy jest plikiem w `signal/files/techarena/`, a ścieżkę pokazuje
  `read`; `house signal send techarena --file …` dostarcza plik do grupy.

## 14. Pytania do Wojtka

Odpowiedzi z Signala 2026-11-16 (Note to Self, cytatem):
- **Q1** ✓ `techarena_claude` (alias `techarena`) oraz grupa Collabothonu, gdy powstanie (`allow` po założeniu).
- **Q2** ✓ drużyna zaufana, wie, że czyta Claude, może do niego pisać.
- **Q3** ✓ hook tylko w repo `house` i konkursów.
- **Q4** ✓ timer systemd: tak.
- **Q5** ✓ 180 dni bez aktywności → archiwum: zip rozmowy + streszczenie decyzji dla agentów (§16).
- **Q6** ✓ agenci widzą całą Note to Self, ale nic poza Note to Self i wyznaczonymi grupami.
- **Q7** ✓ agent ma nie widzieć prywatnych rozmów ani plików → filtr przed zapisem + blokada folderu signal-cli (§6); plików z DM nie usuwamy (F7).
- **Q8** ✓ agent może pisać do grupy i wysyłać pliki.
- **Q9** ✓ 👍/👎 = tak/nie, każde serce = domyślna odpowiedź (§6).
- **Q10** ✓ wiadomość z Signala ma uruchamiać agenta („duża kontrola z pozycji Signala”) → §15; szczegóły F1–F6.

Pytania uzupełniające F1–F7 (o agencie z Signala) wysłane i odpowiedziane na Signalu 2026-11-16; odpowiedzi w §15.

Pierwotne pytania:

- **Q1. Grupy na start.** Czy dodać `techarena_claude` z aliasem `techarena`? Jakie jeszcze grupy (np. drużyna
  Collabothonu, jeśli nie będzie `notify.signal_group` konkursu)? (Domyślnie: tylko `techarena_claude`, alias
  `techarena`, dodajesz sam przez `allow`.)
- **Q2. Zgoda drużyny.** ✓ Rozstrzygnięte 2026-11-16: członkowie są zaufani, wiedzą, że czyta Claude, i mogą do
  niego pisać. `allow` nic nie wysyła (§2, §13).
- **Q10. Kto odpowiada, gdy nie ma sesji?** Skoro drużyna może pisać do Claude, to czy wiadomość w grupie bez
  otwartej sesji (np. „Claude, puść eksperyment X”) ma sama uruchamiać agenta (timer widzi nowe wiadomości →
  `claude -p` w repo konkursu, odpowiedź do grupy), czy czeka na najbliższą sesję lub iterację `night`?
  (Domyślnie: czeka; samodzielny bot to osobny spec.)
- **Q3. Hook w każdej sesji Claude.** Czy podsumowanie Signala ma się pokazywać we wszystkich sesjach na tej
  maszynie (hook w `~/.claude/settings.json`, także w sesjach służbowych), czy tylko w repo `house` i repo
  konkursów? (Domyślnie: tylko `house` + konkursy; komenda `house signal` działa wszędzie.)
- **Q4. Timer systemd.** Czy instalować `house-signal-pull.timer` co 5 min? (Domyślnie: tak, przez
  `house setup --signal-timer`, z pytaniem.)
- **Q5. Retencja.** Trzymać historię bez końca czy np. 180 dni (`retention_days`) dla grup spoza konkursów?
  (Domyślnie: bez końca; usunięte i znikające i tak są czyszczone.)
- **Q6. „Notatka do siebie” dla agentów.** Dziś zapisywane jest wszystko, co piszesz do siebie, także prywatne
  notatki niezwiązane z house. Czy `read self` / `unread` ma pokazywać wszystko, czy tylko wiadomości z prefiksem
  `🤖`, odpowiedzi cytatem na nie i `Q-###:`? (Domyślnie: wszystko, jak dziś zapis.)
- **Q7. Pliki z DM.** Pliki w obie strony są w zakresie (Wojtek, 2026-11-16), więc pobieranie zostaje włączone.
  signal-cli przy tym pobiera też pliki z prywatnych rozmów do `~/.local/share/signal-cli/attachments/` (tak jest
  już dziś). Agent ich nie widzi (dostaje tylko kopie z grup), ale leżą na dysku. Czy `pull` ma je usuwać od razu
  po odbiorze (oryginały zostają na telefonie)? (Domyślnie: nie ruszać.)
- **Q8. Agent piszący do grupy.** ✓ Rozstrzygnięte 2026-11-16: agent może, z prefiksem `🤖` i logiem; może też
  wysyłać pliki (§7).
- **Q9. Reakcja jako odpowiedź.** Czy 👍 / 👎 pod pytaniem tak/nie ma być odpowiedzią na Q-###? (Domyślnie:
  nie, tylko odpowiedź tekstem.)

## 15. Agent uruchamiany z Signala (S7)

Cel (Wojtek, Q10): z telefonu da się zlecić pracę i dostać wynik, bez komputera. ✓ = odpowiedź Wojtka,
F1–F7 i doprecyzowanie F5 odpowiedziane 2026-11-16 na Signalu:

- **Odbiór**: przy włączonym S7 timer odbiera co 1 min zamiast 5 (`OnUnitActiveSec=1min`) **[moja decyzja]**.
  Demon signal-cli dalej odłożony (§10); minuta opóźnienia przy zleceniu z telefonu wystarcza.
- **Wyzwalacz** ✓ (F1, Wojtek): każda wiadomość w dozwolonej grupie lub Note to Self, która **gdziekolwiek**
  zawiera `@claude` (bez rozróżniania wielkości liter; także jako prawdziwa wzmianka Signala, gdyby konto Claude
  było osobnym numerem). Komendy `/status`, `/lb`, `/stop` działają bez `@claude`. Zwykła rozmowa drużyny nie
  odpala agenta.
- **Pierwsza wiadomość w czacie** ✓ (F1, Wojtek): `house signal allow` (i włączenie grupy konkursu) wysyła do
  czatu krótkie powitanie, raz na czat (zapisane w `state.json`), np.:
  „🤖 [house] Cześć, tu Claude Wojtka. Napisz `@claude` + zadanie, a wezmę się do pracy i odpiszę tutaj.
  `/status` co robię, `/stop` przerwij. Odpowiedź na moje pytanie: cytat, 👍/👎 tak/nie, ❤ domyślna.”
  Treść w szablonie `core/templates/signal-hello.md`, żeby dało się ją zmienić bez kodu.
- **Kto** ✓ (F2, Wojtek): każdy członek dozwolonej grupy (drużyna zaufana, Q2).
- **Gdzie** ✓ (F5, Wojtek): grupa konkursu → repo konkursu; Note to Self i grupy spoza konkursów → repo `house`.
  **Jedna grupa dla kilku konkursów** (`techarena_claude` obsługuje kilka TechAren) **[moja decyzja]**: kilka
  speców może mieć ten sam `notify.signal_group` (już dziś `_group_ids` mapuje grupę na listę konkursów), a
  `signal.toml` może przypiąć do grupy listę konkursów (`contests = ["techarena-fr", "techarena-fi"]`).
  **Zasada (Wojtek, 2026-11-16): to house oznacza w każdej swojej wiadomości, którego konkursu dotyczy
  (prefiks `🤖 [fr]`), a odpowiedź cytatem na nią przypisuje zlecenie do tego konkursu.** Wybór repo:
  1. odpowiedź cytatem na wiadomość house → konkurs z jej prefiksu (zapisany w `meta.contest` zdarzenia `out`);
  2. nowa wiadomość bez cytatu, gdy do grupy przypięty jest dokładnie jeden trwający konkurs → ten konkurs;
  3. nowa wiadomość bez cytatu z aliasem (`@claude fr: …`) → ten konkurs **[moja decyzja]**, skrót dla
     drużyny;
  4. inaczej house odpisuje osobną wiadomością na każdy konkurs („🤖 [fr] odpowiedz cytatem, jeśli to do fr”,
     „🤖 [fi] …”), a cytat na jedną z nich wybiera repo (czyli wraca do punktu 1); nic nie startuje wcześniej.
  Wiadomości house w takiej grupie zawsze mają prefiks aliasu.
- **Co wolno** ✓ (F3, Wojtek): to samo co worker w nocy: osobny worktree, kod, testy, eksperymenty w limitach
  specu, scalenie tylko przez bramkę `house check`. Komendy bez agenta (`/status`, `/lb`, `/stop`) obsługuje
  house sam, bez modelu.
- **Limity** ✓ (F4, Wojtek): 1 agent z Signala naraz na repo, reszta w kolejce (house odpisuje pozycję w
  kolejce); limit czasu na zlecenie 2 h; `/stop` przerywa; bez limitu dziennego.
- **Kolizje** ✓ (F6, Wojtek): równolegle z sesją na komputerze i z nocą, każde w swoim worktree; noc ma
  pierwszeństwo do GPU.
- **Odpowiedź**: start („🤖 przyjąłem, pracuję w worktree X”), wynik na końcu (tekst + pliki, §7), błędy od razu.
  Zlecenie i wynik idą do `PROGRESS.md` repo.

## 16. Archiwum nieaktywnych rozmów (S8)

Rozmowa (grupa spoza trwającego konkursu) bez nowej wiadomości przez 180 dni (Q5) jest archiwizowana przez
`compact` **[moja decyzja co do formatu]**:
- `signal/archive/<alias>-<od>-<do>.zip`: zdarzenia rozmowy (`jsonl`, bez `raw`) + kopie plików z `files/`.
- `signal/archive/<alias>-<od>-<do>.md`: streszczenie dla agentów, pisane przez model na podstawie całej rozmowy:
  kto i po co (1–2 zdania), **podjęte decyzje** (data, kto, co, dlaczego, z `ts` źródłowej wiadomości), ustalenia
  techniczne i wyniki (np. wyniki LB, co zadziałało, co nie), otwarte wątki, lista plików z opisem. Format jak
  `DECISIONS.md`, żeby `house search` i agenci mogli go czytać bez rozpakowywania zipa.
- Po archiwizacji zdarzenia znikają z `messages.jsonl` (zostaje szkielet dla dopasowań Q-###), alias zostaje
  w `signal.toml`, więc nowa wiadomość w grupie po prostu zaczyna nową historię.

## 17. Co zostaje dla wykonawcy

Nazwa modułu magazynu (`houselib/signalstore.py` albo rozbudowa `inbox.py`), dokładne mapowanie pól
`editMessage`/`reaction`/`remoteDelete`/`mentions` z signal-cli 0.14.10 (zweryfikować na prawdziwej envelope
z `raw`, a nie z dokumentacji), składnia `--since`, treść jednostek systemd, układ testów. Przed budową plan
(superpowers:writing-plans) z pakietami S1–S8.

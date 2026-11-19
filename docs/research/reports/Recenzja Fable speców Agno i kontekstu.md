# Recenzja speców Agno (A0–A7) i krótkiego kontekstu (K0–K4)

_2026-11-17. Recenzent: Claude (Fable), nie autor speców. Przeczytane: oba specy z 2026-11-16, cztery notatki
z `research_notes/Research speców Agno i kontekstu/`, mini-research o zapisach oraz kod na `main` (`fe7f3f8`).
Niczego w repo nie zmieniałem poza tym plikiem._

Oznaczenia: **[SPEC]** = zmienić spec przed planem, **[IMPL]** = uwaga dla wykonawcy (spec może zostać).
Wpływ: wysoki / średni / niski. Odwołania `plik:linia` dotyczą stanu `main`.

## 1. Werdykt

Kierunek jest dobry, a zasada „co da się odczytać z zapisu, odczytuje kod” jest warta budowy. Zmieniłbym jednak
cztery rzeczy, zanim powstanie pierwszy plan:

1. **Źródło zapisu dla sesji bez człowieka.** Specy każą szukać zapisów w `~/.claude/projects` i `~/.codex/sessions`.
   Dla `night`, evali, rutyn i zadań Signal prostsze i pewniejsze jest to, co `signalagent` już robi: strumień
   `--output-format stream-json` zapisany do własnego logu. To usuwa większość ryzyka A0.
2. **Zakres.** Trzynaście pakietów to za dużo jak na system, w którym `house night` nie ma jeszcze ani jednego
   prawdziwego przebiegu (w obu konkursach z `contests.yaml` nie ma żadnego `reports/night-*.md`). A6 i A7 bym
   odłożył, A4 i K0 zmniejszył, 9b przesunął za 9a.
3. **Kolejność.** Najtańsza i najpewniejsza dźwignia kosztowa (obniżony próg automatu kompakcji) stoi dziś za
   A0 → A1 → K0 → A2. Nie zależy od żadnego z nich i może wejść pierwsza.
4. **Dziury w A1/A2**, przez które wykonawca zbuduje coś, co nie zadziała: „Ślepe uliczki” nie mają trwałego
   źródła, a evale uruchomione według speca wyślą wiadomości na prawdziwy Signal.

Research jest solidny w szczegółach formatów (sprawdziłem lokalnie: rekordy `cost-state`, `compact_boundary`
i katalogi `subagents/` istnieją; `claude --session-id` i `codex exec --json` też). Jego słabość: prawie każda
rekomendacja coś dokłada, żadna nie tnie. Po przyjęciu wszystkich „must” A0 staje się klonem ccusage, a A3 małym
frameworkiem evali.

## 2. Ustalenia o wysokim wpływie

### W1. [SPEC] Sesje bez człowieka: log strumienia zamiast szukania zapisu w katalogu domowym (A0, A1, K0, A3)

Spec Agno §6 („Znajdowanie sesji”) zakłada, że `night` nada sesji identyfikator i potem znajdzie plik w
`~/.claude/projects/…`. Tymczasem:

- `night.worker_command` (`houselib/night.py:286-289`) uruchamia `claude -p` bez `--output-format`, a log workera
  (`night.py:310-314`, ścieżka z `night.py:510`) zawiera dziś tylko końcową odpowiedź.
- `signalagent._claude_argv` (`houselib/signalagent.py:505-510`) już uruchamia `claude -p … --output-format
  stream-json --verbose` do pliku, a `_result` (`signalagent.py:738-749`) czyta z niego linię `result` z kosztem.
- `bin/codex-task` uruchamia `codex exec` bez `--json`; flaga istnieje w zainstalowanym 0.166.

Propozycja: sesje, które startuje `house` (`night`, `house eval`, rutyny, zadania Signal, `bin/codex-task`), piszą
strumień zdarzeń do logu, który `house` i tak już trzyma (`.house/tmp/night/…`, katalog zadania Signal). A0
dostaje adapter „strumień” jako ścieżkę główną dla tych sesji. Zyski:

- znika znajdowanie sesji, kodowanie ścieżki projektu i zależność od `cleanupPeriodDays` (domyślnie 30 dni, po
  których relacja „wyliczana na żądanie” przestaje istnieć);
- znika problem tmpfs w Dockerze (rekomendacja 6 researchu o zapisach): log strumienia leży poza kontenerem;
- cała używana ścieżka Codexa w `house` przechodzi przez `bin/codex-task` i `review.py:164`. Adapter plików
  `rollout-*` (najgorzej udokumentowany fragment A0: narastające liczniki, forki, `.zst`) nie jest wtedy
  potrzebny w pierwszej wersji. Zostawiłbym go jako „później, jeśli ktoś zacznie pracować w Codexie
  interaktywnie”.

Czytnik `~/.claude/projects` zostaje, ale tylko dla sesji interaktywnych (coordinator, K0, retro).
Do sprawdzenia przed planem: czy `codex exec --json` podaje w 0.166 zużycie tokenów i kody wyjścia komend, oraz
czy `stream-json` niesie `usage` w każdym rekordzie `assistant` tak jak zapis jsonl.

### W2. [SPEC] „Ślepe uliczki” nie mają skąd się wziąć (A1 §7 kontra A2 §8)

- §8 mówi, że „Ślepe uliczki” to suma ze wszystkich prób z `.house/attempts.jsonl`. Wiersz rejestru w §7 ma
  zadanie, numer, narzędzie, sesję, czasy, commity i wynik bramki. Ślepych uliczek w nim nie ma.
- Jedynym źródłem jest sekcja raportu workera (`core/templates/house/reports/TEMPLATE.md:19`, „Tested and did
  not work”). Raport powstaje w kroku 5 profilu, po GREEN (`core/agents/coder.md:24-25`). Próba zakończona
  timeoutem albo zabiciem (czyli ta, po której brief jest najbardziej potrzebny) raportu nie ma.
- Raport ma jedną ścieżkę na zadanie (`reports/T###.md`), więc próba 2 nadpisuje raport próby 1.

Zmiana: (a) koder dopisuje ślepe uliczki do raportu **w trakcie pracy**, nie na końcu (wzorzec „plik postępu”
z wytycznych Anthropic, które research cytuje w Q1); (b) `night` po każdej próbie kopiuje sekcję do wiersza
rejestru albo do `reports/T###-attempt-N.md`; (c) brief oznacza te wpisy jako „według raportu workera próby N”,
bo to deklaracja, nie obserwacja. Inaczej test akceptacyjny „trzecia próba zawiera ślepe uliczki z prób 1 i 2”
przejdzie tylko na fixture, w którym worker grzecznie napisał raport.

### W3. [SPEC] Evale według §9a dotkną prawdziwego Signala i nie znajdą konkursu (A3)

Trzy konflikty z kodem, których spec nie adresuje:

- **Signal.** Kopia konkursu po `house adapt` ma hooki. `SessionStart` woła `hook._signal_lines`
  (`houselib/hook.py:147-166`): odbiór, `inbox.process` dla wszystkich trwających konkursów i `inbox.deadlines`,
  które potrafi wysyłać przypomnienia. `PostToolUse` woła `notify.ask_new` po każdej zmianie `QUESTIONS.md`
  (`hook.py:80-93`). Przypadek smoke „pytanie zamiast czekania” wyśle więc sztuczne Q-### do Wojtka. Runner musi
  ustawić to samo środowisko co `tests/helpers.py` (`HOUSE_NOTIFY_CONFIG`, `HOUSE_SIGNAL_CONFIG`,
  `HOUSE_DATA_DIR` na ścieżki tymczasowe). To lepsze niż nowa flaga `HOUSE_EVAL=1` z researchu (pkt 4), bo
  mechanizm już istnieje i jest przetestowany.
- **Nazwa konkursu.** `cli.contest_dir` (`houselib/cli.py:42-56`) rozwiązuje nazwę wyłącznie jako
  `HOUSE_ROOT/contests/<nazwa>`. Agent w „kopii w katalogu tymczasowym” nie wykona `house check <konkurs> T###`.
  Runner potrzebuje tymczasowego korzenia `house` (kopia lub `git worktree` repo na badanym commicie)
  z fixture'em w jego `contests/`. To przy okazji daje `--baseline <commit>` z researchu (pkt 14) prawie za darmo
  i gwarantuje, że eval bada profil z tego commita, a nie z katalogu roboczego.
- **Blokada.** „Odmawia startu, gdy trwa `house night`”: `night` nie zapisuje dziś żadnego pliku blokady ani pid
  (brak w `night.py`). „Konkurs w toku wg harmonogramu”: `inbox.running` (`houselib/inbox.py:90-107`) uznaje
  konkurs bez `timeline.end` za trwający, więc jeden taki wpis w `contests.yaml` blokuje evale na stałe. Spec
  powinien podać: plik blokady `night` (z `flock`) i definicję „w toku” = `start ≤ teraz ≤ end`, oba ustawione.

### W4. [SPEC] `night` nie ma danych, a rejestr prób pomija główną ścieżkę pracy (A1, K4)

W obu zarejestrowanych konkursach nie ma ani jednego raportu nocy. Prawdziwa praca szła przez `/house-run`
(coordinator i podagenci). A1 buduje rejestr tylko dla `night`, a sesje interaktywne obsługuje ręcznym
`--session <id>`. K4 chce porównania „przed i po”, ale „przed” nie istnieje.

Zmiana:
- wiersz rejestru zapisuje wspólna funkcja wołana z **bramki** (`cli.cmd_merge` i `night._finish`,
  `night.py:613-657`), bo odmowa `house merge` w sesji interaktywnej to też próba. `night` dokłada do wiersza
  log sesji;
- przed planem A2 uruchomić jedną prawdziwą noc na małym konkursie (choćby powtórce) na obecnym kodzie. To daje
  punkt odniesienia dla K4 i pokaże, czy problemem są w ogóle kolejne próby, czy raczej timeouty i odmowy bramki;
- K4 powinien wprost napisać, skąd bierze stan „przed”: z tej nocy i z jednorazowego zrzutu `house cost` zrobionego
  **teraz**, bo zapisy sesji sprzed 30 dni znikną.

### W5. [SPEC] Próg automatu kompakcji może wejść od razu (K2, kolejność)

K2 stoi w kolejności za K0 i A2. Jego najważniejsza część to jedna linia konfiguracji. Research (pkt 17)
tłumaczy szczyt 897K z §4: na modelach z oknem 1M automat działa dopiero przy ~967K. Własna tabela speca (§4)
mówi, że przy 400K kompakcja zwraca się po 2–3 wywołaniach. Decyzja „automat przy ~200K” jest odporna na
niepewność wag: jeśli limit subskrypcji liczy odczyt z cache drożej niż API (research o zapisach, pkt 14),
kompakcja zwraca się jeszcze szybciej.

Podział:
- **K2a (teraz, bez zależności):** `house adapt` ustawia próg automatu ~200K i próg kompakcji przy bezczynności.
  Wchodzi razem z samym hookiem `PreCompact` i `SessionStart` po kompakcji.
- **K2b (po K0, być może nigdy):** przypomnienia w paśmie 120–200K. To jedyna część, która naprawdę potrzebuje
  danych, i jedyna z nierozstrzygniętym mechanizmem (patrz N3).

Nazwy ustawień (`autoCompactWindow`, `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS`) znam tylko z researchu. Potwierdzić
uruchomieniem, jak spec już zastrzega w §13.

### W6. [SPEC] Granica prywatności A0 jest postawiona w złym miejscu (A0 §6, A1 §7)

Spec dzieli A0 na warstwę z treścią i „widok bez treści”, ale ten widok zawiera `tool_call` z wejściem. Wejście
`Write` to cała treść pliku, `Edit` to fragmenty pliku, `Bash` to komenda z ewentualnym tokenem w nagłówku,
`Agent` to cały prompt. Jednocześnie A1 potrzebuje treści wyniku (ogon błędu, słowo GREEN/RED, tekst odmowy
guarda). „Bez treści” nie jest więc własnością warstwy, tylko obietnicą, której nikt nie egzekwuje.

Zmiana:
- jeden parser, zdarzenia z dostępem do surowego rekordu. Warstwy „dla wyszukiwania” nie projektować teraz:
  jej jedyny odbiorca nie istnieje, a jego potrzeby zdefiniuje jego własny spec;
- granica leży **przy zapisie**: wszystko, co trafia do `.house/` albo na stdout komend `house`, przechodzi przez
  jedną funkcję (biała lista pól na narzędzie, przycięcie, skaner sekretów). Test akceptacyjny: zapis syntetyczny
  z „kanarkami” w treści plików, promptach i wynikach; żaden kanarek nie pojawia się w wyjściu A1, K0 ani A3;
- biała lista: `Bash.command` (po skanerze, przycięta), `file_path` dla narzędzi edycji, `pattern`/`path` dla
  Grep/Glob, `subagent_type` i `description` dla Agent, nazwa dla Skill. Nic więcej.

Nie zgadzam się tu z researchem (zapisy, pkt 9), który proponuje zostawić z komendy Bash tylko pierwszy token.
Relacja bez argumentów (`pytest tests/x.py -k y`, `house check … --worktree …`) traci większość wartości dla
briefu i retro. Skaner plus przycięcie wystarczą dla sesji konkursowych, których repo i tak widzi drużyna.

[IMPL] „Skaner sekretów z `guard`” jako funkcja na tekście nie istnieje. Jest `guard._secret_file` (na pliku,
`houselib/guard.py:322`) i lista `export.SECRET_RES` (`houselib/export.py:19-31`). Potrzebna mała funkcja
`redact(text)`, która zamienia trafienie, a nie odrzuca cały fragment.

### W7. [SPEC] Co z nowych plików w `.house/` trafia do gita (A1, A4, K3, research A2 pkt 7)

`merge._commit_house` robi `git add -A -- .house` przy każdym scaleniu (`houselib/merge.py:60-64`, wołane w 213,
266, 276). Każdy nowy plik w `.house/` trafia więc do repo konkursu i na `origin`, chyba że jest ignorowany.
Szablon `.house/.gitignore` zawiera tylko `tmp/`, a `scaffold.install_core` odświeża wyłącznie playbooki
i protokoły, więc istniejące konkursy nie dostaną nowych wpisów.

Spec musi rozstrzygnąć dla każdego pliku:
- `.house/out/*.log`: spec mówi „ignorowane przez git”, ale w istniejących konkursach nie będą. Najprościej
  `.house/tmp/out/` (już ignorowane, bez migracji);
- `.house/attempts.jsonl`: śledzony czy nie? Jeśli śledzony, to bez bezwzględnej „ścieżki zapisu” (zawiera nazwę
  użytkownika i układ katalogu domowego; wystarczy identyfikator sesji i względna ścieżka logu). Trzeba też
  dopisać go do rzeczy, których worker nie rusza;
- `.house/reports/evidence-T###-N.md`: śledzone razem z raportami, więc widzi je drużyna. To w porządku tylko po W6;
- `.house/compactions.jsonl` (propozycja researchu): do `tmp/`.

## 3. Ustalenia o średnim wpływie

### S1. [SPEC] A1: fakty najpierw ze stanu, potem z zapisu

Kilka „faktów ważnych dla `house`” da się policzyć bez parsera zapisów, niezależnie od narzędzia:
- czy worker pisał Q-###: różnica `QUESTIONS.md` między początkiem a końcem próby;
- zmienione pliki i commit: `git diff --stat` (spec już to ma);
- ostatni wynik `house check`: `house` sam go zapisuje w `.house/checks/T###.json`;
- naruszenia: `guard.diff_violations` na gałęzi.

Zapis jest potrzebny do kolejności (check po ostatniej edycji), powtórzeń i odmów guarda w trakcie. Odmowy
guarda istnieją tylko dla Claude Code; Codex nie ma hooków (`guard.py:1-5`), więc dla niego to pole ma być
„nie dotyczy”, nie „0”. Relacja zbudowana w tej kolejności działa częściowo nawet wtedy, gdy format zapisu się
zmieni.

### S2. [SPEC] Brief dla workera Codexa dziś nie istnieje (A2)

`night.worker_command` dla `coder-codex` zwraca `[bin/codex-task, <plik zadania>, <worktree>]`
(`night.py:283-284`). Argument `previous` nie jest przekazywany, więc kolejna próba Codexa nie dostaje dziś
nawet 600 znaków. Spec wymienia `bin/codex-task` tylko przy zmianie szablonu raportu. Trzeba dopisać interfejs
(np. `--brief-file`) i to, że warunek ukończenia dla Codexa jest inny: Codex nie commituje i nie ustawia
`status: review`, robi to `night._finish`.

### S3. [SPEC] Hook `SessionStart` po kompakcji: osobny tryb, nie dopisek (A2, research pkt 8 i 9)

`hook.session_start` ma kontrakt „najwyżej 25 linii” (`hook.py:15`, `hook.py:270`), zaczyna od odbioru Signala
z limitem 10 s (`hook.py:156-164`) i kończy przypomnieniem o briefingu. Spec dokłada „5 ostatnich D-###”,
a research pełny tekst ustaleń człowieka i listę zmienionych plików, w ramach 10 000 znaków. To się nie mieści
w 25 liniach i wypchnie linie Signala albo ostatnią linię.

Zmiana: rozgałęzienie po polu `source` zdarzenia. Dla `compact`: bez odbioru Signala (sesja trwa, timer i tak
odbiera co 5 minut), bez linii o briefingu, z własnym budżetem znaków i z ustaloną kolejnością przycinania
(ustalenia człowieka pierwsze). Dla zwykłego startu nic się nie zmienia. Zgadzam się z researchem, że tytuły
D-### to za mało; pełny tekst tylko dla wpisów z `Kto: człowiek` i odpowiedzi na Q-###.

Zdanie w §8 „trzeba to zrobić po scaleniu tamtego [speca grup Signala]” jest nieaktualne: PR #8–#11 są scalone,
`hook.py` już zawiera `signal_summary`. Usunąć, żeby wykonawca nie czekał.

### S4. [SPEC] Limit 8K w sesji interaktywnej nie jest egzekwowalny (A2, zgodnie z researchem pkt 7)

Streszczenie pisze Claude Code, `house` daje tylko instrukcje. Trzy z czterech testów akceptacji A2 dotyczą
tekstu, który składa kod (`night`), i są w porządku. „Przycinanie od końca” dla coordinatora trzeba wykreślić
albo zamienić na pomiar po fakcie. Hook `PostCompact` z researchu przyjąłbym tylko jako licznik (długość, liczba
sekcji) do `tmp/`, bez lintu „wpisy bez źródła”: to heurystyka na tekście modelu, która będzie dawała fałszywe
alarmy.

### S5. [SPEC] Szablon raportu: język i los dwóch sekcji (A2)

- §8 mapuje „Goal → Cel” itd. Raporty są po angielsku z decyzji D21 i z profili (`coder.md:24`, `bin/codex-task`).
  Nie wiadomo, czy nagłówki mają zostać angielskie. Wykonawca zgadnie. Proponuję: angielskie nagłówki, polskie
  nazwy tylko w specu.
- Obecne sekcje „Evidence” i „Result vs acceptance” (`TEMPLATE.md:13-17`) nie występują w mapowaniu. „Evidence”
  pisana przez workera stoi w sprzeczności z zasadą A1. Proponuję: „Result vs acceptance” wypełnia kod z
  `house check`, „Evidence” znika albo staje się odnośnikiem do `house evidence`.
- Istniejące konkursy mają stary szablon (nie jest odświeżany przez `house adapt`). [IMPL] Parser sekcji w
  `night` musi przyjmować stare i nowe nagłówki.

### S6. [SPEC] K3: wklejony dokument jest już w kontekście, a „dosłownie” przez model nie jest dosłownie

`/house-start` ma zapisać wklejone materiały „dosłownie” do `.house/sources/`. Dwa problemy:
- tekst wklejony do rozmowy już siedzi w kontekście tej sesji. Zapis do pliku pomaga dopiero kolejnym sesjom;
- zapis przez narzędzie Write to przepisanie dokumentu tokenami wyjścia (waga ×5 według §4) bez gwarancji
  wierności. Regulamin 500K znaków to ~125K tokenów wyjścia.

Zmiana: materiały trafiają do `.house/sources/` **poza modelem**: człowiek wrzuca pliki albo woła
`house sources add <konkurs> <plik|url|->`, a `/house-start` dostaje ścieżki. Wklejka zostaje jako wyjście awaryjne
dla krótkich tekstów. Do tego: `house sources` zapisuje sumę kontrolną, bo `spec.yaml` ma wskazywać zakresy linii,
a każda edycja pliku je przesuwa. Research tego nie zauważył.

### S7. [SPEC] A3: model, sędzia, skill `/house-improve`

- „Domyślnie model z `night.model`” pasuje do kodera. Przypadki coordinatora i skilli (`/house-plan` zamraża
  kryteria) dotyczą sesji głównej, która jest na Opusie (`core/agents/coordinator.md:3`). Domyślny model powinien
  wynikać z `model_tier` profilu, a dla skilli z pola w przypadku.
- `criteria` (sędzia LLM) wyciąłbym z pierwszej wersji. Pięć przypadków smoke z akceptacji da się sprawdzić
  deterministycznie. Research (pkt 10) sam mówi, że sędzia nie może blokować przed kalibracją na 20–30 zapisach,
  czyli w v1 byłby kodem bez użytku.
- 9b (`/house-improve`) ma sens dopiero, gdy istnieje kilkanaście przypadków i co najmniej jeden cykl
  „regresja wykryta → naprawiona”. Wcześniej skill będzie generował scenariusze dla pustego zestawu. Przesunąć za
  9a i za pierwszy konkurs; zbiór wstrzymany z researchu (pkt 15) wtedy przyjąć.
- Z researchu przyjąłbym jako wymagane: sprawdzenia stanu końcowego (pkt 2), `infra_error` (pkt 5), przypadek
  negatywny (pkt 9), uczciwy opis mocy n=3 (pkt 6). To zmienia wynik evalu. Reszta (Wilson, `case_hash`, eksport
  do plugin eval, `order`) to dodatki na później.

### S8. [SPEC] A4: mniejszy, niż opisano, i bez duplikowania `house exp`

- Research (A4 pkt 1) ma rację: Claude Code sam zapisuje długi wynik udanej komendy do pliku. Uzasadnienie A4
  zawęża się do komend zakończonych błędem, ogona w kopercie i wspólnego miejsca dla Codexa.
- W konkursach optimize trening idzie przez `house exp run`, które już trzyma log (`exp.py:52`, `exp.py:442-445`),
  ale obcięty do ostatniego 1 MB i bez czytnika. Zamiast dwóch magazynów: `house out show` czyta też logi
  eksperymentów (`show E-###`).
- „Sprzątane przez `house exp prune`” jest mylące: `prune` usuwa worktree, nie logi (`exp.py:773`). Zostawić
  samo `house out prune`.
- Składnia `house out -- <komenda>` nie ma argumentu `<konkurs>`, który mają wszystkie inne komendy. Spec
  powinien powiedzieć, że konkurs wynika z `HOUSE_DIR` albo z katalogu. [IMPL] `cli.main` obsługuje `--` tylko
  dla `exp` (`cli.py:702-705`).
- [IMPL] `house out -- rm …` chowa komendę przed `guard.check_bash`, który patrzy na pierwszy token segmentu
  (`guard.py:286-316`). Sprawdzenie po fakcie przy scalaniu to łapie, ale `out` warto dopisać do rozpoznawanych
  opakowań. W zadaniach Signal komenda `house` jest zablokowana (`guard.py:582-583`), więc A4 tam nie działa
  i ma nie działać.

Priorytet A4 obniżyłbym do niskiego i zbudował wersję minimalną: koperta, `show --grep`, `prune`. Stronicowanie
i limity z Agno dopiero, gdy ktoś ich użyje.

### S9. [SPEC] K0: sesje konkursu trzeba znajdować po `cwd` w rekordach, nie po nazwie katalogu

„Katalog projektu po ścieżce repo konkursu” nie zadziała w dwóch przypadkach, które już występują:
- konkurs adoptowany jest symlinkiem (`contests/grok-bot-wro-2026` → `~/studia/hackathony/…`), a zapisy leżą pod
  ścieżką rzeczywistą, nie pod `…-house-contests-<nazwa>`;
- workery `night` i podagenci w worktree mają `cwd` w `.worktrees/…`, czyli inny katalog projektu.

Reguła: sesja należy do konkursu, gdy `cwd` z rekordu, po rozwiązaniu symlinków, leży pod rozwiązanym korzeniem
konkursu. K0 ograniczyłbym do jednej tabeli (`house cost <konkurs>`) i widoku jednej sesji. Koszt per zadanie
przyjdzie z rejestru prób, gdy ten będzie miał dane.

Z researchu przyjąć koniecznie: sumowanie podagentów (pkt 1), walidację względem `cost-state` (pkt 3), zdarzenie
kompakcji z `preTokens`/`postTokens` (pkt 8). Te trzy decydują o tym, czy liczby są prawdziwe.

### S10. [SPEC] A7: jedna komenda zamiast silnika rutyn

Zakres zatwierdzony przez Wojtka to „harmonogram i jedna rutyna, która pisze raport”. Format `routines/*.json`,
`house routine due`, stan, zaległe przebiegi i blokady to silnik dla jednego odbiorcy. Prościej: jedna komenda
(np. `house upkeep`), jeden timer systemd `OnCalendar=weekly` z `Persistent=true`, raport do pliku. Format rutyn
zaprojektuje spec trybu dziennego, gdy będą co najmniej dwie. Zgadzam się z researchem (A7 pkt 1 i 4): nie
kopiować `TIMER_UNIT` z `houselib/setup.py:144-154`, a część deterministyczną liczyć w Pythonie.
Pakiet odłożyłbym za pierwszy konkurs z A1–A3.

### S11. [SPEC] A6 wyciąć albo odłożyć

`house memory lint` dotyczy `~/.claude/projects/*/memory/`, czyli nie stanu `house`, tylko pamięci Claude Code
na maszynie, na której większość projektów jest służbowa (research A6 pkt 2). Dziś pamięć projektu `house` ma
dwa wpisy. Problem jest hipotetyczny („za jakiś czas pojawią się duplikaty”), a ryzyko prywatności realne.
Jeśli ma zostać, to tylko sprawdzenie indeksu (`MEMORY.md` bez pliku, plik bez wpisu, limit 200 linii) dla
projektu `house`, bez wykrywania „prawie duplikatów”.

## 4. Ustalenia o niskim wpływie

- **N1. [SPEC] A5** jest tani i w porządku. Przyjąć z researchu pkt 1 (parser tylko wewnątrz bloków `## D-###`;
  szablon `DECISIONS.md` sam zawiera linię `- **Kto:** człowiek | judge | coordinator`) i pkt 3 (jedna linia na
  wpis). `Zastępuje:` (pkt 2) dopiero, gdy pierwsza decyzja zostanie odwrócona. Uwaga: `merge._review_gate` sam
  dopisuje wpisy D-### z `Kto: coordinator` (`merge.py:158-164`), więc `--by` musi je poprawnie klasyfikować.
- **N2. [SPEC] Dwa numery próby.** §7: numer w rejestrze liczony przez wszystkie noce, a `MAX_ATTEMPTS` dalej
  w obrębie nocy (`night.py:38`, `night.py:481`). Raport nocy, nazwa logu (`night.py:510`) i
  `evidence-T###-N.md` muszą mówić, którego numeru używają. Proponuję wszędzie numer z rejestru, a licznik nocy
  tylko wewnętrznie. Zadanie zablokowane po 3 próbach wraca do `night` dopiero po ręcznym odblokowaniu, więc
  próby między nocami to głównie timeouty i przerwania; warto to napisać, bo wyznacza realną wartość rejestru.
- **N3. [SPEC] K2b: do kogo trafia przypomnienie i którym kanałem.** Spec mówi „jedna linia dodana przez hook
  `PostToolUse`. Decyduje człowiek”. Zwykły stdout tego hooka nie jest pokazywany ani modelowi, ani człowiekowi
  w widoku domyślnym (dzisiejszy `post_tool` też z tego powodu jest w praktyce niewidoczny). Trzeba wskazać
  kanał (komunikat dla użytkownika albo kontekst dla modelu, który to powtórzy) i zdarzenia: „wybór pomysłu”
  nie ma żadnej komendy, po której hook mógłby go rozpoznać. Do potwierdzenia uruchomieniem.
- **N4. [IMPL] K2b: koszt hooka.** Hook odpala się po każdym Bash. Najpierw tani filtr po komendzie, potem
  odczyt ostatniego rekordu `usage` od końca pliku, nie pełne parsowanie zapisu przez A0.
- **N5. [IMPL] `merge_settings`** (`houselib/adapters.py:345-377`) scala tylko `hooks` i `permissions`. Nowe
  klucze (`env`, próg automatu) wymagają rozszerzenia, z zasadą „wartość użytkownika wygrywa”. `house_settings`
  (`adapters.py:307-313`) wybiera hooki po nazwie, więc `PreCompact` nie wycieknie do repo `house`; zostawić tak.
- **N6. [IMPL] Wykrywanie `house check` w zapisie.** Komenda to ścieżka bezwzględna, często po `cd … &&`.
  Użyć `guard._segments` i nazwy pliku, wynik brać z kodu wyjścia (`cli.py:162`: 0 = GREEN), nie z tekstu.
- **N7. [SPEC] §4 short-context, pytanie o wagi limitu** zamknąć jako „nieznane” (research o zapisach, pkt 14)
  i dopisać poprawkę rachunku o odczyt historii przez wywołanie streszczające (research, pkt 19).
- **N8. [SPEC] Sekcja w `CLAUDE.md`.** Zdanie w §8, że nie jest to udokumentowany mechanizm, research uznaje za
  błędne (pkt 14). Poprawić, decyzję (`PreCompact`) zostawić.

## 5. Ocena pakietów

| Pakiet | Ocena | Co bym zrobił |
|---|---|---|
| A0 | warto, za szeroko | Trzy źródła: strumień (główne), jsonl Claude Code (interaktywne), Codex tylko przez `exec --json`. Bez warstwy „dla wyszukiwania”. Granica prywatności przy zapisie (W1, W6). |
| A1 | warto | Rejestr przy bramce, fakty najpierw ze stanu (W4, S1). Dodać „check po ostatniej zmianie” i „twierdzenie bez pokrycia” z researchu. |
| A2 | warto | Naprawić źródło ślepych uliczek i Codexa (W2, S2), tryb `compact` w hooku (S3), wykreślić limit 8K jako twardy (S4). |
| A3 9a | warto, drogie | Izolacja i tymczasowy korzeń (W3), bez sędziego LLM w v1 (S7). |
| A3 9b | odłożyć | Po 9a i pierwszym konkursie. |
| A4 | zmniejszyć | Minimalna wersja, wspólny czytnik z `exp` (S8). |
| A5 | zostawić | Tani (N1). |
| A6 | wyciąć lub odłożyć | S11. |
| A7 | zmniejszyć i odłożyć | Jedna komenda i timer (S10). |
| K0 | zmniejszyć | Jedna tabela, walidacja z `cost-state` (S9). |
| K1 | już w A2 | Bez uwag poza W2. |
| K2 | rozdzielić | K2a teraz, K2b warunkowo (W5, N3). |
| K3 | warto, tani | Materiały poza modelem i suma kontrolna (S6). Zakaz `allow_large` z researchu przyjąć. |
| K4 | zostawić | Najpierw punkt odniesienia (W4). |

## 6. Proponowana kolejność

1. **Od razu, bez zależności:** K2a (próg automatu) z hookiem `PreCompact`, K3 część dokumentowa, jedna
   prawdziwa noc i zrzut kosztów jako punkt odniesienia.
2. **A0 w wersji wąskiej** + przełączenie `night` i `codex-task` na log strumienia.
3. **A1**, potem **A2** dla `night` (z W2, S2).
4. **A3 9a** z pięcioma przypadkami smoke. To jedyny pakiet, który chroni wszystkie pozostałe zmiany w `core/`,
   więc nie powinien czekać na K2 i K0, jak w obecnej kolejności łącznej (§5 speca Agno).
5. **K0** (wąski), **A5**, **A4** (minimalny), w dowolnej kolejności.
6. Po pierwszym konkursie: **K4**, decyzja o **K2b**, **9b**, **A7**. **A6** tylko jeśli problem się pojawi.

Ryzyko obecnej kolejności: A0 → A1 → K0 → A2 → K2 to pięć pakietów, zanim cokolwiek zmieni zachowanie agenta
w konkursie, wszystkie oparte na najmniej stabilnym elemencie (formaty zapisów).

## 7. Research: z czym się zgadzam, z czym nie, czego brakuje

**Zgadzam się (przyjąć do speców):**
- zapisy: podagenci (1), deduplikacja globalna (2), `cost-state` jako walidacja (3), `--session-id` i
  `thread_id` jako fakt (7), kompakcja z liczbami (8), rozpoznawanie po kształcie (10), trójwartościowe
  `is_error` (12);
- evidence: kolejność check względem ostatniej edycji (1), twierdzenie kontra zapis (2), lista edycji plików
  testowych (4);
- przekazanie: sprostowanie limitu 8K (7), pełny tekst ustaleń człowieka (8), ten sam szablon dla kompakcji
  Codexa, o ile da się ustawić per projekt (15);
- K2: próg automatu jako główna dźwignia (17), kompakcja przy bezczynności (18), poprawka rachunku (19);
- evale: własny runner (1), stan końcowy (2), izolacja (4), `infra_error` (5), regresja kontra zdolność (6),
  zbiór wstrzymany (15);
- A4 pkt 1–2, K3 pkt 1, A5 pkt 1, A7 pkt 1 i 4.

**Nie zgadzam się albo odkładam:**
- zapisy pkt 6: montowanie zapisywalnego `CLAUDE_CONFIG_DIR` do kontenera zadania Signal. Powiększa powierzchnię
  piaskownicy dla liczby, którą `signalagent` już ma z linii `result` (`signalagent.py:832`). Wybrać trzeci
  wariant z tej rekomendacji: dla Signal wystarcza log strumienia;
- zapisy pkt 9: pierwszy token komendy Bash (patrz W6);
- zapisy pkt 5, 11, 13, 16 (kolejność źródeł Codexa, forki, `.zst`, pola dodatkowe): przy W1 niepotrzebne.
  Niewspierane rekordy liczyć i raportować, nic więcej;
- przekazanie pkt 10 (kompakcja kotwiczona): `house` nie widzi poprzedniego streszczenia w chwili pisania
  instrukcji, więc byłaby to prośba do modelu bez możliwości sprawdzenia. Ustalenia człowieka i tak przywraca kod;
- przekazanie pkt 9 w pełnej formie (lista zmienionych plików ze statusem sprawdzenia w sesji coordinatora):
  coordinator nie edytuje kodu, robią to podagenci w worktree. Wystarczą statusy zadań, które hook już drukuje;
- K2 pkt 20–22 (`/clear` zamiast `/compact`, `/rewind`, próg dla podagentów): rozsądne rady do playbooka, nie
  wymagania speca;
- evale pkt 7, 13, 14 w v1 (Wilson, eksport do plugin eval, ramię bazowe): po pierwszych 15–20 przypadkach;
- A6 pkt 3–7: rozbudowa pakietu, który proponuję wyciąć.

**Czego research nie zauważył:**
- log strumienia jako źródło dla sesji bez człowieka, mimo że sam cytuje `signalagent._claude_argv` (W1);
- braku trwałego źródła ślepych uliczek (W2);
- skutków ubocznych hooków w evalach: odbiór Signala, `notify.ask_new`, rozwiązywanie nazwy konkursu (W3);
- tego, że `git add -A .house` w bramce commituje każdy nowy plik stanu (W7);
- symlinków i worktree przy przypisywaniu sesji do konkursu (S9);
- kosztu i niewierności zapisu wklejonego dokumentu przez model (S6);
- braku danych z `night` i tego, że praca interaktywna omija rejestr prób (W4);
- pytania „czego nie budować”: żadna z ~90 rekomendacji nie proponuje cięcia.

## 8. Niejasności, przy których wykonawca zgadnie źle

1. Czy `attempts.jsonl` i pliki relacji są śledzone w gicie (W7).
2. Gdzie fizycznie żyją ślepe uliczki z prób 1..N-1 (W2).
3. Który numer próby jest „N” w raporcie, logu i nazwie pliku relacji (N2).
4. Język nagłówków szablonu raportu i los sekcji „Evidence” / „Result vs acceptance” (S5).
5. Jak agent w evalu woła `house check <konkurs>` w kopii tymczasowej i jak runner wykrywa trwającą noc (W3).
6. Co znaczy „konkurs w toku” przy pustym `timeline.end` (W3).
7. Jak `house out` poznaje konkurs i gdzie pisze, gdy woła go worker w worktree (S8).
8. Kto jest adresatem przypomnienia K2b i po jakich komendach się pojawia (N3).
9. Co dokładnie jest „treścią” w A0: wejścia narzędzi tak czy nie (W6).
10. „Brief składa się z części z kodu i części z raportu workera”: co, gdy raportu nie ma albo ma stare
    nagłówki. Proponuję: sekcje z kodu zawsze, sekcje z raportu z dopiskiem „brak raportu z próby N”.
11. „Bez duplikatów” dla ślepych uliczek: wolny tekst da się odduplikować tylko dosłownie. Napisać to wprost.
12. Model domyślny evalu dla przypadków coordinatora i skilli (S7).
13. Brief ~2K tokenów: limit liczony w znakach (8 000) czy przez `context.estimate_tokens`
    (`houselib/context.py:59`). Spec podaje oba.
14. Czy `house evidence --session <id>` wolno zapisać do pliku w `.house/`. Proponuję: tylko stdout, bo sesja
    interaktywna zawiera rozmowę z człowiekiem.

## 9. Konflikty z istniejącym kodem i ze scaloną pracą nad Signalem

| Miejsce | Konflikt | Rodzaj |
|---|---|---|
| `hook.py:147-166`, `hook.py:80-93` | Evale i każda sesja w kopii konkursu uruchamiają odbiór Signala i wysyłkę Q-### | [SPEC] W3 |
| `hook.py:15`, `hook.py:270` | Limit 25 linii kontra D-### i ustalenia człowieka po kompakcji | [SPEC] S3 |
| `hook.py:156-164` | Po każdej kompakcji odbiór Signala z limitem 10 s w hooku z limitem 45 s (`adapters.py:300`) | [SPEC] S3 |
| Spec Agno §8, zdanie o specu grup Signala | Nieaktualne, PR #8–#11 scalone | [SPEC] S3 |
| `merge.py:60-64` | `git add -A .house` commituje `out/`, `attempts.jsonl`, relacje | [SPEC] W7 |
| `core/templates/house/.gitignore`, `scaffold.py:38` | Istniejące konkursy nie dostaną nowych wpisów ignorowania | [SPEC] W7 |
| `night.py:283-284` | Worker Codexa nie dostaje briefu | [SPEC] S2 |
| `night.py:286-289`, `night.py:310-314` | Log workera to tekst końcowy, bez `--session-id` i bez strumienia | [SPEC] W1 |
| `night.py` (brak blokady) | A3 i A7 mają „odmawiać, gdy trwa noc”, a nie ma czego sprawdzić | [SPEC] W3 |
| `cli.py:42-56` | Nazwa konkursu tylko z `HOUSE_ROOT/contests/` | [SPEC] W3 |
| `inbox.py:90-107` | Pusty `timeline.end` = konkurs trwa | [SPEC] W3 |
| `agentdocker.py:138` | Zapis sesji zadania Signal ginie w tmpfs; log strumienia zostaje | [SPEC] W1 |
| `guard.py:582-583` | `house` zablokowane w zadaniach Signal, więc `house out` i `house evidence` tam nie istnieją | [IMPL] napisać w profilach |
| `guard.py:286-316` | `house out -- <cmd>` jako opakowanie niewidoczne dla `check_bash` | [IMPL] S8 |
| `adapters.py:345-377` | `merge_settings` nie scala `env` ani progów | [IMPL] N5 |
| `export.py:19-31`, `guard.py:322` | Brak skanera sekretów działającego na tekście | [IMPL] W6 |
| `exp.py:52`, `exp.py:773` | Drugi magazyn logów; `prune` nie sprząta logów | [SPEC] S8 |
| `setup.py:144-154` | Wzór timera nie nadaje się do rutyn | [IMPL] S10 |
| `TEMPLATE.md:7-23`, `coder.md:24`, `bin/codex-task` | Raport po angielsku, mapowanie w specu po polsku | [SPEC] S5 |

## 10. Czego nie sprawdziłem

- Nazw i zachowania ustawień Claude Code z researchu: `PostCompact`, kompakcja przy bezczynności,
  `autoCompactWindow`, kanał wyjścia hooka `PostToolUse` do użytkownika. Znam je tylko z notatek; wymagają próby.
- Pól zdarzeń `codex exec --json` i `stream-json` (zużycie tokenów per wywołanie). Od tego zależy W1.
- Czy `claude -p` z tymczasowym katalogiem konfiguracji zachowuje logowanie subskrypcją (pytanie otwarte researchu
  o evalach).
- Nie uruchamiałem testów ani żadnej komendy `house`; ustalenia o kodzie pochodzą z lektury.

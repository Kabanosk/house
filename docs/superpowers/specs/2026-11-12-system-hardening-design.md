# Poprawki systemu po researchu (2026-11-12)

_Stan: szkic do akceptacji. Źródła: research z 2026-11-12 (HN, blog inżynierski Anthropic, MLE-bench/AIDE,
autoresearch, wpisy o nocnych przebiegach, ECC). Decyzje oznaczone **[moja decyzja]** podjął Claude, nie Wojtek —
do sprawdzenia przy czytaniu. Pytania do Wojtka: sekcja 12._

## 1. Po co

`house` ma dobre fundamenty (pliki zamiast czatu, spec z kryteriami, worktree, judge, stagnacja), ale **wszystkie
zasady są tylko w promptach**. Agent może je pominąć: zmienić test, żeby przechodził, ustawić `status: done` bez
dowodu, commitnąć `.env`, ogłosić koniec za wcześnie. To są dokładnie te błędy, które opisują ludzie z długich
i nocnych przebiegów (Anthropic: „declare the job done”, „marking features passing without testing”; HN: „reward
hacking is very real”). Do tego roadmapa pkt 2 (tryb całodzienny/nocny) potrzebuje fundamentu, którego dziś nie ma:
deterministycznego sprawdzania, ochrony i powiadomień.

Kierunek ogólny: **to, co da się sprawdzić kodem, sprawdza kod (`house`), nie model.** Model proponuje i pracuje;
CLI i hooki pilnują granic, statusów i dowodów. To jest też rekomendacja z naszego raportu (sekcja „stałe jądro
kontrolne”), której jeszcze nie wdrożyliśmy.

## 2. Cele, nie-cele, anty-cele

**Cele**
- Agent nie może oznaczyć zadania jako gotowe bez zielonych, uruchomionych przez kod sprawdzeń akceptacji.
- Agent nie może zmienić chronionych plików (testy akceptacyjne, lokalna sprawdzarka) ani wypchnąć sekretów.
- Wojtek dostaje powiadomienie na telefon, gdy pojawi się pytanie albo przebieg się skończy/wywali.
- Przebieg nocny: kolejne świeże sesje, jedno zadanie na sesję, z limitami i preflightem.
- Recenzja kodu przez inny model niż autor.
- Mniej stałego kontekstu (mierzone, nie na oko).

**Nie-cele**
- Własny orchestrator na API (D4 zostaje). Dispatcher nocny to skrypt odpalający `claude -p` / `codex exec`.
- Fabryka agentów, wspólny bus z drużyną (jak w SPEC §9).
- Przepisanie zadań z Markdown na JSON w całości. Opis zadania zostaje w `.md`; JSON tylko na stan sprawdzeń.
- Rozmowa z agentem przez telefon. Z telefonu tylko odpowiedzi na Q-### (§7); wydawanie poleceń przez Signal
  nie wchodzi w zakres.

**Anty-cele (porażka, nawet jeśli działa)**
- Hooki, które blokują zwykłą pracę tak często, że trzeba je wyłączać.
- Hooki w repo drużyny, które zmieniają zachowanie Claude Code u kolegów bez ich wiedzy.
- Nocny przebieg, który pali limity w pętli błędu (bez wyłącznika).
- Więcej stałego kontekstu po zmianach niż przed.

## 3. Pakiety i kolejność

Pakiety są niezależne w budowie, ale mają zależności w użyciu:

| # | Pakiet | Zależy od | Rozmiar |
|---|---|---|---|
| P0 | Pomiar kontekstu + usunięcie duplikatów skilli | — | mały |
| P1 | `house check`: sprawdzenia akceptacji wykonywane przez kod | — | średni |
| P2 | `house guard` + hooki Claude Code | P1 (lista chronionych plików) | średni |
| P3 | `house notify` (powiadomienia) | — | mały |
| P4 | `house night`: dispatcher świeżych sesji | P1, P2, P3 | duży |
| P5 | Recenzja krzyżowa Codex ↔ Claude | — | mały |
| P6 | Manifest wygenerowanych plików (pomysł z ECC) + naprawa `rmtree` | — | mały |
| P7 | Pętla eksperymentów dla optimize (`house exp`) | P1 | średni |
| P8 | Testy skilli/profili (roadmapa pkt 3) | — | osobny projekt |
| P9 | Research lokalny: streszczenia per dokument, poziom wstępny/dokładny | P4 | mały–średni |
| P10 | `house search`: Qdrant, hybryda gęsty + rzadki, embeddingi z ollama | P9 | średni |

Kontekst (Wojtek, 2026-11-12): trwają konkursy optimize (TechArena Francja, Finlandia, wkrótce Polska);
**20–22.11 Collabothon, maraton 48 h (build)**. Kolejność zostawił Claude'owi.

Kolejność **[moja decyzja]**:
1. **Do 18.11 (przed Collabothonem):** P0 → P6 → P1 → P2 → P3 (z poziomami decyzji, §7a) → P4 w wersji
   podstawowej. 19.11: próbny mini-hackathon na sztucznym konkursie (roadmapa pkt 3 w małej skali), poprawki.
2. **Po Collabothonie:** P7 (pętla eksperymentów dla trwających optimize), potem P5, potem P9 (research lokalny), potem P10 (`house search` na Qdrancie).

Uzasadnienie: P0 i P6 są tanie, a P6 naprawia błąd w repo przejętych (Francja to repo drużyny). P1–P3 to
fundament, bez którego noc w maratonie 48 h jest ryzykowna. P4 przydaje się w obu typach konkursów. P7 jest
ważny dla optimize, ale nie zdąży się go sprawdzić przed 20.11 razem z P4; trwające optimize do tego czasu
idą obecnym procesem (`submissions.csv` + stagnacja).

## 4. P0: pomiar i dieta kontekstu

**Problem.** Badania i praktycy (ETH Zurich o `AGENTS.md`, „context rot”, komentarze o budżecie skilli w Claude
Code, który po cichu pomija rzadziej używane) mówią to samo: każda linia ładowana zawsze kosztuje jakość. Dziś
`house adapt`:
- kopiuje **wszystkie** `vendor/skills/*` do `.claude/skills/` konkursu, chociaż każdy profil odwołuje się do nich
  ścieżką (`$HOUSE_ROOT/vendor/skills/.../SKILL.md`). Skill jest więc dostępny dwa razy, a jego opis siedzi
  w kontekście każdej sesji, także tam, gdzie nie jest potrzebny;
- wkleja całe ciało `coordinator.md` do `AGENTS.md` (zawsze w kontekście) — to jest OK, ale nie wiemy, ile ważą
  razem AGENTS.md + CLAUDE.md + opisy skilli + opisy agentów + superpowers + skille użytkownika.

**Zmiana.**
1. `house doctor --context <konkurs>`: raport (stdout) z przybliżoną liczbą tokenów (znaki/4) dla: `AGENTS.md`,
   `CLAUDE.md`, sumy opisów skilli w `.claude/skills/` i `~/.claude/skills/`, opisów agentów, oraz listy
   **duplikatów** (ta sama nazwa skilla w kilku miejscach, np. user-skills globalnie i w repo).
2. `house adapt` przestaje kopiować `vendor/skills` do `.claude/skills/` **[moja decyzja]**. Agenci czytają je
   ścieżką, tak jak już mają w instrukcjach. Wyjątek: jeśli skill ma być wywoływany przez główną sesję z opisu
   (żaden dziś nie jest), dodajemy go do jawnej listy w adapterze.
3. Zapis wyniku „przed” i „po” w `docs/` (jedna tabelka), żeby było widać, czy dieta coś dała.

**Akceptacja.** `doctor --context` działa na `grok-bot-wro-2026`; po zmianie suma stałego kontekstu spada;
testy istniejące dla vendor skills zaktualizowane (sprawdzają ścieżki w profilach, nie kopię).

## 5. P1: `house check` — sprawdzenia wykonuje kod

**Problem.** `acceptance: []` w zadaniu to lista zdań. Koder sam je „sprawdza” i sam ustawia `status: done`.
Anthropic: lista funkcji w JSON z flagą `passes`, bo model mniej chętnie przerabia JSON niż Markdown; HN:
numerowane AC, sprawdzane po kolei („If AC-3 fails you know exactly what got dropped”).

**Zmiana.**
- Format kryteriów w frontmatterze zadania (wsteczna zgodność: zwykły string = kryterium ręczne):
  ```yaml
  acceptance:
    - id: AC-1
      run: "pytest -q tests/test_parser.py"      # komenda w katalogu worktree; sukces = exit 0
    - id: AC-2
      run: "python scripts/score.py --split val"
      expect: "score >= 0.71"                    # opcjonalnie: wzorzec/regex na stdout
    - id: AC-3
      manual: "golden path działa w przeglądarce"  # sprawdza judge, dowód w raporcie
  protected: [tests/test_parser.py]             # pliki, których koder nie może zmieniać (P2)
  ```
- `house check <konkurs> T### [--worktree PATH]` uruchamia kryteria `run`, zapisuje wynik do
  `.house/checks/T###.json` (`{AC-1: {passes, exit, ran_at, commit, output_tail}}`) i kończy się kodem ≠ 0,
  jeśli coś nie przechodzi. Ręczne kryteria mają `passes: null`, dopóki judge nie wpisze dowodu
  (`house check T### --manual AC-3 --evidence reports/judge-2.md`).
- **Bramka scalania:** `/house-run` scala gałąź zadania do `main` tylko po zielonym `house check` na tej gałęzi
  i po drugim `house check` na `main` po scaleniu. To zastępuje „check its report and acceptance evidence”.
- Statusy ustawia coordinator po bramce, nie koder **[moja decyzja]**. Koder kończy na `status: review`.
  Zmienia to `coder.md`, `coder-codex.md`, tekst sterownika Codexa i regułę „ready” w `/house-run`.
- Plan (`/house-plan`) musi dawać co najmniej jedno kryterium `run` na zadanie z logiką; zadania bez komendy
  (research, pitch) mają kryteria `manual`.
- **Zamrożenie kryteriów** **[moja decyzja]**: na końcu `/house-plan` `house check --freeze T###` zapisuje
  `acceptance` i `protected` (z sha256) do `.house/checks/T###.json` i coordinator to commituje. `house check`
  używa **zamrożonej** kopii, nie bieżącego frontmatteru; rozjazd = ostrzeżenie + czerwony wynik. Zmiana kryteriów
  tylko przez `house check --refreeze T### --reason "..."`, co dopisuje D-###. W `copilot` plan (a więc kryteria)
  i tak przegląda człowiek przed `/house-run`; nowego etapu akceptacji nie dodajemy.
- **Kryteria ręczne** **[moja decyzja]**: nie blokują scalenia do `main`. Zadanie po zielonych `run` przechodzi do
  `status: merged`; `done` dopiero po dowodzie judge'a dla `manual`. Dla `depends_on` liczy się `merged`.
  Przebieg nocny scala więc też zadania z UI; judge weryfikuje je przy najbliższym kamieniu milowym.
- **Regresja na `main`** **[moja decyzja]**: druga kontrola po scaleniu uruchamia `init.sh` + kryteria `run`
  **wszystkich** zadań `merged`/`done`. Czerwono = automatyczny `git revert` commita scalenia, zadanie
  `blocked`, powiadomienie (P3). `main` zostaje zielony, więc jedno złe scalenie nie zatrzymuje nocy.
- `.house/init.sh` (pomysł Anthropic): tworzony na etapie planu; uruchamia aplikację/testy dymne. `house check`
  odpala go przed kryteriami, jeśli istnieje. Kolejna sesja nie traci czasu na ustalanie, jak odpalić projekt.

**Akceptacja.** Testy jednostkowe `house check` (zielone, czerwone, timeout, `expect`, ręczne, stary format
stringów); `/house-run` i profil `coder` opisują bramkę; e2e na sztucznym konkursie.

## 6. P2: `house guard` i hooki

**Problem.** Jedyny deterministyczny sposób zatrzymania złej akcji w Claude Code to hooki (zgodne zdanie wielu
autorów). W repo nie ma żadnego.

**Zmiana.**
- `bin/house-hook` (Python, stdlib) — jeden skrypt z podkomendami, wywoływany przez hooki:
  - `pre-tool` (PreToolUse, `Edit|Write|MultiEdit|Bash`): blokuje (exit 2 + powód dla modelu)
    1. edycję plików z `protected:` zamrożonych zadań (**na stałe**, także po scaleniu — późniejsze zadanie nie
       może przerobić testów wcześniejszego) oraz `spec.scoring.local_scorer`,
    2. `git add`/`commit` plików pasujących do `.env*`, kluczy (te same wzorce co skan w `house export`),
    3. `git push` na remote spoza `spec.guard.push_allow` (domyślnie tylko `origin`) i każdy `--force` na `main`,
    4. zapis do `.house/checks/` (tylko `house check` tam pisze).
  - `session-start` (SessionStart): wypisuje 15–25 linii: cel i tryb z `spec.yaml`, liczba otwartych Q, zadania
    gotowe/zablokowane, wynik ostatniego `house check`, czy minął czas od `last_briefing`. Coordinator nadal może
    czytać pliki, ale nie musi zaczynać od sześciu odczytów.
- `house adapt` generuje hooki **zawsze w `.claude/settings.local.json`** (poza gitem) **[moja decyzja po Q6]**.
  Powód: hooki wskazują `bin/house-hook` po ścieżce bezwzględnej z maszyny Wojtka; w `settings.json` z gita
  odpaliłyby się u kolegów i wywaliły (brak pliku) albo blokowały ich pracę według naszych reguł. Lokalnie
  działają tylko u Wojtka. Scala z istniejącą zawartością (roadmapa pkt 1 dodaje allowlistę komend), nie
  nadpisuje.
- Codex nie ma tych hooków w naszej konfiguracji, więc dla niego ta sama logika działa **po fakcie**:
  `house guard diff <worktree> T###` sprawdza `git diff` gałęzi pod kątem chronionych plików i sekretów.
  Wywołuje go sterownik `coder-codex` przed commitem i bramka scalania z P1 dla każdego zadania (też Claude'a,
  na wypadek obejścia hooka przez Bash).
- **Kogo wiąże** **[moja decyzja]**: każdą sesję Claude Code w repo konkursu (workery, coordinator, Twoja
  interaktywna). Ty zmieniasz chroniony plik w edytorze albo przez `house check --refreeze`; nie ma
  wiarygodnego sygnału „to worker” w wejściu hooka, a reguła dla wszystkich jest prostsza.
- **Sesje w worktree** **[moja decyzja]**: `settings.local.json` nie istnieje w `.worktrees/*`, więc `house night`
  zawsze odpala `claude -p --settings <główny checkout>/.house/claude-settings.json` (adapter generuje ten plik
  zawsze; `settings.json`/`settings.local.json` tylko go wskazują lub kopiują). Podagenci sesji interaktywnej
  działają pod hookami głównej sesji.
- **Zapisy Codexa do `.house/`** (ma `--add-dir`): `guard diff` porównuje też `.house/checks/` i pola
  `acceptance`/`protected` zadań z zamrożoną kopią w ostatnim commicie coordinatora.
- Wyłącznik awaryjny: `HOUSE_GUARD=off` w środowisku wyłącza `pre-tool` (logując to w `PROGRESS.md`).

**Akceptacja.** Testy `house-hook` na przygotowanych wejściach JSON (format wejścia hooków Claude Code);
ręczny test w prawdziwej sesji: próba edycji chronionego testu jest zablokowana z czytelnym powodem.

## 7. P3: `house notify`

**Problem.** `QUESTIONS.md` jest dobre, ale nikt nie wie, że coś czeka. Według komentarza z HN w sesjach w tle
nie ma `AskUserQuestion`; nocny przebieg nie ma jak zawołać człowieka.

**Zmiana.**
- `house notify "<tekst>" [--priority high]` — wysyła przez kanał z `~/.config/house/notify.env` (poza każdym
  repo; nikt czytający `house` ani repo konkursu nie zobaczy tokenu i nie wyśle Ci nic). Brak konfiguracji =
  nic nie robi, kod 0 (nie psuje przebiegu).
- **Kanał: Signal, dwukierunkowy** (decyzja Wojtka, 2026-11-13). Przez `signal-cli` podłączone jako
  dodatkowe urządzenie do numeru Wojtka (`signal-cli link -n house` → kod QR do zeskanowania w telefonie), wątek
  „Notatka do siebie”. Bez osobnego numeru dla bota.
  - Wysyłka: `signal-cli -a <numer> send --note-to-self --notify-self -m "<tekst>"` (bez `--notify-self`
    wiadomość do siebie przychodzi bez powiadomienia).
  - Odbiór: jednorazowe `signal-cli -a <numer> receive --output=json` przy każdym wywołaniu `house inbox`
    (bez stałego daemona); wiadomości z telefonu do „Notatki do siebie” przychodzą jako wiadomości synchronizacji
    (`syncMessage.sentMessage` z odbiorcą = własny numer).
  - `house doctor` sprawdza `signal-cli` (i Javę, jeśli to wersja JVM), `house setup` podpowiada instalację
    i linkowanie. Brak `signal-cli` = `notify` nic nie robi (kod 0), `doctor` ostrzega.
  - **Pierwszy krok P3 to spike** (pół godziny): link, wysłanie, odbiór odpowiedzi z telefonu, format JSON.
    Jeśli odbiór z „Notatki do siebie” nie działa wiarygodnie, przejście na Discord (REST, stdlib) i sygnał
    dla Wojtka.
  - **Spike zrobiony 2026-11-13, działa.** `signal-cli` 0.14.10 (natywna wersja, bez Javy) w
    `~/.local/opt/`, link w `~/.local/bin/`. Konfiguracja: `~/.config/house/notify.env`
    (`HOUSE_NOTIFY_CHANNEL=signal`, `HOUSE_SIGNAL_ACCOUNT=…`). Wysyłka z `--note-to-self --notify-self` daje
    powiadomienie na telefonie. Odpowiedź z telefonu przychodzi jako
    `envelope.syncMessage.sentMessage` z `destinationNumber` = własny numer i `message` = tekst; pozostałe
    wiadomości synchronizacji (odczyty, kontakty) mają puste `message` i są pomijane.
  - `receive` **zużywa** wiadomości (drugi odczyt ich nie zwróci): `house inbox` zapisuje każdą odebraną
    wiadomość do `~/.local/share/house/inbox.jsonl` i dopiero z tego pliku rozdziela odpowiedzi na konkursy;
    obsłużone oznacza w `.house/state.json` (ostatni timestamp).
  - Signal wymaga regularnego odbioru na połączonym urządzeniu (inaczej może je odłączyć): `house night` i hook
    `SessionStart` wołają `house inbox`; `house doctor` ostrzega, gdy ostatni odbiór był dawno.
  - Linkowanie: kod QR jest ważny ok. 2 minuty; `house setup` generuje go i od razu pokazuje.
  - Ryzyko: stare wersje `signal-cli` przestają działać po zmianach protokołu; `doctor` pokazuje wersję,
    `setup` aktualizuje.
- **Grupa drużyny** (decyzje Wojtka, 2026-11-13):
  - Każde powiadomienie idzie **do „Notatki do siebie” (dla Wojtka, z powiadomieniem) i do grupy Signal
    konkursu**, jeśli jest ustawiona. Grupę podaje się w specyfikacji konkursu:
    `spec.yaml` → `notify: {signal_group: "<nazwa grupy>"}` (wypełniane na `/house-start`; `house` rozwiązuje
    nazwę na ID grupy przez `signal-cli listGroups`, niejednoznaczna lub nieznana nazwa → ostrzeżenie i tylko
    „Notatka do siebie”).
  - **Skrót konkursu** (Wojtek, 2026-11-13; jedna grupa może obsługiwać kilka konkursów):
    `spec.yaml` → `notify.alias` (np. `fr`, `fi`, `collab`; małe litery/cyfry). Unikalny tylko wśród
    **trwających** konkursów: trwający = `timeline.end` w przyszłości albo puste. Zakończone mogą mieć ten sam
    skrót (np. kolejna TechArena znów `fr`); `house doctor` sprawdza kolizje tylko wśród trwających, a
    odpowiedzi dla zakończonego konkursu `house inbox` pomija. Wiadomości w grupie idą z numeru Wojtka (urządzenie połączone,
    bez osobnego numeru) z prefiksem `🤖 [fr]`, żeby było jasne, że pisze system i którego konkursu dotyczy.
  - **Jak odpowiadać, od najwygodniejszego:**
    1. **Odpowiedź z cytatem** (przeciągnięcie wiadomości w prawo w Signalu) i sama treść, np. `B`. `house`
       zapamiętuje timestamp każdej wysłanej wiadomości → (konkurs, Q-###) w `~/.local/share/house/sent.jsonl`;
       odpowiedź ma `quote.id` = ten timestamp, więc nic nie trzeba wpisywać.
    2. Bez cytatu: `Q-007: B` (albo `Q7 B`), jeśli tylko jeden konkurs w tej grupie ma otwarte Q-007.
    3. Inaczej ze skrótem: `fr Q-007: B` (albo `fr 7 B`). Niejednoznaczna odpowiedź → `house` dopytuje w grupie.
  - Kto odpowiedział, `house` wyciąga z samej wiadomości (`envelope.sourceName`, inaczej numer); nikt nie wpisuje
    swojego imienia.
  - **Odpowiadać może każdy**: Wojtek z „Notatki do siebie” albo z grupy, koledzy z grupy. **Liczy się pierwsza
    odpowiedź** (najwcześniejszy timestamp Signala); kto odpowiedział, trafia do `QUESTIONS.md` i D-###
    (`Kto: człowiek (<imię lub numer>)`). Po zapisaniu `house` potwierdza w grupie: `✓ [fr] Q-007: B (<kto>)`.
    Późniejsze odpowiedzi na rozstrzygnięte pytanie: krótka informacja, kto i jak już rozstrzygnął.
  - Prywatność: połączone urządzenie odbiera wszystkie wiadomości konta; `house inbox` zapisuje tylko
    „Notatkę do siebie” i grupy z `notify.signal_group` konkursów, resztę odrzuca bez zapisu.
  - Format w JSON z `signal-cli`: odpowiedź Wojtka w grupie = `syncMessage.sentMessage` z `groupInfo`;
    odpowiedź kolegi = `dataMessage` z `groupInfo` (do zweryfikowania na pierwszej prawdziwej wiadomości).
- **Odpowiedzi z telefonu**: `house inbox <konkurs>` czyta nowe wiadomości, rozpoznaje `Q-007: B` /
  `Q-007 tak` / dowolny tekst po `Q-007:`, zapisuje odpowiedź w `QUESTIONS.md` (`[answered]`, **Odpowiedź**),
  dopisuje D-### i odblokowuje zadania. Wiadomości bez `Q-###` ignoruje (to też Twoje zwykłe notatki).
  Wywołuje go `house night` co iterację i hook `SessionStart`. Kilka konkursów naraz: prefiks w wiadomości
  wychodzącej (`[techarena-fr] Q-007 …`); w odpowiedzi wystarczy `Q-007`, jeśli numer jest jednoznaczny,
  inaczej `fr Q-007: B`.
- Kiedy: nowe `Q-###` (PostToolUse na zapisie `QUESTIONS.md` w `house-hook`), koniec/awaria przebiegu nocnego,
  `house check` czerwony na `main` po scaleniu, hook `Notification` Claude Code (sesja czeka na zgodę).
- Treść: konkurs, jedno zdanie, numer pytania. Bez kodu i bez danych z repo (konkurs jest prywatny).

## 7a. Poziomy decyzji i termin na odpowiedź (część P3)

**Wojtek (Q2):** uprawnienia ostrożne (allowlista + tryb auto), ale mniej ważne decyzje niech podejmuje model
(Opus/Fable), a blokują tylko najważniejsze. Wyjątek: gdy konkurs zaraz się kończy, a on nie odpowiada, model
sam wybiera np. ostatnie zgłoszenie.

Są tu dwie różne warstwy:

1. **Uprawnienia narzędzi** (czy wolno odpalić komendę): sesje bezgłowe dostają
   `--permission-mode auto --permission-prompts none` + allowlistę z roadmapy pkt 1 + hooki z P2. Coś, co
   wymagałoby zgody, jest od razu odrzucane (sesja nie wisi), a agent zapisuje to w raporcie.
2. **Decyzje konkursowe** (`protocols/questions.md`): trzy poziomy zamiast dwóch:

| Poziom | Przykłady | Kto decyduje |
|---|---|---|
| mały | biblioteka, podział zadania, ponowienie | coordinator, log D-### |
| średni | wybór między pomysłami o podobnej ocenie, pivot po stagnacji w `autopilot`, koszt > 20% | judge (model z `judge.model_tier`, może być Opus albo Fable), log D-### + powiadomienie „zdecydowałem, możesz cofnąć” |
| krytyczny | wybór pomysłu w `copilot`, zgłoszenia w końcówce, wybór finalnego zgłoszenia, nieodwracalne/publiczne rzeczy poza spec | człowiek; **z terminem** |
| zawsze człowiek | niejasny regulamin, zgoda organizatorów | człowiek, bez domyślnej odpowiedzi |

- Każde krytyczne Q-### ma `decide_by` (czas) i `default` (rekomendacja judge'a). Gdy minie `decide_by` bez
  odpowiedzi, judge wykonuje `default`, loguje D-### „domyślnie, brak odpowiedzi do HH:MM” i wysyła powiadomienie.
- `decide_by` ustawia coordinator: najpóźniej sensowny moment (push right), w końcówce konkursu np.
  koniec − 10 min dla wyboru finalnego zgłoszenia. Przypomnienie leci w połowie czasu do terminu.
- `copilot`/`autopilot` przesuwa tylko granicę między „średni” a „krytyczny” (jak dziś tabela w protokole).

## 8. P4: `house night` — świeże sesje zamiast jednej długiej

**Problem.** Autor opisu nocnych przebiegów: zasady z `CLAUDE.md` rozmywają się po kolejnych kompakcjach;
rozwiązanie to skrypt odpalający kolejne świeże sesje z własnym budżetem. Anthropic: jedno zadanie na sesję,
rytuał startu. Inny autor: przed nocą sprawdź dysk, limity GitHuba, sekrety i czy `main` nie jest już zepsuty.
To jest roadmapa pkt 2 („dispatcher, nigdy nie pisze kodu”).

**Zmiana.**
- `house night <konkurs> [--hours 8] [--max-tasks N]` — pętla w Pythonie:
  1. **Preflight**: czysty `main`, `house check` zielony na `main`, wolne miejsce, `claude`/`codex` zalogowane,
     sekrety z `spec` obecne, brak pliku `STOP`. Czerwony preflight = powiadomienie i koniec.
  2. Wybór gotowego zadania (ta sama reguła co `/house-run`), worktree, uruchomienie **świeżej** sesji
     `claude -p` (albo `codex-task`) z briefem: ścieżka zadania, `HOUSE_DIR`, „jedno zadanie, skończ na review”.
  3. Bramka z P1 + `guard diff` z P2 → scalenie albo `status: blocked` z raportem.
  4. Powtarzaj. Zadania tylko niezależne, maks. `limits.max_parallel_agents` naraz.
  5. **Wyłączniki**: limit godzin, plik `.house/STOP`, 3 kolejne porażki tego samego zadania → zablokuj zadanie,
     5 porażek z rzędu ogółem → koniec przebiegu **[moja decyzja]**.
  6. Gdy nic nie jest gotowe: w wersji przed Collabothonem **koniec przebiegu** + powiadomienie
     **[moja decyzja]**. Po P9: research nowych kierunków lokalnym modelem, gdy GPU jest wolne (trening ma
     pierwszeństwo, D23); `protocols/questions.md` dostanie ten warunek.
  7. Rano: wpis do `PROGRESS.md`, raport `reports/night-<data>.md`; `/house-status` go uwzględnia.
- Uprawnienia sesji bezgłowych: patrz Q2.
- W trybie `copilot` przebieg nocny nie podejmuje „trudnych decyzji” — zostawia Q i bierze następne zadanie
  (już tak mówi `protocols/questions.md`).

**Akceptacja.** Test na sztucznym konkursie z 3 trywialnymi zadaniami (jedno celowo nie do zrobienia): dwa
scalone, jedno zablokowane, raport nocny i powiadomienie; `STOP` przerywa przebieg.

## 9. P5: recenzja krzyżowa

**Problem.** Koder (Sonnet) i judge (Opus) to ta sama rodzina modeli; modele łagodniej oceniają podobne do
swoich rozwiązania. Ludzie robią: „agent, który nie pisał kodu, robi przegląd”, najlepiej inny dostawca.

**Zmiana.**
- Nowy profil `reviewer-codex` (`model_tier: codex`, `fallback: judge`): przegląd diffu zadania przez
  `codex-task --read-only`, ślepy brief (cel zadania i diff, bez raportu kodera), wynik do
  `reports/review-T###.md` z listą: błąd / ryzyko / drobiazg.
- **Podstawa: vendorowany skill `second-opinion`** (jensvanbellen/second-opinion, MIT; propozycja Wojtka
  2026-11-13) do `vendor/skills/second-opinion/` z `ATTRIBUTION.md`, jak pozostałe. Bierzemy z niego:
  ślepy brief w pliku („Do not include your own findings, hypotheses, or draft review”), wywołanie
  `codex exec --sandbox read-only -C <repo> -o <out> - < <brief>` w tle z kontrolą zawieszenia (start 90 s,
  brak postępu 420 s), raport w trzech grupach **New / Agreed / Disputed**, najwyżej jedna runda dopytania,
  zakaz zmyślania recenzji, gdy CLI zawiedzie. Kierunek odwrotny (Codex → Claude) skill ma przez
  `claude -p --allowedTools "Read,Grep,Glob,Bash(git diff:*),…"`.
  - Czego skill **nie ma**, a dokładamy w profilu `reviewer-codex`: limit dzienny (wyżej), zakres w briefie
    („recenzuj tylko `git diff <baza>...<gałąź>`; inne pliki otwieraj tylko, żeby sprawdzić konkretne
    znalezisko” — Codex czyta repo sam, więc to jedyna kontrola zużycia), zapis do `reports/review-T###.md`.
  - Repo ma 6 tygodni i 0 gwiazdek: kopia przypięta do commita, aktualizacja jak przy innych vendor skills.
    Test w P5: jedna recenzja na prawdziwym diffie z konkursu przed włączeniem do bramki.
- Odwrotnie: zadania wykonane przez `coder-codex` recenzuje Claude (`judge` albo nowy lekki `reviewer` na Sonnecie).
- Kiedy **[moja decyzja po Q4]**: **raz na kamień milowy** (jeden diff od ostatniej recenzji, nie po każdym
  zadaniu: mniej wywołań, mniej powtarzanego kontekstu) **plus zawsze** dla plików wysokiego ryzyka: lokalna
  sprawdzarka, potok zgłoszeń, kontrakty między zadaniami (planer oznacza je `review: always`). Nie blokuje
  scalenia, z wyjątkiem potwierdzonego błędu w sprawdzarce lub potoku zgłoszeń.
- **Limity Codexa są mniejsze niż Claude'a** (Wojtek): recenzja dostaje tylko diff + cel (bez całego repo),
  `limits.codex_reviews_per_day` (domyślnie 4). Po wyczerpaniu limitu albo błędzie limitu z `codex-task`
  recenzję robi `judge` w świeżym kontekście ze ślepym briefem (inny model niż koder-Sonnet, ale ta sama
  rodzina — w raporcie zaznaczone jako słabsza niezależność). Recenzja ma pierwszeństwo przed `coder-codex`
  w puli Codexa, bo kod może pisać też Claude, a niezależnego recenzenta nie ma kto zastąpić.
- Coordinator traktuje wynik jako „zewnętrzną opinię, niekoniecznie słuszną”: poprawia błędy,
  ryzyka loguje.

## 10. P6: manifest wygenerowanych plików + naprawa `rmtree`

**Problem (istniejący błąd).** `adapters.claude_code` robi `shutil.rmtree(contest/.claude/agents)`. W repo
przejętym przez `house adopt` kasuje to agentów, których ktoś z drużyny trzymał w `.claude/agents/`. ECC rozwiązuje
podobny problem manifestem własności: instalator wie, które pliki są jego, i nie rusza reszty.

**Zmiana.** `house adapt` zapisuje `.house/generated.json` (ścieżka → sha256 tego, co wygenerował). Przy kolejnym
`adapt` usuwa/nadpisuje tylko pliki z manifestu; plik z manifestu zmieniony ręcznie → kopia `*.orig` i ostrzeżenie
(jak dziś dla `AGENTS.md`). Obce pliki w `.claude/` zostają. `house doctor <konkurs>` pokazuje rozjazdy.

## 11. P7: pętla eksperymentów (optimize)

**Problem/szansa.** AIDE (najlepszy wynik w MLE-bench) i autoresearch Karpathy'ego mają wspólny wzór: każdy
eksperyment to mała zmiana najlepszego dotąd rozwiązania, **stały budżet czasu**, wynik decyduje automatycznie
(git zachowuje albo cofa), drzewo prób zamiast jednej ścieżki. Mamy `submissions.csv` i stagnację, brakuje lokalnej
pętli eksperymentów.

**Zmiana (szkic, szczegóły przy pierwszym konkursie optymalizacyjnym).**
- `house exp run <konkurs> --from <commit|best> --budget 5m -- <komenda>`: gałąź `exp/<n>`, uruchomienie z limitem
  czasu, wynik z `spec.scoring.local_scorer`, wiersz w `.house/experiments.csv`
  (`id, parent, commit, idea_id, budget, duration, score, status`). Lepszy od rodzica = `keep`, inaczej `revert`.
- `best` = najlepszy `keep`; `parent` daje drzewo (AIDE: „debug” rodzica z błędem albo „improve” najlepszego).
- Stagnacja liczona z `experiments.csv`, nie z pamięci agenta.
- Zgłoszenia nadal przez `/house-submit` i `submissions.csv` (lokalny wynik bierze się z eksperymentu).

## 11a. P9: research lokalny (po Collabothonie)

**Wojtek (Q5):** czy są otwarte narzędzia do researchu, które oszczędzą tokeny?

Gotowe narzędzia (Local Deep Research, GPT Researcher) istnieją, ale ich wyniki jakości są deklarowane przez
autorów i mierzone na większych modelach (np. 27B na RTX 3090); na RTX 3060 (12 GB, model ~8B) jakość pełnego
lokalnego „deep research” będzie niższa. **Nie zakładamy, że są podobnej jakości co Claude.**

Dlatego podział pracy zaproponowany przez Wojtka (2026-11-13): **lokalnie tylko tania, mechaniczna część,
decyzje zostają przy Claude.**

1. Claude (`researcher`) układa 3–6 zapytań.
2. `house research <konkurs> "<pytanie>" --queries q1 q2 …` (bez modelu Claude):
   wyszukiwanie (SearXNG lokalnie, jeśli jest; inaczej wyniki podane przez Claude z WebSearch), pobranie stron,
   wyciągnięcie tekstu, zapis surowego tekstu do `.house/research/<slug>/raw/NN-<domena>.txt`.
3. Lokalny model (ollama, `summarizer`) dla **każdego dokumentu osobno**: ocena trafności 0–3 względem pytania,
   streszczenie w 5 liniach, do 5 faktów z **dosłownymi cytatami**. Wynik: `.house/research/<slug>/index.md`
   (jedna sekcja na dokument, z linkiem do surowego pliku).
4. Claude czyta tylko `index.md` (rząd 2–3 tys. tokenów na 20 dokumentów), sam wybiera, które surowe pliki
   otworzyć w całości, i pisze raport. Surowe dane zawsze zostają (zasada 3 ze SPEC).

Ryzyko: mały model źle oceni trafność albo pominie ważny fakt. Łagodzenie: Claude widzi wszystkie dokumenty
w indeksie (także te z oceną 0–1) i może otworzyć dowolny surowy plik; cytaty są dosłowne, więc łatwo je sprawdzić.
GPU: gdy trwa trening, streszczenia robi Haiku (D23).

**Dwa poziomy** (propozycja Wojtka, 2026-11-13):
- **Wstępny** (`--depth quick`, domyślny): jedna runda zapytań, Claude odpowiada z `index.md`, każdą tezę
  popiera cytatem z indeksu. Do szybkiego rozeznania: „czy to w ogóle istnieje”, „jakie są biblioteki”.
- **Dokładny** (`--depth deep`): 2–3 rundy (Claude dopisuje zapytania na podstawie luk z poprzedniej rundy),
  otwiera surowe pliki najważniejszych dokumentów, sprawdza krzyżowo fakty z ≥ 2 źródeł, zaznacza sprzeczności.
- Kiedy który: `researcher` zaczyna od wstępnego; dokładny **zawsze** dla regulaminu, karty prior-art przy
  wyborze pomysłu i decyzji średnich/krytycznych (§7a), a także gdy wstępny daje sprzeczne lub mało trafne
  wyniki (większość ocen 0–1).

**Później (nie w P9): wyszukiwarka po zgromadzonych materiałach.** Gdy po kilku konkursach materiałów będzie
dużo (research, regulaminy, dokumentacje API, raporty, `ideas.md`, retro), `house search "<zapytanie>"`
zwracający top-k fragmentów z `ścieżka:linia`, wołany przez agentów z Basha (bez MCP).
**Silnik: Qdrant + embeddingi z własnego modelu** (decyzja Wojtka, 2026-11-13; zamiast SQLite FTS5):
- Qdrant w kontenerze Dockera, wolumen w `~/.local/share/house/qdrant`. `house` rozmawia z nim przez REST
  (`urllib`), więc CLI zostaje bez zależności. `house doctor` sprawdza Dockera i czy Qdrant odpowiada;
  `house setup` podpowiada `docker run`.
- **Hybryda w jednej kolekcji:** wektor gęsty (embedding) + wektor rzadki (częstości słów liczone w `house`,
  IDF po stronie Qdranta, `modifier: idf`). Zapytanie przez Query API: dwa `prefetch` + fuzja RRF. Rzadki łapie
  dokładne nazwy (funkcje API, kody błędów, nazwy metryk), gęsty to samo opisane innymi słowami.
- **Embeddingi przez ollama** **[moja decyzja]**, model wielojęzyczny (materiały są po polsku i angielsku),
  np. `bge-m3`; dokładny wybór przy budowie, po teście na naszych materiałach. vLLM dopiero, gdy ollama będzie za
  wolna przy indeksowaniu dużych zbiorów. Model embeddingów jest mały, ale dzieli GPU z treningiem (D23):
  indeksowanie wstrzymuje się, gdy trwa trening.
- Kolekcje: `house-<konkurs>` (materiały z `.house/`) + `house-shared` (wspólne między konkursami: dokumentacje,
  zwycięzcy, retro). Każdy fragment ma `path`, `line`, `kind` (research/rules/report/docs/retro), `contest`, datę.
- Indeksowanie przyrostowe: `house index <konkurs>` po hashach plików; zmiana modelu embeddingów = nowa kolekcja,
  stara zostaje do porównania.
- **Dostęp dla agentów: komenda CLI** `house search "<zapytanie>" [--contest X] [--k 5]` wołana z Basha
  (Claude Code, Codex, podagenci, sesje `claude -p` — wszędzie bez konfiguracji, zero stałego kontekstu).
  Profil `researcher` i coordinator: „najpierw `house search`, potem internet”. Komenda na allowliście.
  Nakładka MCP na tę samą funkcję dopiero, jeśli agenci nie będą z komendy korzystać.
- Wynik `house search` zawsze wskazuje plik źródłowy; agent czyta oryginał, nie fragment z bazy (zasada 3 ze SPEC).
- Kiedy: po P9 (osobny pakiet P10), gdy uzbiera się korpus z kilku konkursów. Pomiar przed włączeniem do
  `researcher`: 10 pytań z odpowiedziami znanymi z poprzednich konkursów, trafienie w top-5 dla: tylko `grep`,
  tylko gęsty, hybryda.

**Przed wdrożeniem pomiar** na 5 pytaniach z poprzednich konkursów: `researcher` sam (WebSearch + czytanie
stron) vs potok z indeksem. Mierzone: tokeny Claude'a, czas, jakość raportu w ślepej ocenie judge'a (który raport
lepiej odpowiada na pytanie, czy coś ważnego pominięto). Wdrażamy, jeśli tokeny spadną wyraźnie, a jakość nie
spadnie w ślepej ocenie. LDR/GPT Researcher zostają jako ewentualne źródło kodu do wyszukiwania i ekstrakcji.

## 12. Pytania do Wojtka (odpowiedzi 2026-11-12)

Odpowiedzi wpisane w sekcje 3, 6, 7, 7a, 8, 9, 11a. Doprecyzowania z 2026-11-13: Signal zamiast Discorda
(§7), mniejsze limity Codexa (§9), research jako lokalne streszczenia per dokument + wybór przez Claude'a (§11a).
Otwartych pytań brak.

### Pierwotne pytania

Od najważniejszego. Odpowiedzi zmieniają, co powstanie.

- **Q1. Kolejność.** Kiedy następny konkurs i jakiego typu (TechArena France datacom: optimize? data?)? Jeśli
  optimize jest za kilka tygodni, P7 i część P4 idą przed P5. Czy roadmapa pkt 1 (jakość kodu, allowlista
  w `settings.json`) jest jeszcze w toku i ma iść przed tym?
- **Q2. Uprawnienia sesji nocnych.** (a) `--dangerously-skip-permissions` tylko w worktree, guard z P2 jako
  zabezpieczenie (prosto, guard musi być szerszy); (b) allowlista komend z roadmapy pkt 1 + tryb auto (bezpieczniej,
  część zadań utknie na braku zgody). Oraz: czy w nocy wolno zgłaszać na leaderboard (`auto_submit`) i deployować?
- **Q3. Powiadomienia.** Kanał: ntfy.sh (zero konta, aplikacja na telefon), Telegram (bot, później można
  odpowiadać), Discord webhook, mail? Czy chcesz kiedyś odpowiadać na Q-### z telefonu?
- **Q4. Recenzja krzyżowa.** Każde zadanie, tylko zadania oznaczone jako ryzykowne, czy raz na kamień milowy?
  Czy znaleziony „błąd” blokuje scalenie? (Każde zadanie = mniej więcej podwójne zużycie limitów.)
- **Q5. Noc bez gotowych zadań.** Koniec przebiegu czy research nowych kierunków (jak dziś mówi
  `protocols/questions.md`)? Research w nocy pali limity, ale rano masz gotowe pomysły.
- **Q6. Hooki we własnych repo konkursów.** Czy `.claude/settings.json` z hookami może być commitowany, skoro
  koledzy też klonują repo (hooki zadziałają u nich)? Jeśli nie: wszędzie `settings.local.json`.

## 13. Co zostaje dla wykonawcy

Nazwy modułów w `houselib/`, dokładny format wyjścia `doctor --context`, wzorce regex sekretów (reużyć z
`export.py`), treść briefów dla sesji nocnych, układ testów. Każdy pakiet dostaje własny plan
(superpowers:writing-plans) przed budową.

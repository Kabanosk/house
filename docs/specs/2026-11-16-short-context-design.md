# Krótki kontekst: przekazanie, kompakcja i wybiórcze czytanie (2026-11-16)

_Stan: zrewidowany 2026-11-17 po researchu i recenzji, **zaakceptowany ponownie przez Wojtka 2026-11-17**. Pierwotnie
zaakceptowany 2026-11-16 (odpowiedzi w §12). K1 i zdarzenia `usage`/`compaction` są przeniesione do speca Agno
(A2, A0) i tam jest ich wersja obowiązująca; tam też stoi jedyna obowiązująca kolejność łączna (spec Agno §5).
Źródła: paper NVIDIA „You don't need long context” (arXiv 2611.05306, notatka:
`docs/research/2611.05306-you-dont-need-long-context.md`), pomiar naszych sesji z 2026-11-16 (§4), research
`docs/research/reports/Research speców Agno i kontekstu.md` (odwołania „[R K2.1]” to wiersze jego tabeli, tam są
linki) i recenzja `docs/research/reports/Recenzja Fable speców Agno i kontekstu.md` (odwołania „[Rec W5]”). Spec
**uzupełnia** `2026-11-16-agno-adoptions-design.md` (pakiety A0, A1, A2, A3, A4) i nie powtarza tego, co tam już
jest. Oznaczenia decyzji: **[moja decyzja]** podjął Claude 2026-11-16, **[decyzja Claude]** podjął Claude
2026-11-17 (trzeba je sprawdzić przy czytaniu), „(Wojtek, 2026-11-17)” to decyzje Wojtka. Pytania do Wojtka są
w sekcji 12._

## 1. Po co

Paper pokazuje na 13 benchmarkach, że agent nie traci dokładności, gdy historię rozmowy co jakiś czas zastępuje
krótki brief, a duże wejścia czyta wybiórczo z plików. Często nawet zyskuje: SWE-bench Verified przy Opus 4.5
wzrósł z 70,0 do 74,8, a zadania z długim wejściem do +40 pp. Dwie rzeczy z tego dotyczą nas wprost:

1. **Treść przekazania.** Najlepiej działa brief „co dalej”, nie streszczenie „co było”. Paper podaje dokładną
   strukturę i prompt, który ją wymusza. Szablon A2 ze speca Agno ma zbliżony kształt, ale brakowało w nim trzech
   rzeczy, które paper uznaje za kluczowe (§6).
2. **Wybiórcze czytanie.** Duży dokument leży w pliku, a agent czyta z niego `grep` i ograniczony zakres. W tych
   zadaniach dokładność rosła najmocniej, a liczba tokenów spadała najbardziej.

Uwaga kosztowa. Paper liczy oszczędności przy hipotetycznym cenniku, w którym krótki kontekst jest tańszy za token.
My płacimy (lub zużywamy limit) **po stałej stawce za token**. Według ich własnych tabel przy dzisiejszych cenach
kompakcja czasem obniża koszt, a czasem go podnosi. Dlatego ten spec **najpierw mierzy**, a progi kompakcji wyznacza
z naszych danych, nie z ich benchmarków. Wyjątek to próg automatu (K2a): jest odporny na niepewność wag (§4).

## 2. Cele, nie-cele, anty-cele

**Cele**
- Kolejna próba, kolejna sesja i sesja po kompakcji dostają brief, który wystarcza do dalszej pracy bez
  ponownego odkrywania: fakty oddzielone od przypuszczeń, ślepe uliczki, następny krok i warunek ukończenia.
- Wiemy z liczb, ile kosztują nasze sesje i z czego ten koszt się bierze.
- Kompakcja w długich sesjach następuje wtedy, gdy się zwraca (§4), nie wcześniej i nie dopiero przy ~967K.
- Dokumenty źródłowe (regulamin, dokumentacja API, opis danych) leżą w plikach. Agenci czytają je fragmentami
  i przekazują sobie ścieżki, nie treść.

**Nie-cele**
- Własny harness w stylu mini-swe-agent, SAC (kompakcja po każdej akcji) i trenowanie modeli (SSSM).
- Briefing dla człowieka (`protocols/briefing.md`). Jego to nie dotyczy.
- Silnik wyszukiwania (osobny czat). Tu tylko dokładamy miejsce (`.house/sources/`), które może potem zaindeksować.
- Tryb dzienny i przekazanie na koniec dnia (roadmapa pkt 2). Skorzystają z tego samego szablonu, ale buduje je
  ich własny spec.

**Anty-cele** (porażka, nawet jeśli działa)
- Kompakcja, która podnosi łączny koszt sesji albo próby, mierzony przez K0.
- Brief dłuższy niż praca, którą oszczędza: kilka tysięcy tokenów wyjścia na każdą próbę.
- Przypuszczenie agenta, które po dwóch kompakcjach wraca jako „ustalony fakt”.
- Zgubione ustalenia człowieka (D-###, odpowiedzi na Q-###) po kompakcji w sesji coordinatora.

## 3. Ograniczenia

- To samo co w specu Agno §3: Python 3.11+, tylko biblioteka standardowa, tolerancyjny parser zapisów sesji,
  polityka gita dla nowych plików w `.house/`.
- Kompakcji w Claude Code nie uruchamia ani model, ani hook (dokumentacja, sprawdzone 2026-11-16). W sesji
  interaktywnej uruchamia ją człowiek (`/compact`), automat przy progu okna albo kompakcja przy bezczynności
  (domyślnie włączona: sesja na TTL cache 1 h, kontekst ≥200K, ~1 h od ostatniego żądania, tuż przed wygaśnięciem
  cache) [R K2.2].
- Streszczenie kompakcji pisze Claude Code. `house` daje mu tylko instrukcje (`PreCompact`) i widzi wynik po fakcie
  (`PostCompact`). Limity długości w sesji interaktywnej są więc wskazówką, nie przycięciem [R A2.1].
- Świeża sesja Claude Code w repo konkursu zaczyna od ~40K tokenów (§4). Nasze progi nie mogą schodzić tak nisko
  jak w paperze (16K–48K), bo samo podłoże sesji jest od nich większe albo im równe.

## 4. Dane: na co idą nasze tokeny

Pomiar z 2026-11-16 jednorazowym skryptem: 30 sesji Claude Code z `~/.claude/projects`, bez zapisów podagentów
i bez wywołań bocznych (Haiku do tytułów i WebSearch, które są tylko w `cost-state`), jedno wywołanie API liczone
raz (po `message.id`). Prawdziwy koszt jest więc wyższy; K0 liczy oba [R A0.1] [R A0.3]. Koszt ważony względnymi
cenami API: wejście 1, odczyt z cache 0,1, zapis do cache 1,25 (TTL 5 min) albo 2 (TTL 1 h), wyjście 5.

**Wagi limitów subskrypcji są nieznane** (pytanie 1 z §12 zamknięte 2026-11-17). Anthropic ich nie publikuje,
a anegdotyczne zgłoszenia sugerują, że odczyt z cache waży w limicie tygodniowym więcej niż 0,1 [R K0.2]. K0
podpisuje wynik jako oszacowanie kosztu API, nie rachunek i nie zużycie limitu.

| | wynik |
|---|---|
| kontekst przy pierwszym wywołaniu sesji (mediana) | ~41K |
| szczytowy kontekst (mediana / maksimum) | ~80K / 897K |
| udział w koszcie: odczyt z cache / zapis do cache / wyjście / wejście | 72% / 14% / 13% / ~0% |
| największa sesja (ccc2026) | 377 wywołań, 192M tokenów odczytu z cache, 315K tokenów wyjścia |

Wnioski:
- **Koszt to głównie wielokrotne czytanie długiej historii.** Każde wywołanie czyta cały dotychczasowy kontekst,
  więc łączny koszt sesji bez kompakcji rośnie mniej więcej z kwadratem liczby kroków. To właśnie skraca kompakcja.
- **Szczyt 897K nie jest anomalią.** Modele z natywnym oknem 1M (Sonnet 5+, Fable, Haiku 5.5, Opus 4.7+) kompaktują
  domyślnie dopiero przy ~967K [R K2.1]. Obniżenie progu automatu to największa pojedyncza dźwignia (K2a).
- **Kompakcja też kosztuje.** Recap to tokeny wyjścia (×5). Nowy prefiks (podłoże P ≈ 40K plus recap R) trzeba
  zapisać do cache: ×1,25 przy TTL 5 min, ×2 przy TTL 1 h. Na subskrypcji główne tury mają TTL 1 h, więc
  domyślnie w = 2 [R K2.3]. Do tego samo wywołanie streszczające czyta całą historię L: przy ciepłym cache za
  0,1·L [R K2.3].

Bilans kompakcji z kontekstu L do P + R przy **ciepłym cache** (poprawiony 2026-11-17). Koszt jednorazowy:
`5R + w·(P+R) + 0,1·L`. Oszczędność na każdym kolejnym wywołaniu: `0,1·(L − P − R)`. Kompakcja zwraca się po
`n* = (5R + w·(P+R) + 0,1·L) / (0,1·(L − P − R))` wywołaniach. Dla P = 40K, R = 2K, w = 2:

| L (kontekst przed) | n* (2026-11-16, bez odczytu historii) | n* poprawione, ciepły cache |
|---|---|---|
| 400K | ~3 | ~4 |
| 200K | ~6 | ~7 |
| 150K | ~9 | ~10 |
| 80K | ~25 | ~27 |

**Zimny cache** (przerwa dłuższa niż TTL) **[decyzja Claude]**. Research liczy dla niego n* rzędu 14–46 i wnioskuje,
że kompakcja dużej zimnej sesji jest droga [R K2.3]. To porównanie pomija, że **dalsza praca bez kompakcji też
płaci** po przerwie: pierwsze wywołanie zapisuje całą historię do cache od nowa (w·L). Kompakcja na zimnym cache
płaci za odczyt historii najwyżej tyle samo, więc zwraca się nie później niż w tabeli z 2026-11-16, a przy
L > ~94K często od razu. Wniosek z researchu, który zostaje: gdy następna praca nie potrzebuje historii (nowe,
niezależne zadanie), `/clear` i stan z plików kosztuje mniej niż jakakolwiek kompakcja [R K2.4].

Kompakcja przy dużym kontekście zwraca się szybko. Przy kontekście bliskim podłożu się nie zwraca, a SAC
w Claude Code byłby drogi. Rachunek pomija ponowne odkrywanie po kompakcji. Paper w swoich zadaniach go nie
zaobserwował, a u nas zmierzy go K4.

## 5. Pakiety i kolejność

| Pakiet | Co | Zależy od | Musi być gotowe przed |
|---|---|---|---|
| K0 | `house cost`: jedna tabela kosztu sesji konkursu | A0 | K2b, K4 |
| K1 | Poprawki szablonu przekazania (w A2) | — (zmiana speca A2, zrobiona) | plan A2 |
| K2a | Próg automatu kompakcji przez `house adapt` (sama konfiguracja) | — | K4 |
| K2b | Przypomnienia o kompakcji na granicach pracy | K0, dane z K4 | — |
| K3 | Wybiórcze czytanie: `.house/sources/` poza modelem, reguły w profilach | — (część o wynikach komend: A4) | eval K3 w A3 |
| K4 | Ocena: koszt i skuteczność prób przed i po | noc bazowa, K0, A1, A2, K2a | decyzja o K2b |

Podział K2 na K2a i K2b **[decyzja Claude]** [Rec W5]: K2a to jedna linia konfiguracji, odporna na niepewność wag,
i nie zależy od niczego. K2b to jedyna część, która naprawdę potrzebuje danych i ma nierozstrzygnięty mechanizm.

**Kolejność**: jedyna obowiązująca jest w specu Agno §5 (kroki 0–6). Dla pakietów K: noc bazowa (krok 0) idzie
przed K2a, bo K2a zmienia też sesje workerów `night`; K2a i K3 część dokumentowa idą w kroku 1; K0 w kroku 5;
K4 i decyzja o K2b w kroku 6.

Poza zakresem, ale korzysta później: przekazanie w trybie dziennym (roadmapa pkt 2), `house search` (indeksuje
`.house/sources/`).

## 6. K1: poprawki szablonu przekazania (do A2)

_Przeniesione do speca Agno, §8 (A2). Tam jest wersja obowiązująca: szablon, reguły instrukcji, limity
i akceptacja. Poniżej zostaje tylko uzasadnienie._

**Problem.** Szablon A2 (z Agno) miał sekcje uporządkowane od najważniejszej: Cel; W toku / następne kroki;
Krytyczny kontekst dosłownie; Ograniczenia i ustalenia człowieka; Decyzje i fakty; Błędy i poprawki; Zrobione.
Paper (Appendix A i G) znalazł, że najwięcej dokładności chronią trzy rzeczy, których A2 nie wymuszał:
oddzielenie obserwacji od przypuszczeń, ślepe uliczki jako osobna lista i warunek ukończenia.

**Uzasadnienie zmian w A2.**
- **Zaobserwowane / Niesprawdzone**: odpowiedź na anty-cel „przypuszczenie, które po dwóch kompakcjach wraca jako
  fakt”. Wpis bez źródła zostaje przypuszczeniem.
- **Ślepe uliczki przed Decyzjami** **[moja decyzja]**: przy kolejnej próbie są najcenniejsze. Ich osobny wkład
  nie ma opublikowanej ablacji; zmierzy go K4 (research, część o A2).
- **Warunek ukończenia wpisywany przez kod**: w `house` jest stały (`house check` drukuje GREEN, potem
  `status: review`; dla Codexa inny, spec Agno A2b).
- **„Przerwij pracę. Napisz tylko przekazanie”**: paper pokazał, że bez tego zakazu model próbuje dalej działać.
- **Długość** (Wojtek, 2026-11-16, doprecyzowane 2026-11-17): sufity, nie cele. Brief `night` ma sufit 8 000
  znaków (~2K tokenów), który egzekwuje kod. W sesji interaktywnej ~8K tokenów to tylko wskazówka w instrukcji,
  bo streszczenie pisze Claude Code; cel w instrukcji to ~2–3K tokenów, jak w punktach odniesienia Anthropic
  [R K1.1] [R A2.7]. Argument kosztowy z §4 (×5 za wyjście) jest za słaby, żeby ciąć niżej, bo przekazanie pisze
  się raz na kompakcję. Paper pokazał, że gadatliwy model przy ciasnym progu nie zdąży nic zrobić (Qwen 3 Coder
  Next spadł z 53% do 3,6%), stąd sufit.
- **Ustalenia człowieka przywraca kod**, nie streszczenie: po kompakcji prompt `/compact` Claude Code zachował 53%
  reguł po jednej rundzie i 10% po pięciu, a przypięcie poza streszczeniem przywraca 0% naruszeń [R A2.2]. Stąd
  tryb `compact` hooka `SessionStart` z pełnym tekstem obowiązujących D-### człowieka (spec Agno A2a).

## 7. K0: profil kosztu (`house cost`)

**Problem.** Decyzja o K2b i ocena zmian (K4) wymagają liczb. Dziś mamy tylko jednorazowy skrypt z §4.

**Zdarzenia** pochodzą z A0 (spec Agno §6): `usage` per wywołanie (cztery koszyki, deduplikacja globalna,
znormalizowany Codex), `session_total` (z `cost-state` albo linii `result`) i `compaction` (z `preTokens`
i `postTokens`). Same liczby, bez treści, więc granica prywatności A0 się nie przesuwa.

**Zmiana: `house cost`** (Wojtek, 2026-11-17: jedna tabela).
- `house cost <konkurs> [--since DATA]`: jeden wiersz na sesję konkursu, sumy dla drzewa sesji (rodzic
  i podagenci). Sesje: interaktywne (przypisane po `cwd`, spec Agno §6), workery `night` (z logów strumienia
  w rejestrze prób), przebiegi zadań Signal w repo konkursu (jeśli są). Kolumny:
  - rodzaj sesji (`interactive` | `night` | `signal`), data, model główny;
  - liczba wywołań, kontekst pierwszego wywołania (podłoże), mediana i szczyt kontekstu;
  - sumy czterech rodzajów tokenów z `usage`;
  - kompakcje: liczba i największe `preTokens`;
  - **„poza zapisem”**: `session_total − Σ usage` per model, czyli wywołania boczne, raportowane jako osobna
    kolumna, nie jako błąd [R K0.1];
  - koszt ważony (wagi w konfiguracji, domyślnie względne ceny API z §4 **[moja decyzja]**).
- Wiersz bez `usage` per wywołanie (zadanie Signal, gdy strumień go nie niesie; sesja bez `cost-state`) pokazuje
  to, co ma, z dopiskiem „tylko suma” albo „brak danych”, bez wywrotki.
- Stopka zawsze: „oszacowanie kosztu API, nie rachunek i nie zużycie limitu subskrypcji”.
- Wynik idzie na standardowe wyjście. Raport K4 zapisuje go do `docs/`, ale tylko liczby, bez ścieżek prywatnych
  projektów spoza konkursu.

Koszt per zadanie (`house cost <konkurs> T###`), widok jednej sesji z przebiegiem kontekstu i n* dla prawdziwych
kompakcji są w §14 „Na później” (Wojtek, 2026-11-17).

**Akceptacja.** Testy na syntetycznych zapisach i strumieniach: powtórzony rekord liczony raz, sesja z kompakcją
(liczba i `preTokens`), sesja z podagentem zsumowana w drzewie, sesja `night` liczona raz mimo strumienia i zapisu
o tym samym ID, kolumna „poza zapisem” z `cost-state` zawierającym dodatkowy model Haiku, konkurs pod symlinkiem
i sesja w `.worktrees/` przypisane do konkursu, brak pól `usage` („brak danych”, bez wywrotki). Wynik dla fixture'a
zgadza się z wartościami policzonymi ręcznie.

## 8. K2: polityka kompakcji w długich sesjach

**Problem.** Sesje coordinatora rosną do setek tysięcy tokenów (897K maksimum w §4), bo na modelach z oknem 1M
automat odpala dopiero przy ~967K. Każde wywołanie czyta to od nowa. Paper pokazuje, że wcześniejsza kompakcja nie
szkodzi dokładności, a nasze dane, że przy takim rozmiarze zwraca się po kilku wywołaniach. A2 adresuje treść
streszczenia, K2 jego moment.

**Co Claude Code pozwala** (dokumentacja, sprawdzone 2026-11-16 i w researchu 2026-11-17) [R K2.1] [R K2.2]:
- kompakcji nie uruchamia ani hook, ani model. Kompakcję zaczyna człowiek (`/compact`), automat albo bezczynność;
- kolejność ustawień progu automatu: najwyższy priorytet ma zmienna `CLAUDE_CODE_AUTO_COMPACT_WINDOW`; ustawienie
  `autoCompactWindow` (100K–1M) działa w każdym pliku ustawień; `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` to procent
  **okna automatu**, nie próg w tokenach, i może go tylko obniżyć;
- kompakcja przy bezczynności: próg `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS` (domyślnie 200K, minimum 100K). Odpala
  `PreCompact` z matcherem `auto`, więc nasze instrukcje działają, i dzieje się przy ciepłym cache;
- `PreCompact`: stdout hooka to instrukcje streszczenia (spec Agno A2a);
- hook nie dostaje rozmiaru kontekstu; musi go policzyć sam z `transcript_path`.

### K2a: próg automatu (sama konfiguracja)

- `house adapt` wpisuje `autoCompactWindow: 200000` do `settings.local.json` konkursu. Wartość ustawiona przez
  użytkownika wygrywa (rozszerzenie `merge_settings`, spec Agno §16). Zmienna `CLAUDE_CODE_AUTO_COMPACT_WINDOW`
  zostaje dla skryptów. `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` nie jest używane.
- `house adapt` wpisuje też `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS=150000` do `env` w `settings.local.json`
  konkursu (Wojtek, 2026-11-17, pytanie 7). Sprawdzenie uruchomieniem jak dla progu automatu.
- Próg kompakcji przy bezczynności zostaje domyślny (200K), dopóki Wojtek nie odpowie na pytanie 7 (§12).
- Ustawienie obejmuje też workery `night`, bo `night.worker_command` podaje `--settings` z `settings.local.json`
  konkursu. Dlatego K2a wchodzi po nocy bazowej (spec Agno §5, krok 0).
- Uzasadnienie bez czekania na K0: przy 400K kompakcja zwraca się po ~4 wywołaniach (§4), a jeśli limit
  subskrypcji liczy odczyt z cache drożej niż API, jeszcze szybciej [Rec W5].

**Akceptacja K2a.**
- Test: `house adapt` wpisuje `autoCompactWindow`, nie nadpisuje wartości użytkownika, zachowuje inne klucze.
- Ręczna próba na zainstalowanej wersji (nazwy ustawień znamy tylko z dokumentacji): sesja konkursu przekracza
  ~200K, automat kompaktuje, `PreCompact` odpala z `trigger: auto`, `PostCompact` zapisuje wiersz,
  a `SessionStart` w trybie `compact` przywraca ustalenia człowieka. Wynik próby (wersja CLI, próg, przy którym
  odpaliło) wpisać do planu K2a.

### K2b: przypomnienia na granicach pracy (po K0)

- **Polityka: hybryda** (Wojtek, 2026-11-16: „czasem przypomina, czasem automatycznie”). Progi są wstępne
  **[moja decyzja]**, a K0 i K4 je potwierdzą:
  - poniżej **~120K** nic się nie dzieje. Nigdy poniżej ~2× podłoża (~80K), bo tam kompakcja się nie zwraca;
  - **~120K–~200K**: przypomnienie, ale tylko na granicy pracy. Decyduje człowiek. Treść przypomnienia zależy od
    tego, co dalej: następne zadanie niezależne (np. po `house merge`) → „`/clear`, stan wróci z `SessionStart`”;
    praca ciągła → „`/compact`” [R K2.4];
  - od **~200K**: automat z K2a jako siatka bezpieczeństwa, z naszymi instrukcjami z `PreCompact`.
- **Nierozstrzygnięte, do sprawdzenia uruchomieniem przed planem K2b** [Rec N3] [Rec N4]:
  - kanał: zwykły stdout hooka `PostToolUse` nie jest pokazywany ani modelowi, ani człowiekowi w widoku domyślnym.
    Trzeba wskazać pole wyjścia hooka, które trafia do człowieka (komunikat dla użytkownika) albo do modelu
    (kontekst, który model powtórzy), i sprawdzić je w próbie;
  - zdarzenia: `house merge` jest komendą, którą hook rozpozna. „Wybór pomysłu” i „zamrożenie planu” trzeba
    przypiąć do konkretnej komendy albo z nich zrezygnować;
  - koszt: hook odpala się po każdym Bash, więc najpierw tani filtr po komendzie, potem odczyt ostatniego rekordu
    `usage` od końca pliku zapisu, nie pełne parsowanie przez A0.
- `house night` się nie zmienia: każda próba to już świeża sesja.

**Akceptacja K2b.** Na porównywalnej pracy przed i po (K4): niższy koszt ważony na scalone zadanie i niższy szczyt
kontekstu, przy tej samej liczbie scalonych zadań. Ręcznie: po kompakcji coordinator zna otwarte Q-### i obowiązujące
D-### człowieka bez ponownego czytania całego `DECISIONS.md`.

## 9. K3: wybiórcze czytanie długich wejść

**Problem.** Regulamin, treść zadania i dokumentacja trafiają dziś do rozmowy wklejone przez człowieka
(`/house-start`) albo czytane w całości. Każdy taki token jest potem czytany od nowa w każdym kolejnym wywołaniu,
a coordinator przekazuje podagentom treść zamiast ścieżki. W paperze właśnie tu był największy zysk dokładności
(od −0,4 do +40 pp przy oknie odczytu 6–8K znaków) [R K3.2].

**Zmiana.**
- **Materiały trafiają do `.house/sources/` poza modelem** [Rec S6]. Tekst wklejony do rozmowy już siedzi
  w kontekście tej sesji, a przepisanie go narzędziem Write to tokeny wyjścia (×5) bez gwarancji wierności
  (regulamin 500K znaków to ~125K tokenów wyjścia). Dlatego:
  - `house sources add <konkurs> <plik|-> [--name NAZWA] [--replace]` kopiuje plik (albo stdin) bajt w bajt
    do `.house/sources/<nazwa>` i dopisuje do `.house/sources/index.json` nazwę, sumę SHA-256, rozmiar, liczbę linii, datę i pochodzenie
    (nazwa pliku albo `stdin`). Pobieranie z URL jest w §14 **[decyzja Claude]**;
  - `/house-start` prosi człowieka o pliki albo o `house sources add` i dostaje ścieżki. Wklejka zostaje jako
    wyjście awaryjne dla krótkich tekstów (do ~5 000 znaków), zapisywanych przez `house sources add -`;
  - `spec.yaml` i zadania odwołują się do pliku i zakresu linii, a nie do treści. Edycja pliku przesuwa zakresy,
    więc `house sources` zgłasza plik, którego suma różni się od `index.json`.
- `house sources <konkurs>`: lista plików z rozmiarem w znakach, przybliżoną liczbą tokenów (znaki/4), liczbą
  linii, **spisem nagłówków Markdown z numerami linii** (odpowiednik `doc-stats` z paperu; agent skacze do
  zakresu zamiast zgadywać słowa do grepa), ostrzeżeniem o liniach dłuższych niż ~2 000 znaków i o zmienionej
  sumie [R K3.3]. Do czytania wystarczą Read z offset/limit, Grep, `sed -n` i `grep -n` **[moja decyzja: bez
  własnego czytnika]**.
- Reguła w profilach coordinatora, kodera, researchera i judge'a oraz w generowanym `AGENTS.md`, w kilku liniach:
  - plik większy niż ~40 KB (~10K tokenów): najpierw spis z `house sources` albo `grep -n` na pojęcia z pytania,
    potem odczyt zakresu wokół trafień;
  - jeden odczyt to najwyżej ~200 linii albo ~16 000 znaków [R K3.2];
  - pliki w `.house/sources/` czyta się zawsze zakresem: Read z `offset`/`limit`, nigdy z `allow_large: true`
    (Read od v2.1.296 czyta wtedy cały plik dopasowany do wolnego kontekstu, co unieważnia K3) [R K3.1];
  - w briefie dla podagenta: ścieżka i zakres linii, nigdy treść dokumentu (zasada just-in-time).
- Wyniki komend (logi treningu, pełne testy): `house out` z A4. K3 tylko odwołuje się do niego w regułach.
- `.house/sources/` i `index.json` są śledzone w repo konkursu, które widzi drużyna (spec Agno §3). Nie trafiają
  do eksportu (jak całe `.house/`).

**Akceptacja.**
- Test `house sources add`: plik i stdin skopiowane bajt w bajt, wpis w `index.json` z sumą; ponowne dodanie tej
  samej nazwy odmawia bez `--replace`.
- Test `house sources`: rozmiary, spis nagłówków z numerami linii, długa linia, zmieniona suma, pusty katalog,
  plik binarny pominięty.
- Test `/house-start` na sztucznym konkursie: materiały w `.house/sources/` przez `house sources add`, a `spec.yaml`
  odwołuje się do nich ścieżką i zakresem.
- Gdy istnieje A3, przypadek evalu: pytanie o regulamin z fixture'a o długości ~500K znaków. Porażka (`forbid`),
  jeśli agent czyta plik bez `limit`, z `allow_large` albo wkleja go do briefu podagenta.

## 10. K4: ocena po wdrożeniu

**Stan „przed”** (Wojtek, 2026-11-17) [Rec W4]: noc bazowa z kroku 0 (spec Agno §5) na konkursie testowym, na
kodzie sprzed A1, A2 i K2a, oraz jednorazowy zrzut liczb skryptem z §4 zrobiony zaraz po niej: sesje workerów tej
nocy (po `cwd` w `.worktrees/` konkursu) i sesje interaktywne konkursów z ostatnich 30 dni. Zapisy sesji znikają
po `cleanupPeriodDays` (domyślnie 30 dni), więc zrzut powstaje od razu i zawiera same liczby, bez treści
(pytanie 6 w §12). Do tego wynik nocy z jej raportu: zadania scalone, próby, timeouty, odmowy bramki. Raport nocy
pokaże też, czy problemem są kolejne próby, czy raczej timeouty i odmowy bramki.

**Stan „po”**: co najmniej jedna prawdziwa noc albo konkurs z A1, A2 (A2a i A2b) i K2a, policzone przez K0
i `.house/attempts.jsonl`.

**Co:**
- odsetek zadań zielonych w próbie ≥ 2 (czy brief ze ślepymi uliczkami pomaga);
- wskaźnik powtórzeń ślepych uliczek z A1 (komendy z błędem powtórzone identycznie w kolejnej próbie);
- tokeny i koszt ważony na próbę i na scalone zadanie, z kolumną „poza zapisem”;
- szczytowy kontekst sesji coordinatora i liczba kompakcji (z K0 i `.house/tmp/compactions.jsonl`);
- obecność sekcji w prawdziwych streszczeniach kompakcji (licznik `PostCompact`);
- ręcznie: ile razy człowiek musiał coś powtórzyć po kompakcji.

**Wynik:** raport w `docs/` (jak `context-budget.md`) i decyzje: próg K2a zostaje lub się zmienia, K2b jest
budowane albo nie. Jeśli kompakcja podnosi koszt na scalone zadanie, K2a jest cofane (anty-cel z §2).

## 11. Sprawdzone i odrzucone

- **SAC (kompakcja po każdej akcji)**: przy podłożu ~40K każda akcja kosztowałaby nową sesję i zapis cache.
  Zwraca się tylko przy podłożu kilku tysięcy tokenów, jak w ich harnessie.
- **Stałe niskie progi z paperu (16K–48K)**: poniżej albo na poziomie naszego podłoża.
- **Własny harness lub mini-swe-agent**: sprzeczne z D4 (Claude Code i Codex są naszymi agentami).
- **Kompakcja jako streszczenie**: paper wprost pokazał, że przegrywa z briefem „co dalej”.
- **Własny czytnik dokumentów (`doc-read`)**: Read, Grep i `sed` już dają ograniczone odczyty. Wystarczy reguła
  i manifest.
- **SSSM**: to propozycja trenowania modeli, nie zmiana po naszej stronie.
- **Kompakcja kotwiczona / iteracyjne streszczenia** (2026-11-17): prośba do streszczacza Claude Code o przeniesienie
  sekcji z poprzedniego przekazania bez zmian. Streszczacza nie kontrolujemy i nie sprawdzimy wykonania; to, co
  musi przetrwać, przywraca kod (spec Agno A2a) **[decyzja Claude]**.
- **Zapis wklejonego dokumentu przez model** (2026-11-17): drogi i niewierny; materiały wchodzą przez
  `house sources add` [Rec S6].
- **Telemetria OTel jako walidacja K0** (2026-11-17): `cost-state` i linia `result` dają sumy z wywołaniami
  bocznymi bez usług [R K0.1]. OTel jest w specu Agno §17.

## 12. Pytania do Wojtka

Odpowiedzi Wojtka z 2026-11-16:

1. **Wagi kosztu**: wagi API jako przybliżenie, K4 je sprawdzi. _Zamknięte 2026-11-17: wagi limitów subskrypcji
   są nieznane i Anthropic ich nie publikuje; K0 podpisuje wynik jako oszacowanie kosztu API (§4)._
2. **K2 w sesji interaktywnej**: hybryda. Na granicach pracy przypomnienie, wyżej automat (§8). _Od 2026-11-17
   realizowane jako K2a (automat) i K2b (przypomnienia, po danych)._
3. **Poprawki do speca Agno**: K1 i `usage`/`compaction` dopisane bezpośrednio do speca Agno (A2, A0).
4. **PDF paperu**: nie trafia do repo. Zostaje notatka z linkiem do arXiv.
5. **Limit przekazania** (pytanie z przeglądu): 2K dla próby i 8K dla coordinatora, jako sufity (§6). _Od
   2026-11-17: 8K w sesji interaktywnej to wskazówka, bo streszczenie pisze Claude Code._

### Pytania do Wojtka (2026-11-17)

6. **Przechowywanie zapisów sesji.** Zapisy w `~/.claude/projects` znikają po 30 dniach (`cleanupPeriodDays`).
   Czy podnieść ten limit w `~/.claude/settings.json` (np. do 90 dni), żeby K4 i `house evidence --session` miały
   dane z całego konkursu i okresu „przed”? Ustawienie jest globalne, więc dłużej przechowuje też zapisy
   projektów służbowych. Bez zmiany zrzut „przed” (§10) musi powstać w ciągu 30 dni od nocy bazowej.
   _Odpowiedź (Wojtek, 2026-11-17): tak, 90 dni (`cleanupPeriodDays: 90` w `~/.claude/settings.json`)._
7. **Kompakcja przy bezczynności.** Research proponuje obniżyć `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS` do ~120–150K:
   po ~1 h przerwy sesja w paśmie 120–200K kompaktowałaby się sama, przy ciepłym cache, z naszymi instrukcjami.
   To kłóci się z ustaloną polityką „w paśmie 120–200K przypomnienie, decyduje człowiek”. Obniżyć (sesja po
   przerwie wraca skompaktowana), czy zostawić domyślne 200K (do decyzji po K4)?
   _Odpowiedź (Wojtek, 2026-11-17): obniżyć do 150K. Przypomnienia K2b działają, kiedy człowiek pracuje;
   po ~1 h przerwy sesja ≥150K kompaktuje się sama, przy ciepłym cache i z naszymi instrukcjami._

## 13. Co zostaje dla wykonawcy

- Rozstrzygnięte przez research 2026-11-17 (do potwierdzenia na zainstalowanej wersji, nie do odkrywania):
  znacznik kompakcji w zapisie (`compact_boundary` z `compactMetadata`), podział zapisu cache na TTL
  (`cache_creation.ephemeral_5m/1h_input_tokens`), `usage` Codexa (z normalizacją, spec Agno §6), kolejność
  zmiennych progu automatu (§8).
- Wynik ręcznej próby K2a (§8).
- Dokładny tekst instrukcji kompakcji (angielski), w ramach reguł ze speca Agno §8.
- Próg rozmiaru pliku w K3 (~40 KB to propozycja), format `index.json` i nazwy plików w `.house/sources/`.
- Przed planem K2b: kanał wyjścia hooka i zdarzenia (§8).

## 14. Na później

Każda pozycja wraca dopiero wtedy, gdy wskazany warunek jest spełniony.

- **`house cost <konkurs> T###`** (koszt prób zadania z rejestru A1) i **`house cost --session <id>`** (przebieg
  kontekstu w czasie, n* dla każdej prawdziwej kompakcji z `preTokens`/`postTokens`) [R K0.3]: gdy rejestr prób ma
  dane z co najmniej jednej nocy i K4 potrzebuje rozbicia (Wojtek, 2026-11-17: K0 to na razie jedna tabela).
- **Przyrost `rate_limits.*.used_percent` Codexa** jako kolumna K0 [R K0.2], razem z adapterem rolloutu Codexa
  (spec Agno §17).
- **Sonda po kompakcji** jako przypadek `house eval` (spec Agno §17), razem z K4.
- **Rady do playbooka coordinatora**: `/rewind` do prefiksu w cache po porzuconej ścieżce (najpierw zapisać ślepą
  uliczkę), `autoCompactWindow` 150–200K we frontmatterze podagentów czytających długie logi [R K2.5] [R K2.6].
  To porady, nie wymagania speca [Rec §7].
- **`house sources add <url>`**: gdy pojawi się dokument dostępny tylko pod adresem; pobranie HTML wymaga
  konwersji do tekstu, której stdlib nie robi dobrze.

## 15. Zmiany 2026-11-17

- Stan: zrewidowany po researchu i recenzji, czeka na ponowną akceptację; nowe oznaczenia decyzji.
- §3: dopisana kompakcja przy bezczynności i to, że limit w sesji interaktywnej jest wskazówką [R K2.2, A2.1].
- §4: pomiar bez podagentów i wywołań bocznych zaniża koszt; szczyt 897K wyjaśniony progiem ~967K [R A0.1, K2.1].
- §4: wzór opłacalności z odczytem historii przez streszczenie (+0,1·L), w = 2 domyślnie; wagi limitów zamknięte jako nieznane [R K2.3, K0.2].
- §4: wniosek researchu o drogiej kompakcji zimnej sesji poprawiony, bo dalsza praca też płaci za ponowny zapis cache [decyzja Claude].
- §5: K2 podzielone na K2a (konfiguracja, wcześnie) i K2b (przypomnienia, po danych); kolejność łączna tylko w specu Agno §5 [Rec W5].
- §6: K1 skrócone do uzasadnienia, bez powtarzania wersji obowiązującej z A2; limit 8K interaktywnie jako wskazówka, cel ~2–3K [R K1.1].
- §7: K0 to jedna tabela (Wojtek), z sumami drzewa sesji, kolumną „poza zapisem”, przypisaniem po `cwd` i stopką o oszacowaniu [R K0.1, Rec S9].
- §8: K2a wpisuje `autoCompactWindow: 200000` przez `house adapt`, sprawdzane próbą; idle compaction czeka na pytanie 7 [R K2.1, K2.2].
- §8: K2b z nierozstrzygniętym kanałem i zdarzeniami do sprawdzenia; `/clear` dla niezależnego następnego zadania [Rec N3, N4, R K2.4].
- §9: materiały do `.house/sources/` poza modelem (`house sources add`, suma kontrolna), spis nagłówków, rozmiar odczytu, zakaz `allow_large` [Rec S6, R K3.1–K3.3].
- §10: K4 ma stan „przed” z nocy bazowej i zrzutu liczb (Wojtek), plus wskaźnik powtórzeń ślepych uliczek i licznik `PostCompact` [Rec W4].
- §11–§14: nowe pozycje odrzucone, pytania 6–7 do Wojtka, „Na później” z warunkami powrotu.

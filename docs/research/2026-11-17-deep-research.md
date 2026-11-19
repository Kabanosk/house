# Deep research: jak działa, jak zaimplementować, jak ulepszyć

Data: 2026-11-17. Wszystkie URL-e poniżej zostały pobrane (WebFetch) i przeczytane. Treść stron traktowana jako dane.

Odrzucone (nie udało się otworzyć, HTTP 403): `openai.com/index/introducing-deep-research/` oraz `openai.com/index/browsecomp/`. Zastąpione przez OpenAI Deep Research System Card (PDF, przeczytany lokalnie przez pdftotext) i artykuł BrowseComp na arXiv. Liczby HLE 26.6% i GAIA 72.57% z posta OpenAI znam tylko z wtórnych źródeł, więc ich nie używam.

Uwaga o metodzie: część stron czytał pośrednio mały model streszczający (WebFetch). Liczby z blogów (Anthropic, HF, Jina, Together, LangChain) pochodzą z takich streszczeń. Liczby z PDF-ów (system card, DeepResearch Bench, BrowseComp) sprawdziłem w tekście wyciągniętym z PDF-a.

---

## A. Źródła (verified)

### Architektura i praktyka produkcyjna

1. **How we built our multi-agent research system** (Anthropic Engineering) — https://www.anthropic.com/engineering/multi-agent-research-system
   Wzorzec orchestrator-worker: lead agent (Opus 4) planuje i spawnuje 3–5 równoległych subagentów (Sonnet 4), osobny CitationAgent dokleja cytowania na końcu; plan zapisywany poza kontekstem. Multi-agent pobił single-agent Opus 4 o 90.2% na wewnętrznym evalu, kosztem ~15x tokenów względem czatu; na BrowseComp samo zużycie tokenów tłumaczy ~80% wariancji wyniku. Pułapki: subagenci dublują pracę przy niejasnych zleceniach, 50 subagentów na proste pytanie, agenci wybierają content-farmy SEO zamiast źródeł autorytatywnych.

2. **Effective context engineering for AI agents** (Anthropic Engineering) — https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
   Kompaktowanie, notatki w pliku (NOTES.md / todo) poza kontekstem, subagenci z czystym oknem zwracający streszczenie ~1–2 tys. tokenów, just-in-time retrieval (trzymaj referencje, ładuj treść narzędziem). Dobre wzorce na kompresję wyników i pamięć w plikach.

3. **Open Deep Research** (LangChain blog) — https://www.langchain.com/blog/open-deep-research
   Trzy fazy: scope (pytania doprecyzowujące, następnie research brief), research (supervisor + subagenci z izolowanym kontekstem, każdy kompresuje własne wyniki do ustalonych wniosków z cytowaniami), jednorazowy zapis raportu z briefu i wszystkich ustaleń. Lekcja: równoległe pisanie sekcji dawało niespójne raporty, więc multi-agent tylko do researchu, a pisanie raz na końcu; supervisor ma decydować, kiedy w ogóle równoleglić.

4. **langchain-ai/open_deep_research** (repo, archiwalne) — https://github.com/langchain-ai/open_deep_research
   Sloty modeli: summarization (tani, gpt-4.1-mini), research, compression, final report. Wyniki na Deep Research Bench (RACE): gpt-5 0.4943, Claude Sonnet 4 0.4401 ($187), gpt-4.1 0.4309 ($46); pełny przebieg evalu to $20–100. Pokazuje realny koszt evalu i wartość taniego modelu do streszczania.

5. **Open-source DeepResearch: freeing our search agents** (Hugging Face, smolagents) — https://huggingface.co/blog/open-deep-research
   CodeAgent (akcje jako kod Pythona) + prosta przeglądarka tekstowa + inspektor plików: 55.15% na GAIA validation (vs ok. 33% z agentem JSON-owym i ok. 46% Magentic-One; OpenAI DR 67.36%). Wniosek: forma akcji (kod zamiast JSON) liczy się bardziej niż zestaw narzędzi; ok. 30% mniej kroków. Wada: przeglądarka tekstowa nie radzi sobie z JS i 403.

6. **A Practical Guide to Implementing DeepSearch/DeepResearch** (Jina AI) — https://jina.ai/news/a-practical-guide-to-implementing-deepsearch-deepresearch/
   Jedna pętla `while` do wyczerpania budżetu tokenów: akcje search/visit/reflect/answer/coding; kolejka pytań-luk (FIFO) zamiast rekurencji; "beast mode" wymusza odpowiedź po wyczerpaniu budżetu; dedup zapytań embeddingami (nie LLM-em); rerank URL-i (częstość, struktura ścieżki, semantyka); osobny prompt oceniający odpowiedź wg kryteriów zależnych od typu pytania. Pamięć trzymana w kontekście, bez bazy wektorowej.

7. **Open Deep Research** (Together AI) — https://www.together.ai/blog/open-deep-research
   Pipeline plan, search (Tavily), self-reflection (luki, kolejne iteracje), streszczenie źródeł, ranking źródeł, writer. Ablacja: ograniczenie do jednego kroku wyszukiwania (czyli zwykły RAG) pogarsza trafność na pytaniach multi-hop. Pułapki: streszczacz wyciąga cytat zależny od kontekstu, cache bez TTL daje nieaktualne dane, błędy się kumulują między iteracjami.

8. **gpt-researcher** (assafelovic, repo) — https://github.com/assafelovic/gpt-researcher
   Planner generuje pytania badawcze, agenci wykonawczy zbierają i streszczają źródła równolegle, publisher składa raport z cytowaniami. Tryb "deep research" to drzewo (głębokość i szerokość konfigurowalne). Deklarowany koszt ok. $0.4 i ok. 5 min na przebieg (o3-mini); twierdzenia o redukcji biasu to deklaracje autorów bez niezależnej weryfikacji.

9. **Gemini Deep Research** (Google, blog z 11.12.2024) — https://blog.google/products/gemini/google-gemini-deep-research/
   Wieloetapowy plan, który użytkownik może edytować i zatwierdzić; potem pętla search→read→nowe wyszukiwania; raport z linkami do źródeł. Opis ogólnikowy, bez szczegółów implementacji.

10. **Gemini Deep Research agent (API docs)** — https://aistudio.google.com/docs/deep-research
    Praca asynchroniczna (`background=true`, polling po `id`), tryb `collaborative_planning` (agent zwraca plan do poprawek, dopiero potem raport), narzędzia: Google Search, URL Context, Code Execution, rozszerzalne o MCP i własne pliki. Dobry wzorzec UX: plan, zatwierdzenie, dopiero koszt.

11. **Deep Research System Card** (OpenAI, 2025) — https://cdn.openai.com/deep-research-system-card.pdf
    Model (wczesny o3) trenowany RL na nowych zbiorach zadań przeglądania (zadania z odpowiedzią wzorcową oraz otwarte z rubrykami ocenianymi przez model-sędziego). Sekcja o prompt injection: dodatkowe dane treningowe odporności plus mitygacja systemowa (zakaz nawigowania do dowolnych/skonstruowanych URL-i, żeby nie wyciec klucza w parametrach). Atak-success po mitygacji ok. 0–0.5% na ich evalu (przed: 1.5–4% tekst, do 18.5% multimodal), przy zastrzeżeniu o ryzyku resztkowym.

### Pipeline'y pisania raportu (papers)

12. **STORM: Assisting in Writing Wikipedia-like Articles From Scratch** (Shao i in., NAACL 2024) — https://arxiv.org/abs/2402.14207
    Etap pre-writing: odkrywanie perspektyw, symulowane rozmowy pisarzy z ekspertem opartym o źródła, wyciąganie outline'u, dopiero potem tekst z cytatami. +25 pkt bezwzględnych w "organized" i +10% w pokryciu vs baseline RAG z outline'em; zauważone: przenoszenie biasu źródeł i łączenie niepowiązanych faktów.

13. **Co-STORM: Into the Unknown Unknowns** (Jiang i in., EMNLP 2024) — https://arxiv.org/abs/2408.15232
    Użytkownik podgląda i steruje dyskusją wielu agentów; dynamiczna mind-mapa porządkuje wiedzę; raport na końcu. Przydatne jako wzorzec "człowiek wtrąca się w trakcie", a nie tylko na starcie.

14. **Deep Researcher with Test-Time Diffusion (TTD-DR)** (Han i in., Google, 2025) — https://arxiv.org/abs/2507.16075
    Raport traktowany jak szkic poprawiany iteracyjnie (diffusion-style): wstępny draft jest szkieletem, każda runda retrieval uzupełnia i poprawia draft. Redukuje utratę informacji w długich pętlach. Szczegółowych liczb nie widziałem na stronie abstraktu.

15. **WebThinker** (Li i in., NeurIPS 2025) — https://arxiv.org/abs/2504.21776
    Model rozumujący sam wchodzi w "Deep Web Explorer" i pisze raport w trakcie myślenia (think-search-draft), trenowany iteracyjnym online DPO. Kierunek: trening zamiast samego promptowania.

16. **Search-R1** (Jin i in., 2025) — https://arxiv.org/abs/2503.09516
    RL z nagrodą wyłącznie za wynik uczy model, kiedy i co szukać; +41% (Qwen2.5-7B) vs baseline RAG. Argument, że prompt-only użycie wyszukiwarki jest suboptymalne (poza zasięgiem `house`, ale warto znać).

17. **Deep Research Agents: A Systematic Examination And Roadmap** (Huang i in., 2025) — https://arxiv.org/abs/2506.18096
    Przegląd: API vs przeglądarka, static vs dynamic workflow, single vs multi-agent; wskazuje, że benchmarki słabo odzwierciedlają praktyczne cele agentów.

### Benchmarki i ewaluacja

18. **BrowseComp** (Wei i in., OpenAI, 2025) — https://arxiv.org/abs/2504.12516
    1266 pytań o trudno dostępne informacje z krótką, łatwo weryfikowalną odpowiedzią. Deep Research 51.5% (GPT-4o z przeglądarką 1.9%), ale błąd kalibracji 91%, czyli bardzo pewny siebie przy błędach. Dokładność rośnie gładko z compute w teście; głosowanie (majority, weighted, best-of-N) po wielu próbach poprawia wynik. (Liczby sprawdzone w PDF.)

19. **DeepResearch Bench** (Du i in., 2025) — https://arxiv.org/abs/2506.11763
    100 zadań na poziomie PhD, 22 dziedziny. RACE: oceny raportu przez sędziego-LLM z kryteriami generowanymi per zadanie i porównaniem z raportem referencyjnym (wymiary: pełność, głębia, zgodność z instrukcją, czytelność). FACT: wyciąga pary (twierdzenie, URL), pobiera stronę (Jina Reader), sędzia ocenia czy źródło wspiera twierdzenie; metryki Citation Accuracy i Effective Citations. Wyniki (tabela z PDF): Gemini 2.5 Pro DR 81.44% accuracy / 111.21 effective citations; OpenAI DR 77.96% / 40.79; Perplexity DR 90.24% / 31.26. Czyli komercyjne agenty cytują poprawnie tylko 78–90% twierdzeń.

20. **Deep Research Bench** (FutureSearch, Bosse i in., 2025) — https://arxiv.org/abs/2506.06287
    89 wieloetapowych zadań w 8 kategoriach; offline "RetroSearch" na zamrożonym zbiorze stron daje wyniki podobne do żywego webu, więc porównania są powtarzalne; automatyczna analiza śladów (halucynacje, zapominanie, użycie narzędzi).

21. **GAIA** (Mialon i in., 2023) — https://arxiv.org/abs/2311.12983
    466 pytań "łatwych dla ludzi": ludzie 92% vs GPT-4 z pluginami 15%. Standardowy benchmark z krótkimi odpowiedziami (używany przez HF i OpenAI).

### Cytowania i bezpieczeństwo

22. **Enabling LLMs to Generate Text with Citations (ALCE)** (Gao i in., 2023) — https://arxiv.org/abs/2305.14627
    Metryki jakości cytowań; nawet najlepsze modele nie mają pełnego poparcia w cytatach w ~50% przypadków (ELI5).

23. **Evaluating Verifiability in Generative Search Engines** (Liu, Zhang, Liang, 2023) — https://arxiv.org/abs/2304.09848
    Ocena ludzka Bing Chat, Perplexity i in.: tylko 51.5% zdań w pełni popartych cytatami (recall), tylko 74.5% cytatów popiera zdanie (precision). Uzasadnia osobny krok weryfikacji cytowań.

24. **Not what you've signed up for: Indirect Prompt Injection** (Greshake i in., 2023) — https://arxiv.org/abs/2302.12173
    Atakujący umieszcza instrukcje na stronie, którą agent pobierze (kradzież danych, kontrola wywołań API, zatruwanie informacji); autorzy: skuteczne mitygacje wciąż brakuje. Kluczowe dla każdego agenta czytającego web.

---

## 1. Jak to działa — typowa architektura

Wspólny schemat wszystkich systemów (Anthropic [1], LangChain [3], Together [7], GPT-Researcher [8], Gemini [9][10]):

1. **Doprecyzowanie i brief.** Pytania do użytkownika (jeśli niejasne), potem skondensowany "research brief" jako stały punkt odniesienia [3]. W Gemini dodatkowo plan do edycji i zatwierdzenia przed kosztownym przebiegiem [9][10].
2. **Planner / lead agent.** Rozbija brief na niezależne pod-pytania, ustala budżet wysiłku zależny od złożoności (fakt: 1 agent, 3–10 wywołań narzędzi; porównanie: 2–4 subagentów po 10–15 wywołań; złożone: 10+) [1]. Plan zapisuje do pliku/pamięci poza kontekstem [1][2].
3. **Subagenci równolegle**, każdy z własnym oknem kontekstu i zleceniem zawierającym cel, format wyjścia, wskazówki o narzędziach i granice zadania [1]. Równoległość skróciła czas nawet o 90% [1].
4. **Pętla search→read→reflect** w każdym subagencie: szeroko i krótko na początek, potem zawężanie [1]; refleksja wykrywa luki i generuje kolejne zapytania [6][7]; dedup zapytań i odwiedzonych URL-i [6]; limit iteracji lub budżet tokenów kończy pętlę, a "beast mode" wymusza odpowiedź [6].
5. **Kompresja wyników.** Subagent zwraca skondensowane ustalenia z cytowaniami (ok. 1–2 tys. tokenów), nie surowe strony [2][3]; tani model streszcza źródła, droższy pisze [4][7]; duże artefakty lądują w plikach, a do lidera idzie tylko referencja [1].
6. **Raport w jednym przebiegu** z briefu i wszystkich ustaleń (nie równolegle po sekcjach, bo wychodzi niespójny) [3]. Wariant: iteracyjnie poprawiany szkic [14] lub wcześniejszy outline z perspektyw [12].
7. **Cytowania i weryfikacja.** Osobny CitationAgent dopisuje atrybucje po zakończeniu researchu [1]. Benchmarki mierzą to odrębnie (FACT [19]), bo systemy produkcyjne mylą się często [19][22][23].
8. **Wariant "wytrenowany".** OpenAI trenuje model RL-em na zadaniach przeglądania z rubrykami [11]; Search-R1 i WebThinker robią to na mniejszą skalę [15][16]. Dla `house` nieosiągalne, a pokazuje, że cały scaffold można zastąpić treningiem.

Skalowanie: wynik zależy głównie od zużytych tokenów/wywołań (80% wariancji na BrowseComp [1]; gładka krzywa compute vs dokładność [18]). Cena: ok. 4x tokenów agenta vs czat, ok. 15x multi-agent [1].

---

## 2. Jak zaimplementować w duchu `house`

Założenia: Claude Code / Codex jako agenci wykonawcy (headless, np. `claude -p`, `codex exec`), pliki zamiast serwera, Python stdlib jako orkiestrator. Poniższe to propozycja moja, oparta na źródłach; nie sprawdzałem tego w repo `house`.

### Minimalna wersja (7 kroków)

1. **Katalog przebiegu** `research/<slug>/` z plikami: `question.md`, `brief.md`, `plan.json`, `findings/<id>.md`, `sources.jsonl`, `report.md`, `verify.json`. Pliki są pamięcią i punktem wznowienia [1][2].
2. **Scope.** Agent (lub człowiek przez Signal) doprecyzowuje pytanie i zapisuje `brief.md` [3]. Opcjonalny krok zatwierdzenia planu przed kosztownym przebiegiem [10].
3. **Plan.** Lead (jedno wywołanie modelu z "extended thinking") pisze `plan.json`: lista pod-pytań, dla każdego cel, format wyjścia, granice, limit wywołań narzędzi, sugerowane źródła. Wbudowane reguły skalowania wysiłku w prompt [1].
4. **Subagenci równolegle.** Orkiestrator w Pythonie (`concurrent.futures.ThreadPoolExecutor` + `subprocess`) odpala N (3–5) headless agentów, każdy z promptem "pod-pytanie + reguły" i uprawnieniem tylko do WebSearch/WebFetch oraz zapisu w `findings/<id>.md`. Wynik: ustalenia z cytowaniami `[url]` plus cytat dosłowny (do późniejszej weryfikacji) i wpisy do `sources.jsonl` (url, tytuł, data pobrania, hash treści).
5. **Reflect.** Lead czyta tylko `findings/*.md` (skompresowane), wskazuje luki i wypuszcza drugą turę subagentów; twardy limit rund (np. 2–3) i budżetu [1][6].
6. **Raport.** Jedno wywołanie piszące `report.md` z briefu i findings, z cytowaniami w formie `[n]` mapowanymi na `sources.jsonl` [3].
7. **Weryfikacja cytowań.** Osobny agent-krytyk dla każdego pary (twierdzenie, źródło): wczytaj zapisaną treść źródła, odpowiedz supported / partial / unsupported; niewspierane usuwa lub oznacza [19][23]. Wynik do `verify.json`.

### Co wziąć z gotowych implementacji
- Brief + supervisor + kompresja + jednorazowy zapis raportu: LangChain [3].
- Pętla z budżetem, kolejka luk, beast mode, dedup, osobny prompt oceny odpowiedzi: Jina [6].
- Role modeli: tani do streszczania i search, mocny do plan i raport [4][7].
- Edytowalny plan przed startem: Gemini [10].
- Schemat evalu (RACE/FACT): DeepResearch Bench [19].

### Na co uważać
- **Koszt tokenów.** Multi-agent to ok. 15x czatu [1]; pełny eval otwartej implementacji to $20–100 [4]. Ustal limity (rundy, wywołania, równoległość) i loguj zużycie na przebieg. Używaj researchu tylko dla zadań wartych tego kosztu [1]; do kodowania słabo pasuje [1].
- **Równoległość.** Lead czeka na batch, więc jeden wolny subagent blokuje wszystko [1]; dawaj timeouty i wznawianie od checkpointu zamiast restartu [1]. Szczegółowe zlecenia, bo inaczej subagenci dublują pracę [1]. Rate-limity API: kompresja pomaga [3].
- **Prompt injection z treści stron.** Strony to dane, nie instrukcje [24][11]. Praktyczne mitygacje (moje wnioski, wzorowane na [11]): subagent bez dostępu do sekretów i bez powłoki z siecią poza fetchem, brak dowolnych URL-i konstruowanych z danych użytkownika, brak zapisu poza `findings/`, owijanie pobranej treści w znaczniki "UNTRUSTED" i instrukcja ignorowania poleceń z niej, krytyk widzi tylko treść i twierdzenie. Residual risk zostaje nawet u OpenAI [11].
- **Cytaty bez pokrycia.** Systemy produkcyjne: 51.5% zdań w pełni popartych [23], ~50% braków wsparcia [22], 78–90% poprawnych cytatów u agentów komercyjnych [19]. Streszczacz potrafi wyciągnąć cytat zależny od kontekstu [7]. Dlatego krok 7 jest obowiązkowy, nie opcjonalny.
- **Źródła niskiej jakości.** Agenci wybierają content-farmy SEO [1]; w prompcie dodaj heurystyki jakości źródeł (pierwotne, oficjalne, daty) [1].
- **Kalibracja.** Model bywa bardzo pewny przy błędzie (calibration error 91% na BrowseComp [18]); raport powinien jawnie oznaczać pewność i luki.
- **Błędy się kumulują** między iteracjami [7]; checkpointy i dostęp do informacji o awarii narzędzia dla agenta [1].

---

## 3. Jak ulepszyć — techniki z dowodami

| # | Technika | Dowód | Jak w `house` |
|---|----------|-------|---------------|
| 1 | **Rozbicie pytania na niezależne pod-pytania + izolacja kontekstu subagentów** | Multi-agent +90.2% vs single-agent [1]; izolowanie kontekstu pozwoliło badać głębiej każdy z 3 tematów [3] | Jeden plik `findings/<id>.md` na pod-pytanie, lead nie widzi surowych stron |
| 2 | **Budżet wysiłku i reguły skalowania w prompcie** | Bez reguł: 50 subagentów na proste pytanie [1]; zależność wyniku od tokenów (80% wariancji) [1]; limit budżetu + beast mode [6] | Pole `budget` w `plan.json`; klasyfikacja prostych vs złożonych pytań na wejściu |
| 3 | **Wieloetapowe wyszukiwanie z refleksją (luki) zamiast jednego RAG** | Ograniczenie do 1 kroku pogarsza multi-hop [7]; gładki wzrost z compute [18] | 2–3 rundy z kolejką luk jak w Jina [6] |
| 4 | **Krytyk / weryfikator cytowań (twierdzenie vs treść źródła)** | Surowe cytowania zawodzą: 74.5% precision [23], ok. 50% braków [22], 78–90% [19]; osobny CitationAgent u Anthropic [1] | Krok 7; liczyć własne Citation Accuracy jak FACT [19] |
| 5 | **Eval na małym zestawie pytań + LLM-judge** | 20 realnych zapytań wystarcza na wczesnym etapie; jeden prompt sędziego z rubryką (fakty, cytaty, pełność, jakość źródeł, efektywność) [1]; RACE z kryteriami adaptacyjnymi i raportem referencyjnym [19] | `evals/questions.jsonl` + stały sędzia; porównuj wersje promptów po wyniku końcowym, nie po ścieżce [1] |
| 6 | **Powtarzalność evalu: zamrożony korpus stron** | RetroSearch daje wyniki zbliżone do żywego webu [20]; cache źródeł do ponownych przebiegów (uwaga na TTL) [7] | `sources.jsonl` z treścią i hashem, tryb "offline replay" |
| 7 | **Pamięć źródeł i plan w plikach, kompaktowanie** | Plan poza kontekstem po 200k tokenów [1]; NOTES.md i wznowienie po resecie [2] | Pliki przebiegu jako wspólna pamięć między runami; wznowienie od checkpointu |
| 8 | **Deduplikacja zapytań i URL-i (embeddingi) + ranking URL-i** | Dedup embeddingami lepszy niż LLM; rerank po częstości, ścieżce i semantyce [6] | W stdlib: normalizacja URL-i + prosty hash/sim n-gramów; embeddingi opcjonalnie |
| 9 | **Voting / best-of-N dla pytań o jedną odpowiedź** | Weighted voting i best-of-N poprawiają BrowseComp [18] | Dla pytań faktograficznych: N równoległych przebiegów, wybór wg zgodności |
| 10 | **Iteracyjny szkic raportu zamiast jednorazowego pisania z zera; outline z perspektyw** | TTD-DR: szkic jako szkielet, redukcja utraty informacji [14]; STORM: pre-writing z perspektywami +25 pkt "organized" [12] | Wersja v2: `draft.md` poprawiany po każdej rundzie reflect |
| 11 | **Plan do zatwierdzenia przez człowieka / wtrącenia w trakcie** | Collaborative planning [10]; Co-STORM [13] | Pytanie przez Signal (wg reguły z pamięci) przed kosztowną turą |
| 12 | **Heurystyki jakości źródeł i rewrite zapytań (wiele języków, formatów)** | Content-farmy SEO [1]; rewriting zapytań jako kluczowy czynnik [6] | Sekcja w prompcie subagenta z preferencją źródeł pierwotnych |
| 13 | **Strojenie opisów narzędzi/promptów przez samego agenta** | Agent testujący narzędzia skrócił czas zadań o 40% [1] | Pętla "przepisz prompt po nieudanym przebiegu" na zestawie z #5 |

### Sugerowana kolejność wdrożenia
(a) minimalna wersja 1–7, (b) eval z #5 i metryka Citation Accuracy z #4, (c) budżety #2, (d) cache/replay #6, (e) szkic iteracyjny #10 i voting #9 dopiero gdy eval pokaże potrzebę.

## Ograniczenia tego researchu
- Szczegóły architektury OpenAI Deep Research i Gemini są niepubliczne; opisy są ogólne [9][10][11].
- Większość blogów czytałem przez streszczenie narzędzia, więc cytaty i liczby warto sprawdzić w oryginale przed użyciem w dokumencie specyfikacji.
- Nie badałem repo `house`, więc propozycje z sekcji 2 są niezależne od istniejących modułów.

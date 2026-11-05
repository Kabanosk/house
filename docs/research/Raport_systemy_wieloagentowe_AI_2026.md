# Systemy wieloagentowe AI: przegląd literatury i rekomendacje dla dynamicznego software house'u

**Stan przeglądu:** 3 listopada 2026  
**Przeznaczenie:** projekt systemu, który na podstawie zadania dobiera kompetencje, tworzy lub wykorzystuje specjalistyczne instancje agentów, deleguje prace, integruje kod i weryfikuje wynik.  
**Zakres:** klasyczne Multi-Agent Systems (MAS), współczesne LLM-MAS, automatyczne generowanie ról, agentic software engineering, ewaluacja, bezpieczeństwo i propozycja architektury.

## Streszczenie

Literatura wspiera tezę, że adaptacyjny system wieloagentowy jest technicznie sensownym kierunkiem. **AutoAgents (IJCAI 2024)** wprost bada automatyczne generowanie zespołu ekspertów zależnie od zadania. **MetaGPT (ICLR 2024)** i **ChatDev (ACL 2024)** badają model wirtualnego zespołu tworzącego oprogramowanie. Klasyczna literatura o **Contract Net Protocol** i **blackboard architecture** dostarcza zasad przydzielania pracy i współdzielenia wiedzy.

Jednocześnie badania empiryczne przeczą prostemu założeniu, że więcej agentów oznacza lepszą skuteczność. W eksperymentach **Google Research (2026)** korzyści z wielu agentów zależały silnie od równoległości problemu; w zadaniach sekwencyjnych układy wieloagentowe wypadały gorzej. **MAST (NeurIPS 2025)** pokazuje błędy koordynacji i weryfikacji, a **Agentless (FSE 2025)** dowodzi wartości prostych, dobrze kontrolowanych przepływów.

**Rekomendacja projektowa (synteza autorstwa raportu, nie stwierdzony naukowo uniwersalny optimum):** zbudować *hierarchiczny, adaptacyjny system ze stałym jądrem kontrolnym*. LLM może proponować role, plan i delegacje, ale kod deterministyczny zarządza wykonaniem, uprawnieniami, budżetem, kolejkami, artefaktami oraz testami. Dynamiczni są **specjaliści**, a nie mechanizmy bezpieczeństwa i księgowania zadań.

## 1. Metoda i ograniczenia przeglądu

**Pytania badawcze:** (1) Jak organizować współpracę agentów? (2) Czy agent może sam dobierać lub generować innych agentów? (3) Co działa w automatycznym tworzeniu aplikacji? (4) Jak mierzyć rzeczywistą korzyść z multi-agentów? (5) Jakie ograniczenia muszą znaleźć się w architekturze produkcyjnej?

Przejrzano publicznie dostępne opisy prac i oficjalne strony wydawców (IEEE, IJCAI, ICLR, ACL, NeurIPS, ACM, PMLR, Springer), uzupełnione oryginalnymi raportami inżynierskimi firm i dokumentacją implementacyjną. Nie jest to pełny *systematic literature review* według PRISMA: nie wykonano wyczerpującego przeszukania baz i formalnej metaanalizy, a nie wszystkie pełne teksty prac były dostępne. Badania porównywane między sobą używają odmiennych modeli, budżetów i benchmarków; nie należy traktować ich wyników procentowych jako bezpośrednio przenośnych na hackathony.

**Hierarchia dowodów:** A — recenzowany artykuł/konferencja; B — empiryczny raport producenta (możliwy konflikt interesów); C — dokumentacja lub opis frameworka (dowód dostępnej funkcji, nie dowód skuteczności); D — wniosek architektoniczny raportu.

## 2. Klasyczne fundamenty MAS

### 2.1. Agent i jego autonomia

Wooldridge i Jennings, *Intelligent Agents: Theory and Practice* (1995), porządkują teorię agentów, architektury oraz języki implementacji. Współczesne agenty LLM łączą model z instrukcjami, narzędziami, pamięcią/staniem i pętlą działania, ale **nazwa roli w promptcie sama w sobie nie tworzy niezależnej kompetencji**. Cenna specjalizacja wymaga różnicy w kontekście, narzędziach, odpowiedzialności, kryteriach zakończenia i możliwości sprawdzenia wyniku.

### 2.2. Contract Net Protocol (1980)

Reid G. Smith przedstawił protokół przydzielania zadań przez negocjacje między zlecającym a potencjalnymi wykonawcami. W dynamicznym software house'ie manager może opublikować zadanie z wymaganiami, a dostępne profile agentów mogą być oceniane względem umiejętności, szacowanego kosztu, dostępności narzędzi i ryzyka. Nie należy mylić tej analogii z gotową implementacją LLM-ów w pracy z 1980 r.

### 2.3. Blackboard architecture (1985)

Barbara Hayes-Roth opisała architekturę sterowania z rozdziałem wiedzy domenowej i wiedzy o kontroli procesu. Dziś jej praktycznym odpowiednikiem może być repozytorium artefaktów wraz z bazą stanów: PRD, ADR, kontrakty API, wyniki testów, historia decyzji i pliki implementacji. Wspólna wiedza jest trwalsza i łatwiej audytowalna niż nieograniczony wieloosobowy czat.

## 3. Współczesne badania LLM-MAS

| Praca | Typ / rok | Mechanizm | Co wnosi do dynamicznego software house'u | Zastrzeżenie |
|---|---|---|---|---|
| **AutoAgents** — Chen i in. | IJCAI 2024, A | Generowanie potrzebnych ról, koordynacja ekspertów, observer | Najbliższy punkt odniesienia dla *agent factory* | Wyniki z benchmarków autorów, nie gwarancja skuteczności przy dużym repo |
| **MetaGPT** — Hong i in. | ICLR 2024, A | SOP-y, role programistyczne, artefakty pośrednie | Standardowe kontrakty i etapy pracy | Zbyt sztywne role mogą być nieadekwatne do nietypowego zadania |
| **ChatDev** — Qian i in. | ACL 2024, A | Komunikacja między specjalistami na etapach design/code/test | Obserwacja interfejsów i przekazywania kontekstu | Duża liczba dialogów może podnosić koszt i propagować błędy |
| **AgentVerse** — Chen i in. | ICLR 2024, A | Koordynacja grupy ekspertów, interakcje i zachowania zespołu | Projektowanie mechanizmów koordynacji | Testy obejmują różne zadania, nie wyłącznie rozwój produktów |
| **AutoGen** — Wu i in. | COLM 2024, A | Programowalne konwersacje agent-agent, narzędzia i człowiek | Elastyczny runtime dla współpracy | Framework nie rozwiązuje sam problemu poprawności |
| **MultiAgentBench** — Zhu i in. | ACL 2025, A | Benchmarki interakcji, topologie star/chain/tree/graph | Ocena koordynacji, nie tylko ostatecznego wyniku | Przewaga konkretnej topologii zależy od scenariusza |
| **MAST: Why Do Multi-Agent LLM Systems Fail?** — Cemri i in. | NeurIPS 2025, A | Analiza ponad 1600 trace'ów z 7 frameworków; 14 typów awarii | Lista rzeczy do testowania w orchestratorze | Analiza badanych frameworków, nie wszystkich możliwych systemów |
| **Agentless** — Xia i in. | FSE 2025, A | Prosty przepływ localization → repair → validation | Konieczność porównania z prostym baseline'em | Wyniki dotyczą przede wszystkim naprawiania issue, nie całych produktów |
| **Towards a Science of Scaling Agent Systems** — Kim i in. | preprint 2025 / Google Research 2026, B | 180 konfiguracji, zależność od równoległości i narzędzi | Dynamiczna decyzja: 1 czy wielu agentów? | Konkretne benchmarki i konfiguracje nie wyznaczają uniwersalnej reguły |
| **SILO-BENCH** — Zhang i in. | ACL 2026, A | 54 konfiguracje i 1620 eksperymentów koordynacji | Rozmowa nie gwarantuje skutecznego współdziałania | Eksperymenty nastawione na rozproszone problemy informacyjne |
| **PASC: Coordinating Agents on a Shared Git Workspace** — Özaytürk i Buzluca | ESEM 2026, A | Agenci obserwują aktywność i commity innych agentów | Interesująca alternatywa dla ścisłej izolacji worktree | Jedno badanie na podzbiorze SWE-Bench Pro; wymaga zabezpieczeń przed kolizjami |

### 3.1. AutoAgents — bezpośrednia odpowiedź na pomysł „manager sam zatrudnia”

AutoAgents formuje role **na podstawie problemu**, a nie tylko dobiera stale zdefiniowane osoby. System obejmuje także rolę obserwatora, który analizuje współpracę i może wpłynąć na dalszą strategię. W Twojej implementacji analogią byłby `TeamPlanner`, który produkuje **ustrukturyzowany plan obsady**; `AgentRegistry` szuka profili istniejących, zaś `AgentFactory` może wygenerować brakujący profil, ale wyłącznie przy użyciu dozwolonych narzędzi i zatwierdzonych uprawnień.

Kluczowe rozróżnienie: **wygenerowanie agenta** najczęściej oznacza skonfigurowanie istniejącego modelu/runnera (instrukcje, narzędzia, ograniczenia, dostęp do plików), a nie wytrenowanie nowego modelu. Kompetencja realna musi wynikać z odpowiedniego modelu, dostępu do danych/narzędzi i testowalnych rezultatów.

### 3.2. MetaGPT i ChatDev — struktura pracy ważniejsza niż persony

MetaGPT koduje standardowe procedury pracy (SOP), aby ograniczać niespójności powstające w łańcuchach generowanych odpowiedzi. ChatDev wykorzystuje dialogi specyficzne dla kolejnych etapów tworzenia software'u. Wniosek dla produktu: agent może być elastyczny, ale **format przekazywanej pracy nie powinien być dowolny**. Wymagaj np. `requirements.json`, kontraktu API, zestawu testów, zmian w Git oraz raportu z wykonania.

### 3.3. Co pokazuje nauka o skalowaniu agentów

Google Research opublikowało w styczniu 2026 omówienie kontrolowanej ewaluacji **180 konfiguracji**. W tym ustawieniu architektura scentralizowana poprawiała wynik o **80,9%** na jednym zadaniu podatnym na równoległość, natomiast na zadaniu sekwencyjnym różne konfiguracje multi-agent pogarszały wyniki o **39–70%**. Model predykcyjny wskazywał odpowiednią architekturę dla **87%** niewidzianych konfiguracji. **Liczby są wynikami określonych benchmarków, nie prognozą dla hackathonów.**

Praktyczna konsekwencja: *TeamPlanner* powinien najpierw oceniać dekompozycję i zależności. Jeśli zadanie nie ma niezależnych podzadań, **jeden agent z dobrym planem może być lepszy** od całego zespołu.

### 3.4. Dlaczego systemy zawodzą

MAST (NeurIPS 2025) opracowano na ponad 1600 oznaczonych przebiegach z siedmiu frameworków. Autorzy wyróżnili 14 trybów awarii w trzech grupach: projekt systemu, rozbieżności między agentami i weryfikacja zadania. Dla software house'u oznacza to konieczność monitorowania: niejasnych delegacji, dublowania pracy, złego kontekstu, cykli bez postępu, braku sprawdzania jakości i błędów integracji.

### 3.5. Czego uczą wdrożenia przemysłowe

Anthropic opisał w czerwcu 2025 system Research: agent główny deleguje równoległy research subagentom, gromadzi wyniki i uruchamia dodatkowe poszukiwania według potrzeb. Raportowany **+90,2%** wynik na ich wewnętrznym benchmarku dotyczy **researchu i konkretnej konfiguracji**, nie autonomicznego codingu. Firma zauważa również duże koszty tokenów (w ich obserwacji systemy multi-agent zużywały około 15 razy więcej tokenów niż zwykłe czaty). To wartościowy *engineering report* z potencjalnym konfliktem interesów, nie niezależna replikacja.

## 4. Taksonomia architektur i dobór do zadań

| Topologia | Kiedy przydatna | Ryzyko |
|---|---|---|
| **Single agent** | Małe, mocno sekwencyjne zadanie | Brak niezależnej kontroli jakości |
| **Independent parallel agents** | Różne hipotezy, research, UI/backend po ustaleniu kontraktów | Niekompatybilne rozwiązania, koszt integracji |
| **Centralized manager–workers** | Ograniczony budżet, audytowalna delegacja, hackathon | Manager jako wąskie gardło |
| **Handoff / chain** | Kolejne fazy o wyraźnych odpowiedzialnościach | Utrata kontekstu, kumulacja błędów |
| **Decentralized peers** | Wymiana opinii i negocjacje | Duży narzut komunikacji, konflikt decyzji |
| **Hierarchical + dynamic** | Wiele niezależnych komponentów i zmienne kompetencje | Złożoność planowania i dodatkowe koszty koordynacji |

**Zalecenie:** scentralizowany kontroler + elastyczny `TeamPlanner` + niewielka liczba workerów. Hierarchia i submanagerowie dopiero po wykazaniu przewagi pomiarem. Wybór projektu jest hipotezą projektową, nie ustaleniem, że ta topologia jest zawsze najlepsza.

## 5. Proponowany projekt: Dynamic AI Software House

```text
Human / Product Owner
         │
         ▼
Intake + Requirements + Budget/Deadline
         │
         ▼
TeamPlanner (LLM): DAG zadań + kompetencje
         │
         ▼
Policy Engine (kod): czy delegować, do kogo, czy tworzyć nowy profil?
         │
   ┌─────┴────────┐
   ▼              ▼
Agent Registry  Agent Factory (tylko konfiguracja roli)
   └─────┬────────┘
         ▼
Task Scheduler → Isolated Agent Runners / Worktrees
         │
         ▼
Artifact Store + Git + State DB
         │
         ▼
Integrate → Build → Unit/Integration/E2E → Security Checks
         │
         ▼
Human Approval → Deploy → Demo / Report
```

### 5.1. Niezmienny core kontra dynamiczna obsada

**Core niezmienny:** metryki, logi, stan, harmonogram, kontrola kosztów, polityka dostępów, wersjonowanie, testy i zatwierdzenia. **Dynamiczne:** profile agentów, ich narzędzia w granicach uprawnień, kolejność delegacji, liczba workerów i kontekst zadania.

### 5.2. Agent Registry

Profil jest obiektem typu `AgentProfile` ze zdefiniowanym identyfikatorem, specjalizacją, narzędziami, poziomem dostępu, kontraktem wejścia/wyjścia i kryteriami sukcesu. Rejestr musi umożliwiać ponowne użycie tej samej roli zamiast generowania kolejnych niemal identycznych promptów.

### 5.3. Agent Factory

Tworzy **konfigurację nowej instancji** na podstawie luki kompetencyjnej, nie nadaje uprawnień autonomicznie. Wygenerowany profil powinien być sprawdzony względem schematu, powtórzeń w registry, potrzebnych narzędzi i polityki bezpieczeństwa. Dynamiczna specjalizacja nie zwalnia z testowania skuteczności danej konfiguracji.

**Przykładowy kontrakt:**

```yaml
agent_profile:
  id: "frontend-nextjs-e2e"
  objective: "Implement the onboarding page against the approved API contract"
  model_backend: "codex"
  tools: ["read_repo", "edit_worktree", "run_tests"]
  permissions: ["workspace_read", "scoped_write"]
  input_artifacts: ["docs/prd.md", "docs/api-contract.yaml"]
  output_artifacts: ["git_patch", "tests_report"]
  acceptance: ["typecheck", "unit_tests", "playwright_smoke"]
  max_turns: 12
```

### 5.4. Delegowanie przez Task DAG

Manager zwraca nie luźną listę ról, lecz **skierowany acykliczny graf zadań**. Każde zadanie ma `id`, `objective`, `dependencies`, `required_capabilities`, `acceptance_checks`, `estimated_cost`, `deadline`, `required_permissions` i `artifacts`. Scheduler wykonuje tylko zadania, których zależności są spełnione; równolegle uruchamia tylko niezależne operacje.

### 5.5. Skąd wiedzieć, czy tworzyć kolejnego agenta?

Proponowany algorytm (heurystyka projektowa):

1. Oceń, czy zadanie ma niezależny zakres i sprawdzalny wynik.
2. Sprawdź, czy istnieje kompetentny profil w registry.
3. Policz spodziewaną korzyść z równoległości względem kosztu koordynacji i integracji.
4. Jeśli profil nie istnieje, rozważ utworzenie nowego; uruchom jedynie po zatwierdzeniu polityki uprawnień.
5. Zatrzymaj tworzenie nowych agentów po limicie równoległości i budżetu.
6. Po wykonaniu oceń **efektywną skuteczność**, a nie liczbę wiadomości czy „wrażenie eksperckości”.

**Test kontrolny:** w tych samych warunkach porównaj wynik z jednym agentem. Każde dodatkowe stanowisko musi uzasadniać swój koszt.

### 5.6. Stan, pamięć, Git i integracja

Wersjonowany projekt i artefakty: `README`, PRD, ADR, kontrakty API, zadania, logi, raporty testów, komity i wydania. Na starcie **osobne worktree dla równolegle edytujących agentów** ograniczają kolizje. Badanie PASC (ESEM 2026) sugeruje jednak, że odpowiednio zaprojektowany wspólny workspace z informacją o aktywności innych agentów może dawać korzyści — warto to sprawdzić później eksperymentalnie zamiast przyjmować absolutny zakaz współdzielenia repo.

### 5.7. Weryfikacja i bezpieczeństwo

Model nie jest źródłem prawdy o poprawności kodu. Zasada: `lint/typecheck → unit → integration → E2E → security smoke → human approval`. Krytyk LLM pomaga znaleźć problemy, lecz nie zastępuje testów. Wynik powinien obejmować: zielone testy, działający happy path, wydajność, brak wycieków sekretów oraz reprodukowalny deployment.

**AgentDojo (NeurIPS 2024)** wskazuje na ryzyko prompt injection w danych pochodzących z narzędzi. Dlatego: izolacja wykonania, najmniejsze uprawnienia, ochrona sekretów, allowlisty i manualne zatwierdzenie deployu/operacji nieodwracalnych. Agent nie może swobodnie eskalować własnych uprawnień ani ufać instrukcjom z obcych stron i repozytoriów.

## 6. Jak ocenić system empirycznie

Nie wystarczy pokazać „agent stworzył aplikację”. Należy porównać konfiguracje na tych samych zadaniach, budżetach i kryteriach jakości.

| Eksperyment | Architektura | Co mierzyć |
|---|---|---|
| **A — baseline** | Jeden coding agent | Dostarczony działający produkt, koszt, czas |
| **B — SOP** | Stały plan: spec → code → test | Czy zyski pochodzą z procesu, nie z wielu agentów? |
| **C — static team** | Z góry ustalony frontend/backend/QA | Korzyść z podziału pracy |
| **D — dynamic team** | TeamPlanner + registry + factory | Czy dynamiczny dobór ról poprawia wynik? |
| **E — dynamic + critic** | Jak D, z krytykiem i bramkami jakości | Czy dodatkowa kontrola zmniejsza awarie? |

**Metryki główne:** odsetek zadań zakończonych kompletnym, uruchomionym rozwiązaniem; odsetek poprawnych scenariuszy E2E; czas do działającego demo; koszt (tokeny i narzędzia) na działający produkt. **Dodatkowe:** liczba poprawek człowieka, regresje, błędy bezpieczeństwa, przekroczenia budżetu, potrzeba reintegracji, liczba agentów, średni czas wykonania zadań i zadowolenie użytkowników. Powtórz każdą konfigurację na różnych seedach i zadaniach. Porównuj tylko przebiegi z podobnym budżetem.

**Benchmarki badawcze do przejrzenia:** MultiAgentBench (ACL 2025), SWE-Bench Pro (ICML 2026), SILO-BENCH (ACL 2026). Własna paczka zadań typu „zbuduj MVP w 3–6 godzin” będzie konieczna, ponieważ benchmark naprawiania bugów nie jest równoważny tworzeniu produktu od zera.

## 7. Ramowy plan implementacji

- **M0 – baseline (1 agent):** repozytorium, wykonywanie zadań, build/test, archiwizacja patchy, logi, stały budżet.
- **M1 – deterministic pipeline:** intake → PRD → implementacja → test → review; człowiek zatwierdza deployment.
- **M2 – TeamPlanner + Registry:** agent planuje DAG i wybiera z istniejących profili; maksymalnie 2–3 workery równolegle.
- **M3 – Agent Factory:** tworzenie brakującego *profilu* z walidacją schematu i uprawnień, możliwość ponownego użycia.
- **M4 – adaptacja:** metryki skuteczności profili, routing według zadania i automatyczna zmiana planu po niepowodzeniu.
- **M5 – rygorystyczne ewaluacje:** porównania A/B, dashboard kosztów, eksperymenty ze strukturą zespołu i alternatywnymi sposobami współdzielenia stanu.

Nie należy traktować etapów jako gwarancji, że rozwiązanie stanie się w pełni autonomicznym software house'em. Nawet nowe prace wskazują na utrzymujące się trudności w koordynacji, integracji i benchmarkowaniu.

## 8. Otwarte pytania i ograniczenia stanu wiedzy

1. **Czy role = kompetencje?** Zmiana promptu może poprawić skupienie, ale nie tworzy wiedzy lub umiejętności poza możliwościami bazowego modelu i narzędzi.
2. **Jaka jest granica opłacalności wielu agentów?** Jest zależna od problemu i budżetu, a nie od liczby dostępnych nazw ról.
3. **Czy lepiej izolować worktree czy współdzielić repo?** Domyślnie izolacja jest prostsza; kontrolowane wspólne repo ma wstępne wsparcie empiryczne, ale wymaga dodatkowego protokołu konfliktów.
4. **Jak testować rozwiązania produktowe?** Benchmarki issue-solving nie wystarczą do sprawdzenia UX, deployu i prezentacji.
5. **Jak chronić system przed wpływem danych zewnętrznych?** Odczytane strony, wyniki wyszukiwania i pliki repozytoriów nie mogą zmieniać instrukcji nadrzędnych ani polityki uprawnień.
6. **Co znaczy „autonomia”?** Lepszą miarą jest ukończony i zweryfikowany rezultat przy ograniczonym udziale człowieka niż sama liczba autonomicznych kroków.

## 9. Lista lektur — kolejność polecana

**Pięć tekstów na start:**

1. **Chen i in. (2024), AutoAgents: A Framework for Automatic Agent Generation**, IJCAI, DOI: [10.24963/ijcai.2024/3](https://doi.org/10.24963/ijcai.2024/3). Fundament dynamicznego powoływania zespołu.
2. **Hong i in. (2024), MetaGPT: Meta Programming for A Multi-Agent Collaborative Framework**, ICLR, [strona publikacji](https://proceedings.iclr.cc/paper_files/paper/2024/hash/6507b115562bb0a305f1958ccc87355a-Abstract-Conference.html). Role, SOP-y, przekazywanie artefaktów.
3. **Cemri i in. (2025), Why Do Multi-Agent LLM Systems Fail?**, NeurIPS, [strona publikacji](https://proceedings.neurips.cc/paper_files/paper/2025/hash/b1041e52d3be19f0a9bc491657488e4a-Abstract-Datasets_and_Benchmarks_Track.html). Błędy współpracy i konieczność weryfikacji.
4. **Kim i in. (2025/2026), Towards a Science of Scaling Agent Systems**, [arXiv](https://arxiv.org/abs/2512.08296), [Google Research](https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/). Gdy podział na wielu agentów ma sens.
5. **Anthropic (2025), How we built our multi-agent research system**, [Engineering Blog](https://www.anthropic.com/engineering/multi-agent-research-system). Praktyczna architektura manager–workers oraz limity kosztowe.

**Pozostałe ważne prace:**

6. **Smith (1980), The Contract Net Protocol**, IEEE Transactions on Computers, DOI [10.1109/TC.1980.1675516](https://doi.org/10.1109/TC.1980.1675516).
7. **Hayes-Roth (1985), A blackboard architecture for control**, Artificial Intelligence, DOI [10.1016/0004-3702(85)90063-3](https://doi.org/10.1016/0004-3702(85)90063-3).
8. **Wooldridge i Jennings (1995), Intelligent Agents: Theory and Practice**, The Knowledge Engineering Review, DOI [10.1017/S0269888900008122](https://doi.org/10.1017/S0269888900008122).
9. **Qian i in. (2024), ChatDev: Communicative Agents for Software Development**, ACL, DOI [10.18653/v1/2024.acl-long.810](https://doi.org/10.18653/v1/2024.acl-long.810).
10. **Chen i in. (2024), AgentVerse**, ICLR, [strona](https://proceedings.iclr.cc/paper_files/paper/2024/hash/578e65cdee35d00c708d4c64bce32971-Abstract-Conference.html).
11. **Wu i in. (2024), AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation**, COLM, [Microsoft Research](https://www.microsoft.com/en-us/research/publication/autogen-enabling-next-gen-llm-applications-via-multi-agent-conversation-framework/).
12. **Guo i in. (2024), Large Language Model Based Multi-agents: A Survey of Progress and Challenges**, IJCAI, DOI [10.24963/ijcai.2024/890](https://doi.org/10.24963/ijcai.2024/890).
13. **Li i in. (2024), A survey on LLM-based multi-agent systems: workflow, infrastructure, and challenges**, DOI [10.1007/s44336-024-00009-2](https://doi.org/10.1007/s44336-024-00009-2).
14. **Tran i in. (2025), Multi-Agent Collaboration Mechanisms: A Survey of LLMs**, [arXiv](https://arxiv.org/abs/2501.06322).
15. **He, Treude, Lo (2025), LLM-Based Multi-Agent Systems for Software Engineering: Literature Review, Vision, and the Road Ahead**, ACM TOSEM, DOI [10.1145/3712003](https://doi.org/10.1145/3712003).
16. **Xia i in. (2025), Demystifying LLM-based Software Engineering Agents (Agentless)**, FSE, DOI [10.1145/3715754](https://doi.org/10.1145/3715754).
17. **Zhu i in. (2025), MultiAgentBench**, ACL, DOI [10.18653/v1/2025.acl-long.421](https://doi.org/10.18653/v1/2025.acl-long.421).
18. **Debenedetti i in. (2024), AgentDojo**, NeurIPS, [strona](https://proceedings.nips.cc/paper_files/paper/2024/hash/97091a5177d8dc64b1da8bf3e1f6fb54-Abstract-Datasets_and_Benchmarks_Track.html).
19. **Deng i in. (2026), SWE-Bench Pro**, ICML/PMLR, [publikacja](https://proceedings.mlr.press/v306/deng26f.html).
20. **Zhang i in. (2026), SILO-BENCH**, ACL, DOI [10.18653/v1/2026.acl-long.1354](https://doi.org/10.18653/v1/2026.acl-long.1354).
21. **Özaytürk i Buzluca (2026), Coordinating Agents on a Shared Git Workspace**, ESEM, DOI [10.4230/LIPIcs.ESEM.2026.18](https://doi.org/10.4230/LIPIcs.ESEM.2026.18).
22. **Rasheed i in. (2026), LLM-based multi-agent systems for code generation: A multi-vocal literature review**, *Journal of Systems and Software* (tom datowany na luty 2027, artykuł dostępny online w 2026), DOI [10.1016/j.jss.2026.113142](https://doi.org/10.1016/j.jss.2026.113142).

**Dokumentacja implementacyjna (nie dowód skuteczności):** [OpenAI Agents SDK – Orchestration](https://openai.github.io/openai-agents-python/multi_agent/), [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview), [Agent2Agent Protocol](https://a2a-protocol.org/), [Model Context Protocol](https://modelcontextprotocol.io/).

---

## Wniosek końcowy

Najbardziej uzasadniony obecnie kierunek to **dynamicznie obsadzany zespół działający w ramach rygorystycznego, mierzalnego workflowu**. System powinien zaczynać od jednego agenta, wykrywać potrzebę specjalizacji, ponownie używać profili, a nowe tworzyć tylko po wykazaniu korzyści. Niezmiennymi elementami powinny pozostać polityka dostępu, trwały stan, testy, kontrola kosztów i zatwierdzanie nieodwracalnych działań. Najważniejsze badanie do wykonania we własnym projekcie to porównanie: **single agent vs stały zespół vs dynamiczny zespół**, na tych samych zadaniach i przy tym samym budżecie.

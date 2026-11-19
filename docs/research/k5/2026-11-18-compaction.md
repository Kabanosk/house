# K5: kompakcja z naszymi instrukcjami i czytanie fragmentami — czy oszczędzają bez straty jakości

Plan: `docs/plans/2026-11-18-k5-compaction-experiment.md` (reguła decyzji ustalona przed przebiegami). Speki:
short-context §2, §8 (K2), §9 (K3), §10 (K4); Agno A3. Sonnet we wszystkich przebiegach, ustawienia jak na produkcji
(`autoCompactWindow: 200000`, `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS=150000`, hooki `PreCompact`/`PostCompact`/
`SessionStart` z `house adapt`), trzy powtórzenia na wariant. Koszt ważony wg K0 (input 1, cache_read 0,1,
cache_write 5m 1,25 / 1h 2, output 5). Same liczby, bez treści sesji. Liczby z `house eval compare`.

Skąd K5: noc K4 „po” (`docs/research/k4/2026-11-17-after.md`) wyszła drożej na scalone zadanie niż noc bazowa, ale
na innych zadaniach i bez żadnej kompakcji, więc nie rozstrzygała, czy K2a/K1/K3 pomagają. K5 mierzy to na tych
samych zadaniach, wariant przeciw wariantowi.

## K3: długi dokument w `.house/sources/` czytany fragmentami (case `long-input`)

Kohorta `k5-li2`, commit `11b375a` (po poprawce L1: eksport bez klucza odpowiedzi; audyt odczytów `house` = 0).

| Wariant | n | Jakość | Koszt ważony (min–max) | Szczyt kontekstu | Kompakcje |
|---|---|---|---|---|---|
| `pasted` (dokument w prompcie) | 3 | 1,00 | 433K (431K–434K) | 179K | 1,0 |
| `sources` (produkcja, czytanie fragmentami) | 3 | 1,00 | 102K (92K–114K) | 43K | 0,0 |

Wcześniejsza kohorta `k5-li` (przed L1, unieważniona) dała to samo: 431K vs 116K, obie 1,00.

**Reguła:** jakość(sources) ≥ jakość(pasted) − 0,05 i koszt niższy → **K3 zostaje.** Oszczędność −76 % przy tej
samej jakości, różnica wielokrotnie większa od rozrzutu powtórzeń.

## Kompakcja w długiej sesji (case `long-chain`, 16 tur)

Pierwsza seria (`k5-lc`, po 1 przebiegu) pokazała, że 15 zadań kończy się na 139–150K kontekstu, poniżej progu
~160K przy oknie 200K: kompakcja nie odpaliła w żadnym wariancie, więc nic nie kosztowała i nic nie dała. Stąd dwa
warianty pomiaru (decyzja Wojtka, Q14: „A i B”):

- **A** — człowiek wpisuje `/compact` na granicy pracy (po turze 9), case `long-chain` bez zmian;
- **B** — dłuższy dzień (`long-chain-xl`, 25 tur), kompakcja ma odpalić sama przy produkcyjnym progu.

Warianty: `control` (bez `autoCompactWindow`, okno 1M, w A zamiast `/compact` neutralna tura „ok”), `house`
(produkcja: nasze hooki i instrukcje kompakcji), `default-compact` (kompakcja bez naszych `PreCompact`/`PostCompact`
i bez `SessionStart` po kompakcji).

### A: `/compact` po turze 9

Kohorta `k5-lc-a2`, commit `35fb84f`, CLI 2.1.325. Pierwsza próba (`k5-lc-a`, 18.11) przepadła na limicie użycia
(6 z 9 przebiegów `infra_error`, 3 urwane) i nie wchodzi do wyniku.

| Wariant | n | Jakość (min–max) | Koszt ważony (min–max) | Szczyt | Kompakcje |
|---|---|---|---|---|---|
| `control` | 3 | 0,83 (0,83–0,83) | 1496K (1177K–1776K) | 168K | 0,0 |
| `house` | 3 | 0,81 (0,78–0,83) | 1368K (1245K–1485K) | 127K | 1,0 |
| `default-compact` | 3 | 0,87 (0,83–0,89) | 1245K (1140K–1297K) | 118K | 1,0 |

Predykaty, które nie były 3/3 we wszystkich wariantach:

| Predykat | control | house | default-compact | Uwagi |
|---|---|---|---|---|
| `fact-root-cause` (pytanie w ostatniej turze) | 3/3 | 2/3 | 2/3 | jedyny predykat pamięci, który ucierpiał |
| `s1-tests` | 0/3 | 0/3 | 2/3 | zadanie z tury 1, przed kompakcją — rozrzut, nie pamięć |
| `s11-tests` | 0/3 | 0/3 | 1/3 | to samo zadanie w każdym wariancie, trudne |
| `d008-api-v1` | 0/3 | 0/3 | 0/3 | pada już w turze 8 (konsola), przed kompakcją |

`d007-integers`, `d009-refunds-apart`, `fact-legacy-hold`, `fastcsv-retry` i pozostałe zadania: 3/3 wszędzie.

**Reguła K2a (house vs control):** jakość 0,81 ≥ 0,83 − 0,05 ✓, koszt 1368K < 1496K (−9 %) ✓; różnica kosztu
(128K) jest mniejsza od rozrzutu kontroli (600K), więc formalnie „brak różnicy”. Kompakcja nie pogorszyła jakości
ponad próg i nie podniosła kosztu.

**Reguła instrukcji (house vs default-compact):** default-compact ma jakość i koszt nie gorsze od `house`; różnica
jakości (0,06) pochodzi z `s1-tests` sprzed kompakcji, a predykaty pamięci są identyczne (`fact-root-cause` 2/3 w
obu). W A nasze hooki **nic nie dodają** ponad domyślną kompakcję.

### B: kompakcja automatyczna przy 200K (`long-chain-xl`, 25 tur)

Kohorta `k5-xl`, commit `0d23d4a`. Pełna jest tylko pierwsza runda (n = 1 na wariant):

| Wariant | Jakość | Koszt ważony | Szczyt | Kompakcje | Nie przeszły |
|---|---|---|---|---|---|
| `control` | 0,86 | 2339K | 211K | 0 | s1, s6, s11, d008 |
| `house` | 0,89 | 2071K (−11 %) | 166K | 1 | s1, s11, d008 |
| `default-compact` | 0,89 | 2323K | 167K | 1 | s1, s11, d008 |

- **Automatyczna kompakcja przy produkcyjnym progu 200K odpala sama**, w obu wariantach z kompakcją (szczyt
  ~166K). Kontrola na oknie 1M doszła do 211K bez kompakcji.
- Wszystkie predykaty pamięci (`d007`, `d009`, `fact-root-cause`, `fact-legacy-hold`) przeszły w każdym wariancie.
  `d008` pada wszędzie, jak w `long-chain`, z powodu zadania z tury 8.

Rundy 2–3 nie dały wyniku. 19.11 ok. 15:00 skończył się limit użycia: 3 przebiegi urwane, 3 `infra_error`.
Wieczorem (laptop w innej sieci) proxy miało najpierw nieaktualny DNS (restart `house-agent-proxy`), a potem
wszystkie 4 przebiegi urwały się w turach 5–22 na zerwanych połączeniach: `api_retry` do 10 prób,
`error_status: null`, a w logu proxy `TimeoutError` i `ConnectionResetError`. Wiersze `incomplete` nie wchodzą do
średnich. Brakuje 6 przebiegów B (`--repeat 2`, kohorta `k5-xl`, ten sam commit) na stabilnej sieci.

## Decyzje

Bramka PR („K5 pokaże oszczędność bez straty jakości”) jest spełniona dla K3 i K2a. Ocena instrukcji kompakcji jest
niejednoznaczna:

1. **K3 zostaje.** Oszczędność −76 % przy tej samej jakości (n = 3 + 3, rozrzut mały).
2. **K2a (`autoCompactWindow: 200000`) zostaje.** W A kompakcja dała −9 % (`house`) i −17 % (`default-compact`)
   kosztu, a jakość mieściła się w progu 0,05. W B (n = 1) odpala sama przy produkcyjnym progu, z −11 % kosztu
   w `house` i bez utraty pamięci. Na dniu z 15 zadań próg nie jest osiągany, więc K2a nic wtedy nie kosztuje.
3. **Nasze instrukcje kompakcji (K1/A2a): brak mierzalnej różnicy względem domyślnej kompakcji.**
   - W A `default-compact` był nie gorszy od `house` w jakości i koszcie, a predykaty pamięci wyszły identycznie.
   - W B (n = 1) `house` był o 11 % tańszy przy tej samej jakości.

   Według reguły to „brak różnicy”. **Decyzja Wojtka (19.11, Signal): domyślna kompakcja.** `house adapt` nie
   instaluje już `PreCompact`/`PostCompact`; instrukcje kompakcji i licznik `compactions.jsonl` są usunięte.
   Zostaje `SessionStart` po kompakcji (`compact_state`: decyzje człowieka z `DECISIONS.md`, pytania, zadania
   w toku). K5 go nie rozstrzyga, bo w obu case'ach decyzje padają w rozmowie, a nie w `DECISIONS.md`.
   Wariant `default-compact` różni się odtąd od `house` tylko tym blokiem.
4. **Infrastruktura:** proxy agenta potrzebuje odświeżenia DNS po zmianie sieci i nie przeżywa niestabilnego Wi-Fi.
   To kandydat na check w `house doctor` (ROADMAP).

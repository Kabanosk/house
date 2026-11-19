# Konkurs krok po kroku

Jak przeprowadzić konkurs przez system: od instalacji na maszynie po zgłoszenie. Komendy slash działają w Claude Code
w katalogu konkursu (`contests/<nazwa>`).

Powiązane: [Sprawdzenia i bramka scalania](checks-and-merge.md), [Tryb nocny](night.md),
[Pętla eksperymentów](experiments.md), [Signal](signal.md).

## Komendy w Claude Code

| Komenda | Co robi |
|---|---|
| `/house-start` + wklejony regulamin, zadanie, kryteria | uzupełnia `.house/spec.yaml`, sprawdza regulamin, zadaje pytania o luki |
| `/house-ideate` | pomysły od ideatora, ocena sędziego według kryteriów, wybór (Ty albo sędzia) |
| `/house-plan` | rozpisuje zadania (DAG) w `.house/tasks/` z kryteriami jako komendami i je zamraża |
| `/house-run` | odpala koderów w osobnych worktree, scala przez `house merge` (bramka) |
| `/house-judge` | ocena według rubryki/metryki z dowodami |
| `/house-ship` | deploy, pitch, slajdy, README zgłoszenia, czysty eksport |
| `/house-submit` | zgłoszenie w konkursie optymalizacyjnym |
| „jak tam” / `/house-status` | briefing: co się działo, potem pytania po kolei |
| `/house-retro` | po konkursie: co poprawić w samym systemie (raport z propozycjami, zmiany dopiero po Twojej akceptacji) |

## Raz na maszynę

```bash
git clone git@github.com:Kabanosk/house.git ~/house && cd ~/house
git config user.name "Wojciech Fiołka"; git config user.email "56364007+Kabanosk@users.noreply.github.com"
bin/house setup       # skille użytkownika (grill-me, retro, …) + remotion-best-practices do ~/.claude/skills, sprawdzenie narzędzi, co doinstalować
echo 'export PATH="$HOME/house/bin:$PATH"' >> ~/.bashrc   # potem `house …` z dowolnego miejsca
house sync            # odtwórz konkursy zapisane w contests.yaml
house adapt <nazwa>   # ścieżki i agenci pod ten komputer
```
Tożsamość git ustawiona w repo systemu jest kopiowana do każdego nowego konkursu. Skille użytkownika żyją w
`user-skills/`; `house setup` instaluje je ponownie po zmianach (lokalnie zmienioną kopię odkłada jako `*.bak-*`).

## Konkurs, który już trwa (istniejące repo)

`house adopt <nazwa> <ścieżka-do-repo>` podpina je w miejscu (odnośnik w `contests/`, bez drugiej kopii) i dodaje
`.house/`, niczego nie commitując. Wybierz gałąź, potem `house adapt <nazwa>` i `/house-start` z treścią zadania,
a do `ideas.md` niech trafi wszystko, co już sprawdzono.
Uwaga: `AGENTS.md`/`CLAUDE.md` na wspólnej gałęzi czytają też narzędzia reszty drużyny.

## Przed startem konkursu

1. `house new <nazwa>` (albo `--repo <url>`, jeśli repo konkursu już istnieje), potem `house adapt <nazwa>`.
2. `cd contests/<nazwa> && claude`
3. `/house-start` i wklej wszystko, co masz: treść zadania, link/tekst regulaminu, kryteria oceny, harmonogram.
   Dopisz tryb (`copilot`/`autopilot`) i limity, jeśli chcesz inne niż domyślne (3 agentów, 5 pomysłów).
4. Odpowiedz na pytania z `QUESTIONS.md` (przez „jak tam”). Dopóki regulamin jest „UNCLEAR”, dopytaj organizatorów.

## Dokumenty konkursu (`house sources`)

Regulamin, treść zadania i dokumentacja API trafiają do `.house/sources/` bajt w bajt, a agenci czytają je po
zakresie linii zamiast przepisywać:

    house sources add <konkurs> regulamin.md              # plik; nazwa = nazwa pliku małymi literami
    house sources add <konkurs> - --name zadanie.txt      # stdin (tekst wklejony, do ok. 5 000 znaków)
    house sources add <konkurs> plik.md --replace         # nadpisz dokument o tej samej nazwie
    house sources <konkurs>                               # spis treści: rozmiar, ~tokeny, nagłówki z numerami linii

Spis treści pokazuje wielkość w znakach i liniach, nagłówki Markdown z numerami linii (poza blokami kodu, do 50),
ostrzega o liniach dłuższych niż 2 000 znaków, o niezamkniętym bloku kodu oraz gdy plik różni się od `index.json`
(wtedy zakresy linii w `spec.yaml` mogły się przesunąć). Pliki binarne są wymienione bez nagłówków. Dowiązań
symbolicznych nie kopiujemy (także `index.json` jako dowiązanie to błąd), nazwa pliku musi pasować do
`[a-z0-9][a-z0-9._-]{0,80}`; plik o innej nazwie leży w katalogu nieczytany i spis wypisuje go jako
`'nazwa': invalid name (not read)`. Pierwsze `add` zapisuje `.house/sources/.gitignore` z wpisem `.add-*` (istniejący
zostaje), więc pozostałość po przerwanym kopiowaniu nie trafi do commita. Komenda niczego nie commituje:
`.house/sources/` i `index.json` trafiają do repo razem z resztą `.house/`. Tekst wklejony w czacie `/house-start`
przekazuje przez heredoc z markerem `HOUSE_SRC_<8 losowych znaków hex>` wybranym osobno przy każdym wklejeniu (agent
sprawdza, że tekst go nie zawiera); heredoc dopisuje jeden końcowy znak nowej linii. `/house-start` prosi o te pliki, a
`spec.yaml` i zadania odwołują się do dokumentów przez ścieżkę i zakres linii (`.house/sources/regulamin.md:L120-L180`).

## W trakcie: hackathon budowlany

1. `/house-ideate` → pomysły, do każdego karta „co już istnieje” (katalog YC, YC Requests for Startups,
   zwycięzcy hackathonów, Product Hunt), ocena sędziego na podstawie kart → wybierasz Ty (`copilot`) albo sędzia
   (`autopilot`). Bez karty pomysł nie może zostać wybrany.
2. `/house-plan` → zadania w `.house/tasks/`. Przejrzyj je, zwłaszcza kryteria akceptacji (komendy), zanim
   zostaną zamrożone; to najtańszy moment na korektę.
3. `/house-run` → koderzy (Claude i Codex) w osobnych worktree w `.worktrees/`, scalanie do `main` na bieżąco
   przez `house merge` (patrz [Sprawdzenia i bramka scalania](checks-and-merge.md)).
4. `/house-judge` przed każdym kamieniem milowym → oceny 1–5 według kryteriów i jedna najtańsza poprawa.
5. `/house-ship` → demo, pitch (`pitch/script.md`, `pitch/slides.html`), README zgłoszenia, czysty eksport.

## W trakcie: konkurs optymalizacyjny

To samo do `/house-run`, potem pętla eksperymentów `house exp` (patrz [Pętla eksperymentów](experiments.md)) →
`/house-judge` → `/house-submit`. Każde zgłoszenie ląduje w `.house/submissions.csv` z wynikiem lokalnym
i z leaderboardu.

## Po powrocie od komputera

Napisz „jak tam”. Najpierw dostajesz briefing (co się zmieniło, wynik, decyzje podjęte bez Ciebie, ryzyka),
potem pytania po jednym. Twoje odpowiedzi trafiają do `DECISIONS.md`.

**Drużyna** czyta `.house/PROGRESS.md` (po polsku) i `.house/reports/` (po angielsku).

## Zgłoszenie publiczne

`house export <nazwa> <katalog>` → sprawdź zawartość → wypchnij **eksport** na publiczne repo. Samego repo
konkursu nigdy nie upubliczniaj.

## Sprawdzenie pomysłu w katalogu YC

Też ręcznie: `house yc AI agents that negotiate prices` → najbliższe firmy YC z edycją, statusem i linkiem.
Dane: codzienny zrzut katalogu YC ([yc-oss](https://github.com/yc-oss/api)), cache w `~/.cache/house/`, działa
offline na starym zrzucie.

## Decyzje z poprzednich konkursów

`house decisions [--contest K] [--exclude K]... [--by human|agent] [--since RRRR-MM-DD] [--grep TEKST] [--all] [--full]` wypisuje
decyzje z `DECISIONS.md` wszystkich konkursów z `contests.yaml`, jedna linia na wpis:
`<konkurs> D-### <data> <kto>: <tytuł> — contests/<konkurs>/.house/DECISIONS.md:<linia>` (ścieżka względem
katalogu systemu). Zastąpione wpisy (`Zastępuje:`) są ukryte, z `--all` mają znacznik `[zastąpiona przez D-###]`;
decyzję człowieka może zastąpić tylko wpis człowieka: gdy wskazuje ją wpis agenta (`judge`, `coordinator`), zostaje na
liście aktywna (także dla hooka po kompakcji) ze znacznikiem `[podważona przez D-### (agent)]`. `--exclude K` (można
powtarzać) pomija konkurs, np. bieżący. Data w `Data:` ma zaczynać się od `RRRR-MM-DD`, dalej może być cokolwiek
(`2026-11-17, 14:30`). Konkurs bez pliku daje jedną linię `note:` i jest pomijany. `--grep` szuka dosłownie, bez wielkości liter, w tytule
i polach. `/house-start` i `/house-ideate` uruchamiają `house decisions --by human --exclude <bieżący konkurs>`, wybierają wpisy
ważne dla tego konkursu (sprzęt, czas, zasady, odrzucone podejścia) i pokazują je Tobie jako ograniczenia do
potwierdzenia albo odrzucenia (w `autopilot` wypisują ważne w briefingu i nie czekają). Nic nie jest przejmowane po cichu ani wpisywane do `DECISIONS.md` bez Twojej odpowiedzi.

## Tryby

Tryby (`mode` w `spec.yaml`): `copilot` pyta o wybór pomysłu, zmianę kierunku i duże koszty;
`autopilot` decyduje sam i loguje decyzje. W końcówce konkursu oba pytają przed zgłoszeniem.

## Stan konkursu (`contests/<nazwa>/.house/`)

| Plik | Dla kogo | Po co |
|---|---|---|
| `spec.yaml` | wszyscy | centralna specyfikacja: wynik, kryteria, harmonogram, limity, tryb, autonomia |
| `QUESTIONS.md` | Ty | pytania o trudne decyzje, ze statusem |
| `DECISIONS.md` | wszyscy | dlaczego coś zostało zdecydowane |
| `PROGRESS.md` | drużyna | krótki ślad postępu (PL) |
| `tasks/`, `reports/` | agenci, drużyna | zadania (DAG) i raporty (EN) |
| `ideas.md`, `submissions.csv` | agenci | archiwum pomysłów, zgłoszenia lokalnie vs leaderboard |
| `experiments.csv` | `house exp` | drzewo eksperymentów: rodzic, commit, budżet, wynik, keep/revert |

## Historia projektowa

Plan: [`docs/plans/2026-11-05-house-core.md`](../plans/2026-11-05-house-core.md). Założenia: [`docs/SPEC.md`](../SPEC.md).

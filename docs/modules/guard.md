# Ochrona (guard i hooki)

Pierwszą linią obrony są hooki Claude Code (`bin/house-hook`), a w worktree zadań także hooki git
(`bin/house-githook`, niżej). Twardą granicą jest
`house merge`, który sprawdza diff gałęzi niezależnie od tego, jak plik został zmieniony
(zob. [Sprawdzenia i bramka scalania](checks-and-merge.md)).

Plan: [`guard-hooks`](../plans/2026-11-13-guard-hooks.md).
Założenia: [`system-hardening`](../specs/2026-11-12-system-hardening-design.md).

## Hooki Claude Code

`house adapt` dopisuje do `.claude/settings.local.json` (nie trafia do gita, wpis w `.git/info/exclude`, więc
drużyny nie dotyczy) hooki `bin/house-hook` i listę dozwolonych komend. Przed każdą edycją i komendą hook blokuje:
zmianę chronionych plików (testy akceptacyjne z `protected`, lokalna sprawdzarka z `spec.scoring.local_scorer`),
pisanie do `.house/checks/`, commit plików `.env*` i plików z kluczami, push na remote spoza
`spec.guard.push_allow` (domyślnie `origin`) i force push na główną gałąź. Na start sesji podaje stan konkursu
(pytania, zadania gotowe do pracy, czerwone sprawdzenia). Codex nie ma hooków, więc te same reguły sprawdza po fakcie
`house guard diff` (sterownik Codexa i `house merge`). Awaryjnie: `HOUSE_GUARD=off claude` (zapis w PROGRESS.md).
Hook to pierwsza linia obrony (łapie typowe komendy: przekierowania, `sed -i`, `rm`, `git -C`, wiele linii),
ale nie każdą sztuczkę (np. `python3 -c "open(...,'w')"`); twardą granicą jest `house merge`, który sprawdza
diff gałęzi niezależnie od tego, jak plik został zmieniony.

`house out -- KOMENDA` guard sprawdza jak samą KOMENDĘ: `house out` jest opakowaniem, tak jak `timeout 5` czy
`env X=1`, także zagnieżdżone, z przekierowaniami między słowami (`house >/dev/null out -- …`; cele przekierowań są
sprawdzane osobno) i po `cd x &&`. Program komendy guard szuka za `VAR=x` i opakowaniami, więc
`timeout 60 git push --no-verify` jest odrzucone jak `git push --no-verify`, a skrypt z `sh|bash -c '…'` sprawdza jak
zwykłą komendę. Ograniczenia są te same co bez `house out`: guard nie rozumie zmiennych, podstawień `$(…)` ani plików
skryptów. `house out show` i `house out prune` nie są opakowaniem. Tak samo `house exp … -- KOMENDA` (także z opcjami
przed `run`, np. `house exp --budget 5m run …`): guard sprawdza KOMENDĘ po pierwszym `--` (jedno słowo po `--` idzie
przez powłokę, więc jest sprawdzane jak skrypt); `house exp` bez `--` nie jest opakowaniem. Przekierowania z nazwanym
deskryptorem (`{fd}>/dev/null`) są pomijane jak `2>/dev/null`, a `>&plik` i `<>plik` liczą się jako zapis (cel `2`,
`-` nie jest plikiem). Opcje opakowań z wartością (`env -u X`, `timeout -s KILL`, `sudo -u u`, `nice -n 5`,
`stdbuf -o L`) i opcje powłoki z wartością (`bash -o pipefail`, `-O extglob`, `--rcfile f`) nie zasłaniają komendy.

Znane ograniczenie: `python3 <root>/bin/house out -- …` (house uruchomiony przez interpreter) nie jest rozpakowywane;
guard widzi wtedy program `python3`, a nie `house out`.

Lista dozwolonych komend (`permissions.allow`) ma osobną regułę dla każdej komendy `bin/house` (generowaną z listy
podkomend CLI) poza `out`. `house out` ma tylko `out show`, `out prune` i `out -- <komenda>` dla komend, które konkurs
i tak ma dozwolone (`git status`, `pytest`, `npm run` itd.). Każda inna komenda opakowana w `house out --` przechodzi
zwykłe pytanie o zgodę (albo klasyfikator trybu auto), tak jak bez opakowania. Dawna ogólna reguła
`Bash(<root>/bin/house:*)` przepuszczała `house out -- <cokolwiek>`; istniejące konkursy dostają nowe reguły
(a stara znika) po `house adapt <konkurs>`.

## Kompakcja kontekstu

`house adapt` ustawia w tym samym pliku progi kompaktowania kontekstu: `autoCompactWindow: 200000` i zmienną
`CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS=150000`. Claude Code kompaktuje sam przy ~167K tokenów (okno minus bufor ~33K),
a bez tego ustawienia dopiero przy ~967K na modelach z oknem 1M. Kompaktowanie w bezczynności rusza po ~1 h ciszy,
gdy cache jest ciepły (TTL 1 h), a sesja ma co najmniej 150K tokenów; `"idleCompaction": false` je wyłącza.
Wartość już obecna w `settings.local.json` wygrywa, także gdy zapisał ją wcześniejszy `house adapt`; `house adapt`
nie nadpisuje jej, więc żeby przyjąć nowy domyślny próg, usuń klucz i uruchom `house adapt` ponownie. Nocni pracownicy
używają tego samego pliku (`--settings`), więc też je dostają.

Sesję konkursu Claude Code kompaktuje według własnych, wbudowanych instrukcji; house nie dodaje swoich (K5:
nasze instrukcje w ośmiu sekcjach nie dały mierzalnej różnicy, `docs/research/k5/2026-11-18-compaction.md`).
Zaraz po kompaktowaniu `SessionStart` przywraca dosłownie obowiązujące decyzje człowieka (`D-###` z `Kto: człowiek`,
niezastąpione polem `Zastępuje:`; odpowiedzi z Signala są już takimi wpisami), odpowiedzi na `Q-###` wpisane ręcznie
w `QUESTIONS.md`, a potem otwarte pytania i zadania w toku, do 10 000 znaków. Hooki `PreCompact` i `PostCompact`
z wcześniejszego `house adapt` znikają przy następnym `house adapt`; do tego czasu nic nie robią.

## Hooki git

Logika w `houselib/githook.py`, wejście `bin/house-githook`. `house adapt` wpisuje do `.git/hooks/` repo konkursu
dwa małe skrypty (`pre-commit`, `pre-push`). Git uruchamia je przy każdym commicie i pushu, więc reguły działają dla
Claude Code, Codexa i zwykłej powłoki, bez zgadywania, co robi komenda.

Sprawdzają **tylko worktree zadań** (linked worktree konkursu). Główny checkout, w którym commitujesz Ty, i repo
spoza house są pominięte.

- `pre-commit` odrzuca sekrety (ocena po treści z indeksu), pliki `.house/` i zmiany chronionych plików. Dwa ostatnie
  pomija w trakcie merge, rebase i cherry-pick; sekrety sprawdza zawsze.
- `pre-push` odrzuca remote spoza `spec.guard.push_allow` (adres URL zamiast nazwy też), usunięcie głównej gałęzi
  i force push na nią. Force push na gałąź zadania przechodzi.

Błąd samego hooka nie blokuje pracy (jedna linia ostrzeżenia, exit 0). Nie ma przełącznika środowiskowego (także
`HOUSE_GUARD=off` nie działa), bo agent kontroluje środowisko własnych komend. Hook Claude Code blokuje agentowi
`--no-verify`, `git commit -n`, `core.hooksPath` (poza odczytem) oraz zapis do `.git/hooks` i `.git/config`.

Jeśli `core.hooksPath` jest już ustawione (np. husky) albo istnieje cudzy hook, house go nie nadpisuje; `house adapt`
i `house doctor <konkurs>` to zgłaszają. Obejście dla człowieka: commit w głównym checkoucie. Istniejące konkursy
dostają hooki po `house adapt <konkurs>`.

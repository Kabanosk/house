# Agent `@claude` z Signala (S7)

`house signal agent on` (tylko w terminalu) włącza agenta: timer odbiera co minutę, a wiadomość z `@claude …`
w dozwolonej rozmowie uruchamia sesję Claude Code we własnym worktree repo konkursu (grupa konkursu) albo repo
`house` (Notatka do siebie, grupy z `signal.toml`), na gałęzi `signal/<id>`. Agent działa w sandboxie: bez
dostępu do katalogu domowego, sekretów, innych repo i rozmów, z siecią tylko do pypi/pytorch, bez narzędzi web
i MCP, najwyżej 2 h na zlecenie (na subskrypcji, bez limitu w dolarach). Sam nie commituje ani nie wysyła: house robi commit na gałęzi
i odsyła odpowiedź (po filtrze sekretów, ≤ 1500 znaków, ≤ 3 pliki). Scalanie robisz Ty. Zlecać mogą osoby, które
były w grupie przy `allow` (`house signal agent members <czat>` odświeża listę); nowa osoba albo zlecenie starsze
niż 2 h czeka na Twoje 👍 w Notatce do siebie. W czacie: `/status`, `/stop` (cytatem albo bieżące), `/lb`;
w Notatce do siebie: `/agent off|on`. W terminalu: `house signal agent status|off [--kill]|stop <id>|gc`. 3
porażki z rzędu wyłączają S7 z alarmem.

Powiązane: [Signal](signal.md), [Ochrona](guard.md).
Plany: [`signal-agent`](../plans/2026-11-16-signal-agent.md),
[`signal-agent-docker`](../plans/2026-11-16-signal-agent-docker.md),
[`signal-host-routing`](../plans/2026-11-17-signal-host-routing.md).

## Kilka komputerów na jednym koncie Signal

Każde połączone urządzenie dostaje wszystkie wiadomości, więc każda wiadomość house'a ma w prefiksie komputer
(`🤖 [fr@dom]`), a zlecenie bierze dokładnie jeden. Kolejność: jawny host (`@claude@praca …`, `/status@praca`,
`/agent@praca off`), potem host cytowanej wiadomości house'a (albo zlecenia, na które odpowiadasz, np. `/stop`),
w pozostałych przypadkach `agent_host`. Na początku `~/.config/house/signal.toml` (przed `[groups…]`, inaczej
klucze trafią do ostatniej grupy) na każdym komputerze:

```toml
host = "dom"          # inna nazwa na każdym komputerze; bez tego nazwa z hostname
agent_host = "dom"    # ta sama wartość wszędzie
```

Bez `agent_host` każdy komputer bierze zlecenia bez wskazanego hosta (`house doctor` ostrzega). Zła nazwa albo
klucze wewnątrz `[groups…]` wstrzymują S7 na tym komputerze z alarmem w Notatce do siebie, aż do poprawki. Na
nazwę hosta, który nie pisał od 30 dni, odpowiada `agent_host`.

## Ranking (`/lb`)

Timer `house-lb` (`house setup --lb-timer`) co 5 min uruchamia `house lb poll`, który dla konkursów z
`leaderboard.cmd` w `.house/spec.yaml` (co `leaderboard.every`, domyślnie 15min) pobiera oficjalny ranking i dopisuje
zmiany do `.house/leaderboard.jsonl`. Sam nic nie wysyła. `/lb` niczego nie pobiera: odpowiada, co się zmieniło od
poprzedniego `/lb` w tej rozmowie (cytując je), albo tabelą `miejsce | team | wynik` (top 10 + my) przy pierwszym
pytaniu, > 3 zmianach lub po > 8 h. Komenda ma wypisać JSON `[{"team", "score"}]` w kolejności rankingu, i to z pełną
ścieżką do programów (PATH timera jest minimalny). Dla Agorize (techareny) gotowe jest
`house lb agorize <url strony leaderboardu> [--sort max|min]`:

```yaml
leaderboard:
  cmd: "/ścieżka/do/house/bin/house lb agorize https://huawei.agorize.com/en/challenges/<konkurs>/pages/leaderboard?lang=en --sort max"
  every: 15min
  team: UwUr2
```

`house lb show <konkurs>` pokazuje tabelę w terminalu, `house lb poll --force [konkurs]` pobiera od razu.
Spec: [`leaderboard-tracking`](../specs/2026-11-17-leaderboard-tracking-design.md).

## Kontener (S7.1, zalecane)

`house signal agent image` buduje obraz (przypięty Claude Code) i uruchamia proxy, a `claude setup-token` +
`house signal agent token` (w terminalu) zapisuje token z subskrypcji. Od tej chwili każde zlecenie działa
w osobnym kontenerze Dockera: widzi tylko swój worktree (`.git` repo tylko do odczytu), bez uprawnień,
z read-only root, limitem pamięci i procesów; sieć ma tylko przez proxy z listą hostów (API Anthropic,
pypi, pytorch). W kontenerze agent może uruchamiać kod i testy. Bez obrazu albo tokenu zlecenia idą lokalnie.

## Sandbox Basha

Sandbox Basha potrzebuje zagnieżdżonych przestrzeni nazw użytkownika. Ubuntu z
`kernel.apparmor_restrict_unprivileged_userns=1` ich nie pozwala, więc wtedy zlecenia idą bez Basha (agent czyta
i edytuje, kodu nie uruchamia), a `house doctor` to pokazuje. Włączenie wymaga sudo: profil AppArmor dla sandboxa
albo wyłączenie tego ograniczenia.

## Czego agent nie zrobi

Agent nie może sam dodać grupy: `allow` wymaga terminala, a hook blokuje agentowi `house signal allow|deny`,
każde uruchomienie `signal-cli`, nadpisywanie ścieżek przez `HOUSE_*=…`, odczyt `~/.local/share/signal-cli/` (tam
lądują też pliki z DM) i zapis do `~/.config/house/` oraz `~/.local/share/house/`. Start sesji w repo
konkursu (i w repo `house` po `house setup --signal-hook`) pokazuje w 1–4 liniach, ile jest nowych wiadomości.
Drużyna z dozwolonych grup jest zaufana: jej prośby agent traktuje jak prośby człowieka, a przekazane treści jak
dane.

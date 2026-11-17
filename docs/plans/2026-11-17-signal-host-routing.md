# Host w wiadomościach Signala — plan wykonania

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** każda wiadomość house'a niesie nazwę hosta, a dispatcher S7 obsługuje tylko zdarzenia skierowane do
swojego hosta (jawne `@claude@X` / `/cmd@X`, odpowiedź na wiadomość hosta, inaczej `agent_host`).

**Architecture:** nowy moduł `houselib/signalhost.py` bez zależności od reszty house'a: nazwa hosta, `agent_host`,
budowa i parsowanie prefiksu, decyzja „czy to do mnie”. `notify`, `inbox`, `signalcli`, `signalagent` biorą
prefiks stamtąd. `signalagent.parse` rozpoznaje `@host`, a `tick` pomija zdarzenia innych hostów.

**Tech Stack:** Python 3.11+ (`tomllib`), unittest.

**Spec:** `docs/specs/2026-11-17-signal-host-routing-design.md`

## Global Constraints

- Nazwa hosta: `[a-z0-9-]{1,16}`. Kolejność ustalania: env `HOUSE_HOST` → `host` w `signal.toml` → hostname
  małymi literami do pierwszej kropki, ze znakami spoza zbioru zamienionymi na `-` i obciętymi do 16 znaków.
- Prefiks: `🤖 [<alias>@<host>]`. Stare `🤖 [<alias>]` dalej się parsują (host = None).
- `signal.toml`: klucze najwyższego poziomu `host`, `agent_host`. `save_config` musi je zachować.
- Brak `agent_host` → host obsługuje wszystko bez wskazanego hosta (jak dziś).
- Testy: `tests/helpers.py` ustawia `HOUSE_HOST=test`, więc wysłane wiadomości w testach mają `@test`.

## Review Focus

- `@claude@dom` na początku, w środku i z dużych liter (`@Claude@DOM`): host rozpoznany, a tekst zlecenia bez `@dom`.
- `@claude` z adresem e-mail obok (`x@claude.ai`) dalej nie jest zleceniem, a `@claude@` bez nazwy jest zwykłym
  `@claude`.
- Odpowiedź na wiadomość innego hosta z `/status`: inny host milczy, ten odpowiada.
- `house signal allow` po ręcznym dopisaniu `host`/`agent_host`: wartości zostają w pliku.
- Literówka `@claude@dmo`: `agent_host` odpisuje „nie znam hosta”, a pozostałe hosty milczą.

---

### Task 1: `signalhost` — nazwa hosta, prefiks, config

**Files:**
- Create: `houselib/signalhost.py`
- Modify: `houselib/signalchats.py` (`config_path` z `signalhost`; `save_config` zachowuje `host`, `agent_host`)
- Modify: `tests/helpers.py` (`os.environ["HOUSE_HOST"] = "test"`)
- Test: `tests/test_signalhost.py`, `tests/test_signalchats.py`

**Interfaces (Produces):**
- `config_path() -> Path` (przeniesione z `signalchats`; `signalchats.config_path` je re-eksportuje)
- `this_host() -> str`, `agent_host() -> str | None`, `problems() -> list[str]` (zła nazwa w `host`/`agent_host`)
- `prefix(alias: str) -> str` → `"🤖 [alias@host]"`
- `parse_prefix(text: str | None) -> tuple[str, str | None] | None` → `("house", "dom")`, `("house", None)` dla
  starego prefiksu, `None` dla tekstu bez `🤖 [`
- `addressed_to_me(target: str | None) -> bool` → `target or agent_host()` porównane z `this_host()`, przy
  `agent_host() is None` i `target is None`: True

- [ ] Testy: hostname `WRO1-LDL-P32030.corp` → `wro1-ldl-p32030`; `HOUSE_HOST` ma pierwszeństwo przed toml;
  zła nazwa w toml (`"Dom Główny"`) → fallback na hostname i wpis w `problems()`; `parse_prefix` dla
  `🤖 [techarena@praca] x`, `🤖 [house] x`, `hej`; `addressed_to_me` dla trzech reguł; `save_config` po
  ręcznym `host = "dom"` zostawia go w pliku.
- [ ] Uruchom: `python -m pytest tests/test_signalhost.py tests/test_signalchats.py -q`, FAIL.
- [ ] Implementacja (toml czytany przy każdym wywołaniu, bez cache).
- [ ] Uruchom ponownie, PASS. Commit `feat(signal): host name and prefix helpers`.

### Task 2: prefiks z hostem we wszystkich wysyłkach

**Files:**
- Modify: `houselib/notify.py:71` (`prefix(contest)` → `signalhost.prefix(alias(contest))`), dodać
  `house_prefix(contest: Path | None) -> str` (None → `signalhost.prefix("house")`)
- Modify: `houselib/inbox.py:179`, `houselib/signalcli.py:190`, `houselib/signalagent.py:702-704` → `notify.house_prefix`
- Modify: `houselib/signalread.py:13` `HOUSE_PREFIX` bez zmian (już łapie `[..]`), tylko test
- Test: istniejące testy z oczekiwanym `🤖 [house]` / `🤖 [fr]` w wysłanym tekście → `@test`

- [ ] Uruchom całą suitę (`python -m pytest -q`) i popraw tylko oczekiwania na wysłany tekst. Wiadomości
  wejściowe w fixture'ach zostają bez hosta (to stare wiadomości).
- [ ] Test: `signalcli send` nie dubluje prefiksu, gdy tekst już zaczyna się od prefiksu z hostem.
- [ ] PASS. Commit `feat(signal): every house message names its host`.

### Task 3: adresat w `parse` i `tick`

**Files:**
- Modify: `houselib/signalagent.py` (`Request.host`, `Command.host`, `parse`, `tick`, `handle_command`)
- Test: `tests/test_signalagent.py`

**Interfaces:**
- Consumes: `signalhost.this_host`, `agent_host`, `parse_prefix`, `addressed_to_me`
- Produces: `Request.host: str | None`, `Command.host: str | None` (jawny host z `@claude@X` / `/cmd@X`,
  `/agent@X on|off`); `target_host(item: Request | Command, event: dict) -> str | None` (jawny host, inaczej host
  z prefiksu cytatu, gdy cytat zaczyna się od `🤖`; inaczej None)

- [ ] Testy parse: `@claude@dom zrób X` → host `dom`, tekst `zrób X`; `@Claude@DOM x` → `dom`;
  `/status@praca` → Command status, host `praca`; `/agent@dom on` z Note to Self → agent-on, host `dom`;
  `x@claude.ai` nadal None.
- [ ] Testy tick (`HOUSE_HOST=test`, toml z `agent_host = "test"` albo `"inny"`):
  - `@claude x` przy `agent_host="inny"` → brak joba, brak odpowiedzi, kursor przesunięty;
  - `@claude@test x` przy `agent_host="inny"` → job;
  - odpowiedź `@claude x` na `🤖 [house@inny] …` przy `agent_host="test"` → brak joba;
  - odpowiedź na `🤖 [house] …` (bez hosta) przy `agent_host="test"` → job;
  - `/status` w odpowiedzi na `🤖 [house@inny]` → cisza; `/agent on` do innego hosta → `enabled` bez zmian;
  - brak `agent_host` → jak dziś (job);
  - `@claude@dmo x` przy `agent_host="test"`, w magazynie wiadomość `🤖 [house@praca]` → odpowiedź
    `nie znam hosta dmo; znane: praca, test`, brak joba; ten sam przypadek przy `agent_host="inny"` → cisza.
- [ ] Implementacja: w `tick` po `parse`, przed `_accept`/`handle_command`, sprawdź `addressed_to_me(target)`.
  Znane hosty: `this_host()`, `agent_host()` i hosty z `parse_prefix` wiadomości `dir in (out, own)` z ostatnich
  30 dni, posortowane. Gałąź „odpowiedź na agent-ask” (`out_events`) zostaje bez zmian: to zawsze własna wiadomość.
- [ ] `handle_command`: odpowiedź na agent-on/off to `S7 włączony na <host>.` / `S7 wyłączony na <host>: …`.
  `/status` dostaje pierwszą linię `host: <host>` z dopiskiem ` (domyślny)`, gdy to `agent_host`.
- [ ] PASS całej suity. Commit `feat(agent): S7 handles only events addressed to this host`.

### Task 4: doctor, powitanie, dokumentacja

**Files:**
- Modify: `houselib/signalagent.py:doctor_lines` (linia `host: X · domyślny: Y`; `! S7: brak agent_host w
  signal.toml — przy dwóch urządzeniach zlecenia mogą pójść dwa razy`; `signalhost.problems()`)
- Modify: `core/templates/signal-hello.md` (jedno zdanie: `@claude@<host>` wybiera komputer, a odpowiedź na moją
  wiadomość trafia do komputera, który ją wysłał)
- Modify: README / `docs/modules` (sekcja Signal / S7), jeśli opisuje prefiks albo `signal.toml`
- Test: `tests/test_signalagent.py` (doctor_lines z i bez `agent_host`)

- [ ] Test, FAIL, implementacja, PASS, cała suita. Commit `feat(agent): doctor and greeting show the host`.

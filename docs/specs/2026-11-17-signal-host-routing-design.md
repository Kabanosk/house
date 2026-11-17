# Host w wiadomościach Signala i kierowanie odpowiedzi do hosta (2026-11-17)

_Stan: **szkic do akceptacji przez Wojtka**. Mała zmiana: prefiks wiadomości i reguła „kto obsługuje” w dispatcherze
S7. Wybrany wariant 2 (domyślny host + jawny wybór `@claude@host`)._

## 1. Po co

Konto Signal Wojtka jest połączone z dwoma komputerami. Każde połączone urządzenie dostaje wszystkie wiadomości,
więc każdy komputer z house'em widzi każde `@claude`, `/status` i `/agent on`. Nie widać, który komputer odpisał,
a zlecenie bierze ten, który akurat ma tick. 2026-11-17 odpowiedź `@claude zrób w tle review codexem` na
notatkę sesji z laptopa uruchomiła job na drugim komputerze. Tamten nie ma Dockera ani sandboxa, więc job ruszył
bez Basha i skończył się `error_during_execution` (job `3b8e8ad56a5abe46`). Gdyby oba komputery miały włączony
S7, każde zlecenie poszłoby dwa razy.

Cel: każda wiadomość house'a mówi, z którego komputera przyszła. Odpowiedź na nią trafia do tego komputera.
Nowe zlecenie trafia do hosta domyślnego, chyba że wskażesz inny.

## 2. Cele, poza zakresem, porażka

- **Cele:** nazwa hosta w każdej wysłanej wiadomości. Dokładnie jeden host obsługuje każde zlecenie i komendę. Hosta
  można wskazać jawnie.
- **Poza zakresem:** synchronizacja stanu S7 (kolejki, kosztów, członków) między hostami. Przenoszenie jobów między
  hostami. Naprawa `agent off`, który zostawia tick w timerze (osobny błąd, `signalcli.py:537`).
- **Porażka, nawet jeśli działa:** zlecenie wykonane dwa razy. Zlecenie po cichu połknięte, bo nikt nie uznał się
  za adresata (literówka w nazwie hosta, brak konfiguracji). Grupa techarena musi pisać nazwę hosta przy każdym
  `@claude`.

## 3. Podejścia (rozważone)

1. Jeden domyślny host, pozostałe milczą. Najprostsze, ale nie da się wybrać maszyny.
2. **Domyślny host + jawny wybór `@claude@<host>` + odpowiedź idzie do autora cytowanej wiadomości. Wybrane.**
3. Tylko jawny wybór. Bezpieczne, ale uciążliwe dla grupy.

## 4. Projekt

**Nazwa hosta.** W `~/.config/house/signal.toml` na każdym komputerze:

```toml
host = "dom"          # krótka nazwa tego komputera; brak → hostname małymi literami, do pierwszej kropki
agent_host = "dom"    # kto bierze zlecenia i komendy bez wskazanego hosta; ta sama wartość na wszystkich hostach
```

Nazwa: `[a-z0-9-]{1,16}`. Inna wartość daje błąd przy wczytaniu configu.

**Prefiks.** Wszystkie wysyłki (`notify.prefix`, `inbox.py:179`, `signalcli.py:190`, `signalagent.py:704`)
dostają hosta: `🤖 [house@dom]`, `🤖 [techarena@dom]`. Jedna funkcja buduje prefiks, a jedna go parsuje
(`alias`, `host | None`). Stare wiadomości bez `@host` dalej się parsują (host = None).
`signalread.snippet` dalej obcina cały prefiks.

**Kto obsługuje zdarzenie** (w `tick`, przed `approval`/`parse`). Pierwsza pasująca reguła:

1. `@claude@X …` albo `/komenda@X` → obsługuje tylko host `X`.
2. Wiadomość cytuje wiadomość house'a (cytowany tekst zaczyna się od `🤖 [`) → obsługuje host z prefiksu cytatu.
   Jeśli cytat nie ma hosta (stara wiadomość), obsługuje `agent_host`. Własne wysyłki (`dir == "out"` z `meta`)
   wystarczą do rozpoznania „to moja”, ale o hoście decyduje prefiks, bo wysyłki drugiego hosta przychodzą jako
   `own` bez `meta`.
3. W pozostałych przypadkach obsługuje `agent_host`.

Host, który nie jest adresatem, przesuwa kursor i nic nie robi. To dotyczy też zatwierdzeń (`approval`) oraz
`/agent on|off` z Signala. Komenda zmienia S7 tylko na hoście-adresacie, a odpowiedź to potwierdza
(„S7 włączony na dom”).

**Literówka w nazwie hosta.** `agent_host` odpowiada na `@claude@X` i `/komenda@X`, gdy `X` nie występuje w prefiksie
żadnej wiadomości house'a z ostatnich 30 dni w magazynie: „nie znam hosta X; znane: dom, praca”. Dzięki temu
zlecenie nie przepada po cichu.

**Brak `agent_host`.** Host zachowuje się jak dziś (obsługuje wszystko bez wskazanego hosta). `house doctor`
ostrzega, że przy dwóch urządzeniach grozi podwójne wykonanie.

**Wiadomość powitalna i `/status`** pokazują nazwę hosta i to, czy jest domyślny.

## 5. Ograniczenia

- Spec `2026-11-17-package-layout-design.md` może przenieść te moduły. Ta zmiana idzie na obecnym układzie, a plan
  przeniesienia ją uwzględni.
- Sesje Claude, które pytają przez Note to Self, czytają odpowiedzi same. Zmiana ich nie
  dotyka poza prefiksem. Odpowiedź z `@claude` na ich wiadomość trafi do S7 na ich hoście, nie na innym.

## 6. Do decyzji wykonawcy

Gdzie dokładnie wpiąć regułę w `tick` (jedna funkcja `addressee(event) -> host | None`). Kształt testów. Treść
komunikatów.

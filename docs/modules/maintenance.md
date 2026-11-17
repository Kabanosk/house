# Utrzymanie systemu

## Aktualizacja systemu

Zmiany w `core/` albo `adapters/` (np. poprawiony playbook) przenosisz do konkursu przez `house adapt <nazwa>`.
Nadpisuje to wygenerowane pliki (`AGENTS.md`, `CLAUDE.md`, agenci i skille `house-*` w `.claude/`,
`.house/playbooks/`, `.house/protocols/`), ale nigdy specyfikacji, decyzji, pytań, zadań ani raportów.
Co wygenerował, zapisuje w `.house/generated.json` i rusza tylko te pliki: cudze pliki w `.claude/` (np. agenci
drużyny) zostają i nie trafiają do commita. Wygenerowany plik zmieniony ręcznie oraz własny `AGENTS.md`/`CLAUDE.md`
z repo konkursu zostają zachowane jako `*.orig` (z ostrzeżeniem). `house doctor <nazwa>` pokazuje, które
wygenerowane pliki ktoś zmienił.

Ile stałego kontekstu niesie sesja w konkursie (AGENTS.md, opisy skilli i agentów, duplikaty skilli):
`house doctor --context <nazwa>`. Pomiary: [`docs/context-budget.md`](../context-budget.md).
Plan: [`docs/superpowers/plans/2026-11-13-context-and-manifest.md`](../superpowers/plans/2026-11-13-context-and-manifest.md).

## Gdy coś nie działa

| Objaw | Co zrobić |
|---|---|
| `coder-codex` nie istnieje albo kończy się kodem 3 | `house doctor`: brak Codex CLI → `npm i -g @openai/codex && codex login`, potem `house adapt` |
| Agent pisze „$HOUSE_DIR” zamiast ścieżki | `house adapt <nazwa>` (stare wygenerowane pliki) |
| `house export` zatrzymuje się na „possible secrets” | usuń klucz z pliku (i z historii, jeśli trzeba); `--force` tylko przy fałszywym alarmie |
| Agent wraca do sprawdzonego pomysłu | sprawdź, czy `ideas.md` jest uzupełniane; ideator musi je czytać |
| Briefing nie pokazuje zmian | `.house/state.json` → `last_briefing` ustaw na `null`, żeby zebrać wszystko od początku |

## Zasady

- Repo konkursu jest zawsze prywatne. Publicznie tylko przez `bin/house export <nazwa> <katalog>`
  (bez `.house/`, configów agentów i sekretów, ze skanem kluczy API).
- Nazwy konkursów: małe litery i cyfry rozdzielone pojedynczym myślnikiem, np. `techarena-finland-2026`.
- Git: tożsamość ustawiana lokalnie w każdym repo (kopiowana z repo systemu), nigdy globalnie.
- Zmiana narzędzia (np. z Claude Code na inne) = nowa nakładka w `adapters/`, rdzeń zostaje.

## Testy

```bash
python3 -m unittest discover -s tests -v
```

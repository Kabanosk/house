# Roadmap

Kolejność uzgodniona 2026-11-05/06. Każdy projekt dostaje osobny dokument do akceptacji przed budową.

1. **Jakość kodu** (w toku): `CODING_STANDARDS.md` (Python/TS), automatyczne sprawdzenia (ruff + skrypt AST przez
   `uv`; tsc + ESLint przez `npx`), cache w kodzie (`functools.cache`, `cache()`), gotowe konfiguracje od startu,
   `.claude/settings.json` z listą dozwolonych komend, `spec.deliverables.format` + krok pakowania zgłoszenia.
2. **Tryb całodzienny i nocny (TechArena)**: dispatcher (start/wznów/pomiń, nigdy nie pisze kodu), praca nigdy nie
   czeka na człowieka, nocne treningi, wykorzystanie dziennych slotów, pull na start i push po każdym kroku,
   brief per zadanie dla przejmującego agenta.
3. **Benchmark i testy jakości skilli**: powtórki własnych konkursów bez starych rozwiązań; ćwiczenia z researchu:
   symulowany 6-godzinny hackathon (publiczny URL, test Playwright, film 3 min, 2 slajdy techniczne),
   **failure injection** w połowie (zepsute API, deploy, schemat odpowiedzi AI), single agent vs zespół
   (czas do działającego demo, interwencje człowieka, nieudane merge, E2E, ślepa ocena sędziego);
   `claude plugin eval` / skill-creator do mierzenia skilli.
4. **Prezentacje i filmiki** z Claude/Codex: nagrywanie dema (Playwright video), montaż, slajdy, szablon
   `core/protocols/demo-video.md`.

## Pomysły na później
- **Kompresja ról (role compression) / fabryka agentów**: zacząć od małego rejestru profili (project-lead, fullstack, ai-engineer,
  qa-demo-critic, researcher), a frontend/DevOps/dane/prezentację traktować jako skille; osobnego specjalistę
  generować dopiero, gdy ten obszar powtarzalnie jest wąskim gardłem (hiring agent).
- **MLflow** (albo lżejszy odpowiednik) do śledzenia eksperymentów w konkursach optymalizacyjnych: parametry, wersja
  kodu, metryki, artefakty — gdy `submissions.csv` i `history/` przestaną wystarczać.
- **API-first / pretrained-first** na jednej karcie RTX 3060: trenować tylko to, co wyróżnia rozwiązanie.
- Kalibracja sędziego względem werdyktów jury (`/house-retro`).

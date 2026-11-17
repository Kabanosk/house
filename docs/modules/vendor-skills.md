# Skille zewnętrzne (`vendor/skills/`)

Skopiowane bez zmian, z licencją i `ATTRIBUTION.md`. Agenci czytają je po ścieżce z `vendor/skills/` (profile
w `core/agents/` wskazują plik); `house adapt` nie kopiuje ich do konkursów, żeby ich opisy nie zajmowały kontekstu
każdej sesji (stare kopie identyczne z `vendor/` usuwa).

| Skill | Źródło | Kto używa |
|---|---|---|
| `codebase-design` | Matt Pocock (MIT) | coordinator przy planowaniu modułów, judge przy review struktury |
| `frontend-design` | Anthropic (Apache-2.0) | coder przy zadaniach z UI |
| `webapp-testing` | Anthropic (Apache-2.0) | judge: Playwright, zrzuty ekranu, błędy konsoli w demo |
| `no-ai-slop` | Peter Yang (MIT) | pitch: skrypt, slajdy, README bez „AI slop” |
| `second-opinion` | Jens van Bellen (MIT) | podstawa `house review`; interaktywnie: niezależna opinia innego modelu |
| `video-director` | win4r (MIT) | pitch: reżyseria filmiku (hak w 3 s, `timeline.json`, kontrola `qa_video.py`) |

Aktualizacja: sklonuj źródło na nowo i podmień folder (nie edytuj w miejscu).

`remotion-best-practices` (oficjalny skill Remotion, budowanie filmiku w kodzie) **nie** jest w `vendor/`, bo jego
repo nie ma licencji pozwalającej na kopiowanie. `house setup` pobiera go na każdej maszynie z
[remotion-dev/skills](https://github.com/remotion-dev/skills) do `~/.claude/skills/` (cache w `~/.cache/house/`,
offline zostaje poprzednia kopia). Remotion jest darmowy dla osób prywatnych i firm do 3 osób. Zasady filmiku
i nadpisania obu skilli: [`core/protocols/demo-video.md`](../../core/protocols/demo-video.md).

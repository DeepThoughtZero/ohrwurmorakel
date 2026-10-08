# CLAUDE.md – Arbeitsweise im Projekt Ohrwurm-Orakel

> **Wichtigste Regel: Commit und Push immer direkt auf `main`.**

## Git-Workflow (verbindlich)

Nach **jeder** Änderung – insbesondere bei allen merklichen Änderungen (Features, Fixes, UI-Anpassungen, Playlist-Updates) – immer direkt committen und auf `main` pushen:

```bash
git add -A
git commit -m "Kurze, beschreibende Commit-Message auf Deutsch"
git push origin main
```

- Direkt auf `main` arbeiten – keine Feature-Branches, keine Pull Requests.
- Nicht nachfragen, ob committet/gepusht werden soll: `add` → `commit` → `push` gehört zu jeder erledigten Aufgabe.
- Commit-Messages auf Deutsch, kurz und aussagekräftig (Stil der bisherigen History).

## Projekt-Überblick

- Musik-Quiz als Single-Page-App, gehostet über GitHub Pages:
  https://deepthoughtzero.github.io/ohrwurmorakel/
- Alles steckt in **`index.html`** – kein Build, keine Abhängigkeiten:
  1. `<style>`: Design-Tokens (`:root`) und alle Komponenten (Bühne, Plattenspieler, Antwort-Tafeln, Buttons, Panel, Modals)
  2. Markup: Header, Spielbereich (`.game`), Einstellungs-Panel mit Tabs (Playlists / Timer / Verlauf), Modals
  3. `<script>` mit den eingebetteten Playlists (`PLAYLISTS`)
  4. `<script>` mit der Spiellogik (YouTube IFrame API, Timer, Auflösung, Effekte)
- Externe Ressourcen: YouTube IFrame API, Google Fonts (Poppins, Unbounded).
- `material/`: Jahreskärtchen (PDF) und Vorschaubilder fürs README.

## Playlists pflegen

```js
PLAYLISTS.push({
    name: "Anzeigename",
    id: "eindeutige-id",
    songs: [
        { id: '<YouTube-Video-ID>', title: 'Interpret - Titel', year: 1985 },
    ]
});
```

- `title` immer im Format `Interpret - Titel` – die Antwort-Tafeln trennen am ersten ` - `.
- Für neue Playlists ein passendes Emoji in `PLAYLIST_ICONS` (Spiellogik-Script) eintragen, sonst wird 🎵 verwendet.

## Testen

- Lokal über einen Webserver öffnen (YouTube funktioniert nicht über `file://`):
  `python3 -m http.server 8000` → http://localhost:8000
- Kurz prüfen: Desktop- und Handy-Breite, Abspielen → Auflösen → Nächster Song, Timer, Playlist-Auswahl, Tastenkürzel.

## YouTube-Links prüfen und reparieren

- `python3 tools/check_youtube_links.py` – fragt pro Video den oEmbed-Endpunkt ab (gedrosselt, mit Cache in `tools/.youtube-check-cache.json`, abbrechbar und fortsetzbar) und meldet gelöschte/nicht einbettbare Videos, unpassende Titel und gleiche Video-IDs für verschiedene Songs. Bericht: `tools/youtube-check-report.md`. Optionen: `--playlist <id>`, `--via noembed`, `--delay`, `--limit`, `--suggest` (Ersatzvorschläge per yt-dlp).
- `python3 tools/find_replacements.py --playlist <id> [--apply]` – sucht per `yt-dlp` automatisch nach funktionierenden und einbettbaren Ersatz-Videos für alle als FEHLT oder KEIN_EMBED gemeldeten Songs einer Playlist und kann diese direkt in `index.html` übertragen.


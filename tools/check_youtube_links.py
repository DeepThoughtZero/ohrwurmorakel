#!/usr/bin/env python3
"""
Prüft die YouTube-Links aller Playlists in index.html.

Für jedes Video wird der oEmbed-Endpunkt abgefragt (kein API-Key nötig,
deutlich "leichter" als die Watch-Seite). Damit lässt sich erkennen:

  FEHLT       Video gelöscht / privat / gesperrt (HTTP 404/400)
  KEIN_EMBED  Einbetten deaktiviert (HTTP 401/403) – spielt im Quiz nicht ab
  VERDACHT    Video existiert, aber Titel/Kanal passen nicht zu "Interpret - Titel"
  OK          Interpret und Titel wiedergefunden

Gegen Sperren durch zu viele Anfragen:
  * Pausen zwischen den Anfragen (Standard 2-4 s, mit Zufallsanteil)
  * bei HTTP 429 exponentielles Zurückweichen, nach mehreren 429 Abbruch
  * Ergebnisse werden in einem Cache gespeichert -> ein abgebrochener Lauf
    kann einfach erneut gestartet werden und macht dort weiter
  * --via noembed fragt über noembed.com statt direkt bei YouTube

Nur Python-Standardbibliothek. Beispiele:

  python3 tools/check_youtube_links.py
  python3 tools/check_youtube_links.py --playlist kraftklub
  python3 tools/check_youtube_links.py --via noembed --delay 1
  python3 tools/check_youtube_links.py --suggest      # Ersatz per yt-dlp vorschlagen

Ergebnis: Konsolenausgabe + tools/youtube-check-report.md
"""

import argparse
import json
import random
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HTML = ROOT / "index.html"
DEFAULT_CACHE = ROOT / "tools" / ".youtube-check-cache.json"
DEFAULT_REPORT = ROOT / "tools" / "youtube-check-report.md"

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) ohrwurmorakel-linkcheck/1.0"

SONG_RE = re.compile(
    r"\{\s*id:\s*'(?P<id>[^']+)'\s*,\s*title:\s*'(?P<title>(?:[^'\\]|\\.)*)'\s*,\s*year:\s*(?P<year>\d+)"
)
PLAYLIST_NAME_RE = re.compile(r'^\s*name:\s*"(?P<name>[^"]+)"')
PLAYLIST_ID_RE = re.compile(r'^\s*id:\s*"(?P<id>[^"]+)"')

# Wörter, die in YouTube-Titeln häufig vorkommen, aber nichts zur Zuordnung beitragen
NOISE = {
    "official", "video", "music", "musicvideo", "audio", "lyrics", "lyric", "hd", "hq",
    "remastered", "remaster", "version", "live", "the", "der", "die", "das", "and",
    "und", "feat", "ft", "with", "a", "an", "of", "topic", "vevo", "4k", "clip",
}


# --------------------------------------------------------------------------- Parsing

def parse_songs(html_path):
    """Liest alle (nicht auskommentierten) Songs samt Playlist aus index.html."""
    songs = []
    playlist_name, playlist_id = None, None
    for lineno, line in enumerate(html_path.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("//"):
            continue
        m = PLAYLIST_NAME_RE.match(line)
        if m:
            playlist_name = m.group("name")
            continue
        m = PLAYLIST_ID_RE.match(line)
        if m:
            playlist_id = m.group("id")
            continue
        m = SONG_RE.search(line)
        if m:
            title = m.group("title").replace("\\'", "'")
            artist, _, song = title.partition(" - ")
            songs.append({
                "line": lineno,
                "playlist": playlist_name or "?",
                "playlist_id": playlist_id or "?",
                "id": m.group("id"),
                "title": title,
                "artist": artist.strip(),
                "song": song.strip(),
                "year": int(m.group("year")),
            })
    return songs


# --------------------------------------------------------------------------- Abfrage

class RateLimited(Exception):
    pass


def fetch_oembed(video_id, via):
    watch = f"https://www.youtube.com/watch?v={video_id}"
    if via == "noembed":
        url = "https://noembed.com/embed?" + urllib.parse.urlencode({"url": watch})
    else:
        url = "https://www.youtube.com/oembed?" + urllib.parse.urlencode({"url": watch, "format": "json"})

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimited()
        if e.code in (401, 403):
            return {"status": "KEIN_EMBED", "http": e.code}
        if e.code in (400, 404):
            return {"status": "FEHLT", "http": e.code}
        return {"status": "FEHLER", "http": e.code}

    if via == "noembed" and "error" in data:
        # noembed liefert Fehler mit HTTP 200; die Ursache steht im Text
        err = str(data.get("error", ""))
        if "401" in err or "403" in err:
            return {"status": "KEIN_EMBED", "error": err}
        if "429" in err:
            raise RateLimited()
        return {"status": "FEHLT", "error": err}

    return {"status": "DA", "yt_title": data.get("title", ""), "yt_author": data.get("author_name", "")}


# --------------------------------------------------------------------------- Abgleich

def normalize(text):
    text = text.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"), ("&", " and ")):
        text = text.replace(a, b)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def tokens(text):
    return [t for t in normalize(text).split() if t not in NOISE]


def coverage(expected, haystack):
    """Anteil der erwarteten Wörter, die im YouTube-Titel/Kanal vorkommen."""
    exp = tokens(expected)
    if not exp:
        return 1.0
    hay = set(normalize(haystack).split())
    squashed = normalize(haystack).replace(" ", "")
    hits = sum(1 for t in exp if t in hay or (len(t) > 3 and t in squashed))
    return hits / len(exp)


def judge(song, info):
    hay = f"{info.get('yt_title', '')} {info.get('yt_author', '')}"
    a = coverage(song["artist"], hay)
    t = coverage(song["song"], hay)
    info["artist_score"] = round(a, 2)
    info["title_score"] = round(t, 2)
    if t >= 0.6 and a >= 0.5:
        return "OK"
    return "VERDACHT"


# --------------------------------------------------------------------------- Vorschläge

def suggest(song, count=3):
    """Sucht per yt-dlp nach Alternativen (nur für Problemfälle aufrufen)."""
    if not shutil.which("yt-dlp"):
        return None
    query = f"ytsearch{count}:{song['artist']} {song['song']}"
    try:
        out = subprocess.run(
            ["yt-dlp", "--flat-playlist", "--dump-json", "--no-warnings", query],
            capture_output=True, text=True, timeout=60,
        ).stdout
    except subprocess.TimeoutExpired:
        return []
    results = []
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        results.append({"id": d.get("id"), "title": d.get("title"), "channel": d.get("channel") or d.get("uploader")})
    return results


def search_url(song):
    return "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": f"{song['artist']} {song['song']}"})


# --------------------------------------------------------------------------- Hauptprogramm

def main():
    ap = argparse.ArgumentParser(description="YouTube-Links in index.html prüfen")
    ap.add_argument("--html", type=Path, default=DEFAULT_HTML)
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    ap.add_argument("--playlist", help="nur diese Playlist (id oder Teil des Namens)")
    ap.add_argument("--via", choices=["youtube", "noembed"], default="youtube",
                    help="youtube = direkt oEmbed, noembed = über noembed.com (schont die eigene IP bei YouTube)")
    ap.add_argument("--delay", type=float, default=2.0, help="Grundpause zwischen Anfragen in Sekunden (Standard 2)")
    ap.add_argument("--limit", type=int, help="höchstens so viele neue Abfragen in diesem Lauf")
    ap.add_argument("--recheck", action="store_true", help="Cache ignorieren und alles neu abfragen")
    ap.add_argument("--suggest", action="store_true", help="für Problemfälle Ersatz per yt-dlp suchen (falls installiert)")
    args = ap.parse_args()

    songs = parse_songs(args.html)
    if args.playlist:
        p = args.playlist.lower()
        songs = [s for s in songs if p == s["playlist_id"].lower() or p in s["playlist"].lower()]
    if not songs:
        sys.exit("Keine Songs gefunden.")

    cache = {}
    if args.cache.exists() and not args.recheck:
        cache = json.loads(args.cache.read_text(encoding="utf-8"))

    todo = []
    seen = set()
    for s in songs:
        if s["id"] not in cache and s["id"] not in seen:
            todo.append(s["id"])
            seen.add(s["id"])
    if args.limit is not None:
        todo = todo[: args.limit]

    print(f"{len(songs)} Songs, {len(set(s['id'] for s in songs))} Videos, davon {len(todo)} neu abzufragen "
          f"(via {args.via}, ~{args.delay:.1f}-{args.delay * 2:.1f} s Pause).")

    backoff, rate_hits = 60, 0
    try:
        i = 0
        while i < len(todo):
            vid = todo[i]
            try:
                cache[vid] = fetch_oembed(vid, args.via)
                cache[vid]["checked"] = time.strftime("%Y-%m-%d")
                rate_hits = 0
                i += 1
                print(f"  [{i}/{len(todo)}] {vid}: {cache[vid]['status']} {cache[vid].get('yt_title', '')}")
            except RateLimited:
                rate_hits += 1
                if rate_hits > 3:
                    print("\nMehrfach HTTP 429 (zu viele Anfragen) – Abbruch. Später erneut starten, "
                          "der Cache sorgt dafür, dass es dort weitergeht.")
                    break
                print(f"  HTTP 429 – warte {backoff} s …")
                time.sleep(backoff)
                backoff *= 2
                continue
            except (urllib.error.URLError, TimeoutError) as e:
                print(f"  {vid}: Netzwerkfehler {e} – übersprungen")
                i += 1
            if i % 10 == 0:
                args.cache.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
            time.sleep(args.delay + random.uniform(0, args.delay))
    except KeyboardInterrupt:
        print("\nAbgebrochen – bisherige Ergebnisse werden gespeichert.")
    finally:
        args.cache.parent.mkdir(parents=True, exist_ok=True)
        args.cache.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")

    # Auswertung
    order = {"FEHLT": 0, "KEIN_EMBED": 1, "VERDACHT": 2, "FEHLER": 3, "OK": 4}
    results, unchecked = [], 0
    for s in songs:
        info = cache.get(s["id"])
        if not info:
            unchecked += 1
            continue
        info = dict(info)
        status = judge(s, info) if info["status"] == "DA" else info["status"]
        results.append((status, s, info))

    # Doppelte Video-IDs gleich mit melden
    by_id = {}
    for s in songs:
        by_id.setdefault(s["id"], []).append(s)
    def same_song(x, y):
        a = f"{x['artist']} {re.sub(r'[(].*?[)]', '', x['song'])}"
        b = f"{y['artist']} {re.sub(r'[(].*?[)]', '', y['song'])}"
        return coverage(a, b) >= 0.6 and coverage(b, a) >= 0.6
    # Nur melden, wenn dieselbe Video-ID für *verschiedene* Songs steht – dann ist mind. einer falsch
    dupes = {vid: lst for vid, lst in by_id.items()
             if any(not same_song(lst[0], o) for o in lst[1:])}

    problems = sorted([r for r in results if r[0] != "OK"], key=lambda r: (order[r[0]], r[1]["line"]))
    counts = {}
    for st, _, _ in results:
        counts[st] = counts.get(st, 0) + 1

    lines = ["# YouTube-Linkprüfung", "",
             f"Stand: {time.strftime('%Y-%m-%d %H:%M')} · geprüft: {len(results)} · ungeprüft: {unchecked}", "",
             " · ".join(f"**{k}**: {v}" for k, v in sorted(counts.items(), key=lambda kv: order[kv[0]])), ""]
    if problems:
        lines += ["| Status | Zeile | Playlist | Erwartet | YouTube-Titel / Kanal | Links |",
                  "|---|---|---|---|---|---|"]
        for st, s, info in problems:
            yt = f"{info.get('yt_title', '')} / {info.get('yt_author', '')}" if info.get("yt_title") else \
                 (info.get("error") or f"HTTP {info.get('http', '?')}")
            links = f"[Video](https://www.youtube.com/watch?v={s['id']}) · [Suche]({search_url(s)})"
            if args.suggest:
                sug = suggest(s)
                if sug is None:
                    links += " · (yt-dlp nicht installiert)"
                else:
                    for x in sug:
                        links += f"<br>`{x['id']}` {x['title']} ({x['channel']})"
            esc = lambda t: str(t).replace("|", "\\|")
            lines.append(f"| {st} | {s['line']} | {esc(s['playlist'])} | {esc(s['title'])} ({s['year']}) | {esc(yt)} | {links} |")
    if dupes:
        lines += ["", "## Gleiche Video-ID für verschiedene Songs", ""]
        for vid, lst in dupes.items():
            lines.append(f"- `{vid}`: " + "; ".join(f"Z. {s['line']} {s['title']}" for s in lst))
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print()
    for st, s, info in problems:
        yt = info.get("yt_title") or info.get("error") or f"HTTP {info.get('http', '?')}"
        print(f"{st:<10} Z.{s['line']:<5} {s['title']}  ->  {yt}"
              + (f"  [{info.get('yt_author')}]" if info.get("yt_author") else ""))
    if dupes:
        print("\nGleiche Video-ID für verschiedene Songs (ohne Abfrage erkannt):")
        for vid, lst in dupes.items():
            print(f"  {vid}: " + " | ".join(f"Z.{s['line']} {s['title']}" for s in lst))
    print("\n" + " · ".join(f"{k}: {v}" for k, v in counts.items()) + (f" · ungeprüft: {unchecked}" if unchecked else ""))
    print(f"Bericht: {args.report.relative_to(ROOT) if args.report.is_relative_to(ROOT) else args.report}")


if __name__ == "__main__":
    main()

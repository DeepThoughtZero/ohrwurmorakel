#!/usr/bin/env python3
"""
Sucht per yt-dlp nach funktionierenden Ersatz-Video-IDs für fehlerhafte YouTube-Links
(FEHLT / KEIN_EMBED) in Playlists aus index.html, prüft diese per oEmbed auf
Einbettbarkeit und kann sie direkt in index.html übertragen.

Beispiele:
  python3 tools/find_replacements.py --playlist swr1-hitparade-1-100
  python3 tools/find_replacements.py --playlist swr1-hitparade-101-200 --apply
"""

import argparse
import json
import subprocess
import time
from pathlib import Path
from check_youtube_links import parse_songs, fetch_oembed, DEFAULT_HTML, DEFAULT_CACHE

USER_OVERRIDES = {
    "Hubert von Goisern - Weit, weit weg": "BSPVdFggVy4",
    "Die Toten Hosen - Tage wie diese": "j09hpp3AxIE",
    "Reinhard Mey - Wie vor Jahr und Tag": "8zpxqjJMj64",
    "Queen - Love of my life": "sUJkCXE4sAA",
}


def search_top_embeddable(artist, song_title, existing_ids):
    """Sucht mit yt-dlp und liefert den ersten Treffer, der per oEmbed einbettbar ist."""
    # Suchbegriffe von Satzzeichen befreien für saubere YouTube-Suche
    clean_title = song_title.replace("'", "").replace('"', "")
    clean_artist = artist.replace("'", "").replace('"', "")
    query = f"ytsearch5:{clean_artist} {clean_title}"

    try:
        out = subprocess.run(
            ["yt-dlp", "--flat-playlist", "--dump-json", "--no-warnings", query],
            capture_output=True, text=True, timeout=30
        ).stdout
    except Exception as e:
        print(f"    Fehler bei yt-dlp Suche: {e}")
        return None

    for line in out.splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        vid = d.get("id")
        if not vid or vid in existing_ids:
            continue

        # Prüfe Einbettbarkeit und Existenz via oEmbed
        info = fetch_oembed(vid, "youtube")
        if info.get("status") == "DA":
            return {
                "id": vid,
                "yt_title": info.get("yt_title"),
                "yt_author": info.get("yt_author")
            }
        else:
            print(f"    Überspringe {vid} (oEmbed Status: {info.get('status')})")
    return None


def apply_replacements(html_path, replacements):
    """Wendet die Ersetzungen zeilenbasiert in index.html an."""
    lines = html_path.read_text(encoding="utf-8").splitlines()
    applied = 0
    for r in replacements:
        idx = r["line"] - 1
        if 0 <= idx < len(lines):
            line = lines[idx]
            old_str = f"id: '{r['old_id']}'"
            new_str = f"id: '{r['new_id']}'"
            if old_str in line:
                lines[idx] = line.replace(old_str, new_str)
                applied += 1
            else:
                print(f"  Warnung: '{old_str}' nicht in Zeile {r['line']} gefunden!")
    html_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n{applied} von {len(replacements)} Ersetzungen in {html_path.name} angewendet.")


def main():
    ap = argparse.ArgumentParser(description="Ersatz-Links für defekte YouTube-Videos suchen")
    ap.add_argument("--playlist", default="swr1-hitparade-1-100", help="Playlist-ID (z. B. swr1-hitparade-1-100)")
    ap.add_argument("--apply", action="store_true", help="Gefundene Ersetzungen direkt in index.html anwenden")
    args = ap.parse_args()

    cache = json.loads(DEFAULT_CACHE.read_text("utf-8")) if DEFAULT_CACHE.exists() else {}
    all_songs = parse_songs(DEFAULT_HTML)
    songs = [s for s in all_songs if s["playlist_id"].lower() == args.playlist.lower() or args.playlist.lower() in s["playlist"].lower()]

    if not songs:
        print(f"Keine Songs für Playlist '{args.playlist}' gefunden.")
        return

    used_ids = set(s["id"] for s in all_songs)
    replacements = []

    print(f"Prüfe Playlist '{args.playlist}' ({len(songs)} Songs)...")

    for s in songs:
        title = s["title"]
        old_id = s["id"]

        if title in USER_OVERRIDES:
            new_id = USER_OVERRIDES[title]
            info = fetch_oembed(new_id, "youtube")
            replacements.append({
                "line": s["line"],
                "title": title,
                "old_id": old_id,
                "new_id": new_id,
                "source": "USER",
                "yt_title": info.get("yt_title"),
                "yt_author": info.get("yt_author")
            })
            used_ids.add(new_id)
            print(f"Z.{s['line']} [USER-VORGABE] {title} -> {new_id} ({info.get('yt_title')})")
            continue

        c_info = cache.get(old_id, {})
        st = c_info.get("status")
        if st in ("FEHLT", "KEIN_EMBED"):
            print(f"Z.{s['line']} [SUCHE] {title} (bisher: {old_id}, Status: {st}) ...")
            cand = search_top_embeddable(s["artist"], s["song"], used_ids)
            if cand:
                used_ids.add(cand["id"])
                replacements.append({
                    "line": s["line"],
                    "title": title,
                    "old_id": old_id,
                    "new_id": cand["id"],
                    "source": "SEARCH",
                    "yt_title": cand["yt_title"],
                    "yt_author": cand["yt_author"]
                })
                print(f"  -> GEFUNDEN: {cand['id']} ({cand['yt_title']})")
            else:
                print(f"  -> KEIN TREFFER!")
            time.sleep(0.5)

    out_file = Path(__file__).resolve().parent / f"replacements_{args.playlist}.json"
    out_file.write_text(json.dumps(replacements, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{len(replacements)} Ersetzungen in {out_file.name} gespeichert.")

    if args.apply and replacements:
        apply_replacements(DEFAULT_HTML, replacements)


if __name__ == "__main__":
    main()

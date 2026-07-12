#!/usr/bin/env python3
"""Build tools/catalog.csv - every known PS3 serial with its official title.

Sources (downloaded with a local cache in tools/cache/):
  GameTDB ps3tdb.zip        disc + PSN entries with official titles (primary source)
  NoPayStation PS3_GAMES    PSN store dump - adds PSN serials GameTDB lacks

The catalog is the authority for which serials exist and what game they belong to; the seeder
groups serials into games by normalized official title (not by untrusted Artemis filenames).

Usage:
  python catalog.py [--out <repo-root>] [--refresh]
"""

import argparse, csv, io, re, unicodedata, urllib.request, zipfile
from pathlib import Path

GAMETDB_URL = 'https://www.gametdb.com/ps3tdb.zip'
NPS_URL = 'https://nopaystation.com/tsv/PS3_GAMES.tsv'
GAMEHACKING_URL = 'https://netcheat.gamehacking.org/gameList.txt'
GH_LINE = re.compile(r'^([A-Z]{4}\d{5}) (.+?)\|')
SERIAL = re.compile(r'^[A-Z]{4}\d{5}$')

def fetch(url, cachePath, refresh):
   if cachePath.exists() and not refresh:
      return cachePath.read_bytes()
   request = urllib.request.Request(url, headers={'User-Agent': 'game-cheats-catalog'})
   with urllib.request.urlopen(request, timeout=120) as response:
      data = response.read()
   cachePath.write_bytes(data)
   return data

def normalizeTitle(title):
   """grouping key: accent-folded, lowercase, alphanumeric only."""
   folded = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode('ascii')
   return re.sub(r'[^a-z0-9]+', '', folded.lower())

def main():
   ap = argparse.ArgumentParser()
   ap.add_argument('--out', default='.', help='game-cheats repo root')
   ap.add_argument('--refresh', action='store_true', help='re-download sources')
   args = ap.parse_args()

   outDir = Path(args.out)
   cacheDir = outDir / 'tools' / 'cache'
   cacheDir.mkdir(parents=True, exist_ok=True)

   catalog = {}   # titleId -> (title, source)

   # GameTDB first - official titles, disc + PSN. the dump's XML is not well-formed, so
   # extract per-game blocks with regex instead of an XML parser.
   zipData = fetch(GAMETDB_URL, cacheDir / 'ps3tdb.zip', args.refresh)
   with zipfile.ZipFile(io.BytesIO(zipData)) as zf:
      xmlText = zf.read('ps3tdb.xml').decode('utf-8', errors='replace')
   for block in re.findall(r'<game name="[^"]*">.*?</game>', xmlText, re.S):
      idMatch = re.search(r'<id>([A-Z]{4}\d{5})</id>', block)
      if not idMatch:
         continue
      titleId = idMatch.group(1)
      enMatch = re.search(r'<locale lang="EN">\s*<title>([^<]+)</title>', block)
      anyMatch = re.search(r'<locale lang="[^"]*">\s*<title>([^<]+)</title>', block)
      title = (enMatch or anyMatch).group(1).strip() if (enMatch or anyMatch) else ''
      if title:
         catalog[titleId] = (title, 'gametdb')

   # NoPayStation adds PSN serials GameTDB lacks
   npsData = fetch(NPS_URL, cacheDir / 'nps-ps3-games.tsv', args.refresh).decode('utf-8', errors='replace')
   npsAdded = 0
   for row in csv.DictReader(io.StringIO(npsData), delimiter='\t'):
      titleId = (row.get('Title ID') or '').strip().upper()
      title = (row.get('Name') or '').strip()
      if SERIAL.match(titleId) and title and titleId not in catalog:
         catalog[titleId] = (title, 'nps')
         npsAdded += 1

   # gamehacking's list catches serials both others miss (e.g. BCES01173 Uncharted 3, MRTC*)
   ghData = fetch(GAMEHACKING_URL, cacheDir / 'gamehacking-list.txt', args.refresh).decode('utf-8', errors='replace')
   ghAdded = 0
   for line in ghData.splitlines():
      match = GH_LINE.match(line.strip())
      if match and match.group(1) not in catalog:
         catalog[match.group(1)] = (match.group(2).strip().title(), 'gamehacking')
         ghAdded += 1

   with (outDir / 'tools' / 'catalog.csv').open('w', encoding='utf-8', newline='') as f:
      writer = csv.writer(f, lineterminator='\n')
      writer.writerow(['titleId', 'title', 'source'])
      for titleId in sorted(catalog):
         title, source = catalog[titleId]
         writer.writerow([titleId, title, source])

   groups = len({normalizeTitle(t) for t, _ in catalog.values()})
   print(f'catalog: {len(catalog)} serials (+{npsAdded} NPS, +{ghAdded} gamehacking on top of GameTDB), ~{groups} distinct titles')

if __name__ == '__main__':
   main()

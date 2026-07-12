#!/usr/bin/env python3
"""Seed source/ from the PS3 catalog + Artemis .ncl cheats + curated files.

Grouping is authoritative: tools/catalog.csv (built by catalog.py from GameTDB + NoPayStation)
decides which serials exist and which official title they share. EVERY catalog game gets a
source file; the group's PRIMARY serial (lowest id) holds the cheats, every other serial is a
DO-NOT-EDIT alias (same-as: <primary>). Games with no cheats yet get an empty primary so
contributors find the file already in place.

Cheats attach to a game by their titleId (from .ncl filename / curated filename), mapped through
the catalog. Cheats whose serial is not in the catalog create a standalone game and are flagged in
seed-report.txt. Artemis filename titles are NEVER used for grouping (they lie); only for a
sub-game name prefix when several titles ship under one serial.

Usage:
  python seeder.py --codes <artemis-dir> [--curated <file>...] [--out <repo-root>] [--limit N]
"""

import argparse, csv, re, sys, unicodedata
from pathlib import Path
from collections import defaultdict

TITLE_ID = re.compile(r'\b([A-Z]{4}\d{5})\b', re.IGNORECASE)
VERSION_TOKEN = re.compile(r'\b(?:a?v)?\d{1,2}\.\d{2}\b', re.IGNORECASE)
INLINE_COMMENT = re.compile(r'/\*.*?\*/|//.*$|;.*$')
HEX_OK = re.compile(r'^[0-9A-Fa-f]+$')

def normalizeTitle(title):
   folded = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode('ascii')
   return re.sub(r'[^a-z0-9]+', '', folded.lower())

def parseFilename(stem):
   """-> (filenameTitle, [titleIds]); version tokens discarded. Title is only for a sub-game prefix."""
   ids = [m.upper() for m in TITLE_ID.findall(stem)]
   title = VERSION_TOKEN.sub('', TITLE_ID.sub('', stem))
   return re.sub(r'\s{2,}', ' ', title).strip(' -_'), ids

# ncl record conversion (opcode 0 -> w8/w16/w32, opcode B -> aob; everything else skipped whole)

def convertValueWrite(address, valueHex, out):
   n = len(valueHex)
   address = address.upper()
   if not HEX_OK.match(valueHex):
      return 'placeholder-or-bad-hex'
   if n == 8:
      out.append(f'w32 {address} {valueHex.upper()}')
   elif n == 4:
      out.append(f'w16 {address} {valueHex.upper()}')
   elif n == 2:
      out.append(f'w8 {address} {valueHex.upper()}')
   elif n % 8 == 0:
      base = int(address, 16)
      for i in range(n // 8):
         out.append(f'w32 {base + i*4:08X} {valueHex[i*8:i*8+8].upper()}')
   else:
      return f'odd-hex-len-{n}'
   return None

def convertRecord(codeLines):
   ops, inBlockComment = [], False
   for raw in codeLines:
      line = raw
      if inBlockComment:
         if '*/' not in line:
            continue
         line, inBlockComment = line.split('*/', 1)[1], False
      if '/*' in line and '*/' not in line.split('/*', 1)[1]:
         line, inBlockComment = line.split('/*', 1)[0], True
      line = INLINE_COMMENT.sub('', line).strip()
      if not line:
         continue
      if line.startswith('[Z]'):
         return None, 'z-picker'
      parts = line.split()
      op = parts[0].upper()
      if op == '0' and len(parts) == 3:
         if not (HEX_OK.match(parts[1]) and len(parts[1]) == 8):
            return None, 'bad-address'
         reason = convertValueWrite(parts[1], parts[2], ops)
         if reason:
            return None, reason
      elif op == 'B' and len(parts) == 3:
         find, repl = parts[1], parts[2]
         if len(find) == 8 and len(repl) == 8 and HEX_OK.match(find) and HEX_OK.match(repl):
            continue  # control line, not pattern data
         if not (HEX_OK.match(find) and HEX_OK.match(repl)):
            return None, 'aob-bad-hex'
         if len(find) % 2 or len(repl) % 2:
            return None, 'aob-odd-len'
         ops.append(f'aob {find.upper()} {repl.upper()}')
      elif op in ('1', '2', '4', '5', '6', '8', '9', 'A', 'D'):
         return None, f'opcode-{op}'
      else:
         return None, 'unparsed-line'
   return (ops, None) if ops else (None, 'no-ops')

def parseNcl(path, stats):
   cheats = []
   for block in path.read_text(encoding='utf-8', errors='replace').split('\n#'):
      lines = [l.rstrip('\r') for l in block.split('\n')]
      lines = [l for l in lines if l.strip() and l.strip() != '#']
      if len(lines) < 4:
         continue
      ops, reason = convertRecord(lines[3:])
      if reason:
         stats['skipped'][reason] += 1
      else:
         cheats.append((lines[0].strip(), ops))
   return cheats

def parseCurated(path):
   """-> (filenameTitle, list of (name, ops)). Header '# Title' overrides for the sub-game prefix."""
   title, cheats, name, ops = path.stem, [], None, []
   for raw in path.read_text(encoding='utf-8', errors='replace').splitlines():
      line = raw.strip()
      if line.startswith('#'):
         header = line.lstrip('#').strip()
         if header and name is None and not cheats:
            title = re.split(r'[—-]{1,2}\s', header)[0].strip(' :')
      elif line.startswith('name:'):
         if name and ops:
            cheats.append((name, ops))
         name, ops = line[5:].strip(), []
      elif line.split(' ', 1)[0] in ('w8', 'w16', 'w32', 'aob'):
         ops.append(line)
   if name and ops:
      cheats.append((name, ops))
   return title, cheats

# output

def emit(outDir, primary, variants, title, cheats):
   lines = [f'# {title}', '']
   for name, ops in cheats:
      lines += [f'name: {name}', 'mode: once', *ops, '']
   (outDir / 'source' / f'{primary}.txt').write_text('\n'.join(lines), encoding='utf-8', newline='\n')
   for variant in variants:
      alias = [f'# {title}',
               f'# DO NOT EDIT THIS FILE - all variants share {primary}.txt, edit that instead',
               f'same-as: {primary}', '']
      (outDir / 'source' / f'{variant}.txt').write_text('\n'.join(alias), encoding='utf-8', newline='\n')

def main():
   ap = argparse.ArgumentParser()
   ap.add_argument('--codes', required=True)
   ap.add_argument('--curated', nargs='*', default=[])
   ap.add_argument('--out', default='.')
   ap.add_argument('--limit', type=int, default=0)
   args = ap.parse_args()

   outDir = Path(args.out)
   (outDir / 'source').mkdir(parents=True, exist_ok=True)

   # catalog: authoritative serial -> title, and title-group -> serials
   catalog = {}   # titleId -> officialTitle
   with (outDir / 'tools' / 'catalog.csv').open(encoding='utf-8') as f:
      for row in csv.DictReader(f):
         catalog[row['titleId']] = row['title']

   # manual overrides (Mohammed's seed-report resolutions): 'add' a real serial the sources miss,
   # or 'map' cheats from a wrong/typo'd serial onto the correct one's group
   remap = {}   # wrongSerial -> correctSerial
   overridesPath = outDir / 'tools' / 'overrides.csv'
   if overridesPath.exists():
      with overridesPath.open(encoding='utf-8') as f:
         for row in csv.DictReader(f):
            serial = row['serial'].strip()
            if row['title'].strip():
               catalog.setdefault(serial, row['title'].strip())
            if row['mapTo'].strip():
               remap[serial] = row['mapTo'].strip()
   groupSerials = defaultdict(list)   # normTitle -> [serials]
   groupTitle = {}                    # normTitle -> officialTitle (from lowest serial)
   for titleId in sorted(catalog):
      key = normalizeTitle(catalog[titleId])
      groupSerials[key].append(titleId)
      groupTitle.setdefault(key, catalog[titleId])
   titleIdToGroup = {tid: normalizeTitle(catalog[tid]) for tid in catalog}

   # collect cheats by their (catalog) group; unknown serials become standalone groups
   stats = {'skipped': defaultdict(int), 'kept': 0, 'dupes': 0, 'unknownSerial': 0}
   cheatsByGroup = defaultdict(list)   # normTitle -> [(name, ops, subTitle)]
   unknownSerials = {}                 # serial -> filenameTitle (not in catalog)

   sources = [('curated', Path(c)) for c in args.curated]
   nclFiles = sorted(Path(args.codes).glob('*.ncl'))
   if args.limit:
      nclFiles = nclFiles[:args.limit]
   sources += [('ncl', p) for p in nclFiles]

   curatedGroups = set()   # a game with a hand-curated file is authoritative; corpus is dropped for it
   for kind, path in sources:
      if kind == 'curated':
         fileTitle, cheats = parseCurated(path)
         ids = [remap.get(m.upper(), m.upper()) for m in TITLE_ID.findall(path.stem)]
      else:
         fileTitle, ids = parseFilename(path.stem)
         ids = [remap.get(t, t) for t in ids]
         cheats = parseNcl(path, stats)
      if not ids or not cheats:
         continue
      # attach to the group of the FIRST catalog-known serial; unknown-only files stand alone
      group = next((titleIdToGroup[t] for t in ids if t in catalog), None)
      if kind == 'ncl' and group in curatedGroups:
         continue   # curated file wins for this game
      if kind == 'curated' and group is not None:
         curatedGroups.add(group)
      if group is None:
         primary = min(ids)
         group = f'_unknown_{primary}'
         groupSerials.setdefault(group, sorted(set(ids)))
         groupTitle.setdefault(group, fileTitle or primary)
         for t in ids:
            unknownSerials.setdefault(t, fileTitle or t)
            stats['unknownSerial'] += 1
      subTitle = fileTitle if normalizeTitle(fileTitle) != group and not group.startswith('_unknown_') else ''
      for name, ops in cheats:
         cheatsByGroup[group].append((name.strip(), ops, subTitle))
      stats['kept'] += len(cheats)

   # emit every catalog game (+ unknown standalone games); primary = lowest serial
   emitted = emptyGames = serialFiles = 0
   for group, serials in groupSerials.items():
      serials = sorted(set(serials))
      primary = serials[0]
      title = groupTitle[group]
      seen, cheats = set(), []
      for name, ops, subTitle in cheatsByGroup.get(group, []):
         displayName = f'{subTitle}: {name}' if subTitle else name
         key = (displayName.lower(), tuple(ops))
         if key in seen:
            stats['dupes'] += 1
            continue
         seen.add(key)
         cheats.append((displayName, ops))
      emit(outDir, primary, serials[1:], title, cheats)
      serialFiles += len(serials)
      emitted += 1
      if not cheats:
         emptyGames += 1

   if unknownSerials:
      report = '\n'.join(f'{s},{t}' for s, t in sorted(unknownSerials.items()))
      (outDir / 'seed-report.txt').write_text('serials with cheats but NOT in catalog (review):\n' + report + '\n', encoding='utf-8')

   print(f"catalog games:      {len(groupSerials)}  (empty, awaiting cheats: {emptyGames})")
   print(f"serial files:       {serialFiles}")
   print(f"cheats kept:        {stats['kept']}  (duplicates folded: {stats['dupes']})")
   print(f"unknown serials:    {stats['unknownSerial']}  -> seed-report.txt")
   total = sum(stats['skipped'].values())
   print(f"records skipped:    {total}")
   for reason, count in sorted(stats['skipped'].items(), key=lambda kv: -kv[1])[:8]:
      print(f"   {reason:<24}{count}")

if __name__ == '__main__':
   main()

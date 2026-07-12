#!/usr/bin/env python3
"""Verify the seeded cheat files: grammar check everything, alias sanity, then round-trip a
random sample of cheats back to the Artemis corpus and confirm the converted ops match.

Usage:
  python verify-seed.py --codes <artemis-dir> [--out <repo-root>] [--curated <file>...] [--sample 300] [--seed 42]
"""

import argparse, random, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from seeder import parseNcl, parseFilename, parseCurated   # reuse the exact conversion logic
from collections import defaultdict

LINE_GRAMMAR = re.compile(
   r'^(# .+'
   r'|same-as: [A-Z]{4}\d{5}'
   r'|name: .+'
   r'|mode: (once|constant)'
   r'|w8 [0-9A-F]{8} [0-9A-F]{2}'
   r'|w16 [0-9A-F]{8} [0-9A-F]{4}'
   r'|w32 [0-9A-F]{8} [0-9A-F]{8}'
   r'|aob [0-9A-F]+ [0-9A-F]+'
   r'|)$'
)
SERIAL = re.compile(r'^[A-Z]{4}\d{5}$')

def main():
   ap = argparse.ArgumentParser()
   ap.add_argument('--codes', required=True)
   ap.add_argument('--out', default='.')
   ap.add_argument('--curated', nargs='*', default=[], help='curated source files (their cheats are not corpus-derived)')
   ap.add_argument('--sample', type=int, default=300)
   ap.add_argument('--seed', type=int, default=42)
   args = ap.parse_args()

   cheatsDir = Path(args.out) / 'source'
   problems = []

   # 1. every line grammar-valid, every filename a serial, every alias target a real primary
   flat = []   # (serial, name, opsTuple) from primary files, for sampling
   aliasTargets = {}
   primaries = set()
   for path in sorted(cheatsDir.glob('*.txt')):
      if not SERIAL.match(path.stem):
         problems.append(f'{path.name}: filename is not a serial')
         continue
      text = path.read_text(encoding='utf-8')
      for lineNo, line in enumerate(text.splitlines(), 1):
         if not LINE_GRAMMAR.match(line):
            problems.append(f'{path.name}:{lineNo}: {line[:70]}')
      aliasMatch = re.search(r'^same-as: (\w+)$', text, re.M)
      if aliasMatch:
         aliasTargets[path.stem] = aliasMatch.group(1)
         if 'name: ' in text:
            problems.append(f'{path.name}: alias file also contains cheats')
         continue
      primaries.add(path.stem)
      name, ops = None, []
      for line in text.splitlines() + ['name: _end']:
         if line.startswith('name: '):
            if name and ops:
               flat.append((path.stem, name, tuple(ops)))
            name, ops = line[6:], []
         elif line.split(' ', 1)[0] in ('w8', 'w16', 'w32', 'aob'):
            ops.append(line)
   for alias, target in aliasTargets.items():
      if target not in primaries:
         problems.append(f'{alias}.txt: same-as target {target} is not a primary file')
   print(f'files: {len(primaries)} primaries, {len(aliasTargets)} aliases; grammar/alias check: {"OK" if not problems else f"{len(problems)} problems"}')
   for p in problems[:10]:
      print(f'   {p}')

   # 2. round-trip: seeded cheats must exist verbatim in a fresh conversion of the corpus
   #    (merged collection/localization cheats may carry a "Sub Game: " name prefix)
   sourceSet = set()
   stats = {'skipped': defaultdict(int)}
   for path in sorted(Path(args.codes).glob('*.ncl')):
      _, ids = parseFilename(path.stem)
      if not ids:
         continue
      for name, ops in parseNcl(path, stats):
         sourceSet.add((name.strip().lower(), tuple(ops)))
   curatedSet = set()
   for cur in args.curated:
      _, cheats = parseCurated(Path(cur))
      for name, ops in cheats:
         curatedSet.add((name.strip().lower(), tuple(ops)))

   random.seed(args.seed)
   sample = random.sample(flat, min(args.sample, len(flat)))
   mismatches, curatedSkipped = [], 0
   for serial, name, ops in sample:
      candidates = [name.lower()]
      if ': ' in name:
         candidates.append(name.split(': ', 1)[1].lower())
      if any((c, ops) in curatedSet for c in candidates):
         curatedSkipped += 1
         continue
      if not any((c, ops) in sourceSet for c in candidates):
         mismatches.append(f'{serial}: "{name}" ops not found in re-converted corpus')
   checked = len(sample) - curatedSkipped
   print(f'round-trip: {checked} sampled, {len(mismatches)} mismatches, {curatedSkipped} curated-only skipped')
   for m in mismatches[:10]:
      print(f'   {m}')

   sys.exit(1 if (problems or mismatches) else 0)

if __name__ == '__main__':
   main()

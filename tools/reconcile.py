#!/usr/bin/env python3
"""Fold new votes into the running tally kept IN the compiled files, then prune the votes.

The tally is machine-owned, so it lives in compiled/ (never source/, which contributors edit via
PRs). A game's compiled/<primary>.txt holds every variant's tally in place; the other variant
files are copies of it. There is no separate aggregate store.

Each run:
  1. read the votes currently in votes/ (a drain queue)
  2. for each affected game, read the tally from compiled/<primary>, add the new votes, rewrite the
     score lines + working-val in place, copy the result to every variant compiled file
  3. delete the folded vote files (git history still has them)
So reconcile only touches games that got votes and reads each once - O(new votes), not O(all votes
ever). Contributors can never edit scores; the source files carry no tally at all.

In compiled the tally is stored verbatim on each cheat:
  score <titleId> <version>: <score> <worked>+ <failed>-      (worked/failed = the running counts)
  w32 <addr> <val> working-val=<orig>:<count>,...             (per-original agreement counts)
The console reads the score and, for a w32 cheat, matches live memory against the working-val
originals (count = how many consoles confirmed that original).

Usage:
  python reconcile.py [--out <repo-root>] [--keep-votes]
"""

import argparse, re, sys
from pathlib import Path
from collections import defaultdict, Counter

CLAMP = 100000          # cap any single count (anonymous-flood guard)
MAX_WV_DISTINCT = 64    # cap distinct originals tracked per line (stops value-spam bloat)
OP = ('w8', 'w16', 'w32', 'aob')
SCORE_LINE = re.compile(r'^score ([A-Z]{4}\d{5}) (\d{2}\.\d{2}): \d+ (\d+)\+ (\d+)-\s*$')
WV = re.compile(r'\s+working-val=(\S+)')
PAYLOAD_LINE = re.compile(r'^(\d+)=([0-9A-Fa-f]{2,8})$')

def fnv1a32(text):
   h = 2166136261
   for b in text.encode('utf-8'):
      h = ((h ^ b) * 16777619) & 0xFFFFFFFF
   return f'{h:08x}'

def opsHash(ops):
   return fnv1a32('\n'.join(WV.sub('', op) for op in ops))

def confidence(worked, failed):
   return round(100 * (2 * worked + 1) / (2 * worked + 2 * failed + 2))

def blockOps(block):
   return [l for l in block if l.split(' ', 1)[0] in OP]

def main():
   ap = argparse.ArgumentParser()
   ap.add_argument('--out', default='.')
   ap.add_argument('--keep-votes', action='store_true', help='do not prune folded votes (debug)')
   args = ap.parse_args()

   outDir = Path(args.out)
   sourceDir, compiledDir, votesDir = outDir / 'source', outDir / 'compiled', outDir / 'votes'

   # cheatHash -> primary stem, and primary -> [variant serials], both from source
   hashToPrimary, variants = {}, defaultdict(list)
   for path in sourceDir.glob('*.txt'):
      text = path.read_text(encoding='utf-8')
      alias = re.search(r'^same-as: (\w+)$', text, re.M)
      variants[alias.group(1) if alias else path.stem].append(path.stem)
      if alias:
         continue
      name, ops = None, []
      for line in text.splitlines() + ['name: _end']:
         if line.startswith('name: '):
            if name and ops:
               hashToPrimary[opsHash(ops)] = path.stem
            name, ops = line[6:], []
         elif line.split(' ', 1)[0] in OP:
            ops.append(line)

   # group present votes by the primary that owns each cheat
   byPrimary = defaultdict(list)
   voteFiles, unmatched = [], 0
   for path in votesDir.rglob('*'):
      if not path.is_file() or path.name == '.gitkeep':
         continue
      parts = path.relative_to(votesDir).parts
      if len(parts) != 4:
         continue
      titleId, version, cheatHash = parts[0], parts[1], parts[2]
      event = path.name.split('-')[0]
      if event not in ('CHEAT_WORKED', 'CHEAT_FAILED'):
         continue
      voteFiles.append(path)
      primary = hashToPrimary.get(cheatHash) or aliasPrimary(sourceDir, titleId)
      if primary is None:
         unmatched += 1
         continue
      pairs = []
      if event == 'CHEAT_WORKED':
         for bl in path.read_text(encoding='utf-8').splitlines():
            m = PAYLOAD_LINE.match(bl.strip())
            if m:
               pairs.append((int(m.group(1)), m.group(2).upper()))
      byPrimary[primary].append((titleId, version, cheatHash, event, pairs))

   for primary, votes in byPrimary.items():
      primaryFile = compiledDir / f'{primary}.txt'
      if not primaryFile.exists():
         unmatched += len(votes)
         continue
      content = foldVotes(primaryFile.read_text(encoding='utf-8'), votes)
      for serial in variants[primary]:   # write primary + copy to every variant
         (compiledDir / f'{serial}.txt').write_text(content, encoding='utf-8', newline='\n')

   if not args.keep_votes:
      for path in voteFiles:
         path.unlink()
   print(f'folded {len(voteFiles)} votes into {len(byPrimary)} games' + (f', {unmatched} unmatched' if unmatched else ''))

def aliasPrimary(sourceDir, titleId):
   path = sourceDir / f'{titleId}.txt'
   if not path.exists():
      return None
   alias = re.search(r'^same-as: (\w+)$', path.read_text(encoding='utf-8'), re.M)
   return alias.group(1) if alias else titleId

def foldVotes(text, votes):
   """update the tally inside one compiled primary: parse each cheat's current counts, add the new
   votes for its hash, rewrite the score lines + working-val in place."""
   # gather new counts per cheatHash
   newScores = defaultdict(lambda: defaultdict(lambda: [0, 0]))   # hash -> (tid,ver) -> [w,f]
   newWv = defaultdict(lambda: defaultdict(Counter))              # hash -> opIdx -> Counter(value)
   for titleId, version, cheatHash, event, pairs in votes:
      newScores[cheatHash][(titleId, version)][0 if event == 'CHEAT_WORKED' else 1] += 1
      for lineIdx, value in pairs:
         newWv[cheatHash][lineIdx][value] += 1

   out, block, name = [], [], None
   def flush():
      if name is None:
         return
      ops = blockOps(block)
      h = opsHash(ops) if ops else None
      # merge existing tally in this block with the new votes for its hash
      scores = defaultdict(lambda: [0, 0])
      wv = defaultdict(Counter)
      for l in block:
         m = SCORE_LINE.match(l)
         if m:
            scores[(m.group(1), m.group(2))] = [int(m.group(3)), int(m.group(4))]
      opIdx = 0
      for l in block:
         if l.split(' ', 1)[0] in OP:
            m = WV.search(l)
            if m:
               for tok in m.group(1).split(','):
                  v, _, c = tok.partition(':')
                  wv[opIdx][v] = int(c) if c else 1
            opIdx += 1
      for combo, c in newScores.get(h, {}).items():
         scores[combo][0] = min(scores[combo][0] + c[0], CLAMP)
         scores[combo][1] = min(scores[combo][1] + c[1], CLAMP)
      for idx, counter in newWv.get(h, {}).items():
         for v, n in counter.items():
            if v in wv[idx] or len(wv[idx]) < MAX_WV_DISTINCT:
               wv[idx][v] = min(wv[idx][v] + n, CLAMP)

      out.append(f'name: {name}')
      for (tid, ver), (w, f) in sorted(scores.items()):
         out.append(f'score {tid} {ver}: {confidence(w, f)} {w}+ {f}-')
      opIdx = 0
      for l in block:
         if SCORE_LINE.match(l):
            continue
         head = l.split(' ', 1)[0]
         if head in OP:
            base = WV.sub('', l)
            if head == 'w32' and wv.get(opIdx):
               base += ' working-val=' + ','.join(f'{v}:{n}' for v, n in sorted(wv[opIdx].items()))
            l = base
            opIdx += 1
         out.append(l)

   for line in text.splitlines():
      if line.startswith('name: '):
         flush()
         name, block = line[6:], []
      elif name is None:
         out.append(line)
      else:
         block.append(line)
   flush()
   return '\n'.join(out) + '\n'

if __name__ == '__main__':
   main()

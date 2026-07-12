#!/usr/bin/env python3
"""Materialize source/ into compiled/ - the machine-owned files consoles fetch.

source/<TITLEID>.txt is the human layer (user-editable via PRs): cheats only, no scores. A primary
file per game plus DO-NOT-EDIT alias files (same-as: <primary>) for the other serials.

compiled/<TITLEID>.txt is the console layer (machine-owned; PRs touching it are rejected). It is a
full standalone copy of the game's cheats WITH the running tally attached - score lines and
working-val - so consoles do one GET and the scores can't be edited by contributors.

The tally lives here, not in source. compile PRESERVES it: it re-emits each cheat's source content
and re-attaches the score/working-val lines that the existing compiled file already carried for
that cheat (matched by op hash). reconcile.py updates the tally; a cheat whose ops change loses its
old tally (correct - different cheat).

Usage:
  python compile.py [--out <repo-root>]
"""

import argparse, re
from pathlib import Path
from collections import defaultdict

OP = ('w8', 'w16', 'w32', 'aob')
SCORE_LINE = re.compile(r'^score [A-Z]{4}\d{5} \d{2}\.\d{2}: ')
WV = re.compile(r'\s+working-val=\S+')

def fnv1a32(text):
   h = 2166136261
   for b in text.encode('utf-8'):
      h = ((h ^ b) * 16777619) & 0xFFFFFFFF
   return f'{h:08x}'

def opsHash(ops):
   return fnv1a32('\n'.join(WV.sub('', op) for op in ops))

def parseCheats(text):
   """-> (headerLines, [ {name, body:[lines as written]} ]). body keeps mode + op lines verbatim."""
   header, cheats, cur = [], [], None
   for line in text.splitlines():
      if line.startswith('name: '):
         cur = {'name': line[6:], 'body': []}
         cheats.append(cur)
      elif cur is None:
         header.append(line)
      elif not SCORE_LINE.match(line):
         cur['body'].append(line)
   return header, cheats

def parseTally(text):
   """existing compiled -> {cheatHash: (scoreLines[], {opIdx: ' working-val=...'})}."""
   tally = {}
   name, block = None, []
   def flush():
      if name is None:
         return
      ops = [l for l in block if l.split(' ', 1)[0] in OP]
      if not ops:
         return
      scores = [l for l in block if SCORE_LINE.match(l)]
      wv, opIdx = {}, 0
      for l in block:
         if l.split(' ', 1)[0] in OP:
            m = WV.search(l)
            if m:
               wv[opIdx] = m.group(0)
            opIdx += 1
      tally[opsHash(ops)] = (scores, wv)
   for line in text.splitlines():
      if line.startswith('name: '):
         flush()
         name, block = line[6:], []
      elif name is not None:
         block.append(line)
   flush()
   return tally

def buildCompiled(sourceText, existingCompiled):
   """source cheats + preserved tally (score lines + working-val) from the existing compiled."""
   header, cheats = parseCheats(sourceText)
   tally = parseTally(existingCompiled) if existingCompiled else {}
   out = list(header)
   for cheat in cheats:
      ops = [l for l in cheat['body'] if l.split(' ', 1)[0] in OP]
      scores, wv = tally.get(opsHash(ops), ([], {}))
      out.append(f"name: {cheat['name']}")
      out.extend(scores)
      opIdx = 0
      for l in cheat['body']:
         head = l.split(' ', 1)[0]
         if head in OP:
            l = WV.sub('', l) + wv.get(opIdx, '')
            opIdx += 1
         out.append(l)
   return '\n'.join(out) + '\n'

def main():
   ap = argparse.ArgumentParser()
   ap.add_argument('--out', default='.')
   args = ap.parse_args()

   outDir = Path(args.out)
   sourceDir, compiledDir = outDir / 'source', outDir / 'compiled'
   compiledDir.mkdir(exist_ok=True)

   # variant map: primary stem -> [all serials of the game]
   variants = defaultdict(list)
   for path in sorted(sourceDir.glob('*.txt')):
      alias = re.search(r'^same-as: (\w+)$', path.read_text(encoding='utf-8'), re.M)
      variants[alias.group(1) if alias else path.stem].append(path.stem)

   sourceStems = {p.stem for p in sourceDir.glob('*.txt')}
   stale = 0
   for path in compiledDir.glob('*.txt'):
      if path.stem not in sourceStems:
         path.unlink()
         stale += 1

   written, unchanged, skippedEmpty = 0, 0, 0
   for primary, serials in variants.items():
      sourceText = (sourceDir / f'{primary}.txt').read_text(encoding='utf-8')
      if 'name: ' not in sourceText:
         skippedEmpty += 1   # empty game awaiting cheats
         continue
      existing = compiledDir / f'{primary}.txt'
      content = buildCompiled(sourceText, existing.read_text(encoding='utf-8') if existing.exists() else '')
      for serial in serials:   # every variant gets the same self-contained file
         out = compiledDir / f'{serial}.txt'
         if out.exists() and out.read_text(encoding='utf-8') == content:
            unchanged += 1
         else:
            out.write_text(content, encoding='utf-8', newline='\n')
            written += 1

   print(f'compiled: {written} written, {unchanged} unchanged, {skippedEmpty} empty skipped, {stale} stale removed')

if __name__ == '__main__':
   main()

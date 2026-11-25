# Notes: what I learned building this bot

Kept between rounds. After each round, write what you changed and why, and what you expect from the next
fights; when FIGHTS.md comes back, write whether it worked. Keep it short enough to read in a minute.

## Rule numbers my bot relies on

(round 1: write them here: ranges, damage, cooldowns, speeds, zone timing...)

## Baseline

- Victris's Python starter (the first strategy.py): beats `random` 100%, `ts-starter` about 30-45%, itself about 45%.
- Each fight opens with 0-8 ticks of random steps for both fighters, so every game differs: win rates move a few
  points between rounds by chance; trust differences of 10+ points out of 100.

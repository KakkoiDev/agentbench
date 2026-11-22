# Notes: what we learned building Arena bots

Kept between rounds and runs. Add what you learn, with the `./eval` numbers that show it; correct
what turns out wrong. Keep it short enough to read in a minute.

## Baseline

- Victris's Python starter (the first strategy.py): beats `random` 100%, `ts-starter` about 30-45%, itself about 45%.
- `./eval` games open with 0-8 ticks of random steps for both fighters (the maps alone give only 6 distinct starts), so
  each seed is a different game: win rates move a few points between runs; trust differences of 10+ points.

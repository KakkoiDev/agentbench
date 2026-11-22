# Build the strongest Victris Arena bot you can

Victris Arena is a deterministic, tick-based 1v1 fighting game for programmed bots (rules in
`docs/`). Your bot is `strategy.py`: its `decide(obs, match)` is called every tick (10 per second,
up to 600 ticks) and returns that tick's turn. Win by knocking the other fighter out, or by having
more HP when time runs out; stay inside the shrinking zone.

## Your goal

Make `strategy.py` win as many matches as possible against every opponent in the pool: Victris's
`random` and starter bots, and our earlier champion bots. A score is measured after you finish, on
seeds you don't see, against the same pool.

## How to work

1. Read `docs/rules.md` (every number) and `skills/arena-combat/SKILL.md`. `docs/bot-guide.md` is long (mostly
   the network protocol): look things up in it with grep rather than reading it all. Everything you need is
   in this folder; files outside it are not part of the task (most can't be read from here).
2. Run `./eval` to see where the current bot stands: wins per opponent, and how the losses went
   (who did the damage, with what, zone time, stuns, pickups).
3. Change `strategy.py`, run `./eval` again, keep what wins more. Every game starts with 0-8 ticks of random
   steps for both fighters, so each seed is a different game: a gain of a few points can be noise. `./eval --games 20` is quicker;
   `./eval --vs ts-starter` plays one opponent.
4. Two more tools:
   - `./replays` studies real ladder games from victrisarena.com, where the best bots are rated about 2700:
     `./replays scout` (each bot's habits: range kept, attacks per 100 ticks, accuracy, dodges, wall slams,
     pickups), `./replays show MATCH --fighter NAME --from 100 --to 140` (a match tick by tick from one
     fighter's view), `./replays mine` (strategy.py measured the same way, to compare with the best).
   - `./tune` searches the values of the constants between `# TUNE-BEGIN` and `# TUNE-END` in strategy.py
     (each named in `TUNE_RANGES` with its range) and writes back the best set only if it also wins on
     fresh seeds. Put every distance, threshold and weight your logic uses there, then run
     `./tune --minutes 8` once the logic is right. It only adjusts numbers: the ideas are yours.
5. Write what you learned in `NOTES.md` as you go: what worked, what did not, numbers from `./eval`.
   It is kept for the next round and the next run: write it for whoever continues after you.

## Rules for the bot

- Pure Python, standard library only; one file, `strategy.py`, with `decide(obs, match)`.
- Time: each call has a budget (`./eval` prints it: 200 ms in the benches, the ladder's 1 s in the Arena lab), and
  the whole match has the ladder's 300 s (`obs["self"]["budget_ms_left"]`). An over-budget call becomes an idle
  turn. In the lab, thinking time can win games: read `skills/arena-search/SKILL.md` before using it.
- No randomness that changes between runs unless seeded from `match["id"]`: matches are deterministic.
- An exception in `decide` costs an idle turn, not the match; still, `./eval` lists errors: fix them.
- Store anything you want to remember during a match in `match` (a dict kept for the whole match).

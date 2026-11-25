# Build a Victris Arena bot, then make it better fight after fight

Victris Arena is a deterministic, tick-based 1v1 fighting game for programmed bots (rules in `docs/`). Your bot is
`strategy.py`: its `decide(obs, match)` is called every tick (10 per second, up to 600 ticks) and returns that
tick's turn. Win by knocking the other fighter out, or by having more HP when time runs out; stay inside the
shrinking zone.

## How this works: 10 rounds of build, fight, learn

- **Round 1:** read the rules, then write your bot in `strategy.py` (it starts as Victris's simple starter bot:
  rewrite it or build on it).
- **After each round, your bot fights.** When you stop, the harness plays your `strategy.py` against three
  opponents: Victris's `random` bot, Victris's starter bot (`ts-starter`) and the Python starter (`py-starter`),
  on new seeds every round. You don't run these fights yourself.
- **Rounds 2 to 10:** the results are in `FIGHTS.md`: win rates, how every loss went (who did the damage and
  with what, zone time, stuns, pickups, errors), and a table of every round so far. Learn from it, improve
  `strategy.py`, and stop. Your files (`strategy.py`, `NOTES.md`, anything you wrote) carry over to the next round.

Each round also gets a hidden score, on seeds you never see, against the same three opponents. The goal is the
strongest bot by round 10: a change that loses more than before can be undone next round.

## How to work

1. Round 1: read `docs/rules.md` (every number) and `skills/arena-combat/SKILL.md`. `docs/bot-guide.md` is long
   (mostly the network protocol): grep it rather than reading it all. `skills/arena-search/` has an enemy
   tracker you may copy. Everything you need is in this folder; files outside it are not part of the task.
2. Later rounds: start from `FIGHTS.md`, `NOTES.md` and `strategy.py`, decide what to change, change it. Don't re-read
   the docs: round 1 writes the rule numbers the bot relies on into `NOTES.md`; grep a doc only to check one fact.
3. Write the bot early: a first working `strategy.py` within your first minutes of writing, then improve it. Don't
   build your own test games or dig into the simulator's code: the harness runs the fights after you stop.
   Put code into `strategy.py` with the write and edit tools as you go, one change at a time: code you only draft
   in your reasoning is lost when the round ends.
4. Run `./check` before you stop: it loads `strategy.py` and plays 10 quick games against `random`, to catch
   errors. It is not the fight: the fight happens after you stop.
5. Write in `NOTES.md` what you changed this round and why, and what you expect from the next fights, so the
   next round can tell whether it worked.

## Rules for the bot

- Pure Python, standard library only; one file, `strategy.py`, with `decide(obs, match)`.
- Each call has a 200 ms budget; an over-budget call becomes an idle turn.
- No randomness that changes between runs unless seeded from `match["id"]`: matches are deterministic.
- An exception in `decide` costs an idle turn, not the match; `FIGHTS.md` lists errors: fix them.
- Store anything you want to remember during a match in `match` (a dict kept for the whole match).

# Build a Victris Arena bot that beats every bot that ever won here

Victris Arena is a deterministic, tick-based 1v1 fighting game for programmed bots (rules in `docs/`). Your bot is
`strategy.py`: its `decide(obs, match)` is called every tick (10 per second, up to 600 ticks) and returns that
tick's turn. Win by knocking the other fighter out, or by having more HP when time runs out; stay inside the
shrinking zone.

## The goal

The opponents are our **hall of fame**: Victris's starter bot and every bot that won one of our benches or lab
sessions, written by other AI models. The strongest beat all the others. Bots can beat each other in a circle,
so the goal is a bot that beats **every one of them**, not just the strongest. The hidden score checks 55% and 70%
wins against each, on games you never see. Their code is not available to you: learn them from the fights.

## How this works: 10 rounds of build, test, fight, learn

- **Round 1:** read the rules, then write your bot in `strategy.py` (it starts as Victris's simple starter bot:
  rewrite it or build on it).
- **Test as you go, as much as you like.** `./fight` plays any bot in this folder against Victris's `random` and
  `ts-starter` bots, your earlier rounds' bots (`previous-bots/`) or sparring bots you write yourself, and shows
  how each loss went. Try an idea, fight it, keep it only if it wins more.
- **After each round, your bot fights the hall of fame.** When you stop, the harness plays your `strategy.py`
  against every hall-of-fame bot, 100 games each, and writes `FIGHTS.md`: win rates, how every loss went (who did
  the damage and with what, zone time, stuns, pickups, errors), and a table of every round so far.
- **Rounds 2 to 10:** start from `FIGHTS.md`, find what loses, improve, test, stop. Your files carry over.

There is no time pressure. Work as long as you need; stop when you believe the bot is the best you can make this
round.

## How to work

1. Round 1: read `docs/rules.md` (every number) and `skills/arena-combat/SKILL.md`. `docs/bot-guide.md` is long
   (mostly the network protocol): grep it rather than reading it all. `skills/arena-search/` has an enemy
   tracker you may copy. Everything you need is in this folder; files outside it are not part of the task.
2. Write the rule numbers your bot relies on into `NOTES.md`, so later rounds don't re-read the docs.
3. Put code into `strategy.py` with the write and edit tools as you go: code you only draft in your reasoning is
   lost when the round ends.
4. Test every change with `./fight` before you keep it. `./check` loads `strategy.py` and plays 10 quick games
   against `random`, to catch errors.
5. Write in `NOTES.md` what you changed, what `./fight` showed, and what you expect from the hall-of-fame fights,
   so the next round can tell whether it worked.

## Rules for the bot

- Pure Python, standard library only; one file, `strategy.py`, with `decide(obs, match)`.
- Each call has a 200 ms budget; an over-budget call becomes an idle turn.
- No randomness that changes between runs unless seeded from `match["id"]`: matches are deterministic.
- An exception in `decide` costs an idle turn, not the match; `FIGHTS.md` lists errors: fix them.
- Store anything you want to remember during a match in `match` (a dict kept for the whole match).

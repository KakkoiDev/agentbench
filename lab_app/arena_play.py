"""Play Victris Arena yourself, from the GLM Lab Arena page: you steer fighter 1 from the keyboard,
an opponent bot (Victris's random or starter bot, or one of our champions) plays fighter 2, on the
ladder's own match code (arena_sim). One Simulator per game, kept in this process, closed at the end
of the match or after 10 idle minutes.
"""
import sys, threading, time, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GAMES = {}
LOCK = threading.Lock()
IDLE_S = 600


class PlayError(ValueError):
    pass


def _reap():
    now = time.time()
    with LOCK:
        for gid in [g for g, s in GAMES.items() if now - s['last'] > IDLE_S]:
            _close(gid)


def _close(gid):
    s = GAMES.pop(gid, None)
    if s:
        try:
            s['sim'].close()
        except Exception:
            pass


def opponents():
    from arena import league
    pool = league.pool()
    return dict(opponents=[dict(name=n, kind='house' if n in league.HOUSE else 'champion') for n in pool])


def start(body):
    from arena_sim import Simulator
    from arena import league, runner
    _reap()
    pool = league.pool()
    name = body.get('opponent') or 'ts-starter'
    if name not in pool:
        raise PlayError(f'unknown opponent {name!r}; choose one of: {", ".join(pool)}')
    seed = int(body.get('seed') if body.get('seed') not in (None, '') else int(time.time()) % 100000)
    sim = Simulator(1)
    house = name in league.HOUSE
    hello, nxt = sim.start(0, seed=seed, opponent=name) if house else sim.start(0, seed=seed)
    s = dict(sim=sim, seed=seed, opponent=name, last=time.time(), lock=threading.Lock(), nxt=nxt, hello=hello[1])
    if not house:
        bot = runner.Bot(pool[name])
        s['bot'] = bot
        s['mod'], s['match'] = bot.new_match(hello[2])
    gid = uuid.uuid4().hex
    with LOCK:
        GAMES[gid] = s
    h = hello[1]
    return dict(game=gid, seed=seed, opponent=name, map=h.get('map'), map_name=h.get('map_name'), rules=h.get('rules'), pickups=h.get('pickups'),
                obs=nxt[1])


def _clean_turn(t):
    """Only the parts of a turn a person can send from the page."""
    if not isinstance(t, dict):
        return {}
    out = {}
    if isinstance(t.get('move'), str):
        out['move'] = t['move']
    elif isinstance(t.get('dash'), str):
        out['dash'] = t['dash']
    a = t.get('attack')
    if isinstance(a, dict) and a.get('name') in ('ranged', 'frostburst', 'repulsor'):
        out['attack'] = {'name': a['name']}
        tg = a.get('target')
        if isinstance(tg, list) and len(tg) == 2 and all(isinstance(v, int) for v in tg):
            out['attack']['target'] = tg
    return out


def step(body):
    gid = body.get('game')
    s = GAMES.get(gid)
    if not s:
        raise PlayError('that game has ended or expired; start a new one')
    with s['lock']:
        s['last'] = time.time()
        if s['nxt'][1].get('type') == 'end':
            return dict(obs=s['nxt'][1], end=True)
        replies = {1: _clean_turn(body.get('turn'))}
        if 'bot' in s:
            replies[2] = s['bot'].turn(s['mod'], s['nxt'][2], s['match'])
        s['nxt'] = s['sim'].step([replies])[0]
        obs = s['nxt'][1]
        end = obs.get('type') == 'end'
    if end:
        with LOCK:
            _close(gid)
        from arena import runner
        summary = runner._summary(obs, 1)
        obs = {k: v for k, v in obs.items() if k != 'stats'}
        return dict(obs=obs, end=True, summary=summary)
    return dict(obs=obs, end=False)


def stop(body):
    with LOCK:
        _close(body.get('game'))
    return dict(ok=True)


def league():
    """The league table, the Arena lab's sessions and every arena-v1 bench run, for the Arena page."""
    import json
    from arena import lab
    st = lab.status()
    runs = []
    for d in sorted((ROOT / 'results').glob('arena-v1-*')):
        meta = json.loads((d / 'meta.json').read_text()) if (d / 'meta.json').exists() else {}
        rounds = []
        for r in sorted(d.glob('round-*/result.json')):
            rec = json.loads(r.read_text())
            g = (r.parent / 'hidden.txt').read_text(errors='ignore') if (r.parent / 'hidden.txt').exists() else ''
            line = next((l for l in g.splitlines() if l.startswith('ARENA_JSON ')), None)
            rounds.append(dict(round=r.parent.name, passed=rec.get('hidden_passed'), total=rec.get('hidden_total'),
                               minutes=round(rec.get('wall_s', 0) / 60, 1), tool_calls=rec.get('tool_calls'), timed_out=rec.get('timed_out'),
                               win_rates=json.loads(line[11:])['win_rates'] if line else None))
        runs.append(dict(run=d.name, model=meta.get('model'), rounds=rounds))
    analysis = []
    for d in sorted((ROOT / 'results').glob('arena-analysis-v1-*')):
        meta = json.loads((d / 'meta.json').read_text()) if (d / 'meta.json').exists() else {}
        tasks = {r.parent.name: json.loads(r.read_text()) for r in d.glob('*/result.json')}
        diag = [t for n, t in tasks.items() if n.startswith('diagnose-')]
        read = [t for n, t in tasks.items() if n.startswith('ladder-')]
        named = []
        for n, t in tasks.items():
            h = d / n / 'hidden.txt'
            if n.startswith('diagnose-') and h.exists() and 'test_names_the_flaw ... ok' in h.read_text(errors='ignore'):
                named.append(n[len('diagnose-'):])
        analysis.append(dict(run=d.name, model=meta.get('model'), done=len(tasks), diagnosed=len(named), diagnosed_of=len(diag), named=named,
                             reading=sum(t.get('hidden_passed') or 0 for t in read), reading_of=sum(t.get('hidden_total') or 0 for t in read),
                             minutes=round(sum(t.get('wall_s', 0) for t in tasks.values()) / 60, 1)))
    prof = {}
    pf = ROOT / 'arena' / 'replays' / 'profiles.json'
    if pf.exists():
        prof = json.loads(pf.read_text())
        prof['bots'] = dict(list(prof['bots'].items())[:15])
    exp = ROOT / 'arena' / 'export' / 'lab-best'
    return dict(league=st['league'], lab=st['lab'], sessions=st['sessions'], runs=runs, ladder=prof, analysis=analysis,
                export=str(exp).replace(str(Path.home()), '~') if (exp / 'bot.py').exists() else None)


def export(body):
    from arena import export as ex, lab
    if not (lab.WS / 'strategy.py').exists():
        raise PlayError('the Arena lab has no bot yet: its first session has not run')
    d = ex.build()
    wins = ex.selftest(d)
    return dict(path=str(d).replace(str(Path.home()), '~'), selftest=f'{wins}/3 won against ts-starter through bot.py')

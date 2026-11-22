/* GLM lab: play Victris Arena yourself. You are fighter 1 (blue), steered from the keyboard and
   mouse; the bot you pick is fighter 2 (orange). Every tick goes to the lab server, which runs the
   ladder's own match code (arena_sim) and the bot. You see what a bot sees: the enemy only within
   sight (8 tiles). */
(function () {
  'use strict';
  var N = 21, DIRS = { '0,-1': 'N', '1,-1': 'NE', '1,0': 'E', '1,1': 'SE', '0,1': 'S', '-1,1': 'SW', '-1,0': 'W', '-1,-1': 'NW' };
  var BOARD = { floor: '#22252b', floor2: '#262a31', wall: '#5b6270', fog: 'rgba(8,9,12,0.55)', zoneOut: 'rgba(214,69,65,0.28)',
                me: '#4c8dff', foe: '#ff9a3c', danger: 'rgba(255,72,72,', frost: 'rgba(120,200,255,0.30)', health: '#3fcf8e', power: '#ffd23f',
                grid: 'rgba(255,255,255,0.04)', aim: 'rgba(255,255,255,0.65)', text: '#f2f2f2' };

  function el(tag, attrs, kids) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { if (attrs[k] == null) return; if (k === 'text') e.textContent = attrs[k]; else if (k.slice(0, 2) === 'on') e.addEventListener(k.slice(2), attrs[k]); else e.setAttribute(k, attrs[k]); });
    (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
    return e;
  }
  function cheb(a, b) { return Math.max(Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1])); }

  function pct(x) { return x == null ? '–' : Math.round(100 * x) + '%'; }
  function tbl(heads, rows) {
    return el('div', { class: 'table-wrap' }, [el('table', { class: 't' }, [el('thead', {}, [el('tr', {}, heads.map(function (x) { return el('th', { text: x }); }))]),
      el('tbody', {}, rows.map(function (r) { return el('tr', {}, r.map(function (c) { return el('td', { class: typeof c === 'number' ? 'num' : null, text: c == null ? '–' : String(c) }); })); }))])]);
  }
  function sec(title, hint, kids) {
    return el('section', { class: 'stack', style: 'margin-top:28px' }, [el('div', { class: 'sec-title' }, [el('h2', { text: title })]),
      hint ? el('p', { class: 'hint', style: 'margin:0', text: hint }) : null].concat(kids));
  }
  function leagueView(L, post, toast) {
    var lg = L.league || {}, ch = lg.champions || [], wrap = el('div');
    wrap.appendChild(sec('League: every generation', 'Every bench run\'s best bot and every lab session that improved is kept here and plays all the others (' +
      (lg.games || 60) + ' games a pair). Top Elo is the reigning champion' + (lg.updated ? '; table from ' + lg.updated : '') + '.',
      [ch.length ? tbl(['#', 'Bot', 'Elo', 'vs random', 'vs Victris starter', 'Built by', 'Added'], ch.map(function (c, i) {
        var m = c.meta || {};
        return [i + 1, c.name + (c.name === lg.reigning ? ' 👑' : ''), c.elo, pct((c.vs_house || {}).random), pct((c.vs_house || {})['ts-starter']),
                m.model || m.origin || '', (m.added || m.crowned || '').slice(0, 16)];
      })) : el('p', { class: 'muted', text: 'No table yet.' })]));
    var lab = L.lab || {}, ss = (L.sessions || []).slice().reverse();
    var by = lab.best_by || {};
    var expBtn = el('button', { type: 'button', class: 'btn', text: L.export ? 'Package again for the ladder' : 'Package for the ladder', onclick: function () {
      expBtn.disabled = true;
      post('/api/arena/export', {}).then(function (r) { expBtn.disabled = null; toast('Ladder bot ready in ' + r.path + ' (' + r.selftest + '). See its README.md.'); },
                                         function (e) { expBtn.disabled = null; toast(e.message, true); });
    } });
    wrap.appendChild(sec('Arena lab', 'One bot improved session after session to dominate every bot in the pool: it is scored on its worst matchup first, then the average. ' +
      'It keeps its notes between sessions whatever model runs them, and studies the ladder\'s best bots. ' +
      (lab.sessions ? lab.sessions + ' sessions so far; worst matchup ' + pct(lab.best_worst) + ', average ' + pct(lab.best) + '.' : 'No session yet.'),
      [Object.keys(by).length ? tbl(['Opponent', 'Lab bot wins'], Object.keys(by).map(function (k) { return [k, pct(by[k])]; })) : null,
       ss.length ? tbl(['When', 'Model', 'Minutes', 'Worst before', 'Worst after', 'Average after', 'Improved', 'Kept as'], ss.map(function (s) {
        return [s.when, s.model, s.minutes, pct(s.before_worst), pct(s.after_worst), pct(s.after), s.improved ? 'yes' : 'no', (s.league || {}).kept_as || ''];
      })) : null,
       el('div', { class: 'row', style: 'gap:10px;align-items:center;flex-wrap:wrap' }, [expBtn,
         el('span', { class: 'hint', text: L.export ? 'Last package: ' + L.export + ' (run.sh, with your bot token)' : 'Makes a folder with the lab\'s best bot and Victris\'s client, ready to play on victrisarena.com.' })])].filter(Boolean)));
    var an = L.analysis || [];
    wrap.appendChild(sec('Bench: arena-analysis-v1', 'Strategic understanding: 4 bots each with one hidden flaw (code hidden; the model can only watch and measure them) and 6 questions on real ladder games.',
      an.length ? [tbl(['Model', 'Flaws named', 'Which', 'Ladder questions right', 'Minutes'], an.map(function (r) {
        return [r.model || r.run, r.diagnosed + '/' + r.diagnosed_of, r.named.join(', '), r.reading + '/' + r.reading_of, r.minutes];
      }))] : [el('p', { class: 'muted', text: 'No run yet: queued on 11 models.' })]));
    var lad = L.ladder || {}, pb = lad.bots || {};
    wrap.appendChild(sec('Ladder games', Object.keys(pb).length ? 'Habits of the strongest bots on victrisarena.com, from ' + lad.matches + ' imported matches (' + lad.updated + '). The lab model reads the same report with ./replays.' :
      'No ladder games imported yet.', Object.keys(pb).length ? [tbl(['Bot', 'Rating', 'Matches', 'Won', 'Range kept', 'Shots / 100 ticks', 'Shot accuracy', 'Dashes that dodge', 'Wall slams in damage'], Object.keys(pb).map(function (n) {
        var p = pb[n];
        return [n, p.rating, p.matches, pct(p.win_rate), p.mean_range == null ? null : p.mean_range.toFixed(1), (p.shots_per100 || {}).ranged == null ? null : p.shots_per100.ranged.toFixed(1),
                pct(p.ranged_accuracy), pct(p.dodge_share), pct((p.dealt_mix || {}).wall_slam || 0)];
      }))] : []));
    var runs = L.runs || [];
    wrap.appendChild(sec('Bench: arena-v1', 'Each model builds a bot in 3 rounds (each continues from the last). Hidden tests: no errors, fast enough, and win-rate bars against random, Victris\'s starter and our first champion. The starter bot scores 3/10.',
      runs.length ? [tbl(['Model', 'Round 1', 'Round 2', 'Round 3', 'Best vs Victris starter'], runs.map(function (r) {
        var cells = [1, 2, 3].map(function (n) { var x = r.rounds.filter(function (q) { return q.round === 'round-' + n; })[0]; return x ? x.passed + '/' + x.total + (x.timed_out ? ' (time)' : '') : '–'; });
        var best = Math.max.apply(null, r.rounds.map(function (q) { return q.win_rates ? q.win_rates.ts_starter : -1; }).concat([-1]));
        return [r.model || r.run].concat(cells, [best < 0 ? '–' : pct(best)]);
      }))] : [el('p', { class: 'muted', text: 'No run yet: queued for the strongest coders.' })]));
    return wrap;
  }

  window.LabArena = function (main, post, header, toast) {
    return Promise.all([fetch('/api/arena/opponents').then(function (r) { return r.json(); }),
                        fetch('/api/arena/league').then(function (r) { return r.json(); })]).then(function (both) {
      var d = both[0], L = both[1];
      var g = null, obs = null, keys = {}, pending = null, paused = false, timer = null, hover = null, log = [], sending = false, lastSent = {};
      var opp = el('select', { class: 'input', id: 'arena-opp', 'aria-label': 'Opponent' }, d.opponents.map(function (o) {
        return el('option', { value: o.name, text: o.kind === 'house' ? 'Victris ' + o.name : 'Champion ' + o.name });
      }));
      opp.value = 'ts-starter';
      var speed = el('select', { class: 'input', 'aria-label': 'Speed' }, [['100', 'Normal (10 ticks/s, the real speed)'], ['200', 'Slow (5 ticks/s)'], ['333', 'Very slow (3 ticks/s)']].map(function (s) { return el('option', { value: s[0], text: s[1] }); }));
      var seed = el('input', { class: 'input', type: 'number', placeholder: 'random', style: 'width:110px', 'aria-label': 'Seed' });
      var startBtn = el('button', { type: 'button', class: 'btn btn-primary', text: 'Start a match', onclick: start });
      var pauseBtn = el('button', { type: 'button', class: 'btn', text: 'Pause', disabled: '', onclick: function () { togglePause(); } });
      var canvas = el('canvas', { width: 630, height: 630, tabindex: '0', 'aria-label': 'Arena board: WASD to move, click to shoot',
                                  style: 'width:100%;max-width:630px;aspect-ratio:1;display:block;border-radius:8px;outline-offset:3px;touch-action:none;cursor:crosshair' });
      var ctx = canvas.getContext('2d');
      var status = el('div', { class: 'hint', role: 'status', 'aria-live': 'polite', text: 'Pick an opponent and start a match.' });
      var side = el('div', { class: 'stack', style: 'min-width:230px;flex:1' });
      var help = el('div', { class: 'card', style: 'padding:12px 14px' }, [
        el('div', { class: 'eyebrow', text: 'controls' }),
        el('table', { class: 'hint', style: 'border-collapse:collapse;margin-top:6px' }, [
          ['WASD / arrows', 'move (two keys = diagonal)'], ['Shift + direction', 'dash 3 tiles (invulnerable on its tick)'],
          ['Left click / J', 'shoot at the tile (J: at the enemy)'], ['Right click / K', 'frostburst at the tile (K: at the enemy)'],
          ['Space / L', 'repulsor (hits and pushes everything next to you)'], ['P / Esc', 'pause']
        ].map(function (r) { return el('tr', {}, [el('td', { style: 'padding:2px 10px 2px 0;font-weight:600;white-space:nowrap', text: r[0] }), el('td', { text: r[1] })]); })),
        el('p', { class: 'hint', style: 'margin:8px 0 0', text: 'Stay in the zone (red tiles hurt, more the longer you stay). Green pads heal 32, yellow pads triple your damage for 5 s. Red squares are hits about to land.' })]);

      var top = el('div', { class: 'row', style: 'flex-wrap:wrap;gap:8px;align-items:center' }, [el('label', { class: 'hint', for: 'arena-opp', text: 'Opponent' }), opp, speed, el('span', { class: 'hint', text: 'Seed' }), seed, startBtn, pauseBtn]);
      var body = el('div', { style: 'display:flex;flex-wrap:wrap;gap:16px;align-items:flex-start' }, [el('div', { style: 'flex:2;min-width:280px;max-width:630px' }, [canvas]), side]);
      main.appendChild(header('Arena', 'Play Victris Arena yourself against a bot, on the ladder\'s own match code.'));
      [top, status, body, leagueView(L, post, toast)].forEach(function (x) { main.appendChild(x); });
      drawSide();
      drawBoard();

      function start() {
        stop();
        startBtn.disabled = true;
        post('/api/arena/start', { opponent: opp.value, seed: seed.value }).then(function (r) {
          g = r; obs = r.obs; log = []; pending = null; paused = false; lastSent = {};
          pauseBtn.disabled = null; pauseBtn.textContent = 'Pause'; startBtn.disabled = null; startBtn.textContent = 'Restart';
          status.textContent = 'Match on ' + (r.map_name || 'the arena') + ', seed ' + r.seed + ', against ' + r.opponent + '. Go!';
          canvas.focus(); drawBoard(); drawSide();
          timer = setInterval(tick, +speed.value);
        }, function (e) { startBtn.disabled = null; toast(e.message, true); });
      }
      function stop() {
        clearInterval(timer); timer = null;
        if (g && g.game) post('/api/arena/stop', { game: g.game }).catch(function () {});
      }
      function togglePause() {
        if (!g || !timer && !paused) return;
        paused = !paused; pauseBtn.textContent = paused ? 'Resume' : 'Pause';
        status.textContent = paused ? 'Paused. P or Resume to continue.' : 'Playing.';
        drawBoard();
      }

      function heldDir() {
        var dx = (keys.d || keys.arrowright ? 1 : 0) - (keys.a || keys.arrowleft ? 1 : 0);
        var dy = (keys.s || keys.arrowdown ? 1 : 0) - (keys.w || keys.arrowup ? 1 : 0);
        return DIRS[dx + ',' + dy] || null;
      }
      function enemy() { return obs && obs.enemies && obs.enemies[0]; }
      function buildTurn() {
        var me = obs.self, cd = me.cooldowns || {}, turn = {};
        if (me.state === 'idle') {
          var dir = heldDir();
          if (dir && keys.shift && !cd.dash && obs.dashes.some(function (x) { return x.dir === dir; })) turn.dash = dir;
          else if (dir && !cd.move && obs.moves.some(function (x) { return x.dir === dir; })) turn.move = dir;
          if (pending) {
            var tgt = pending.target === 'enemy' ? (enemy() ? enemy().pos : null) : pending.target;
            if (pending.name === 'repulsor') { if (!cd.repulsor) { turn.attack = { name: 'repulsor' }; pending = null; } }
            else if (!tgt) { pending = null; status.textContent = 'No enemy in sight to aim at.'; }
            else if (!cd[pending.name]) { turn.attack = { name: pending.name, target: tgt }; pending = null; }
            else if (Date.now() - pending.at > 1200) pending = null;   // not ready for over a second: drop it
          }
        }
        return turn;
      }
      function tick() {
        if (paused || sending || !g) return;
        sending = true;
        var turn = buildTurn(); lastSent = turn;
        post('/api/arena/step', { game: g.game, turn: turn }).then(function (r) {
          sending = false;
          if (r.end) return finish(r);
          obs = r.obs; note(obs.events || []); drawBoard(); drawSide();
        }, function (e) { sending = false; clearInterval(timer); timer = null; toast(e.message, true); });
      }
      function finish(r) {
        clearInterval(timer); timer = null; pauseBtn.disabled = '';
        var s = r.summary || {}, word = { win: 'You won!', loss: 'You lost.', draw: 'Draw.' }[r.obs.result] || r.obs.result;
        status.textContent = word + ' ' + (s.reason || '').replace(/_/g, ' ') + ' at tick ' + s.tick + '. HP ' + s.hp + ' vs ' + s.enemy_hp +
          '; you dealt ' + s.dealt + ' and took ' + s.taken + (s.zone_ticks ? ', ' + s.zone_ticks + ' ticks outside the zone' : '') + '.';
        g.ended = { word: word, s: s }; drawBoard(); drawSide(); startBtn.textContent = 'Play again'; startBtn.focus();
      }
      function note(evs) {
        evs.forEach(function (e) {
          var me = obs.self.id, who = function (f) { return f === me ? 'You' : 'Bot'; }, t = null;
          if (e.kind === 'hit') t = who(e.attacker) + ' hit ' + (e.target === me ? 'you' : 'the bot') + ' with ' + e.attack + ' (' + e.damage + ')';
          else if (e.kind === 'wall_slam') t = (e.target === me ? 'You were' : 'The bot was') + ' slammed into a wall (' + e.damage + ')';
          else if (e.kind === 'stunned') t = (e.fighter === me ? 'You are' : 'The bot is') + ' stunned for ' + e.ticks + ' ticks';
          else if (e.kind === 'pickup') t = who(e.fighter) + ' took ' + e.pickup;
          else if (e.kind === 'zone_shrink') t = 'The zone shrank to radius ' + e.radius;
          else if (e.kind === 'zone_damage' && e.fighter === me && obs.tick % 10 === 0) t = 'Outside the zone: -' + e.damage;
          else if (e.kind === 'death') t = (e.fighter === me ? 'You were' : 'The bot was') + ' knocked out';
          else if (e.kind === 'shout' && e.fighter !== me) t = 'Bot: “' + e.text + '”';
          else if (e.kind === 'rejected' && e.fighter === me && e.part !== 'move') t = 'Refused ' + e.part + ': ' + e.reason.replace(/_/g, ' ');
          if (t) { log.unshift('t' + obs.tick + '  ' + t); if (log.length > 9) log.pop(); }
        });
      }

      // ---------- drawing ----------
      function drawBoard() {
        var W = canvas.width, c = W / N;
        ctx.clearRect(0, 0, W, W);
        if (!g) {
          ctx.fillStyle = BOARD.floor; ctx.fillRect(0, 0, W, W);
          ctx.fillStyle = BOARD.text; ctx.font = '600 22px system-ui'; ctx.textAlign = 'center';
          ctx.fillText('Pick an opponent, then Start a match', W / 2, W / 2); return;
        }
        var me = obs.self, z = obs.zone || {}, map = g.map;
        for (var y = 0; y < N; y++) for (var x = 0; x < N; x++) {
          var wall = map[y][x] === '#';
          ctx.fillStyle = wall ? BOARD.wall : ((x + y) % 2 ? BOARD.floor : BOARD.floor2);
          ctx.fillRect(x * c, y * c, c, c);
          if (!wall && z.center && cheb([x, y], z.center) > z.radius) { ctx.fillStyle = BOARD.zoneOut; ctx.fillRect(x * c, y * c, c, c); }
        }
        if (z.next_radius != null && z.center) {
          ctx.setLineDash([6, 5]); ctx.strokeStyle = 'rgba(255,120,110,0.9)'; ctx.lineWidth = 2;
          var r = z.next_radius; ctx.strokeRect((z.center[0] - r) * c, (z.center[1] - r) * c, (2 * r + 1) * c, (2 * r + 1) * c); ctx.setLineDash([]);
        }
        (obs.areas || []).forEach(function (a) {
          ctx.fillStyle = BOARD.frost; var r = a.radius || 1;
          ctx.fillRect((a.pos[0] - r) * c, (a.pos[1] - r) * c, (2 * r + 1) * c, (2 * r + 1) * c);
        });
        (obs.danger || []).forEach(function (d) {
          var soon = Math.max(0, d.tick - obs.tick); ctx.fillStyle = BOARD.danger + Math.max(0.18, 0.6 - soon * 0.1) + ')';
          ctx.fillRect(d.tile[0] * c + 2, d.tile[1] * c + 2, c - 4, c - 4);
        });
        (obs.pickups || []).forEach(function (p) {
          var cx = (p.pos[0] + 0.5) * c, cy = (p.pos[1] + 0.5) * c, ready = !p.ready_in;
          ctx.globalAlpha = ready ? 1 : 0.3; ctx.fillStyle = p.kind === 'health' ? BOARD.health : BOARD.power;
          if (p.kind === 'health') { ctx.fillRect(cx - c * 0.28, cy - c * 0.09, c * 0.56, c * 0.18); ctx.fillRect(cx - c * 0.09, cy - c * 0.28, c * 0.18, c * 0.56); }
          else { ctx.beginPath(); ctx.moveTo(cx + c * 0.08, cy - c * 0.32); ctx.lineTo(cx - c * 0.2, cy + c * 0.04); ctx.lineTo(cx, cy + c * 0.04); ctx.lineTo(cx - c * 0.08, cy + c * 0.32); ctx.lineTo(cx + c * 0.2, cy - c * 0.04); ctx.lineTo(cx, cy - c * 0.04); ctx.closePath(); ctx.fill(); }
          ctx.globalAlpha = 1;
        });
        // fog beyond sight
        var sight = (g.rules.stats || {}).sight_radius || 8;
        ctx.fillStyle = BOARD.fog;
        for (y = 0; y < N; y++) for (x = 0; x < N; x++) if (cheb([x, y], me.pos) > sight) ctx.fillRect(x * c, y * c, c, c);
        (obs.projectiles || []).forEach(function (p) {
          var pos = p.pos; ctx.fillStyle = p.owner === me.id ? '#bcd4ff' : '#ffd0a8';
          ctx.beginPath(); ctx.arc((pos[0] + 0.5) * c, (pos[1] + 0.5) * c, c * 0.17, 0, 7); ctx.fill();
        });
        function fighter(pos, col, hp, max, label, f) {
          var cx = (pos[0] + 0.5) * c, cy = (pos[1] + 0.5) * c;
          ctx.fillStyle = col; ctx.strokeStyle = '#0d0f12'; ctx.lineWidth = 2;
          ctx.beginPath(); ctx.arc(cx, cy, c * 0.4, 0, 7); ctx.fill(); ctx.stroke();
          if (f && f.state === 'stunned') { ctx.strokeStyle = '#fff'; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.arc(cx, cy, c * 0.48, 0, 7); ctx.stroke(); ctx.setLineDash([]); }
          if (f && f.buffs && Object.keys(f.buffs).length) { ctx.strokeStyle = BOARD.power; ctx.lineWidth = 3; ctx.beginPath(); ctx.arc(cx, cy, c * 0.46, 0, 7); ctx.stroke(); }
          ctx.fillStyle = '#0d0f12'; ctx.font = '700 ' + Math.round(c * 0.36) + 'px system-ui'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
          ctx.fillText(label, cx, cy + 1);
          var w = c * 0.9; ctx.fillStyle = 'rgba(0,0,0,0.6)'; ctx.fillRect(cx - w / 2, cy - c * 0.62, w, 4);
          ctx.fillStyle = hp / max > 0.35 ? BOARD.health : '#ff5a52'; ctx.fillRect(cx - w / 2, cy - c * 0.62, w * Math.max(0, hp) / max, 4);
        }
        var e = enemy();
        if (e) fighter(e.pos, BOARD.foe, e.hp, me.max_hp, 'B', e);
        fighter(me.pos, BOARD.me, me.hp, me.max_hp, 'U', me);
        if (hover) { ctx.strokeStyle = BOARD.aim; ctx.lineWidth = 2; ctx.strokeRect(hover[0] * c + 1, hover[1] * c + 1, c - 2, c - 2); }
        if (paused || g.ended) {
          ctx.fillStyle = 'rgba(0,0,0,0.55)'; ctx.fillRect(0, 0, W, W);
          ctx.fillStyle = BOARD.text; ctx.textAlign = 'center'; ctx.font = '700 40px system-ui';
          ctx.fillText(g.ended ? g.ended.word : 'Paused', W / 2, W / 2 - 10);
          ctx.font = '500 18px system-ui'; ctx.fillText(g.ended ? 'Play again to start a new match' : 'Press P to resume', W / 2, W / 2 + 24);
        }
      }
      function meter(label, left, total) {
        var ready = !left, pct = ready ? 100 : Math.round(100 * (1 - left / total));
        return el('div', { style: 'display:grid;grid-template-columns:80px 1fr 44px;gap:8px;align-items:center' }, [
          el('span', { class: 'hint', text: label }),
          el('div', { style: 'height:8px;border-radius:4px;background:var(--sunk);overflow:hidden' }, [el('div', { style: 'height:100%;width:' + pct + '%;background:' + (ready ? 'var(--fg)' : 'var(--fg-2)') + ';border-radius:4px' })]),
          el('span', { class: 'hint mono', style: 'text-align:right', text: ready ? 'ready' : left })]);
      }
      function hpRow(name, hp, max, col) {
        return el('div', {}, [el('div', { class: 'row', style: 'justify-content:space-between' }, [el('strong', { text: name }), el('span', { class: 'mono', text: Math.max(0, hp) + ' / ' + max })]),
          el('div', { style: 'height:10px;border-radius:5px;background:var(--sunk);overflow:hidden;margin-top:4px' }, [el('div', { style: 'height:100%;width:' + Math.max(0, 100 * hp / max) + '%;background:' + col })])]);
      }
      function drawSide() {
        side.textContent = '';
        if (g && obs && obs.self) {
          var me = obs.self, e = enemy(), r = g.rules, cd = me.cooldowns || {};
          var z = obs.zone || {};
          side.appendChild(el('div', { class: 'card', style: 'padding:12px 14px;display:grid;gap:10px' }, [
            el('div', { class: 'row', style: 'justify-content:space-between' }, [el('span', { class: 'eyebrow', text: 'tick ' + obs.tick + ' / ' + r.max_ticks }),
              el('span', { class: 'hint', text: z.shrinks_in != null ? 'zone shrinks in ' + z.shrinks_in : 'zone radius ' + z.radius })]),
            hpRow('You', me.hp, me.max_hp, BOARD.me),
            e ? hpRow('Bot (' + g.opponent + ')', e.hp, me.max_hp, BOARD.foe) : el('div', { class: 'hint', text: 'Bot (' + g.opponent + '): out of sight' }),
            meter('Shoot', cd.ranged, r.attacks.ranged.cooldown), meter('Frostburst', cd.frostburst, r.attacks.frostburst.cooldown),
            meter('Repulsor', cd.repulsor, r.attacks.repulsor.cooldown), meter('Dash', cd.dash, r.dash.cooldown),
            me.buffs && me.buffs.power ? el('div', { class: 'hint', text: 'Power: triple damage for ' + me.buffs.power + ' more ticks' }) : null,
            pending ? el('div', { class: 'hint', text: 'Queued: ' + pending.name }) : null]));
          if (log.length) side.appendChild(el('div', { class: 'card', style: 'padding:10px 14px' }, [el('div', { class: 'eyebrow', text: 'events' }),
            el('div', { class: 'hint mono', style: 'white-space:pre-wrap;margin-top:4px', text: log.join('\n') })]));
        }
        side.appendChild(help);
      }

      // ---------- input ----------
      function tileAt(ev) {
        var rc = canvas.getBoundingClientRect();
        var x = Math.floor((ev.clientX - rc.left) / rc.width * N), y = Math.floor((ev.clientY - rc.top) / rc.height * N);
        return x >= 0 && y >= 0 && x < N && y < N ? [x, y] : null;
      }
      function queue(name, target) { if (!g || g.ended) return; pending = { name: name, target: target, at: Date.now() }; }
      canvas.addEventListener('mousemove', function (ev) { hover = tileAt(ev); if (!timer || paused) drawBoard(); });
      canvas.addEventListener('mouseleave', function () { hover = null; });
      canvas.addEventListener('mousedown', function (ev) { var t = tileAt(ev); canvas.focus(); if (!t) return; ev.preventDefault(); queue(ev.button === 2 ? 'frostburst' : 'ranged', t); });
      canvas.addEventListener('contextmenu', function (ev) { ev.preventDefault(); });
      var GAME_KEYS = ['w', 'a', 's', 'd', 'arrowup', 'arrowdown', 'arrowleft', 'arrowright', ' ', 'j', 'k', 'l', 'p', 'escape', 'shift'];
      function onKey(ev) {
        if (!g || !document.body.contains(canvas)) return;
        var k = ev.key.toLowerCase(), t = ev.target;
        if (t && (t.tagName === 'INPUT' || t.tagName === 'SELECT') ) return;
        if (GAME_KEYS.indexOf(k) < 0) return;
        ev.preventDefault();
        if (ev.type === 'keyup') { keys[k] = false; if (k === 'shift') keys.shift = false; return; }
        if (k === 'shift') { keys.shift = true; return; }
        keys.shift = ev.shiftKey;
        if (ev.repeat && 'jklp '.indexOf(k) >= 0) return;
        if (k === 'p' || k === 'escape') return togglePause();
        if (k === 'j') return queue('ranged', 'enemy');
        if (k === 'k') return queue('frostburst', 'enemy');
        if (k === ' ' || k === 'l') return queue('repulsor', null);
        keys[k] = true;
      }
      document.addEventListener('keydown', onKey);
      document.addEventListener('keyup', onKey);
      window.addEventListener('blur', function () { keys = {}; });
      // leaving the Arena page ends the match
      window.addEventListener('hashchange', function leave() {
        if ((location.hash || '').slice(1).split('/')[0] === 'arena') return;
        stop(); document.removeEventListener('keydown', onKey); document.removeEventListener('keyup', onKey);
        window.removeEventListener('hashchange', leave);
      });
    });
  };
})();

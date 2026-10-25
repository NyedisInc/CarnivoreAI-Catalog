// clean your tv: the owner's panel. Every change to a TV starts here, as a click, through the plugin's routes (postMessage bridge).
(function () {
  'use strict';
  var nonce = null, nextId = 1, pending = {}, state = { tvs: [], plan: {} }, ui = { list: 'vh21', confirm: null, running: 0 }, form = {};
  var $ = function (id) { return document.getElementById(id); };

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function say(text, kind) { var m = $('msg'); m.textContent = text || ''; m.className = 'msg ' + (kind || ''); }

  window.addEventListener('message', function (e) {
    var d = e.data;
    if (!d || typeof d !== 'object') return;
    if (d.verb === 'init' && typeof d.nonce === 'string' && !nonce) { nonce = d.nonce; refresh(); return; }
    if (d.nonce !== nonce || !pending[d.id]) return;
    var cb = pending[d.id]; delete pending[d.id];
    cb(d.ok ? d.result : { ok: false, code: d.error || 'failed', message: d.error === 'rate_limited' ? 'too many clicks, wait a moment' : 'the plugin did not answer' });
  });

  function call(route, payload) {
    return new Promise(function (resolve) {
      var id = 'c' + nextId++;
      pending[id] = resolve;
      window.parent.postMessage({ verb: 'route.call', nonce: nonce, id: id, route: route, payload: payload || {} }, '*');
    });
  }

  // run one step; a slow one comes back as a job that is polled until it is done
  async function act(route, payload, okText) {
    ui.running++; draw();
    say(route + ' ...');
    var r = await call(route, payload);
    while (r && r.ok && r.job && r.running) {
      await new Promise(function (res) { setTimeout(res, 2000); });
      var j = await call('job', { id: r.job });
      if (!j || !j.ok) { r = j; break; }
      if (j.job.state === 'done') { r = j.job.result.body; break; }
    }
    ui.running--;
    if (r && r.ok) say(okText || (route + ' done'), 'ok'); else say((r && r.message) || 'failed', 'bad');
    ui.confirm = null;
    await refresh();
    return r;
  }

  async function refresh() {
    var r = await call('state');
    if (r && r.ok) { state = r; draw(); }
  }

  function badge(text, kind) { return el('span', 'pill ' + (kind || ''), text); }

  function draw() {
    var root = $('tvs');
    // keep what the owner typed or ticked across a redraw
    Array.prototype.forEach.call(root.querySelectorAll('input[data-k]'), function (i) { form[i.getAttribute('data-k')] = i.type === 'checkbox' ? i.checked : i.value; });
    root.textContent = '';
    state.tvs.forEach(function (tv) { root.appendChild(tvBlock(tv)); });
    if (!state.tvs.length) root.appendChild(el('p', 'note', 'no tvs yet.'));
  }

  function button(text, cls, fn, disabled) {
    var b = el('button', 'btn ' + (cls || ''), text);
    b.type = 'button';
    b.disabled = !!disabled || ui.running > 0;
    b.addEventListener('click', fn);
    return b;
  }

  function twoClick(key, text, cls, fn, disabled) {
    var armed = ui.confirm === key;
    return button(armed ? 'confirm: ' + text : text, armed ? 'danger' : cls, function () {
      if (ui.confirm === key) { fn(); } else { ui.confirm = key; draw(); }
    }, disabled);
  }

  function check(tv, key, text) {
    var l = el('label', 'chk'), i = el('input'); i.type = 'checkbox'; i.setAttribute('data-k', tv.id + key); i.checked = !!form[tv.id + key];
    l.appendChild(i); l.appendChild(el('span', '', text));
    return l;
  }
  function ticked(tv, key) { var i = document.querySelector('input[data-k="' + tv.id + key + '"]'); return !!(i && i.checked); }

  function tvBlock(tv) {
    var box = el('section', 'block tv'), head = el('div', 'head');
    head.appendChild(el('h3', '', tv.name));
    head.appendChild(el('span', 'dim', tv.ip + (tv.identity && tv.identity.model ? '  ' + tv.identity.model : '')));
    head.appendChild(badge(tv.adb_open ? 'adb open' : 'adb closed', tv.adb_open ? 'warn' : ''));
    if (tv.paired) head.appendChild(badge(tv.locked ? 'locked' : 'ready', tv.locked ? 'warn' : 'ok'));
    if (tv.checks) head.appendChild(badge(tv.checks.passed ? 'checks pass' : 'checks fail', tv.checks.passed ? 'ok' : 'bad'));
    box.appendChild(head);
    if (tv.locked && tv.lock_reason) box.appendChild(el('p', 'note', tv.lock_reason));

    var s = el('div', 'step'); s.appendChild(el('span', 'label', '1 pair'));
    s.appendChild(el('p', 'note', 'makes this tv its own key. the tv asks "allow usb debugging?": tap allow, not always allow.'));
    s.appendChild(button(tv.paired ? 're-pair' : 'pair', tv.paired ? '' : 'go', function () { act('pair', { tv: tv.id }, 'paired'); }));
    box.appendChild(s);

    if (tv.paired) { box.appendChild(batchStep(tv)); box.appendChild(rebootStep(tv)); box.appendChild(checksStep(tv)); box.appendChild(restoreStep(tv)); box.appendChild(wraithStep(tv)); }
    box.appendChild(closeStep(tv));
    return box;
  }

  function batchStep(tv) {
    var s = el('div', 'step'); s.appendChild(el('span', 'label', '2 batches'));
    var sel = el('select'); Object.keys(state.plan).forEach(function (k) { var o = el('option', '', k === 'vh21' ? 'sony vh21 (au list)' : 'sony vh21 (eu list)'); o.value = k; o.selected = k === ui.list; sel.appendChild(o); });
    sel.addEventListener('change', function () { ui.list = sel.value; draw(); });
    s.appendChild(sel);
    var plan = state.plan[ui.list] || { batches: [], refused: [] };
    plan.batches.forEach(function (b) {
      var done = tv.applied.indexOf(b.id) >= 0, r = el('div', 'batch');
      var txt = el('div', 'pk'); txt.appendChild(el('span', 't', b.id.split(':')[1] + '. ' + b.title)); txt.appendChild(document.createTextNode('  ' + b.packages.join(' ')));
      r.appendChild(txt);
      r.appendChild(done ? badge('applied', 'ok') : button('apply', 'go', function () { act('batch', { tv: tv.id, batch: b.id }, 'batch applied; wait, reboot, then run the checks'); }, tv.locked));
      s.appendChild(r);
    });
    if (plan.refused.length) s.appendChild(el('p', 'note', 'never batched: ' + plan.refused.map(function (x) { return x.package + ' (' + x.reason + ')'; }).join('; ')));
    s.appendChild(el('p', 'note', 'disable only, never uninstall. the next batch unlocks when the post-reboot checks pass.'));
    return s;
  }

  function rebootStep(tv) {
    var s = el('div', 'step'); s.appendChild(el('span', 'label', '3 reboot'));
    var left = tv.settle_remaining;
    s.appendChild(el('p', 'note', left > 0 ? 'wait ' + left + ' s: disables persist lazily and a reboot too soon reverts them. the plugin runs sync, then reboots.' : 'settled. the plugin runs sync, then reboots.'));
    s.appendChild(button(left > 0 ? 'reboot (' + left + ' s)' : 'reboot', '', function () { act('reboot', { tv: tv.id }, 'rebooting; give it a minute, then run the checks'); }, left > 0));
    return s;
  }

  function checksStep(tv) {
    var s = el('div', 'step'); s.appendChild(el('span', 'label', '4 checks after reboot'));
    s.appendChild(button('run checks', 'go', function () { act('checks', { tv: tv.id }, 'checks finished'); }));
    if (tv.checks) {
      var t = el('table');
      tv.checks.results.forEach(function (c) { var tr = el('tr'); tr.appendChild(el('td', '', c.name)); tr.appendChild(el('td', c.status, c.status)); tr.appendChild(el('td', 'dim', c.detail || '')); t.appendChild(tr); });
      s.appendChild(t);
    }
    return s;
  }

  function restoreStep(tv) {
    var s = el('div', 'step'); s.appendChild(el('span', 'label', 'restore'));
    s.appendChild(el('p', 'note', tv.has_undo ? 'puts back exactly what this plugin changed; anything disabled before it started stays disabled. then reboot and run the checks.' : 'nothing to restore: this plugin has changed nothing on this tv.'));
    s.appendChild(twoClick('restore' + tv.id, 'restore', '', function () { act('restore', { tv: tv.id }, 'restored; reboot and run the checks'); }, !tv.has_undo));
    return s;
  }

  function wraithStep(tv) {
    var s = el('div', 'step'); s.appendChild(el('span', 'label', 'setupwraith (separate step)'));
    s.appendChild(el('p', 'note', 'disabling it breaks google cast. allowed only on ethernet with no saved wifi networks, or on wifi when no saved network belongs to it; otherwise forget and rejoin the network on the tv first.'));
    s.appendChild(check(tv, ':wraith', 'google cast will stop working on this tv'));
    s.appendChild(button('disable setupwraith', 'danger', function () { act('setupwraith', { tv: tv.id, cast_will_stop: ticked(tv, ':wraith') }, 'setupwraith disabled; reboot and run the checks'); }, tv.locked || tv.cast_dropped));
    return s;
  }

  function closeStep(tv) {
    var s = el('div', 'step'); s.appendChild(el('span', 'label', 'close out'));
    s.appendChild(el('p', 'note', 'in order: you revoke usb debugging authorizations on the tv, the plugin verifies it, you turn usb and network debugging off and reboot the tv, the plugin checks port 5555 is closed, then deletes this tv\'s key.'));
    s.appendChild(check(tv, ':rev', 'i revoked usb debugging authorizations on the tv'));
    s.appendChild(check(tv, ':off', 'usb and network debugging are off on the tv'));
    var go = button('close out', 'go', async function () {
      var r = await act('closeout', { tv: tv.id, revoked_on_tv: ticked(tv, ':rev'), debugging_off: ticked(tv, ':off') }, 'closeout step finished');
      if (r && r.ok) say(r.done ? 'closed out: adb is closed and the key is deleted' : r.steps.map(function (x) { return x.step + ': ' + x.detail; }).join(' | '), r.done ? 'ok' : 'bad');
    });
    s.appendChild(go);
    var co = tv.closeout || {};
    var t = el('table');
    [['revoked', 'revoke authorizations'], ['revoke_verified', 'revoke verified'], ['port_closed', 'port 5555 closed'], ['key_deleted', 'key deleted']].forEach(function (p) {
      var tr = el('tr'); tr.appendChild(el('td', '', p[1])); tr.appendChild(el('td', co[p[0]] ? 'pass' : 'dim', co[p[0]] ? 'done' : 'pending')); t.appendChild(tr);
    });
    s.appendChild(t);
    if (!tv.adb_open) s.appendChild(button('remove tv', '', function () { act('remove', { tv: tv.id }, 'removed'); }));
    return s;
  }

  // no <form>: the view frame is sandboxed without allow-forms, so a submit is silently dropped; a button click and Enter do the add
  function addTv() {
    act('add', { ip: $('ip').value, name: $('nm').value }, 'tv added; now pair it').then(function (r) { if (r && r.ok) { $('ip').value = ''; $('nm').value = ''; } });
  }
  $('addgo').addEventListener('click', addTv);
  [$('ip'), $('nm')].forEach(function (i) { i.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); addTv(); } }); });

  // the settle countdown ticks locally; state is re-read only after actions
  setInterval(function () {
    var changed = false;
    state.tvs.forEach(function (tv) { if (tv.settle_remaining > 0) { tv.settle_remaining--; changed = true; } });
    if (changed && ui.running === 0) draw();
  }, 1000);
})();

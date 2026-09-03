/* =====================================================================
 * EC2 VPS under $20 — pricing explorer
 *
 * The cost model here mirrors monthly_cost() in tools/build_pricing.py.
 * If you change one, change the other.
 * ===================================================================== */

(function () {
  'use strict';

  var app = document.getElementById('app');
  if (!app) return;

  var DATA = null;
  var REGION = null;
  var SORT = { key: 'total', dir: 1 };
  var SELECTED = null;

  var DEFAULTS = {
    region: 'us-east-1',
    budget: 20, hours: 730, ebs: 8, egress: 20, snapshot: 0,
    ipv4: true, route53: true, nat: false,
    arch: 'all', minvcpu: '0', minmem: '0',
    search: '', onlyfit: false, burstonly: false
  };

  var COLORS = {
    compute: '#0b6bcb', ipv4: '#ff9d2f', ebs: '#10794a',
    snapshots: '#7a5cd6', dns: '#c94f8a', egress: '#0f9bb0', nat: '#a4322a'
  };

  var LABELS = {
    compute: 'EC2 compute', ipv4: 'Public IPv4', ebs: 'EBS gp3 root volume',
    snapshots: 'EBS snapshots', dns: 'Route 53 hosted zone',
    egress: 'Data transfer out', nat: 'NAT Gateway'
  };

  var $ = function (id) { return document.getElementById(id); };

  /* ---------- element handles ---------- */

  var el = {
    region: $('region'), regionHint: $('region-hint'),
    budget: $('budget'), budgetOut: $('budget-out'),
    hours: $('hours'), hoursOut: $('hours-out'),
    ebs: $('ebs'), ebsOut: $('ebs-out'),
    egress: $('egress'), egressOut: $('egress-out'),
    snapshot: $('snapshot'), snapshotOut: $('snapshot-out'),
    ipv4: $('ipv4'), route53: $('route53'), nat: $('nat'),
    minvcpu: $('minvcpu'), minmem: $('minmem'), search: $('search'),
    onlyfit: $('onlyfit'), burstonly: $('burstonly'),
    archChips: $('arch-chips'), reset: $('reset'),
    assumption: $('assumption-line'),
    tbody: $('tbody'), table: $('table'),
    tableCount: $('table-count'), tableNote: $('table-note'),
    bdPick: $('bd-pick'), bdSpec: $('bd-spec'), bdLines: $('bd-lines'),
    bdVerdict: $('bd-verdict'), bdBar: $('bd-bar'), bdLegend: $('bd-legend'),
    bdStats: $('bd-stats'), bdRegions: $('bd-regions')
  };

  /* ---------- helpers ---------- */

  function money(n) {
    return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function rate(n, digits) {
    return '$' + n.toFixed(digits === undefined ? 4 : digits);
  }

  function mem(gib) {
    return gib < 1 ? (gib * 1024) + ' MiB' : gib + ' GiB';
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* ---------- current UI state ---------- */

  function state() {
    var arch = 'all';
    var pressed = el.archChips.querySelector('[aria-pressed="true"]');
    if (pressed) arch = pressed.getAttribute('data-arch');
    return {
      budget: +el.budget.value,
      hours: +el.hours.value,
      ebs: +el.ebs.value,
      egress: +el.egress.value,
      snapshot: +el.snapshot.value,
      ipv4: el.ipv4.checked,
      route53: el.route53.checked,
      nat: el.nat.checked,
      arch: arch,
      minvcpu: +el.minvcpu.value,
      minmem: +el.minmem.value,
      search: el.search.value.trim().toLowerCase(),
      onlyfit: el.onlyfit.checked,
      burstonly: el.burstonly.checked
    };
  }

  /* ---------- the cost model ---------- */

  function breakdown(hourly, rates, s) {
    var compute = hourly * s.hours;
    var ipv4 = s.ipv4 ? (rates.ipv4_in_use_hr || 0.005) * s.hours : 0;
    var ebs = (rates.ebs_gp3_gb_mo || 0.08) * s.ebs;
    var snapshots = (rates.snapshot_gb_mo || 0.05) * s.snapshot;
    var dns = s.route53 ? DATA.route53_hosted_zone_mo : 0;
    var billable = Math.max(0, s.egress - DATA.free_egress_gb);
    var egress = billable * (rates.dto_gb || 0.09);
    var nat = 0;
    if (s.nat) {
      nat = (rates.nat_hr || 0.045) * s.hours + (rates.nat_gb || 0.045) * s.egress;
    }
    var b = {
      compute: compute, ipv4: ipv4, ebs: ebs, snapshots: snapshots,
      dns: dns, egress: egress, nat: nat
    };
    b.total = compute + ipv4 + ebs + snapshots + dns + egress + nat;
    b.fixed = ipv4 + ebs + dns;
    return b;
  }

  /* ---------- rendering ---------- */

  function visibleRows() {
    var s = state();
    var out = [];
    for (var i = 0; i < REGION.instances.length; i++) {
      var inst = REGION.instances[i];
      if (s.arch !== 'all' && inst.a !== s.arch) continue;
      if (s.burstonly && !inst.b) continue;
      if (inst.v < s.minvcpu) continue;
      if (inst.m < s.minmem) continue;
      if (s.search && inst.t.toLowerCase().indexOf(s.search) === -1) continue;
      var b = breakdown(inst.h, REGION.rates, s);
      if (s.onlyfit && b.total >= s.budget) continue;
      out.push({ inst: inst, b: b });
    }
    var key = SORT.key, dir = SORT.dir;
    out.sort(function (x, y) {
      var a = (key in x.inst) ? x.inst[key] : x.b[key];
      var c = (key in y.inst) ? y.inst[key] : y.b[key];
      if (typeof a === 'string') return dir * a.localeCompare(c);
      if (a === c) return x.inst.t.localeCompare(y.inst.t);
      return dir * (a - c);
    });
    return { rows: out, s: s };
  }

  function renderTable() {
    var v = visibleRows(), rows = v.rows, s = v.s;

    if (!rows.length) {
      el.tbody.innerHTML = '<tr><td colspan="11" class="empty">' +
        'No instance types match these filters. Try widening the budget or clearing the name filter.' +
        '</td></tr>';
      el.tableCount.textContent = '0 of ' + REGION.instances.length;
      el.tableNote.textContent = '';
      return;
    }

    var fits = 0, html = '';
    for (var i = 0; i < rows.length; i++) {
      var inst = rows[i].inst, b = rows[i].b;
      var ok = b.total < s.budget;
      if (ok) fits++;
      var cls = [];
      if (!ok) cls.push('over');
      if (SELECTED === inst.t) cls.push('selected');

      html += '<tr' + (cls.length ? ' class="' + cls.join(' ') + '"' : '') +
        ' data-type="' + esc(inst.t) + '">' +
        '<td class="tl type">' + esc(inst.t) +
          (inst.b ? ' <span class="badge burst">burst</span>' : '') + '</td>' +
        '<td class="num">' + inst.v + '</td>' +
        '<td class="num">' + mem(inst.m) + '</td>' +
        '<td class="tl"><span class="badge ' + (inst.a === 'arm64' ? 'arm' : 'x86') + '">' +
          (inst.a === 'arm64' ? 'arm64' : 'x86_64') + '</span></td>' +
        '<td class="tl">' + esc(inst.n || '—') + '</td>' +
        '<td class="num">' + rate(inst.h) + '</td>' +
        '<td class="num">' + money(b.compute) + '</td>' +
        '<td class="num">' + money(b.fixed) + '</td>' +
        '<td class="total">' + money(b.total) + '</td>' +
        '<td><span class="badge ' + (ok ? 'pass">under' : 'fail">over') + '</span></td>' +
        '<td><button class="tag-pick" type="button" data-pick="' + esc(inst.t) + '">Price it</button></td>' +
        '</tr>';
    }

    el.tbody.innerHTML = html;
    el.tableCount.textContent = rows.length + ' shown · ' + fits + ' under ' + money(s.budget);
    el.tableNote.textContent =
      'Showing ' + rows.length + ' of ' + REGION.instances.length + ' instance types priced at or below ' +
      rate(DATA.max_hourly_included, 2) + '/hour in ' + REGION.name +
      '. Linux, shared tenancy, On-Demand list price, no reservation or Savings Plan applied.';
  }

  function renderBreakdown() {
    var s = state();
    var inst = null;
    for (var i = 0; i < REGION.instances.length; i++) {
      if (REGION.instances[i].t === SELECTED) { inst = REGION.instances[i]; break; }
    }
    if (!inst) {
      var v = visibleRows();
      inst = v.rows.length ? v.rows[0].inst : REGION.instances[0];
      SELECTED = inst ? inst.t : null;
    }
    if (!inst) return;

    var b = breakdown(inst.h, REGION.rates, s);

    el.bdPick.textContent = inst.t;
    el.bdSpec.innerHTML =
      inst.v + ' vCPU · ' + mem(inst.m) + ' · ' + esc(inst.a) +
      (inst.b ? ' · burstable' : '') +
      (inst.d ? ' · ' + esc(inst.d) : '') +
      '<br>' + esc(inst.f) + ' · ' + esc(inst.n) + ' · ' + esc(REGION.name);

    var order = ['compute', 'ipv4', 'ebs', 'snapshots', 'dns', 'egress', 'nat'];
    var notes = {
      compute: rate(inst.h) + '/hr × ' + s.hours + ' hr',
      ipv4: s.ipv4 ? rate(REGION.rates.ipv4_in_use_hr, 3) + '/hr × ' + s.hours + ' hr'
                   : 'disabled — IPv6-only instance',
      ebs: rate(REGION.rates.ebs_gp3_gb_mo, 4) + '/GB-mo × ' + s.ebs + ' GB',
      snapshots: s.snapshot ? rate(REGION.rates.snapshot_gb_mo, 3) + '/GB-mo × ' + s.snapshot + ' GB'
                            : 'no snapshots configured',
      dns: s.route53 ? 'one hosted zone, flat rate' : 'using external DNS instead',
      egress: s.egress <= DATA.free_egress_gb
        ? s.egress + ' GB — inside the ' + DATA.free_egress_gb + ' GB free allowance'
        : (s.egress - DATA.free_egress_gb) + ' GB billable × ' + rate(REGION.rates.dto_gb, 4) + '/GB',
      nat: s.nat ? rate(REGION.rates.nat_hr, 3) + '/hr × ' + s.hours + ' hr + ' +
                   rate(REGION.rates.nat_gb, 3) + '/GB × ' + s.egress + ' GB'
                 : 'not provisioned — correct for a public-subnet VPS'
    };

    var lines = '';
    for (var j = 0; j < order.length; j++) {
      var k = order[j];
      lines += '<li' + (b[k] === 0 ? ' class="zero"' : '') + '>' +
        '<span class="l-name">' + LABELS[k] + '<small>' + notes[k] + '</small></span>' +
        '<span class="l-val">' + money(b[k]) + '</span></li>';
    }
    lines += '<li class="l-total"><span class="l-name">Total per month</span>' +
      '<span class="l-val">' + money(b.total) + '</span></li>';
    el.bdLines.innerHTML = lines;

    var under = b.total < s.budget;
    el.bdVerdict.hidden = false;
    el.bdVerdict.className = 'verdict-banner ' + (under ? 'ok' : 'no');
    el.bdVerdict.innerHTML =
      '<span class="mark" aria-hidden="true"></span><span class="txt">' +
      (under
        ? 'Fits your ' + money(s.budget) + ' budget <span>with ' +
          money(s.budget - b.total) + ' of headroom — about ' + money(b.total / 30.44) +
          ' a day</span>'
        : 'Over your ' + money(s.budget) + ' budget <span>by ' + money(b.total - s.budget) +
          '. Try a smaller instance, fewer hours, or a cheaper region.</span>') +
      '</span>';

    // composition bar
    var bar = '', legend = '';
    for (var m = 0; m < order.length; m++) {
      var kk = order[m];
      if (b[kk] <= 0) continue;
      var pct = (b[kk] / b.total) * 100;
      bar += '<div style="width:' + pct.toFixed(2) + '%;background:' + COLORS[kk] + '" ' +
             'title="' + LABELS[kk] + ': ' + money(b[kk]) + '"></div>';
      legend += '<span><i style="background:' + COLORS[kk] + '"></i>' + LABELS[kk] +
                ' ' + pct.toFixed(0) + '%</span>';
    }
    el.bdBar.innerHTML = bar;
    el.bdLegend.innerHTML = legend;

    // headline stats
    var overheadShare = b.total > 0 ? (b.fixed / b.total) * 100 : 0;
    el.bdStats.innerHTML =
      '<div class="stat"><div class="k">Per year</div><div class="v">' +
        money(b.total * 12) + '</div><div class="n">at these settings</div></div>' +
      '<div class="stat"><div class="k">Per day</div><div class="v">' +
        money(b.total / 30.44) + '</div><div class="n">over a 30.44-day month</div></div>' +
      '<div class="stat"><div class="k">Fixed overhead</div><div class="v">' +
        overheadShare.toFixed(0) + '%</div><div class="n">IPv4 + EBS + DNS share</div></div>';

    // same instance type across regions, cheapest first
    renderRegionCompare(inst.t, b.total, s);
  }

  function renderRegionCompare(type, currentTotal, s) {
    var rows = [];
    for (var i = 0; i < DATA.regions.length; i++) {
      var reg = DATA.regions[i];
      var match = null;
      for (var j = 0; j < reg.instances.length; j++) {
        if (reg.instances[j].t === type) { match = reg.instances[j]; break; }
      }
      if (!match) continue;
      rows.push({
        reg: reg,
        hourly: match.h,
        total: breakdown(match.h, reg.rates, s).total
      });
    }
    rows.sort(function (a, c) { return a.total - c.total; });

    // Always show the cheapest few plus the selected region, even if it is not cheap.
    var show = rows.slice(0, 5);
    var hasCurrent = show.some(function (x) { return x.reg.id === REGION.id; });
    if (!hasCurrent) {
      var cur = rows.filter(function (x) { return x.reg.id === REGION.id; })[0];
      if (cur) { show = show.slice(0, 4); show.push(cur); }
    }

    var html = '';
    for (var k = 0; k < show.length; k++) {
      var row = show[k];
      var here = row.reg.id === REGION.id;
      var delta = row.total - currentTotal;
      var same = Math.abs(delta) < 0.005;          // identical after rounding
      var deltaTxt = (here || same)
        ? 'same'
        : (delta < 0 ? '-' : '+') + money(Math.abs(delta));
      var deltaColor = (here || same)
        ? 'var(--text-faint)'
        : (delta < 0 ? 'var(--pass)' : 'var(--fail)');
      html += '<tr' + (here ? ' class="selected"' : '') + '>' +
        '<td class="tl">' + esc(row.reg.short) +
          (here ? ' <span class="badge arm">here</span>' : '') + '</td>' +
        '<td class="num">' + rate(row.hourly) + '</td>' +
        '<td class="total">' + money(row.total) + '</td>' +
        '<td class="num" style="color:' + deltaColor + '">' + deltaTxt + '</td>' +
        '</tr>';
    }
    if (!html) {
      html = '<tr><td colspan="4" class="empty">' + esc(type) +
             ' is not offered in the other regions in this dataset.</td></tr>';
    }
    el.bdRegions.querySelector('tbody').innerHTML = html;
  }

  function renderAssumptions() {
    var s = state();
    var bits = [
      s.hours + ' hr/month',
      s.ebs + ' GB gp3',
      s.ipv4 ? '1 public IPv4' : 'IPv6-only',
      s.route53 ? 'Route 53 zone' : 'external DNS',
      s.egress + ' GB egress'
    ];
    if (s.snapshot) bits.push(s.snapshot + ' GB snapshots');
    if (s.nat) bits.push('NAT Gateway ON');
    el.assumption.textContent = 'Pricing ' + bits.join(' · ') + ' in ' + REGION.name + '.';
  }

  function renderRegionHint() {
    var r = REGION.rates;
    el.regionHint.textContent =
      'IPv4 ' + rate(r.ipv4_in_use_hr, 3) + '/hr · gp3 ' + rate(r.ebs_gp3_gb_mo, 4) +
      '/GB-mo · egress ' + rate(r.dto_gb, 4) + '/GB · NAT ' + rate(r.nat_hr, 3) + '/hr';
  }

  function renderAll() {
    renderRegionHint();
    renderAssumptions();
    renderTable();
    renderBreakdown();
  }

  /* ---------- wiring ---------- */

  function syncOutputs() {
    el.budgetOut.textContent = '$' + el.budget.value;
    el.hoursOut.textContent = el.hours.value;
    el.ebsOut.textContent = el.ebs.value;
    el.egressOut.textContent = el.egress.value;
    el.snapshotOut.textContent = el.snapshot.value;
  }

  function onInput() { syncOutputs(); renderAll(); }

  function bind() {
    ['budget', 'hours', 'ebs', 'egress', 'snapshot'].forEach(function (k) {
      el[k].addEventListener('input', onInput);
    });
    ['ipv4', 'route53', 'nat', 'onlyfit', 'burstonly', 'minvcpu', 'minmem'].forEach(function (k) {
      el[k].addEventListener('change', onInput);
    });
    el.search.addEventListener('input', renderTable);

    el.region.addEventListener('change', function () {
      REGION = DATA.regions.filter(function (r) { return r.id === el.region.value; })[0];
      // Keep comparing the same instance type across regions when it exists there.
      var stillThere = REGION.instances.some(function (i) { return i.t === SELECTED; });
      if (!stillThere) SELECTED = null;
      renderAll();
    });

    el.archChips.addEventListener('click', function (e) {
      var btn = e.target.closest('[data-arch]');
      if (!btn) return;
      Array.prototype.forEach.call(el.archChips.querySelectorAll('[data-arch]'), function (b) {
        b.setAttribute('aria-pressed', String(b === btn));
      });
      renderAll();
    });

    el.table.querySelector('thead').addEventListener('click', function (e) {
      var th = e.target.closest('th.sortable');
      if (!th) return;
      var key = th.getAttribute('data-sort');
      // Text sorts ascending first; numbers descending feels wrong for cost, so
      // cost-like columns also start ascending (cheapest first).
      if (SORT.key === key) SORT.dir = -SORT.dir;
      else { SORT.key = key; SORT.dir = 1; }
      Array.prototype.forEach.call(el.table.querySelectorAll('th'), function (h) {
        h.removeAttribute('aria-sort');
      });
      // The indicator itself is pure CSS, keyed off aria-sort.
      th.setAttribute('aria-sort', SORT.dir === 1 ? 'ascending' : 'descending');
      renderTable();
    });

    el.tbody.addEventListener('click', function (e) {
      var row = e.target.closest('tr[data-type]');
      if (!row) return;
      SELECTED = row.getAttribute('data-type');
      renderTable();
      renderBreakdown();
      if (e.target.closest('[data-pick]')) {
        document.getElementById('bd-pick')
          .scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    });

    el.reset.addEventListener('click', function () {
      el.region.value = DEFAULTS.region;
      el.budget.value = DEFAULTS.budget;
      el.hours.value = DEFAULTS.hours;
      el.ebs.value = DEFAULTS.ebs;
      el.egress.value = DEFAULTS.egress;
      el.snapshot.value = DEFAULTS.snapshot;
      el.ipv4.checked = DEFAULTS.ipv4;
      el.route53.checked = DEFAULTS.route53;
      el.nat.checked = DEFAULTS.nat;
      el.minvcpu.value = DEFAULTS.minvcpu;
      el.minmem.value = DEFAULTS.minmem;
      el.search.value = DEFAULTS.search;
      el.onlyfit.checked = DEFAULTS.onlyfit;
      el.burstonly.checked = DEFAULTS.burstonly;
      Array.prototype.forEach.call(el.archChips.querySelectorAll('[data-arch]'), function (b) {
        b.setAttribute('aria-pressed', String(b.getAttribute('data-arch') === 'all'));
      });
      REGION = DATA.regions.filter(function (r) { return r.id === DEFAULTS.region; })[0];
      SORT = { key: 'total', dir: 1 };
      SELECTED = null;
      syncOutputs();
      renderAll();
    });
  }

  /* ---------- boot ---------- */

  function fail(msg) {
    el.tbody.innerHTML = '<tr><td colspan="11" class="empty">' + esc(msg) + '</td></tr>';
    el.bdSpec.textContent = msg;
  }

  fetch(app.getAttribute('data-pricing-url'), { cache: 'no-cache' })
    .then(function (res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    })
    .then(function (json) {
      DATA = json;

      // region picker, grouped by geography
      var groups = {};
      DATA.regions.forEach(function (r) {
        (groups[r.geo] = groups[r.geo] || []).push(r);
      });
      var html = '';
      Object.keys(groups).forEach(function (g) {
        html += '<optgroup label="' + esc(g) + '">';
        groups[g].forEach(function (r) {
          html += '<option value="' + esc(r.id) + '">' + esc(r.short) +
                  ' — ' + esc(r.id) + '</option>';
        });
        html += '</optgroup>';
      });
      el.region.innerHTML = html;
      el.region.value = DEFAULTS.region;

      REGION = DATA.regions.filter(function (r) { return r.id === DEFAULTS.region; })[0]
               || DATA.regions[0];

      bind();
      syncOutputs();
      renderAll();
    })
    .catch(function (err) {
      fail('Could not load pricing data (' + err.message +
           '). If you are running Jekyll locally, make sure tools/build_pricing.py has been run.');
    });
})();

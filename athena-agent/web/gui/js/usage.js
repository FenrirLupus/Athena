// ── Usage (the Operator's spec: renamed from Billing — same endpoint) ─────
async function loadUsage() {
  const prof = currentProfile ? currentProfile() : '';
  try {
    const res = await fetch('/billing?profile=' + encodeURIComponent(prof));
    const d = await res.json();
    const sum = d.summary || {};
    $('usage-summary').innerHTML =
      '<div class="billing-cards">' +
      '<div class="bill-card"><b>' + (sum.requests || 0) + '</b><span>requests</span></div>' +
      '<div class="bill-card"><b>' + (sum.tokens || 0) + '</b><span>tokens</span></div>' +
      '<div class="bill-card"><b>' + (sum.cost || '0.00') + '</b><span>cost</span></div>' +
      '</div>';
    const prov = d.providers || [];
    const phtml = prov.length
      ? '<table class="bill-table"><tr><th>provider</th><th>tokens</th><th>cost</th></tr>' +
        prov.map(p => '<tr><td>' + p.name + '</td><td>' + (p.tokens || 0) +
          '</td><td>' + (p.cost || '0.00') + '</td></tr>').join('') + '</table>'
      : '<p class="hint">No provider usage recorded yet.</p>';
    $('usage-providers').innerHTML = phtml;
    const sess = d.sessions || [];
    const shtml = sess.length
      ? '<table class="bill-table"><tr><th>session</th><th>tokens</th><th>cost</th></tr>' +
        sess.map(s => '<tr><td>' + s.id + '</td><td>' + (s.tokens || 0) +
          '</td><td>' + (s.cost || '0.00') + '</td></tr>').join('') + '</table>'
      : '<p class="hint">No session usage recorded yet.</p>';
    $('usage-sessions').innerHTML = shtml;
  } catch (e) { /* server mid-boot */ }
}

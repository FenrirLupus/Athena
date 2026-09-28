// ── Approvals (the GUI's interactive permission surface) ──────────────
let APPROVAL_ID = null;
async function pollApprovals() {
  try {
    const res = await fetch('/approvals/pending');
    const d = await res.json();
    const banner = $('approval-banner');
    if (d.count > 0 && d.pending.length) {
      const p = d.pending[0];
      APPROVAL_ID = p.id;
      // The Operator's spec: the message must show WHAT is being called,
      // the ARGUMENTS, and WHY it needs approval.
      const risk = p.risk || 'unsafe';
      const reason = p.reason || ('risk: ' + risk);
      let argsText = '';
      const args = p.arguments || {};
      if (Object.keys(args).length) {
        try {
          argsText = '<br>Arguments: <code>' +
            JSON.stringify(args).slice(0, 300) + '</code>';
        } catch (e) { argsText = ''; }
      }
      $('approval-text').innerHTML =
        'The agent wants to call <b>' + p.tool + '</b>.' + argsText +
        '<br><span class="approval-reason">' + reason + '</span>';
      banner.style.display = 'flex';
    } else {
      APPROVAL_ID = null;
      banner.style.display = 'none';
    }
  } catch (e) { /* ignore */ }
}
async function decideApproval(verdict) {
  if (!APPROVAL_ID) return;
  const scope = $('approval-scope').value || 'once';
  try {
    await fetch('/approvals/' + encodeURIComponent(APPROVAL_ID), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({verdict, scope}),
    });
  } catch (e) { /* ignore */ }
  APPROVAL_ID = null;
  pollApprovals();
}
$('approval-allow').addEventListener('click', () => decideApproval('allow'));
$('approval-deny').addEventListener('click', () => decideApproval('deny'));
$('approval-block').addEventListener('click', () => decideApproval('block'));
setInterval(pollApprovals, 2000);


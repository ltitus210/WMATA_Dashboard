(() => {
  const board = document.querySelector('#board');
  let state = window.WMATA_INITIAL_STATE;
  let signature = '';
  const escape = (value) => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const occupancyLabel = value => ({EMPTY:'○ Low',MANY_SEATS_AVAILABLE:'○ Low',FEW_SEATS_AVAILABLE:'◐ Medium',STANDING_ROOM_ONLY:'◐ Medium',CRUSHED_STANDING_ROOM_ONLY:'● High',FULL:'● Full',NOT_ACCEPTING_PASSENGERS:'× Closed'}[value] || '');
  const arrival = a => `<span class="arrival state-${escape(a.state)}" title="${escape(a.reason)}">${escape(a.display)}${a.occupancy ? `<small>${escape(occupancyLabel(a.occupancy))}</small>` : ''}</span>`;
  function render(next) {
    const nextSignature = JSON.stringify(next);
    if (nextSignature === signature) return;
    signature = nextSignature; state = next;
    const groups = state.profile.layout === 'card'
      ? Object.values(state.entries.reduce((all, e) => { const key=`${e.mode}:${e.location_id}`; (all[key] ||= {name:e.location_name,mode:e.mode,rows:[]}).rows.push(e); return all; }, {}))
      : state.entries.map(e => ({name:e.location_name,mode:e.mode,rows:[e]}));
    const content = groups.map(group => `<section class="arrival-card">
      <header><span class="mode-symbol">${group.mode === 'bus' ? 'B' : 'M'}</span><h2>${escape(group.name)}</h2></header>
      ${group.rows.map(e => `<div class="arrival-row"><div class="route-block"><strong class="route">${escape(e.route || (e.mode === 'rail' ? 'Rail' : 'Bus'))}</strong><div><p class="destination">${escape(e.destination || 'All destinations')}</p>${e.last ? `<p class="last">Last ${escape(e.last.display)}</p>` : e.mode === 'bus' ? '<p class="last unknown">Last —</p>' : ''}</div></div><div class="times">${e.arrivals.length ? e.arrivals.map(arrival).join('') : '<span class="no-service">No arrivals</span>'}</div></div>`).join('')}
    </section>`).join('');
    board.innerHTML = `<header class="board-header"><div><p class="eyebrow">LIVE DEPARTURES</p><h1>${escape(state.profile.name)}</h1></div><div class="clock"><strong>${new Date(state.server_time).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})}</strong><span>${new Date(state.server_time).toLocaleDateString([], {weekday:'short',month:'short',day:'numeric'})}</span></div></header>
      ${state.warning ? `<div class="warning">${escape(state.warning)}</div>` : ''}<div class="cards">${content || '<div class="empty-board"><strong>No arrivals configured</strong><span>Open /admin from another device to add a stop.</span></div>'}</div>
      <footer><span>Updated ${state.freshness_seconds}s ago</span>${state.profile.show_legend ? '<span><b>ˢ</b> scheduled · <b>?</b> stale live</span>' : ''}<span>WMATA data</span></footer>`;
  }
  render(state);
  async function refresh() {
    try { const response = await fetch(`/api/dashboard/${encodeURIComponent(state.profile.slug)}`, {cache:'no-store'}); if (response.ok) render(await response.json()); }
    catch (_) { /* keep the last good board visible */ }
  }
  window.setInterval(refresh, Math.max(10, state.refresh_interval) * 1000);
  if (state.profile.display_type !== 'lcd') {
    window.setTimeout(() => window.location.reload(), Math.max(300, state.profile.full_refresh_interval) * 1000);
  }
})();

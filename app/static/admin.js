document.querySelectorAll('.discover-box').forEach(box => {
  box.querySelector('.discover-button').addEventListener('click', async () => {
    const id = box.querySelector('.discover-id').value.trim();
    const output = box.querySelector('.discover-result');
    if (!id) return;
    output.textContent = 'Querying WMATA…';
    try {
      const response = await fetch(`/admin/discover/stop/${encodeURIComponent(id)}`);
      const data = await response.json();
      output.textContent = data.error ? data.error : `${data.stop.Name}\n${data.stop.Lat}, ${data.stop.Lon}\n\n` + data.variants.map(v => `${v.route} · dir ${v.direction} · ${v.destination}`).join('\n');
    } catch (error) { output.textContent = `Lookup failed: ${error}`; }
  });
});
document.querySelectorAll('.rail-discover').forEach(box => {
  box.querySelector('.stations-button').addEventListener('click', async () => {
    const output = box.querySelector('.stations-result'); output.textContent = 'Querying WMATA…';
    try {
      const response = await fetch('/admin/discover/stations'); const stations = await response.json();
      output.textContent = stations.error ? stations.error : stations.map(s => `${s.Code} · ${s.Name} · ${[s.LineCode1,s.LineCode2,s.LineCode3].filter(Boolean).join('/')}`).join('\n');
    } catch (error) { output.textContent = `Lookup failed: ${error}`; }
  });
});
document.querySelectorAll('.shutdown-form').forEach(form => {
  form.addEventListener('submit', event => {
    if (!window.confirm('Stop the WMATA dashboard server? All displays will go offline until it is started again.')) {
      event.preventDefault();
    }
  });
});
document.querySelectorAll('.purge-logs-form').forEach(form => {
  form.addEventListener('submit', event => {
    if (!window.confirm('Permanently delete the current WMATA dashboard log and all rotated backups?')) {
      event.preventDefault();
    }
  });
});

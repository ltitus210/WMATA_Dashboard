document.querySelectorAll('.discover-button').forEach(button => {
  const box = button.closest('.discover-box');
  button.addEventListener('click', async () => {
    const id = box.querySelector('.discover-id').value.trim();
    const output = box.querySelector('.discover-result');
    if (!id) {
      output.textContent = 'Enter a StopID first.';
      return;
    }
    output.textContent = 'Querying WMATA…';
    button.disabled = true;
    try {
      const response = await fetch(`/admin/discover/stop/${encodeURIComponent(id)}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      const variants = data.variants.length
        ? data.variants.map(v => `${v.route} · dir ${v.direction} · ${v.destination}`).join('\n')
        : 'No scheduled or currently predicted route variants were returned.';
      output.textContent = `${data.stop.Name}\n${data.stop.Lat}, ${data.stop.Lon}\n\n${variants}`;
    } catch (error) {
      output.textContent = `Lookup failed: ${error.message || error}`;
    } finally {
      button.disabled = false;
    }
  });
});
document.querySelectorAll('.rail-discover').forEach(box => {
  const button = box.querySelector('.stations-button');
  button.addEventListener('click', async () => {
    const output = box.querySelector('.stations-result'); output.textContent = 'Querying WMATA…';
    button.disabled = true;
    try {
      const response = await fetch('/admin/discover/stations');
      const stations = await response.json();
      if (!response.ok) throw new Error(stations.error || `HTTP ${response.status}`);
      if (!Array.isArray(stations)) throw new Error('WMATA returned an unexpected station response.');
      output.textContent = stations.map(s => `${s.Code} · ${s.Name} · ${[s.LineCode1,s.LineCode2,s.LineCode3].filter(Boolean).join('/')}`).join('\n');
    } catch (error) {
      output.textContent = `Lookup failed: ${error.message || error}`;
    } finally {
      button.disabled = false;
    }
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

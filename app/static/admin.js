document.querySelectorAll('.discover-button').forEach(button => {
  const box = button.closest('.discover-box');
  const form = box.closest('.add-entry').querySelector('.entry-form');
  const variantField = box.querySelector('.bus-variant-field');
  const variantSelect = box.querySelector('.bus-variant-select');
  const locationId = form.querySelector('.entry-location-id');
  const locationName = form.querySelector('.entry-location-name');
  const route = form.querySelector('.entry-route');
  const direction = form.querySelector('.entry-direction');
  const destination = form.querySelector('.entry-destination');

  function applyBusVariant(option) {
    if (!option || !option.dataset.route) return;
    locationId.value = option.dataset.stopId;
    locationName.value = option.dataset.stopName;
    route.value = option.dataset.route;
    direction.value = option.dataset.direction;
    destination.value = option.dataset.destination;
  }

  variantSelect.addEventListener('change', () => applyBusVariant(variantSelect.selectedOptions[0]));
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
      const data = await fetchJson(`/admin/discover/stop/${encodeURIComponent(id)}`);
      locationId.value = String(data.stop.StopID || id);
      locationName.value = data.stop.Name || '';
      variantSelect.innerHTML = '';
      if (!data.variants.length) {
        variantField.hidden = true;
        route.value = '';
        direction.value = '';
        destination.value = '';
        output.textContent = `${data.stop.Name} found, but WMATA returned no route variants.`;
        return;
      }
      data.variants.forEach((variant, index) => {
        const option = document.createElement('option');
        option.value = String(index);
        const directionLabel = variant.direction === '' ? 'Any direction' : `Direction ${variant.direction}`;
        const destinationLabel = variant.destination || 'Any destination';
        const availabilityLabel = variant.provisional ? ' · no current service details' : '';
        option.textContent = `${data.stop.Name} · ${variant.route} · ${directionLabel} → ${destinationLabel}${availabilityLabel}`;
        option.dataset.stopId = String(data.stop.StopID || id);
        option.dataset.stopName = data.stop.Name || '';
        option.dataset.route = variant.route;
        option.dataset.direction = variant.direction;
        option.dataset.destination = variant.destination;
        variantSelect.append(option);
      });
      variantField.hidden = false;
      applyBusVariant(variantSelect.options[0]);
      output.textContent = `${data.variants.length} service option${data.variants.length === 1 ? '' : 's'} loaded. Choose one above to fill the Metrobus fields.`;
    } catch (error) {
      variantField.hidden = true;
      output.textContent = `Lookup failed: ${error.message || error}`;
    } finally {
      button.disabled = false;
    }
  });
});
let stationCatalogPromise;

async function fetchJson(url) {
  const response = await fetch(url);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function stationLines(station) {
  return [station.LineCode1, station.LineCode2, station.LineCode3, station.LineCode4].filter(Boolean);
}

function normalizedDestination(value) {
  return String(value || '').toUpperCase().replace(/\bAVENUE\b|\bAV\b/g, 'AVE').replace(/[^A-Z0-9]+/g, ' ').trim();
}

function loadStationCatalog() {
  stationCatalogPromise ||= fetchJson('/admin/discover/stations').then(stations => {
    if (!Array.isArray(stations)) throw new Error('WMATA returned an unexpected station response.');
    return stations.sort((a, b) => a.Name.localeCompare(b.Name));
  });
  return stationCatalogPromise;
}

document.querySelectorAll('.entry-form').forEach(form => {
  const mode = form.querySelector('.entry-mode');
  const guided = form.querySelector('.rail-guided-fields');
  const stationSelect = form.querySelector('.rail-station-select');
  const variantSelect = form.querySelector('.rail-variant-select');
  const status = form.querySelector('.rail-options-status');
  const manualFields = form.querySelectorAll('.manual-entry-field');
  const locationId = form.querySelector('.entry-location-id');
  const locationName = form.querySelector('.entry-location-name');
  const route = form.querySelector('.entry-route');
  const direction = form.querySelector('.entry-direction');
  const destination = form.querySelector('.entry-destination');
  let stations = [];

  function applyVariant(option) {
    if (!option || !option.dataset.line) return;
    route.value = option.dataset.line;
    direction.value = option.dataset.group;
    destination.value = option.dataset.destination;
  }

  async function loadVariants(code, preserveCurrent) {
    variantSelect.disabled = true;
    variantSelect.innerHTML = '<option value="">Loading services…</option>';
    status.textContent = 'Loading cached routes and destinations…';
    try {
      const data = await fetchJson(`/admin/discover/station/${encodeURIComponent(code)}`);
      const variants = data.variants || [];
      variantSelect.innerHTML = '';
      if (!variants.length) {
        variantSelect.innerHTML = '<option value="">No service variants currently available</option>';
        route.value = (data.lines || []).join('/');
        direction.value = '';
        destination.value = '';
        status.textContent = 'Station selected. WMATA has not returned a destination variant yet.';
        return;
      }
      variants.forEach((variant, index) => {
        const option = document.createElement('option');
        option.value = String(index);
        option.textContent = `${variant.line} · Group ${variant.group} → ${variant.destination}`;
        option.dataset.line = variant.line;
        option.dataset.group = variant.group;
        option.dataset.destination = variant.destination;
        variantSelect.append(option);
      });
      let selected = variantSelect.options[0];
      if (preserveCurrent) {
        const configuredLines = new Set(String(variantSelect.dataset.currentRoute || route.value).toUpperCase().split(/[\s,;/]+/).filter(Boolean));
        const configuredGroup = String(variantSelect.dataset.currentDirection || direction.value);
        const configuredDestination = normalizedDestination(variantSelect.dataset.currentDestination || destination.value);
        selected = [...variantSelect.options].find(option =>
          (!configuredLines.size || configuredLines.has(option.dataset.line)) &&
          (!['1', '2'].includes(configuredGroup) || configuredGroup === option.dataset.group) &&
          (!configuredDestination || configuredDestination === normalizedDestination(option.dataset.destination))
        ) || selected;
      }
      selected.selected = true;
      applyVariant(selected);
      status.textContent = `${data.station.Name} (${data.station.Code}) · ${(data.lines || []).join('/')} · cached for 24 hours`;
    } catch (error) {
      variantSelect.innerHTML = '<option value="">Unable to load services</option>';
      status.textContent = `Rail lookup failed: ${error.message || error}`;
    } finally {
      variantSelect.disabled = false;
    }
  }

  async function enableRailMode() {
    guided.hidden = false;
    manualFields.forEach(field => { field.hidden = true; });
    stationSelect.disabled = true;
    status.textContent = 'Loading cached station catalog…';
    try {
      stations = await loadStationCatalog();
      const currentCode = (stationSelect.dataset.currentCode || locationId.value).toUpperCase();
      stationSelect.innerHTML = '<option value="">Choose a Metrorail station…</option>';
      stations.forEach(station => {
        const option = document.createElement('option');
        option.value = station.Code;
        option.textContent = `${station.Name} (${station.Code}) · ${stationLines(station).join('/')}`;
        stationSelect.append(option);
      });
      if (currentCode && stations.some(station => station.Code === currentCode)) {
        stationSelect.value = currentCode;
        await loadVariants(currentCode, true);
      } else {
        status.textContent = `${stations.length} stations loaded from the 24-hour cache.`;
      }
    } catch (error) {
      stationSelect.innerHTML = '<option value="">Unable to load stations</option>';
      status.textContent = `Station lookup failed: ${error.message || error}`;
    } finally {
      stationSelect.disabled = false;
    }
  }

  function updateMode() {
    if (mode.value === 'rail') {
      enableRailMode();
    } else {
      guided.hidden = true;
      manualFields.forEach(field => { field.hidden = false; });
    }
  }

  stationSelect.addEventListener('change', async () => {
    const station = stations.find(item => item.Code === stationSelect.value);
    if (!station) return;
    locationId.value = station.Code;
    locationName.value = station.Name;
    route.value = stationLines(station).join('/');
    direction.value = '';
    destination.value = '';
    await loadVariants(station.Code, false);
  });
  variantSelect.addEventListener('change', () => applyVariant(variantSelect.selectedOptions[0]));
  mode.addEventListener('change', updateMode);
  updateMode();
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

document.querySelectorAll('.widget-list').forEach(list => {
  const status = list.querySelector('.widget-order-status');
  let dragging = null;
  let changed = false;

  async function saveOrder() {
    if (!changed) return;
    const entryIds = [...list.querySelectorAll('.entry-widget')].map(card => Number(card.dataset.entryId));
    status.textContent = 'Saving widget order…';
    try {
      const response = await fetch(list.dataset.reorderUrl, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({entry_ids: entryIds}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      status.textContent = 'Widget order saved.';
      changed = false;
    } catch (error) {
      status.textContent = `Could not save widget order: ${error.message || error}`;
    }
  }

  list.querySelectorAll('.drag-handle').forEach(handle => {
    handle.addEventListener('dragstart', event => {
      dragging = handle.closest('.entry-widget');
      changed = false;
      dragging.classList.add('is-dragging');
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', dragging.dataset.entryId);
      if (event.dataTransfer.setDragImage) event.dataTransfer.setDragImage(dragging, 28, 24);
    });
    handle.addEventListener('dragend', async () => {
      if (dragging) dragging.classList.remove('is-dragging');
      dragging = null;
      await saveOrder();
    });
  });

  list.addEventListener('dragover', event => {
    if (!dragging) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    const target = event.target.closest('.entry-widget');
    if (!target || target === dragging) return;
    const bounds = target.getBoundingClientRect();
    const after = event.clientY > bounds.top + bounds.height / 2;
    list.insertBefore(dragging, after ? target.nextSibling : target);
    changed = true;
  });

  list.addEventListener('drop', event => event.preventDefault());
});

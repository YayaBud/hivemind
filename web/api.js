(() => {
  const sample = window.HM;
  const params = new URLSearchParams(location.search);
  const sourceTag = source => {
    const tag = document.getElementById('src-tag');
    if (tag) tag.textContent = source === 'offline' ? 'offline · sample data' : source === 'live' ? 'live data' : source === 'empty' ? 'live · empty' : 'sample data';
  };
  const validState = state => state && typeof state === 'object' &&
    ['AG', 'FID', 'MEMS', 'LOGS'].every(key => state[key] && typeof state[key] === 'object' && !Array.isArray(state[key]));
  const load = async () => {
    if (params.has('demo')) {
      window.HM_SOURCE = 'sample';
      sourceTag('sample');
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch('/api/state', { signal: controller.signal });
      if (!response.ok) throw new Error(`state request failed: ${response.status}`);
      const state = await response.json();
      if (!validState(state)) throw new Error('invalid state response');
      window.HM = { AG: state.AG, FID: state.FID, KIND: state.KIND || sample.KIND, MEMS: state.MEMS, LOGS: state.LOGS };
      const hasData = Object.keys(state.MEMS).length > 0 || Object.values(state.LOGS).some(logs => Array.isArray(logs) && logs.length);
      window.HM_SOURCE = hasData ? 'live' : 'empty';
      sourceTag(window.HM_SOURCE);
    } catch (error) {
      window.HM = sample;
      window.HM_SOURCE = 'offline';
      sourceTag('offline');
    } finally {
      clearTimeout(timer);
    }
  };
  window.HM_READY = load();
})();

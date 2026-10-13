/* Cassini Live mounts on AppAPI's embedded Nextcloud page. */
(() => {
  const host = document.getElementById('content');
  if (!host) return;
  const source = Array.from(document.scripts).find(s => s.src.includes('/ui/live.js'));
  const root = source ? new URL('../', source.src).href :
    window.location.origin + (window.OC?.generateUrl('/apps/app_api/proxy/live_transcription/') ||
    '/index.php/apps/app_api/proxy/live_transcription/');
  const mount = document.createElement('div');
  host.replaceChildren(mount);
  const shadow = mount.attachShadow({ mode: 'open' });
  shadow.innerHTML = `
    <link rel="stylesheet" href="${root}ui/live.css">
    <main>
      <header><span class="mark" aria-hidden="true"><img src="${root}img/app.svg" alt=""></span><span>CASSINI <b>LIVE</b></span>
        <span class="private">On your infrastructure</span></header>
      <section class="hero">
        <p class="eyebrow">KEEP UP WITH THE CONVERSATION</p>
        <h1>Every voice.<br>Every word.</h1>
        <p class="intro">Live captions for your Talk calls.<br>Small model. Seven languages. All yours.</p>
        <a class="button" id="open-talk">Open Talk <span aria-hidden="true">↗</span></a>
        <p class="hint">In a call, choose <strong>Show captions</strong> from the call menu.</p>
      </section>
      <section class="sample" aria-label="Example captions">
        <div class="sample-head"><span class="pulse" aria-hidden="true"></span> A conversation, made clearer
          <span class="sample-label">EXAMPLE</span></div>
        <div class="caption"><span class="avatar">A</span><div><b>Alex</b>
          <p>Let’s make sure everyone can follow along.</p></div></div>
        <div class="caption"><span class="avatar other">S</span><div><b>Sam</b>
          <p>Ja, so können alle dabei sein.</p></div></div>
        <p class="sample-note">Captions recognise the spoken language. They do not translate it.</p>
      </section>
      <section class="details" aria-label="About your caption service">
        <article><h2>Ready when you are</h2><p>No microphone setup, accounts or API keys.
          Turn captions on in Talk; your speakers’ names come from the call.</p></article>
        <article><h2>A little more understanding</h2><p>English · Deutsch · Français · Español ·
          Italiano · Nederlands · Polski</p><p>Automatic language detection.
          Choose a call language in Talk when you need to.</p></article>
        <article><h2>Your conversation stays yours</h2><p>Whistle runs on your server’s CPU.
          Cassini Live keeps no audio files or transcript history.</p></article>
      </section>
      <section class="capacity" aria-label="Measured caption capacity" aria-live="polite">
        <h2 id="capacity-title">Checking server capacity…</h2>
        <p id="capacity-summary">Loading the installation check.</p>
        <p class="hint" id="capacity-detail"></p>
      </section>
      <footer><div><span id="state" role="status" aria-live="polite">Checking caption service…</span>
        <p id="explanation">Loading service status.</p></div><span>Powered by Whistle</span></footer>
    </main>`;
  shadow.getElementById('open-talk').href = window.OC?.generateUrl('/apps/spreed/') || '/index.php/apps/spreed/';
  const state = shadow.getElementById('state');
  const explanation = shadow.getElementById('explanation');
  const capacityTitle = shadow.getElementById('capacity-title');
  const capacitySummary = shadow.getElementById('capacity-summary');
  const capacityDetail = shadow.getElementById('capacity-detail');
  function showCapacity(status) {
    const check = status.capacityAssessment || { state: 'not_tested' };
    const safety = status.resourceSafety || { state: 'blocked', reason: 'Resource safety has not been established' };
    const measured = safety.state === 'ready' && ['passed', 'limited'].includes(check.state) && check.testedSpeakers > 0;
    capacityTitle.className = measured ? 'ready' : 'attention';
    if (safety.state === 'blocked') {
      capacityTitle.textContent = 'Captions are unavailable';
      capacitySummary.textContent = safety.reason;
      capacityDetail.textContent = 'Ask your administrator to fix the resource limits or reduce load, then run the capacity check again.';
    } else if (measured) {
      const n = check.testedSpeakers;
      capacityTitle.textContent = `Measured for ${n} simultaneous ${n === 1 ? 'speaker' : 'speakers'}`;
      capacitySummary.textContent = `Across all calls on this server. Tested with ${check.testedParticipants} open audio tracks, speech and simulated background noise, with throughput headroom.`;
      capacityDetail.textContent = 'This is a short English sample, not a guarantee. Microphones, network conditions, other languages and changing server load still affect captions.';
      if (check.checkedAt) capacityDetail.textContent += ` Checked ${new Date(check.checkedAt).toLocaleString()}.`;
    } else if (check.state === 'checking') {
      capacityTitle.textContent = 'Measuring server capacity…';
      capacitySummary.textContent = 'Running a bundled recording locally. No call or microphone is needed. Captions stay unavailable until the check passes.';
      capacityDetail.textContent = 'Usually takes less than two minutes.';
    } else if (check.state === 'insufficient') {
      capacityTitle.textContent = 'Caption capacity check did not pass';
      capacitySummary.textContent = 'This installation did not demonstrate reliable captions with headroom for one speaker.';
      capacityDetail.textContent = 'Ask your administrator to inspect the check results, reduce server load or provide more resources, then run the check again.';
    } else {
      capacityTitle.textContent = 'Caption capacity has not been established';
      capacitySummary.textContent = 'The installation check has not completed successfully.';
      capacityDetail.textContent = 'Ask your administrator to run the capacity check while no captioned calls are active.';
    }
    if (safety.state === 'ready' && status.recentCapacityPressure) {
      state.textContent = 'Caption service is under load';
      state.className = 'attention';
      explanation.textContent = 'Some speech was recently dropped. Captions may be incomplete or delayed.';
    }
  }
  async function refresh() {
    try {
      const response = await fetch(root + 'status', { credentials: 'same-origin',
        signal: AbortSignal.timeout(10000) });
      if (!response.ok) throw new Error('unavailable');
      const status = await response.json();
      const ready = status.enabled && status.signallingConfigured && status.resourceSafety?.state === 'ready';
      state.textContent = ready ? 'Caption service is connected' : 'Caption service needs attention';
      state.className = ready ? 'ready' : 'attention';
      explanation.textContent = ready ? 'Open a Talk call and turn on captions.' :
        'Ask your administrator to check Talk’s high-performance backend and the app’s deploy options.';
      showCapacity(status);
    } catch {
      state.textContent = 'Cannot reach the caption service';
      state.className = 'attention';
      explanation.textContent = 'Try again shortly. If this continues, contact your administrator.';
      capacityTitle.textContent = 'Capacity status unavailable';
      capacityTitle.className = 'attention';
      capacitySummary.textContent = 'Could not load the latest measurement.';
      capacityDetail.textContent = '';
    }
  }
  refresh();
  const timer = setInterval(() => {
    if (!mount.isConnected) { clearInterval(timer); return; }
    if (!document.hidden) refresh();
  }, 30000);
})();

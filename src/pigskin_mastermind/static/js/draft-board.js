/* ============================================================
   Draft Board — Interactive Draft Experience
   ============================================================ */

const DraftBoard = (() => {
  // --- State ---
  let state = null;
  let userSlot = null;
  let clockInterval = null;
  let clockSeconds = 90;
  let highlightedIdx = -1;
  let spotlightPlayer = null;
  let queue = [];
  let queueOpen = false;
  let commentaryItems = [];
  let aiAdvancing = false;
  let soundEnabled = false;

  const POS_COLORS = {QB:'badge-qb',RB:'badge-rb',WR:'badge-wr',TE:'badge-te',K:'badge-k',DEF:'badge-def'};
  const POS_EMOJI = {QB:'🎯',RB:'🏃',WR:'🖐️',TE:'🤝',K:'🦵',DEF:'🛡️'};
  const LINEUP_SLOTS = ['QB','RB1','RB2','WR1','WR2','TE','FLEX','K','DEF'];
  const SLOT_POSITIONS = {QB:'QB',RB1:'RB',RB2:'RB',WR1:'WR',WR2:'WR',TE:'TE',FLEX:'FLEX',K:'K',DEF:'DEF'};
  const FLEX_ELIGIBLE = ['RB','WR','TE'];

  function posClass(pos) { return POS_COLORS[pos] || 'bg-slate-600 text-slate-200'; }

  // --- Init ---
  function init(initialState) {
    state = initialState;
    userSlot = String(state.user_pick_position);
    loadQueue();
    render(state);
    setupKeyboardShortcuts();
  }

  // --- Full render ---
  function render(newState) {
    state = newState;
    renderStatusBanner();
    renderGrid();
    renderTicker();
    renderPlayerList();
    renderRosterSlots();
    renderPicksLog();
    renderQueuePanel();
    renderCommentary();
    if (state.status === 'complete') showDraftComplete();
  }

  // ============================================================
  // Status Banner — "On the Clock" / AI Picking
  // ============================================================
  function renderStatusBanner() {
    const banner = document.getElementById('otc-banner');
    if (!banner) return;
    const isComplete = state.status === 'complete';
    const isUser = !isComplete && String(state.current_slot) === userSlot;

    if (isComplete) {
      banner.className = 'rounded-xl px-5 py-3 bg-green-900/50 border border-green-500/30 glow-green';
      banner.innerHTML = `
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-3">
            <span class="text-3xl">🏆</span>
            <div>
              <p class="text-lg font-extrabold text-green-400">Draft Complete!</p>
              <p class="text-xs text-green-300/70">Your team has been assembled</p>
            </div>
          </div>
          <div class="text-right">
            <p class="text-sm text-slate-400">${state.current_pick_index} / ${state.total_picks} picks</p>
          </div>
        </div>`;
      stopClock();
      return;
    }

    if (isUser) {
      startClock();
      banner.className = 'on-the-clock rounded-xl px-5 py-3 border border-pigskin-400/30';
      banner.innerHTML = `
        <div class="flex items-center justify-between flex-wrap gap-3">
          <div class="flex items-center gap-4">
            <div class="flex flex-col items-center bg-black/20 rounded-lg px-3 py-1.5 min-w-[60px]">
              <span class="text-[10px] uppercase tracking-wider text-pigskin-200/70 font-semibold">Round</span>
              <span class="text-xl font-black text-white">${state.current_round}</span>
            </div>
            <div class="flex flex-col items-center bg-black/20 rounded-lg px-3 py-1.5 min-w-[60px]">
              <span class="text-[10px] uppercase tracking-wider text-pigskin-200/70 font-semibold">Pick</span>
              <span class="text-xl font-black text-white">${state.current_pick_index + 1}</span>
            </div>
            <div>
              <p class="text-xl font-black text-white tracking-wide">YOUR PICK</p>
              <p class="text-xs text-pigskin-100/80">Slot ${userSlot} · Select a player below</p>
            </div>
          </div>
          <div class="flex items-center gap-4">
            <div class="text-center bg-black/20 rounded-lg px-4 py-2">
              <p id="clock-display" class="text-2xl font-black text-white tabular-nums">${formatTime(clockSeconds)}</p>
              <p class="text-[9px] uppercase tracking-wider text-pigskin-200/60 font-semibold">Time</p>
            </div>
          </div>
        </div>`;
    } else {
      stopClock();
      banner.className = 'rounded-xl px-5 py-3 bg-slate-800/80 border border-slate-600/30';
      banner.innerHTML = `
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-4">
            <div class="flex flex-col items-center bg-slate-700/50 rounded-lg px-3 py-1.5 min-w-[60px]">
              <span class="text-[10px] uppercase tracking-wider text-slate-400 font-semibold">Round</span>
              <span class="text-xl font-black text-slate-200">${state.current_round}</span>
            </div>
            <div class="flex flex-col items-center bg-slate-700/50 rounded-lg px-3 py-1.5 min-w-[60px]">
              <span class="text-[10px] uppercase tracking-wider text-slate-400 font-semibold">Pick</span>
              <span class="text-xl font-black text-slate-200">${state.current_pick_index + 1}</span>
            </div>
            <div>
              <p class="text-base font-bold text-slate-300">
                AI Picking — Slot ${state.current_slot}
                <span class="ai-dot-1 text-slate-400">.</span>
                <span class="ai-dot-2 text-slate-400">.</span>
                <span class="ai-dot-3 text-slate-400">.</span>
              </p>
              <p class="text-xs text-slate-500">${state.current_pick_index + 1} of ${state.total_picks} total picks</p>
            </div>
          </div>
        </div>`;
    }
  }

  // ============================================================
  // Clock
  // ============================================================
  function startClock() {
    stopClock();
    clockSeconds = 90;
    clockInterval = setInterval(() => {
      clockSeconds = Math.max(0, clockSeconds - 1);
      const el = document.getElementById('clock-display');
      if (el) el.textContent = formatTime(clockSeconds);
    }, 1000);
  }
  function stopClock() {
    if (clockInterval) { clearInterval(clockInterval); clockInterval = null; }
  }
  function formatTime(s) {
    return `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;
  }

  // ============================================================
  // Draft Board Grid
  // ============================================================
  function renderGrid() {
    const container = document.getElementById('draft-grid');
    if (!container) return;

    const { num_teams, num_rounds, picks_log } = state;
    // Build a lookup: "round-slot" -> pick info
    const pickMap = {};
    picks_log.forEach(p => { pickMap[`${p.round}-${p.slot}`] = p; });

    container.style.gridTemplateColumns = `40px repeat(${num_teams}, minmax(70px, 1fr))`;

    let html = '<div class="draft-grid-header"></div>';
    for (let t = 1; t <= num_teams; t++) {
      const isUser = String(t) === userSlot;
      html += `<div class="draft-grid-header ${isUser ? 'text-pigskin-400' : ''}">${isUser ? '★ You' : 'Tm ' + t}</div>`;
    }

    for (let r = 1; r <= num_rounds; r++) {
      html += `<div class="round-label">R${r}</div>`;
      const slotsOrder = r % 2 === 1
        ? Array.from({length: num_teams}, (_, i) => i + 1)
        : Array.from({length: num_teams}, (_, i) => num_teams - i);

      for (let t = 1; t <= num_teams; t++) {
        const isUser = String(t) === userSlot;
        const pick = pickMap[`${r}-${t}`];
        const isCurrent = !pick && state.status !== 'complete'
          && state.current_round === r && state.current_slot === t;

        let cellClass = 'draft-grid-cell';
        if (isUser) cellClass += ' user-col-highlight';
        if (isCurrent) cellClass += ' current-cell';
        if (pick) cellClass += ' filled cell-animate';

        let cellContent = '';
        if (pick) {
          const p = pick.player;
          cellContent = `
            <span class="inline-flex items-center justify-center w-6 h-4 rounded text-[8px] font-bold flex-shrink-0 ${posClass(p.position)}">${p.position}</span>
            <span class="truncate text-slate-300 text-[11px]" title="${p.name}">${p.name.split(' ').pop()}</span>`;
        } else if (isCurrent) {
          cellContent = `<span class="text-pigskin-400 text-[10px] font-bold">●</span>`;
        }

        html += `<div class="${cellClass}">${cellContent}</div>`;
      }
    }

    container.innerHTML = html;
  }

  // ============================================================
  // Draft Ticker
  // ============================================================
  function renderTicker() {
    const el = document.getElementById('draft-ticker');
    if (!el) return;

    const recent = state.picks_log.slice(-12).reverse();
    if (!recent.length) {
      el.innerHTML = '<span class="text-slate-500 text-xs px-4">Waiting for first pick…</span>';
      return;
    }

    el.innerHTML = recent.map((pick, i) => {
      const p = pick.player;
      const isNew = i === 0;
      return `<div class="flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800/80 border border-slate-700/50 ${isNew ? 'ticker-item-enter' : ''}">
        <span class="text-[10px] text-slate-500 font-bold">R${pick.round}P${pick.pick_number}</span>
        <span class="inline-flex items-center justify-center w-5 h-3.5 rounded text-[8px] font-bold ${posClass(p.position)}">${p.position}</span>
        <span class="text-[11px] text-slate-300 font-medium whitespace-nowrap">${p.name}</span>
        <span class="text-[10px] text-slate-500">→ ${String(pick.slot) === userSlot ? 'You' : 'Tm ' + pick.slot}</span>
      </div>`;
    }).join('');

    el.scrollLeft = 0;
  }

  // ============================================================
  // Available Players List
  // ============================================================
  function renderPlayerList() {
    const nameFilter = (document.getElementById('player-filter')?.value || '').toLowerCase();
    const posFilter = document.getElementById('pos-filter')?.value || '';
    const players = state.available_players.filter(p =>
      (!posFilter || p.position === posFilter) &&
      (!nameFilter || p.name.toLowerCase().includes(nameFilter))
    );

    const isUserTurn = state.status === 'in_progress' && String(state.current_slot) === userSlot;
    const currentPick = state.current_pick_index + 1;
    const container = document.getElementById('player-list');
    if (!container) return;

    if (!players.length) {
      container.innerHTML = '<p class="text-xs text-slate-500 text-center py-8">No players match your filter</p>';
      return;
    }

    container.innerHTML = players.slice(0, 200).map((p, i) => {
      const adpLabel = p.adp_rank != null ? p.adp_rank.toFixed(1) : '';
      const valueDelta = p.adp_rank != null ? p.adp_rank - currentPick : null;
      let valueTag = '';
      if (valueDelta !== null) {
        if (valueDelta >= 10) valueTag = '<span class="text-[9px] font-bold value-steal ml-1">🔥 STEAL</span>';
        else if (valueDelta >= 3) valueTag = '<span class="text-[9px] font-bold value-steal ml-1">✓ Value</span>';
        else if (valueDelta <= -15) valueTag = '<span class="text-[9px] font-bold value-reach ml-1">⚠️ Reach</span>';
      }

      const isQueued = queue.includes(p.id);
      const isHighlighted = i === highlightedIdx;

      return `<div class="flex items-center justify-between px-3 py-2 hover:bg-slate-700/40 transition-colors cursor-pointer group ${isHighlighted ? 'bg-slate-700/60 ring-1 ring-pigskin-500/50' : ''}"
                  data-player-idx="${i}" data-player-id="${p.id}"
                  onclick="DraftBoard.openSpotlight('${p.id}')">
        <div class="flex items-center gap-2 min-w-0">
          ${p.headshot_url ? `<img src="${p.headshot_url}" alt="" class="w-7 h-7 rounded-full object-cover bg-slate-700 flex-shrink-0" onerror="this.style.display='none'" />` : `<div class="w-7 h-7 rounded-full bg-slate-700 flex items-center justify-center flex-shrink-0 text-xs">${POS_EMOJI[p.position] || '🏈'}</div>`}
          <span class="inline-flex items-center justify-center w-9 h-5 rounded text-[10px] font-bold flex-shrink-0 ${posClass(p.position)}">${p.position}</span>
          <div class="min-w-0">
            <p class="text-sm font-medium text-slate-200 truncate">${p.name}</p>
            <p class="text-xs text-slate-500">
              ${p.nfl_team} · ${p.projected_points.toFixed(1)} pts
              ${adpLabel ? `<span class="ml-1 text-slate-400">ADP ${adpLabel}</span>` : ''}
              ${valueTag}
            </p>
          </div>
        </div>
        <div class="flex items-center gap-1.5 flex-shrink-0 ml-2">
          <button onclick="event.stopPropagation(); DraftBoard.toggleQueue('${p.id}')"
                  class="p-1 rounded text-xs transition-colors ${isQueued ? 'text-yellow-400' : 'text-slate-600 hover:text-yellow-400'}"
                  title="${isQueued ? 'Remove from queue' : 'Add to queue'}">
            ${isQueued ? '★' : '☆'}
          </button>
          ${isUserTurn
            ? `<button onclick="event.stopPropagation(); DraftBoard.makePick('${p.id}')"
                 class="px-2.5 py-1 text-xs font-bold rounded-lg bg-pigskin-500 text-white hover:bg-pigskin-600 transition-colors">
                 Draft
               </button>`
            : ''
          }
        </div>
      </div>`;
    }).join('');
  }

  // ============================================================
  // Player Spotlight Panel
  // ============================================================
  function openSpotlight(playerId) {
    const player = state.available_players.find(p => p.id === playerId);
    if (!player) return;
    spotlightPlayer = player;

    const panel = document.getElementById('spotlight-panel');
    if (!panel) return;

    const currentPick = state.current_pick_index + 1;
    const isUserTurn = state.status === 'in_progress' && String(state.current_slot) === userSlot;

    // Value assessment
    let valueClass = 'value-fair', valueLabel = '✅ Fair Value', valueBg = 'value-fair-bg';
    if (player.adp_rank != null) {
      const delta = player.adp_rank - currentPick;
      if (delta >= 10) { valueClass = 'value-steal'; valueLabel = '🔥 Great Steal'; valueBg = 'value-steal-bg'; }
      else if (delta >= 3) { valueClass = 'value-steal'; valueLabel = '✅ Good Value'; valueBg = 'value-steal-bg'; }
      else if (delta <= -15) { valueClass = 'value-reach'; valueLabel = '⚠️ Big Reach'; valueBg = 'value-reach-bg'; }
      else if (delta <= -5) { valueClass = 'value-reach'; valueLabel = '⚠️ Slight Reach'; valueBg = 'value-reach-bg'; }
    }

    // Positional rank
    const samePos = state.available_players.filter(p => p.position === player.position);
    samePos.sort((a, b) => b.projected_points - a.projected_points);
    const posRank = samePos.findIndex(p => p.id === player.id) + 1;

    panel.classList.remove('hidden');
    panel.innerHTML = `
      <div class="spotlight-enter draft-card p-5 space-y-4 max-h-[80vh] overflow-y-auto">
        <div class="flex items-center justify-between">
          <h3 class="text-sm font-bold text-slate-400 uppercase tracking-wide">Player Spotlight</h3>
          <button onclick="DraftBoard.closeSpotlight()" class="text-slate-500 hover:text-slate-300 text-lg">&times;</button>
        </div>

        <div class="flex items-center gap-3">
          ${player.headshot_url ? `<img src="${player.headshot_url}" alt="" class="w-16 h-16 rounded-full object-cover bg-slate-700 border-2 border-slate-600" onerror="this.style.display='none'" />` : `<div class="w-16 h-16 rounded-full bg-slate-700 flex items-center justify-center text-2xl">${POS_EMOJI[player.position] || '🏈'}</div>`}
          <div>
            <p class="text-lg font-bold text-white">${player.name}</p>
            <div class="flex items-center gap-2 mt-0.5">
              <span class="inline-flex items-center justify-center px-2 py-0.5 rounded text-[10px] font-bold ${posClass(player.position)}">${player.position}</span>
              <span class="text-xs text-slate-400">${player.nfl_team}</span>
            </div>
          </div>
        </div>

        <div class="rounded-lg border p-3 ${valueBg}">
          <p class="text-sm font-bold ${valueClass}">${valueLabel}</p>
          ${player.adp_rank != null ? `<p class="text-xs text-slate-400 mt-0.5">ADP: ${player.adp_rank.toFixed(1)} · Current Pick: ${currentPick} · Delta: ${(player.adp_rank - currentPick) > 0 ? '+' : ''}${(player.adp_rank - currentPick).toFixed(0)}</p>` : ''}
        </div>

        <div class="grid grid-cols-2 gap-3">
          <div class="bg-slate-800/50 rounded-lg p-3 text-center">
            <p class="text-xl font-black text-white">${player.projected_points.toFixed(1)}</p>
            <p class="text-[10px] uppercase text-slate-500 font-semibold">Projected Pts</p>
          </div>
          <div class="bg-slate-800/50 rounded-lg p-3 text-center">
            <p class="text-xl font-black text-pigskin-400">#${posRank}</p>
            <p class="text-[10px] uppercase text-slate-500 font-semibold">${player.position} Available</p>
          </div>
        </div>

        ${player.adp_rank != null ? `
        <div class="bg-slate-800/50 rounded-lg p-3">
          <p class="text-[10px] uppercase text-slate-500 font-semibold mb-2">Draft Value</p>
          <div class="w-full bg-slate-700 rounded-full h-2">
            <div class="h-2 rounded-full ${player.adp_rank <= 36 ? 'bg-pigskin-500' : player.adp_rank <= 72 ? 'bg-green-500' : player.adp_rank <= 120 ? 'bg-blue-500' : 'bg-slate-500'}"
                 style="width: ${Math.max(5, 100 - (player.adp_rank / 3))}%"></div>
          </div>
          <p class="text-[10px] text-slate-500 mt-1">Tier ${player.adp_rank <= 12 ? '1 — Elite' : player.adp_rank <= 36 ? '2 — Starter' : player.adp_rank <= 72 ? '3 — Solid' : player.adp_rank <= 120 ? '4 — Depth' : '5 — Late'}</p>
        </div>` : ''}

        ${isUserTurn ? `
        <button onclick="DraftBoard.makePick('${player.id}')"
                class="w-full py-3 rounded-xl font-bold text-white btn-enter-draft text-sm">
          🏈 Draft ${player.name.split(' ').pop()}
        </button>` : `
        <p class="text-center text-xs text-slate-500 py-2">Wait for your pick to draft this player</p>`}
      </div>`;
  }

  function closeSpotlight() {
    const panel = document.getElementById('spotlight-panel');
    if (panel) {
      const inner = panel.querySelector('.spotlight-enter');
      if (inner) {
        inner.classList.remove('spotlight-enter');
        inner.classList.add('spotlight-exit');
        setTimeout(() => { panel.classList.add('hidden'); }, 200);
      } else {
        panel.classList.add('hidden');
      }
    }
    spotlightPlayer = null;
  }

  // ============================================================
  // Roster Slots (Visual Lineup Builder)
  // ============================================================
  function renderRosterSlots() {
    const container = document.getElementById('roster-slots');
    if (!container) return;

    const myPlayers = state.rosters[userSlot] || [];
    const filled = assignToSlots(myPlayers);
    const bench = myPlayers.filter(p => !Object.values(filled).some(fp => fp && fp.id === p.id));
    const totalProj = myPlayers.reduce((s, p) => s + p.projected_points, 0);

    // Needs summary
    const needs = LINEUP_SLOTS.filter(s => !filled[s]);
    const needsText = needs.length > 0
      ? needs.map(s => SLOT_POSITIONS[s] === 'FLEX' ? 'FLEX' : SLOT_POSITIONS[s]).join(', ')
      : 'Lineup complete!';

    let html = `<div class="flex items-center justify-between mb-3">
      <div>
        <p class="text-sm font-bold text-slate-200">${myPlayers.length} players</p>
        <p class="text-[10px] text-slate-500">${needs.length > 0 ? 'Need: ' + needsText : '✓ ' + needsText}</p>
      </div>
      <div class="text-right">
        <p class="text-lg font-black text-pigskin-400">${totalProj.toFixed(1)}</p>
        <p class="text-[10px] text-slate-500 uppercase">Projected</p>
      </div>
    </div>`;

    // Starters
    html += '<div class="space-y-1.5 mb-3">';
    LINEUP_SLOTS.forEach(slot => {
      const player = filled[slot];
      const label = slot.replace(/\d/,'');
      if (player) {
        html += `<div class="slot-filled rounded-lg px-3 py-2 flex items-center gap-2">
          <span class="w-8 text-[10px] font-bold text-green-400 flex-shrink-0">${label}</span>
          ${player.headshot_url ? `<img src="${player.headshot_url}" class="w-5 h-5 rounded-full object-cover bg-slate-700 flex-shrink-0" onerror="this.style.display='none'" />` : ''}
          <span class="inline-flex items-center justify-center w-7 h-4 rounded text-[8px] font-bold ${posClass(player.position)}">${player.position}</span>
          <span class="text-xs text-slate-200 truncate flex-1">${player.name}</span>
          <span class="text-[10px] text-slate-500 flex-shrink-0">${player.projected_points.toFixed(1)}</span>
        </div>`;
      } else {
        html += `<div class="slot-empty rounded-lg px-3 py-2 flex items-center gap-2">
          <span class="w-8 text-[10px] font-bold text-slate-500 flex-shrink-0">${label}</span>
          <span class="text-[10px] text-slate-600 italic">Empty</span>
        </div>`;
      }
    });
    html += '</div>';

    // Bench
    if (bench.length > 0) {
      html += `<p class="text-[10px] font-bold text-slate-500 uppercase tracking-wide mb-1.5">Bench (${bench.length})</p>`;
      html += '<div class="space-y-1">';
      bench.forEach(p => {
        html += `<div class="flex items-center gap-2 px-3 py-1.5 rounded bg-slate-800/30">
          <span class="inline-flex items-center justify-center w-7 h-4 rounded text-[8px] font-bold ${posClass(p.position)}">${p.position}</span>
          <span class="text-xs text-slate-400 truncate flex-1">${p.name}</span>
          <span class="text-[10px] text-slate-600">${p.projected_points.toFixed(1)}</span>
        </div>`;
      });
      html += '</div>';
    }

    container.innerHTML = html;
  }

  function assignToSlots(players) {
    const filled = {};
    const used = new Set();

    // Fill required positions first
    LINEUP_SLOTS.forEach(slot => {
      if (slot === 'FLEX') return;
      const targetPos = SLOT_POSITIONS[slot];
      const candidate = players.find(p => p.position === targetPos && !used.has(p.id));
      if (candidate) {
        filled[slot] = candidate;
        used.add(candidate.id);
      }
    });

    // Fill FLEX with best remaining eligible
    const flexCandidate = players
      .filter(p => FLEX_ELIGIBLE.includes(p.position) && !used.has(p.id))
      .sort((a, b) => b.projected_points - a.projected_points)[0];
    if (flexCandidate) {
      filled['FLEX'] = flexCandidate;
      used.add(flexCandidate.id);
    }

    return filled;
  }

  // ============================================================
  // Picks Log
  // ============================================================
  function renderPicksLog() {
    const container = document.getElementById('picks-log');
    if (!container) return;
    const recent = [...state.picks_log].reverse().slice(0, 20);

    if (!recent.length) {
      container.innerHTML = '<p class="text-xs text-slate-600 text-center py-4">No picks yet</p>';
      return;
    }

    container.innerHTML = recent.map((pick, i) => {
      const p = pick.player;
      const isNew = i === 0;
      const isUserPick = String(pick.slot) === userSlot;
      return `<div class="flex items-center gap-2 px-2 py-1.5 text-xs rounded ${isNew ? 'pick-slide' : ''} ${isUserPick ? 'bg-pigskin-900/30' : ''}">
        <span class="text-slate-500 w-12 flex-shrink-0 font-mono">R${pick.round}P${pick.pick_number}</span>
        <span class="inline-flex items-center justify-center w-7 h-4 rounded text-[8px] font-bold flex-shrink-0 ${posClass(p.position)}">${p.position}</span>
        <span class="truncate font-medium ${isUserPick ? 'text-pigskin-300' : 'text-slate-300'}">${p.name}</span>
        <span class="ml-auto text-slate-600 flex-shrink-0">${isUserPick ? '★ You' : 'Tm ' + pick.slot}</span>
      </div>`;
    }).join('');
  }

  // ============================================================
  // Queue System
  // ============================================================
  function toggleQueue(playerId) {
    const idx = queue.indexOf(playerId);
    if (idx >= 0) queue.splice(idx, 1);
    else queue.push(playerId);
    saveQueue();
    renderPlayerList();
    renderQueuePanel();
  }

  function renderQueuePanel() {
    const container = document.getElementById('queue-panel');
    const toggleBtn = document.getElementById('queue-toggle-btn');
    if (!container) return;

    if (toggleBtn) {
      toggleBtn.textContent = `Queue (${queue.length})`;
    }

    if (!queueOpen) {
      container.classList.add('hidden');
      return;
    }
    container.classList.remove('hidden');

    if (!queue.length) {
      container.innerHTML = '<p class="text-xs text-slate-600 text-center py-4">Click ☆ to add players to your queue</p>';
      return;
    }

    container.innerHTML = queue.map((id, i) => {
      const p = state.available_players.find(pl => pl.id === id);
      if (!p) return `<div class="queue-stolen flex items-center gap-2 px-2 py-1.5 text-xs text-slate-600">
        <span class="font-bold">${i+1}.</span>
        <span>Player taken 💔</span>
        <button onclick="DraftBoard.removeFromQueue('${id}')" class="ml-auto text-slate-600 hover:text-red-400">×</button>
      </div>`;

      return `<div class="flex items-center gap-2 px-2 py-1.5 text-xs">
        <span class="text-slate-500 font-bold w-4">${i+1}.</span>
        <span class="inline-flex items-center justify-center w-7 h-4 rounded text-[8px] font-bold ${posClass(p.position)}">${p.position}</span>
        <span class="truncate text-slate-300 flex-1">${p.name}</span>
        <span class="text-slate-600">${p.projected_points.toFixed(1)}</span>
        <button onclick="DraftBoard.removeFromQueue('${p.id}')" class="text-slate-600 hover:text-red-400">×</button>
      </div>`;
    }).join('');
  }

  function removeFromQueue(id) {
    queue = queue.filter(q => q !== id);
    saveQueue();
    renderPlayerList();
    renderQueuePanel();
  }

  function toggleQueuePanel() {
    queueOpen = !queueOpen;
    renderQueuePanel();
  }

  function saveQueue() { try { localStorage.setItem('draft_queue', JSON.stringify(queue)); } catch(e){} }
  function loadQueue() { try { const q = localStorage.getItem('draft_queue'); if (q) queue = JSON.parse(q); } catch(e){} }

  // ============================================================
  // Commentary
  // ============================================================
  function addCommentary(items) {
    if (!items || !items.length) return;
    commentaryItems = [...items, ...commentaryItems].slice(0, 30);
    renderCommentary();
  }

  function generateLocalCommentary(pick) {
    const items = [];
    const p = pick.player;
    const currentPick = pick.pick_number;

    // Value assessment
    if (p.adp_rank != null) {
      const delta = p.adp_rank - currentPick;
      if (delta >= 15) items.push({ type: 'steal', text: `🔥 Steal! ${p.name} (ADP ${p.adp_rank.toFixed(0)}) falls ${delta.toFixed(0)} spots` });
      else if (delta <= -15) items.push({ type: 'reach', text: `⚠️ Reach — ${p.name} drafted ${Math.abs(delta).toFixed(0)} picks above ADP` });
    }

    // Position run detection
    const last5 = state.picks_log.slice(-5);
    const posCount = last5.filter(pk => pk.player.position === p.position).length;
    if (posCount >= 3) items.push({ type: 'alert', text: `📊 ${p.position} run! ${posCount} of last 5 picks are ${p.position}s` });

    // Scarcity alert for user
    const remaining = state.available_players.filter(pl => pl.position === p.position);
    if (['QB','TE','K','DEF'].includes(p.position) && remaining.length <= 3 && remaining.length > 0) {
      items.push({ type: 'tip', text: `⏳ Only ${remaining.length} ${p.position}${remaining.length > 1 ? 's' : ''} left in pool` });
    }

    return items;
  }

  function renderCommentary() {
    const container = document.getElementById('commentary-feed');
    if (!container) return;

    if (!commentaryItems.length) {
      container.innerHTML = '<p class="text-xs text-slate-600 text-center py-4">Draft commentary will appear here</p>';
      return;
    }

    container.innerHTML = commentaryItems.slice(0, 15).map((item, i) => {
      const typeClass = `commentary-${item.type || 'info'}`;
      return `<div class="${typeClass} commentary-item px-3 py-2 rounded text-xs text-slate-300 ${i === 0 ? '' : ''}">${item.text}</div>`;
    }).join('');
  }

  // ============================================================
  // Make Pick
  // ============================================================
  async function makePick(playerId) {
    if (aiAdvancing) return;
    closeSpotlight();

    try {
      // Submit user's pick (backend does NOT auto-advance AI now)
      const resp = await fetch('/draft/pick', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ draft_id: state.draft_id, player_id: playerId }),
      });
      if (!resp.ok) {
        const err = await resp.json();
        if (typeof showToast === 'function') showToast(err.detail || 'Pick failed', 'error');
        return;
      }
      const afterUserState = await resp.json();

      // Confetti for user pick
      fireConfetti();

      // Commentary from server for user pick
      if (afterUserState.commentary) {
        addCommentary(afterUserState.commentary);
      } else {
        const userPick = afterUserState.picks_log.find(p =>
          !state.picks_log.some(sp => sp.pick_number === p.pick_number)
        );
        if (userPick) addCommentary(generateLocalCommentary(userPick));
      }

      render(afterUserState);

      // Staggered AI advancement — one pick at a time with visual delays
      await advanceAiPicksStaggered();

    } catch (e) {
      if (typeof showToast === 'function') showToast('Network error', 'error');
    }
  }

  async function advanceAiPicksStaggered() {
    if (aiAdvancing) return;
    aiAdvancing = true;
    renderStatusBanner();

    const delay = ms => new Promise(r => setTimeout(r, ms));

    while (true) {
      await delay(600 + Math.random() * 400);

      try {
        const resp = await fetch('/draft/advance', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ draft_id: state.draft_id }),
        });

        if (!resp.ok) break; // user's turn or draft complete

        const newState = await resp.json();

        // Server commentary
        if (newState.commentary && newState.commentary.length) {
          addCommentary(newState.commentary);
        } else {
          const latest = newState.picks_log[newState.picks_log.length - 1];
          if (latest) addCommentary(generateLocalCommentary(latest));
        }

        // Check if any queued players were taken
        const latestPick = newState.picks_log[newState.picks_log.length - 1];
        if (latestPick && queue.includes(latestPick.player.id)) {
          addCommentary([{ type: 'alert', text: `💔 ${latestPick.player.name} was taken from your queue by Tm ${latestPick.slot}` }]);
        }

        render(newState);
      } catch (e) {
        break;
      }
    }

    aiAdvancing = false;
    renderStatusBanner();
  }

  // ============================================================
  // Confetti
  // ============================================================
  function fireConfetti() {
    const colors = ['#d47a1e','#dd9642','#22c55e','#3b82f6','#ef4444','#a78bfa','#f59e0b'];
    for (let i = 0; i < 50; i++) {
      const piece = document.createElement('div');
      piece.className = 'confetti-piece';
      piece.style.left = Math.random() * 100 + 'vw';
      piece.style.backgroundColor = colors[Math.floor(Math.random() * colors.length)];
      piece.style.animationDuration = (2 + Math.random() * 2) + 's';
      piece.style.animationDelay = Math.random() * 0.5 + 's';
      piece.style.borderRadius = Math.random() > 0.5 ? '50%' : '2px';
      piece.style.width = (6 + Math.random() * 8) + 'px';
      piece.style.height = (6 + Math.random() * 8) + 'px';
      document.body.appendChild(piece);
      setTimeout(() => piece.remove(), 4500);
    }
  }

  // ============================================================
  // Countdown Overlay
  // ============================================================
  function playCountdown(callback) {
    const overlay = document.createElement('div');
    overlay.className = 'countdown-overlay';
    overlay.id = 'countdown-overlay';
    document.body.appendChild(overlay);

    const steps = [
      { text: '3', duration: 900 },
      { text: '2', duration: 900 },
      { text: '1', duration: 900 },
      { text: 'DRAFT', isTitle: true, duration: 1200 },
    ];

    let i = 0;
    function showNext() {
      if (i >= steps.length) {
        overlay.style.transition = 'opacity 0.4s';
        overlay.style.opacity = '0';
        setTimeout(() => { overlay.remove(); if (callback) callback(); }, 400);
        return;
      }
      const step = steps[i];
      overlay.innerHTML = step.isTitle
        ? `<div class="draft-title-text">🏈 ${step.text} NIGHT</div>`
        : `<div class="countdown-number">${step.text}</div>`;
      i++;
      setTimeout(showNext, step.duration);
    }
    showNext();
  }

  // ============================================================
  // Draft Complete
  // ============================================================
  async function showDraftComplete() {
    const overlay = document.getElementById('complete-overlay');
    if (!overlay) return;

    const myPlayers = state.rosters[userSlot] || [];
    const totalProj = myPlayers.reduce((s, p) => s + p.projected_points, 0);
    const posCounts = {};
    myPlayers.forEach(p => { posCounts[p.position] = (posCounts[p.position] || 0) + 1; });

    // Show immediate overlay while loading grades
    overlay.classList.remove('hidden');
    overlay.innerHTML = `
      <div class="draft-card max-w-lg w-full p-8 text-center space-y-5">
        <div class="text-5xl">🏆</div>
        <h2 class="text-2xl font-extrabold text-white">Draft Complete!</h2>
        <p class="text-slate-400 animate-pulse">Calculating grades...</p>
      </div>`;

    fireConfetti();

    // Fetch detailed grades from backend
    let gradeData = null;
    try {
      const resp = await fetch(`/draft/grade/${state.draft_id}`);
      if (resp.ok) gradeData = await resp.json();
    } catch (e) { /* fall through to simple grade */ }

    const grade = gradeData ? gradeData.grade : 'B';
    const gradeClass = grade.startsWith('A') ? 'grade-a' : grade.startsWith('B') ? 'grade-b' : grade.startsWith('C') ? 'grade-c' : 'grade-d';

    let pickBreakdownHtml = '';
    if (gradeData && gradeData.pick_analysis) {
      pickBreakdownHtml = `
        <div class="text-left mt-4 max-h-48 overflow-y-auto styled-scrollbar">
          <p class="text-xs text-slate-500 uppercase mb-2 font-bold">Pick-by-Pick Analysis</p>
          <div class="space-y-1">
            ${gradeData.pick_analysis.map(pa => `
              <div class="flex items-center justify-between text-xs px-2 py-1.5 rounded bg-slate-800/60">
                <span class="text-slate-500">R${pa.round}P${pa.pick_number}</span>
                <span class="font-semibold text-white flex-1 ml-2">${pa.player}</span>
                <span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold ${posClass(pa.position)}">${pa.position}</span>
                <span class="ml-2 ${pa.verdict === 'steal' ? 'text-green-400' : pa.verdict === 'value' ? 'text-green-300' : pa.verdict === 'fair' ? 'text-slate-400' : 'text-yellow-400'}">${pa.label}</span>
              </div>`).join('')}
          </div>
        </div>`;
    }

    let strengthsHtml = '';
    if (gradeData && (gradeData.strengths.length || gradeData.weaknesses.length)) {
      strengthsHtml = `
        <div class="flex gap-4 text-left mt-3">
          ${gradeData.strengths.length ? `<div class="flex-1"><p class="text-[10px] text-green-500 uppercase font-bold mb-1">Strengths</p>
            ${gradeData.strengths.map(s => `<p class="text-xs text-green-300">✅ ${s}</p>`).join('')}</div>` : ''}
          ${gradeData.weaknesses.length ? `<div class="flex-1"><p class="text-[10px] text-red-500 uppercase font-bold mb-1">Weaknesses</p>
            ${gradeData.weaknesses.map(w => `<p class="text-xs text-red-300">⚠️ ${w}</p>`).join('')}</div>` : ''}
        </div>`;
    }

    let comparisonHtml = '';
    if (gradeData && gradeData.best_ai) {
      const diff = totalProj - gradeData.best_ai.projected;
      comparisonHtml = `
        <div class="text-xs text-slate-400 mt-2 bg-slate-800/40 rounded px-3 py-2">
          vs. Best AI (Tm ${gradeData.best_ai.slot}): <span class="${diff >= 0 ? 'text-green-400' : 'text-red-400'} font-bold">${diff >= 0 ? '+' : ''}${diff.toFixed(1)} pts</span>
          <span class="text-slate-600 ml-1">(${gradeData.best_ai.strategy || 'unknown'} strategy)</span>
        </div>`;
    }

    overlay.innerHTML = `
      <div class="draft-card max-w-xl w-full p-8 text-center space-y-4 max-h-[90vh] overflow-y-auto styled-scrollbar">
        <div class="text-5xl">🏆</div>
        <h2 class="text-2xl font-extrabold text-white">Draft Complete!</h2>
        <div class="flex items-center justify-center gap-6">
          <div>
            <p class="text-6xl font-black ${gradeClass}">${grade}</p>
            <p class="text-xs text-slate-500 uppercase mt-1">Draft Grade</p>
          </div>
          <div class="text-left space-y-1">
            <p class="text-sm text-slate-300"><span class="font-bold text-white">${myPlayers.length}</span> players drafted</p>
            <p class="text-sm text-slate-300"><span class="font-bold text-pigskin-400">${totalProj.toFixed(1)}</span> projected pts</p>
            <div class="flex flex-wrap gap-1 mt-2">
              ${Object.entries(posCounts).map(([pos, cnt]) =>
                `<span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold ${posClass(pos)}">${pos} ${cnt}</span>`
              ).join('')}
            </div>
          </div>
        </div>
        ${comparisonHtml}
        ${strengthsHtml}
        ${pickBreakdownHtml}
        <div class="flex gap-3 justify-center pt-3">
          <a href="/draft" class="px-5 py-2.5 bg-slate-700 text-slate-200 rounded-xl text-sm font-semibold hover:bg-slate-600 transition-colors">New Draft</a>
          <button onclick="document.getElementById('complete-overlay').classList.add('hidden')"
                  class="px-5 py-2.5 btn-enter-draft text-white rounded-xl text-sm font-semibold">View Board</button>
        </div>
      </div>`;
  }

  // ============================================================
  // Keyboard Shortcuts
  // ============================================================
  function setupKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
      // Don't capture when typing in inputs
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return;

      const players = getFilteredPlayers();
      const isUserTurn = state.status === 'in_progress' && String(state.current_slot) === userSlot;

      switch(e.key) {
        case 'ArrowDown':
          e.preventDefault();
          highlightedIdx = Math.min(highlightedIdx + 1, players.length - 1);
          renderPlayerList();
          scrollToHighlighted();
          break;
        case 'ArrowUp':
          e.preventDefault();
          highlightedIdx = Math.max(highlightedIdx - 1, 0);
          renderPlayerList();
          scrollToHighlighted();
          break;
        case 'Enter':
          if (isUserTurn && highlightedIdx >= 0 && players[highlightedIdx]) {
            e.preventDefault();
            makePick(players[highlightedIdx].id);
          }
          break;
        case ' ':
          if (highlightedIdx >= 0 && players[highlightedIdx]) {
            e.preventDefault();
            openSpotlight(players[highlightedIdx].id);
          }
          break;
        case 'Escape':
          closeSpotlight();
          break;
        case 'q':
        case 'Q':
          toggleQueuePanel();
          break;
        case 'f':
        case 'F':
          e.preventDefault();
          document.getElementById('pos-filter')?.focus();
          break;
      }
    });
  }

  function getFilteredPlayers() {
    const nameFilter = (document.getElementById('player-filter')?.value || '').toLowerCase();
    const posFilter = document.getElementById('pos-filter')?.value || '';
    return state.available_players.filter(p =>
      (!posFilter || p.position === posFilter) &&
      (!nameFilter || p.name.toLowerCase().includes(nameFilter))
    );
  }

  function scrollToHighlighted() {
    const el = document.querySelector(`[data-player-idx="${highlightedIdx}"]`);
    if (el) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  function filterPlayers() {
    highlightedIdx = -1;
    renderPlayerList();
  }

  // ============================================================
  // Public API
  // ============================================================
  return {
    init,
    render,
    makePick,
    openSpotlight,
    closeSpotlight,
    toggleQueue,
    removeFromQueue,
    toggleQueuePanel,
    filterPlayers,
    fireConfetti,
    playCountdown,
    addCommentary,
    advanceAiPicksStaggered,
  };
})();

/**
 * Projection Algorithm Tuner — client-side interactivity.
 *
 * Handles slider↔input sync, simulation API calls, waterfall chart
 * rendering, and before/after comparison for bulk backtesting.
 */

const TunerApp = (() => {
    let mode = 'single'; // 'single' | 'bulk'
    let debounceTimer = null;
    let algorithmPollTimer = null;
    let importPollTimer = null;

    // ── Slider / Input Sync ──────────────────────────────────────────

    function syncInput(key) {
        const slider = document.getElementById(`mc-slider-${key}`);
        const num = document.getElementById(`mc-num-${key}`);
        num.value = slider.value;
        markModified(key);
    }

    function syncSlider(key) {
        const slider = document.getElementById(`mc-slider-${key}`);
        const num = document.getElementById(`mc-num-${key}`);
        slider.value = num.value;
        markModified(key);
    }

    function markModified(key) {
        const num = document.getElementById(`mc-num-${key}`);
        if (!num) return;
        const def = parseFloat(num.dataset.default);
        const cur = parseFloat(num.value);
        const container = num.closest('[data-mc-key]');
        if (container) {
            if (Math.abs(cur - def) > 0.0001) {
                container.classList.add('bg-pigskin-50');
                num.classList.add('ring-1', 'ring-pigskin-300');
            } else {
                container.classList.remove('bg-pigskin-50');
                num.classList.remove('ring-1', 'ring-pigskin-300');
            }
        }
        updateMCParamsBanner();
    }

    // ── Reset ────────────────────────────────────────────────────────

    function resetAll() {
        document.querySelectorAll('.mc-input').forEach(input => {
            const key = input.id.replace('mc-num-', '');
            const defVal = parseFloat(input.dataset.default);
            input.value = defVal;
            const slider = document.getElementById(`mc-slider-${key}`);
            if (slider) slider.value = defVal;
            markModified(key);
        });
    }

    function resetGroup(group) {
        document.querySelectorAll(`[data-mc-group="${group}"]`).forEach(container => {
            const input = container.querySelector('.mc-input');
            const slider = container.querySelector('.mc-slider');
            if (!input || !slider) return;
            const key = input.id.replace('mc-num-', '');
            const defVal = parseFloat(input.dataset.default);
            input.value = defVal;
            slider.value = defVal;
            markModified(key);
        });
    }

    // ── MC Parameters Banner ─────────────────────────────────────────

    function updateMCParamsBanner() {
        const label = document.getElementById('mc-params-label');
        if (!label) return;
        const inputs = document.querySelectorAll('.mc-input');
        let modifiedCount = 0;
        inputs.forEach(input => {
            const def = parseFloat(input.dataset.default);
            const cur = parseFloat(input.value);
            if (Math.abs(cur - def) > 0.0001) modifiedCount++;
        });
        const total = inputs.length;
        if (modifiedCount === 0) {
            label.textContent = 'All defaults';
            label.className = 'text-slate-500';
        } else {
            label.textContent = `${modifiedCount}/${total} modified`;
            label.className = 'text-pigskin-600 font-semibold';
        }
    }

    // ── Mode Tabs ────────────────────────────────────────────────────

    function setMode(newMode) {
        mode = newMode;
        const tabSingle = document.getElementById('tab-single');
        const tabBulk = document.getElementById('tab-bulk');
        const singleCtrl = document.getElementById('single-controls');
        const bulkCtrl = document.getElementById('bulk-controls');

        if (mode === 'single') {
            tabSingle.className = 'flex-1 px-4 py-2 text-xs font-semibold bg-pigskin-500 text-white';
            tabBulk.className = 'flex-1 px-4 py-2 text-xs font-semibold bg-white text-slate-600 hover:bg-slate-50';
            singleCtrl.classList.remove('hidden');
            bulkCtrl.classList.add('hidden');
        } else {
            tabBulk.className = 'flex-1 px-4 py-2 text-xs font-semibold bg-pigskin-500 text-white';
            tabSingle.className = 'flex-1 px-4 py-2 text-xs font-semibold bg-white text-slate-600 hover:bg-slate-50';
            singleCtrl.classList.add('hidden');
            bulkCtrl.classList.remove('hidden');
        }
    }

    // ── Collect MC Parameters ────────────────────────────────────────

    function getMCParams() {
        const params = {};
        document.querySelectorAll('.mc-input').forEach(input => {
            const key = input.id.replace('mc-num-', '');
            const val = parseFloat(input.value);
            const def = parseFloat(input.dataset.default);
            // Only include params that differ from defaults to keep payload minimal
            if (Math.abs(val - def) > 0.0001) {
                params[key] = val;
            }
        });
        // Always include simulations so the count is explicit
        const simInput = document.getElementById('mc-num-simulations');
        if (simInput) params['simulations'] = parseInt(simInput.value);
        return Object.keys(params).length > 0 ? params : null;
    }

    // ── Filter Players ───────────────────────────────────────────────

    function filterPlayers() {
        const pos = document.getElementById('filter-pos').value;
        const select = document.getElementById('sim-player');
        Array.from(select.options).forEach(opt => {
            if (!pos || opt.dataset.pos === pos) {
                opt.hidden = false;
            } else {
                opt.hidden = true;
            }
        });
        // Select first visible option
        const firstVisible = Array.from(select.options).find(o => !o.hidden);
        if (firstVisible) select.value = firstVisible.value;
    }

    // ── Run Simulation ─────────────────────────────────────────────────

    async function runSimulation() {
        const btn = document.getElementById('sim-run-btn');
        const spinner = document.getElementById('sim-spinner');
        btn.disabled = true;
        spinner.classList.remove('hidden');

        const mcParams = getMCParams();
        const projType = document.getElementById('sim-type').value;
        const year = parseInt(document.getElementById('sim-year').value);
        const week = parseInt(document.getElementById('sim-week').value) || null;

        try {
            if (mode === 'single') {
                const playerId = parseInt(document.getElementById('sim-player').value);
                const resp = await fetch('/api/projection-tuner/simulate-player', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        player_id: playerId,
                        year: year,
                        week: projType === 'weekly' ? week : null,
                        projection_type: projType,
                        mc_params: mcParams,
                    }),
                });
                const data = await resp.json();
                renderSingleResult(data);
            } else {
                const position = document.getElementById('bulk-position').value || null;
                const resp = await fetch('/api/projection-tuner/simulate-bulk', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        position: position,
                        year: year,
                        week: projType === 'weekly' ? week : null,
                        projection_type: projType,
                        mc_params: mcParams,
                    }),
                });
                const data = await resp.json();
                renderBulkResult(data);
            }
        } catch (err) {
            document.getElementById('results-container').innerHTML = `
                <div class="bg-red-50 border border-red-200 rounded-xl p-4">
                    <p class="text-sm text-red-700 font-medium">Error: ${err.message}</p>
                </div>`;
        } finally {
            btn.disabled = false;
            spinner.classList.add('hidden');
        }
    }

    // ── Render Single Player Result ──────────────────────────────────

    function renderSingleResult(data) {
        const container = document.getElementById('results-container');
        if (data.error) {
            container.innerHTML = `
                <div class="bg-amber-50 border border-amber-200 rounded-xl p-4">
                    <p class="text-sm text-amber-700 font-medium">${data.error}</p>
                </div>`;
            return;
        }

        const { player, tuned, default: def } = data;
        const steps = tuned.steps || [];
        const mc = tuned.monte_carlo || null;

        // Summary cards — now MC-primary
        let html = `
        <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">
            <div class="flex items-center justify-between mb-3">
                <div>
                    <h4 class="text-sm font-bold text-slate-700">${player.name}</h4>
                    <p class="text-xs text-slate-500">${player.position} · ${player.nfl_team} · ${tuned.projection_type} ${tuned.week ? 'Week ' + tuned.week : ''} ${tuned.year}</p>
                </div>
                <span class="text-[10px] font-semibold text-indigo-500 bg-indigo-50 px-2 py-0.5 rounded-full">Monte Carlo · 10k sims</span>
            </div>
            <div class="grid grid-cols-3 gap-3 mb-4">
                <div class="bg-slate-50 rounded-lg p-3 text-center">
                    <p class="text-lg font-bold text-slate-700">${def ? def.total : '—'}</p>
                    <p class="text-[10px] uppercase tracking-wider text-slate-400 font-semibold">Default MC</p>
                </div>
                <div class="bg-indigo-50 rounded-lg p-3 text-center border border-indigo-200">
                    <p class="text-lg font-bold text-indigo-700">${tuned.total}</p>
                    <p class="text-[10px] uppercase tracking-wider text-indigo-500 font-semibold">MC Expected</p>
                </div>
                <div class="bg-field-50 rounded-lg p-3 text-center">
                    <p class="text-lg font-bold text-field-700">${tuned.actual_points !== null ? tuned.actual_points : '—'}</p>
                    <p class="text-[10px] uppercase tracking-wider text-field-500 font-semibold">Actual</p>
                </div>
            </div>`;

        // Monte Carlo distribution cards
        if (mc) {
            html += `
            <div class="grid grid-cols-5 gap-2 mb-4">
                <div class="bg-slate-50 rounded-lg p-2 text-center">
                    <p class="text-sm font-bold text-slate-600">${mc.floor}</p>
                    <p class="text-[9px] uppercase tracking-wider text-slate-400 font-semibold">Floor (P10)</p>
                </div>
                <div class="bg-slate-50 rounded-lg p-2 text-center">
                    <p class="text-sm font-bold text-slate-600">${mc.median}</p>
                    <p class="text-[9px] uppercase tracking-wider text-slate-400 font-semibold">Median</p>
                </div>
                <div class="bg-slate-50 rounded-lg p-2 text-center">
                    <p class="text-sm font-bold text-slate-600">${mc.ceiling}</p>
                    <p class="text-[9px] uppercase tracking-wider text-slate-400 font-semibold">Ceiling (P90)</p>
                </div>
                <div class="bg-emerald-50 rounded-lg p-2 text-center border border-emerald-200">
                    <p class="text-sm font-bold text-emerald-700">${(mc.boom_probability * 100).toFixed(1)}%</p>
                    <p class="text-[9px] uppercase tracking-wider text-emerald-500 font-semibold">Boom (≥25)</p>
                </div>
                <div class="bg-red-50 rounded-lg p-2 text-center border border-red-200">
                    <p class="text-sm font-bold text-red-600">${(mc.bust_probability * 100).toFixed(1)}%</p>
                    <p class="text-[9px] uppercase tracking-wider text-red-500 font-semibold">Bust (≤8)</p>
                </div>
            </div>`;

            // Range bar visualization
            const rangeMin = mc.floor;
            const rangeMax = mc.ceiling;
            const range = rangeMax - rangeMin || 1;
            const expectedPct = ((mc.expected - rangeMin) / range * 100).toFixed(1);
            const actualPct = tuned.actual_points !== null ? ((tuned.actual_points - rangeMin) / range * 100).toFixed(1) : null;
            html += `
            <div class="mb-4">
                <h5 class="text-xs font-bold text-slate-600 uppercase tracking-wider mb-2">Projection Range</h5>
                <div class="relative h-6 bg-gradient-to-r from-red-100 via-slate-100 to-emerald-100 rounded-full overflow-visible">
                    <div class="absolute top-0 left-0 h-full flex items-center" style="left: ${expectedPct}%">
                        <div class="w-3 h-3 bg-indigo-600 rounded-full border-2 border-white shadow" title="MC Expected: ${mc.expected}"></div>
                    </div>
                    ${actualPct !== null ? `<div class="absolute top-0 left-0 h-full flex items-center" style="left: ${Math.max(0, Math.min(100, actualPct))}%">
                        <div class="w-3 h-3 bg-field-600 rounded-full border-2 border-white shadow" title="Actual: ${tuned.actual_points}"></div>
                    </div>` : ''}
                </div>
                <div class="flex justify-between text-[10px] text-slate-400 mt-1">
                    <span>Floor: ${mc.floor}</span>
                    <span class="text-indigo-500 font-semibold">● Expected</span>
                    ${actualPct !== null ? '<span class="text-field-500 font-semibold">● Actual</span>' : ''}
                    <span>Ceiling: ${mc.ceiling}</span>
                </div>
            </div>`;

            // Full MC histogram
            if (mc.histogram) {
                const mcWithActual = { ...mc, actual: tuned.actual_points };
                html += `
            <div class="mb-4">
                <h5 class="text-xs font-bold text-slate-600 uppercase tracking-wider mb-2">Score Distribution</h5>
                <div class="overflow-x-auto rounded border border-slate-100 bg-slate-50/50 p-2">${renderHistogramSVG(mc.histogram, mcWithActual)}</div>
                <div class="flex gap-4 mt-1.5 text-[10px] text-slate-400">
                    <span><span style="color:#fca5a5">■</span> Bust zone (≤8)</span>
                    <span><span style="color:#818cf8">■</span> Mid range</span>
                    <span><span style="color:#86efac">■</span> Boom zone (≥25)</span>
                    <span style="color:#ef4444">— Floor</span>
                    <span style="color:#4f46e5">— Expected</span>
                    <span style="color:#16a34a">— Ceiling</span>
                    ${tuned.actual_points != null ? '<span style="color:#d97706">— Actual</span>' : ''}
                </div>
            </div>`;
            }
        }

        // Delta badge
        if (def) {
            const delta = (tuned.total - def.total).toFixed(2);
            const deltaClass = delta > 0 ? 'text-field-600 bg-field-50' : delta < 0 ? 'text-red-600 bg-red-50' : 'text-slate-500 bg-slate-50';
            html += `<p class="text-xs mb-3"><span class="px-2 py-0.5 rounded-full font-semibold ${deltaClass}">${delta > 0 ? '+' : ''}${delta} from default</span>`;
            if (tuned.actual_points !== null) {
                const tunedErr = Math.abs(tuned.total - tuned.actual_points).toFixed(2);
                const defErr = Math.abs(def.total - tuned.actual_points).toFixed(2);
                const better = tunedErr < defErr;
                html += ` <span class="px-2 py-0.5 rounded-full font-semibold ${better ? 'text-field-600 bg-field-50' : 'text-red-600 bg-red-50'}">Error: ${tunedErr} ${better ? '✓ better' : '✗ worse'} (was ${defErr})</span>`;
            }
            html += `</p>`;
        }

        // Waterfall chart (deterministic breakdown for transparency)
        html += `<div class="mt-2">
            <h5 class="text-xs font-bold text-slate-600 uppercase tracking-wider mb-2">Deterministic Formula Breakdown (Reference)</h5>
            ${renderWaterfall(steps)}
        </div>`;

        // Criteria values table
        html += `<div class="mt-4">
            <h5 class="text-xs font-bold text-slate-600 uppercase tracking-wider mb-2">Criteria Values</h5>
            <div class="max-h-48 overflow-y-auto">
                <table class="w-full text-xs">
                    <thead class="text-slate-500 uppercase tracking-wider">
                        <tr><th class="text-left py-1 px-2">Criteria</th><th class="text-right py-1 px-2">Value</th></tr>
                    </thead>
                    <tbody class="divide-y divide-slate-50">`;
        if (tuned.criteria) {
            for (const [k, v] of Object.entries(tuned.criteria)) {
                html += `<tr><td class="py-1 px-2 text-slate-600">${k}</td><td class="py-1 px-2 text-right font-mono text-slate-700">${typeof v === 'number' ? v.toFixed(2) : v}</td></tr>`;
            }
        }
        html += `</tbody></table></div></div></div>`;

        container.innerHTML = html;
    }

    // ── Waterfall Chart (SVG) ────────────────────────────────────────

    function renderWaterfall(steps) {
        if (!steps.length) return '<p class="text-xs text-slate-400">No breakdown data</p>';

        const barHeight = 22;
        const labelWidth = 160;
        const chartWidth = 300;
        const padding = 8;
        const totalHeight = steps.length * (barHeight + 4) + padding * 2;

        // Find the range of cumulative values for scaling
        let cumulative = 0;
        const cumulativeValues = [0];
        for (const s of steps) {
            cumulative += s.value;
            cumulativeValues.push(cumulative);
        }
        const minVal = Math.min(0, ...cumulativeValues);
        const maxVal = Math.max(0, ...cumulativeValues);
        const range = maxVal - minVal || 1;
        const scale = chartWidth / range;
        const zeroX = labelWidth + (-minVal * scale);

        let svg = `<svg width="100%" viewBox="0 0 ${labelWidth + chartWidth + 80} ${totalHeight}" class="font-sans">`;

        cumulative = 0;
        steps.forEach((step, i) => {
            const y = padding + i * (barHeight + 4);
            const prevCum = cumulative;
            cumulative += step.value;

            const barStart = labelWidth + (Math.min(prevCum, cumulative) - minVal) * scale;
            const barWidth = Math.abs(step.value) * scale;
            const isPositive = step.value >= 0;
            const isBaseline = step.coefficient_key === null;
            const color = isBaseline ? '#64748b' : isPositive ? '#16a34a' : '#dc2626';

            // Label
            svg += `<text x="${labelWidth - 4}" y="${y + barHeight / 2 + 4}" text-anchor="end" class="text-[10px]" fill="#64748b">${step.label}</text>`;
            // Bar
            svg += `<rect x="${barStart}" y="${y}" width="${Math.max(barWidth, 1)}" height="${barHeight}" rx="3" fill="${color}" opacity="0.8"/>`;
            // Value label
            svg += `<text x="${barStart + barWidth + 4}" y="${y + barHeight / 2 + 4}" class="text-[10px] font-semibold" fill="${color}">${step.value > 0 ? '+' : ''}${step.value}</text>`;
        });

        // Zero line
        svg += `<line x1="${zeroX}" y1="0" x2="${zeroX}" y2="${totalHeight}" stroke="#94a3b8" stroke-width="1" stroke-dasharray="3,3"/>`;

        // Total line
        const totalY = totalHeight - 2;
        svg += `<text x="${labelWidth - 4}" y="${totalY}" text-anchor="end" class="text-[11px] font-bold" fill="#334155">TOTAL</text>`;
        svg += `<text x="${labelWidth + 4}" y="${totalY}" class="text-[11px] font-bold" fill="#d47a1e">${cumulative.toFixed(2)} pts</text>`;

        svg += '</svg>';
        return svg;
    }

    // ── Render Bulk Result ───────────────────────────────────────────

    function renderBulkResult(data) {
        const container = document.getElementById('results-container');
        if (data.error) {
            container.innerHTML = `
                <div class="bg-amber-50 border border-amber-200 rounded-xl p-4">
                    <p class="text-sm text-amber-700 font-medium">${data.error}</p>
                </div>`;
            return;
        }

        const { tuned, default: def } = data;

        if (!tuned.sample_count) {
            container.innerHTML = `
                <div class="bg-amber-50 border border-amber-200 rounded-xl p-4">
                    <p class="text-sm text-amber-700 font-medium">No data available for this selection. Make sure players have game log data.</p>
                </div>`;
            return;
        }

        let html = `
        <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">
            <div class="flex items-center justify-between mb-3">
                <h4 class="text-sm font-bold text-slate-700">Backtest Results — ${data.position || 'All Positions'} ${data.year}${data.week ? ' Week ' + data.week : ''}</h4>
                <span class="text-[10px] font-semibold text-indigo-500 bg-indigo-50 px-2 py-0.5 rounded-full">Monte Carlo · 10k sims</span>
            </div>

            <!-- Summary Stats -->
            <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                <div class="bg-slate-50 rounded-lg p-3 text-center">
                    <p class="text-lg font-bold text-slate-700">${tuned.player_count}</p>
                    <p class="text-[10px] uppercase tracking-wider text-slate-400 font-semibold">Players</p>
                </div>
                <div class="bg-slate-50 rounded-lg p-3 text-center">
                    <p class="text-lg font-bold text-slate-700">${tuned.sample_count}</p>
                    <p class="text-[10px] uppercase tracking-wider text-slate-400 font-semibold">Samples</p>
                </div>
                <div class="bg-pigskin-50 rounded-lg p-3 text-center border border-pigskin-200">
                    <p class="text-lg font-bold text-pigskin-700">${tuned.mae}</p>
                    <p class="text-[10px] uppercase tracking-wider text-pigskin-500 font-semibold">Tuned MAE</p>
                </div>
                <div class="bg-slate-50 rounded-lg p-3 text-center">
                    <p class="text-lg font-bold text-slate-700">${def ? def.mae : '—'}</p>
                    <p class="text-[10px] uppercase tracking-wider text-slate-400 font-semibold">Default MAE</p>
                </div>
            </div>`;

        // MAE comparison badge
        if (def && tuned.mae !== null && def.mae !== null) {
            const improvement = (def.mae - tuned.mae).toFixed(2);
            const better = improvement > 0;
            html += `<p class="text-xs mb-3">
                <span class="px-2 py-0.5 rounded-full font-semibold ${better ? 'text-field-600 bg-field-50' : improvement < 0 ? 'text-red-600 bg-red-50' : 'text-slate-500 bg-slate-50'}">
                    MAE ${better ? 'improved' : improvement < 0 ? 'worsened' : 'unchanged'} by ${Math.abs(improvement)} pts
                </span>
                <span class="px-2 py-0.5 rounded-full font-semibold text-slate-500 bg-slate-50 ml-1">
                    RMSE: ${tuned.rmse} (was ${def.rmse})
                </span>
            </p>`;
        }

        // Top movers table
        if (tuned.top_movers && tuned.top_movers.length) {
            html += `<h5 class="text-xs font-bold text-slate-600 uppercase tracking-wider mb-2 mt-4">Biggest Errors (Top 10)</h5>
            <div class="max-h-64 overflow-y-auto">
                <table class="w-full text-xs">
                    <thead class="text-slate-500 uppercase tracking-wider bg-slate-50 sticky top-0">
                        <tr>
                            <th class="text-left py-1.5 px-2">Player</th>
                            <th class="text-center py-1.5 px-2">Pos</th>
                            ${tuned.top_movers[0].week !== undefined ? '<th class="text-center py-1.5 px-2">Wk</th>' : ''}
                            <th class="text-right py-1.5 px-2">MC Proj</th>
                            <th class="text-right py-1.5 px-2">Actual</th>
                            <th class="text-right py-1.5 px-2">Error</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-slate-50">`;
            for (const r of tuned.top_movers) {
                const errClass = Math.abs(r.error) > 10 ? 'text-red-600' : Math.abs(r.error) > 5 ? 'text-amber-600' : 'text-slate-600';
                html += `<tr>
                    <td class="py-1.5 px-2 text-slate-700 font-medium">${r.player_name}</td>
                    <td class="py-1.5 px-2 text-center text-slate-500">${r.position}</td>
                    ${r.week !== undefined ? `<td class="py-1.5 px-2 text-center text-slate-500">${r.week}</td>` : ''}
                    <td class="py-1.5 px-2 text-right font-mono">${r.projected}</td>
                    <td class="py-1.5 px-2 text-right font-mono">${r.actual}</td>
                    <td class="py-1.5 px-2 text-right font-mono font-semibold ${errClass}">${r.error > 0 ? '+' : ''}${r.error}</td>
                </tr>`;
            }
            html += `</tbody></table></div>`;
        }

        html += '</div>';
        container.innerHTML = html;
    }

    // ── Diagnose Data ────────────────────────────────────────────────

    async function runDiagnose() {
        if (mode !== 'single') {
            alert('Diagnostics are only available for single-player mode.');
            return;
        }
        const playerId = parseInt(document.getElementById('sim-player').value);
        const year = parseInt(document.getElementById('sim-year').value);
        const btn = document.getElementById('diag-btn');
        btn.disabled = true;
        btn.textContent = '⏳ Diagnosing…';

        try {
            const resp = await fetch(
                `/api/projection-tuner/diagnose/${playerId}?year=${year}`
            );
            const data = await resp.json();
            renderDiagnostics(data);
        } catch (err) {
            document.getElementById('results-container').innerHTML = `
                <div class="bg-red-50 border border-red-200 rounded-xl p-4">
                    <p class="text-sm text-red-700 font-medium">Diagnostic error: ${err.message}</p>
                </div>`;
        } finally {
            btn.disabled = false;
            btn.textContent = '🔍 Diagnose';
        }
    }

    function renderDiagnostics(data) {
        const container = document.getElementById('results-container');
        if (data.error) {
            container.innerHTML = `<div class="bg-amber-50 border border-amber-200 rounded-xl p-4"><p class="text-sm text-amber-700">${data.error}</p></div>`;
            return;
        }

        const { player, year, checks, criteria_sources, warnings } = data;

        const statusBadge = (s) => {
            if (s === 'ok') return '<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-field-100 text-field-700">✓ OK</span>';
            if (s === 'fallback') return '<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-100 text-amber-700">⚠ Fallback</span>';
            if (s === 'partial') return '<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-blue-100 text-blue-700">~ Partial</span>';
            return '<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-red-100 text-red-700">✗ Missing</span>';
        };

        let html = `<div class="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
            <div class="p-4 border-b border-slate-100 bg-slate-50 flex items-center justify-between">
                <h4 class="text-sm font-bold text-slate-700">🔍 Data Diagnostic — ${player.name} (${year})</h4>
                <span class="text-xs text-slate-500">${player.position} · ${player.nfl_team}</span>
            </div>`;

        // Warnings
        if (warnings && warnings.length) {
            html += `<div class="p-4 bg-red-50 border-b border-red-100 space-y-1">`;
            for (const w of warnings) {
                html += `<p class="text-xs text-red-700 font-medium">${w}</p>`;
            }
            html += `</div>`;
        }

        html += `<div class="p-4 grid grid-cols-1 md:grid-cols-2 gap-4">`;

        // Data source checks
        const {season_stats, game_logs, weekly_player_stats, nfl_team_stats, player_metadata} = checks;

        html += `<div>
            <h5 class="text-xs font-bold text-slate-600 uppercase tracking-wider mb-2">Data Sources</h5>
            <div class="space-y-2 text-xs">
                <div class="flex items-center justify-between p-2 rounded bg-slate-50">
                    <span class="font-semibold text-slate-700">Season Stats (${year})</span>
                    ${season_stats.found
                        ? `<span class="text-field-600 font-semibold">✓ Found — ${season_stats.games_played} games, avg ${season_stats.fantasy_points_avg} pts</span>`
                        : `<span class="text-red-600 font-semibold">✗ Not found</span>`}
                </div>
                <div class="p-2 rounded bg-slate-50">
                    <div class="flex items-center justify-between">
                        <span class="font-semibold text-slate-700">Game Logs (${year})</span>
                        ${game_logs.count > 0
                            ? `<span class="text-field-600 font-semibold">✓ ${game_logs.count} games, avg ${game_logs.fantasy_points_avg} pts</span>`
                            : `<span class="text-red-600 font-semibold">✗ No game logs</span>`}
                    </div>
                    ${game_logs.count > 0 ? `<p class="text-slate-400 mt-1">Weeks: ${game_logs.weeks.join(', ')}</p>` : ''}
                </div>
                <div class="p-2 rounded bg-slate-50">
                    <div class="flex items-center justify-between">
                        <span class="font-semibold text-slate-700">Weekly ESPN Stats</span>
                        ${weekly_player_stats.count > 0
                            ? `<span class="text-field-600 font-semibold">✓ ${weekly_player_stats.count} weeks, avg ${weekly_player_stats.actual_points_avg} pts</span>`
                            : `<span class="text-amber-600 font-semibold">⚠ No weekly stats</span>`}
                    </div>
                    ${weekly_player_stats.count > 0 ? `<p class="text-slate-400 mt-1">Weeks with data: ${weekly_player_stats.weeks_with_data.join(', ')}</p>` : ''}
                </div>
                <div class="flex items-center justify-between p-2 rounded bg-slate-50">
                    <span class="font-semibold text-slate-700">NFL Team Stats (${player.nfl_team})</span>
                    ${nfl_team_stats.found
                        ? `<span class="text-field-600 font-semibold">✓ ${nfl_team_stats.points_scored} pts, ${nfl_team_stats.total_yards} yds</span>`
                        : `<span class="text-red-600 font-semibold">✗ Not found</span>`}
                </div>
                <div class="flex items-center justify-between p-2 rounded bg-slate-50">
                    <span class="font-semibold text-slate-700">Player Metadata</span>
                    <span class="text-slate-600">Age: ${player_metadata.age}, Status: ${player_metadata.injury_status}</span>
                </div>
            </div>
        </div>`;

        // Criteria sources
        html += `<div>
            <h5 class="text-xs font-bold text-slate-600 uppercase tracking-wider mb-2">Criteria Data Sources</h5>
            <div class="space-y-1.5 text-xs">`;
        for (const [key, info] of Object.entries(criteria_sources)) {
            html += `<div class="flex items-center justify-between p-2 rounded bg-slate-50">
                <span class="text-slate-600 font-medium">${key.replace(/_/g, ' ')}</span>
                <div class="text-right">
                    ${statusBadge(info.status)}
                    <p class="text-[10px] text-slate-400 mt-0.5">${info.source}</p>
                </div>
            </div>`;
        }
        html += `</div></div>`;

        html += `</div></div>`;
        container.innerHTML = html;
    }

    // ── Type change handler ──────────────────────────────────────────

    document.addEventListener('DOMContentLoaded', () => {
        const typeSelect = document.getElementById('sim-type');
        if (typeSelect) {
            typeSelect.addEventListener('change', () => {
                const weekCtrl = document.getElementById('week-control');
                if (typeSelect.value === 'yearly') {
                    weekCtrl.classList.add('hidden');
                } else {
                    weekCtrl.classList.remove('hidden');
                }
            });
        }
    });

    // ── Criteria Grid ────────────────────────────────────────────────

    let gridData = null;
    let gridSortCol = null;
    let gridSortAsc = true;
    let gridRowData = {}; // player_id → row (holds histogram data for modal)

    const INVERTED_COLS = new Set(['injury_risk_score', 'opposing_defense_vs_position_rank']);

    // ── Monte Carlo Histogram Rendering ─────────────────────────────

    function renderHistogramSVG(histogram, mc, opts = {}) {
        if (!histogram || !histogram.length) return '<p class="text-xs text-slate-400">No histogram data</p>';
        const W = opts.width || 460;
        const H = opts.height || 140;
        const pad = { top: 20, right: 16, bottom: 28, left: 28 };
        const cW = W - pad.left - pad.right;
        const cH = H - pad.top - pad.bottom;
        const maxFreq = Math.max(...histogram.map(b => b.frequency));
        const minBin = histogram[0].bin_start;
        const maxBin = histogram[histogram.length - 1].bin_end;
        const bw = cW / histogram.length;
        const xScale = v => pad.left + (v - minBin) / (maxBin - minBin) * cW;
        const yScale = v => pad.top + cH - (v / maxFreq) * cH;

        let svg = `<svg width="100%" viewBox="0 0 ${W} ${H}" font-family="ui-sans-serif,system-ui,sans-serif">`;

        // Colored bars
        histogram.forEach((bin, i) => {
            const x = pad.left + i * bw;
            const bh = Math.max(1, (bin.frequency / maxFreq) * cH);
            const y = pad.top + cH - bh;
            let fill = '#818cf8'; // indigo-400 default
            if (bin.bin_end <= 8) fill = '#fca5a5';        // red-300 bust zone
            else if (bin.bin_start >= 25) fill = '#86efac'; // green-300 boom zone
            svg += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${Math.max(1, bw - 1).toFixed(1)}" height="${bh.toFixed(1)}" fill="${fill}" opacity="0.85" rx="1"/>`;
        });

        // Vertical marker lines
        const markers = [];
        if (mc) {
            markers.push({ val: mc.floor,    color: '#ef4444', label: `F:${mc.floor}` });
            markers.push({ val: mc.expected, color: '#4f46e5', label: `E:${mc.expected}`, bold: true });
            markers.push({ val: mc.ceiling,  color: '#16a34a', label: `C:${mc.ceiling}` });
            if (mc.actual != null) markers.push({ val: mc.actual, color: '#d97706', label: `A:${mc.actual}` });
        }
        markers.forEach(({ val, color, label, bold }) => {
            if (val < minBin - 1 || val > maxBin + 1) return;
            const x = xScale(val).toFixed(1);
            svg += `<line x1="${x}" y1="${pad.top}" x2="${x}" y2="${pad.top + cH}" stroke="${color}" stroke-width="${bold ? 2 : 1.5}" stroke-dasharray="4,2"/>`;
            svg += `<text x="${x}" y="${pad.top - 4}" text-anchor="middle" font-size="9" fill="${color}" font-weight="${bold ? 'bold' : 'normal'}">${label}</text>`;
        });

        // X-axis tick labels
        const ticks = [minBin, ...(mc ? [mc.floor, mc.expected, mc.ceiling] : []), maxBin];
        const seen = new Set();
        ticks.forEach(val => {
            const rounded = Math.round(val);
            if (seen.has(rounded) || val < minBin || val > maxBin) return;
            seen.add(rounded);
            const x = xScale(val).toFixed(1);
            svg += `<text x="${x}" y="${pad.top + cH + 16}" text-anchor="middle" font-size="9" fill="#94a3b8">${rounded}</text>`;
        });
        // Axis line
        svg += `<line x1="${pad.left}" y1="${pad.top + cH}" x2="${pad.left + cW}" y2="${pad.top + cH}" stroke="#e2e8f0" stroke-width="1"/>`;
        svg += '</svg>';
        return svg;
    }

    function renderSparklineSVG(histogram) {
        if (!histogram || !histogram.length) return '';
        const W = 56, H = 14;
        const maxFreq = Math.max(...histogram.map(b => b.frequency));
        const bw = W / histogram.length;
        let svg = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" class="inline-block align-middle ml-1.5" style="cursor:pointer">`;
        histogram.forEach((bin, i) => {
            const bh = Math.max(1, (bin.frequency / maxFreq) * H);
            let fill = '#818cf8';
            if (bin.bin_end <= 8) fill = '#fca5a5';
            else if (bin.bin_start >= 25) fill = '#86efac';
            svg += `<rect x="${(i * bw).toFixed(1)}" y="${(H - bh).toFixed(1)}" width="${Math.max(1, bw - 0.5).toFixed(1)}" height="${bh.toFixed(1)}" fill="${fill}" opacity="0.8"/>`;
        });
        svg += '</svg>';
        return svg;
    }

    function showHistogram(playerId) {
        const row = gridRowData[playerId];
        if (!row) return;
        const mc = {
            floor: row.mc_floor, expected: row.projected, ceiling: row.mc_ceiling,
            std_dev: row.mc_std_dev, boom_probability: row.mc_boom_pct,
            bust_probability: row.mc_bust_pct, actual: row.actual,
        };
        const existing = document.getElementById('mc-histogram-modal');
        if (existing) existing.remove();
        const modal = document.createElement('div');
        modal.id = 'mc-histogram-modal';
        modal.className = 'fixed inset-0 z-50 flex items-center justify-center';
        modal.style.background = 'rgba(0,0,0,0.45)';
        modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
        modal.innerHTML = `
        <div class="bg-white rounded-xl shadow-2xl p-5 w-full max-w-lg mx-4">
            <div class="flex items-center justify-between mb-3">
                <div>
                    <h3 class="text-sm font-bold text-slate-700">${row.player_name}</h3>
                    <p class="text-[11px] text-slate-400">${row.position} · ${row.nfl_team || ''} · Monte Carlo Distribution (10k sims)</p>
                </div>
                <button onclick="document.getElementById('mc-histogram-modal').remove()" class="text-slate-400 hover:text-slate-600 text-xl leading-none ml-4">×</button>
            </div>
            <div class="overflow-x-auto">${renderHistogramSVG(row.mc_histogram, mc)}</div>
            <div class="grid grid-cols-3 gap-2 mt-3 text-center">
                <div class="bg-red-50 rounded p-2">
                    <p class="text-xs font-bold text-red-600">${mc.floor ?? '—'}</p>
                    <p class="text-[10px] text-red-400">Floor (P10)</p>
                </div>
                <div class="bg-indigo-50 rounded p-2 border border-indigo-200">
                    <p class="text-xs font-bold text-indigo-700">${mc.expected ?? '—'}</p>
                    <p class="text-[10px] text-indigo-400">Expected</p>
                </div>
                <div class="bg-emerald-50 rounded p-2">
                    <p class="text-xs font-bold text-emerald-700">${mc.ceiling ?? '—'}</p>
                    <p class="text-[10px] text-emerald-400">Ceiling (P90)</p>
                </div>
            </div>
            <div class="grid grid-cols-3 gap-2 mt-2 text-center">
                <div class="bg-slate-50 rounded p-2">
                    <p class="text-xs font-bold text-slate-600">${mc.std_dev ?? '—'}</p>
                    <p class="text-[10px] text-slate-400">Std Dev</p>
                </div>
                <div class="bg-emerald-50 rounded p-2">
                    <p class="text-xs font-bold text-emerald-700">${mc.boom_probability != null ? (mc.boom_probability * 100).toFixed(1) + '%' : '—'}</p>
                    <p class="text-[10px] text-emerald-400">Boom (≥25)</p>
                </div>
                <div class="bg-red-50 rounded p-2">
                    <p class="text-xs font-bold text-red-600">${mc.bust_probability != null ? (mc.bust_probability * 100).toFixed(1) + '%' : '—'}</p>
                    <p class="text-[10px] text-red-400">Bust (≤8)</p>
                </div>
            </div>
            ${mc.actual != null ? `<p class="text-[11px] text-amber-600 text-center mt-2">● Actual score: <strong>${mc.actual}</strong></p>` : ''}
        </div>`;
        document.body.appendChild(modal);
    }

    function normalizeVal(val, s) {
        if (s.max === s.min) return 0.5;
        return Math.max(0, Math.min(1, (val - s.min) / (s.max - s.min)));
    }

    function heatBg(norm, inverted) {
        const t = inverted ? 1 - norm : norm;
        return `hsl(${Math.round(t * 120)},60%,92%)`;
    }

    function fmtVal(v) {
        if (v === null || v === undefined) return '<span class="text-slate-300">—</span>';
        const n = parseFloat(v);
        if (isNaN(n)) return String(v);
        const cls = n === 0 ? ' class="text-slate-300 font-medium"' : '';
        return `<span${cls}>${n.toFixed(2)}</span>`;
    }

    function loadGrid() {
        const position = document.getElementById('grid-position').value;
        const year = parseInt(document.getElementById('grid-year').value, 10);
        const type = document.getElementById('grid-type').value;
        const weekEl = document.getElementById('grid-week');
        const limitEl = document.getElementById('grid-limit');
        const week = type === 'weekly' && weekEl ? parseInt(weekEl.value, 10) : null;
        const limit = limitEl ? parseInt(limitEl.value, 10) : 50;

        const btn = document.getElementById('grid-load-btn');
        const spinner = document.getElementById('grid-spinner');
        const container = document.getElementById('grid-container');

        btn.disabled = true;
        spinner.classList.remove('hidden');
        container.innerHTML = '<p class="text-xs text-slate-400 italic p-4">Loading…</p>';

        const body = { year, projection_type: type, limit };
        if (position) body.position = position;
        if (week !== null) body.week = week;

        fetch('/api/projection-tuner/criteria-grid', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        })
        .then(r => r.json())
        .then(data => {
            gridData = data;
            gridSortCol = null;
            renderGrid(data);
        })
        .catch(err => {
            container.innerHTML = `<p class="text-xs text-red-500 p-4">Error: ${err}</p>`;
        })
        .finally(() => {
            btn.disabled = false;
            spinner.classList.add('hidden');
        });
    }

    function renderGrid(data) {
        const container = document.getElementById('grid-container');
        if (!data || !data.rows || data.rows.length === 0) {
            container.innerHTML = '<p class="text-xs text-slate-400 italic p-4">No players found.</p>';
            return;
        }

        const rows = [...data.rows];
        const MC_NUM_COLS = ['projected', 'mc_floor', 'mc_ceiling', 'mc_boom_pct', 'mc_bust_pct', 'actual'];
        if (gridSortCol) {
            rows.sort((a, b) => {
                let va, vb;
                if (['player_name', 'position', 'nfl_team'].includes(gridSortCol)) {
                    va = a[gridSortCol] || ''; vb = b[gridSortCol] || '';
                    return gridSortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
                }
                if (MC_NUM_COLS.includes(gridSortCol)) {
                    va = a[gridSortCol] ?? -Infinity; vb = b[gridSortCol] ?? -Infinity;
                } else {
                    va = a.criteria[gridSortCol] ?? -Infinity; vb = b.criteria[gridSortCol] ?? -Infinity;
                }
                return gridSortAsc ? va - vb : vb - va;
            });
        }

        const cols = data.criteria_cols;
        const stats = data.col_stats;
        const colLabel = c => c.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
        const sortIcon = col => {
            if (gridSortCol !== col) return '<span class="text-slate-300 ml-1">⇅</span>';
            return gridSortAsc ? '<span class="text-pigskin-600 ml-1">↑</span>' : '<span class="text-pigskin-600 ml-1">↓</span>';
        };

        const thCls = 'sticky top-0 bg-white px-3 py-2 text-left text-[10px] font-semibold text-slate-500 uppercase tracking-wider whitespace-nowrap border-b border-r border-slate-200 cursor-pointer hover:bg-slate-50 select-none';
        const th1Cls = 'sticky top-0 left-0 z-20 bg-white px-3 py-2 text-left text-[10px] font-semibold text-slate-500 uppercase tracking-wider whitespace-nowrap border-b border-r border-slate-200 cursor-pointer hover:bg-slate-50 select-none';
        const thMcCls = 'sticky top-0 bg-indigo-50 px-3 py-2 text-left text-[10px] font-semibold text-indigo-600 uppercase tracking-wider whitespace-nowrap border-b border-r border-indigo-200 cursor-pointer hover:bg-indigo-100 select-none';
        const tdCls = 'px-3 py-1.5 border-b border-r border-slate-100 whitespace-nowrap text-right';
        const td1Cls = 'sticky left-0 bg-white px-3 py-1.5 border-b border-r border-slate-200 whitespace-nowrap font-medium text-slate-700';
        const tdMcCls = 'px-3 py-1.5 border-b border-r border-indigo-50 whitespace-nowrap text-right';

        const fmtPct = v => {
            if (v === null || v === undefined) return '<span class="text-slate-300">—</span>';
            return `<span>${(v * 100).toFixed(1)}%</span>`;
        };

        let html = `<table class="min-w-full text-xs border-collapse"><thead><tr>
            <th class="${th1Cls}" onclick="TunerApp.sortGrid('player_name')">Player ${sortIcon('player_name')}</th>
            <th class="${thCls}" onclick="TunerApp.sortGrid('position')">Pos ${sortIcon('position')}</th>
            <th class="${thCls}" onclick="TunerApp.sortGrid('nfl_team')">Team ${sortIcon('nfl_team')}</th>
            <th class="${thMcCls}" onclick="TunerApp.sortGrid('projected')" title="Monte Carlo Expected Value">MC Proj ${sortIcon('projected')}</th>
            <th class="${thMcCls}" onclick="TunerApp.sortGrid('mc_floor')" title="Monte Carlo 10th Percentile">Floor ${sortIcon('mc_floor')}</th>
            <th class="${thMcCls}" onclick="TunerApp.sortGrid('mc_ceiling')" title="Monte Carlo 90th Percentile">Ceiling ${sortIcon('mc_ceiling')}</th>
            <th class="${thMcCls}" onclick="TunerApp.sortGrid('mc_boom_pct')" title="Probability of scoring ≥25 pts">Boom% ${sortIcon('mc_boom_pct')}</th>
            <th class="${thMcCls}" onclick="TunerApp.sortGrid('mc_bust_pct')" title="Probability of scoring ≤8 pts">Bust% ${sortIcon('mc_bust_pct')}</th>
            <th class="${thCls}" onclick="TunerApp.sortGrid('actual')">Actual ${sortIcon('actual')}</th>
            ${cols.map(c => `<th class="${thCls}" onclick="TunerApp.sortGrid('${c}')" title="${c}">${colLabel(c)} ${sortIcon(c)}</th>`).join('')}
        </tr></thead><tbody>`;

        gridRowData = {};
        for (const row of rows) {
            gridRowData[row.player_id] = row;
            const sparkline = row.mc_histogram ? renderSparklineSVG(row.mc_histogram) : '';
            html += `<tr class="hover:bg-slate-50/50 transition-colors">
                <td class="${td1Cls}">${row.player_name}</td>
                <td class="${tdCls} text-center">${row.position}</td>
                <td class="${tdCls} text-center text-slate-500">${row.nfl_team || '—'}</td>
                <td class="${tdMcCls} font-semibold text-indigo-700 cursor-pointer" onclick="TunerApp.showHistogram(${row.player_id})" title="Click to view MC distribution">${fmtVal(row.projected)}${sparkline}</td>
                <td class="${tdMcCls} text-slate-600">${fmtVal(row.mc_floor)}</td>
                <td class="${tdMcCls} text-slate-600">${fmtVal(row.mc_ceiling)}</td>
                <td class="${tdMcCls}">${fmtPct(row.mc_boom_pct)}</td>
                <td class="${tdMcCls}">${fmtPct(row.mc_bust_pct)}</td>
                <td class="${tdCls} text-slate-500">${fmtVal(row.actual)}</td>`;
            for (const col of cols) {
                const val = row.criteria[col];
                const s = stats[col];
                let bg = '';
                if (val !== null && val !== undefined && s && s.max !== s.min) {
                    bg = ` style="background:${heatBg(normalizeVal(val, s), INVERTED_COLS.has(col))}"`;
                }
                html += `<td class="${tdCls}"${bg}>${fmtVal(val)}</td>`;
            }
            html += `</tr>`;
        }

        html += '</tbody></table>';
        const note = data.truncated
            ? `<p class="text-[10px] text-amber-600 px-3 py-1 border-t border-slate-100">Showing first ${data.player_count} players — narrow by position.</p>`
            : `<p class="text-[10px] text-slate-400 px-3 py-1 border-t border-slate-100">${data.player_count} players loaded · Monte Carlo (10k sims)</p>`;
        container.innerHTML = html + note;
    }

    function sortGrid(col) {
        if (gridSortCol === col) { gridSortAsc = !gridSortAsc; }
        else { gridSortCol = col; gridSortAsc = ['player_name', 'position', 'nfl_team'].includes(col); }
        if (gridData) renderGrid(gridData);
    }

    // ── Algorithm Tuning (background runs) ───────────────────────────

    function numFmt(v, digits = 2) {
        if (v === null || v === undefined) return '—';
        const n = Number(v);
        return Number.isFinite(n) ? n.toFixed(digits) : '—';
    }

    function renderAlgorithmStatus(payload, tone = 'info') {
        const el = document.getElementById('alg-job-status');
        if (!el) return;
        const styles = {
            info: 'text-slate-600',
            success: 'text-field-700',
            error: 'text-red-600',
        };
        const cls = styles[tone] || styles.info;
        const pct = payload && payload.progress_pct !== undefined ? ` (${payload.progress_pct}%)` : '';
        const msg = payload && payload.message ? payload.message : '';
        const status = payload && payload.status ? payload.status : 'idle';
        const runId = payload && payload.run_id ? ` · run ${payload.run_id}` : '';
        el.className = `text-xs ${cls}`;
        el.textContent = `${status}${pct}${msg ? ` · ${msg}` : ''}${runId}`;
    }

    function updateAlgorithmRunDetailLink(runId) {
        const link = document.getElementById('alg-run-detail-link');
        if (!link) return;
        if (!runId) {
            link.href = '#';
            link.className = 'mt-2 inline-flex items-center text-xs font-semibold text-slate-400 pointer-events-none';
            return;
        }
        link.href = `/projection-tuner/runs/${encodeURIComponent(runId)}`;
        link.className = 'mt-2 inline-flex items-center text-xs font-semibold text-pigskin-600 hover:text-pigskin-700';
    }

    function algorithmRequestBody() {
        const year = parseInt(document.getElementById('alg-year').value, 10);
        const weeksRaw = (document.getElementById('alg-weeks').value || '').trim();
        const maxVariations = parseInt(document.getElementById('alg-max-variations').value, 10);
        const topN = parseInt(document.getElementById('alg-top-n').value, 10);
        const posSelect = document.getElementById('alg-positions');
        const positions = posSelect
            ? Array.from(posSelect.selectedOptions).map(o => o.value).filter(Boolean)
            : [];

        const body = {
            year: year,
            max_variations: maxVariations,
            top_n: topN,
        };
        if (positions.length) body.positions = positions;
        if (weeksRaw) {
            const parsedWeeks = weeksRaw
                .split(',')
                .map(w => parseInt(w.trim(), 10))
                .filter(w => Number.isFinite(w));
            if (parsedWeeks.length) body.weeks = parsedWeeks;
        }
        return body;
    }

    async function runAlgorithmTuning() {
        const btn = document.getElementById('alg-run-btn');
        if (!btn) return;
        btn.disabled = true;
        renderAlgorithmStatus({ status: 'submitting', progress_pct: 0, message: 'Submitting run request' });

        try {
            const resp = await fetch('/api/projection-tuner/algorithm/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(algorithmRequestBody()),
            });
            const data = await resp.json();
            if (data.error) {
                renderAlgorithmStatus({ status: 'error', progress_pct: 0, message: data.error }, 'error');
                btn.disabled = false;
                return;
            }
            renderAlgorithmStatus(data, 'info');
            await pollAlgorithmJob(data.job_id);
        } catch (err) {
            renderAlgorithmStatus({ status: 'error', progress_pct: 0, message: err.message }, 'error');
            btn.disabled = false;
        }
    }

    async function pollAlgorithmJob(jobId) {
        if (!jobId) return;
        if (algorithmPollTimer) {
            clearInterval(algorithmPollTimer);
            algorithmPollTimer = null;
        }

        const runBtn = document.getElementById('alg-run-btn');
        const pollOnce = async () => {
            try {
                const resp = await fetch(`/api/projection-tuner/algorithm/jobs/${encodeURIComponent(jobId)}`);
                const data = await resp.json();
                if (data.error) {
                    renderAlgorithmStatus({ status: 'error', progress_pct: 0, message: data.error }, 'error');
                    if (runBtn) runBtn.disabled = false;
                    if (algorithmPollTimer) {
                        clearInterval(algorithmPollTimer);
                        algorithmPollTimer = null;
                    }
                    return;
                }

                const tone = data.status === 'completed'
                    ? 'success'
                    : data.status === 'failed'
                        ? 'error'
                        : 'info';
                renderAlgorithmStatus(data, tone);

                if (data.status === 'completed' || data.status === 'failed') {
                    if (algorithmPollTimer) {
                        clearInterval(algorithmPollTimer);
                        algorithmPollTimer = null;
                    }
                    if (runBtn) runBtn.disabled = false;

                    if (data.status === 'completed') {
                        renderAlgorithmResult(data);
                        await loadAlgorithmHistory(data.run_id || null);
                    }
                }
            } catch (err) {
                renderAlgorithmStatus({ status: 'error', progress_pct: 0, message: err.message }, 'error');
                if (runBtn) runBtn.disabled = false;
                if (algorithmPollTimer) {
                    clearInterval(algorithmPollTimer);
                    algorithmPollTimer = null;
                }
            }
        };

        await pollOnce();
        algorithmPollTimer = setInterval(() => {
            pollOnce();
        }, 2000);
    }

    function openImportModal() {
        const modal = document.getElementById('import-job-modal');
        if (modal) modal.classList.remove('hidden');
    }

    function dismissImportModal() {
        const modal = document.getElementById('import-job-modal');
        if (modal) modal.classList.add('hidden');
    }

    function renderImportJob(job) {
        openImportModal();
        const messageEl = document.getElementById('import-job-message');
        const summaryEl = document.getElementById('import-job-summary');
        const badgeEl = document.getElementById('import-job-status-badge');
        const progressTextEl = document.getElementById('import-job-progress-text');
        const progressBarEl = document.getElementById('import-job-progress-bar');
        const logsEl = document.getElementById('import-job-logs');

        const status = job?.status || 'queued';
        const progressPct = Number.isFinite(job?.progress_pct) ? job.progress_pct : 0;
        const badgeClass = status === 'completed'
            ? 'inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-700'
            : status === 'failed'
                ? 'inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-red-100 text-red-700'
                : 'inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-violet-100 text-violet-700';

        if (messageEl) messageEl.textContent = job?.message || 'Working…';
        if (summaryEl) summaryEl.textContent = job?.summary || '';
        if (badgeEl) {
            badgeEl.className = badgeClass;
            badgeEl.textContent = status.charAt(0).toUpperCase() + status.slice(1);
        }
        if (progressTextEl) progressTextEl.textContent = `${progressPct}%`;
        if (progressBarEl) progressBarEl.style.width = `${Math.max(0, Math.min(100, progressPct))}%`;
        if (logsEl) {
            const nextText = Array.isArray(job?.logs) && job.logs.length
                ? job.logs.join('\n')
                : 'Waiting to start…';
            const shouldStick = Math.abs(logsEl.scrollHeight - logsEl.scrollTop - logsEl.clientHeight) < 24;
            logsEl.textContent = nextText;
            if (shouldStick || status === 'completed' || status === 'failed') {
                logsEl.scrollTop = logsEl.scrollHeight;
            }
        }
    }

    async function pollImportJob(jobId) {
        if (!jobId) return;
        if (importPollTimer) {
            clearInterval(importPollTimer);
            importPollTimer = null;
        }

        const btn = document.getElementById('import-espn-btn');
        const spinner = document.getElementById('import-espn-spinner');
        const status = document.getElementById('import-status');

        const pollOnce = async () => {
            try {
                const resp = await fetch(`/api/projection-tuner/import-jobs/${encodeURIComponent(jobId)}`);
                const data = await resp.json();
                if (data.error) {
                    renderImportJob({ status: 'failed', progress_pct: 0, message: data.error, logs: [data.error] });
                    if (status) {
                        status.textContent = `✗ ${data.error}`;
                        status.className = 'text-xs text-red-600 font-semibold';
                    }
                    if (btn) btn.disabled = false;
                    if (spinner) spinner.classList.add('hidden');
                    if (importPollTimer) {
                        clearInterval(importPollTimer);
                        importPollTimer = null;
                    }
                    return;
                }

                renderImportJob(data);
                if (status) {
                    status.textContent = data.status === 'completed'
                        ? `✓ ${data.summary || data.message}`
                        : data.status === 'failed'
                            ? `✗ ${data.message}`
                            : `${data.progress_pct || 0}% — ${data.message}`;
                    status.className = data.status === 'completed'
                        ? 'text-xs text-emerald-600 font-semibold'
                        : data.status === 'failed'
                            ? 'text-xs text-red-600 font-semibold'
                            : 'text-xs text-slate-500';
                }

                if (data.status === 'completed' || data.status === 'failed') {
                    if (importPollTimer) {
                        clearInterval(importPollTimer);
                        importPollTimer = null;
                    }
                    if (btn) btn.disabled = false;
                    if (spinner) spinner.classList.add('hidden');

                    if (data.status === 'completed') {
                        const gridContainer = document.getElementById('grid-container');
                        if (gridContainer && !gridContainer.querySelector('p.italic')) {
                            loadGrid();
                        }
                    }
                }
            } catch (err) {
                renderImportJob({ status: 'failed', progress_pct: 0, message: err.message, logs: [err.message] });
                if (status) {
                    status.textContent = `✗ Network error: ${err.message}`;
                    status.className = 'text-xs text-red-600 font-semibold';
                }
                if (btn) btn.disabled = false;
                if (spinner) spinner.classList.add('hidden');
                if (importPollTimer) {
                    clearInterval(importPollTimer);
                    importPollTimer = null;
                }
            }
        };

        await pollOnce();
        importPollTimer = setInterval(() => {
            pollOnce();
        }, 1500);
    }

    function renderAlgorithmResult(data) {
        const container = document.getElementById('alg-results');
        if (!container) return;
        if (!data || data.error) {
            container.innerHTML = `<p class="text-xs text-red-600">${data && data.error ? data.error : 'Unable to render run output.'}</p>`;
            return;
        }

        const summary = data.summary || {};
        const visuals = data.visuals || {};
        const kpi = visuals.kpis || {};
        const topVariations = visuals.top_variations || [];
        const perPos = visuals.per_position_comparison || [];
        const coefficientChanges = visuals.coefficient_changes || [];
        const runDetailLink = summary.run_id
            ? `<a href="/projection-tuner/runs/${encodeURIComponent(summary.run_id)}" class="text-xs font-semibold text-pigskin-600 hover:text-pigskin-700">View detail page</a>`
            : '';
        const acceptBtn = summary.run_id
            ? `<button onclick="TunerApp.acceptFromSelectedRun()" class="px-3 py-1.5 text-xs font-semibold text-white bg-emerald-600 hover:bg-emerald-700 rounded-lg transition-colors">✓ Accept as Master</button>`
            : '';

        let html = `<div class="space-y-3">
            <div class="flex flex-wrap items-center justify-between gap-2">
                <div>
                    <p class="text-xs text-slate-500 uppercase tracking-wider font-semibold">Run ${summary.run_id || '—'}</p>
                    <p class="text-xs text-slate-400">Year ${summary.year || '—'} · ${summary.sample_count || 0} samples · ${summary.player_count || 0} players</p>
                </div>
                <div class="flex items-center gap-3">
                    ${acceptBtn}
                    ${runDetailLink}
                    <p class="text-xs text-slate-400">${summary.timestamp || ''}</p>
                </div>
            </div>

            <div class="grid grid-cols-2 md:grid-cols-5 gap-2">
                <div class="bg-white border border-slate-200 rounded-lg p-2 text-center">
                    <p class="text-[10px] uppercase text-slate-400">Default MAE</p>
                    <p class="text-sm font-bold text-slate-700">${numFmt(kpi.default_mae, 3)}</p>
                </div>
                <div class="bg-white border border-pigskin-200 rounded-lg p-2 text-center">
                    <p class="text-[10px] uppercase text-pigskin-500">Tuned MAE</p>
                    <p class="text-sm font-bold text-pigskin-700">${numFmt(kpi.tuned_mae, 3)}</p>
                </div>
                <div class="bg-white border border-field-200 rounded-lg p-2 text-center">
                    <p class="text-[10px] uppercase text-field-500">MAE Reduction</p>
                    <p class="text-sm font-bold text-field-700">${numFmt(kpi.mae_reduction, 3)}</p>
                </div>
                <div class="bg-white border border-slate-200 rounded-lg p-2 text-center">
                    <p class="text-[10px] uppercase text-slate-400">Improvement %</p>
                    <p class="text-sm font-bold text-slate-700">${numFmt(kpi.mae_improvement_pct, 2)}%</p>
                </div>
                <div class="bg-white border border-slate-200 rounded-lg p-2 text-center">
                    <p class="text-[10px] uppercase text-slate-400">Variations Tested</p>
                    <p class="text-sm font-bold text-slate-700">${summary.variations_tested || kpi.variations_tested || 0}</p>
                </div>
            </div>`;

        if (perPos.length) {
            html += `<div>
                <h5 class="text-[11px] font-bold uppercase tracking-wider text-slate-600 mb-1.5">Per-position MAE</h5>
                <div class="overflow-x-auto">
                    <table class="w-full text-xs bg-white border border-slate-200 rounded-lg overflow-hidden">
                        <thead class="bg-slate-50 text-slate-500 uppercase tracking-wider">
                            <tr>
                                <th class="text-left px-2 py-1.5">Pos</th>
                                <th class="text-right px-2 py-1.5">Default</th>
                                <th class="text-right px-2 py-1.5">Tuned</th>
                                <th class="text-right px-2 py-1.5">Reduction</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-slate-100">
                            ${perPos.map(r => `
                                <tr>
                                    <td class="px-2 py-1.5 font-semibold text-slate-700">${r.position}</td>
                                    <td class="px-2 py-1.5 text-right">${numFmt(r.default_mae, 3)}</td>
                                    <td class="px-2 py-1.5 text-right">${numFmt(r.tuned_mae, 3)}</td>
                                    <td class="px-2 py-1.5 text-right font-semibold ${r.mae_reduction >= 0 ? 'text-field-700' : 'text-red-600'}">${numFmt(r.mae_reduction, 3)}</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>`;
        }

        if (topVariations.length) {
            html += `<div>
                <h5 class="text-[11px] font-bold uppercase tracking-wider text-slate-600 mb-1.5">Top Variations</h5>
                <div class="overflow-x-auto max-h-56">
                    <table class="w-full text-xs bg-white border border-slate-200 rounded-lg overflow-hidden">
                        <thead class="bg-slate-50 text-slate-500 uppercase tracking-wider sticky top-0">
                            <tr>
                                <th class="text-left px-2 py-1.5">Rank</th>
                                <th class="text-right px-2 py-1.5">MAE</th>
                                <th class="text-right px-2 py-1.5">RMSE</th>
                                <th class="text-right px-2 py-1.5">Samples</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-slate-100">
                            ${topVariations.map(r => `
                                <tr>
                                    <td class="px-2 py-1.5 font-semibold text-slate-700">#${r.rank}</td>
                                    <td class="px-2 py-1.5 text-right">${numFmt(r.mae, 3)}</td>
                                    <td class="px-2 py-1.5 text-right">${numFmt(r.rmse, 3)}</td>
                                    <td class="px-2 py-1.5 text-right text-slate-600">${r.sample_count}</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>`;
        }

        if (coefficientChanges.length) {
            html += `<div>
                <h5 class="text-[11px] font-bold uppercase tracking-wider text-slate-600 mb-1.5">Coefficient Changes</h5>
                <div class="overflow-x-auto max-h-56">
                    <table class="w-full text-xs bg-white border border-slate-200 rounded-lg overflow-hidden">
                        <thead class="bg-slate-50 text-slate-500 uppercase tracking-wider sticky top-0">
                            <tr>
                                <th class="text-left px-2 py-1.5">Coefficient</th>
                                <th class="text-right px-2 py-1.5">Default</th>
                                <th class="text-right px-2 py-1.5">Tuned</th>
                                <th class="text-right px-2 py-1.5">Change %</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-slate-100">
                            ${coefficientChanges.map(r => `
                                <tr>
                                    <td class="px-2 py-1.5 text-slate-700">${r.key}</td>
                                    <td class="px-2 py-1.5 text-right">${numFmt(r.default, 4)}</td>
                                    <td class="px-2 py-1.5 text-right">${numFmt(r.tuned, 4)}</td>
                                    <td class="px-2 py-1.5 text-right font-semibold ${Number(r.change_pct) >= 0 ? 'text-field-700' : 'text-red-600'}">${numFmt(r.change_pct, 2)}%</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>`;
        }

        html += '</div>';
        container.innerHTML = html;
    }

    async function loadAlgorithmHistory(selectedRunId = null) {
        const select = document.getElementById('alg-history-select');
        if (!select) return;
        try {
            const resp = await fetch('/api/projection-tuner/algorithm/runs?limit=50');
            const data = await resp.json();
            if (data.error) {
                select.innerHTML = `<option value="">${data.error}</option>`;
                return;
            }

            const runs = data.runs || [];
            if (!runs.length) {
                select.innerHTML = '<option value="">No saved runs</option>';
                updateAlgorithmRunDetailLink(null);
                return;
            }

            const previous = select.value;
            select.innerHTML = runs.map(r => {
                const stamp = r.timestamp ? new Date(r.timestamp).toLocaleString() : '';
                return `<option value="${r.run_id}">${r.year} · ${stamp} · MAE ${numFmt(r.best_mae, 3)}</option>`;
            }).join('');

            const targetRun = selectedRunId || previous || runs[0].run_id;
            if (targetRun) {
                select.value = targetRun;
                updateAlgorithmRunDetailLink(targetRun);
                await loadAlgorithmRun(targetRun);
            }
        } catch (err) {
            select.innerHTML = `<option value="">Failed to load history (${err.message})</option>`;
            updateAlgorithmRunDetailLink(null);
        }
    }

    async function loadAlgorithmRun(runId) {
        const container = document.getElementById('alg-results');
        if (!container || !runId) return;
        updateAlgorithmRunDetailLink(runId);
        container.innerHTML = '<p class="text-xs text-slate-400 italic">Loading run details…</p>';
        try {
            const resp = await fetch(`/api/projection-tuner/algorithm/runs/${encodeURIComponent(runId)}`);
            const data = await resp.json();
            renderAlgorithmResult(data);
        } catch (err) {
            container.innerHTML = `<p class="text-xs text-red-600">Failed to load run: ${err.message}</p>`;
        }
    }

    function loadAlgorithmRunFromHistory() {
        const select = document.getElementById('alg-history-select');
        if (!select || !select.value) return;
        updateAlgorithmRunDetailLink(select.value);
        loadAlgorithmRun(select.value);
    }

    function initAlgorithmTuning() {
        updateAlgorithmRunDetailLink(null);
        loadAlgorithmHistory();
    }

    // ── Grid type visibility ──────────────────────────────────────────

    function initGridTypeToggle() {
        const gridType = document.getElementById('grid-type');
        if (!gridType) return;
        gridType.addEventListener('change', () => {
            const wrap = document.getElementById('grid-week-wrap');
            if (wrap) wrap.style.display = gridType.value === 'yearly' ? 'none' : '';
        });
    }

    document.addEventListener('DOMContentLoaded', initGridTypeToggle);
    document.addEventListener('DOMContentLoaded', initAlgorithmTuning);

    // ── Import NFL Data ───────────────────────────────────────────────

    async function importNFLData() {
        const btn = document.getElementById('import-btn');
        const spinner = document.getElementById('import-spinner');
        const status = document.getElementById('import-status');
        const yearSelect = document.getElementById('import-year');
        const year = yearSelect ? parseInt(yearSelect.value) : new Date().getFullYear() - 1;

        btn.disabled = true;
        spinner.classList.remove('hidden');
        status.textContent = `Importing ${year} data from nfl_data_py…`;
        status.className = 'text-xs text-slate-500';

        try {
            const resp = await fetch(`/api/projection-tuner/import-nfl-data?year=${year}`, {
                method: 'POST',
            });
            const data = await resp.json();
            if (data.status === 'ok') {
                status.textContent = `✓ ${data.message}`;
                status.className = 'text-xs text-emerald-600 font-semibold';
                // Reload the grid automatically if it was already loaded
                const gridContainer = document.getElementById('grid-container');
                if (gridContainer && !gridContainer.querySelector('p.italic')) {
                    loadGrid();
                }
            } else {
                status.textContent = `✗ ${data.message}`;
                status.className = 'text-xs text-red-600 font-semibold';
            }
        } catch (err) {
            status.textContent = `✗ Network error: ${err.message}`;
            status.className = 'text-xs text-red-600 font-semibold';
        } finally {
            btn.disabled = false;
            spinner.classList.add('hidden');
        }
    }

    async function importESPNPlayers() {
        const btn = document.getElementById('import-espn-btn');
        const spinner = document.getElementById('import-espn-spinner');
        const status = document.getElementById('import-status');
        const yearSelect = document.getElementById('import-year');
        const year = yearSelect ? parseInt(yearSelect.value) : new Date().getFullYear() - 1;

        btn.disabled = true;
        spinner.classList.remove('hidden');
        status.textContent = `Queueing ESPN full-history import for ${year}…`;
        status.className = 'text-xs text-slate-500';
        renderImportJob({
            status: 'queued',
            progress_pct: 0,
            message: `Queueing ESPN full-history import for ${year}…`,
            logs: [`Preparing import request for ${year}…`],
        });

        try {
            const resp = await fetch(`/api/projection-tuner/import-relevant-players?year=${year}`, {
                method: 'POST',
            });
            const data = await resp.json();
            if (data.error || data.status === 'error') {
                const message = data.error || data.message || 'Import failed';
                status.textContent = `✗ ${message}`;
                status.className = 'text-xs text-red-600 font-semibold';
                renderImportJob({ status: 'failed', progress_pct: 0, message, logs: [message] });
                btn.disabled = false;
                spinner.classList.add('hidden');
            } else {
                status.textContent = `${data.progress_pct || 0}% — ${data.message || 'Queued'}`;
                status.className = 'text-xs text-slate-500';
                renderImportJob(data);
                await pollImportJob(data.job_id);
            }
        } catch (err) {
            status.textContent = `✗ Network error: ${err.message}`;
            status.className = 'text-xs text-red-600 font-semibold';
            renderImportJob({ status: 'failed', progress_pct: 0, message: err.message, logs: [err.message] });
            btn.disabled = false;
            spinner.classList.add('hidden');
        }
    }

    async function computeFromLogs() {
        const btn = document.getElementById('compute-btn');
        const spinner = document.getElementById('compute-spinner');
        const status = document.getElementById('import-status');
        const yearSelect = document.getElementById('import-year');
        const year = yearSelect ? parseInt(yearSelect.value) : new Date().getFullYear() - 1;

        btn.disabled = true;
        spinner.classList.remove('hidden');
        status.textContent = `Computing season stats from ${year} game logs…`;
        status.className = 'text-xs text-slate-500';

        try {
            const resp = await fetch(`/api/projection-tuner/compute-season-stats?year=${year}`, {
                method: 'POST',
            });
            const data = await resp.json();
            if (data.status === 'ok') {
                status.textContent = `✓ ${data.message}`;
                status.className = 'text-xs text-emerald-600 font-semibold';
                const gridContainer = document.getElementById('grid-container');
                if (gridContainer && !gridContainer.querySelector('p.italic')) {
                    loadGrid();
                }
            } else {
                status.textContent = `✗ ${data.message}`;
                status.className = 'text-xs text-red-600 font-semibold';
            }
        } catch (err) {
            status.textContent = `✗ Network error: ${err.message}`;
            status.className = 'text-xs text-red-600 font-semibold';
        } finally {
            btn.disabled = false;
            spinner.classList.add('hidden');
        }
    }

// ── MC Parameters Banner Init ────────────────────────────────────────

    function loadMasterCoefficientsBanner() {
        // Legacy stub — MC params banner is always visible and updated by markModified()
        updateMCParamsBanner();
    }

    document.addEventListener('DOMContentLoaded', updateMCParamsBanner);

// ── Public API ──────────────────────────────────────────────────────── ───────────────────────────────────────────────────

    return {
        syncInput,
        syncSlider,
        resetAll,
        resetGroup,
        setMode,
        filterPlayers,
        getMCParams,
        runSimulation,
        runDiagnose,
        loadGrid,
        sortGrid,
        showHistogram,
        runAlgorithmTuning,
        loadAlgorithmHistory,
        loadAlgorithmRunFromHistory,
        importNFLData,
        importESPNPlayers,
        dismissImportModal,
        computeFromLogs,
    };
})();

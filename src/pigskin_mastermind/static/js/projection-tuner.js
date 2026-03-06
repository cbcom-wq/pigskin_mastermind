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
    let activeEditorPosition = 'default'; // 'default' | 'QB' | 'RB' | 'WR' | 'TE' | 'K' | 'DEF'
    let perPositionDefaults = {};   // { default: {key: val}, QB: {key: val}, ... }
    let positionCoeffState = {};    // tracks user-edited values per position

    // ── Slider / Input Sync ──────────────────────────────────────────

    function syncInput(key) {
        const slider = document.getElementById(`slider-${key}`);
        const num = document.getElementById(`num-${key}`);
        num.value = slider.value;
        markModified(key);
    }

    function syncSlider(key) {
        const slider = document.getElementById(`slider-${key}`);
        const num = document.getElementById(`num-${key}`);
        slider.value = num.value;
        markModified(key);
    }

    function markModified(key) {
        const num = document.getElementById(`num-${key}`);
        const def = parseFloat(num.dataset.default);
        const cur = parseFloat(num.value);
        const container = num.closest('[data-coeff-key]');
        if (Math.abs(cur - def) > 0.0001) {
            container.classList.add('bg-pigskin-50');
            num.classList.add('ring-1', 'ring-pigskin-300');
        } else {
            container.classList.remove('bg-pigskin-50');
            num.classList.remove('ring-1', 'ring-pigskin-300');
        }
    }

    // ── Reset ────────────────────────────────────────────────────────

    function resetAll() {
        const posDefaults = perPositionDefaults[activeEditorPosition] || {};
        document.querySelectorAll('.coeff-input').forEach(input => {
            const key = input.id.replace('num-', '');
            const defVal = (key in posDefaults) ? posDefaults[key] : parseFloat(input.dataset.default);
            input.value = defVal;
            input.dataset.default = defVal;
            const slider = document.getElementById(`slider-${key}`);
            if (slider) slider.dataset.default = defVal;
            syncSlider(key);
        });
        if (Object.keys(posDefaults).length > 0) {
            positionCoeffState[activeEditorPosition] = { ...posDefaults };
        }
    }

    function resetGroup(group) {
        const posDefaults = perPositionDefaults[activeEditorPosition] || {};
        document.querySelectorAll(`[data-coeff-key]`).forEach(container => {
            const input = container.querySelector('.coeff-input');
            const slider = container.querySelector('.coeff-slider');
            if (!input || !slider) return;
            const groupHeader = container.closest('.divide-y')?.querySelector('[onclick*="resetGroup"]');
            if (groupHeader && groupHeader.getAttribute('onclick').includes(group)) {
                const key = input.id.replace('num-', '');
                const defVal = (key in posDefaults) ? posDefaults[key] : parseFloat(input.dataset.default);
                input.value = defVal;
                input.dataset.default = defVal;
                slider.value = defVal;
                slider.dataset.default = defVal;
                markModified(key);
                if (positionCoeffState[activeEditorPosition]) {
                    positionCoeffState[activeEditorPosition][key] = defVal;
                }
            }
        });
    }

    // ── Position-specific Coefficient State ─────────────────────────

    function saveCurrentPositionState() {
        const state = {};
        document.querySelectorAll('.coeff-input').forEach(input => {
            const key = input.id.replace('num-', '');
            state[key] = parseFloat(input.value);
        });
        positionCoeffState[activeEditorPosition] = state;
    }

    function setEditorPosition(pos) {
        // Save slider values for the position we're leaving
        saveCurrentPositionState();
        activeEditorPosition = pos;

        // Update tab button styles
        const TAB_ACTIVE   = 'px-2.5 py-1 text-[11px] font-semibold rounded-md bg-pigskin-500 text-white transition-colors';
        const TAB_INACTIVE = 'px-2.5 py-1 text-[11px] font-semibold rounded-md bg-white text-slate-600 border border-slate-200 hover:bg-slate-50 transition-colors';
        document.querySelectorAll('#coeff-pos-tabs button').forEach(btn => {
            btn.className = btn.id === `coeff-tab-${pos}` ? TAB_ACTIVE : TAB_INACTIVE;
        });

        // Load saved state (user edits) or fall back to defaults for this position
        const posState    = positionCoeffState[pos] || perPositionDefaults[pos] || perPositionDefaults['default'] || {};
        const posDefaults = perPositionDefaults[pos] || perPositionDefaults['default'] || {};

        document.querySelectorAll('.coeff-input').forEach(input => {
            const key = input.id.replace('num-', '');
            if (key in posState)    input.value          = posState[key];
            if (key in posDefaults) input.dataset.default = posDefaults[key];
            const slider = document.getElementById(`slider-${key}`);
            if (slider) {
                if (key in posState)    slider.value          = posState[key];
                if (key in posDefaults) slider.dataset.default = posDefaults[key];
            }
            markModified(key);
        });
    }

    async function initPositionCoefficients() {
        try {
            const resp = await fetch('/api/projection-tuner/defaults');
            const data = await resp.json();
            const ppd = data.per_position_defaults || {};
            if (!Object.keys(ppd).length) return;

            perPositionDefaults = ppd;

            // Seed state from defaults for every position
            positionCoeffState = {};
            for (const [pos, coeffs] of Object.entries(ppd)) {
                positionCoeffState[pos] = { ...coeffs };
            }

            // Override 'default' with current DOM values so any pre-load edits are preserved
            const domCoeffs = {};
            document.querySelectorAll('.coeff-input').forEach(input => {
                const key = input.id.replace('num-', '');
                domCoeffs[key] = parseFloat(input.value);
            });
            positionCoeffState['default'] = domCoeffs;
        } catch (err) {
            console.warn('Failed to load per-position defaults:', err);
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

    // ── Collect Coefficients ─────────────────────────────────────────

    function getCoefficients() {
        // Capture current DOM values for the active position
        const currentCoeffs = {};
        document.querySelectorAll('.coeff-input').forEach(input => {
            const key = input.id.replace('num-', '');
            currentCoeffs[key] = parseFloat(input.value);
        });

        // If position state is loaded, return a full nested position-keyed dict
        if (Object.keys(positionCoeffState).length > 0) {
            positionCoeffState[activeEditorPosition] = currentCoeffs;
            const result = {};
            for (const [pos, coeffs] of Object.entries(positionCoeffState)) {
                result[pos] = { ...coeffs };
            }
            return result;
        }

        // Fallback: flat dict (position state not yet loaded from API)
        return currentCoeffs;
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

    // ── Run Simulation ───────────────────────────────────────────────

    async function runSimulation() {
        const btn = document.getElementById('sim-run-btn');
        const spinner = document.getElementById('sim-spinner');
        btn.disabled = true;
        spinner.classList.remove('hidden');

        const coefficients = getCoefficients();
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
                        coefficients: coefficients,
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
                        coefficients: coefficients,
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

        // Summary cards
        let html = `
        <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">
            <div class="flex items-center justify-between mb-3">
                <div>
                    <h4 class="text-sm font-bold text-slate-700">${player.name}</h4>
                    <p class="text-xs text-slate-500">${player.position} · ${player.nfl_team} · ${tuned.projection_type} ${tuned.week ? 'Week ' + tuned.week : ''} ${tuned.year}</p>
                </div>
            </div>
            <div class="grid grid-cols-3 gap-3 mb-4">
                <div class="bg-slate-50 rounded-lg p-3 text-center">
                    <p class="text-lg font-bold text-slate-700">${def ? def.total : '—'}</p>
                    <p class="text-[10px] uppercase tracking-wider text-slate-400 font-semibold">Default</p>
                </div>
                <div class="bg-pigskin-50 rounded-lg p-3 text-center border border-pigskin-200">
                    <p class="text-lg font-bold text-pigskin-700">${tuned.total}</p>
                    <p class="text-[10px] uppercase tracking-wider text-pigskin-500 font-semibold">Tuned</p>
                </div>
                <div class="bg-field-50 rounded-lg p-3 text-center">
                    <p class="text-lg font-bold text-field-700">${tuned.actual_points !== null ? tuned.actual_points : '—'}</p>
                    <p class="text-[10px] uppercase tracking-wider text-field-500 font-semibold">Actual</p>
                </div>
            </div>`;

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

        // Waterfall chart
        html += `<div class="mt-2">
            <h5 class="text-xs font-bold text-slate-600 uppercase tracking-wider mb-2">Formula Breakdown (Waterfall)</h5>
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
            <h4 class="text-sm font-bold text-slate-700 mb-3">Backtest Results — ${data.position || 'All Positions'} ${data.year}${data.week ? ' Week ' + data.week : ''}</h4>

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
                            <th class="text-right py-1.5 px-2">Projected</th>
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

    const INVERTED_COLS = new Set(['injury_risk_score', 'opposing_defense_vs_position_rank']);

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
        const week = type === 'weekly' && weekEl ? parseInt(weekEl.value, 10) : null;

        const btn = document.getElementById('grid-load-btn');
        const spinner = document.getElementById('grid-spinner');
        const container = document.getElementById('grid-container');

        btn.disabled = true;
        spinner.classList.remove('hidden');
        container.innerHTML = '<p class="text-xs text-slate-400 italic p-4">Loading…</p>';

        const body = { year, projection_type: type };
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
        if (gridSortCol) {
            rows.sort((a, b) => {
                let va, vb;
                if (['player_name', 'position', 'nfl_team'].includes(gridSortCol)) {
                    va = a[gridSortCol] || ''; vb = b[gridSortCol] || '';
                    return gridSortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
                }
                if (gridSortCol === 'projected') { va = a.projected ?? -Infinity; vb = b.projected ?? -Infinity; }
                else if (gridSortCol === 'actual') { va = a.actual ?? -Infinity; vb = b.actual ?? -Infinity; }
                else { va = a.criteria[gridSortCol] ?? -Infinity; vb = b.criteria[gridSortCol] ?? -Infinity; }
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
        const tdCls = 'px-3 py-1.5 border-b border-r border-slate-100 whitespace-nowrap text-right';
        const td1Cls = 'sticky left-0 bg-white px-3 py-1.5 border-b border-r border-slate-200 whitespace-nowrap font-medium text-slate-700';

        let html = `<table class="min-w-full text-xs border-collapse"><thead><tr>
            <th class="${th1Cls}" onclick="TunerApp.sortGrid('player_name')">Player ${sortIcon('player_name')}</th>
            <th class="${thCls}" onclick="TunerApp.sortGrid('position')">Pos ${sortIcon('position')}</th>
            <th class="${thCls}" onclick="TunerApp.sortGrid('nfl_team')">Team ${sortIcon('nfl_team')}</th>
            ${cols.map(c => `<th class="${thCls}" onclick="TunerApp.sortGrid('${c}')" title="${c}">${colLabel(c)} ${sortIcon(c)}</th>`).join('')}
            <th class="${thCls}" onclick="TunerApp.sortGrid('projected')">Projected ${sortIcon('projected')}</th>
            <th class="${thCls}" onclick="TunerApp.sortGrid('actual')">Actual ${sortIcon('actual')}</th>
        </tr></thead><tbody>`;

        for (const row of rows) {
            html += `<tr class="hover:bg-slate-50/50 transition-colors">
                <td class="${td1Cls}">${row.player_name}</td>
                <td class="${tdCls} text-center">${row.position}</td>
                <td class="${tdCls} text-center text-slate-500">${row.nfl_team || '—'}</td>`;
            for (const col of cols) {
                const val = row.criteria[col];
                const s = stats[col];
                let bg = '';
                if (val !== null && val !== undefined && s && s.max !== s.min) {
                    bg = ` style="background:${heatBg(normalizeVal(val, s), INVERTED_COLS.has(col))}"`;
                }
                html += `<td class="${tdCls}"${bg}>${fmtVal(val)}</td>`;
            }
            html += `<td class="${tdCls} font-semibold text-slate-700">${fmtVal(row.projected)}</td>
                <td class="${tdCls} text-slate-500">${fmtVal(row.actual)}</td></tr>`;
        }

        html += '</tbody></table>';
        const note = data.truncated
            ? `<p class="text-[10px] text-amber-600 px-3 py-1 border-t border-slate-100">Showing first ${data.player_count} players — narrow by position.</p>`
            : `<p class="text-[10px] text-slate-400 px-3 py-1 border-t border-slate-100">${data.player_count} players loaded.</p>`;
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

        let html = `<div class="space-y-3">
            <div class="flex flex-wrap items-center justify-between gap-2">
                <div>
                    <p class="text-xs text-slate-500 uppercase tracking-wider font-semibold">Run ${summary.run_id || '—'}</p>
                    <p class="text-xs text-slate-400">Year ${summary.year || '—'} · ${summary.sample_count || 0} samples · ${summary.player_count || 0} players</p>
                </div>
                <div class="flex items-center gap-3">
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
    document.addEventListener('DOMContentLoaded', initPositionCoefficients);

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

// ── Public API ──────────────────────────────────────────────────────── ───────────────────────────────────────────────────

    return {
        syncInput,
        syncSlider,
        resetAll,
        resetGroup,
        setMode,
        setEditorPosition,
        filterPlayers,
        runSimulation,
        runDiagnose,
        loadGrid,
        sortGrid,
        runAlgorithmTuning,
        loadAlgorithmHistory,
        loadAlgorithmRunFromHistory,
        importNFLData,
        computeFromLogs,
    };
})();

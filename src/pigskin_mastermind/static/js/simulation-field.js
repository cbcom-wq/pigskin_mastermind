/* =====================================================================
   Simulation Field Engine — Shared JavaScript
   =====================================================================
   Provides the behind-LOS route-chart field renderer, coordinate helpers,
   route drawing with polylines + headshot animation, playback controls,
   and celebration system.  Used by game, player, and team simulations.

   USAGE:
     In each page's <script> block, set up a SimConfig object before
     calling SimField.init(config).  The config object allows each page
     to hook into page-specific behaviour (stats updates, celebrations,
     data loading) while sharing 100% of the rendering & playback code.
   ===================================================================== */

const SimField = (function () {
    'use strict';

    const SVG_NS = 'http://www.w3.org/2000/svg';
    const XLINK_NS = 'http://www.w3.org/1999/xlink';

    /* ── field geometry constants ────────────────────────────────────── */
    const VB_W = 400;
    const VB_H = 300;
    const MIN_FIELD_DEPTH_YARDS = 40;
    const BEHIND_LOS_YARDS = 8;
    let fieldDepthYards = MIN_FIELD_DEPTH_YARDS;
    let totalDepthYards = fieldDepthYards + BEHIND_LOS_YARDS;
    const FIELD_TOP_Y = 20;
    const FIELD_BOT_Y = 280;
    const FIELD_LEFT_X = 30;
    const FIELD_RIGHT_X = 370;

    function getLosY() {
        return FIELD_BOT_Y - (BEHIND_LOS_YARDS / totalDepthYards) * (FIELD_BOT_Y - FIELD_TOP_Y);
    }

    /** Scan events and return the field depth (yards beyond LOS) needed. */
    function computeNeededDepth(events) {
        let maxBeyond = MIN_FIELD_DEPTH_YARDS;
        events.forEach(function(evt) {
            if (!evt.route_path || !evt.route_path.segments) return;
            var losX = evt.start_x;
            evt.route_path.segments.forEach(function(seg) {
                var beyond = seg.depth - losX;
                if (beyond > maxBeyond) maxBeyond = beyond;
            });
        });
        /* Round up to next 5-yard increment with 5-yard pad */
        return Math.ceil((maxBeyond + 5) / 5) * 5;
    }

    function updateFieldDepth(depth) {
        fieldDepthYards = depth;
        totalDepthYards = fieldDepthYards + BEHIND_LOS_YARDS;
    }

    /* ── state ──────────────────────────────────────────────────────── */
    let simEvents = [];
    let simCurrentIndex = 0;
    let simTimer = null;
    let simPlaying = false;
    let simSpeed = 1.0;
    let allRoutesMode = false;
    const simBaseDelay = 1400;
    let _headshotCounter = 0;
    let celebrationTimer = null;
    const CELEBRATION_DURATION = 2800;
    const CONFETTI_COLORS = ['#fbbf24','#34d399','#60a5fa','#f472b6','#a78bfa','#fb923c','#ffffff'];

    /* Config set by the host page */
    let cfg = {};

    /* ── helpers ─────────────────────────────────────────────────────── */
    function _q(id) { return document.getElementById(id); }
    function _def(v, fb) { return v === null || v === undefined ? fb : v; }

    function svgEl(tag, attrs) {
        const el = document.createElementNS(SVG_NS, tag);
        for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
        return el;
    }

    /* ── coordinate helpers ─────────────────────────────────────────── */

    function depthToY(depthPct, losX) {
        const yardsBeyond = depthPct - losX;
        const frac = (yardsBeyond + BEHIND_LOS_YARDS) / totalDepthYards;
        return FIELD_BOT_Y - frac * (FIELD_BOT_Y - FIELD_TOP_Y);
    }

    function lateralToX(lateralPct) {
        return FIELD_LEFT_X + (lateralPct / 100) * (FIELD_RIGHT_X - FIELD_LEFT_X);
    }

    /* ── field background ───────────────────────────────────────────── */

    function buildFieldBackground(losFieldX) {
        const g = _q('svg-field-bg');
        g.innerHTML = '';

        g.appendChild(svgEl('rect', {x:0, y:0, width: VB_W, height: VB_H, fill:'#1a2a1a'}));

        const losYard = losFieldX;
        const startYard = Math.floor((losYard - BEHIND_LOS_YARDS - 5) / 5) * 5;
        const endYard = losYard + fieldDepthYards + 5;
        for (let yd = startYard; yd <= endYard; yd += 5) {
            const y = depthToY(yd, losFieldX);
            if (y < 0 || y > VB_H) continue;
            const isTen = (yd % 10 === 0);
            g.appendChild(svgEl('line', {
                x1: FIELD_LEFT_X, y1: y, x2: FIELD_RIGHT_X, y2: y,
                stroke: isTen ? 'rgba(255,255,255,0.22)' : 'rgba(255,255,255,0.1)',
                'stroke-width': isTen ? 0.6 : 0.3,
            }));

            if (isTen) {
                const displayYd = yd > 50 ? (100 - yd) : yd;
                if (displayYd >= 0 && displayYd <= 50) {
                    const makeLabel = function(x, anchor) {
                        const t = svgEl('text', {
                            x: x, y: y + 3,
                            fill: 'rgba(255,255,255,0.35)',
                            'font-size': '8', 'text-anchor': anchor,
                            'font-family': 'system-ui, sans-serif',
                            'font-weight': '700',
                        });
                        t.textContent = displayYd;
                        return t;
                    };
                    g.appendChild(makeLabel(FIELD_LEFT_X - 4, 'end'));
                    g.appendChild(makeLabel(FIELD_RIGHT_X + 4, 'start'));
                }
            }
        }

        g.appendChild(svgEl('line', {x1: FIELD_LEFT_X, y1:0, x2: FIELD_LEFT_X, y2: VB_H, stroke:'rgba(255,255,255,0.08)', 'stroke-width':1}));
        g.appendChild(svgEl('line', {x1: FIELD_RIGHT_X, y1:0, x2: FIELD_RIGHT_X, y2: VB_H, stroke:'rgba(255,255,255,0.08)', 'stroke-width':1}));
    }

    function drawLOS(losFieldX) {
        const g = _q('svg-los-group');
        g.innerHTML = '';
        const y = getLosY();
        g.appendChild(svgEl('line', {
            x1: FIELD_LEFT_X - 8, y1: y, x2: FIELD_RIGHT_X + 8, y2: y,
            stroke: '#3b82f6', 'stroke-width': 1.5, opacity: 0.9,
        }));
        const makeLosLabel = function(x, anchor) {
            const t = svgEl('text', {
                x: x, y: y + 12,
                fill: '#3b82f6', 'font-size': '8', 'text-anchor': anchor,
                'font-family': 'system-ui, sans-serif', 'font-weight': '700',
            });
            t.textContent = 'LOS';
            return t;
        };
        g.appendChild(makeLosLabel(FIELD_LEFT_X - 8, 'end'));
        g.appendChild(makeLosLabel(FIELD_RIGHT_X + 8, 'start'));
    }

    /* ── route drawing ──────────────────────────────────────────────── */

    function clearRoutes() {
        _q('svg-routes').innerHTML = '';
        _q('svg-endpoints').innerHTML = '';
        _q('svg-headshot').innerHTML = '';
        _q('svg-labels').innerHTML = '';
        const svg = _q('route-svg');
        if (svg) svg.querySelectorAll('[id^="headshot-motion-path-"]').forEach(function(el) { el.remove(); });
        _headshotCounter = 0;
    }

    /**
     * Break segments into CSS-classified polyline groups.
     */
    function classifySegments(segs, rp, role) {
        const groups = [];
        let currentGroup = null;

        for (let i = 0; i < segs.length; i++) {
            const seg = segs[i];
            let cssClass, marker;

            if (role === 'rush') {
                cssClass = 'rush-segment';
                marker = 'arrow-yellow';
            } else if (rp.is_sack && (seg.type === 'sack_end' || (i > 0 && segs[i-1].type === 'drop'))) {
                cssClass = 'sack-segment';
                marker = 'arrow-red';
            } else if (seg.type === 'catch_end') {
                if (currentGroup) groups.push(currentGroup);
                currentGroup = {
                    cssClass: 'yac-segment',
                    marker: 'arrow-green',
                    points: [segs[i - 1], seg],
                };
                groups.push(currentGroup);
                currentGroup = null;
                continue;
            } else if (!rp.is_complete && !rp.is_sack && role !== 'rush') {
                cssClass = 'incomplete-segment';
                marker = 'arrow-grey';
            } else {
                cssClass = 'route-segment';
                marker = 'arrow-white';
            }

            if (currentGroup && currentGroup.cssClass === cssClass) {
                currentGroup.points.push(seg);
                currentGroup.marker = marker;
            } else {
                if (currentGroup) groups.push(currentGroup);
                currentGroup = {
                    cssClass: cssClass,
                    marker: marker,
                    points: i > 0 && (!currentGroup || currentGroup.cssClass !== cssClass) ? [segs[i-1], seg] : [seg],
                };
            }
        }
        if (currentGroup && currentGroup.points.length >= 2) groups.push(currentGroup);

        return groups.filter(function(g) { return g.points.length >= 2; });
    }

    /**
     * Draw a single play's route_path onto the SVG.
     * Handles all play types (pass, rush, sack, incomplete) with
     * polylines, arrow markers, headshots, and endpoint markers.
     *
     * @param {Object} evt - The simulation event
     * @param {boolean} animate - Whether to animate the route drawing
     * @param {number} opacity - Opacity for multi-route overlay
     */
    function drawRoute(evt, animate, opacity) {
        const rp = evt.route_path;
        if (!rp || !rp.segments || rp.segments.length < 2) return;

        const routesG = _q('svg-routes');
        const endpointsG = _q('svg-endpoints');
        const labelsG = _q('svg-labels');
        const losX = evt.start_x;
        const segs = rp.segments;
        const playerColor = evt.player_color || '#94a3b8';

        const groups = classifySegments(segs, rp, evt.role);

        groups.forEach(function(group) {
            if (group.points.length < 2) return;
            const pts = group.points.map(function(s) {
                return lateralToX(s.lateral).toFixed(1) + ',' + depthToY(s.depth, losX).toFixed(1);
            }).join(' ');

            const attrs = {
                points: pts,
                class: 'route-line ' + group.cssClass,
                opacity: opacity,
            };
            if (group.marker) attrs['marker-end'] = 'url(#' + group.marker + ')';

            const polyline = svgEl('polyline', attrs);
            if (animate) {
                routesG.appendChild(polyline);
                const len = polyline.getTotalLength ? polyline.getTotalLength() : 500;
                polyline.style.setProperty('--route-len', len);
                polyline.classList.add('animate-draw');
            } else {
                routesG.appendChild(polyline);
            }
        });

        /* ── Headshot / player markers ──────────────────────────────── */
        /* Derive the "primary" headshot from whichever role-specific field
           is available.  Game-sim events carry passer/rusher/receiver URLs
           but not a unified player_headshot_url; player-sim and team-sim
           set player_headshot_url directly. */
        let headshotUrl = evt.player_headshot_url || '';
        if (!headshotUrl) {
            if (evt.role === 'rush')         headshotUrl = evt.rusher_headshot_url || '';
            else if (evt.role === 'pass')    headshotUrl = evt.passer_headshot_url || '';
            else if (evt.role === 'receive') headshotUrl = evt.receiver_headshot_url || '';
        }
        /* Derive position label from role-specific names when player_position is absent */
        if (!evt.player_position) {
            if (evt.role === 'rush' && evt.rusher_name)        evt._derivedPos = 'RB';
            else if (evt.role === 'pass' && evt.passer_name)   evt._derivedPos = 'QB';
            else if (evt.role === 'receive' && evt.receiver_name) evt._derivedPos = 'WR';
        }
        const displayPos = evt.player_position || evt._derivedPos || '';

        const isPassPlay = evt.play_type === 'pass' && !rp.is_sack && opacity >= 0.8;

        if (isPassPlay) {
            const dropSeg = segs.find(function(s) { return s.type === 'drop'; });
            let qbUrl, qbColor, qbName, rcvUrl, rcvColor, rcvName;

            if (evt.role === 'pass') {
                qbUrl = headshotUrl || evt.passer_headshot_url || '';
                qbColor = evt.passer_color || playerColor || '#ef4444';
                qbName = evt.player_name || evt.passer_name || '';
                rcvUrl = evt.receiver_headshot_url || '';
                rcvColor = evt.receiver_color || '#3b82f6';
                rcvName = evt.receiver_name || '';
            } else {
                qbUrl = evt.passer_headshot_url || '';
                qbColor = evt.passer_color || '#ef4444';
                qbName = evt.passer_name || '';
                rcvUrl = headshotUrl || evt.receiver_headshot_url || '';
                rcvColor = evt.receiver_color || playerColor || '#3b82f6';
                rcvName = evt.player_name || evt.receiver_name || '';
            }

            /* QB at throw/drop point */
            const qbSeg = dropSeg || segs[0];
            if (qbSeg) {
                const _qx = lateralToX(qbSeg.lateral);
                const _qy = depthToY(qbSeg.depth, losX);
                if (qbUrl) {
                    drawStaticHeadshot(qbSeg, losX, qbUrl, qbColor);
                    if (qbName) drawNameBadge(_qx, _qy + 12, qbName, qbColor);
                } else if (qbName) {
                    drawNameBadge(_qx, _qy - 14, qbName, qbColor);
                }
            }

            /* Receiver travels to / sits at route endpoint */
            const _rcvLastSeg = segs[segs.length - 1];
            const _rcvEx = lateralToX(_rcvLastSeg.lateral);
            const _rcvEy = depthToY(_rcvLastSeg.depth, losX);
            if (rcvUrl) {
                drawHeadshotMarker(segs, losX, animate, rcvUrl, rcvColor);
                if (rcvName) drawNameBadge(_rcvEx, _rcvEy + 12, rcvName, rcvColor);
            } else if (rcvName) {
                drawNameBadge(_rcvEx, _rcvEy - 14, rcvName, rcvColor);
            } else {
                drawHeadshotMarker(segs, losX, animate, headshotUrl, playerColor);
            }
        } else if (headshotUrl && opacity >= 0.8) {
            drawHeadshotMarker(segs, losX, animate, headshotUrl, playerColor);
            /* Also show name badge below the headshot */
            const _primaryName = evt.player_name || evt.rusher_name || evt.passer_name || evt.receiver_name || '';
            if (_primaryName) {
                const _ls = segs[segs.length - 1];
                const _ex = lateralToX(_ls.lateral);
                const _ey = depthToY(_ls.depth, losX);
                drawNameBadge(_ex, _ey + 12, _primaryName, playerColor);
            }
        } else {
            /* Try name badge before falling back to bare position dot */
            const _primaryName = evt.player_name || evt.rusher_name || evt.passer_name || evt.receiver_name || '';
            const lastSeg = segs[segs.length - 1];
            const ex = lateralToX(lastSeg.lateral);
            const ey = depthToY(lastSeg.depth, losX);

            if (_primaryName && opacity >= 0.8) {
                drawNameBadge(ex, ey - 14, _primaryName, playerColor);
            } else {
                /* Fallback: position-colored badge */
                const badgeG = svgEl('g', {});
                badgeG.appendChild(svgEl('circle', {
                    cx: ex, cy: ey - 14, r: 8,
                    fill: playerColor, stroke: '#0f172a', 'stroke-width': 1, opacity: Math.max(opacity, 0.8),
                }));
                const posText = svgEl('text', {
                    x: ex, y: ey - 11, fill: '#ffffff', 'font-size': '6', 'text-anchor': 'middle',
                    'font-family': 'system-ui, sans-serif', 'font-weight': '700', opacity: Math.max(opacity, 0.8),
                });
                posText.textContent = (displayPos || '??').substring(0, 2);
                badgeG.appendChild(posText);
                endpointsG.appendChild(badgeG);
            }
        }

        /* ── Endpoint marker ────────────────────────────────────────── */
        const lastSeg = segs[segs.length - 1];
        const ex = lateralToX(lastSeg.lateral);
        const ey = depthToY(lastSeg.depth, losX);

        if (rp.is_touchdown) {
            endpointsG.appendChild(svgEl('circle', {
                cx: ex, cy: ey, r: 6,
                fill: 'none', stroke: '#3b82f6', 'stroke-width': 2, opacity: opacity,
                class: 'route-endpoint',
            }));
            endpointsG.appendChild(svgEl('circle', {
                cx: ex, cy: ey, r: 2.5,
                fill: '#3b82f6', opacity: opacity, class: 'route-endpoint',
            }));
            const td = svgEl('text', {
                x: ex, y: ey + 18, fill: '#3b82f6', 'font-size': '8', 'text-anchor': 'middle',
                'font-family': 'system-ui, sans-serif', 'font-weight': '700', opacity: opacity,
            });
            td.textContent = 'TD';
            labelsG.appendChild(td);
        } else if (rp.is_sack) {
            const sack = svgEl('text', {
                x: ex, y: ey + 3,
                fill: '#ef4444', 'font-size': '10', 'text-anchor': 'middle',
                'font-family': 'system-ui, sans-serif', 'font-weight': '900', opacity: opacity,
            });
            sack.textContent = '✕';
            endpointsG.appendChild(sack);
        } else {
            const dotColor = rp.is_complete ? '#22c55e' : (evt.role === 'rush' ? '#facc15' : '#9ca3af');
            endpointsG.appendChild(svgEl('circle', {
                cx: ex, cy: ey, r: 3,
                fill: dotColor, opacity: opacity, class: 'route-endpoint',
            }));
        }

        /* ── Yards-gained readout ───────────────────────────────────── */
        if (opacity >= 0.8) {
            const yds = evt.yards_gained;
            const ydsText = yds > 0 ? '+' + yds : String(yds);
            const ydsColor = yds > 0 ? '#4ade80' : yds < 0 ? '#f87171' : '#94a3b8';
            const labelX = ex + 14;
            const labelY = ey + 3;

            const pillW = ydsText.length * 5.5 + 8;
            labelsG.appendChild(svgEl('rect', {
                x: labelX - 3, y: labelY - 8, width: pillW, height: 12,
                rx: 3, ry: 3, fill: '#0f172a', opacity: 0.8,
            }));
            const yt = svgEl('text', {
                x: labelX + pillW / 2 - 3, y: labelY,
                fill: ydsColor, 'font-size': '7.5', 'text-anchor': 'middle',
                'font-family': 'system-ui, sans-serif', 'font-weight': '700',
                opacity: opacity,
            });
            yt.textContent = ydsText;
            labelsG.appendChild(yt);
        }
    }

    /* ── headshot helpers ────────────────────────────────────────────── */

    function drawStaticHeadshot(seg, losX, headshotUrl, ringColor) {
        const hsG = _q('svg-headshot');
        const R = 9;
        const BORDER = 1.5;
        const px = lateralToX(seg.lateral);
        const py = depthToY(seg.depth, losX);

        const g = svgEl('g', {
            class: 'headshot-marker',
            transform: 'translate(' + px.toFixed(1) + ',' + py.toFixed(1) + ')',
        });

        g.appendChild(svgEl('circle', {
            cx: 0, cy: 0, r: R + BORDER,
            fill: '#0f172a', stroke: ringColor || '#ef4444', 'stroke-width': BORDER,
        }));

        const img = document.createElementNS(SVG_NS, 'image');
        img.setAttributeNS(XLINK_NS, 'href', headshotUrl);
        img.setAttribute('x', String(-R));
        img.setAttribute('y', String(-R));
        img.setAttribute('width', String(R * 2));
        img.setAttribute('height', String(R * 2));
        img.setAttribute('clip-path', 'url(#headshot-clip)');
        img.setAttribute('preserveAspectRatio', 'xMidYMid slice');
        img.addEventListener('error', function() { g.style.display = 'none'; });
        g.appendChild(img);

        hsG.appendChild(g);
    }

    function drawNameBadge(cx, cy, name, bgColor) {
        const labelsG = _q('svg-labels');
        const shortName = name.length > 12 ? name.substring(0, 11) + '…' : name;
        const pillW = shortName.length * 4.5 + 10;

        labelsG.appendChild(svgEl('rect', {
            x: cx - pillW / 2, y: cy - 5, width: pillW, height: 11,
            rx: 3, ry: 3, fill: bgColor || '#3b82f6', opacity: 0.85,
        }));
        const t = svgEl('text', {
            x: cx, y: cy + 3, fill: '#ffffff', 'font-size': '6', 'text-anchor': 'middle',
            'font-family': 'system-ui, sans-serif', 'font-weight': '700',
        });
        t.textContent = shortName;
        labelsG.appendChild(t);
    }

    function drawHeadshotMarker(segs, losX, animate, headshotUrl, playerColor) {
        const hsG = _q('svg-headshot');
        if (!segs || segs.length < 2) return;

        const R = 10;
        const BORDER = 1.8;

        const pathPoints = segs.map(function(s) {
            return { x: lateralToX(s.lateral), y: depthToY(s.depth, losX) };
        });
        const pathD = pathPoints.map(function(p, i) {
            return (i === 0 ? 'M' : 'L') + p.x.toFixed(1) + ',' + p.y.toFixed(1);
        }).join(' ');

        const g = svgEl('g', { class: 'headshot-marker' + (animate ? ' animate' : '') });

        g.appendChild(svgEl('circle', {
            cx: 0, cy: 0, r: R + BORDER,
            fill: '#0f172a', stroke: playerColor || '#3b82f6', 'stroke-width': BORDER,
        }));

        if (headshotUrl) {
            const img = document.createElementNS(SVG_NS, 'image');
            img.setAttributeNS(XLINK_NS, 'href', headshotUrl);
            img.setAttribute('x', String(-R));
            img.setAttribute('y', String(-R));
            img.setAttribute('width', String(R * 2));
            img.setAttribute('height', String(R * 2));
            img.setAttribute('clip-path', 'url(#headshot-clip)');
            img.setAttribute('preserveAspectRatio', 'xMidYMid slice');
            img.addEventListener('error', function() { g.style.display = 'none'; });
            g.appendChild(img);
        }

        if (animate) {
            _headshotCounter++;
            const motionId = 'headshot-motion-path-' + _headshotCounter;
            const defsPath = svgEl('path', { d: pathD, id: motionId });
            _q('route-svg').querySelector('defs').appendChild(defsPath);

            const animMotion = document.createElementNS(SVG_NS, 'animateMotion');
            animMotion.setAttribute('dur', '0.7s');
            animMotion.setAttribute('fill', 'freeze');
            animMotion.setAttribute('calcMode', 'linear');

            const mpath = document.createElementNS(SVG_NS, 'mpath');
            mpath.setAttributeNS(XLINK_NS, 'href', '#' + motionId);
            animMotion.appendChild(mpath);
            g.appendChild(animMotion);
        } else {
            const last = pathPoints[pathPoints.length - 1];
            g.setAttribute('transform', 'translate(' + last.x.toFixed(1) + ',' + last.y.toFixed(1) + ')');
        }

        hsG.appendChild(g);
    }

    /* ── all-routes overlay & flat view ──────────────────────────────── */

    function toggleAllRoutes() {
        allRoutesMode = !allRoutesMode;
        const btn = _q('btn-all-routes');
        if (!btn) return;
        if (allRoutesMode) {
            btn.classList.add('border-white', 'text-white');
            btn.classList.remove('border-slate-600', 'text-slate-400');
            drawAllRoutes();
        } else {
            btn.classList.remove('border-white', 'text-white');
            btn.classList.add('border-slate-600', 'text-slate-400');
            if (simEvents.length) applyFrame(simCurrentIndex, true);
        }
    }

    function drawAllRoutes() {
        if (!simEvents.length) return;
        const refLos = simEvents[0].start_x;
        updateFieldDepth(computeNeededDepth(simEvents));
        clearRoutes();
        buildFieldBackground(refLos);
        drawLOS(refLos);
        simEvents.forEach(function(evt) { drawRoute(evt, false, 0.5); });
    }

    function toggleFlatView() {
        const inner = _q('route-field-inner');
        const btn = _q('btn-flat-view');
        if (!inner || !btn) return;
        inner.classList.toggle('flat-view');
        if (inner.classList.contains('flat-view')) {
            btn.classList.add('border-white', 'text-white');
            btn.classList.remove('border-slate-600', 'text-slate-400');
        } else {
            btn.classList.remove('border-white', 'text-white');
            btn.classList.add('border-slate-600', 'text-slate-400');
        }
    }

    /* ── play banner ────────────────────────────────────────────────── */

    function updatePlayBanner(e) {
        const banner = _q('play-banner');
        const bannerDesc = _q('play-banner-desc');
        const bannerMeta = _q('play-banner-meta');
        const desc = (e.description || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        const yards = e.yards_gained > 0 ? '+' + e.yards_gained : e.yards_gained;
        const badges = e.badges || {};

        if (desc) {
            bannerDesc.innerHTML = desc;
            const metaParts = [];
            metaParts.push('<span style="color:#cbd5e1">' + (e.down_distance || '') + '</span>');
            const roleClass = e.role === 'pass' ? 'background:#3b82f6;color:#fff'
                : e.role === 'rush' ? 'background:#eab308;color:#1a1a1a'
                : 'background:#22c55e;color:#fff';
            metaParts.push('<span class="play-banner-badge" style="' + roleClass + '">' + (e.role || 'play').toUpperCase() + '</span>');
            metaParts.push('<span>' + yards + ' yds</span>');
            if (badges.touchdown) metaParts.push('<span class="play-banner-badge" style="background:#16a34a;color:#fff">TD</span>');
            if (badges.first_down) metaParts.push('<span class="play-banner-badge" style="background:#2563eb;color:#fff">1ST DOWN</span>');
            if (badges.turnover) metaParts.push('<span class="play-banner-badge" style="background:#dc2626;color:#fff">TURNOVER</span>');
            if (badges.big_play) metaParts.push('<span class="play-banner-badge" style="background:#d97706;color:#fff">BIG PLAY</span>');
            bannerMeta.innerHTML = metaParts.join('');
            banner.classList.add('visible');
        } else {
            banner.classList.remove('visible');
        }
    }

    /* ── playback controls ──────────────────────────────────────────── */

    function setControlsEnabled(enabled) {
        ['btn-prev', 'btn-play', 'btn-pause', 'btn-next', 'btn-reset', 'sim-progress'].forEach(function(id) {
            const el = _q(id);
            if (el) el.disabled = !enabled;
        });
    }

    function playSimulation() {
        if (!simEvents.length || simPlaying) return;
        simPlaying = true;
        const btnPlay = _q('btn-play');
        const btnPause = _q('btn-pause');
        if (btnPlay) btnPlay.disabled = true;
        if (btnPause) btnPause.disabled = false;
        simTimer = setInterval(function() {
            if (simCurrentIndex >= simEvents.length - 1) { pauseSimulation(); return; }
            applyFrame(simCurrentIndex + 1, false);
        }, simBaseDelay / simSpeed);
    }

    function pauseSimulation() {
        simPlaying = false;
        const btnPlay = _q('btn-play');
        const btnPause = _q('btn-pause');
        if (btnPlay) btnPlay.disabled = !simEvents.length;
        if (btnPause) btnPause.disabled = true;
        if (simTimer) { clearInterval(simTimer); simTimer = null; }
    }

    function stepForward() {
        if (!simEvents.length) return;
        pauseSimulation();
        applyFrame(simCurrentIndex + 1, false);
    }

    function stepBackward() {
        if (!simEvents.length) return;
        pauseSimulation();
        applyFrame(simCurrentIndex - 1, false);
    }

    function resetSimulation() {
        if (!simEvents.length) return;
        pauseSimulation();
        allRoutesMode = false;
        const btn = _q('btn-all-routes');
        if (btn) {
            btn.classList.remove('border-white','text-white');
            btn.classList.add('border-slate-600','text-slate-400');
        }
        applyFrame(0, true);
    }

    function jumpToPlay(value) {
        if (!simEvents.length) return;
        pauseSimulation();
        allRoutesMode = false;
        const btn = _q('btn-all-routes');
        if (btn) {
            btn.classList.remove('border-white','text-white');
            btn.classList.add('border-slate-600','text-slate-400');
        }
        applyFrame(parseInt(value, 10) || 0, true);
    }

    function setSpeed(value) {
        simSpeed = parseFloat(value);
        const label = _q('sim-speed-label');
        if (label) label.textContent = simSpeed.toFixed(1) + 'x';
        if (simPlaying) { pauseSimulation(); playSimulation(); }
    }

    /* ── frame rendering ────────────────────────────────────────────── */

    function applyFrame(index, instant) {
        if (!simEvents.length) return;
        if (index < 0) index = 0;
        if (index >= simEvents.length) index = simEvents.length - 1;
        simCurrentIndex = index;

        const e = simEvents[index];

        if (allRoutesMode) {
            drawAllRoutes();
        } else {
            updateFieldDepth(computeNeededDepth([e]));
            clearRoutes();
            buildFieldBackground(e.start_x);
            drawLOS(e.start_x);
            drawRoute(e, !instant, 1.0);
        }

        const progress = _q('sim-progress');
        if (progress) progress.value = String(index);

        updatePlayBanner(e);

        /* Let the host page update its specific UI */
        if (cfg.onFrame) cfg.onFrame(e, index, instant);

        const btnPrev = _q('btn-prev');
        const btnNext = _q('btn-next');
        if (btnPrev) btnPrev.disabled = index === 0;
        if (btnNext) btnNext.disabled = index >= simEvents.length - 1;

        checkCelebration(e, index, instant);
    }

    /* ── celebration system ─────────────────────────────────────────── */

    function buildConfetti(container, tier) {
        container.innerHTML = '';
        if (tier === 'turnover') return;
        const count = tier === 'touchdown' ? 30 : 15;
        for (let i = 0; i < count; i++) {
            const piece = document.createElement('div');
            piece.className = 'confetti-piece';
            const color = CONFETTI_COLORS[Math.floor(Math.random() * CONFETTI_COLORS.length)];
            const left = Math.random() * 100;
            const delay = Math.random() * 0.8;
            const size = 4 + Math.random() * 8;
            piece.style.cssText = 'left:' + left + '%;background:' + color
                + ';animation-delay:' + delay + 's;width:' + size + 'px;height:' + size + 'px;border-radius:'
                + (Math.random() > 0.5 ? '50%' : '2px') + ';';
            container.appendChild(piece);
        }
    }

    function showCelebration(evt, tier, fptsGained, playerName, playerHeadshot, playerPosition) {
        const overlay = _q('celebration-overlay');
        const card = _q('celebration-card');
        const confetti = _q('celebration-confetti');
        const content = _q('celebration-content');

        if (!overlay || !card) return;

        if (celebrationTimer) { clearTimeout(celebrationTimer); celebrationTimer = null; }
        overlay.classList.remove('active');
        card.className = 'celebration-card tier-' + tier;

        const tierConfig = {
            'touchdown': { emoji: '🏆', title: 'TOUCHDOWN!', color: '#fbbf24' },
            'big-play':  { emoji: '🔥', title: 'BIG PLAY!', color: '#f59e0b' },
            'turnover':  { emoji: '💀', title: 'TURNOVER',  color: '#ef4444' },
            'high-fpts': { emoji: '⚡', title: 'HUGE PLAY!', color: '#818cf8' },
        };
        const c = tierConfig[tier] || tierConfig['big-play'];

        let html = '';
        if (playerHeadshot) {
            html += '<img src="' + playerHeadshot + '" class="celebration-headshot" onerror="this.style.display=\'none\'">';
        }
        html += '<div class="celebration-emoji">' + c.emoji + '</div>';
        html += '<div class="celebration-title">' + c.title + '</div>';
        if (playerName) {
            html += '<div class="celebration-player">' + (playerPosition ? playerPosition + ' · ' : '') + playerName + '</div>';
        }
        const yards = evt.yards_gained;
        const ydsStr = yards > 0 ? '+' + yards : String(yards);
        html += '<div class="celebration-detail">' + ydsStr + ' yards · ' + (evt.description || '').substring(0, 60) + '</div>';
        if (fptsGained > 0 && tier !== 'turnover') {
            html += '<span class="celebration-fpts">+' + fptsGained.toFixed(1) + ' FPTS</span>';
        }

        if (content) {
            content.innerHTML = html;
        }
        if (confetti) {
            buildConfetti(confetti, tier);
        }

        /* Pause the playback timer (but keep simPlaying=true so dismissCelebration resumes) */
        if (simTimer) { clearInterval(simTimer); simTimer = null; }

        requestAnimationFrame(function() { overlay.classList.add('active'); });

        celebrationTimer = setTimeout(function() { dismissCelebration(); }, CELEBRATION_DURATION);
    }

    function dismissCelebration() {
        const overlay = _q('celebration-overlay');
        if (overlay) overlay.classList.remove('active');
        if (celebrationTimer) { clearTimeout(celebrationTimer); celebrationTimer = null; }
        if (simPlaying && !simTimer) {
            simTimer = setInterval(function() {
                if (simCurrentIndex >= simEvents.length - 1) { pauseSimulation(); return; }
                applyFrame(simCurrentIndex + 1, false);
            }, simBaseDelay / simSpeed);
        }
    }

    function checkCelebration(evt, index, instant) {
        if (instant) return;
        /* Delegate to host page's celebration logic if provided */
        if (cfg.checkCelebration) {
            cfg.checkCelebration(evt, index, instant);
        } else {
            /* Default: basic badge-based celebrations */
            const b = evt.badges || {};
            let tier = null;
            if (b.touchdown) tier = 'touchdown';
            else if (b.turnover) tier = 'turnover';
            else if (b.big_play) tier = 'big-play';
            if (!tier) return;

            const playerName = evt.passer_name || evt.rusher_name || evt.receiver_name || evt.player_name || '';
            const playerHeadshot = evt.passer_headshot_url || evt.rusher_headshot_url || evt.receiver_headshot_url || evt.player_headshot_url || '';
            const playerPosition = evt.player_position || '';

            showCelebration(evt, tier, 0, playerName, playerHeadshot, playerPosition);
        }
    }

    /* ── timeline helpers ───────────────────────────────────────────── */

    function updateActiveRow(index) {
        simEvents.forEach(function(_, i) {
            const row = _q('sim-row-' + i);
            if (!row) return;
            row.classList.toggle('bg-field-50', i === index);
        });
    }

    /* ── public API ─────────────────────────────────────────────────── */

    function init(config) {
        cfg = config || {};
        buildFieldBackground(25);
        drawLOS(25);
        setControlsEnabled(false);
    }

    function setEvents(events) {
        simEvents = events || [];
        simCurrentIndex = 0;
        const progress = _q('sim-progress');
        if (progress) {
            progress.max = String(Math.max(0, simEvents.length - 1));
            progress.value = '0';
        }
        setControlsEnabled(simEvents.length > 0);
    }

    function getEvents() { return simEvents; }
    function getCurrentIndex() { return simCurrentIndex; }
    function isPlaying() { return simPlaying; }

    return {
        /* Lifecycle */
        init: init,
        setEvents: setEvents,
        getEvents: getEvents,
        getCurrentIndex: getCurrentIndex,
        isPlaying: isPlaying,

        /* Field rendering */
        buildFieldBackground: buildFieldBackground,
        drawLOS: drawLOS,
        clearRoutes: clearRoutes,
        drawRoute: drawRoute,

        /* Playback */
        applyFrame: applyFrame,
        playSimulation: playSimulation,
        pauseSimulation: pauseSimulation,
        stepForward: stepForward,
        stepBackward: stepBackward,
        resetSimulation: resetSimulation,
        jumpToPlay: jumpToPlay,
        setSpeed: setSpeed,
        setControlsEnabled: setControlsEnabled,

        /* UI toggles */
        toggleAllRoutes: toggleAllRoutes,
        toggleFlatView: toggleFlatView,

        /* Celebration */
        showCelebration: showCelebration,
        dismissCelebration: dismissCelebration,

        /* Timeline */
        updateActiveRow: updateActiveRow,

        /* Utilities (exposed for host pages) */
        _q: _q,
        _def: _def,
    };
})();

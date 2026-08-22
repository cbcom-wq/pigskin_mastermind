/* ============================================================
   Draft Pick Celebrations
   ============================================================

   Plays a randomly chosen ~2.5s graphic over the draft board when the user
   makes a pick, built from the drafted player's headshot, their club's logo,
   and their club's colors. Styling lives in static/css/draft-celebration.css.

   Requires nfl-team-visuals.js to be loaded first.

   Design notes worth knowing before editing:

   - The stage never blocks the board. It is pointer-events: none and sits at
     z-index 45, below the spotlight panel and the player modal, because AI
     picks keep advancing behind it and the user should be able to watch.
   - Images are remote and unreliable. Headshots 404 for fringe players and are
     blank for defenses, so every animation degrades headshot → team logo →
     a position disc, and no animation waits longer than IMAGE_BUDGET_MS for a
     CDN that is having a bad day.
   - One celebration at a time. A new pick supersedes a running one through
     `runToken`, which also cancels an in-flight image load that would otherwise
     paint a stale player onto the new stage.
   ============================================================ */

const DraftCelebration = (() => {
  'use strict';

  const STORAGE_KEY = 'draft_celebrations';
  const TEARDOWN_MS = 3000;      // longest animation is 2.6s; this is the sweep-up
  const IMAGE_BUDGET_MS = 400;   // run with whatever has arrived by then
  const SHAKE_MS = 260;          // must match .cel-board-shake in the CSS

  const reduceMotion = window.matchMedia
    ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
    : false;

  let enabled = readEnabled();
  let stage = null;
  let timers = [];
  let runToken = 0;
  let lastId = null;

  // ----------------------------------------------------------
  // Preference
  // ----------------------------------------------------------

  function readEnabled() {
    // Anyone who has asked their OS for less motion is opted out and stays
    // opted out — the stored preference does not get to override that.
    if (reduceMotion) return false;
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw === null ? true : raw === '1';
    } catch (e) {
      return true;
    }
  }

  function setEnabled(next) {
    enabled = !!next && !reduceMotion;
    try { localStorage.setItem(STORAGE_KEY, enabled ? '1' : '0'); } catch (e) { /* best effort */ }
    if (!enabled) teardown();
    syncToggle();
    return enabled;
  }

  // ----------------------------------------------------------
  // Header toggle
  // ----------------------------------------------------------

  function toggleButton() {
    return document.getElementById('celebration-toggle');
  }

  function syncToggle() {
    const btn = toggleButton();
    if (!btn) return;
    if (reduceMotion) {
      btn.setAttribute('aria-pressed', 'false');
      btn.disabled = true;
      btn.textContent = 'Replays off · reduced motion';
      btn.title = 'Your system is set to reduce motion, so pick replays stay off.';
      return;
    }
    btn.setAttribute('aria-pressed', enabled ? 'true' : 'false');
    btn.textContent = enabled ? '🎬 Pick replays on' : '🎬 Pick replays off';
    btn.title = enabled
      ? 'Turn off the graphic that plays when you draft someone'
      : 'Turn on the graphic that plays when you draft someone';
  }

  function initToggle() {
    const btn = toggleButton();
    if (!btn) return;
    btn.addEventListener('click', () => setEnabled(!enabled));
    syncToggle();
  }

  // ----------------------------------------------------------
  // Assets
  // ----------------------------------------------------------

  /**
   * Resolve to `url` once it has decoded, or to '' if it errors or does not
   * arrive within `budgetMs`. Never rejects — a missing image is a layout
   * decision here, not an error.
   */
  function loadImage(url, budgetMs) {
    if (!url) return Promise.resolve('');
    return new Promise(resolve => {
      let settled = false;
      const done = value => { if (!settled) { settled = true; resolve(value); } };
      const img = new Image();
      img.onload = () => done(url);
      img.onerror = () => done('');
      img.src = url;
      // A cached image can be complete before the handlers attach.
      if (img.complete && img.naturalWidth > 0) done(url);
      setTimeout(() => done(''), budgetMs);
    });
  }

  // ----------------------------------------------------------
  // Markup helpers
  // ----------------------------------------------------------

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /** Headshot if we have it, else the club logo, else a position disc. */
  function faceEl(ctx, extraClass) {
    const extra = extraClass ? ' ' + extraClass : '';
    if (ctx.headshot) {
      return `<div class="cel-face${extra}"><img src="${esc(ctx.headshot)}" alt=""></div>`;
    }
    if (ctx.logo) {
      return `<div class="cel-face cel-face--logo${extra}"><img src="${esc(ctx.logo)}" alt=""></div>`;
    }
    return `<div class="cel-face cel-face--initials${extra}">${esc(ctx.position || '—')}</div>`;
  }

  /** Club logo, or its abbreviation set in the display face. */
  function markEl(ctx, extraClass) {
    const extra = extraClass ? ' ' + extraClass : '';
    if (ctx.logo) return `<img class="cel-mark${extra}" src="${esc(ctx.logo)}" alt="">`;
    return `<div class="cel-mark cel-mark--text${extra}">${esc(ctx.team || 'FA')}</div>`;
  }

  function nameEl(ctx) { return `<div class="cel-name">${esc(ctx.name)}</div>`; }
  function metaEl(ctx) { return `<div class="cel-meta">${esc(ctx.meta)}</div>`; }
  function eyebrowEl(text) { return `<div class="cel-eyebrow">${esc(text)}</div>`; }
  function wordEl(text) { return `<div class="cel-word">${esc(text)}</div>`; }

  // ----------------------------------------------------------
  // The animations
  //
  // Each declares the class its CSS block is keyed on, the markup for its
  // actors, and the moment (if any) the board should take a hit. The copy is
  // the pick's own vocabulary — a round and pick number, or the phrase a
  // broadcast would use for that exact motion — never a generic "Drafted!".
  // ----------------------------------------------------------

  const ANIMATIONS = [
    {
      id: 'sweep',
      label: 'Light Sweep',
      shakeAt: null,
      build: ctx => `
        <div class="cel-slab"></div>
        <div class="cel-sweep-band">
          ${markEl(ctx)}
          <div class="cel-sweep-copy">
            ${eyebrowEl(ctx.pickLabel)}
            ${nameEl(ctx)}
            ${metaEl(ctx)}
          </div>
          ${faceEl(ctx)}
        </div>`,
    },
    {
      id: 'slam',
      label: 'Card Slam',
      shakeAt: 420,
      build: ctx => `
        <div class="cel-shock"></div>
        <div class="cel-card">
          ${ctx.logo ? `<div class="cel-card-mark"><img src="${esc(ctx.logo)}" alt=""></div>` : ''}
          <div class="cel-card-body">
            ${faceEl(ctx)}
            ${eyebrowEl('On the card')}
            <div class="cel-nameplate">${nameEl(ctx)}</div>
            ${metaEl(ctx)}
          </div>
        </div>`,
    },
    {
      id: 'spotlight',
      label: 'Spotlight',
      shakeAt: null,
      build: ctx => `
        ${ctx.logo ? `<img class="cel-spot-mark" src="${esc(ctx.logo)}" alt="">` : ''}
        <div class="cel-beam"></div>
        <div class="cel-spot-stack">
          ${faceEl(ctx)}
          ${eyebrowEl('The pick is in')}
          ${nameEl(ctx)}
          ${metaEl(ctx)}
        </div>`,
    },
    {
      id: 'hailmary',
      label: 'Hail Mary',
      shakeAt: 1130,
      build: ctx => `
        <div class="cel-arc-x"><div class="cel-arc-y"><div class="cel-arc-spin">
          ${faceEl(ctx)}
        </div></div></div>
        <div class="cel-spike"></div>
        <div class="cel-hail-copy">
          ${wordEl('Caught')}
          ${nameEl(ctx)}
          ${metaEl(ctx)}
        </div>`,
    },
    {
      id: 'blitz',
      label: 'Logo Blitz',
      shakeAt: 1000,
      build: ctx => `
        <div class="cel-speed"></div>
        ${faceEl(ctx, 'cel-blitz-face')}
        ${markEl(ctx, 'cel-blitz-mark')}
        <div class="cel-blitz-copy">
          ${wordEl('Locked up')}
          ${nameEl(ctx)}
          ${metaEl(ctx)}
        </div>`,
    },
    {
      id: 'fieldgoal',
      label: 'Field Goal',
      shakeAt: null,
      build: ctx => `
        <div class="cel-posts">
          <div class="cel-post-up cel-post-up--l"></div>
          <div class="cel-post-up cel-post-up--r"></div>
          <div class="cel-post-bar"></div>
        </div>
        ${faceEl(ctx, 'cel-fg-face')}
        <div class="cel-fg-copy">
          ${wordEl("It's good")}
          ${nameEl(ctx)}
          ${metaEl(ctx)}
        </div>`,
    },
  ];

  /** Random pick that never repeats the previous animation back to back. */
  function chooseAnimation(forcedId) {
    if (forcedId) {
      return ANIMATIONS.find(a => a.id === forcedId) || ANIMATIONS[0];
    }
    const choices = ANIMATIONS.length > 1
      ? ANIMATIONS.filter(a => a.id !== lastId)
      : ANIMATIONS;
    return choices[Math.floor(Math.random() * choices.length)];
  }

  // ----------------------------------------------------------
  // Run
  // ----------------------------------------------------------

  function teardown() {
    timers.forEach(clearTimeout);
    timers = [];
    if (stage) {
      stage.remove();
      stage = null;
    }
  }

  function later(fn, ms) {
    timers.push(setTimeout(fn, ms));
  }

  function shakeBoard() {
    const board = document.getElementById('draft-app');
    if (!board) return;
    board.classList.remove('cel-board-shake');
    void board.offsetWidth;               // restart the animation
    board.classList.add('cel-board-shake');
    later(() => board.classList.remove('cel-board-shake'), SHAKE_MS + 40);
  }

  function paintTeamColors(el, team) {
    const accent = NFLTeamVisuals.accent(team);
    el.style.setProperty('--cel-team', accent);
    el.style.setProperty('--cel-team-deep', NFLTeamVisuals.mix(accent, '#0b1220', 0.66));
    el.style.setProperty('--cel-team-mid', NFLTeamVisuals.mix(accent, '#0b1220', 0.45));
    el.style.setProperty('--cel-team-a70', NFLTeamVisuals.rgba(accent, 0.7));
    el.style.setProperty('--cel-team-a60', NFLTeamVisuals.rgba(accent, 0.6));
    el.style.setProperty('--cel-team-a55', NFLTeamVisuals.rgba(accent, 0.55));
    el.style.setProperty('--cel-team-a12', NFLTeamVisuals.rgba(accent, 0.12));
  }

  /**
   * Play a celebration for one pick.
   *
   * @param {object} player  A draft-pool player: name, position, nfl_team,
   *                         headshot_url.
   * @param {object} [opts]  round / pick for the copy, and `animation` to force
   *                         a specific one by id (used by preview()).
   */
  function play(player, opts) {
    const options = opts || {};
    if (reduceMotion || !player) return;
    if (!enabled && !options.force) return;

    const token = ++runToken;
    teardown();

    const team = player.nfl_team || '';
    Promise.all([
      loadImage(player.headshot_url, IMAGE_BUDGET_MS),
      loadImage(NFLTeamVisuals.logoUrl(team), IMAGE_BUDGET_MS),
    ]).then(([headshot, logo]) => {
      // A newer pick landed while the CDN was thinking, or the user switched
      // replays off mid-load. Drop this one.
      if (token !== runToken) return;
      if (!enabled && !options.force) return;

      const position = player.position || '';
      const ctx = {
        name: player.name || 'Your pick',
        position,
        team,
        headshot,
        logo,
        meta: [position, team].filter(Boolean).join(' · '),
        pickLabel: options.round && options.pick
          ? `Round ${options.round} · Pick ${options.pick}`
          : 'Your pick',
      };

      const animation = chooseAnimation(options.animation);
      lastId = animation.id;

      stage = document.createElement('div');
      stage.className = `cel-stage cel--${animation.id}`;
      stage.setAttribute('aria-hidden', 'true');
      paintTeamColors(stage, team);
      stage.innerHTML = `<div class="cel-vignette"></div>${animation.build(ctx)}`;
      document.body.appendChild(stage);

      if (animation.shakeAt !== null) later(shakeBoard, animation.shakeAt);
      later(teardown, TEARDOWN_MS);
    });
  }

  /**
   * Play a named animation with stand-in data, ignoring the on/off preference.
   * For eyeballing the set from the console: DraftCelebration.preview('blitz').
   */
  function preview(id, player) {
    play(player || {
      name: 'Christian McCaffrey',
      position: 'RB',
      nfl_team: 'SF',
      headshot_url: 'https://a.espncdn.com/i/headshots/nfl/players/full/3117251.png',
    }, { animation: id, round: 1, pick: 4, force: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initToggle);
  } else {
    initToggle();
  }

  return {
    play,
    preview,
    teardown,
    initToggle,
    isEnabled: () => enabled,
    setEnabled,
    toggle: () => setEnabled(!enabled),
    animations: () => ANIMATIONS.map(a => ({ id: a.id, label: a.label })),
  };
})();

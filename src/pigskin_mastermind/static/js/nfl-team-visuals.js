/* ============================================================
   NFL Team Visuals — logos and colors, shared across pages
   ============================================================

   One place for "what does this franchise look like".  Two consumers today:
   the live scoreboard cards and the draft-board pick celebrations.

   The logo URLs mirror `services/draft_recap.py::team_logo_url` — same ESPN
   CDN, same Washington spelling quirk. Keep the two in sync; the Python side
   is the source of truth for anything server-rendered.
   ============================================================ */

const NFLTeamVisuals = (() => {
  'use strict';

  const LOGO_BASE = 'https://a.espncdn.com/i/teamlogos/nfl/500';

  // ESPN's CDN spells Washington `wsh`; utils.nfl_teams.normalize_team gives
  // `WAS`. Every other abbreviation matches once lowercased.
  const LOGO_ABBR_OVERRIDES = { WAS: 'wsh' };

  // Values normalize_team uses for "no NFL team", which have no logo.
  const TEAMLESS = ['FA', 'NONE', ''];

  // [primary, secondary]. Primaries are the values the scoreboard has always
  // used, so nothing there changes appearance. Secondaries are each club's
  // second identity color — Bears orange, Packers gold, Seahawks action green —
  // and exist because half the league's primary is a navy that disappears
  // against the dark draft board.
  const TEAM_COLORS = {
    ARI: ['#97233F', '#FFB612'], ATL: ['#A71930', '#A5ACAF'],
    BAL: ['#241773', '#9E7C0C'], BUF: ['#00338D', '#C60C30'],
    CAR: ['#0085CA', '#BFC0BF'], CHI: ['#0B162A', '#C83803'],
    CIN: ['#FB4F14', '#FFFFFF'], CLE: ['#311D00', '#FF3C00'],
    DAL: ['#041E42', '#869397'], DEN: ['#FB4F14', '#002244'],
    DET: ['#0076B6', '#B0B7BC'], GB:  ['#203731', '#FFB612'],
    HOU: ['#03202F', '#A71930'], IND: ['#002C5F', '#A2AAAD'],
    JAX: ['#006778', '#D7A22A'], KC:  ['#E31837', '#FFB81C'],
    LV:  ['#000000', '#A5ACAF'], LAC: ['#0080C6', '#FFC20E'],
    LAR: ['#003594', '#FFA300'], MIA: ['#008E97', '#FC4C02'],
    MIN: ['#4F2683', '#FFC62F'], NE:  ['#002244', '#C60C30'],
    NO:  ['#D3BC8D', '#101820'], NYG: ['#0B2265', '#A71930'],
    NYJ: ['#125740', '#FFFFFF'], PHI: ['#004C54', '#A5ACAF'],
    PIT: ['#FFB612', '#101820'], SF:  ['#AA0000', '#B3995D'],
    SEA: ['#002244', '#69BE28'], TB:  ['#D50A0A', '#FF7900'],
    TEN: ['#0C2340', '#4B92DB'], WAS: ['#773141', '#FFB612'],
  };

  const NEUTRAL = ['#64748b', '#cbd5e1'];

  // Below this WCAG relative luminance a color reads as "black" against the
  // slate board and stops working as an accent.
  const DARK_FLOOR = 0.05;

  function key(abbr) {
    return String(abbr || '').trim().toUpperCase();
  }

  function pair(abbr) {
    return TEAM_COLORS[key(abbr)] || NEUTRAL;
  }

  function rgb(hex) {
    const h = hex.replace('#', '');
    return [
      parseInt(h.slice(0, 2), 16),
      parseInt(h.slice(2, 4), 16),
      parseInt(h.slice(4, 6), 16),
    ];
  }

  /** WCAG relative luminance, 0 (black) to 1 (white). */
  function luminance(hex) {
    const channels = rgb(hex).map(v => {
      const c = v / 255;
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
  }

  /** Mix a hex toward white by `amount` (0–1). */
  function lighten(hex, amount) {
    const out = rgb(hex).map(v => Math.round(v + (255 - v) * amount));
    return '#' + out.map(v => v.toString(16).padStart(2, '0')).join('');
  }

  return {
    /** Primary club color. Returns slate-500 for unknown teams. */
    color(abbr) {
      return pair(abbr)[0];
    },

    /** Both club colors as [primary, secondary]. */
    pair(abbr) {
      return pair(abbr).slice();
    },

    /**
     * A club color guaranteed to separate from a dark background: the primary
     * where it is bright enough, otherwise the secondary, otherwise the
     * primary lifted toward white.
     */
    accent(abbr) {
      const [primary, secondary] = pair(abbr);
      if (luminance(primary) >= DARK_FLOOR) return primary;
      if (luminance(secondary) >= DARK_FLOOR) return secondary;
      return lighten(primary, 0.55);
    },

    /** ESPN logo URL, or '' for free agents and unknown teams. */
    logoUrl(abbr) {
      const abbrev = key(abbr);
      if (TEAMLESS.indexOf(abbrev) !== -1) return '';
      return `${LOGO_BASE}/${LOGO_ABBR_OVERRIDES[abbrev] || abbrev.toLowerCase()}.png`;
    },

    /** `hex` at `alpha` as an rgba() string. */
    rgba(hex, alpha) {
      const [r, g, b] = rgb(hex);
      return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    },

    /** Blend `hex` toward `toward` by `amount` (0–1). */
    mix(hex, toward, amount) {
      const a = rgb(hex);
      const b = rgb(toward);
      const out = a.map((v, i) => Math.round(v + (b[i] - v) * amount));
      return '#' + out.map(v => v.toString(16).padStart(2, '0')).join('');
    },

    luminance,
    lighten,
  };
})();

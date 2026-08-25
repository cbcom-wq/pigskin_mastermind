/* Draft recap page.
 *
 * Only two things need a client here: the opening count-up on the hero
 * numbers, and the per-player simulation button.  Everything else is rendered
 * server-side by draft_recap.py.
 */
(function () {
  'use strict';

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ── Hero count-up ────────────────────────────────────────────────────
  function countUp(el) {
    var target = parseFloat(el.dataset.count);
    if (isNaN(target)) return;

    var decimals = (el.dataset.count.split('.')[1] || '').length;
    if (reduceMotion) {
      el.textContent = target.toFixed(decimals);
      return;
    }

    var duration = 900;
    var start = null;

    function frame(now) {
      if (start === null) start = now;
      var progress = Math.min((now - start) / duration, 1);
      // Ease out, so the number decelerates into its final value.
      var eased = 1 - Math.pow(1 - progress, 3);
      el.textContent = (target * eased).toFixed(decimals);
      if (progress < 1) requestAnimationFrame(frame);
    }

    // The server already rendered the real number.  Zeroing it here would
    // strand the element at 0 in a background tab, where requestAnimationFrame
    // never runs — so the reset happens inside the first frame instead.
    requestAnimationFrame(function (now) {
      frame(now);
    });
  }

  document.querySelectorAll('[data-count]').forEach(countUp);

  // ── Per-player simulation ────────────────────────────────────────────
  function percent(value) {
    return Math.round(value * 100) + '%';
  }

  function renderResult(output, data) {
    output.hidden = false;
    output.classList.remove('is-error');
    output.innerHTML =
      'Simulated week 1: <strong>' + data.expected_points + '</strong> expected, ' +
      'floor <strong>' + data.floor + '</strong>, ceiling <strong>' + data.ceiling + '</strong>.<br>' +
      'Boom ' + percent(data.boom_probability) + ' &middot; bust ' + percent(data.bust_probability);
  }

  function renderError(output, message) {
    output.hidden = false;
    output.classList.add('is-error');
    output.textContent = message;
  }

  async function simulate(button) {
    var output = button.nextElementSibling;
    var label = button.textContent;

    button.disabled = true;
    button.textContent = 'Simulating…';

    try {
      var response = await fetch(
        '/draft/recap/' + encodeURIComponent(button.dataset.draftId) +
        '/simulate/' + encodeURIComponent(button.dataset.dbId),
        { method: 'POST' }
      );

      if (!response.ok) {
        renderError(output, "Couldn't simulate this player.");
        return;
      }

      var data = await response.json();
      if (data.ok) {
        renderResult(output, data);
        button.remove();
        return;
      }
      renderError(output, "Couldn't simulate — not enough local data for this player.");
    } catch (err) {
      renderError(output, "Couldn't reach the server. The numbers above still stand.");
    } finally {
      if (button.isConnected) {
        button.disabled = false;
        button.textContent = label;
      }
    }
  }

  document.querySelectorAll('.rc-sim').forEach(function (button) {
    button.addEventListener('click', function () { simulate(button); });
  });

  // ── Commit to a season league ────────────────────────────────────────
  var commitBtn = document.getElementById('commit-league-btn');
  if (commitBtn) {
    commitBtn.addEventListener('click', async function () {
      var errorEl = document.getElementById('commit-error');
      errorEl.hidden = true;
      var label = commitBtn.textContent;
      commitBtn.disabled = true;
      commitBtn.textContent = 'Creating…';

      try {
        var response = await fetch('/season/commit-draft', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            draft_id: commitBtn.dataset.draftId,
            name: document.getElementById('commit-league-name').value || 'My League',
            user_team_name: document.getElementById('commit-team-name').value || 'My Team',
          }),
        });
        var body = await response.json();

        if (response.ok) {
          window.location.href = body.redirect_url;
          return;
        }

        var detail = body.detail;
        errorEl.textContent = typeof detail === 'string'
          ? detail
          : detail.message + ': ' + detail.unresolved.map(function (u) { return u.name; }).join(', ');
        errorEl.hidden = false;
      } catch (err) {
        errorEl.textContent = "Couldn't reach the server.";
        errorEl.hidden = false;
      } finally {
        if (commitBtn.isConnected) {
          commitBtn.disabled = false;
          commitBtn.textContent = label;
        }
      }
    });
  }
})();

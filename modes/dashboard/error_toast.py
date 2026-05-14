"""
modes/dashboard/error_toast.py
==============================

Shared top-right "toast" notification widget used by every dashboard
page. Polls `/api/errors` every 5 s, surfaces external-API failures
with exact error text, and links to `/login` when the failure is
auth-shaped (Zerodha rejected the token).

The widget is intentionally one self-contained <div> + <script>
block so any page can mount it by just calling
`error_toast_html() + error_toast_script()` somewhere inside <body>.
The poller's last-seen-id cursor is persisted in
`localStorage.errSinkLastSeenId` (S51, 2026-05-14) so it survives
page navigation; first-ever load uses `?init=1` to bookmark the
current high-water mark without rendering historical toasts. The
poll itself sends `max_age_secs=300` as a server-side belt-and-
braces filter against wiped localStorage replaying ancient errors.
"""

from __future__ import annotations


_TOAST_HTML = """\
<div id="err-toast-host"
     style="position:fixed;top:14px;right:14px;z-index:9999;
            display:flex;flex-direction:column;gap:8px;
            max-width:380px;font-family:-apple-system,'Segoe UI',Roboto,sans-serif;
            font-size:13px;pointer-events:none"></div>
"""


_TOAST_SCRIPT = r"""
<script>
// ── External-API error toast (2026-05-14) ──────────────────────
//
// Polls /api/errors every 5 s, renders top-right toast cards for
// any error newer than what the page has already shown. Clicking
// the X dismisses a toast; auth-shaped errors include a "Re-login"
// CTA that opens /login. The poller is silent on its own failures
// (network blip during polling shouldn't add MORE toasts).
//
// last-seen id persistence (2026-05-14, S51):
// Stored in localStorage under "errSinkLastSeenId" so it survives
// page navigation / refresh / tab-close-and-reopen within the same
// browser. Pre-fix, the variable lived only on `window` and reset
// to 0 on every navigation, so e.g. a laptop-sleep-resume that
// accumulated 20 stale Zerodha network errors would re-spawn all
// 20 toasts on every page change. First-ever load (no localStorage
// value) does an `?init=1` call to bookmark the current high-water
// mark without rendering ANY pre-existing errors as toasts. Belt-
// and-braces server-side: regular polls send `max_age_secs=300` so
// even a wiped localStorage can't surface errors >5min old.
(function () {
    if (window._errToastInstalled) return;
    window._errToastInstalled = true;

    var LS_KEY = 'errSinkLastSeenId';
    var MAX_AGE_SECS = 300;   // 5 min — server-side stale-error filter

    function _getLastSeen() {
        try {
            var v = window.localStorage.getItem(LS_KEY);
            var n = parseInt(v, 10);
            return isNaN(n) || n < 0 ? 0 : n;
        } catch (e) { return 0; }
    }
    function _setLastSeen(id) {
        try { window.localStorage.setItem(LS_KEY, String(id)); }
        catch (e) { /* private mode / quota — fall back to in-memory */ }
        window._errSinkLastSeenId = id;
    }

    var COLOURS = {
        auth:       { bg: '#7a1f1f', fg: '#fff', accent: '#ffd1d1' },
        rate_limit: { bg: '#7a5500', fg: '#fff', accent: '#ffe6b3' },
        network:    { bg: '#404a55', fg: '#fff', accent: '#cfd9eb' },
        other:      { bg: '#3d3d3d', fg: '#fff', accent: '#dddddd' }
    };

    function _fmtTime(ts) {
        try {
            var d = new Date((ts || 0) * 1000);
            var hh = String(d.getHours()).padStart(2, '0');
            var mm = String(d.getMinutes()).padStart(2, '0');
            var ss = String(d.getSeconds()).padStart(2, '0');
            return hh + ':' + mm + ':' + ss;
        } catch (e) { return ''; }
    }

    function _renderToast(err) {
        var host = document.getElementById('err-toast-host');
        if (!host) return;
        var c = COLOURS[err.kind] || COLOURS.other;
        var card = document.createElement('div');
        card.style.cssText =
            'background:' + c.bg + ';color:' + c.fg + ';' +
            'border-radius:6px;padding:10px 12px;' +
            'box-shadow:0 4px 14px rgba(0,0,0,0.18);' +
            'pointer-events:auto;line-height:1.4';
        var head = document.createElement('div');
        head.style.cssText =
            'display:flex;justify-content:space-between;' +
            'align-items:center;margin-bottom:4px;font-weight:600';
        var title = document.createElement('span');
        title.textContent =
            (err.source || 'api').toUpperCase()
            + ' · ' + (err.kind || 'other')
            + (err.ts ? ' · ' + _fmtTime(err.ts) : '');
        head.appendChild(title);
        var close = document.createElement('span');
        close.textContent = '×';
        close.style.cssText =
            'cursor:pointer;font-size:18px;line-height:1;' +
            'padding:0 4px;color:' + c.accent;
        close.onclick = function () { card.remove(); };
        head.appendChild(close);
        card.appendChild(head);

        var msg = document.createElement('div');
        msg.textContent = err.message || '';
        msg.style.cssText = 'word-break:break-word;font-size:12.5px';
        card.appendChild(msg);

        if (err.auth_invalid) {
            var cta = document.createElement('a');
            cta.href = '/login';
            cta.textContent = 'Open login page →';
            cta.style.cssText =
                'display:inline-block;margin-top:8px;color:' + c.accent +
                ';text-decoration:underline;font-weight:600';
            card.appendChild(cta);
        }

        host.appendChild(card);

        // Auto-dismiss non-auth toasts after 10 s; auth toasts stay
        // until the user clicks them so they can't be missed.
        if (!err.auth_invalid) {
            setTimeout(function () {
                if (card.parentNode) card.remove();
            }, 10000);
        }
    }

    function _pollErrors() {
        var since = _getLastSeen();
        var url = '/api/errors?since=' + since +
                  '&max_age_secs=' + MAX_AGE_SECS;
        fetch(url)
            .then(function (r) { return r.json(); })
            .then(function (j) {
                var errs = (j && j.errors) || [];
                if (!errs.length) {
                    // Even with no new errors, advance the cursor to
                    // server's max_id so old (>5min) errors that
                    // exist in the sink but were age-filtered don't
                    // ever surface if we later see a newer one.
                    if (typeof j.max_id === 'number'
                            && j.max_id > _getLastSeen()) {
                        _setLastSeen(j.max_id);
                    }
                    return;
                }
                var newMax = _getLastSeen();
                errs.forEach(function (e) {
                    if (e.id > newMax) newMax = e.id;
                    _renderToast(e);
                });
                if (newMax > _getLastSeen()) _setLastSeen(newMax);
            })
            .catch(function () { /* silent — never spawn toast-on-toast */ });
    }

    function _initThenPoll() {
        // First-ever browser load (no localStorage entry): hit the
        // ?init=1 path to grab the current high-water mark without
        // surfacing pre-existing errors as toasts. Then start the
        // regular poll loop.
        var hasSeen = false;
        try { hasSeen = window.localStorage.getItem(LS_KEY) !== null; }
        catch (e) { hasSeen = false; }
        if (hasSeen) {
            // Existing client — go straight to a normal poll.
            _pollErrors();
            return;
        }
        fetch('/api/errors?init=1')
            .then(function (r) { return r.json(); })
            .then(function (j) {
                if (j && typeof j.max_id === 'number') {
                    _setLastSeen(j.max_id);
                } else {
                    _setLastSeen(0);
                }
            })
            .catch(function () { _setLastSeen(0); })
            .finally(function () {
                // First real poll after init — quickly so any
                // genuinely fresh error still surfaces.
                setTimeout(_pollErrors, 400);
            });
    }

    window.addEventListener('DOMContentLoaded', function () {
        _initThenPoll();
        setInterval(_pollErrors, 5000);
    });
})();
</script>
"""


def error_toast_html() -> str:
    """The empty toast container. Place inside <body>."""
    return _TOAST_HTML


def error_toast_script() -> str:
    """The poller + render script. Place inside <body> after the
    container div."""
    return _TOAST_SCRIPT

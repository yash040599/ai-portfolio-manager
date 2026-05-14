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
No global state outside `window._errSinkLastSeenId`.
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
(function () {
    if (window._errToastInstalled) return;
    window._errToastInstalled = true;
    window._errSinkLastSeenId = window._errSinkLastSeenId || 0;

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
        fetch('/api/errors?since=' + (window._errSinkLastSeenId || 0))
            .then(function (r) { return r.json(); })
            .then(function (j) {
                var errs = (j && j.errors) || [];
                if (!errs.length) return;
                errs.forEach(function (e) {
                    if (e.id > (window._errSinkLastSeenId || 0)) {
                        window._errSinkLastSeenId = e.id;
                    }
                    _renderToast(e);
                });
            })
            .catch(function () { /* silent — never spawn toast-on-toast */ });
    }

    window.addEventListener('DOMContentLoaded', function () {
        // First poll quickly so any error already in the sink at
        // page-load time surfaces immediately.
        setTimeout(_pollErrors, 400);
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

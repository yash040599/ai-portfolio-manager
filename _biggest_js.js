
function _swingBanner(msg, kind) {
    var host = document.getElementById('swing-job-banner');
    if (!host) return;
    var spin = (kind === 'info') ? '<span class="spinner"></span> ' : '';
    host.innerHTML = '<div class="banner ' + kind + '">' + spin + msg + '</div>';
}

function _swingDisableButtons(disabled) {
    document.querySelectorAll('button.action').forEach(function(b) {
        b.disabled = disabled;
    });
}

function runSwingScan() {
    var aiToggle = document.getElementById('swing-ai-toggle');
    var mode = (aiToggle && aiToggle.checked) ? 'AI' : 'NOAI';

    // AI cost confirm — origin 2026-05-14 user feedback ("ran AI
    // mode and it ran no stop until I stopped it"). Echo the
    // server-side cap + per-call so the dialog matches the same
    // numbers shown above the Run Scan button.
    if (mode === 'AI') {
        var perCall = window._swingAiPerCall || 3.0;
        var cap = window._swingAiCap || 15;
        var maxCost = (perCall * cap).toFixed(0);
        if (!confirm('Claude AI overlay will be added on top of the NoAI scan.\n\n' +
                     'Cost cap: ~Rs.' + maxCost + ' for up to ' + cap +
                     ' top-priority candidates (Rs.' + perCall.toFixed(0) +
                     '/stock).\n\nProceed?')) {
            return;
        }
    }

    // If a run already exists today, ask before rerunning
    if (window._swingHasRunToday) {
        var lastMode = window._swingLastMode || 'NoAI';
        if (mode === 'AI' && lastMode === 'NOAI') {
            if (!confirm('Today\'s scan was NoAI. Run again with AI overlay?\n' +
                         'This will add qualitative analysis on top of the existing scan.')) {
                return;
            }
        } else {
            if (!confirm('A ' + lastMode + ' swing scan already ran today.\n' +
                         'Rerun the analysis? (e.g. after code improvements)')) {
                return;
            }
        }
    }

    // Read capital from input
    var capitalEl = document.getElementById('swing-capital');
    var capital = capitalEl ? parseFloat(capitalEl.value.replace(/,/g, '')) : 0;

    // Show loading immediately
    _swingBanner('Starting swing scan (' + mode + ')\u2026 this can take 2-5 minutes for NIFTY 100.', 'info');
    _swingDisableButtons(true);

    fetch('/api/swing/run?mode=' + mode + '&capital=' + capital, {method: 'POST'})
        .then(r => r.json())
        .then(d => {
            if (d.status === 'RUNNING') {
                _swingBanner('Swing scan running (job #' + d.job_id + ', ' + d.mode + ')\u2026', 'info');
                _pollSwingStatus();
            } else {
                _swingBanner('Scan submitted.', 'info');
                _pollSwingStatus();
            }
        })
        .catch(e => {
            _swingBanner('Error: ' + e, 'warn');
            _swingDisableButtons(false);
        });
}

function _pollSwingStatus() {
    setTimeout(function() {
        fetch('/api/swing/run_status')
            .then(r => r.json())
            .then(d => {
                if (d.status === 'RUNNING') {
                    _swingBanner('Swing scan running (job #' + d.job_id + ', ' + d.mode + ')\u2026', 'info');
                    _pollSwingStatus();
                } else if (d.status === 'DONE') {
                    if (d.error) {
                        _swingBanner('Scan completed with note: ' + d.error, 'warn');
                        setTimeout(function() { location.reload(); }, 2000);
                    } else {
                        _swingBanner('Scan complete \u2014 refreshing page\u2026', 'info');
                        setTimeout(function() { location.reload(); }, 1200);
                    }
                } else if (d.status === 'FAILED') {
                    _swingBanner('Scan FAILED: ' + (d.error || 'unknown error'), 'warn');
                    _swingDisableButtons(false);
                } else {
                    _swingDisableButtons(false);
                }
            })
            .catch(function() { _pollSwingStatus(); });
    }, 2000);
}

// On page load: if a job is already running, show the banner + poll
window.addEventListener('DOMContentLoaded', function() {
    fetch('/api/swing/run_status')
        .then(r => r.json())
        .then(d => {
            if (d && d.status === 'RUNNING') {
                _swingBanner('Swing scan already running (job #' + d.job_id + ')\u2026', 'info');
                _swingDisableButtons(true);
                _pollSwingStatus();
            }
        });
});

function _parsePosNum(raw, label) {
    // Defensive parse: returns the number when raw is a positive
    // integer/float string, or null when it's empty / negative /
    // non-numeric. The server-side endpoints reject the same cases,
    // but failing early in the browser saves a round-trip and gives
    // an instantly-readable error to the user.
    if (raw === null || raw === undefined) return null;
    var s = String(raw).trim();
    if (!s) return null;
    var n = Number(s);
    if (!isFinite(n) || isNaN(n) || n <= 0) {
        alert('Please enter a positive number for ' + label + ' (got "' + raw + '").');
        return null;
    }
    return n;
}

function confirmAction(actionId) {
    // Show a modal dialog with qty + price fields together
    var overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;' +
        'background:rgba(0,0,0,0.4);z-index:1000;display:flex;' +
        'align-items:center;justify-content:center';
    overlay.innerHTML =
        '<div style="background:white;border-radius:10px;padding:24px 28px;' +
        'min-width:320px;max-width:400px;box-shadow:0 8px 32px rgba(0,0,0,0.2)">' +
        '<h3 style="margin:0 0 16px;font-size:16px">Confirm Purchase</h3>' +
        '<label style="font-size:13px;font-weight:500;display:block;margin-bottom:4px">' +
        'Quantity (shares)</label>' +
        '<input id="buy-qty" type="number" min="1" step="1" ' +
        'style="width:100%;padding:8px 10px;font:inherit;border:1px solid #cfd9eb;' +
        'border-radius:5px;margin-bottom:12px;font-size:15px" autofocus />' +
        '<label style="font-size:13px;font-weight:500;display:block;margin-bottom:4px">' +
        'Price per share (Rs.)</label>' +
        '<input id="buy-price" type="number" min="0.01" step="0.05" ' +
        'style="width:100%;padding:8px 10px;font:inherit;border:1px solid #cfd9eb;' +
        'border-radius:5px;margin-bottom:12px;font-size:15px" />' +
        '<label style="font-size:13px;font-weight:500;display:block;margin-bottom:4px">' +
        'Stop-loss price (Rs.) <span style="color:var(--muted);font-weight:400">' +
        '— optional, leave blank for default</span></label>' +
        '<input id="buy-stop" type="number" min="0" step="0.05" ' +
        'style="width:100%;padding:8px 10px;font:inherit;border:1px solid #cfd9eb;' +
        'border-radius:5px;margin-bottom:16px;font-size:15px" />' +
        '<div style="display:flex;gap:8px;justify-content:flex-end">' +
        '<button id="buy-cancel" class="action alt" style="padding:8px 16px">Cancel</button>' +
        '<button id="buy-submit" class="action" style="padding:8px 16px">Confirm</button>' +
        '</div></div>';
    document.body.appendChild(overlay);

    // Focus qty field
    setTimeout(function() { document.getElementById('buy-qty').focus(); }, 50);

    // Close on overlay click or Cancel
    overlay.addEventListener('click', function(e) {
        if (e.target === overlay) { document.body.removeChild(overlay); }
    });
    document.getElementById('buy-cancel').onclick = function() {
        document.body.removeChild(overlay);
    };

    // Submit
    document.getElementById('buy-submit').onclick = function() {
        var qty = _parsePosNum(document.getElementById('buy-qty').value, 'quantity');
        if (qty === null) return;
        var price = _parsePosNum(document.getElementById('buy-price').value, 'price');
        if (price === null) return;
        var stopVal = document.getElementById('buy-stop').value.trim();
        var stop = 0;
        if (stopVal) {
            var s = _parsePosNum(stopVal, 'stop');
            if (s === null) return;
            stop = s;
        }
        document.body.removeChild(overlay);
        fetch('/api/swing/actions/' + actionId + '/confirm', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({qty: Math.floor(qty), price: price, stop: stop})
        })
            .then(function(r) { return r.json().then(function(j) { return {ok: r.ok, body: j}; }); })
            .then(function(res) {
                if (!res.ok || !res.body.ok) {
                    alert('Confirm failed: ' + (res.body.error || 'unknown error'));
                    return;
                }
                location.reload();
            })
            .catch(function(e) { alert('Network error: ' + e); });
    };

    // Enter key submits
    overlay.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') { document.getElementById('buy-submit').click(); }
        if (e.key === 'Escape') { document.body.removeChild(overlay); }
    });
}

function addAction(selectEl, actionId, symbol) {
    var choice = selectEl.value;
    if (!choice) return;
    // Reset dropdown so it shows Add+ again
    selectEl.selectedIndex = 0;

    if (choice === 'watch') {
        fetch('/api/swing/watchlist/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({action_id: actionId, symbol: symbol})
        })
            .then(function(r) { return r.json(); })
            .then(function(j) {
                if (j.ok) { location.reload(); }
                else { alert('Failed: ' + (j.error || 'unknown')); }
            })
            .catch(function(e) { alert('Error: ' + e); });
    } else if (choice === 'buy') {
        confirmAction(actionId);
    }
}

function promoteWatchlist(watchlistId, symbol) {
    var qtyRaw = prompt(symbol + ' — How many shares did you buy?');
    var qty = _parsePosNum(qtyRaw, 'quantity');
    if (qty === null) return;
    var priceRaw = prompt('At what price (Rs.)?');
    var price = _parsePosNum(priceRaw, 'price');
    if (price === null) return;
    var stopRaw = prompt('Stop-loss price (Rs.) — leave blank for 10% below buy:', '');
    var stop = 0;
    if (stopRaw && stopRaw.trim()) {
        var s = _parsePosNum(stopRaw, 'stop');
        if (s === null) return;
        stop = s;
    }
    fetch('/api/swing/watchlist/' + watchlistId + '/promote', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({qty: Math.floor(qty), price: price, stop: stop})
    })
        .then(function(r) { return r.json(); })
        .then(function(j) {
            if (j.ok) { location.reload(); }
            else { alert('Failed: ' + (j.error || 'unknown')); }
        })
        .catch(function(e) { alert('Error: ' + e); });
}

function removeWatchlist(watchlistId) {
    if (!confirm('Remove from watchlist? It will go back to recommendations.')) return;
    fetch('/api/swing/watchlist/' + watchlistId + '/remove', {
        method: 'POST'
    })
        .then(function(r) { return r.json(); })
        .then(function(j) {
            if (j.ok) { location.reload(); }
            else { alert('Failed: ' + (j.error || 'unknown')); }
        })
        .catch(function(e) { alert('Error: ' + e); });
}

// `skipAction` removed in S46 (2026-05-14): the Skip button was a
// no-op in a permanently-report-only world (the bot never auto-acts
// on un-skipped rows; PENDING is just a "not yet noted" marker), so
// the per-row "Done | Skip" pair collapsed to a single Add+ button.
// The server endpoint `/api/swing/actions/<id>/skip` and the
// `skip_action()` persistence helper are kept for the CLI
// `--mode swing --skip <ID>` path which is still useful for
// scripting / batch reviews.

function exitPosition(posId) {
    var qtyRaw = prompt('Exit quantity:');
    var qty = _parsePosNum(qtyRaw, 'quantity');
    if (qty === null) return;
    var priceRaw = prompt('Exit price (Rs.):');
    var price = _parsePosNum(priceRaw, 'price');
    if (price === null) return;
    fetch('/api/swing/positions/' + posId + '/exit', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({qty: Math.floor(qty), price: price})
    })
        .then(function(r) { return r.json().then(function(j) { return {ok: r.ok, body: j}; }); })
        .then(function(res) {
            if (!res.ok || !res.body.ok) {
                alert('Exit failed: ' + (res.body.error || 'unknown error'));
                return;
            }
            location.reload();
        })
        .catch(function(e) { alert('Network error: ' + e); });
}

// ── Live-price poller (2026-05-14 fix) ─────────────────────────
//
// Origin: user reported the dashboard claimed prices refreshed
// every 5 seconds but they were actually frozen at page-render
// time. This poller does the work the copy was promising:
//
//   1. Walk every `[data-live-symbol]` element on the page,
//      collect the unique set of symbols actually visible.
//   2. POST /api/live_prices?symbols=A,B,C — backed by the
//      existing rate-limited get_live_quotes() helper, so the
//      Zerodha broker is never hit faster than once per 5s
//      regardless of how many polls fire.
//   3. For each `[data-live-symbol] [data-live-field]` cell,
//      rewrite ONLY the live values (price / pnl / r-mult /
//      price_with_change). Avg / qty / entry / stop / target
//      have no markers and therefore never get touched.
//
// Quiet on errors: a failed poll leaves the previous DOM untouched
// so a network blip doesn't blank out the table.
function _swingPollLivePrices() {
    var nodes = document.querySelectorAll('[data-live-symbol]');
    var symbols = [];
    var seen = {};
    nodes.forEach(function (n) {
        var s = n.getAttribute('data-live-symbol');
        if (s && !seen[s]) { seen[s] = true; symbols.push(s); }
    });
    if (!symbols.length) return;
    fetch('/api/live_prices?symbols=' + encodeURIComponent(symbols.join(',')))
        .then(function (r) { return r.json(); })
        .then(function (j) {
            var quotes = (j && j.quotes) || {};
            nodes.forEach(function (row) {
                var sym = row.getAttribute('data-live-symbol');
                var q = quotes[sym] || {};
                var price = Number(q.price);
                if (!isFinite(price) || price <= 0) return;
                var change = Number(q.change_pct) || 0;
                var chgCls = change >= 0 ? 'pos' : 'neg';
                // Update each marked cell within this row.
                row.querySelectorAll('[data-live-field]').forEach(function (cell) {
                    var field = cell.getAttribute('data-live-field');
                    if (field === 'price') {
                        cell.textContent = 'Rs.' + price.toLocaleString(
                            'en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
                    } else if (field === 'price_with_change') {
                        cell.innerHTML = '<span class="' + chgCls + '">Rs.'
                            + price.toLocaleString('en-IN',
                                { minimumFractionDigits: 2, maximumFractionDigits: 2 })
                            + '</span> <span class="muted">('
                            + (change >= 0 ? '+' : '') + change.toFixed(1) + '%)</span>';
                    } else if (field === 'pnl') {
                        var entry = Number(row.getAttribute('data-entry-price'));
                        var qty = Number(row.getAttribute('data-managed-qty'));
                        if (isFinite(entry) && isFinite(qty) && entry > 0 && qty > 0) {
                            var upnl = (price - entry) * qty;
                            var pnlCls = upnl >= 0 ? 'pos' : 'neg';
                            cell.innerHTML = '<span class="' + pnlCls + '">Rs.'
                                + (upnl >= 0 ? '+' : '')
                                + upnl.toLocaleString('en-IN',
                                    { minimumFractionDigits: 2, maximumFractionDigits: 2 })
                                + '</span>';
                        }
                    } else if (field === 'r_mult') {
                        var entry2 = Number(row.getAttribute('data-entry-price'));
                        // We don't have stop in a data-attr; this cell
                        // is informational on swing positions where
                        // stop comes from the backend on next reload.
                        // Leave as-is to avoid showing wrong R-multiple.
                    }
                });
            });
        })
        .catch(function () { /* silent — keep stale values */ });
}

// First poll a moment after load (let the page paint), then every 5s.
window.addEventListener('DOMContentLoaded', function () {
    setTimeout(_swingPollLivePrices, 800);
    setInterval(_swingPollLivePrices, 5000);
});

// ── Single-stock analyse (S38 search box) ──────────────────────
//
// Reads the symbol + AI checkbox + capital input, POSTs to
// /api/swing/analyse_one, renders the result card below the
// search box. Result card carries Done / Skip buttons that re-use
// the existing /api/swing/actions/<id>/{confirm,skip} endpoints
// (so the user's input flow is identical to a recommendation
// from the full scan — same prompts, same persistence).
function _renderSingleResult(host, data) {
    var c = data.candidate || {};
    var status = c.status || 'UNKNOWN';
    var actionId = data.action_id;
    var ai = data.ai_overlay || null;
    var rejected = (status !== 'ACCEPTED');
    var border = rejected ? '#c62828' : '#1b8e3a';

    var html = '';
    html += '<div style="border-left:4px solid ' + border + ';' +
            'padding:10px 12px;background:#fafbfc;border-radius:4px;' +
            'margin-top:6px">';
    html += '<div style="display:flex;justify-content:space-between;' +
            'align-items:center;margin-bottom:6px">';
    html += '<strong style="font-size:15px">' + (c.symbol || '?') +
            '</strong>';
    html += '<span style="font-size:12px;color:' + border +
            ';font-weight:600">' + status + '</span>';
    html += '</div>';

    if (rejected) {
        // S47: enrich the rejected card with the same 52w-high
        // context shown for ACCEPTED candidates plus a clear
        // "what would qualify" hint, so the user understands the
        // search ran successfully and the stock just doesn't
        // qualify TODAY (vs reading the bare "REJECTED" pill as
        // "the tool won't let me search this name"). Origin:
        // 2026-05-14 user reported "Analyze a single stock doesn't
        // allow me to search for ICICIBANK why?" — ICICIBANK was
        // at -16.7% from 52w high, just shy of the 18% dip-buy
        // threshold; rendering the dip% and the threshold makes
        // the near-miss obvious.
        var rejReason = c.rejected_reason || 'Rejected for unknown reason';
        var fmtRs = function (n) {
            return 'Rs.' + Number(n || 0).toLocaleString('en-IN', {
                minimumFractionDigits: 2, maximumFractionDigits: 2,
            });
        };
        html += '<p style="margin:4px 0;font-size:13px">' +
                rejReason + '</p>';
        // Context block — show the snapshot of where the stock
        // actually sits even though it was rejected.
        if (c.close_price && c.ath_price) {
            html += '<table class="kvtable" style="margin-top:8px;' +
                    'font-size:12.5px"><tbody>';
            html += '<tr><td>Current price</td><td>' + fmtRs(c.close_price) +
                    '</td></tr>';
            html += '<tr><td>52-week high (rolling)</td><td>' +
                    fmtRs(c.ath_price) + '</td></tr>';
            html += '<tr><td>% below 52w high</td><td>' +
                    Number(c.dip_from_ath_pct || 0).toFixed(2) +
                    '%</td></tr>';
            if (c.rsi_daily) {
                html += '<tr><td>RSI(14)</td><td>' +
                        Number(c.rsi_daily).toFixed(1) + '</td></tr>';
            }
            if (c.relative_strength !== undefined && c.relative_strength !== null) {
                var rsSign = c.relative_strength >= 0 ? '+' : '';
                html += '<tr><td>RS vs NIFTY (60d)</td><td>' + rsSign +
                        Number(c.relative_strength).toFixed(2) + '%</td></tr>';
            }
            html += '</tbody></table>';
        }
        // Always offer the detail-page link so the user can drill
        // in to see the full health-check + AI analyse button even
        // when the stock didn't qualify for entry today.
        html += '<div style="margin-top:8px">';
        html += '<a href="/swing/' + encodeURIComponent(c.symbol) +
                '" style="padding:5px 10px;font-size:12px;' +
                'border:1px solid #cfd9eb;border-radius:5px;' +
                'text-decoration:none;display:inline-block">' +
                'Open detail page</a>';
        html += '</div>';
    } else {
        html += '<table class="kvtable" style="margin-top:4px">';
        var rr = (c.rr_ratio || 0).toFixed(2);
        var dip = (c.dip_from_ath_pct || 0).toFixed(1);
        var fmt = function (n, d) {
            return 'Rs.' + Number(n || 0).toLocaleString('en-IN',
                { minimumFractionDigits: d, maximumFractionDigits: d });
        };
        html += '<tr><td>Setup</td><td>' + (c.setup_type || '—') +
                ' (score ' + (c.score || 0).toFixed(2) + ')</td></tr>';
        html += '<tr><td>Sector</td><td>' + (c.sector || '—') + '</td></tr>';
        html += '<tr><td>Current</td><td>' + fmt(c.close_price, 2) + '</td></tr>';
        html += '<tr><td>Suggested entry</td><td>' + fmt(c.entry_price, 2) +
                '</td></tr>';
        html += '<tr><td>Stop</td><td>' + fmt(c.stop_price, 2) + '</td></tr>';
        html += '<tr><td>Target</td><td>' + fmt(c.target_price, 2) + '</td></tr>';
        html += '<tr><td>Suggested qty</td><td>' + (c.suggested_qty || 0) +
                '</td></tr>';
        html += '<tr><td>R:R</td><td>' + rr + 'x</td></tr>';
        html += '<tr><td>% Below 52w high</td><td>' + dip + '% (Rs.' +
                Number(c.ath_price || 0).toLocaleString('en-IN',
                    { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ')</td></tr>';
        html += '<tr><td>RSI</td><td>' + (c.rsi_daily || 0).toFixed(1) + '</td></tr>';
        html += '<tr><td>RS vs NIFTY</td><td>' + (c.relative_strength >= 0 ? '+' : '') +
                (c.relative_strength || 0).toFixed(1) + '%</td></tr>';
        html += '<tr><td>Volume vs avg</td><td>' + (c.volume_ratio || 0).toFixed(1) +
                'x</td></tr>';
        html += '</table>';

        var reasons = c.reasons || [];
        if (reasons.length) {
            html += '<p style="margin:8px 0 4px;font-size:12px;font-weight:600">' +
                    'Why this score:</p>';
            html += '<ul style="margin:0 0 8px 20px;font-size:12px;line-height:1.6">';
            reasons.forEach(function (r) {
                html += '<li>' + r + '</li>';
            });
            html += '</ul>';
        }

        if (actionId) {
            html += '<div style="margin-top:8px;display:flex;gap:8px;align-items:center">';
            html += '<select class="add-dropdown" ' +
                    'onchange="addAction(this, ' + actionId + ', '' + c.symbol + '')" ' +
                    'style="padding:4px 6px;font-size:12px;font-weight:600;' +
                    'border:1px solid var(--accent);border-radius:5px;' +
                    'background:var(--card);cursor:pointer">' +
                    '<option value="">Add+</option>' +
                    '<option value="watch">Watch</option>' +
                    '<option value="buy">I Bought It</option>' +
                    '</select>';
            html += '<a href="/swing/' + encodeURIComponent(c.symbol) +
                    '" style="padding:5px 10px;font-size:12px;' +
                    'border:1px solid #cfd9eb;border-radius:5px;text-decoration:none">' +
                    'Open detail page</a>';
            html += '</div>';
        }
    }

    if (ai) {
        html += '<div style="margin-top:12px;padding-top:8px;' +
                'border-top:1px solid #e5e7eb">';
        html += '<strong style="font-size:13px">AI Analysis</strong>';
        if (ai.error) {
            html += '<div class="banner warn" style="margin-top:6px">AI error: ' +
                    ai.error + '</div>';
        } else if (ai.raw_response) {
            html += '<div id="single-ai-md" style="font-size:12.5px;' +
                    'line-height:1.7;margin-top:6px"></div>';
        }
        html += '</div>';
    }

    html += '</div>';
    host.innerHTML = html;

    // Render markdown structures (** bold, --- HR, ## headings,
    // - bullets) via _aiMdToHtml so a long AI response surfaces
    // formatted instead of as a wall of pre-wrap source text.
    // Pre-S43 the dashboard used textContent which printed the
    // raw markdown. _aiMdToHtml escapes input first so Claude
    // can't inject HTML.
    if (ai && ai.raw_response) {
        var host2 = host.querySelector('#single-ai-md');
        if (host2) host2.innerHTML = _aiMdToHtml(ai.raw_response);
    }
}

// Note: `_aiMdToHtml` lives in `_ai_md_js()` injected by `_wrap()`
// so both the home page (this _js block) and the per-stock detail
// page can call it.

function analyseOne() {
    var symEl = document.getElementById('single-symbol');
    var aiEl = document.getElementById('single-ai-toggle');
    var capEl = document.getElementById('swing-capital');
    var host = document.getElementById('single-result-host');
    if (!symEl || !host) return;
    var sym = (symEl.value || '').trim().toUpperCase();
    if (!sym) {
        host.innerHTML = '<div class="banner warn">' +
            'Type a ticker (e.g. SBIN) first.</div>';
        return;
    }
    var ai = aiEl && aiEl.checked ? '1' : '0';
    var capital = capEl ? parseFloat((capEl.value || '0').replace(/,/g, '')) : 0;
    if (ai === '1') {
        var perCall = window._swingAiPerCall || 3;
        if (!confirm('Spend ~Rs.' + perCall.toFixed(0) +
                     ' on a Claude AI overlay for ' + sym + '?')) {
            return;
        }
    }
    host.innerHTML = '<p class="muted"><span class="spinner"></span> ' +
        'Fetching candles + computing indicators for ' + sym +
        (ai === '1' ? ' (with AI overlay)' : '') + '...</p>';
    fetch('/api/swing/analyse_one?symbol=' + encodeURIComponent(sym) +
          '&ai=' + ai + '&capital=' + (capital || 0),
          {method: 'POST'})
        .then(function (r) { return r.json().then(function (j) {
            return {ok: r.ok, body: j};
        }); })
        .then(function (res) {
            if (!res.ok || !res.body.ok) {
                host.innerHTML = '<div class="banner warn">' +
                    'Analyse failed: ' + (res.body.error || 'unknown') + '</div>';
                return;
            }
            _renderSingleResult(host, res.body);
        })
        .catch(function (e) {
            host.innerHTML = '<div class="banner warn">Network error: ' +
                e + '</div>';
        });
}

// ── Compare up to 4 stocks (S45 search box) ────────────────────
//
// Two seed paths:
//  1. Free-text comma-separated tickers in #compare-symbols.
//  2. Sector dropdown (#compare-sector) — when changed, the input
//     auto-fills with the top 4 in that sector via /api/swing/compare.
// "Compare" button posts to /api/swing/compare and renders the
// metrics-x-stocks matrix below with winner cells highlighted in
// green and a "X of N metrics" tally per stock.
window.addEventListener('DOMContentLoaded', function () {
    var sel = document.getElementById('compare-sector');
    if (!sel) return;
    fetch('/api/swing/sectors')
        .then(function (r) { return r.json(); })
        .then(function (j) {
            var sectors = (j && j.sectors) || [];
            sectors.forEach(function (s) {
                var opt = document.createElement('option');
                opt.value = s; opt.textContent = s;
                sel.appendChild(opt);
            });
        })
        .catch(function () { /* silent — dropdown stays minimal */ });
    sel.addEventListener('change', function () {
        var sector = sel.value;
        if (!sector) return;
        // Pre-fetch the symbols list so the input box mirrors what
        // the Compare click will fetch.
        fetch('/api/swing/compare?sector=' + encodeURIComponent(sector))
            .then(function (r) { return r.json(); })
            .then(function (j) {
                if (j && j.symbols) {
                    var inp = document.getElementById('compare-symbols');
                    if (inp) inp.value = j.symbols.join(', ');
                    // Render the result that came back.
                    _renderCompareResult(
                        document.getElementById('compare-result-host'), j);
                }
            })
            .catch(function () { /* silent */ });
    });
});

function compareNow() {
    var inp = document.getElementById('compare-symbols');
    var sel = document.getElementById('compare-sector');
    var host = document.getElementById('compare-result-host');
    if (!host) return;
    var syms = (inp && inp.value || '').trim();
    var sector = (sel && sel.value || '').trim();
    if (!syms && !sector) {
        host.innerHTML = '<div class="banner warn">' +
            'Type tickers OR pick a sector first.</div>';
        return;
    }
    var url = syms
        ? '/api/swing/compare?symbols=' + encodeURIComponent(syms)
        : '/api/swing/compare?sector=' + encodeURIComponent(sector);
    host.innerHTML = '<p class="muted"><span class="spinner"></span> ' +
        'Fetching candles + computing comparison ' +
        '(this can take 5-15 seconds for 4 names)...</p>';
    fetch(url)
        .then(function (r) { return r.json().then(function (j) {
            return {ok: r.ok, body: j}; }); })
        .then(function (res) {
            if (!res.ok || !res.body.ok) {
                host.innerHTML = '<div class="banner warn">' +
                    'Compare failed: ' +
                    (res.body && res.body.error || 'unknown') + '</div>';
                return;
            }
            _renderCompareResult(host, res.body);
        })
        .catch(function (e) {
            host.innerHTML = '<div class="banner warn">Network error: ' +
                e + '</div>';
        });
}

function compareClear() {
    var inp = document.getElementById('compare-symbols');
    var sel = document.getElementById('compare-sector');
    var host = document.getElementById('compare-result-host');
    if (inp) inp.value = '';
    if (sel) sel.value = '';
    if (host) host.innerHTML = '';
}

function _renderCompareResult(host, data) {
    if (!host) return;
    var syms = data.symbols || [];
    if (!syms.length) {
        host.innerHTML = '<div class="banner warn">No data.</div>';
        return;
    }
    var winnerCounts = data.win_counts || [];
    var headOverall = data.winner_overall;
    var html = '';
    // Headline tally.
    if (headOverall) {
        html += '<div style="margin:6px 0 10px 0;font-size:13px">';
        html += '<strong>' + esc(headOverall) + '</strong> wins ' +
                'most metrics. Tally: ';
        var bits = [];
        for (var i = 0; i < syms.length; i++) {
            bits.push('<span style="font-weight:' +
                      (syms[i] === headOverall ? '600' : '400') + '">' +
                      esc(syms[i]) + ' ' + winnerCounts[i] + '</span>');
        }
        html += bits.join(' &middot; ');
        html += '</div>';
    }
    if (data.sector) {
        html += '<div class="muted" style="font-size:11px;margin-bottom:6px">' +
                'Sector: <strong>' + esc(data.sector) + '</strong> &middot; ' +
                'top ' + syms.length + ' by SECTOR_MAP order.</div>';
    }
    // Table.
    html += '<div style="overflow-x:auto"><table class="holdings" ' +
            'style="font-size:12.5px"><thead><tr>';
    html += '<th style="text-align:left;min-width:180px">Metric</th>';
    syms.forEach(function (s) {
        html += '<th style="text-align:center;min-width:120px">' +
                '<a href="/swing/' + encodeURIComponent(s) + '" ' +
                'style="color:var(--fg);font-weight:600">' +
                esc(s) + '</a></th>';
    });
    html += '</tr></thead><tbody>';
    (data.rows || []).forEach(function (row) {
        html += '<tr>';
        var lbl = esc(row.label);
        if (row.explain) {
            lbl = '<span title="' + esc(row.explain) + '" ' +
                  'style="border-bottom:1px dotted #cfd9eb;cursor:help">' +
                  lbl + '</span>';
        }
        html += '<td style="text-align:left">' + lbl + '</td>';
        (row.values || []).forEach(function (v, i) {
            // Multi-winner support (S53): bool rows highlight ALL
            // True cells, not just the first. Falls back to the
            // legacy single `winner_idx` if `winners_idx` missing.
            var wins = row.winners_idx;
            var winning = false;
            if (Array.isArray(wins) && wins.length) {
                winning = wins.indexOf(i) !== -1;
            } else {
                winning = (row.winner_idx === i);
            }
            var bg = winning ? 'background:#e6f4ea;font-weight:600' : '';
            html += '<td style="text-align:center;' + bg + '">' +
                    esc(v) + '</td>';
        });
        html += '</tr>';
    });
    html += '</tbody></table></div>';
    if (data.notes && data.notes.length) {
        html += '<div class="muted" style="font-size:11px;margin-top:8px">' +
                data.notes.map(esc).join('<br>') + '</div>';
    }
    host.innerHTML = html;

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
            return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];
        });
    }
}

// ── What changed since last trading day (S52) ──────────────────
//
// Loads /api/swing/changes_since once on page-load and renders a
// 3-section diff card (new entries / dropped / rank movers) into
// #changes-since-host. Re-fetches itself when the in-page scan
// completes (hooked from the existing scan-status poller below)
// so a fresh scan immediately refreshes the diff.
window._loadChangesSince = function () {
    var host = document.getElementById('changes-since-host');
    if (!host) return;
    fetch('/api/swing/changes_since')
        .then(function (r) { return r.json(); })
        .then(function (j) { _renderChangesSince(host, j || {}); })
        .catch(function () {
            host.innerHTML = '<span class="muted">Unable to load ' +
                             'change diff.</span>';
        });
};

function _renderChangesSince(host, d) {
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
            return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];
        });
    }
    if (!d || !d.current_run_id) {
        host.innerHTML = '<span class="muted">No scan history yet ' +
                         '— run a scan to start tracking changes.</span>';
        return;
    }
    if (!d.prior_run_id) {
        host.innerHTML = '<span class="muted">First scan in the DB ' +
                         '— no prior trading day to compare against.' +
                         '</span>';
        return;
    }
    var html = '';
    // Header line: "Comparing latest scan (...) vs <prior label>".
    var priorLabel = d.prior_run_age_label || ('scan from ' +
                     (d.prior_run_date || '?'));
    var curStamp = d.current_run_finished_at || d.current_run_date || '';
    html += '<div style="font-size:13px;margin-bottom:10px">';
    html += '<strong>Comparing latest scan</strong> ' +
            '<span class="muted">(' + esc(d.current_run_date) +
            (curStamp && curStamp !== d.current_run_date
                ? ' · ' + esc(curStamp.slice(11, 16))
                : '') + ')</span>';
    html += ' <strong>vs ' + esc(priorLabel) + '</strong>';
    if (d.skipped_runs && d.skipped_runs > 0) {
        html += ' <span class="muted">· ' + d.skipped_runs +
                ' intervening scan' + (d.skipped_runs === 1 ? '' : 's') +
                ' had no notable changes</span>';
    }
    html += '</div>';

    var nIn  = (d.new_entries || []).length;
    var nOut = (d.dropped || []).length;
    var nMov = (d.rank_movers || []).length;
    if (nIn === 0 && nOut === 0 && nMov === 0) {
        html += '<div class="muted">No changes from the previous scan.</div>';
        // Check if there's a "last meaningful change" further back
        if (d.last_meaningful_change) {
            var lmc = d.last_meaningful_change;
            html += '<div style="margin-top:12px;padding-top:12px;' +
                    'border-top:1px dashed var(--line)">';
            html += '<strong>Last meaningful change:</strong> ' +
                    '<span class="muted">vs scan from ' +
                    esc(lmc.prior_run_date || '?') +
                    ' (' + lmc.skipped_runs + ' scan' +
                    (lmc.skipped_runs === 1 ? '' : 's') +
                    ' between)</span><br>';
            html += '<span style="font-size:13px">' +
                    esc(lmc.summary || 'changes found') + '</span>';
            html += '</div>';
        }
        host.innerHTML = html;
        return;
    }

    // Headline tally chip-row.
    html += '<div style="margin-bottom:12px;font-size:13px">';
    if (nIn) html += '<span style="background:#e6f4ea;color:#1b5e20;' +
                    'padding:3px 8px;border-radius:4px;margin-right:6px;' +
                    'font-weight:600">+' + nIn + ' new</span>';
    if (nOut) html += '<span style="background:#fde8e8;color:#7a1f1f;' +
                     'padding:3px 8px;border-radius:4px;margin-right:6px;' +
                     'font-weight:600">−' + nOut + ' dropped</span>';
    if (nMov) html += '<span style="background:#fff4cc;color:#7a5500;' +
                     'padding:3px 8px;border-radius:4px;margin-right:6px;' +
                     'font-weight:600">⇅ ' + nMov + ' rank mover' +
                     (nMov === 1 ? '' : 's') + '</span>';
    html += '</div>';

    function _link(sym) {
        return '<a href="/swing/' + encodeURIComponent(sym) +
               '" style="font-weight:600;color:var(--fg)">' +
               esc(sym) + '</a>';
    }

    if (nIn) {
        html += '<div style="margin-bottom:10px"><strong>New entries</strong>' +
                ' <span class="muted">— in the latest scan but not in ' +
                esc(priorLabel) + ':</span><br>';
        html += '<div style="margin-top:6px;font-size:13px;line-height:1.8">';
        d.new_entries.forEach(function (e) {
            html += '• ' + _link(e.symbol) +
                    ' <span class="muted">(rank #' + e.rank +
                    ', score ' + (Number(e.score) || 0).toFixed(1) +
                    ', ' + esc(e.setup_type || '') + ')</span><br>';
        });
        html += '</div></div>';
    }

    if (nOut) {
        html += '<div style="margin-bottom:10px"><strong>Dropped</strong>' +
                ' <span class="muted">— were in ' + esc(priorLabel) +
                ' but not in the latest scan:</span><br>';
        html += '<div style="margin-top:6px;font-size:13px;line-height:1.8">';
        d.dropped.forEach(function (e) {
            var noteCls = e.now_status === 'REJECTED' ? '' : 'muted';
            var note = e.now_status === 'REJECTED'
                ? 'now REJECTED in latest'
                : (e.now_status === 'MISSING'
                    ? 'not present in latest'
                    : ('now ' + e.now_status));
            html += '• ' + _link(e.symbol) +
                    ' <span class="muted">(was rank #' + e.prior_rank +
                    ', score ' + (Number(e.prior_score) || 0).toFixed(1) +
                    ', ' + esc(e.prior_setup_type || '') + ')</span> ' +
                    '<span class="' + noteCls + '">— ' + esc(note) +
                    '</span><br>';
        });
        html += '</div></div>';
    }

    if (nMov) {
        html += '<div style="margin-bottom:6px"><strong>Rank movers</strong>' +
                ' <span class="muted">— in both scans, |Δrank| ≥ 3:' +
                '</span><br>';
        html += '<div style="margin-top:6px;font-size:13px;line-height:1.8">';
        d.rank_movers.forEach(function (e) {
            var dir = e.delta > 0 ? '↑' : '↓';
            var col = e.delta > 0 ? '#1b5e20' : '#7a1f1f';
            html += '• ' + _link(e.symbol) +
                    ' <span style="color:' + col + ';font-weight:600">' +
                    dir + Math.abs(e.delta) + '</span> ' +
                    '<span class="muted">(#' + e.prior_rank +
                    ' → #' + e.new_rank;
            if (e.score_delta && Math.abs(e.score_delta) >= 0.1) {
                html += ', Δscore ' +
                        (e.score_delta > 0 ? '+' : '') +
                        Number(e.score_delta).toFixed(1);
            }
            html += ')</span><br>';
        });
        html += '</div></div>';
    }

    host.innerHTML = html;
}

window.addEventListener('DOMContentLoaded', function () {
    if (window._loadChangesSince) window._loadChangesSince();
});

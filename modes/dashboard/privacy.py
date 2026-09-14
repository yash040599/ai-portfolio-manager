"""Shared, persistent screen-privacy controls for dashboard renderers."""

from __future__ import annotations


def privacy_css() -> str:
    """Remove private glyphs rather than blurring recoverable text."""
    return r"""
html[data-privacy="on"]:not([data-privacy-ready]) body { visibility: hidden; }
html[data-privacy="on"] .privacy-value,
html[data-privacy="on"] [data-private] {
  position: relative; visibility: hidden; user-select: none;
}
html[data-privacy="on"] .privacy-value::after,
html[data-privacy="on"] [data-private]::after {
  content: "\2022\2022\2022\2022"; visibility: visible;
  position: absolute; left: 0; top: 0; color: var(--muted, #64748b);
  letter-spacing: 0; white-space: nowrap;
}
html[data-privacy="on"] [data-private] *,
html[data-privacy="on"] [data-private] *::after { visibility: hidden; }
html[data-privacy="on"] [data-private-input] {
  color: transparent !important; text-shadow: none !important;
  caret-color: transparent; user-select: none; pointer-events: none;
}
html[data-privacy="on"] [data-private-input]::placeholder { color: transparent; }
html[data-privacy="on"] canvas:not([data-privacy-public]),
html[data-privacy="on"] [data-privacy-block] { visibility: hidden; pointer-events: none; }
html[data-privacy="on"] .privacy-chart { position: relative; }
html[data-privacy="on"] .privacy-chart::after {
  content: "Amounts hidden"; position: absolute; inset: 0;
  display: grid; place-items: center; color: var(--muted, #64748b);
  font-size: 13px; pointer-events: none;
}

/* Matches the nav's pill chrome (.nav-back / nav links), not a page action. */
nav.topnav button.privacy-toggle {
  font: inherit; font-size: 13px; font-weight: 600;
  display: inline-flex; align-items: center; gap: 7px;
  padding: 5px 12px; border-radius: var(--radius-pill);
  border: 1px solid var(--line-strong);
  background: var(--card); color: var(--fg);
  cursor: pointer; white-space: nowrap;
  transition: background .12s ease, color .12s ease, border-color .12s ease;
}
nav.topnav button.privacy-toggle::before {
  content: ""; width: 7px; height: 7px; border-radius: 50%;
  background: var(--muted); transition: background .12s ease;
}
nav.topnav button.privacy-toggle:hover { background: var(--soft); }
nav.topnav button.privacy-toggle[aria-pressed="true"] {
  background: var(--accent-soft); color: var(--accent);
  border-color: var(--accent-line);
}
nav.topnav button.privacy-toggle[aria-pressed="true"]::before { background: var(--accent); }
"""


def privacy_toggle_html() -> str:
    """Render the shared control; the HQ control uses the same controller."""
    return (
        '<button class="privacy-toggle" type="button" data-privacy-toggle '
        'onclick="toggleDashboardPrivacy()" aria-pressed="false" '
        'title="Hide personal amounts and quantities across every dashboard page">'
        'Hide amounts</button>'
    )


def privacy_boot_script() -> str:
    """Restore screen privacy before paint, including the former HQ setting."""
    return '<style id="dashboard-privacy-style">' + privacy_css() + '</style>' + r"""
<script>
(function () {
  if (window.dashboardPrivacy) return;
  class DashboardPrivacy {
    constructor() {
      this.key = 'dashPrivacy';
      this.attributes = new Map();
      this.inputs = new Map();
      this.moneySource = String.raw`[+\-\u2212]?\s*(?:\u20b9|\$|\bRs\.?|\bINR\b|\bUSD\b)\s*[+\-\u2212]?\s*\d[\d,]*(?:\.\d+)?(?:\s*(?:Cr\b|crores?\b|lakhs?\b|L\b|[KMB]\b))?`;
      this.identitySource = String.raw`\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|[A-Z]:[\\/]Users[\\/][^\\/\s<>]+|/home/[^/\s<>]+`;
      this.quantitySource = String.raw`\b\d[\d,]*(?:\.\d+)?\s*(?:shares?\b|units?\b|u\b)|\b(?:qty|quantity|folio|account)\s*[:=]\s*[A-Z0-9./-]+`;
      // Only these regions hold the owner's money and holdings. Research
      // notes, guides, thresholds and reference tables stay readable.
      this.scopes = '[data-personal], .kpis, .books, .realised, #geo-legend,'
        + ' table.holdings, table.kvtable, table.kv, .big-value, .day-detail';
      this.observer = new MutationObserver(() => this.refresh());
      this.hidden = this.readSaved();
      this.apply();
      document.addEventListener('DOMContentLoaded', () => this.apply());
      document.addEventListener('click', event => {
        if (this.hidden && event.target.closest('[data-privacy-block]')) {
          event.preventDefault();
          event.stopImmediatePropagation();
        }
      }, true);
      window.addEventListener('pageshow', () => {
        this.hidden = this.readSaved();
        this.apply();
      });
      window.addEventListener('storage', event => {
        if (event.key === this.key || event.key === 'homePrivacy' || event.key === null) {
          this.hidden = this.readSaved();
          this.apply();
        }
      });
    }

    readSaved() {
      try {
        const saved = window.localStorage.getItem(this.key);
        return (saved === null ? window.localStorage.getItem('homePrivacy') : saved) === '1';
      } catch (error) {
        return true;
      }
    }

    apply() {
      document.documentElement.dataset.privacy = this.hidden ? 'on' : 'off';
      this.refresh();
      window.dispatchEvent(new CustomEvent('dashprivacychange', {detail: this.hidden}));
    }

    personalRoots() {
      const roots = Array.from(document.querySelectorAll(this.scopes))
        .filter(element => !element.closest('[data-privacy-public]'));
      return roots.filter(root => !roots.some(other => other !== root && other.contains(root)));
    }

    prepareTables(roots) {
      const personal = /\b(qty|quantity|units|folio|account|invested|investment|value|amount|cost basis|budget|capital|sip|monthly|instalment|income|tax|charges|fees?|dividends?|brokerage|p&l|gain|profit|loss|margin|balance|realised|realized|unrealised|unrealized|turnover|cash|frees|savings|returns?|proceeds|drawdown|corpus|committed|premium)\b/i;
      const publicPrice = /^(?:avg(?:erage)?(?:\s+(?:nav|cost|buy price|price))?|nav|ltp|(?:live|current|watchlist|entry|buy|unit|share|stock)\s+price|price)$/i;
      const identity = /\b(qty|quantity|units|folio|account)\b/i;
      const classify = (label, cell) => {
        if (identity.test(label)) cell.setAttribute('data-private', '');
        else if (publicPrice.test(label)) cell.setAttribute('data-privacy-public', '');
        else if (personal.test(label)) cell.setAttribute('data-private-values', '');
      };
      const tables = new Set();
      roots.forEach(root => {
        if (root.matches('table')) tables.add(root);
        root.querySelectorAll('table').forEach(table => tables.add(table));
      });
      tables.forEach(table => {
        Array.from(table.rows).forEach(row => {
          if (row.cells.length < 2) return;
          const label = row.cells[0].textContent.trim();
          if (label.length > 80 || (!identity.test(label) && !personal.test(label) && !publicPrice.test(label))) return;
          Array.from(row.cells).slice(1).forEach(cell => {
            if (cell.tagName === 'TD' && cell.colSpan === 1) classify(label, cell);
          });
        });
        const headerRow = Array.from(table.rows).find(row => row.querySelector('th'));
        if (!headerRow) {
          Array.from(table.rows).forEach(row => {
            if (row.cells.length === 2) classify(row.cells[0].textContent.trim(), row.cells[1]);
          });
          return;
        }
        Array.from(headerRow.cells).forEach((header, column) => {
          const label = header.textContent.trim();
          if (!personal.test(label) && !publicPrice.test(label)) return;
          Array.from(table.rows).forEach(row => {
            const cell = row.cells[column];
            if (!cell || cell.tagName === 'TH' || cell.colSpan !== 1) return;
            classify(label, cell);
          });
        });
      });
    }

    maskText(roots) {
      const nodes = [];
      roots.forEach(root => {
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        while (walker.nextNode()) nodes.push(walker.currentNode);
      });
      nodes.forEach(node => {
        const parent = node.parentElement;
        if (!parent || parent.closest('script, style, noscript, svg, option, textarea, .privacy-value, [data-private], [data-privacy-public], [data-privacy-toggle]')) return;
        const numeric = parent.closest('[data-private-values]') ? '|[+\\-\\u2212]?\\d[\\d,]*(?:\\.\\d+)?' : '';
        const pattern = new RegExp(this.moneySource + '|' + this.identitySource + '|' + this.quantitySource + numeric, 'gi');
        const text = node.textContent;
        const matches = Array.from(text.matchAll(pattern)).filter(match => !/^\s*%/.test(text.slice(match.index + match[0].length)));
        if (!matches.length) return;
        const fragment = document.createDocumentFragment();
        let offset = 0;
        matches.forEach(match => {
          fragment.append(document.createTextNode(text.slice(offset, match.index)));
          const masked = document.createElement('span');
          masked.className = 'privacy-value';
          masked.textContent = match[0];
          fragment.append(masked);
          offset = match.index + match[0].length;
        });
        fragment.append(document.createTextNode(text.slice(offset)));
        node.replaceWith(fragment);
      });
    }

    protectAttributes(roots) {
      if (!this.hidden) {
        this.attributes.forEach((saved, element) => {
          if (element.isConnected) saved.forEach((value, name) => element.setAttribute(name, value));
        });
        this.attributes.clear();
        return;
      }
      this.attributes.forEach((saved, element) => {
        if (!element.isConnected) this.attributes.delete(element);
      });
      const selector = '[title], [aria-label], [data-tooltip], [data-original-title]';
      const candidates = new Set(document.querySelectorAll('[data-private-input][title]'));
      roots.forEach(root => {
        if (root.matches(selector)) candidates.add(root);
        root.querySelectorAll(selector).forEach(element => candidates.add(element));
      });
      candidates.forEach(element => {
        if (element.closest('[data-privacy-public]')) return;
        ['title', 'aria-label', 'data-tooltip', 'data-original-title'].forEach(name => {
          const value = element.getAttribute(name);
          if (!value) return;
          const pattern = new RegExp(this.moneySource + '|' + this.identitySource + '|' + this.quantitySource, 'i');
          if (!pattern.test(value) && !element.closest('[data-private], [data-private-input]') && !/\b(qty|quantity|units|folio|account|salary|capital)\b.*\d/i.test(value)) return;
          if (!this.attributes.has(element)) this.attributes.set(element, new Map());
          this.attributes.get(element).set(name, value);
          element.removeAttribute(name);
        });
      });
    }

    protectInputs() {
      document.querySelectorAll('input, textarea, select').forEach(input => {
        if (input.closest('[data-privacy-public]') || /^(checkbox|radio|hidden|button|submit|date|month)$/.test(input.type)) return;
        const identity = [input.id, input.name, input.type].join(' ');
        const privateOption = input.tagName === 'SELECT' && new RegExp(this.moneySource + '|' + this.identitySource, 'i').test(input.textContent);
        if (input.tagName === 'TEXTAREA' || privateOption || /number|password|qty|quantity|units|capital|ticket|salary|income|amount|budget|folio|totp|token|secret|pin|user.?id|redirect_url/i.test(identity)) {
          input.setAttribute('data-private-input', '');
        }
        if (!input.hasAttribute('data-private-input')) return;
        if (this.hidden) {
          if (!this.inputs.has(input)) this.inputs.set(input, {
            disabled: input.disabled, ariaHidden: input.getAttribute('aria-hidden')
          });
          input.disabled = true;
          input.setAttribute('aria-hidden', 'true');
        } else if (this.inputs.has(input)) {
          const saved = this.inputs.get(input);
          input.disabled = saved.disabled;
          if (saved.ariaHidden === null) input.removeAttribute('aria-hidden');
          else input.setAttribute('aria-hidden', saved.ariaHidden);
          this.inputs.delete(input);
        }
      });
      this.inputs.forEach((saved, input) => {
        if (!input.isConnected) this.inputs.delete(input);
      });
    }

    refresh() {
      this.observer.disconnect();
      if (document.body) {
        const roots = this.personalRoots();
        this.prepareTables(roots);
        this.maskText(roots);
        this.protectInputs();
        this.protectAttributes(roots);
        document.querySelectorAll('canvas:not([data-privacy-public])').forEach(canvas => {
          canvas.parentElement.classList.add('privacy-chart');
        });
        document.querySelectorAll('a[download], .chat-build, .chat-copy, [onclick]').forEach(control => {
          const action = control.getAttribute('onclick') || '';
          if (control.matches('a[download], .chat-build, .chat-copy') || /\b(?:edit|exit|buy|sell|record|confirm|promote|copy|export|download)\w*\s*\(/i.test(action)) {
            control.setAttribute('data-privacy-block', '');
          }
        });
        document.documentElement.setAttribute('data-privacy-ready', '');
      }
      document.querySelectorAll('[data-privacy-toggle]').forEach(button => {
        button.textContent = this.hidden ? 'Show amounts' : 'Hide amounts';
        button.setAttribute('aria-pressed', String(this.hidden));
      });
      this.observer.observe(document.documentElement, {
        subtree: true, childList: true, characterData: true, attributes: true,
        attributeFilter: ['title', 'aria-label', 'data-tooltip', 'data-original-title', 'value']
      });
    }

    toggle() {
      this.hidden = !this.hidden;
      try {
        window.localStorage.setItem(this.key, this.hidden ? '1' : '0');
      } catch (error) {}
      this.apply();
    }
  }
  window.dashboardPrivacy = new DashboardPrivacy();
  window.toggleDashboardPrivacy = () => window.dashboardPrivacy.toggle();
})();
</script>
"""
# ================================================================
# modes/dashboard/ai_widget.py
# ================================================================
# Shared AI model selector banner + script for dashboard pages.
# Used on: swing, portfolio/analyse, US pages.
# ================================================================

from __future__ import annotations

import html

from config import Config
from modes.dashboard.icons import icon


def ai_banner_html() -> str:
    """Renders the AI model selector banner.

    Shows provider/plan dropdowns with per-option cost, active model
    label, free-tier info (Gemini only), and an Apply button.
    """
    ai_plan = Config.ai()
    provider = Config.AI_PROVIDER
    plan = Config.AI_PLAN
    model = ai_plan["model"]
    cost = ai_plan["cost_inr_approx"]
    free_tier = ai_plan.get("free_tier") or ""

    # Build provider options with cost hint per option
    provider_opts = ""
    for p in ["gemini", "gpt", "claude"]:
        # Get the cost for this provider at the current plan level
        table_attr = Config._AI_PROVIDER_TABLE.get(p)
        rules = getattr(Config, table_attr) if table_attr else {}
        p_plan = rules.get(plan, rules.get("pro", {}))
        p_cost = p_plan.get("cost_inr_approx", "?")
        p_free = p_plan.get("free_tier")
        label = p.upper()
        if p_free:
            label += f" — FREE ({p_free})"
        else:
            label += f" — {p_cost}"
        sel = " selected" if p == provider else ""
        provider_opts += (
            f'<option value="{p}"{sel}>{html.escape(label)}</option>'
        )

    # Build plan options with cost for current provider
    plan_opts = ""
    table_attr = Config._AI_PROVIDER_TABLE.get(provider)
    rules = getattr(Config, table_attr) if table_attr else {}
    for pl in ["basic", "detailed", "full"]:
        p_data = rules.get(pl, {})
        p_cost = p_data.get("cost_inr_approx", "?")
        label = f"{pl.upper()} — {p_cost}"
        sel = " selected" if pl == plan else ""
        plan_opts += f'<option value="{pl}"{sel}>{html.escape(label)}</option>'

    free_line = ""
    if free_tier:
        free_line = (
            f'<div id="ai-free" class="ai-free pos">'
            f'Free tier: {html.escape(free_tier)} · No credit card required</div>'
        )
    else:
        free_line = '<div id="ai-free" class="ai-free"></div>'

    # Collapsed by default: the summary shows the active model, the
    # switcher opens on demand.
    return (
        '<details class="ai-widget">'
        '<summary>'
        f'<span class="ask-icon">{icon("spark", size=20)}</span>'
        '<span class="ask-copy"><span class="ask-title">AI model</span>'
        f'<span class="ask-hint" id="ai-label">{html.escape(provider.upper())} / '
        f'{html.escape(model)} · {html.escape(cost)}</span></span>'
        f'<span class="ask-chev">{icon("chevron-down", size=20)}</span>'
        '</summary>'
        '<div class="ai-body">'
        '<div class="ai-row">'
        f'<select id="ai-provider" aria-label="AI provider">{provider_opts}</select>'
        f'<select id="ai-plan" aria-label="AI plan">{plan_opts}</select>'
        '<button id="ai-apply" class="action" type="button">Apply</button>'
        '</div>'
        f'{free_line}'
        '<div id="ai-msg" class="ai-msg"></div>'
        '</div>'
        '</details>'
    )


def ai_widget_css() -> str:
    """Styles for the collapsible model switcher (shares Ask AI's row look)."""
    return r"""
/* ── AI model switcher ─────────────────────────────────────── */
details.ai-widget { margin: 0 0 16px; background: var(--card);
                    border: 1px solid var(--line); border-radius: var(--radius); }
details.ai-widget > summary { display: flex; align-items: center; gap: 12px; padding: 10px 16px;
                              border-radius: var(--radius); cursor: pointer; list-style: none; }
details.ai-widget > summary::-webkit-details-marker { display: none; }
details.ai-widget > summary:hover { background: var(--card-2); }
details.ai-widget[open] .ask-chev { transform: rotate(180deg); }
.ai-body { display: grid; gap: 6px; padding: 4px 16px 14px; }
.ai-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.ai-row select { font-size: 13px; }
.ai-free, .ai-msg { font-size: 12.5px; color: var(--muted); white-space: pre-line; }
.ai-free.pos { color: var(--pos); }
.ai-free:empty, .ai-msg:empty { display: none; }
"""


def ai_banner_script() -> str:
    """JS for the AI model switcher. Updates plan dropdown costs on
    provider change, calls /api/ai/switch on Apply."""
    return """<script>
(function(){
  var btn = document.getElementById('ai-apply');
  if (!btn) return;

  // Refresh plan dropdown options when provider changes
  var provSel = document.getElementById('ai-provider');
  var planSel = document.getElementById('ai-plan');
  provSel.addEventListener('change', function(){
    fetch('/api/ai/status').then(function(r){return r.json()}).then(function(d){
      if (!d.all_options) return;
      var prov = provSel.value;
      var curPlan = planSel.value;
      planSel.innerHTML = '';
      d.all_options.forEach(function(opt){
        if (opt.provider !== prov) return;
        var o = document.createElement('option');
        o.value = opt.plan;
        var lbl = opt.plan.toUpperCase() + ' \\u2014 ' + opt.cost;
        o.textContent = lbl;
        if (opt.plan === curPlan) o.selected = true;
        planSel.appendChild(o);
      });
    });
  });

  btn.addEventListener('click', function(){
    var provider = provSel.value;
    var plan = planSel.value;
    var msg = document.getElementById('ai-msg');
    msg.textContent = 'Switching...';
    fetch('/api/ai/switch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({provider: provider, plan: plan})
    }).then(function(r){ return r.json(); })
    .then(function(d){
      if (d.ok) {
        document.getElementById('ai-label').textContent =
          d.provider.toUpperCase() + ' / ' + d.model + ' \\u00b7 ' + d.cost;
        var ft = document.getElementById('ai-free');
        if (ft) {
          ft.textContent = d.free_tier ? ('Free tier: ' + d.free_tier + ' \\u00b7 No credit card required') : '';
          ft.style.color = d.free_tier ? 'var(--pos)' : '';
        }
        var txt = 'Switched to ' + d.provider.toUpperCase() +
          ' / ' + d.model + ' (' + d.plan.toUpperCase() + ')';
        if (d.cost_warning) {
          txt += '\\n' + d.cost_warning;
          msg.style.color = 'var(--warn-fg)';
        } else {
          msg.style.color = 'var(--pos)';
        }
        msg.textContent = txt;
      } else {
        msg.textContent = d.error || 'Switch failed';
        msg.style.color = 'var(--neg)';
      }
    }).catch(function(e){
      msg.textContent = 'Error: ' + e;
      msg.style.color = 'var(--neg)';
    });
  });
})();
</script>"""


def ai_toggle_label(element_id: str = "ai-toggle-input") -> str:
    """Returns the HTML for an AI on/off toggle checkbox.

    The label dynamically shows the active provider name and cost
    instead of hardcoding 'Claude'.
    """
    ai_plan = Config.ai()
    provider = Config.AI_PROVIDER.upper()
    model = ai_plan["model"]
    cost = ai_plan["cost_inr_approx"]
    free_tier = ai_plan.get("free_tier")

    hint_parts = ["NoAI is default; AI adds thesis + risks + news."]
    hint_parts.append(f"Cost: {cost}.")
    if free_tier:
        hint_parts.append(f"Free tier: {free_tier}.")

    return (
        f'<label class="ai-toggle" '
        f'title="Toggle to use {html.escape(provider)} AI overlay">'
        f'<input type="checkbox" id="{html.escape(element_id)}">'
        f'<span class="lbl">Use AI overlay '
        f'({html.escape(provider)} / {html.escape(model)})</span>'
        f'<span class="hint">({" ".join(hint_parts)})</span>'
        f'</label>'
    )

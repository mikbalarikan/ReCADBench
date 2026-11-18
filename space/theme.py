"""Brand theme and CSS for the ReCADBench Open Space.

Palette = the README badges: background ``#0D161F``, red ``#FF3347``, cyan ``#00E5FF``,
lime ``#9DFF2E``, orange ``#FF9A4D``, light text ``#ECF1F5``. The theme is dark first (the
``*_dark`` values use the brand background) and has a readable light variant; Gradio switches
between them with the ``dark`` class on ``<body>`` (system preference or ``?__theme=``).
The header hero is always dark so the banner art looks the same in both modes.
"""

from __future__ import annotations

import gradio as gr
from gradio.themes.utils import colors, fonts, sizes

BRAND = {
    "bg": "#0D161F",
    "red": "#FF3347",
    "cyan": "#00E5FF",
    "lime": "#9DFF2E",
    "orange": "#FF9A4D",
    "text": "#ECF1F5",
}

CYAN = colors.Color(
    name="rcb_cyan",
    c50="#E6FCFF",
    c100="#B3F6FF",
    c200="#80F0FF",
    c300="#4DEBFF",
    c400="#1AE7FF",
    c500="#00E5FF",
    c600="#00B8CC",
    c700="#008A99",
    c800="#005C66",
    c900="#002E33",
    c950="#001A1D",
)
RED = colors.Color(
    name="rcb_red",
    c50="#FFEBED",
    c100="#FFC2C8",
    c200="#FF99A3",
    c300="#FF707E",
    c400="#FF4D5F",
    c500="#FF3347",
    c600="#E0203A",
    c700="#B3192E",
    c800="#851322",
    c900="#570C16",
    c950="#2E060C",
)
SLATE = colors.Color(
    name="rcb_slate",
    c50="#ECF1F5",
    c100="#D5DEE6",
    c200="#B3C1CD",
    c300="#8D9DAC",
    c400="#6B7C8C",
    c500="#4E5E6E",
    c600="#364554",
    c700="#22313F",
    c800="#16232F",
    c900="#111C26",
    c950="#0D161F",
)


class RCBTheme(gr.themes.Base):
    """Dark-first brand theme derived from ``gr.themes.Base``."""

    def __init__(self) -> None:
        super().__init__(
            primary_hue=CYAN,
            secondary_hue=RED,
            neutral_hue=SLATE,
            radius_size=sizes.radius_md,
            font=(fonts.GoogleFont("Inter"), "ui-sans-serif", "system-ui", "sans-serif"),
            font_mono=(fonts.GoogleFont("JetBrains Mono"), "ui-monospace", "Consolas", "monospace"),
        )
        self.name = "recadbench"
        super().set(
            # surfaces
            body_background_fill="#F3F6F9",
            body_background_fill_dark=BRAND["bg"],
            body_text_color=BRAND["bg"],
            body_text_color_dark=BRAND["text"],
            body_text_color_subdued="#4E5E6E",
            body_text_color_subdued_dark="#8D9DAC",
            background_fill_primary="#FFFFFF",
            background_fill_primary_dark="#111C26",
            background_fill_secondary="#EEF3F7",
            background_fill_secondary_dark=BRAND["bg"],
            block_background_fill="#FFFFFF",
            block_background_fill_dark="#111C26",
            block_border_color="#D5DEE6",
            block_border_color_dark="#22313F",
            block_label_background_fill="#EEF3F7",
            block_label_background_fill_dark="#16232F",
            block_label_text_color="#4E5E6E",
            block_label_text_color_dark="#B3C1CD",
            block_title_text_color="#0D161F",
            block_title_text_color_dark=BRAND["text"],
            border_color_primary="#D5DEE6",
            border_color_primary_dark="#22313F",
            border_color_accent="#00B8CC",
            border_color_accent_dark=BRAND["cyan"],
            color_accent="#00B8CC",
            color_accent_soft="#E6FCFF",
            color_accent_soft_dark="#0E2A33",
            panel_background_fill="#FFFFFF",
            panel_background_fill_dark="#111C26",
            input_background_fill="#FFFFFF",
            input_background_fill_dark=BRAND["bg"],
            input_border_color="#D5DEE6",
            input_border_color_dark="#22313F",
            code_background_fill="#EEF3F7",
            code_background_fill_dark="#0A1219",
            table_border_color="#D5DEE6",
            table_border_color_dark="#22313F",
            table_even_background_fill="#FFFFFF",
            table_even_background_fill_dark="#111C26",
            table_odd_background_fill="#F6F9FB",
            table_odd_background_fill_dark="#0F1922",
            # links
            link_text_color="#007C8A",
            link_text_color_dark=BRAND["cyan"],
            link_text_color_hover="#005C66",
            link_text_color_hover_dark="#80F0FF",
            link_text_color_visited="#007C8A",
            link_text_color_visited_dark=BRAND["cyan"],
            # buttons: cyan primary with dark text, red "stop", quiet secondary
            button_primary_background_fill=BRAND["cyan"],
            button_primary_background_fill_dark=BRAND["cyan"],
            button_primary_background_fill_hover="#4DEBFF",
            button_primary_background_fill_hover_dark="#80F0FF",
            button_primary_text_color=BRAND["bg"],
            button_primary_text_color_dark=BRAND["bg"],
            button_primary_border_color="#00B8CC",
            button_primary_border_color_dark=BRAND["cyan"],
            button_secondary_background_fill="#FFFFFF",
            button_secondary_background_fill_dark="#16232F",
            button_secondary_background_fill_hover="#EEF3F7",
            button_secondary_background_fill_hover_dark="#22313F",
            button_secondary_text_color=BRAND["bg"],
            button_secondary_text_color_dark=BRAND["text"],
            button_secondary_border_color="#D5DEE6",
            button_secondary_border_color_dark="#2C3E4F",
            button_cancel_background_fill=BRAND["red"],
            button_cancel_background_fill_dark=BRAND["red"],
            button_cancel_background_fill_hover="#FF4D5F",
            button_cancel_background_fill_hover_dark="#FF4D5F",
            button_cancel_text_color="#FFFFFF",
            button_cancel_text_color_dark="#FFFFFF",
            button_large_text_weight="700",
            button_medium_text_weight="600",
            loader_color=BRAND["cyan"],
            slider_color=BRAND["cyan"],
            block_radius="14px",
            container_radius="14px",
            shadow_drop="none",
            shadow_drop_lg="0 8px 30px rgba(13,22,31,.08)",
        )


def build_theme() -> gr.themes.Base:
    return RCBTheme()


# Display font for headings and big numbers; theme fonts (Inter, JetBrains Mono) load via the theme.
HEAD = (
    '<meta name="description" content="ReCADBench Open: scan to parametric CAD benchmark for AI '
    'agents. Run your agent on 10 real 3D scans, upload STEP files, get an automatic score.">'
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Anton&display=swap">'
)

CSS = r"""
:root {
  --rcb-bg: #F3F6F9;
  --rcb-surface: #FFFFFF;
  --rcb-surface-2: #EEF3F7;
  --rcb-border: #D5DEE6;
  --rcb-text: #0D161F;
  --rcb-muted: #4E5E6E;
  --rcb-cyan: #007C8A;
  --rcb-cyan-soft: rgba(0, 184, 204, .12);
  --rcb-red: #D61F37;
  --rcb-red-soft: rgba(255, 51, 71, .12);
  --rcb-lime: #3F7A00;
  --rcb-lime-soft: rgba(110, 190, 20, .16);
  --rcb-orange: #B85410;
  --rcb-orange-soft: rgba(255, 154, 77, .18);
  --rcb-grid: rgba(13, 22, 31, .05);
  --rcb-display: "Anton", "Impact", "Arial Narrow Bold", sans-serif;
  --rcb-mono: "JetBrains Mono", ui-monospace, Consolas, monospace;
}
.dark {
  --rcb-bg: #0D161F;
  --rcb-surface: #111C26;
  --rcb-surface-2: #16232F;
  --rcb-border: #22313F;
  --rcb-text: #ECF1F5;
  --rcb-muted: #8D9DAC;
  --rcb-cyan: #00E5FF;
  --rcb-cyan-soft: rgba(0, 229, 255, .12);
  --rcb-red: #FF3347;
  --rcb-red-soft: rgba(255, 51, 71, .14);
  --rcb-lime: #9DFF2E;
  --rcb-lime-soft: rgba(157, 255, 46, .12);
  --rcb-orange: #FF9A4D;
  --rcb-orange-soft: rgba(255, 154, 77, .14);
  --rcb-grid: rgba(236, 241, 245, .045);
}

.gradio-container {
  width: 100% !important; max-width: 1240px !important; margin: 0 auto !important;
}
/* wide tables scroll inside their card instead of widening the page (phones) */
.rcb-board-wrap, .rcb-heat-wrap, .rcb-code pre { contain: inline-size; max-width: 100%; }
.rcb-board-wrap, .rcb-heat-wrap, .rcb-code pre { scrollbar-color: #2C3E4F transparent; }
.rcb a { color: var(--rcb-cyan); text-decoration: none; }
.rcb p a:hover, .rcb li a:hover { text-decoration: underline; }
.rcb { color: var(--rcb-text); line-height: 1.55; }
.rcb-host .html-container { padding: 0 !important; }
.rcb *, .rcb *::before, .rcb *::after { box-sizing: border-box; }
.rcb code, .rcb pre { font-family: var(--rcb-mono); }
.rcb code {
  font-size: .88em; background: var(--rcb-surface-2); border: 1px solid var(--rcb-border);
  padding: 1px 5px; border-radius: 6px;
}
.rcb ul { margin: 6px 0 10px 0; padding-left: 20px; list-style: disc; }
.rcb li { margin: 3px 0; }
.rcb b, .rcb strong { font-weight: 700; }
.rcb .muted { color: var(--rcb-muted); }
.rcb p { margin: 0 0 10px 0; }

/* ---------- hero (always dark) ---------- */
.rcb-hero {
  background: #0D161F; border: 1px solid #22313F; border-radius: 18px; overflow: hidden;
  color: #ECF1F5; position: relative;
}
.rcb-hero .rcb-banner { display: block; width: 100%; height: auto; aspect-ratio: 1600 / 560; }
/* covers the art's "Open track · soon" chip (x 291-520, y 439-484 of 1600x560) with "live" */
.rcb-banner-wrap { position: relative; container-type: inline-size; }
.rcb-banner-live {
  position: absolute; left: 18.06%; top: 78.21%; width: 14.5%; height: 8.57%;
  display: flex; align-items: center; justify-content: center; white-space: nowrap;
  border: max(1px, .13cqw) solid rgba(0, 229, 255, .82); border-radius: 999px;
  background: #0F161F; color: #ECF1F5; font: 700 1.18cqw/1 var(--rcb-mono);
  pointer-events: none; overflow: hidden;
}
.rcb-banner-live b { color: #9DFF2E; font-weight: 700; }
.rcb-hero-body {
  display: flex; flex-wrap: wrap; gap: 14px 24px; align-items: center;
  justify-content: space-between;
  padding: 16px 22px 18px 22px; border-top: 1px solid #22313F;
  background:
    linear-gradient(90deg, rgba(0,229,255,.06), rgba(255,51,71,.05)),
    #0D161F;
}
.rcb-tagline { font-size: 1.02rem; color: #ECF1F5; margin: 0; flex: 1 1 360px; }
.rcb-tagline b { color: #00E5FF; font-weight: 700; }
.rcb-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.rcb-chip {
  display: inline-flex; align-items: center; gap: 6px; padding: 5px 12px; border-radius: 999px;
  border: 1px solid #2C3E4F; background: rgba(236,241,245,.04); color: #ECF1F5 !important;
  font: 600 12.5px/1.2 var(--rcb-mono); letter-spacing: .01em; white-space: nowrap;
  transition: border-color .15s, background .15s, transform .15s;
}
.rcb-chip:hover { transform: translateY(-1px); background: rgba(236,241,245,.08); }
.rcb-chip .dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.rcb-chip.c-cyan .dot { background: #00E5FF; } .rcb-chip.c-cyan:hover { border-color: #00E5FF; }
.rcb-chip.c-red .dot { background: #FF3347; } .rcb-chip.c-red:hover { border-color: #FF3347; }
.rcb-chip.c-lime .dot { background: #9DFF2E; } .rcb-chip.c-lime:hover { border-color: #9DFF2E; }
.rcb-chip.c-orange .dot { background: #FF9A4D; } .rcb-chip.c-orange:hover { border-color: #FF9A4D; }
.rcb-chip.c-text .dot { background: #ECF1F5; } .rcb-chip.c-text:hover { border-color: #ECF1F5; }

/* ---------- stats ---------- */
.rcb-stats {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px;
}
.rcb-stat {
  background: var(--rcb-surface); border: 1px solid var(--rcb-border); border-radius: 14px;
  padding: 12px 16px; position: relative; overflow: hidden;
}
.rcb-stat::before {
  content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 4px;
  background: var(--accent);
}
.rcb-stat .v {
  font: 400 2rem/1.05 var(--rcb-display); letter-spacing: .02em; color: var(--rcb-text);
}
.rcb-stat .v small { font: 600 .9rem var(--rcb-mono); color: var(--rcb-muted); margin-left: 3px; }
.rcb-stat .l {
  font: 600 11.5px/1.3 var(--rcb-mono); text-transform: uppercase; letter-spacing: .08em;
  color: var(--rcb-muted); margin-top: 4px;
}
.rcb-stat .s {
  font-size: 12px; color: var(--rcb-muted); margin-top: 2px; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis;
}

/* ---------- tabs ---------- */
.rcb-tabs > .tab-wrapper, .rcb-tabs .tab-container { margin-top: 6px; }
.rcb-tabs button[role="tab"] { font-weight: 600; font-size: 15px; }
.rcb-tabs button[role="tab"][aria-selected="true"] { color: var(--rcb-text); }

/* ---------- section headings ---------- */
.rcb-h { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin: 6px 0 12px 0; }
.rcb-h h2 {
  font: 400 1.7rem/1.1 var(--rcb-display); letter-spacing: .02em; margin: 0; color: var(--rcb-text);
}
.rcb-h .sub { color: var(--rcb-muted); font-size: .95rem; }
.rcb-kicker {
  font: 600 11.5px var(--rcb-mono); text-transform: uppercase; letter-spacing: .12em;
  color: var(--rcb-cyan);
}

/* ---------- badges / pills ---------- */
.rcb-badge {
  display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 999px;
  font: 600 11px/1.5 var(--rcb-mono); letter-spacing: .02em; white-space: nowrap;
  border: 1px solid transparent; vertical-align: middle;
}
.rcb-badge.validated {
  color: var(--rcb-lime); background: var(--rcb-lime-soft);
  border-color: color-mix(in srgb, var(--rcb-lime) 40%, transparent);
}
.rcb-badge.unvalidated {
  color: var(--rcb-muted); background: var(--rcb-surface-2); border-color: var(--rcb-border);
}
.rcb-badge.reference {
  color: var(--rcb-cyan); background: var(--rcb-cyan-soft);
  border-color: color-mix(in srgb, var(--rcb-cyan) 40%, transparent);
}
.rcb-badge.hil {
  color: var(--rcb-orange); background: var(--rcb-orange-soft);
  border-color: color-mix(in srgb, var(--rcb-orange) 40%, transparent);
}
.rcb-badge.queued, .rcb-badge.scoring { color: var(--rcb-cyan); background: var(--rcb-cyan-soft); }
.rcb-badge.failed { color: var(--rcb-red); background: var(--rcb-red-soft); }
.rcb-badge.done { color: var(--rcb-lime); background: var(--rcb-lime-soft); }
.rcb-badge.scoring::before {
  content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor;
  animation: rcb-pulse 1.2s infinite;
}
@keyframes rcb-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .25; } }

.rcb-pill {
  display: inline-block; padding: 2px 9px; border-radius: 999px;
  font: 600 11.5px/1.6 var(--rcb-mono);
  background: var(--rcb-surface-2); color: var(--rcb-muted); border: 1px solid var(--rcb-border);
  white-space: nowrap;
}
.rcb-pill.d-medium {
  color: var(--rcb-lime); background: var(--rcb-lime-soft); border-color: transparent;
}
.rcb-pill.d-hard {
  color: var(--rcb-orange); background: var(--rcb-orange-soft); border-color: transparent;
}
.rcb-pill.d-very-hard {
  color: var(--rcb-red); background: var(--rcb-red-soft); border-color: transparent;
}

/* ---------- leaderboard table ---------- */
.rcb-board-wrap {
  overflow-x: auto; border: 1px solid var(--rcb-border); border-radius: 14px;
  background: var(--rcb-surface);
  -webkit-overflow-scrolling: touch;
}
table.rcb-board {
  width: 100%; border-collapse: collapse; font-size: 14px; min-width: 860px; margin: 0;
}
table.rcb-board th {
  text-align: left; font: 600 11px/1.3 var(--rcb-mono); text-transform: uppercase;
  letter-spacing: .07em; color: var(--rcb-muted); padding: 11px 9px;
  border: 0; border-bottom: 1px solid var(--rcb-border);
  background: var(--rcb-surface-2); white-space: nowrap;
}
table.rcb-board td {
  padding: 10px 9px; border: 0; border-bottom: 1px solid var(--rcb-border);
  vertical-align: middle; color: var(--rcb-text);
}
table.rcb-board td.subm { min-width: 210px; }
table.rcb-board th:first-child, table.rcb-board td:first-child { padding-left: 14px; }
table.rcb-board tr:last-child td { border-bottom: none; }
table.rcb-board tbody tr:hover td {
  background: color-mix(in srgb, var(--rcb-cyan) 5%, transparent);
}
table.rcb-board .num {
  text-align: right; font-family: var(--rcb-mono); font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
table.rcb-board th.num { text-align: right; }
table.rcb-board tr.unfinished td { opacity: .78; }
.rcb-rank {
  display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px;
  border-radius: 50%;
  font: 400 15px/1 var(--rcb-display); background: var(--rcb-surface-2); color: var(--rcb-muted);
  border: 1px solid var(--rcb-border);
}
.rcb-rank.r1 { background: #FFD54A; color: #0D161F; border-color: #E6B800; }
.rcb-rank.r2 { background: #D5DEE6; color: #0D161F; border-color: #B3C1CD; }
.rcb-rank.r3 { background: #F0A36B; color: #0D161F; border-color: #D9813F; }
.rcb-name { font-weight: 700; color: var(--rcb-text); }
.rcb-name a {
  color: var(--rcb-text); text-decoration: underline dotted var(--rcb-muted);
  text-underline-offset: 3px;
}
.rcb-name a:hover { color: var(--rcb-cyan); text-decoration-color: var(--rcb-cyan); }
.rcb-by { font-size: 12.5px; color: var(--rcb-muted); margin-top: 1px; }
.rcb-badges { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 5px; }
.rcb-model { font-weight: 600; }
.rcb-score { display: flex; align-items: center; gap: 10px; justify-content: flex-end; }
.rcb-score b {
  font: 400 1.35rem/1 var(--rcb-display); letter-spacing: .02em; min-width: 48px; text-align: right;
}
.rcb-bar {
  width: 64px; height: 6px; border-radius: 99px; background: var(--rcb-surface-2); overflow: hidden;
  border: 1px solid var(--rcb-border);
}
.rcb-bar > span {
  display: block; height: 100%; border-radius: 99px;
  background: linear-gradient(90deg, #00E5FF, #9DFF2E);
}
.rcb-report {
  display: inline-block; padding: 4px 10px; border-radius: 8px; border: 1px solid var(--rcb-border);
  font: 600 12px var(--rcb-mono); color: var(--rcb-text) !important; white-space: nowrap;
}
.rcb-report:hover { border-color: var(--rcb-cyan); color: var(--rcb-cyan) !important; }

/* ---------- empty state / notes ---------- */
.rcb-empty {
  text-align: center; padding: 42px 20px; border: 1px dashed var(--rcb-border); border-radius: 14px;
  background:
    linear-gradient(var(--rcb-grid) 1px, transparent 1px) 0 0 / 28px 28px,
    linear-gradient(90deg, var(--rcb-grid) 1px, transparent 1px) 0 0 / 28px 28px,
    var(--rcb-surface);
}
.rcb-empty .big {
  font: 400 1.6rem/1.2 var(--rcb-display); letter-spacing: .02em; color: var(--rcb-text);
}
.rcb-empty p { color: var(--rcb-muted); margin: 6px 0 0 0; }
.rcb-empty b { color: var(--rcb-cyan); }
.rcb-legend {
  display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; margin-top: 10px;
  font-size: 13px; color: var(--rcb-muted);
}
.rcb-note {
  border: 1px solid var(--rcb-border); border-left: 4px solid var(--accent, var(--rcb-cyan));
  border-radius: 12px;
  padding: 12px 16px; background: var(--rcb-surface); margin: 12px 0; color: var(--rcb-text);
}
.rcb-note p:last-child, .rcb-note ul:last-child { margin-bottom: 0; }

/* ---------- task cards ---------- */
.rcb-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px;
}
.rcb-card {
  display: flex; flex-direction: column; background: var(--rcb-surface);
  border: 1px solid var(--rcb-border);
  border-radius: 16px; overflow: hidden;
  transition: transform .15s, border-color .15s, box-shadow .15s;
}
.rcb-card:hover {
  transform: translateY(-2px);
  border-color: color-mix(in srgb, var(--rcb-cyan) 55%, var(--rcb-border));
  box-shadow: 0 10px 28px rgba(0,0,0,.12);
}
.rcb-card .media {
  position: relative; aspect-ratio: 1 / 1; display: flex; align-items: center;
  justify-content: center;
  background:
    linear-gradient(var(--rcb-grid) 1px, transparent 1px) 0 0 / 22px 22px,
    linear-gradient(90deg, var(--rcb-grid) 1px, transparent 1px) 0 0 / 22px 22px,
    radial-gradient(circle at 50% 45%, var(--rcb-surface-2), var(--rcb-surface) 72%);
  border-bottom: 1px solid var(--rcb-border);
}
.rcb-card .media img {
  width: 84%; height: 84%; object-fit: contain; filter: drop-shadow(0 8px 14px rgba(0,0,0,.25));
}
.rcb-card .media .ph { font: 400 3rem var(--rcb-display); color: var(--rcb-border); }
.rcb-card .no {
  position: absolute; top: 10px; left: 10px; font: 400 1.05rem/1 var(--rcb-display);
  letter-spacing: .03em;
  padding: 5px 8px; border-radius: 8px; background: #0D161F; color: #ECF1F5;
  border: 1px solid #22313F;
}
.rcb-card .media .rcb-pill { position: absolute; top: 10px; right: 10px; }
.rcb-card .body {
  padding: 12px 14px 14px 14px; display: flex; flex-direction: column; gap: 8px; flex: 1;
}
.rcb-card .title {
  font-weight: 800; font-size: 1.02rem; line-height: 1.25; color: var(--rcb-text);
}
.rcb-card .tid {
  font: 500 11.5px/1.3 var(--rcb-mono); color: var(--rcb-muted); word-break: break-all;
}
.rcb-card .fam {
  font-size: 12.5px; color: var(--rcb-muted); display: flex; align-items: center; gap: 6px;
}
.rcb-card .fam::before {
  content: ""; width: 8px; height: 8px; border-radius: 2px; background: var(--fam, var(--rcb-cyan));
  flex: none;
}
.rcb-card .facts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; }
.rcb-card .fact {
  background: var(--rcb-surface-2); border-radius: 8px; padding: 5px 7px; min-width: 0;
}
.rcb-card .fact .k {
  font: 600 9.5px/1.2 var(--rcb-mono); text-transform: uppercase; letter-spacing: .08em;
  color: var(--rcb-muted);
}
.rcb-card .fact .v {
  font: 700 13px/1.35 var(--rcb-mono); color: var(--rcb-text); white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis;
}
.rcb-card details { font-size: 13px; color: var(--rcb-text); }
.rcb-card details summary {
  cursor: pointer; color: var(--rcb-muted); font-weight: 600; font-size: 12.5px; list-style: none;
}
.rcb-card details summary::-webkit-details-marker { display: none; }
.rcb-card details summary::before {
  content: "+ "; font-family: var(--rcb-mono); color: var(--rcb-cyan);
}
.rcb-card details[open] summary::before { content: "- "; }
.rcb-card details ul { margin: 6px 0 0 0; padding-left: 18px; }
.rcb-card details li { margin: 2px 0; }
.rcb-card .dl { display: flex; gap: 6px; margin-top: auto; padding-top: 4px; }
.rcb-card .dl a {
  flex: 1; text-align: center; padding: 6px 8px; border-radius: 9px;
  border: 1px solid var(--rcb-border);
  font: 600 12px var(--rcb-mono); color: var(--rcb-text) !important; background: var(--rcb-surface);
}
.rcb-card .dl a:hover { border-color: var(--rcb-cyan); color: var(--rcb-cyan) !important; }
.rcb-card .dl a.primary {
  background: var(--rcb-cyan-soft);
  border-color: color-mix(in srgb, var(--rcb-cyan) 40%, transparent);
}

/* ---------- steps (take part) ---------- */
.rcb-steps { display: flex; flex-direction: column; gap: 14px; }
.rcb-step {
  display: grid; grid-template-columns: 58px minmax(0, 1fr); gap: 14px;
  background: var(--rcb-surface);
  border: 1px solid var(--rcb-border); border-radius: 16px; padding: 16px 18px; position: relative;
  overflow: hidden;
}
.rcb-step::before {
  content: ""; position: absolute; left: 0; right: 0; top: 0; height: 3px;
  background: var(--accent);
}
.rcb-step .n { font: 400 2.3rem/1 var(--rcb-display); color: var(--accent); letter-spacing: .02em; }
.rcb-step h3 { margin: 2px 0 6px 0; font-size: 1.12rem; font-weight: 800; color: var(--rcb-text); }
.rcb-step p, .rcb-step li { color: var(--rcb-text); font-size: 14.5px; }
.rcb-step ul { margin: 4px 0 8px 0; padding-left: 18px; }
.rcb-code { margin: 8px 0 10px 0; }
.rcb-code .box { position: relative; }
.rcb-code pre {
  margin: 0; padding: 12px 14px; padding-right: 74px; border-radius: 10px; overflow-x: auto;
  background: #0A1219; color: #ECF1F5; border: 1px solid #22313F; font-size: 13px;
  line-height: 1.55;
}
.rcb-code pre code {
  background: none; color: inherit; padding: 0; font-size: inherit; white-space: pre;
  border: 0; border-radius: 0;
}
.rcb-code .copy {
  position: absolute; top: 8px; right: 8px; padding: 3px 9px; border-radius: 7px; cursor: pointer;
  font: 600 11px var(--rcb-mono); color: #ECF1F5; background: #16232F; border: 1px solid #2C3E4F;
}
.rcb-code .copy:hover { border-color: #00E5FF; color: #00E5FF; }
.rcb-code .label {
  font: 600 11px/1.3 var(--rcb-mono); text-transform: uppercase; letter-spacing: .08em;
  color: var(--rcb-muted); margin-bottom: 4px;
}
.rcb-two { display: grid; grid-template-columns: minmax(0, 6fr) minmax(0, 5fr); gap: 12px; }
.rcb-upload-card {
  border: 1px solid var(--rcb-border) !important; border-radius: 16px !important;
  padding: 14px !important;
  background: var(--rcb-surface) !important; position: relative;
}
.rcb-rules {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 4px;
}
.rcb-rules .rcb-note { margin: 0; }
.rcb-rules h4 { margin: 0 0 6px 0; font-size: .98rem; font-weight: 800; }
.rcb-rules ul { margin: 0; padding-left: 18px; font-size: 14px; }
.rcb-rules li { margin: 3px 0; }

.rcb-step4 {
  border: 1px solid var(--rcb-border) !important; border-radius: 16px !important;
  padding: 16px 18px !important;
  background: var(--rcb-surface) !important; position: relative; overflow: hidden; margin-top: 14px;
  gap: 12px !important;
}
.rcb-step4::before {
  content: ""; position: absolute; left: 0; right: 0; top: 0; height: 3px; background: #FF3347;
}
.rcb-step4-head { display: grid; grid-template-columns: 58px minmax(0, 1fr); gap: 14px; }
.rcb-step4-head .n {
  font: 400 2.3rem/1 var(--rcb-display); color: var(--rcb-red); letter-spacing: .02em;
}
.rcb-step4-head h3 {
  margin: 2px 0 6px 0; font-size: 1.12rem; font-weight: 800; color: var(--rcb-text);
}
.rcb-heat-host p {
  text-align: center; padding: 36px 20px; border: 1px dashed var(--rcb-border); border-radius: 14px;
  color: var(--rcb-muted); background: var(--rcb-surface); margin: 0;
}

/* ---------- scoring ---------- */
.rcb-points { width: 100%; border-collapse: separate; border-spacing: 0 8px; margin: 4px 0; }
.rcb-points td {
  padding: 10px 12px; background: var(--rcb-surface); border-top: 1px solid var(--rcb-border);
  border-bottom: 1px solid var(--rcb-border); vertical-align: middle; color: var(--rcb-text);
}
.rcb-points td:first-child {
  border-left: 4px solid var(--accent); border-radius: 12px 0 0 12px; width: 190px;
}
.rcb-points td:last-child {
  border-right: 1px solid var(--rcb-border); border-radius: 0 12px 12px 0;
}
.rcb-points .pts {
  font: 400 1.7rem/1 var(--rcb-display); color: var(--accent); width: 70px; text-align: right;
}
.rcb-points .pts small { font: 600 11px var(--rcb-mono); color: var(--rcb-muted); }
.rcb-points .what { font-size: 14px; }
.rcb-points .nm { font-weight: 800; }
.rcb-points .nm span {
  display: block; font: 600 11px var(--rcb-mono); color: var(--rcb-muted);
  text-transform: uppercase; letter-spacing: .06em;
}
.rcb-pbar {
  height: 6px; border-radius: 9px; background: var(--rcb-surface-2); margin-top: 6px;
  overflow: hidden;
}
.rcb-pbar > span { display: block; height: 100%; background: var(--accent); border-radius: 9px; }
.rcb-flow {
  margin: 14px 0 4px 0; border-radius: 16px; overflow: hidden; border: 1px solid var(--rcb-border);
}
/* flow-d / flow-l, never "dark" / "light": Gradio turns any selector containing ".dark" into a
   dark-mode-only rule, which left both images visible in light mode */
.rcb-flow img { display: block; width: 100%; height: auto; }
.rcb-flow { background: #F3F6F9; }
.rcb-flow img.flow-d { display: none; }
.rcb-flow.only-d { background: #0D161F; }
.rcb-flow.only-d img.flow-d { display: block; }
.dark .rcb-flow { background: #0D161F; }
.dark .rcb-flow img.flow-d { display: block; }
.dark .rcb-flow img.flow-l { display: none; }
.rcb-tracks { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.rcb-track {
  border: 1px solid var(--rcb-border); border-radius: 16px; padding: 16px;
  background: var(--rcb-surface);
}
.rcb-track h3 {
  margin: 0 0 4px 0; font: 400 1.35rem var(--rcb-display); letter-spacing: .02em;
  color: var(--accent);
}
.rcb-track ul { margin: 6px 0 0 0; padding-left: 18px; font-size: 14px; }

/* ---------- heatmap ---------- */
.rcb-heat-wrap {
  overflow-x: auto; border: 1px solid var(--rcb-border); border-radius: 14px;
  background: var(--rcb-surface);
}
table.rcb-heat {
  border-collapse: separate; border-spacing: 3px; font-size: 13px; margin: 0; min-width: 760px;
  width: 100%;
}
table.rcb-heat th {
  font: 600 11px/1.25 var(--rcb-mono); color: var(--rcb-muted); padding: 8px 4px;
  text-align: center; white-space: nowrap; background: none; border: none;
}
table.rcb-heat th.sub, table.rcb-heat td.sub { text-align: left; }
table.rcb-heat td { padding: 8px 6px; border-radius: 6px; border: none; }
table.rcb-heat td.sub {
  font-weight: 700; color: var(--rcb-text); white-space: nowrap; max-width: 240px; overflow: hidden;
  text-overflow: ellipsis;
}
table.rcb-heat td.tot {
  font: 400 1.1rem var(--rcb-display); text-align: center; color: var(--rcb-text);
}
table.rcb-heat td.cell {
  text-align: center; font: 700 12.5px var(--rcb-mono); color: #0D161F; min-width: 54px;
}
table.rcb-heat td.cell.none {
  background: var(--rcb-surface-2) !important; color: var(--rcb-muted); font-weight: 600;
  font-size: 11px;
}

/* ---------- FAQ ---------- */
.rcb-faq { display: flex; flex-direction: column; gap: 10px; }
.rcb-faq details {
  border: 1px solid var(--rcb-border); border-radius: 14px; background: var(--rcb-surface);
  padding: 0; overflow: hidden;
}
.rcb-faq summary {
  cursor: pointer; padding: 14px 18px; font-weight: 700; font-size: 15px; color: var(--rcb-text);
  list-style: none; display: flex; justify-content: space-between; gap: 12px;
}
.rcb-faq summary::-webkit-details-marker { display: none; }
.rcb-faq summary::after { content: "+"; font: 700 18px/1 var(--rcb-mono); color: var(--rcb-cyan); }
.rcb-faq details[open] summary::after { content: "-"; }
.rcb-faq details[open] summary { border-bottom: 1px solid var(--rcb-border); }
.rcb-faq .a { padding: 12px 18px 14px 18px; font-size: 14.5px; color: var(--rcb-text); }
.rcb-faq .a p:last-child { margin-bottom: 0; }

/* ---------- my submissions / footer ---------- */
table.rcb-mine { min-width: 560px; }
.rcb-footer {
  text-align: center; color: var(--rcb-muted); font-size: 12.5px; padding: 18px 0 6px 0;
}
.rcb-footer a {
  color: var(--rcb-muted); text-decoration: underline dotted; text-underline-offset: 3px;
}

/* ---------- responsive ---------- */
@media (max-width: 860px) {
  .rcb-two, .rcb-tracks, .rcb-rules { grid-template-columns: minmax(0, 1fr); }
  .rcb-points td:first-child { width: auto; }
}
@media (max-width: 640px) {
  .rcb-tabs button[role="tab"] { font-size: 13px; padding-left: 8px; padding-right: 8px; }
  .rcb-hero { border-radius: 12px; }
  .rcb-hero-body { padding: 12px 14px; }
  .rcb-tagline { font-size: .93rem; flex-basis: 100%; }
  .rcb-chip { font-size: 11.5px; padding: 4px 10px; }
  .rcb-stats { gap: 8px; }
  .rcb-stat { padding: 10px 10px 10px 14px; }
  .rcb-stat .v { font-size: 1.5rem; }
  .rcb-stat .l { font-size: 10px; letter-spacing: .05em; }
  .rcb-stat .s { display: none; }
  .rcb-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  .rcb-card .body { padding: 10px; }
  .rcb-card .facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .rcb-step { grid-template-columns: minmax(0, 1fr); gap: 4px; padding: 14px; }
  .rcb-step .n, .rcb-step4-head .n { font-size: 1.8rem; }
  .rcb-step4-head { grid-template-columns: minmax(0, 1fr); gap: 4px; }
  .rcb-step4 { padding: 14px !important; }
  .rcb-h h2 { font-size: 1.4rem; }
  .rcb-points td { padding: 8px; }
  .rcb-points .pts { font-size: 1.35rem; width: 52px; }
}
@media (max-width: 420px) {
  .rcb-grid { grid-template-columns: minmax(0, 1fr); }
  .rcb-card .media { aspect-ratio: 16 / 10; }
  .rcb-card .media img { width: 70%; height: 90%; }
  .rcb-card .facts { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
"""

"""
CREW — Facility Intake & Evaluation
Run with: streamlit run crew_carbon_app.py
Requires: pip install streamlit reportlab
"""
from __future__ import annotations

from datetime import date
from io import BytesIO

import streamlit as st
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from crew_intake_engine import (
    Confidence,
    FacilityInputs,
    IntakeRecommendationEngine,
    MIN_RESIDUAL_ALK,
    TARGET_RESIDUAL_ALK,
)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="CREW", page_icon="💧", layout="wide")

CONF_STYLE: dict[Confidence, tuple[str, str]] = {
    Confidence.HIGH:        ("#D1FAE5", "#065F46"),   # green
    Confidence.MEDIUM:      ("#DBEAFE", "#1E40AF"),   # blue
    Confidence.LOW:         ("#FEF3C7", "#92400E"),   # amber
    Confidence.PRELIMINARY: ("#F3F4F6", "#374151"),   # gray
}

st.markdown("""
<style>
[data-testid="stMetric"] {
    background: #ffffff;
    border-radius: 8px;
    padding: 1rem 1.2rem;
    border-left: 4px solid #0077B6;
    box-shadow: 0 1px 4px rgba(0,0,0,0.10);
}
[data-testid="stMetricLabel"] p {
    color: #6B7280 !important;
    font-size: 0.82rem !important;
    font-weight: 600 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.04em !important;
}
[data-testid="stMetricValue"] {
    color: #0077B6 !important;
    font-weight: 700 !important;
}
.hero-card {
    background: #ffffff;
    border: 2px solid #0077B6;
    border-radius: 12px;
    padding: 2rem 2.5rem;
    text-align: center;
    box-shadow: 0 2px 8px rgba(0,119,182,0.12);
}
.hero-label {
    color: #6B7280;
    font-size: 0.85rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 0.4rem;
}
.hero-value {
    color: #0077B6;
    font-size: 3.8rem;
    font-weight: 800;
    line-height: 1.0;
}
.hero-sub {
    color: #9CA3AF;
    font-size: 0.82rem;
    margin-top: 0.5rem;
}
</style>
""", unsafe_allow_html=True)


# ── Helper: optional number input ─────────────────────────────────────────────

def _opt(label: str, unit: str = "mg/L", help: str = "") -> float | None:
    """Text input that returns None when left blank."""
    raw = st.text_input(
        f"{label}" + (f" ({unit})" if unit else ""),
        value="",
        placeholder="—",
        help=help,
    )
    if not raw.strip():
        return None
    try:
        v = float(raw)
        return max(0.0, v)
    except ValueError:
        st.caption(f"⚠ Enter a number or leave blank")
        return None


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 💧 CREW")
    st.caption("Facility Intake & Evaluation")
    st.divider()

    facility_name = st.text_input("Facility name", value="", placeholder="e.g. Riverside WWTP")

    st.markdown("**Required**")
    flow_mgd     = st.number_input("Flow", min_value=0.1, max_value=500.0, value=5.0,
                                   step=0.5, format="%.1f", label_visibility="visible",
                                   help="Plant flow in million gallons per day (MGD)")
    st.caption("million gallons / day")
    gcc_cost     = st.number_input("GCC product cost ($ / metric ton)",
                                   min_value=10.0, max_value=1000.0, value=120.0,
                                   step=10.0, format="%.0f")

    st.divider()

    with st.expander("💧 Influent water quality readings", expanded=True):
        st.caption("Enter whatever values you have from a recent lab report.")
        influent_nh3  = _opt("Ammonia, NH₃-N",       help="Incoming ammonia nitrogen")
        influent_no2  = _opt("Nitrite, NO₂-N",        help="Incoming nitrite nitrogen")
        influent_no3  = _opt("Nitrate, NO₃-N",        help="Incoming nitrate nitrogen")
        influent_p    = _opt("Ortho-phosphorus, PO₄", help="Incoming orthophosphate")
        influent_ph   = _opt("pH",          unit="",  help="Incoming wastewater pH (0–14)")
        influent_alk  = _opt("Alkalinity",            help="Total alkalinity as CaCO₃")

    with st.expander("📋 Effluent permit limits / targets"):
        st.caption("Enter any regulatory limits from your permit or design targets.")
        target_nh3  = _opt("Ammonia limit, NH₃-N",        help="Effluent NH₃-N target or permit limit")
        target_no3  = _opt("Nitrate limit, NO₃-N",        help="Effluent NO₃-N target or permit limit")
        target_tn   = _opt("Total nitrogen (TN) limit",   help="Combined N limit")
        target_tp   = _opt("Total phosphorus (TP) limit", help="Phosphorus limit")

    with st.expander("⚙️ Operational / settling data"):
        st.caption("From your MLSS or settleability records.")
        current_svi     = _opt("Current SVI", unit="mL/g",
                               help="Sludge volume index — measures settling behaviour")
        target_svi_red  = _opt("Target SVI reduction", unit="%",
                               help="Desired percentage improvement in SVI")

    with st.expander("🔧 GCC product settings"):
        dissolution_pct = st.slider("Dissolution efficiency (%)", 50, 100, 85, 5,
                                    help="Fraction of dosed GCC that dissolves. Lower values reflect high aeration-basin CO₂ stripping.")
        residual_target = st.slider("Target residual alkalinity (mg/L as CaCO₃)",
                                    int(MIN_RESIDUAL_ALK), 100, int(TARGET_RESIDUAL_ALK), 5,
                                    help="Safety buffer to protect nitrifying bacteria from pH swings.")


# ── Run engine ────────────────────────────────────────────────────────────────

inputs = FacilityInputs(
    flow_mgd               = flow_mgd,
    gcc_cost_per_mt        = gcc_cost,
    influent_nh3_mgl       = influent_nh3,
    influent_no2_mgl       = influent_no2,
    influent_no3_mgl       = influent_no3,
    influent_ortho_p_mgl   = influent_p,
    influent_ph            = influent_ph,
    influent_alkalinity_mgl= influent_alk,
    target_nh3_mgl         = target_nh3,
    target_no3_mgl         = target_no3,
    target_tn_mgl          = target_tn,
    target_tp_mgl          = target_tp,
    current_svi_ml_g       = current_svi,
    target_svi_reduction_pct = target_svi_red,
    dissolution_efficiency = dissolution_pct / 100,
    target_residual_alk_mgl= float(residual_target),
)

rec = IntakeRecommendationEngine(inputs).recommend()
fname = facility_name.strip() or "Facility"


# ── Header ────────────────────────────────────────────────────────────────────

st.title("💧 CREW")
st.markdown(f"**{fname}** &nbsp;·&nbsp; {flow_mgd:.1f} MGD")
st.divider()


# ── Hero: dose ────────────────────────────────────────────────────────────────

col_hero, col_metrics = st.columns([2, 3], gap="large")

with col_hero:
    range_text = f"Typical range: {rec.dose_range_mgl[0]:.0f} – {rec.dose_range_mgl[1]:.0f} mg/L"
    st.markdown(f"""
<div class="hero-card">
  <div class="hero-label">Recommended GCC Dose</div>
  <div class="hero-value">{rec.dose_mgl:.0f} mg/L</div>
  <div class="hero-sub">{range_text}</div>
</div>
""", unsafe_allow_html=True)

    # Confidence badge
    bg, fg = CONF_STYLE[rec.confidence]
    st.markdown(f"""
<div style="margin-top:1rem; text-align:center;">
  <span style="background:{bg}; color:{fg}; border-radius:20px; padding:0.3rem 1rem;
               font-size:0.82rem; font-weight:700; letter-spacing:0.04em;">
    {rec.confidence.value} confidence
  </span>
</div>
<div style="margin-top:0.5rem; text-align:center; color:#9CA3AF; font-size:0.78rem;">
  {rec.method}
</div>
""", unsafe_allow_html=True)

    # Data quality bar
    st.markdown("<div style='margin-top:1.2rem;'>", unsafe_allow_html=True)
    st.caption(f"Data completeness — {rec.data_score}%")
    st.progress(rec.data_score / 100)
    st.markdown("</div>", unsafe_allow_html=True)

with col_metrics:
    m1, m2 = st.columns(2)
    m1.metric("Tons per day",  f"{rec.mass_mt_per_day:.2f} MT")
    m2.metric("Cost per day",  f"${rec.cost_per_day_usd:,.0f}")

    m3, m4 = st.columns(2)
    m3.metric("Tons per year", f"{rec.mass_mt_per_day * 365:,.0f} MT")
    m4.metric("Cost per year", f"${rec.cost_per_year_usd:,.0f}")

    st.markdown("<br>", unsafe_allow_html=True)
    st.info(f"**How we calculated this:**\n\n{rec.explanation}")


# ── Assumptions (if any) ──────────────────────────────────────────────────────

if rec.assumptions:
    with st.expander(f"ℹ️ {len(rec.assumptions)} assumption(s) made — click to review"):
        for a in rec.assumptions:
            st.markdown(f"- {a}")
        st.caption(
            "Fill in more values in the sidebar to replace these assumptions "
            "with measured data and improve recommendation confidence."
        )

st.divider()


# ── PDF builder ───────────────────────────────────────────────────────────────

def _alt_rows(n: int, c1: object, c2: object) -> list:
    return [("BACKGROUND", (0, i), (-1, i), c1 if i % 2 == 1 else c2)
            for i in range(1, n + 1)]


def build_pdf(fname: str, inp: FacilityInputs, r) -> bytes:
    buf  = BytesIO()
    W, _ = letter
    mg   = 0.75 * inch
    uw   = W - 2 * mg

    doc = SimpleDocTemplate(buf, pagesize=letter,
                            topMargin=0.6*inch, bottomMargin=0.6*inch,
                            leftMargin=mg, rightMargin=mg)

    BLUE  = colors.HexColor("#0077B6")
    LB    = colors.HexColor("#E8F4FD")
    GRAY  = colors.HexColor("#6C757D")
    LGRAY = colors.HexColor("#DEE2E6")
    GREEN = colors.HexColor("#2DC653")
    AMBER = colors.HexColor("#D97706")

    base  = getSampleStyleSheet()
    T     = lambda name, **kw: ParagraphStyle(name, parent=base["Normal"], **kw)

    title_s   = T("t",  fontName="Helvetica-Bold", fontSize=20, textColor=BLUE, spaceAfter=2)
    sub_s     = T("s",  fontName="Helvetica",       fontSize=10, textColor=GRAY, spaceAfter=10)
    sec_s     = T("sc", fontName="Helvetica-Bold",  fontSize=11, textColor=BLUE,
                  spaceBefore=10, spaceAfter=5)
    body_s    = T("b",  fontName="Helvetica",       fontSize=9)
    caption_s = T("c",  fontName="Helvetica-Oblique", fontSize=7.5, textColor=GRAY, spaceAfter=6)
    foot_s    = T("f",  fontName="Helvetica", fontSize=7, textColor=GRAY, alignment=TA_CENTER)

    HCMDS = [
        ("BACKGROUND", (0,0), (-1,0), BLUE),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,0), 9),
        ("ALIGN",      (0,0), (-1,0), "CENTER"),
    ]
    BASE = [
        ("FONTSIZE",       (0,1), (-1,-1), 9),
        ("TOPPADDING",     (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",  (0,0), (-1,-1), 5),
        ("LEFTPADDING",    (0,0), (-1,-1), 6),
        ("RIGHTPADDING",   (0,0), (-1,-1), 6),
        ("VALIGN",         (0,0), (-1,-1), "MIDDLE"),
        ("GRID",           (0,0), (-1,-1), 0.25, LGRAY),
        ("BOX",            (0,0), (-1,-1), 1,    BLUE),
    ]

    story = []

    # Header
    story.append(Paragraph("CREW", title_s))
    story.append(Paragraph(
        f"Facility Evaluation &nbsp;·&nbsp; <b>{fname}</b> &nbsp;·&nbsp; "
        f"{date.today().strftime('%B %d, %Y')}",
        sub_s,
    ))
    story.append(HRFlowable(width="100%", thickness=2, color=BLUE, spaceAfter=10))

    # Dose hero table
    conf_color = GREEN if r.confidence == Confidence.HIGH else \
                 BLUE  if r.confidence == Confidence.MEDIUM else \
                 AMBER if r.confidence == Confidence.LOW else GRAY

    hero_data = [
        ["Recommended GCC Dose",   "Tons / Day",                  "Cost / Day",                 "Cost / Year"],
        [f"{r.dose_mgl:.0f} mg/L", f"{r.mass_mt_per_day:.2f} MT", f"${r.cost_per_day_usd:,.0f}", f"${r.cost_per_year_usd:,.0f}"],
        [f"Range: {r.dose_range_mgl[0]:.0f}–{r.dose_range_mgl[1]:.0f} mg/L",
         f"Plant flow: {inp.flow_mgd:.1f} MGD",
         f"${inp.gcc_cost_per_mt:,.0f}/MT",
         f"{r.mass_mt_per_day * 365:,.0f} MT/yr"],
    ]
    bw = uw / 4
    ht = Table(hero_data, colWidths=[bw]*4, rowHeights=[16, 26, 12])
    ht.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0), BLUE),
        ("TEXTCOLOR",     (0,0), (-1,0), colors.white),
        ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,0), 8),
        ("BACKGROUND",    (0,1), (-1,1), LB),
        ("TEXTCOLOR",     (0,1), (-1,1), BLUE),
        ("FONTNAME",      (0,1), (-1,1), "Helvetica-Bold"),
        ("FONTSIZE",      (0,1), (-1,1), 14),
        ("BACKGROUND",    (0,2), (-1,2), colors.white),
        ("TEXTCOLOR",     (0,2), (-1,2), GRAY),
        ("FONTNAME",      (0,2), (-1,2), "Helvetica-Oblique"),
        ("FONTSIZE",      (0,2), (-1,2), 7),
        ("ALIGN",         (0,0), (-1,-1), "CENTER"),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("BOX",           (0,0), (-1,-1), 1.5, BLUE),
        ("INNERGRID",     (0,0), (-1,-1), 0.5, LGRAY),
    ]))
    story.append(ht)

    # Confidence badge row
    story.append(Spacer(1, 6))
    badge_data = [[f"{r.confidence.value} Confidence  ·  {r.method}"]]
    bt = Table(badge_data, colWidths=[uw])
    bt.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), conf_color),
        ("TEXTCOLOR",     (0,0), (-1,-1), colors.white),
        ("FONTNAME",      (0,0), (-1,-1), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,-1), 8),
        ("ALIGN",         (0,0), (-1,-1), "CENTER"),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]))
    story.append(bt)
    story.append(Spacer(1, 10))

    # Explanation
    story.append(Paragraph("Recommendation Basis", sec_s))
    story.append(Paragraph(r.explanation, body_s))
    story.append(Spacer(1, 8))

    # Inputs used
    story.append(Paragraph("Facility Inputs", sec_s))

    def row(label, val): return [label, val if val is not None else "—"]
    input_rows = [
        ["Parameter", "Value"],
        row("Flow",                  f"{inp.flow_mgd:.1f} MGD"),
        row("GCC cost",              f"${inp.gcc_cost_per_mt:,.0f}/MT"),
        row("Dissolution efficiency",f"{inp.dissolution_efficiency*100:.0f}%"),
        row("Target residual alk.",  f"{inp.target_residual_alk_mgl:.0f} mg/L as CaCO₃"),
        row("Influent NH₃-N",        f"{inp.influent_nh3_mgl:.1f} mg/L" if inp.influent_nh3_mgl else None),
        row("Influent NO₂-N",        f"{inp.influent_no2_mgl:.1f} mg/L" if inp.influent_no2_mgl else None),
        row("Influent NO₃-N",        f"{inp.influent_no3_mgl:.1f} mg/L" if inp.influent_no3_mgl else None),
        row("Influent ortho-P",      f"{inp.influent_ortho_p_mgl:.1f} mg/L" if inp.influent_ortho_p_mgl else None),
        row("Influent pH",           f"{inp.influent_ph:.1f}" if inp.influent_ph else None),
        row("Influent alkalinity",   f"{inp.influent_alkalinity_mgl:.0f} mg/L as CaCO₃" if inp.influent_alkalinity_mgl else None),
        row("Effluent NH₃-N limit",  f"{inp.target_nh3_mgl:.1f} mg/L" if inp.target_nh3_mgl else None),
        row("Effluent NO₃-N limit",  f"{inp.target_no3_mgl:.1f} mg/L" if inp.target_no3_mgl else None),
        row("TN limit",              f"{inp.target_tn_mgl:.1f} mg/L" if inp.target_tn_mgl else None),
        row("TP limit",              f"{inp.target_tp_mgl:.1f} mg/L" if inp.target_tp_mgl else None),
        row("Current SVI",           f"{inp.current_svi_ml_g:.0f} mL/g" if inp.current_svi_ml_g else None),
    ]
    # Remove blank optional rows
    input_rows = [input_rows[0]] + [r for r in input_rows[1:] if r[1] != "—"]

    cw2 = [uw * 0.5, uw * 0.5]
    it  = Table(input_rows, colWidths=cw2)
    it.setStyle(TableStyle(
        HCMDS + BASE + _alt_rows(len(input_rows) - 1, colors.white, LB)
        + [("FONTNAME", (0,1), (0,-1), "Helvetica-Bold"),
           ("TEXTCOLOR",(0,1), (0,-1), GRAY)]
    ))
    story.append(it)

    if r.assumptions:
        story.append(Spacer(1, 8))
        story.append(Paragraph("Assumptions", sec_s))
        for a in r.assumptions:
            story.append(Paragraph(f"• {a}", caption_s))

    story.append(Spacer(1, 14))
    story.append(HRFlowable(width="100%", thickness=0.75, color=LGRAY, spaceAfter=6))
    story.append(Paragraph(
        f"Generated by CREW Facility Evaluation  ·  {date.today().strftime('%B %d, %Y')}  ·  "
        "Results are estimates based on stoichiometric mass balance and empirical data. "
        "Site-specific sampling is recommended before full-scale implementation.",
        foot_s,
    ))

    doc.build(story)
    return buf.getvalue()


# ── Download ──────────────────────────────────────────────────────────────────

pdf  = build_pdf(fname, inputs, rec)
safe = fname.replace(" ", "_")
st.download_button(
    label             = "📄 Download Summary Report",
    data              = pdf,
    file_name         = f"CREW_{safe}_{date.today()}.pdf",
    mime              = "application/pdf",
    type              = "primary",
    use_container_width = True,
)

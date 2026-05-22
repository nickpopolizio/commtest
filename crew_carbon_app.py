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
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from crew_carbon_evaluator import (
    CLARIFIER_CAPACITY_INCREASE,
    DO_SETPOINT_REDUCTION,
    MAX_RESIDUAL_ALK,
    MIN_RESIDUAL_ALK,
    NITRIFICATION_RATE_INCREASE,
    RAS_REDUCTION_MAX,
    RAS_REDUCTION_MIN,
    SVI_REDUCTION_MAX,
    SVI_REDUCTION_MIN,
    TARGET_RESIDUAL_ALK,
    CrewCarbonEvaluator,
)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="CREW Evaluation",
    page_icon="💧",
    layout="wide",
)

st.markdown("""
<style>
[data-testid="stMetric"] {
    background: #f0f7ff;
    border-radius: 8px;
    padding: 1rem 1.2rem;
    border-left: 3px solid #0077B6;
}
</style>
""", unsafe_allow_html=True)


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 💧 CREW")
    st.caption("Facility Intake & Evaluation")
    st.divider()

    facility_name = st.text_input("Facility Name", value="Sample WWTP")

    st.markdown("**Wastewater Characteristics**")
    flow_mgd = st.number_input(
        "Flow (MGD)", min_value=0.1, max_value=500.0, value=5.0, step=0.5, format="%.1f"
    )
    influent_nh3 = st.number_input(
        "Influent NH₃-N (mg/L)", min_value=0.0, max_value=150.0, value=30.0, step=1.0, format="%.1f"
    )
    effluent_nh3_target = st.number_input(
        "Target Effluent NH₃-N (mg/L)", min_value=0.0, max_value=30.0, value=3.0, step=0.5, format="%.1f"
    )
    effluent_no3_target = st.number_input(
        "Target Effluent NO₃-N (mg/L)", min_value=0.0, max_value=50.0, value=10.0, step=1.0, format="%.1f"
    )
    influent_alkalinity = st.number_input(
        "Influent Alkalinity (mg/L as CaCO₃)",
        min_value=10.0, max_value=500.0, value=200.0, step=10.0, format="%.0f",
        help="Typical municipal range: 50–400 mg/L",
    )

    st.markdown("**GCC Parameters**")
    gcc_dose = st.slider("GCC Dose (mg/L)", min_value=10.0, max_value=200.0, value=70.0, step=5.0)
    dissolution_pct = st.slider(
        "Dissolution Efficiency (%)", min_value=50, max_value=100, value=85, step=5,
        help="Discount for CO₂ stripping competition in aerated basins",
    )
    residual_target = st.slider(
        "Target Residual Alkalinity (mg/L as CaCO₃)",
        min_value=int(MIN_RESIDUAL_ALK),
        max_value=int(MAX_RESIDUAL_ALK),
        value=int(TARGET_RESIDUAL_ALK),
        step=5,
    )
    gcc_cost = st.number_input(
        "GCC Cost ($/metric ton)", min_value=50.0, max_value=500.0, value=120.0, step=10.0, format="%.0f"
    )


# ── Run evaluator ─────────────────────────────────────────────────────────────
try:
    ev = CrewCarbonEvaluator(
        flow_mgd=flow_mgd,
        influent_nh3=influent_nh3,
        effluent_nh3_target=effluent_nh3_target,
        effluent_no3_target=effluent_no3_target,
        influent_alkalinity_mgl=influent_alkalinity,
        gcc_dose_mgl=gcc_dose,
        dissolution_efficiency=dissolution_pct / 100,
        residual_alk_target=float(residual_target),
        gcc_cost_per_mt=gcc_cost,
    )
    fin = ev.calculate_financials_and_carbon()
    alk = ev.calculate_alkalinity_balance()
    kin = ev.estimate_kinetic_performance()
    phy = ev.calculate_physical_capacity()
except ValueError as exc:
    st.error(f"Input error: {exc}")
    st.stop()


# ── Header ────────────────────────────────────────────────────────────────────
st.title("💧 CREW")
st.markdown(f"**{facility_name}** &nbsp;·&nbsp; {flow_mgd:.1f} MGD &nbsp;·&nbsp; GCC dose: {gcc_dose:.0f} mg/L")
st.divider()

# ── Hero metrics ──────────────────────────────────────────────────────────────
st.subheader("Key Benefits at a Glance")
c1, c2, c3, c4 = st.columns(4)
c1.metric(
    "Annual Chemical OPEX",
    f"${fin.annual_opex_usd:,.0f}",
    help="Annual cost of GCC addition at the specified dose and unit price",
)
c2.metric(
    "Net CO₂ Sequestered",
    f"{fin.carbon_removal_tons:,.0f} MT / yr",
    help="Metric tons of CO₂ offset per year via CaCO₃ mineral weathering dissolution",
)
c3.metric(
    "Aeration DO Relief",
    f"−{kin.do_compensation_point_mg_l:.2f} mg/L",
    help="Allowable DO setpoint reduction that maintains equivalent nitrification throughput",
)
c4.metric(
    "Clarifier Capacity Gain",
    f"+{phy.clarifier_capacity_increase_pct:.0f}%",
    help="Regained hydraulic capacity from mineral ballasting and divalent cation bridging",
)

st.divider()

# ── Alkalinity balance ────────────────────────────────────────────────────────
st.subheader("Alkalinity Mass Balance")
col_table, col_status = st.columns([3, 2])

with col_table:
    nh3_removed = ev.influent_nh3 - ev.effluent_nh3_target
    st.markdown(f"""
| Parameter | mg/L as CaCO₃ |
|:---|---:|
| Influent alkalinity (baseline) | +{alk.influent_alkalinity_mg_l:.1f} |
| Nitrification demand &nbsp;({nh3_removed:.1f} mg/L NH₃ × 7.14) | −{alk.alk_consumed_mg_l:.1f} |
| Denitrification credit &nbsp;({alk.no3_denitrified_mg_l:.1f} mg/L NO₃ × 3.57) | +{alk.alk_recovered_mg_l:.1f} |
| **Net biological demand** | **{alk.net_biological_demand_mg_l:+.1f}** |
| GCC lift &nbsp;({gcc_dose:.0f} mg/L × {dissolution_pct}% efficiency) | +{alk.gcc_alkalinity_lift_mg_l:.1f} |
| **Net residual alkalinity** | **{alk.new_residual_alk_mg_l:.1f}** |
""")

with col_status:
    if alk.is_stable:
        st.success(
            f"**✓ Alkalinity Stable**\n\n"
            f"Residual: **{alk.new_residual_alk_mg_l:.1f} mg/L** as CaCO₃  \n"
            f"Target: {alk.target_residual_alk_mg_l:.0f} mg/L  \n"
            f"Floor: {MIN_RESIDUAL_ALK:.0f} mg/L"
        )
    else:
        st.error(
            f"**✗ Below Minimum Floor**\n\n"
            f"Residual: **{alk.new_residual_alk_mg_l:.1f} mg/L** as CaCO₃  \n"
            f"Minimum: {MIN_RESIDUAL_ALK:.0f} mg/L"
        )

    if alk.required_gcc_dose_mgl < gcc_dose * 0.99:
        st.info(
            f"ℹ️ **Dose headroom available**  \n"
            f"Min. dose to meet target: **{alk.required_gcc_dose_mgl:.1f} mg/L**  \n"
            f"Current dose: {gcc_dose:.0f} mg/L"
        )
    elif alk.required_gcc_dose_mgl > gcc_dose * 1.01:
        st.warning(
            f"⚠️ **Increase dose to {alk.required_gcc_dose_mgl:.1f} mg/L**  \n"
            f"to meet the {alk.target_residual_alk_mg_l:.0f} mg/L residual target."
        )

st.divider()

# ── Kinetics & Physical ───────────────────────────────────────────────────────
col_kin, col_phy = st.columns(2)

with col_kin:
    st.subheader("Nitrification Kinetics")
    st.markdown(f"""
| Metric | Value |
|:---|---:|
| Max. specific rate increase | +{NITRIFICATION_RATE_INCREASE * 100:.0f}% |
| Allowable DO setpoint reduction | −{kin.do_compensation_point_mg_l:.2f} mg/L |
""")
    st.caption(
        "GCC stabilises pH, driving the Monod pH-limitation factor toward 1.0. "
        "The resulting rate uplift allows an equivalent reduction in DO setpoint — "
        "directly reducing blower energy without sacrificing compliance."
    )

with col_phy:
    st.subheader("Clarifier & Solids Handling")
    st.markdown(f"""
| Metric | Value |
|:---|---:|
| SVI reduction | {phy.svi_reduction_min_pct:.0f}% – {phy.svi_reduction_max_pct:.0f}% |
| RAS flow reduction | {phy.ras_reduction_min_pct:.0f}% – {phy.ras_reduction_max_pct:.0f}% |
| Clarifier hydraulic capacity gain | +{phy.clarifier_capacity_increase_pct:.0f}% |
| CaCO₃ : floc density ratio | {phy.density_ratio:.2f}× |
""")
    st.caption(
        "Mineral ballasting from incorporated CaCO₃ crystals and divalent Ca²⁺ "
        "bridging improve floc settleability — increasing clarifier capacity and "
        "reducing return-sludge pumping costs without capital expenditure."
    )

st.divider()


# ── PDF builder ───────────────────────────────────────────────────────────────

def _row_backgrounds(n_data_rows: int, even: object, odd: object) -> list:
    """Return BACKGROUND commands for alternating row shading (rows 1..n)."""
    cmds = []
    for i in range(1, n_data_rows + 1):
        cmds.append(("BACKGROUND", (0, i), (-1, i), even if i % 2 == 1 else odd))
    return cmds


def build_pdf(
    facility: str,
    evaluator: CrewCarbonEvaluator,
    fin_r,
    alk_r,
    kin_r,
    phy_r,
) -> bytes:
    buffer = BytesIO()
    W, H = letter
    margin = 0.75 * inch
    usable_w = W - 2 * margin

    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        leftMargin=margin,
        rightMargin=margin,
    )

    # Palette
    BLUE       = colors.HexColor("#0077B6")
    LIGHT_BLUE = colors.HexColor("#E8F4FD")
    MID_BLUE   = colors.HexColor("#005F91")
    GREEN      = colors.HexColor("#2DC653")
    RED        = colors.HexColor("#DC3545")
    GRAY       = colors.HexColor("#6C757D")
    LGRAY      = colors.HexColor("#DEE2E6")

    base = getSampleStyleSheet()
    title_s = ParagraphStyle(
        "CRTitle", parent=base["Normal"],
        fontName="Helvetica-Bold", fontSize=20, textColor=BLUE, spaceAfter=2,
    )
    sub_s = ParagraphStyle(
        "CRSub", parent=base["Normal"],
        fontName="Helvetica", fontSize=10, textColor=GRAY, spaceAfter=10,
    )
    section_s = ParagraphStyle(
        "CRSection", parent=base["Normal"],
        fontName="Helvetica-Bold", fontSize=11, textColor=BLUE,
        spaceBefore=10, spaceAfter=5,
    )
    caption_s = ParagraphStyle(
        "CRCaption", parent=base["Normal"],
        fontName="Helvetica-Oblique", fontSize=7.5, textColor=GRAY, spaceAfter=6,
    )
    footer_s = ParagraphStyle(
        "CRFooter", parent=base["Normal"],
        fontName="Helvetica", fontSize=7, textColor=GRAY, alignment=TA_CENTER,
    )

    # Shared table style helpers
    HEADER_CMDS = [
        ("BACKGROUND",  (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
        ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, 0), 9),
        ("ALIGN",       (0, 0), (-1, 0), "CENTER"),
    ]
    CELL_CMDS = [
        ("FONTSIZE",       (0, 1), (-1, -1), 9),
        ("TOPPADDING",     (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 5),
        ("LEFTPADDING",    (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",   (0, 0), (-1, -1), 6),
        ("VALIGN",         (0, 0), (-1, -1), "MIDDLE"),
        ("GRID",           (0, 0), (-1, -1), 0.25, LGRAY),
        ("BOX",            (0, 0), (-1, -1), 1,    BLUE),
    ]

    story = []

    # ── Page header ──────────────────────────────────────────────────────────
    story.append(Paragraph("CREW", title_s))
    story.append(Paragraph(
        f"Facility Evaluation Report &nbsp;·&nbsp; <b>{facility}</b> &nbsp;·&nbsp; "
        f"{date.today().strftime('%B %d, %Y')}",
        sub_s,
    ))
    story.append(HRFlowable(width="100%", thickness=2, color=BLUE, spaceAfter=10))

    # ── Input summary table ───────────────────────────────────────────────────
    nh3_removed = evaluator.influent_nh3 - evaluator.effluent_nh3_target
    input_data = [
        ["Flow",              f"{evaluator.flow_mgd:.1f} MGD",
         "GCC Dose",          f"{evaluator.gcc_dose_mgl:.0f} mg/L"],
        ["Influent NH₃-N",   f"{evaluator.influent_nh3:.1f} mg/L",
         "Dissolution Eff.",  f"{evaluator.dissolution_efficiency * 100:.0f}%"],
        ["Target Eff. NH₃-N", f"{evaluator.effluent_nh3_target:.1f} mg/L",
         "Target Residual",   f"{evaluator.residual_alk_target:.0f} mg/L CaCO₃"],
        ["Target Eff. NO₃-N", f"{evaluator.effluent_no3_target:.1f} mg/L",
         "GCC Unit Cost",     f"${evaluator.gcc_cost_per_mt:,.0f}/MT"],
        ["Influent Alk.",     f"{evaluator.influent_alkalinity_mgl:.0f} mg/L CaCO₃",
         "",                  ""],
    ]
    cw = usable_w / 4
    inp_tbl = Table(input_data, colWidths=[cw * 0.75, cw * 0.75, cw * 0.85, cw * 0.65])
    inp_style = TableStyle(CELL_CMDS + [
        ("FONTNAME",  (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME",  (2, 0), (2, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 0), (0, -1), GRAY),
        ("TEXTCOLOR", (2, 0), (2, -1), GRAY),
        ("FONTSIZE",  (0, 0), (-1, -1), 8.5),
    ] + _row_backgrounds(len(input_data) - 1, colors.white, LIGHT_BLUE))
    inp_tbl.setStyle(inp_style)
    story.append(inp_tbl)
    story.append(Spacer(1, 10))

    # ── Key benefits (4-up metric boxes) ─────────────────────────────────────
    story.append(Paragraph("Key Benefits", section_s))
    bw = usable_w / 4
    benefit_data = [
        ["Annual OPEX",          "Net CO₂ Offset",              "Aeration DO Relief",          "Clarifier Capacity Gain"],
        [f"${fin_r.annual_opex_usd:,.0f}",
         f"{fin_r.carbon_removal_tons:,.0f} MT/yr",
         f"−{kin_r.do_compensation_point_mg_l:.2f} mg/L",
         f"+{phy_r.clarifier_capacity_increase_pct:.0f}%"],
        ["chemical cost per year", "CO₂ via mineral weathering", "DO setpoint reduction",       "hydraulic capacity"],
    ]
    ben_tbl = Table(benefit_data, colWidths=[bw] * 4, rowHeights=[18, 28, 14])
    ben_tbl.setStyle(TableStyle([
        # Label row
        ("BACKGROUND",  (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
        ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, 0), 8),
        # Value row
        ("BACKGROUND",  (0, 1), (-1, 1), LIGHT_BLUE),
        ("TEXTCOLOR",   (0, 1), (-1, 1), BLUE),
        ("FONTNAME",    (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 1), (-1, 1), 15),
        # Caption row
        ("BACKGROUND",  (0, 2), (-1, 2), colors.white),
        ("TEXTCOLOR",   (0, 2), (-1, 2), GRAY),
        ("FONTNAME",    (0, 2), (-1, 2), "Helvetica-Oblique"),
        ("FONTSIZE",    (0, 2), (-1, 2), 7),
        # All
        ("ALIGN",       (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",  (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("BOX",         (0, 0), (-1, -1), 1.5, BLUE),
        ("INNERGRID",   (0, 0), (-1, -1), 0.5, LGRAY),
    ]))
    story.append(ben_tbl)
    story.append(Spacer(1, 10))

    # ── Alkalinity balance ────────────────────────────────────────────────────
    story.append(Paragraph("Alkalinity Mass Balance", section_s))
    alk_status_text = "STABLE ✓" if alk_r.is_stable else "BELOW FLOOR ✗"
    alk_status_color = GREEN if alk_r.is_stable else RED
    alk_data = [
        ["Parameter",               "mg/L as CaCO₃", "Notes"],
        ["Influent alkalinity",     f"+{alk_r.influent_alkalinity_mg_l:.1f}",
         "Natural wastewater buffer"],
        ["Nitrification demand",    f"−{alk_r.alk_consumed_mg_l:.1f}",
         f"{nh3_removed:.1f} mg/L NH₃-N removed × 7.14"],
        ["Denitrification credit",  f"+{alk_r.alk_recovered_mg_l:.1f}",
         f"{alk_r.no3_denitrified_mg_l:.1f} mg/L NO₃-N reduced × 3.57"],
        ["Net biological demand",   f"{alk_r.net_biological_demand_mg_l:+.1f}",
         "Consumed minus recovered"],
        ["GCC alkalinity lift",     f"+{alk_r.gcc_alkalinity_lift_mg_l:.1f}",
         f"{evaluator.gcc_dose_mgl:.0f} mg/L × {evaluator.dissolution_efficiency*100:.0f}% dissolution efficiency"],
        ["Net residual alkalinity", f"{alk_r.new_residual_alk_mg_l:.1f}",
         f"Target: {alk_r.target_residual_alk_mg_l:.0f} mg/L  |  {alk_status_text}"],
    ]
    aw = [usable_w * 0.32, usable_w * 0.17, usable_w * 0.51]
    alk_tbl = Table(alk_data, colWidths=aw)
    alk_style = TableStyle(
        HEADER_CMDS + CELL_CMDS
        + _row_backgrounds(len(alk_data) - 2, colors.white, LIGHT_BLUE)
        + [
            ("FONTNAME",   (0, 1), (0, -1), "Helvetica-Bold"),
            ("ALIGN",      (1, 0), (1, -1), "CENTER"),
            # Residual row highlight
            ("BACKGROUND", (0, -1), (-1, -1), alk_status_color),
            ("TEXTCOLOR",  (0, -1), (-1, -1), colors.white),
            ("FONTNAME",   (0, -1), (-1, -1), "Helvetica-Bold"),
        ]
    )
    alk_tbl.setStyle(alk_style)
    story.append(alk_tbl)

    dose_diff = alk_r.required_gcc_dose_mgl - evaluator.gcc_dose_mgl
    if abs(dose_diff) > 0.5:
        direction = f"Increase dose to" if dose_diff > 0 else "Minimum dose to meet target:"
        story.append(Paragraph(
            f"{direction} <b>{alk_r.required_gcc_dose_mgl:.1f} mg/L</b> "
            f"(current: {evaluator.gcc_dose_mgl:.0f} mg/L).",
            caption_s,
        ))
    story.append(Spacer(1, 10))

    # ── Process performance table ─────────────────────────────────────────────
    story.append(Paragraph("Process Performance Benefits", section_s))
    perf_data = [
        ["Benefit Area",          "Metric",                      "Impact"],
        ["Nitrification kinetics", "Rate increase",              f"+{NITRIFICATION_RATE_INCREASE*100:.0f}%  (pH-factor → 1.0)"],
        ["",                       "Allowable DO reduction",     f"−{kin_r.do_compensation_point_mg_l:.2f} mg/L → blower energy savings"],
        ["Solids settling",        "SVI improvement",            f"{phy_r.svi_reduction_min_pct:.0f}% – {phy_r.svi_reduction_max_pct:.0f}% reduction"],
        ["",                       "RAS flow reduction",         f"{phy_r.ras_reduction_min_pct:.0f}% – {phy_r.ras_reduction_max_pct:.0f}% reduction"],
        ["Clarifier capacity",     "Hydraulic gain",             f"+{phy_r.clarifier_capacity_increase_pct:.0f}%  (mineral ballasting)"],
        ["Carbon & sustainability", "Annual GCC applied",        f"{fin_r.annual_tonnage_mt:,.1f} metric tons/yr"],
        ["",                        "CO₂ sequestered",           f"{fin_r.carbon_removal_tons:,.0f} MT CO₂/yr via mineral weathering"],
    ]
    pw = [usable_w * 0.26, usable_w * 0.28, usable_w * 0.46]
    perf_tbl = Table(perf_data, colWidths=pw)
    perf_tbl.setStyle(TableStyle(
        HEADER_CMDS + CELL_CMDS
        + _row_backgrounds(len(perf_data) - 1, colors.white, LIGHT_BLUE)
        + [("TEXTCOLOR", (0, 1), (0, -1), GRAY)]
    ))
    story.append(perf_tbl)
    story.append(Spacer(1, 14))

    # ── Footer ────────────────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.75, color=LGRAY, spaceAfter=6))
    story.append(Paragraph(
        f"Generated by CREW Facility Evaluation  ·  {date.today().strftime('%B %d, %Y')}  ·  "
        "Results are estimates based on stoichiometric mass balances and Monod kinetics. "
        "Site-specific pilot testing is recommended before full-scale implementation.",
        footer_s,
    ))

    doc.build(story)
    return buffer.getvalue()


# ── PDF download button ───────────────────────────────────────────────────────
pdf_bytes = build_pdf(facility_name, ev, fin, alk, kin, phy)
safe_name = facility_name.replace(" ", "_")
st.download_button(
    label="📄 Download PDF Report",
    data=pdf_bytes,
    file_name=f"CREW_Carbon_{safe_name}_{date.today()}.pdf",
    mime="application/pdf",
    type="primary",
    use_container_width=True,
)

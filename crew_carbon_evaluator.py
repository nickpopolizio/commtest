"""
CREW — Facility Evaluation
Intake and evaluation tool for wastewater treatment facilities.
Simulates the impact of Ground Calcium Carbonate (GCC) addition on
stoichiometric mass balances, Monod kinetics, and floc densification.
"""

from __future__ import annotations
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Global Constants
# ---------------------------------------------------------------------------

ALK_CONSUMED_PER_NH3: float = 7.14       # mg CaCO3 per mg NH3-N oxidized
ALK_RECOVERED_PER_NO3: float = 3.57      # mg CaCO3 per mg NO3-N reduced (denitrification)
MIN_RESIDUAL_ALK: float = 50.0           # mg/L hard lower safety floor (AOB/NOB inhibition threshold)
MAX_RESIDUAL_ALK: float = 100.0          # mg/L upper bound of target safety band
TARGET_RESIDUAL_ALK: float = 75.0        # mg/L default design residual (midpoint of safety band)

NITRIFICATION_RATE_INCREASE: float = 0.11   # 11% rate bump when pH factor → 1.0
DO_SETPOINT_REDUCTION: float = 0.75         # mg/L allowable DO reduction at parity

FLOC_DENSITY_G_CM3: float = 1.05
CACO3_DENSITY_G_CM3: float = 2.7

SVI_REDUCTION_MIN: float = 0.15             # 15% lower bound
SVI_REDUCTION_MAX: float = 0.35             # 35% upper bound
RAS_REDUCTION_MIN: float = 0.05             # 5% lower bound
RAS_REDUCTION_MAX: float = 0.40             # 40% upper bound
CLARIFIER_CAPACITY_INCREASE: float = 0.20  # 20%

CO2_SEQUESTERED_PER_CACO3: float = 0.44    # tons CO2 per ton CaCO3 dissolved

# Unit-conversion helpers
MGD_TO_L_PER_DAY: float = 3_785_411.784    # 1 MGD in L/day
MG_PER_L_TO_MT_PER_L: float = 1e-9         # mg/L × L → metric tons


# ---------------------------------------------------------------------------
# Result dataclasses (typed containers so callers can dot-access fields)
# ---------------------------------------------------------------------------

@dataclass
class FinancialResult:
    annual_tonnage_mt: float
    annual_opex_usd: float
    carbon_removal_tons: float


@dataclass
class AlkalinityResult:
    influent_alkalinity_mg_l: float
    alk_consumed_mg_l: float           # nitrification demand
    alk_recovered_mg_l: float          # denitrification credit
    no3_denitrified_mg_l: float        # NO3-N actually reduced to N2
    net_biological_demand_mg_l: float  # consumed − recovered (positive = net demand)
    gcc_alkalinity_lift_mg_l: float
    new_residual_alk_mg_l: float
    target_residual_alk_mg_l: float
    required_gcc_dose_mgl: float       # dose needed to just meet target residual
    is_stable: bool                    # True if residual ≥ MIN_RESIDUAL_ALK


@dataclass
class KineticResult:
    baseline_nitrification_rate_relative: float    # normalised to 1.0
    enhanced_nitrification_rate_relative: float
    do_compensation_point_mg_l: float


@dataclass
class PhysicalCapacityResult:
    svi_reduction_min_pct: float
    svi_reduction_max_pct: float
    ras_reduction_min_pct: float
    ras_reduction_max_pct: float
    clarifier_capacity_increase_pct: float
    density_ratio: float                           # CaCO3 / floc density


# ---------------------------------------------------------------------------
# Main evaluator class
# ---------------------------------------------------------------------------

class CrewCarbonEvaluator:
    """
    Evaluates the operational, financial, and environmental impact of adding
    Ground Calcium Carbonate (GCC) to a wastewater treatment facility.

    Parameters
    ----------
    flow_mgd : float
        Plant flow in million gallons per day.
    influent_nh3 : float
        Influent NH3-N concentration in mg/L.
    effluent_nh3_target : float
        Target effluent NH3-N concentration in mg/L.
    effluent_no3_target : float
        Target effluent NO3-N concentration in mg/L (after denitrification).
    influent_alkalinity_mgl : float
        Influent total alkalinity in mg/L as CaCO3. Municipal wastewater typically
        ranges from 50–400 mg/L; this is the primary natural buffer in the system.
    gcc_dose_mgl : float
        GCC dose in mg/L (default 70.0).
    dissolution_efficiency : float
        Fraction of dosed GCC that dissolves via the carbonic acid pathway (0–1).
        Values below 1.0 reflect CO2 stripping competition in highly aerated basins.
        Default 0.85.
    residual_alk_target : float
        Design target for residual alkalinity in mg/L as CaCO3 (default TARGET_RESIDUAL_ALK).
        Must be ≥ MIN_RESIDUAL_ALK to maintain pH stability around AOB/NOB.
    gcc_cost_per_mt : float
        GCC chemical cost in USD per metric ton.
    """

    def __init__(
        self,
        flow_mgd: float,
        influent_nh3: float,
        effluent_nh3_target: float,
        effluent_no3_target: float,
        influent_alkalinity_mgl: float,
        gcc_dose_mgl: float = 70.0,
        dissolution_efficiency: float = 0.85,
        residual_alk_target: float = TARGET_RESIDUAL_ALK,
        gcc_cost_per_mt: float = 120.0,
    ) -> None:
        if not (0.0 < dissolution_efficiency <= 1.0):
            raise ValueError("dissolution_efficiency must be in (0, 1]")
        if residual_alk_target < MIN_RESIDUAL_ALK:
            raise ValueError(
                f"residual_alk_target ({residual_alk_target}) is below the minimum "
                f"safe floor ({MIN_RESIDUAL_ALK} mg/L as CaCO3)"
            )

        self.flow_mgd = flow_mgd
        self.influent_nh3 = influent_nh3
        self.effluent_nh3_target = effluent_nh3_target
        self.effluent_no3_target = effluent_no3_target
        self.influent_alkalinity_mgl = influent_alkalinity_mgl
        self.gcc_dose_mgl = gcc_dose_mgl
        self.dissolution_efficiency = dissolution_efficiency
        self.residual_alk_target = residual_alk_target
        self.gcc_cost_per_mt = gcc_cost_per_mt

        # Derived convenience attributes
        self._flow_l_per_day: float = flow_mgd * MGD_TO_L_PER_DAY
        self._nh3_removed_mg_l: float = max(0.0, influent_nh3 - effluent_nh3_target)

    # ------------------------------------------------------------------
    # 1. Financials & Carbon
    # ------------------------------------------------------------------

    def calculate_financials_and_carbon(self) -> FinancialResult:
        """
        Convert plant flow and GCC dose to annual mass, cost, and CO2 sequestration.

        Returns
        -------
        FinancialResult
            annual_tonnage_mt  : metric tons of GCC applied per year
            annual_opex_usd    : annual chemical cost in USD
            carbon_removal_tons: metric tons of CO2 sequestered per year
        """
        # mg/L × L/day × days/year → mg/year → metric tons/year
        annual_tonnage_mt: float = (
            self.gcc_dose_mgl
            * self._flow_l_per_day
            * 365
            * MG_PER_L_TO_MT_PER_L
        )
        annual_opex_usd: float = annual_tonnage_mt * self.gcc_cost_per_mt
        carbon_removal_tons: float = annual_tonnage_mt * CO2_SEQUESTERED_PER_CACO3

        return FinancialResult(
            annual_tonnage_mt=annual_tonnage_mt,
            annual_opex_usd=annual_opex_usd,
            carbon_removal_tons=carbon_removal_tons,
        )

    # ------------------------------------------------------------------
    # 2. Alkalinity Balance
    # ------------------------------------------------------------------

    def calculate_alkalinity_balance(self) -> AlkalinityResult:
        """
        Full stoichiometric alkalinity mass balance including influent buffer,
        biological demand, denitrification credit, and GCC dissolution.

        Stoichiometry
        -------------
        Nitrification consumes : NH3_removed × 7.14 mg/L as CaCO3
        Denitrification recovers: NO3_denitrified × 3.57 mg/L as CaCO3
          where NO3_denitrified = max(0, NH3_oxidized − effluent_NO3_target)

        GCC dissolution (carbonic acid pathway):
          CaCO3(s) + CO2(aq) + H2O → Ca²⁺ + 2 HCO3⁻
          On a CaCO3-equivalent basis this is a 1:1 conversion — 1 mg/L GCC
          dissolved yields exactly 1 mg/L alkalinity as CaCO3.  The
          dissolution_efficiency parameter (0–1) discounts this for CO2
          stripping competition in aerated basins.

        Residual = influent_alk + gcc_lift − net_biological_demand

        Back-calculates the minimum GCC dose required to meet residual_alk_target.

        Returns
        -------
        AlkalinityResult
        """
        # --- Biological demand ---
        alk_consumed: float = self._nh3_removed_mg_l * ALK_CONSUMED_PER_NH3

        # NO3 denitrified = N nitrified − NO3 remaining in effluent (≥ 0)
        no3_denitrified: float = max(0.0, self._nh3_removed_mg_l - self.effluent_no3_target)
        alk_recovered: float = no3_denitrified * ALK_RECOVERED_PER_NO3

        net_bio_demand: float = alk_consumed - alk_recovered

        # --- GCC alkalinity lift ---
        # 1 mg/L CaCO3 dissolved → 1 mg/L alk as CaCO3 (stoichiometric, 1:1)
        # discounted by dissolution_efficiency for aeration-basin CO2 stripping
        gcc_lift: float = self.gcc_dose_mgl * self.dissolution_efficiency

        # --- Net residual ---
        new_residual: float = self.influent_alkalinity_mgl + gcc_lift - net_bio_demand

        # --- Required dose to meet target residual ---
        # target = influent_alk + (required_dose × efficiency) − net_bio_demand
        # → required_dose = (target − influent_alk + net_bio_demand) / efficiency
        required_dose: float = max(
            0.0,
            (self.residual_alk_target - self.influent_alkalinity_mgl + net_bio_demand)
            / self.dissolution_efficiency,
        )

        return AlkalinityResult(
            influent_alkalinity_mg_l=self.influent_alkalinity_mgl,
            alk_consumed_mg_l=alk_consumed,
            alk_recovered_mg_l=alk_recovered,
            no3_denitrified_mg_l=no3_denitrified,
            net_biological_demand_mg_l=net_bio_demand,
            gcc_alkalinity_lift_mg_l=gcc_lift,
            new_residual_alk_mg_l=new_residual,
            target_residual_alk_mg_l=self.residual_alk_target,
            required_gcc_dose_mgl=required_dose,
            is_stable=new_residual >= MIN_RESIDUAL_ALK,
        )

    # ------------------------------------------------------------------
    # 3. Kinetic Performance
    # ------------------------------------------------------------------

    def estimate_kinetic_performance(self) -> KineticResult:
        """
        Apply simplified Monod kinetics assuming GCC addition neutralises the
        pH limitation factor (η_pH → 1.0), yielding an 11% increase in the
        maximum specific nitrification rate.

        The same rate uplift is expressed as an equivalent DO compensation:
        the plant can lower its DO setpoint by DO_SETPOINT_REDUCTION mg/L and
        still achieve the same nitrification throughput.

        Returns
        -------
        KineticResult
            baseline_nitrification_rate_relative : 1.0 (normalised baseline)
            enhanced_nitrification_rate_relative : 1.0 + NITRIFICATION_RATE_INCREASE
            do_compensation_point_mg_l           : allowable DO reduction in mg/L
        """
        baseline: float = 1.0
        enhanced: float = baseline * (1.0 + NITRIFICATION_RATE_INCREASE)

        return KineticResult(
            baseline_nitrification_rate_relative=baseline,
            enhanced_nitrification_rate_relative=enhanced,
            do_compensation_point_mg_l=DO_SETPOINT_REDUCTION,
        )

    # ------------------------------------------------------------------
    # 4. Physical / Clarifier Capacity
    # ------------------------------------------------------------------

    def calculate_physical_capacity(self) -> PhysicalCapacityResult:
        """
        Estimate sedimentation and clarifier benefits from mineral ballasting
        (increased floc density) and divalent cation bridging (Ca²⁺ from GCC).

        Density ratio quantifies the relative ballasting effect of CaCO3
        crystals incorporated into floc vs. native floc density.

        Returns
        -------
        PhysicalCapacityResult
            svi_reduction_min/max_pct        : SVI improvement range (%)
            ras_reduction_min/max_pct        : RAS flow reduction range (%)
            clarifier_capacity_increase_pct : regained clarifier capacity (%)
            density_ratio                   : CaCO3 density / floc density
        """
        density_ratio: float = CACO3_DENSITY_G_CM3 / FLOC_DENSITY_G_CM3

        return PhysicalCapacityResult(
            svi_reduction_min_pct=SVI_REDUCTION_MIN * 100,
            svi_reduction_max_pct=SVI_REDUCTION_MAX * 100,
            ras_reduction_min_pct=RAS_REDUCTION_MIN * 100,
            ras_reduction_max_pct=RAS_REDUCTION_MAX * 100,
            clarifier_capacity_increase_pct=CLARIFIER_CAPACITY_INCREASE * 100,
            density_ratio=density_ratio,
        )

    # ------------------------------------------------------------------
    # 5. Manager Report
    # ------------------------------------------------------------------

    def generate_manager_report(self) -> str:
        """
        Compile results from all evaluation methods into a plain-language
        summary formatted for a utility manager.

        Returns
        -------
        str
            Multi-section formatted report string.
        """
        fin = self.calculate_financials_and_carbon()
        alk = self.calculate_alkalinity_balance()
        kin = self.estimate_kinetic_performance()
        phy = self.calculate_physical_capacity()

        alk_status = "STABLE" if alk.is_stable else "REVIEW REQUIRED"
        alk_flag   = "" if alk.is_stable else "  *** BELOW MINIMUM THRESHOLD ***"

        dose_note: str = (
            f"  Recommended GCC Dose     : {alk.required_gcc_dose_mgl:.1f} mg/L "
            f"(to reach {alk.target_residual_alk_mg_l:.0f} mg/L residual)"
        )

        report_lines: list[str] = [
            "=" * 65,
            "  CREW — Facility Evaluation Report",
            "=" * 65,
            "",
            "PLANT INPUTS",
            f"  Flow                     : {self.flow_mgd:.2f} MGD",
            f"  Influent NH3-N           : {self.influent_nh3:.1f} mg/L",
            f"  Target Effluent NH3-N    : {self.effluent_nh3_target:.1f} mg/L",
            f"  Target Effluent NO3-N    : {self.effluent_no3_target:.1f} mg/L",
            f"  Influent Alkalinity      : {self.influent_alkalinity_mgl:.0f} mg/L as CaCO₃",
            f"  GCC Dose (proposed)      : {self.gcc_dose_mgl:.1f} mg/L",
            f"  Dissolution Efficiency   : {self.dissolution_efficiency * 100:.0f}%",
            f"  GCC Unit Cost            : ${self.gcc_cost_per_mt:,.2f} / metric ton",
            "",
            "─" * 65,
            "1. FINANCIAL & CARBON IMPACT",
            "─" * 65,
            f"  Annual GCC Applied       : {fin.annual_tonnage_mt:,.1f} metric tons/yr",
            f"  Annual Chemical OPEX     : ${fin.annual_opex_usd:,.0f} / yr",
            f"  Net CO₂ Sequestered      : {fin.carbon_removal_tons:,.1f} metric tons CO₂/yr",
            "  (CO₂ credit via CaCO₃ mineral weathering dissolution pathway)",
            "",
            "─" * 65,
            "2. ALKALINITY BALANCE",
            "─" * 65,
            f"  Influent Alkalinity      : +{alk.influent_alkalinity_mg_l:.1f} mg/L as CaCO₃",
            f"  Nitrification Demand     :  -{alk.alk_consumed_mg_l:.1f} mg/L as CaCO₃",
            f"    (NH3 removed: {self._nh3_removed_mg_l:.1f} mg/L × 7.14)",
            f"  Denitrification Credit   : +{alk.alk_recovered_mg_l:.1f} mg/L as CaCO₃",
            f"    (NO3 denitrified: {alk.no3_denitrified_mg_l:.1f} mg/L × 3.57)",
            f"  Net Biological Demand    :  {alk.net_biological_demand_mg_l:+.1f} mg/L as CaCO₃",
            f"  GCC Alkalinity Lift      : +{alk.gcc_alkalinity_lift_mg_l:.1f} mg/L as CaCO₃",
            f"    ({self.gcc_dose_mgl:.1f} mg/L × {self.dissolution_efficiency * 100:.0f}% efficiency; 1:1 stoichiometric basis)",
            f"  Net Residual Alkalinity  :  {alk.new_residual_alk_mg_l:.1f} mg/L as CaCO₃",
            f"  Target Residual          : {alk.target_residual_alk_mg_l:.0f} mg/L  "
            f"[floor: {MIN_RESIDUAL_ALK:.0f}, ceiling: {MAX_RESIDUAL_ALK:.0f}]",
            f"  Alkalinity Status        : {alk_status}{alk_flag}",
            dose_note,
            "",
            "─" * 65,
            "3. NITRIFICATION KINETICS",
            "─" * 65,
            f"  Baseline Rate (relative) : {kin.baseline_nitrification_rate_relative:.2f}×",
            f"  Enhanced Rate (relative) : {kin.enhanced_nitrification_rate_relative:.2f}×  "
            f"(+{NITRIFICATION_RATE_INCREASE * 100:.0f}% via pH-factor optimisation)",
            f"  Aeration Headroom        : DO setpoint may be reduced by "
            f"{kin.do_compensation_point_mg_l:.2f} mg/L",
            "  → Lower DO setpoint = direct blower energy savings with no",
            "    loss of nitrification capacity.",
            "",
            "─" * 65,
            "4. CLARIFIER & SOLIDS HANDLING",
            "─" * 65,
            f"  Ballast Density Ratio    : {phy.density_ratio:.2f}× (CaCO₃ vs. floc)",
            f"  Expected SVI Reduction   : {phy.svi_reduction_min_pct:.0f}% – {phy.svi_reduction_max_pct:.0f}%",
            f"  RAS Flow Reduction       : {phy.ras_reduction_min_pct:.0f}% – {phy.ras_reduction_max_pct:.0f}%",
            f"  Regained Clarifier Cap.  : ~{phy.clarifier_capacity_increase_pct:.0f}%",
            "  → Improved settling reduces return sludge pumping costs and",
            "    increases hydraulic capacity without capital expenditure.",
            "",
            "=" * 65,
            "  KEY ACTIONABLE METRICS FOR MANAGEMENT",
            "=" * 65,
            f"  Annual OPEX              : ${fin.annual_opex_usd:,.0f}",
            f"  Net CO₂ Offset           : {fin.carbon_removal_tons:,.1f} MT CO₂/yr",
            f"  Aeration Energy Relief   : -{kin.do_compensation_point_mg_l:.2f} mg/L DO setpoint",
            f"  Clarifier Capacity Gain  : +{phy.clarifier_capacity_increase_pct:.0f}%",
            f"  Alkalinity Stability     : {alk_status}",
            "=" * 65,
        ]

        return "\n".join(report_lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    evaluator = CrewCarbonEvaluator(
        flow_mgd=5.0,
        influent_nh3=30.0,
        effluent_nh3_target=3.0,
        effluent_no3_target=10.0,
        influent_alkalinity_mgl=200.0,   # typical municipal wastewater
        gcc_dose_mgl=70.0,
        dissolution_efficiency=0.85,     # 85% — moderate aeration-basin CO2 stripping
        residual_alk_target=75.0,        # midpoint of 50–100 mg/L safety band
        gcc_cost_per_mt=120.0,
    )

    print(evaluator.generate_manager_report())

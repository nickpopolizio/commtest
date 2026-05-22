"""
CREW — Facility Intake Engine
Given any subset of facility measurements and permit limits, selects the
highest-confidence calculation path and returns a GCC dose recommendation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# ── Stoichiometric constants ───────────────────────────────────────────────────
ALK_CONSUMED_PER_NH3: float = 7.14    # mg CaCO₃ per mg NH₃-N nitrified
ALK_RECOVERED_PER_NO3: float = 3.57   # mg CaCO₃ per mg NO₃-N denitrified
TARGET_RESIDUAL_ALK: float = 75.0     # mg/L default design residual
MIN_RESIDUAL_ALK: float = 50.0        # mg/L hard safety floor

# Conservative fall-back assumptions when measured data is absent
ASSUMED_INFLUENT_NH3: float = 30.0    # mg/L — typical municipal
ASSUMED_INFLUENT_ALK: float = 150.0   # mg/L as CaCO₃ — typical municipal

# Unit conversion: dose_mgl × flow_mgd × this = MT/day
_MT_PER_DAY_FACTOR: float = 3.785412e-3


# ── Data containers ───────────────────────────────────────────────────────────

class Confidence(str, Enum):
    HIGH        = "High"
    MEDIUM      = "Medium"
    LOW         = "Low"
    PRELIMINARY = "Preliminary"


@dataclass
class FacilityInputs:
    """
    Intake form. Only flow_mgd and gcc_cost_per_mt are required.
    All water quality and operational fields are optional — provide
    whatever the facility has on hand. More data improves accuracy.
    """
    # Required
    flow_mgd: float
    gcc_cost_per_mt: float

    # Influent water quality (any combination)
    influent_nh3_mgl: float | None = None
    influent_no2_mgl: float | None = None
    influent_no3_mgl: float | None = None
    influent_ortho_p_mgl: float | None = None
    influent_ph: float | None = None
    influent_alkalinity_mgl: float | None = None

    # Effluent permit limits / targets (any combination)
    target_nh3_mgl: float | None = None
    target_no3_mgl: float | None = None
    target_tn_mgl: float | None = None
    target_tp_mgl: float | None = None

    # Operational
    current_svi_ml_g: float | None = None
    target_svi_reduction_pct: float | None = None

    # GCC product parameters
    dissolution_efficiency: float = 0.85
    target_residual_alk_mgl: float = TARGET_RESIDUAL_ALK


@dataclass
class DoseRecommendation:
    dose_mgl: float
    dose_range_mgl: tuple[float, float]   # (conservative min, upper bound)
    mass_mt_per_day: float
    cost_per_day_usd: float
    cost_per_year_usd: float
    confidence: Confidence
    method: str                            # one-liner shown in UI
    explanation: str                       # plain-language paragraph
    assumptions: list[str]                 # any values that were assumed
    data_score: int                        # 0–100, drives quality indicator


# ── Recommendation engine ─────────────────────────────────────────────────────

class IntakeRecommendationEngine:
    """
    Picks the highest-confidence available calculation path, runs it,
    and returns a DoseRecommendation.

    Path priority (descending confidence):
      A  Full stoichiometric balance    — influent NH₃ + alk + effluent target
      B  Alkalinity deficit             — influent alk known, N demand estimated
      C  pH proxy                       — alk estimated from pH
      D  Permit limits only             — N targets but no influent quality
      E  SVI-based empirical            — settling data only
      F  Conservative default           — flow & cost only
    """

    def __init__(self, inputs: FacilityInputs) -> None:
        self.inp = inputs

    # ── Public ────────────────────────────────────────────────────────────────

    def recommend(self) -> DoseRecommendation:
        nh3_removed, net_alk_demand = self._nitrogen_demand()
        influent_alk               = self._effective_influent_alkalinity()

        dose, confidence, method, explanation, assumptions = self._select_path(
            nh3_removed, net_alk_demand, influent_alk
        )

        dose     = max(0.0, min(dose, 300.0))
        dose_min = round(max(0.0, dose * 0.85), 1)
        dose_max = round(min(300.0, dose * 1.20), 1)

        mass    = dose * self.inp.flow_mgd * _MT_PER_DAY_FACTOR
        cpd     = mass * self.inp.gcc_cost_per_mt
        cpy     = cpd * 365

        return DoseRecommendation(
            dose_mgl         = round(dose, 1),
            dose_range_mgl   = (dose_min, dose_max),
            mass_mt_per_day  = round(mass, 3),
            cost_per_day_usd = round(cpd, 2),
            cost_per_year_usd= round(cpy, 0),
            confidence       = confidence,
            method           = method,
            explanation      = explanation,
            assumptions      = assumptions,
            data_score       = self._data_score(),
        )

    # ── Nitrogen demand ───────────────────────────────────────────────────────

    def _nitrogen_demand(self) -> tuple[float, float]:
        """
        Return (nh3_removed_mgl, net_alk_demand_mgl) from whatever
        nitrogen data is available. Conservative when data is absent
        (no denitrification credit assumed unless a NO₃ target is known).
        """
        inp = self.inp

        # ── Influent NH₃ ──────────────────────────────────────────────────────
        influent_nh3 = inp.influent_nh3_mgl if inp.influent_nh3_mgl is not None \
                       else ASSUMED_INFLUENT_NH3

        # ── Effluent NH₃ target ───────────────────────────────────────────────
        target_nh3 = inp.target_nh3_mgl
        if target_nh3 is None and inp.target_tn_mgl is not None:
            # TN limit implies full nitrification; assume tight NH₃ target
            target_nh3 = min(influent_nh3 * 0.1, 3.0)
        if target_nh3 is None:
            target_nh3 = 3.0   # assume typical permit limit (conservative)

        nh3_removed     = max(0.0, influent_nh3 - target_nh3)
        alk_consumed    = nh3_removed * ALK_CONSUMED_PER_NH3

        # ── Denitrification credit (only if a NO₃ target is available) ────────
        target_no3 = inp.target_no3_mgl
        if target_no3 is None and inp.target_tn_mgl is not None:
            target_no3 = max(0.0, inp.target_tn_mgl - (target_nh3 or 0))

        alk_recovered = 0.0
        if target_no3 is not None:
            no3_denitrified = max(0.0, nh3_removed - target_no3)
            alk_recovered   = no3_denitrified * ALK_RECOVERED_PER_NO3

        return nh3_removed, max(0.0, alk_consumed - alk_recovered)

    # ── Effective influent alkalinity ─────────────────────────────────────────

    def _effective_influent_alkalinity(self) -> float:
        if self.inp.influent_alkalinity_mgl is not None:
            return self.inp.influent_alkalinity_mgl
        if self.inp.influent_ph is not None:
            return self._alk_from_ph(self.inp.influent_ph)
        return ASSUMED_INFLUENT_ALK

    @staticmethod
    def _alk_from_ph(ph: float) -> float:
        """
        Empirical pH → alkalinity brackets for municipal activated sludge.
        Not a substitute for measurement; always flagged as an assumption.
        """
        if ph < 6.5:  return 25.0
        if ph < 6.8:  return 50.0
        if ph < 7.0:  return 80.0
        if ph < 7.2:  return 120.0
        if ph < 7.5:  return 175.0
        if ph < 7.8:  return 250.0
        return 320.0

    # ── Path selection ────────────────────────────────────────────────────────

    def _select_path(
        self,
        nh3_removed: float,
        net_alk_demand: float,
        influent_alk: float,
    ) -> tuple[float, Confidence, str, str, list[str]]:

        inp         = self.inp
        assumptions: list[str] = []
        eff         = inp.dissolution_efficiency
        target_res  = inp.target_residual_alk_mgl

        def dose_from_balance(alk: float) -> float:
            deficit = net_alk_demand - (alk - target_res)
            return max(0.0, deficit / eff)

        # ── Path A: full stoichiometric ───────────────────────────────────────
        nh3_known     = inp.influent_nh3_mgl is not None
        alk_measured  = inp.influent_alkalinity_mgl is not None
        target_known  = inp.target_nh3_mgl is not None or inp.target_tn_mgl is not None

        if nh3_known and alk_measured and target_known:
            dose = dose_from_balance(influent_alk)
            return (
                dose,
                Confidence.HIGH,
                "Full stoichiometric alkalinity mass balance",
                (
                    f"With {inp.influent_nh3_mgl:.0f} mg/L incoming ammonia and a target of "
                    f"{inp.target_nh3_mgl or '~3'} mg/L, nitrification will consume approximately "
                    f"{nh3_removed * ALK_CONSUMED_PER_NH3:.0f} mg/L of alkalinity. "
                    f"Measured influent alkalinity of {influent_alk:.0f} mg/L as CaCO₃ "
                    f"{'covers this demand — only a small maintenance dose is needed'if dose < 10 else 'is not sufficient on its own'}. "
                    f"A dose of {dose:.0f} mg/L GCC maintains a safe residual of {target_res:.0f} mg/L."
                ),
                assumptions,
            )

        # ── Path B: alkalinity measured, nitrogen estimated ───────────────────
        if alk_measured:
            if not nh3_known:
                assumptions.append(
                    f"Incoming ammonia assumed {ASSUMED_INFLUENT_NH3:.0f} mg/L (typical municipal — measure for better accuracy)"
                )
            if not target_known:
                assumptions.append("Full nitrification to 3 mg/L assumed (conservative)")

            dose = dose_from_balance(influent_alk)
            return (
                dose,
                Confidence.MEDIUM,
                "Alkalinity deficit calculation with estimated nitrogen demand",
                (
                    f"Starting from the measured alkalinity of {influent_alk:.0f} mg/L as CaCO₃, "
                    f"the estimated nitrification demand of {net_alk_demand:.0f} mg/L "
                    f"{'leaves you with adequate headroom — a small dose maintains the safety buffer' if dose < 10 else 'creates a deficit that GCC needs to cover'}. "
                    f"A dose of {dose:.0f} mg/L GCC will maintain the {target_res:.0f} mg/L "
                    f"target residual. Entering the plant's effluent permit limits will refine this further."
                ),
                assumptions,
            )

        # ── Path C: pH as alkalinity proxy ────────────────────────────────────
        if inp.influent_ph is not None:
            est_alk = self._alk_from_ph(inp.influent_ph)
            assumptions.append(
                f"Alkalinity estimated from pH {inp.influent_ph:.1f} as ~{est_alk:.0f} mg/L as CaCO₃ "
                "(direct measurement will significantly improve accuracy)"
            )
            if not nh3_known:
                assumptions.append(f"Incoming ammonia assumed {ASSUMED_INFLUENT_NH3:.0f} mg/L")

            dose = dose_from_balance(est_alk)
            return (
                dose,
                Confidence.LOW,
                "pH-derived alkalinity estimate",
                (
                    f"A pH of {inp.influent_ph:.1f} suggests an influent alkalinity of roughly "
                    f"{est_alk:.0f} mg/L as CaCO₃. Based on this and an estimated nitrification "
                    f"demand of {net_alk_demand:.0f} mg/L, a dose of {dose:.0f} mg/L is indicated. "
                    "We recommend measuring alkalinity directly — it takes 5 minutes on-site "
                    "and will move this estimate into the Medium-High confidence range."
                ),
                assumptions,
            )

        # ── Path D: permit limits only, no quality data ────────────────────────
        if any(v is not None for v in [inp.target_nh3_mgl, inp.target_no3_mgl, inp.target_tn_mgl]):
            assumptions.append(
                f"Incoming ammonia assumed {ASSUMED_INFLUENT_NH3:.0f} mg/L (typical municipal)"
            )
            assumptions.append(
                f"Influent alkalinity assumed {ASSUMED_INFLUENT_ALK:.0f} mg/L (typical municipal)"
            )
            dose = dose_from_balance(ASSUMED_INFLUENT_ALK)
            return (
                dose,
                Confidence.LOW,
                "Permit-limit estimate using assumed influent quality",
                (
                    f"Using the plant's effluent limits and typical municipal influent values, a starting "
                    f"dose of {dose:.0f} mg/L is estimated. This could vary significantly depending "
                    f"on actual water quality. Entering the plant's measured alkalinity is the single "
                    "most impactful step to improve this recommendation."
                ),
                assumptions,
            )

        # ── Path E: SVI only ──────────────────────────────────────────────────
        if inp.current_svi_ml_g is not None or inp.target_svi_reduction_pct is not None:
            svi = inp.current_svi_ml_g
            if svi is not None:
                dose = 20.0 if svi < 100 else \
                       40.0 if svi < 150 else \
                       65.0 if svi < 200 else \
                       85.0 if svi < 300 else 110.0
            else:
                dose = 60.0
            assumptions.append("Dose estimated from empirical SVI-response data")
            assumptions.append("Alkalinity balance not assessed — enter water quality readings for a full recommendation")
            return (
                dose,
                Confidence.LOW,
                "Empirical SVI-based estimate",
                (
                    f"Based on {'a current SVI of ' + str(int(svi)) + ' mL/g' if svi else 'a target SVI reduction'}, "
                    f"an empirical dose of {dose:.0f} mg/L is estimated from published settling "
                    "improvement data. This does not account for alkalinity balance. "
                    "Please provide alkalinity or pH readings for a more complete recommendation."
                ),
                assumptions,
            )

        # ── Path F: minimal data ──────────────────────────────────────────────
        assumptions.append(f"Incoming ammonia assumed {ASSUMED_INFLUENT_NH3:.0f} mg/L")
        assumptions.append(f"Influent alkalinity assumed {ASSUMED_INFLUENT_ALK:.0f} mg/L")
        assumptions.append("Conservative preliminary estimate — enter any water quality data to improve")
        return (
            50.0,
            Confidence.PRELIMINARY,
            "Conservative preliminary estimate — no water quality data provided",
            (
                "No water quality data has been entered yet. A conservative starting dose of "
                "50 mg/L is shown based on typical municipal wastewater conditions. "
                "Enter your measured alkalinity, pH, or ammonia in the sidebar to generate "
                "a site-specific recommendation."
            ),
            assumptions,
        )

    # ── Data completeness score ───────────────────────────────────────────────

    def _data_score(self) -> int:
        """0–100 score reflecting how well-determined the recommendation is."""
        weights = {
            "influent_alkalinity_mgl":  30,
            "influent_nh3_mgl":         25,
            "target_nh3_mgl":           20,
            "influent_ph":              10,
            "target_no3_mgl":            8,
            "target_tn_mgl":             5,
            "current_svi_ml_g":          2,
        }
        return min(100, sum(
            w for attr, w in weights.items()
            if getattr(self.inp, attr) is not None
        ))

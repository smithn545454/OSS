"""Calibration report generator.

Per Section 20 of OSS_Complete_Requirements.md.

Generates weekly calibration reports with:
- Summary statistics
- Gate effectiveness analysis
- Threshold suggestions
- Score band calibration
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from app.calibration.analyzer import GateAnalyzer
from app.calibration.models import (
    CalibrationReport,
    GateAnalysis,
    ScoreBandAnalysis,
    ThresholdSuggestion,
)
from app.calibration.simulator import ThresholdSimulator
from app.core.schemas import (
    Decision,
    Evaluation,
    GateResult,
    PaperPosition,
    Policy,
    PositionStatus,
)
from app.db.tables import (
    EvaluationTable,
    GateResultTable,
    PaperPositionTable,
    PolicyTable,
)

logger = logging.getLogger(__name__)


# Score bands for analysis per Section 20.2
SCORE_BANDS = [
    ("60-65", 60, 65),
    ("65-75", 65, 75),
    ("75-85", 75, 85),
    ("85+", 85, 100),
]


class CalibrationReporter:
    """Generates weekly calibration reports.
    
    Per Section 20.3 - Report Format.
    """
    
    def __init__(self):
        self._positions: list[PaperPosition] = []
        self._evaluations: list[dict] = []
        self._gate_results: dict[str, list[GateResult]] = {}
        self._policy: Optional[Policy] = None
    
    async def generate_report(
        self,
        week_start: Optional[str] = None,
        week_end: Optional[str] = None,
    ) -> CalibrationReport:
        """Generate a calibration report for the specified week.
        
        Args:
            week_start: Start date (ISO format). Defaults to 7 days ago.
            week_end: End date (ISO format). Defaults to today.
            
        Returns:
            CalibrationReport with full analysis
        """
        # Default to last 7 days
        if not week_end:
            week_end = datetime.utcnow().date().isoformat()
        if not week_start:
            start_date = datetime.fromisoformat(week_end) - timedelta(days=7)
            week_start = start_date.date().isoformat()
        
        # Load data
        await self._load_data()
        
        # Filter positions to the time window
        closed_positions = [
            p for p in self._positions
            if p.status == PositionStatus.CLOSED
            and p.exit_date
            and week_start <= p.exit_date <= week_end
        ]
        
        # Calculate summary stats
        win_rate, avg_return = self._calculate_summary_stats(closed_positions)
        
        # Create base report
        report = CalibrationReport.create(
            week_start=week_start,
            week_end=week_end,
            positions_closed=len(closed_positions),
            win_rate=win_rate,
            avg_return=avg_return,
        )
        
        # Analyze gates
        report.gate_analyses = await self._analyze_gates()
        
        # Generate suggestions
        report.suggestions = await self._generate_suggestions(report.gate_analyses)
        
        # Analyze score bands
        report.score_band_analysis = self._analyze_score_bands(closed_positions)
        
        return report
    
    async def _load_data(self) -> None:
        """Load data needed for analysis."""
        # Load active policy
        self._policy = await PolicyTable.get_active()
        
        # Load all positions
        self._positions = await PaperPositionTable.list_all(limit=1000)
        
        # Load evaluations by verdict
        approve_evals = await EvaluationTable.list_by_verdict("APPROVE", limit=500)
        watch_evals = await EvaluationTable.list_by_verdict("WATCH", limit=500)
        reject_evals = await EvaluationTable.list_by_verdict("REJECT", limit=500)
        self._evaluations = approve_evals + watch_evals + reject_evals
        
        # Load gate results for evaluations
        for eval_dict in self._evaluations:
            eval_id = eval_dict.get("evaluation_id")
            if eval_id:
                gates = await GateResultTable.list_by_evaluation(eval_id)
                self._gate_results[eval_id] = gates
    
    def _calculate_summary_stats(
        self,
        positions: list[PaperPosition],
    ) -> tuple[float, float]:
        """Calculate summary statistics.
        
        Args:
            positions: List of closed positions
            
        Returns:
            Tuple of (win_rate, avg_return)
        """
        if not positions:
            return 0.0, 0.0
        
        winners = [p for p in positions if p.current_pnl_pct > 0]
        win_rate = len(winners) / len(positions) * 100
        
        total_return = sum(p.current_pnl_pct for p in positions)
        avg_return = total_return / len(positions)
        
        return win_rate, avg_return
    
    async def _analyze_gates(self) -> list[GateAnalysis]:
        """Analyze gate effectiveness.
        
        Returns:
            List of gate analyses
        """
        analyzer = GateAnalyzer()
        
        # Add gate results
        for eval_id, gates in self._gate_results.items():
            analyzer.add_gate_results(gates, eval_id)
        
        # Add shadow positions for false negative analysis
        # Shadow positions are tracked REJECTs
        shadow_positions = [
            p for p in self._positions
            if str(p.verdict_at_entry.value) == "REJECT"
        ]
        for pos in shadow_positions:
            analyzer.add_shadow_position(pos)
        
        return analyzer.analyze_all_gates()
    
    async def _generate_suggestions(
        self,
        gate_analyses: list[GateAnalysis],
    ) -> list[ThresholdSuggestion]:
        """Generate threshold suggestions.
        
        Args:
            gate_analyses: Gate analysis results
            
        Returns:
            List of threshold suggestions
        """
        if not self._policy:
            return []
        
        # Convert evaluation dicts to Evaluation objects
        evaluations = []
        gate_results_list = []
        
        for eval_dict in self._evaluations:
            eval_id = eval_dict.get("evaluation_id")
            if eval_id and eval_id in self._gate_results:
                try:
                    # Remove nested decision for Evaluation construction
                    eval_copy = {k: v for k, v in eval_dict.items() if k != "decision"}
                    evaluations.append(Evaluation(**eval_copy))
                    gate_results_list.append(self._gate_results[eval_id])
                except Exception as e:
                    logger.warning(f"Failed to parse evaluation {eval_id}: {e}")
                    continue
        
        if not evaluations:
            return []
        
        simulator = ThresholdSimulator(
            policy=self._policy,
            evaluations=evaluations,
            gate_results=gate_results_list,
        )
        
        return simulator.generate_suggestions(gate_analyses)
    
    def _analyze_score_bands(
        self,
        positions: list[PaperPosition],
    ) -> list[ScoreBandAnalysis]:
        """Analyze performance by score band.
        
        Args:
            positions: List of closed positions
            
        Returns:
            List of score band analyses
        """
        analyses = []
        
        # Get decisions for positions
        position_scores = {}
        for pos in positions:
            eval_dict = next(
                (e for e in self._evaluations if e.get("evaluation_id") == pos.evaluation_id),
                None
            )
            if eval_dict and eval_dict.get("decision"):
                decision = eval_dict["decision"]
                score = decision.get("final_score", 0)
                position_scores[pos.position_id] = score
        
        for band_name, min_score, max_score in SCORE_BANDS:
            # Filter positions in this band
            band_positions = [
                p for p in positions
                if p.position_id in position_scores
                and min_score <= position_scores[p.position_id] < max_score
            ]
            
            if not band_positions:
                analyses.append(ScoreBandAnalysis(
                    band=band_name,
                    min_score=min_score,
                    max_score=max_score,
                    count=0,
                    win_rate=0,
                    avg_return=0,
                ))
                continue
            
            winners = [p for p in band_positions if p.current_pnl_pct > 0]
            win_rate = len(winners) / len(band_positions) * 100
            avg_return = sum(p.current_pnl_pct for p in band_positions) / len(band_positions)
            
            analyses.append(ScoreBandAnalysis(
                band=band_name,
                min_score=min_score,
                max_score=max_score,
                count=len(band_positions),
                win_rate=win_rate,
                avg_return=avg_return,
            ))
        
        return analyses


async def run_weekly_calibration() -> CalibrationReport:
    """Convenience function to run weekly calibration.
    
    Returns:
        CalibrationReport for the last 7 days
    """
    reporter = CalibrationReporter()
    return await reporter.generate_report()

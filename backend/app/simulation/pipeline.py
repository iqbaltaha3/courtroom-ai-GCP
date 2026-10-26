"""The simulation stages in their required execution order."""

from app.simulation.advocacy import (
    stage_defense_r1,
    stage_defense_r2,
    stage_defense_research,
    stage_prosecution_research,
    stage_prosecutor_r1,
    stage_prosecutor_r2,
)
from app.simulation.case_manager import stage_case_manager
from app.simulation.consultants import stage_defense_consultant, stage_prosecution_consultant
from app.simulation.extraction import stage_clarify_extract
from app.simulation.judgment import stage_judge

# Each tuple supplies the display name, stored report key, and stage function.
PIPELINE_STEPS = [
    ("Clarify + Extract", "clarify_extract", stage_clarify_extract),
    ("Case Manager", "case_manager", stage_case_manager),
    ("Prosecution Consultant", "prosecution_consultant", stage_prosecution_consultant),
    ("Defense Consultant", "defense_consultant", stage_defense_consultant),
    ("Prosecution Research", "prosecution_research", stage_prosecution_research),
    ("Defense Research", "defense_research", stage_defense_research),
    ("Prosecutor Round 1", "pros_r1", stage_prosecutor_r1),
    ("Defense Round 1", "def_r1", stage_defense_r1),
    ("Prosecutor Round 2", "pros_r2", stage_prosecutor_r2),
    ("Defense Round 2", "def_r2", stage_defense_r2),
    ("Judge", "judge", stage_judge),
]

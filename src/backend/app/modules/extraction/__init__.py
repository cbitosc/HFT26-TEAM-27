"""Module 2 — Evidence extraction & normalisation (witness .txt, sensor .csv).

Owner builds: Featherless LLM extraction for witness statements, Pandas
normalisation for sensor CSVs. Both must emit the common Observation schema.
"""
from app.schemas import Evidence, ObservationSet


def extract_witness(evidence: Evidence) -> ObservationSet:
    """Vehicles, actions, relative ordering, approximate times, uncertainty."""
    raise NotImplementedError("witness extraction not built yet")


def extract_sensor(evidence: Evidence) -> ObservationSet:
    """Parse timestamps, normalise speed units, flag missing records."""
    raise NotImplementedError("sensor extraction not built yet")

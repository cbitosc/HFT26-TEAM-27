"""Module 2 — Evidence extraction & normalisation (witness .txt, sensor .csv).

Featherless LLM extraction for witness statements (witness.py), Pandas
normalisation for sensor CSVs (sensor.py). Both emit the common Observation schema.
"""
from app.modules.extraction.sensor import extract_sensor
from app.modules.extraction.witness import extract_witness

__all__ = ["extract_sensor", "extract_witness"]

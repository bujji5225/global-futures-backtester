"""NYMEX WTI contract conventions and illustrative margin assumptions."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ContractSpec:
    code: str
    name: str
    multiplier: int
    illustrative_margin: int
    settlement: str
    tick: float = 0.01

    @property
    def tick_value(self):
        return self.tick * self.multiplier


SPECS = {
    "CL": ContractSpec("CL", "WTI Crude Oil", 1_000, 6_000, "Physical"),
    "MCL": ContractSpec("MCL", "Micro WTI Crude Oil", 100, 600, "Financial"),
}

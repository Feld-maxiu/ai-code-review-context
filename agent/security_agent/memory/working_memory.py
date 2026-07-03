from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from ..models import CodeDiff, ControlFlowGraph, VulnerabilityReport


@dataclass
class WorkingMemoryEntry:
    key: str
    value: Any
    ttl: int = -1
    access_count: int = 0


class WorkingMemory:
    def __init__(self, max_size: int = 100):
        self._store: Dict[str, WorkingMemoryEntry] = {}
        self.max_size = max_size

    def store_diff(self, diffs: List[CodeDiff]):
        self.put("current_diffs", diffs)

    def store_cfg(self, cfgs: List[ControlFlowGraph]):
        self.put("current_cfgs", cfgs)

    def store_intermediate_result(self, key: str, value: Any):
        self.put(f"intermediate_{key}", value)

    def get_diffs(self) -> List[CodeDiff]:
        return self.get("current_diffs", [])

    def get_cfgs(self) -> List[ControlFlowGraph]:
        return self.get("current_cfgs", [])

    def get_intermediate_result(self, key: str) -> Any:
        return self.get(f"intermediate_{key}")

    def store_detector_results(self, results: List[VulnerabilityReport]):
        self.put("detector_results", results)

    def get_detector_results(self) -> List[VulnerabilityReport]:
        return self.get("detector_results", [])

    def store_verifier_results(self, results: List[VulnerabilityReport]):
        self.put("verifier_results", results)

    def get_verifier_results(self) -> List[VulnerabilityReport]:
        return self.get("verifier_results", [])

    def store_fuzzer_results(self, results: List[VulnerabilityReport]):
        self.put("fuzzer_results", results)

    def get_fuzzer_results(self) -> List[VulnerabilityReport]:
        return self.get("fuzzer_results", [])

    def put(self, key: str, value: Any, ttl: int = -1):
        if len(self._store) >= self.max_size and key not in self._store:
            oldest_key = min(
                self._store.keys(),
                key=lambda k: self._store[k].access_count,
                default=None
            )
            if oldest_key and oldest_key not in [
                "current_diffs", "current_cfgs",
                "detector_results", "verifier_results", "fuzzer_results"
            ]:
                del self._store[oldest_key]

        self._store[key] = WorkingMemoryEntry(
            key=key,
            value=value,
            ttl=ttl,
            access_count=0
        )

    def get(self, key: str, default: Any = None) -> Any:
        entry = self._store.get(key)
        if entry is None:
            return default
        entry.access_count += 1
        return entry.value

    def clear(self):
        self._store.clear()

    def clear_intermediate(self):
        keys_to_remove = [
            k for k in self._store
            if k not in [
                "current_diffs", "current_cfgs",
                "detector_results", "verifier_results", "fuzzer_results"
            ]
        ]
        for k in keys_to_remove:
            del self._store[k]

    def snapshot(self) -> Dict[str, Any]:
        return {k: v.value for k, v in self._store.items()}
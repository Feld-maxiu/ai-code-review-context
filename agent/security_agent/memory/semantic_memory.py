import json
from typing import Any, Dict, List, Optional
from ..utils.llm_client import LLMClient


class SemanticMemory:
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client
        self._sast_rules: List[Dict[str, Any]] = []
        self._cwe_entries: List[Dict[str, Any]] = []
        self._rule_embeddings: Dict[int, List[float]] = {}
        self._cwe_hierarchy: Dict[str, List[str]] = {}

    def load_sast_rules(self, rules: List[Dict[str, Any]]):
        self._sast_rules = rules

    def load_cwe_tree(self, entries: List[Dict[str, Any]], hierarchy: Dict[str, List[str]]):
        self._cwe_entries = entries
        self._cwe_hierarchy = hierarchy

    def search_sast_rules(
        self,
        query: str,
        top_k: int = 5,
        cwe_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        candidates = self._sast_rules
        if cwe_filter:
            candidates = [r for r in candidates if r.get("cwe_id", "") == cwe_filter]

        if not candidates:
            return []

        if self.llm_client and len(candidates) > top_k:
            candidate_texts = [
                f"CWE:{r.get('cwe_id', 'N/A')} | {r.get('name', '')} | {r.get('description', '')[:200]}"
                for r in candidates
            ]
            try:
                indices = self.llm_client.semantic_search(query, candidate_texts, top_k)
                if indices:
                    return [candidates[i] for i in indices if i < len(candidates)]
            except Exception:
                pass

        scored = []
        query_lower = query.lower()
        for rule in candidates:
            score = 0
            rule_text = json.dumps(rule, ensure_ascii=False).lower()
            keywords = set(query_lower.split())
            for kw in keywords:
                if kw in rule_text:
                    score += 1
            if rule.get("cwe_id", "").lower() in query_lower:
                score += 3
            scored.append((score, rule))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:top_k]]

    def get_cwe_entry(self, cwe_id: str) -> Optional[Dict[str, Any]]:
        for entry in self._cwe_entries:
            if entry.get("cwe_id", "") == cwe_id:
                return entry
        return None

    def get_cwe_children(self, cwe_id: str) -> List[Dict[str, Any]]:
        children_ids = self._cwe_hierarchy.get(cwe_id, [])
        return [e for e in self._cwe_entries if e.get("cwe_id", "") in children_ids]

    def get_cwe_ancestors(self, cwe_id: str) -> List[Dict[str, Any]]:
        ancestors = []
        parent_map = {}
        for pid, children in self._cwe_hierarchy.items():
            for cid in children:
                parent_map[cid] = pid

        current = cwe_id
        while current in parent_map:
            current = parent_map[current]
            entry = self.get_cwe_entry(current)
            if entry:
                ancestors.append(entry)

        return ancestors

    def get_cwe_full_path(self, cwe_id: str) -> List[Dict[str, Any]]:
        ancestors = list(reversed(self.get_cwe_ancestors(cwe_id)))
        entry = self.get_cwe_entry(cwe_id)
        if entry:
            ancestors.append(entry)
        return ancestors

    def list_all_rules(self) -> List[Dict[str, Any]]:
        return self._sast_rules

    def list_all_cwe(self) -> List[Dict[str, Any]]:
        return self._cwe_entries

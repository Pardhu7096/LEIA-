"""Reproducible LEIA customer-search evaluation (synthetic inventory, MOCKED Groq).

Usage (writes docs/eval/search_eval_results.{json,md}):
    python -X utf8 -I backend/tests/eval/runner.py
pytest runs the same cases via tests/test_search_eval.py.

Safety: backend/.env is never loaded and no database connection is opened.
Results are "mock-tested": Groq replies come from each case's ``mock_ai``.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
sys.path.insert(0, str(BACKEND))

import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *args, **kwargs: False  # never read backend/.env (staging credentials)
for name in ("DATABASE_URL", "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"):
    os.environ.pop(name, None)
os.environ.setdefault("JWT_SECRET", "isolated-eval-secret-never-use-in-production-123456")
os.environ.setdefault("GROQ_API_KEY", "eval-not-a-real-key")

FORBIDDEN = {"J", "K", "L"}  # draft, rejected, sold: must never reach a customer


def load():
    inventory = json.loads((HERE / "inventory.json").read_text(encoding="utf-8"))
    cases = json.loads((HERE / "search_eval_cases.json").read_text(encoding="utf-8"))["cases"]
    return inventory, cases


def customer_facts(p):
    """The migration 014 fields a customer payload carries (RERA number only when verified)."""
    verified = p["rera_source"] == "document_checked" and bool(p["rera_number"])
    return {"builder": p["builder"], "project_name": p["project_name"],
            "construction_status": p["construction_status"], "possession_date": p["possession_date"],
            "floor_number": p["floor_number"], "total_floors": p["total_floors"], "lift": p["lift"],
            "gated_community": p["gated_community"], "security": p["security"], "amenities": p["amenities"],
            "project_source": p["project_source"], "rera_verified": verified,
            "rera_number": p["rera_number"] if verified else None}


def make_filter(inventory, search_parser):
    def norm(value):
        return re.sub(r"[^a-z0-9ఀ-౿]", "", (value or "").lower())

    def filter_properties(filters):
        f = filters.model_dump()
        keys = set(search_parser.location_keys(f["location"]) or [])
        rows = []
        for p in inventory:
            if p["status"] != "approved" or p["availability"] != "available":
                continue
            if f["bedrooms"] is not None and p["bedrooms"] != f["bedrooms"]:
                continue
            if f["min_price"] is not None and p["price"] < f["min_price"]:
                continue
            if f["max_price"] is not None and p["price"] > f["max_price"]:
                continue
            if f["location"] and not ({norm(p["locality"]), norm(p["city"])} & keys
                                      or f["location"].lower() in {p["locality"].lower(), p["city"].lower()}):
                continue
            if f["property_type"] and p["property_type"] != f["property_type"]:
                continue
            if f["facing"] and p["facing"] != f["facing"]:
                continue
            if f["min_area_sqft"] is not None and p["area_sqft"] < f["min_area_sqft"]:
                continue
            if f["max_area_sqft"] is not None and p["area_sqft"] > f["max_area_sqft"]:
                continue
            if f["parking"] and not ((p["car_parking_spaces"] or 0) > 0 or p["parking_type"] in ("open", "covered", "both")):
                continue
            if f["power_backup"] and p["power_backup"] not in ("common_areas", "partial_home", "full_home"):
                continue
            # Migration 014 project facts: same rules as the SQL predicates (unknown never matches).
            if f["builder"] and not norm(p["builder"]).startswith(norm(f["builder"])):
                continue
            if f["construction_status"] and p["construction_status"] != f["construction_status"]:
                continue
            if f["possession_by"] and not (p["construction_status"] == "ready_to_move" or (
                    p["construction_status"] == "under_construction" and p["possession_date"]
                    and p["possession_date"] <= f["possession_by"])):
                continue
            if f["lift"] and p["lift"] != "yes":
                continue
            if f["gated"] and p["gated_community"] != "yes":
                continue
            if f["security"] and p["security"] not in ("guard", "cctv", "guard_and_cctv"):
                continue
            if f["rera_verified"] and not (p["rera_source"] == "document_checked" and p["rera_number"]):
                continue
            if f["amenities"] and not set(f["amenities"]) <= set(p["amenities"]):
                continue
            rows.append({"id": p["key"], "title": p["title"], "price": p["price"], "bedrooms": p["bedrooms"],
                         "location": f"{p['locality']}, {p['city']}", "area_sqft": p["area_sqft"],
                         "property_type": p["property_type"], "facing": p["facing"], **customer_facts(p)})
        order = {"price_asc": ("price", False), "price_desc": ("price", True), "area_desc": ("area_sqft", True)}
        if f["sort"] in order:
            key, reverse = order[f["sort"]]
            rows.sort(key=lambda r: r[key], reverse=reverse)
        return rows[:30]

    return filter_properties


class FakeCursor:
    """Answers the two queries compare_reply issues, from the synthetic inventory."""

    def __init__(self, inventory, context_ids):
        self.inventory = {p["key"]: p for p in inventory}
        self.context = [{"id": key} for key in context_ids]
        self.result = None

    def execute(self, sql, params=()):
        if "FROM chat_messages" in sql:
            self.result = [{"properties": self.context}] if self.context else []
        elif "FROM public.properties" in sql:
            rows = []
            for wanted in params[0]:
                for key, p in self.inventory.items():
                    if FakeCursor.uuid_for(key) == wanted and p["status"] == "approved" and p["availability"] == "available":
                        rows.append({"id": wanted, "title": p["title"], "price": p["price"], "bedrooms": p["bedrooms"],
                                     "bathrooms": None, "area_sqft": p["area_sqft"], "facing": p["facing"],
                                     "location": f"{p['locality']}, {p['city']}",
                                     "car_parking_spaces": p["car_parking_spaces"], **customer_facts(p)})
            self.result = rows

    def fetchone(self):
        return self.result[0] if self.result else None

    def fetchall(self):
        return self.result or []

    @staticmethod
    def uuid_for(key):
        from uuid import UUID
        return UUID(int=ord(key))


def run(main=None, ai_guard=None):
    if main is None:
        import ai_guard  # noqa: F811
        import main
    import search_parser
    from uuid import UUID

    inventory, cases = load()
    filter_fn = make_filter(inventory, search_parser)
    original = (main.filter_properties, main.count_relaxed_matches, main.client.chat.completions.create,
                ai_guard.guarded_completion)
    rows = []
    try:
        main.filter_properties = filter_fn
        main.count_relaxed_matches = lambda variants: [
            len(filter_fn(main.SearchFilters.model_validate(v))) for v in variants]
        for case in cases:
            calls = []

            def create(**kwargs):
                calls.append(kwargs)
                if "mock_ai" not in case:
                    raise AssertionError("unexpected provider call")
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                    content=json.dumps(case["mock_ai"])))], usage=None)

            def guarded(user_id, create_fn, messages, usage, sleep=None):
                usage.attempts += 1
                return create_fn(messages=messages)

            main.client.chat.completions.create = create
            ai_guard.guarded_completion = guarded
            ai_guard.extraction_cache.clear()
            prior = main.SearchFilters.model_validate(case.get("prior_filters", {}))
            event = main.SearchEvent("eval")
            error = None
            try:
                if main.COMPARE_QUESTION.search(case["question"]):
                    cursor = FakeCursor(inventory, case.get("context_ids", []))
                    cursor.context = [{"id": str(FakeCursor.uuid_for(k))} for k in case.get("context_ids", [])]
                    result = {"reply": main.compare_reply(cursor, UUID(int=1), case["question"]),
                              "properties": [], "filters": prior.model_dump()}
                    intent = "compare"
                else:
                    result = main.answer_customer_search(case["question"], prior, {"id": "eval-user"}, event)
                    route = event.data["route"]
                    intent = {"ai": "ai", "fallback": "ai", "blocked": "blocked", "clarify": "clarify"}.get(route)
                    if intent is None:
                        intent = ("reset" if result["reply"] == main.RESET_REPLY else
                                  "conversation" if result["reply"] == main.GREETING_REPLY else "search")
            except Exception as exc:  # noqa: BLE001
                result, intent, error = {"reply": "", "properties": [], "filters": {}}, "error", repr(exc)
            ids = [p["id"] for p in result["properties"]]
            failures = []
            if error:
                failures.append(error)
            if intent != case["expected_intent"]:
                failures.append(f"intent {intent} != {case['expected_intent']}")
            if bool(calls) != case["groq"]:
                failures.append(f"groq called={bool(calls)} expected={case['groq']}")
            for key, value in case.get("expected_filters", {}).items():
                if result["filters"].get(key) != value:
                    failures.append(f"filter {key}={result['filters'].get(key)!r} expected {value!r}")
            if "expected_ids" in case and sorted(ids) != sorted(case["expected_ids"]):
                failures.append(f"ids {sorted(ids)} expected {sorted(case['expected_ids'])}")
            if case.get("expected_order_first") and (not ids or ids[0] != case["expected_order_first"]):
                failures.append(f"first {ids[:1]} expected {case['expected_order_first']}")
            if FORBIDDEN & set(ids):
                failures.append(f"unpublished/unavailable listing leaked: {sorted(FORBIDDEN & set(ids))}")
            for text in case.get("reply_contains", []):
                if text.lower() not in result["reply"].lower():
                    failures.append(f"reply missing {text!r}")
            for text in case.get("not_contains", []):
                if text.lower() in result["reply"].lower():
                    failures.append(f"reply must not contain {text!r}")
            rows.append({
                "id": case["id"], "category": case["category"], "question": case["question"],
                "expected_intent": case["expected_intent"], "expected_filters": case.get("expected_filters", {}),
                "expected_behavior": case["behavior"], "groq_expected": case["groq"],
                "actual_intent": intent, "groq_called": bool(calls), "actual_filters": result["filters"],
                "actual_ids": ids, "actual_reply": result["reply"], "mocked_ai": "mock_ai" in case,
                "pass": not failures, "failure_reason": "; ".join(failures),
            })
    finally:
        (main.filter_properties, main.count_relaxed_matches, main.client.chat.completions.create,
         ai_guard.guarded_completion) = original
    return rows


def write_report(rows, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "search_eval_results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    passed = sum(r["pass"] for r in rows)
    lines = [
        "# LEIA customer-search evaluation results",
        "",
        f"**{passed}/{len(rows)} passed.** Groq calls are MOCKED (no real provider validation). "
        "Inventory is synthetic (`backend/tests/eval/inventory.json`).",
        f"Groq called in {sum(r['groq_called'] for r in rows)} of {len(rows)} questions.",
        "",
        "| ID | Category | Question | Expected intent | Groq expected | Groq called | Actual intent | Result | Failure reason |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        q = r["question"].replace("|", "\\|")
        lines.append(f"| {r['id']} | {r['category']} | {q} | {r['expected_intent']} | {r['groq_expected']} | "
                     f"{r['groq_called']} | {r['actual_intent']} | {'PASS' if r['pass'] else 'FAIL'} | "
                     f"{r['failure_reason'].replace('|', '/')} |")
    lines += ["", "Full expected filters, actual filters, listings and replies: `search_eval_results.json`."]
    (out_dir / "search_eval_results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    results = run()
    write_report(results, BACKEND.parent / "docs" / "eval")
    failed = [r for r in results if not r["pass"]]
    print(f"{len(results) - len(failed)}/{len(results)} passed")
    for r in failed:
        print(f"FAIL {r['id']}: {r['failure_reason']}")
    sys.exit(1 if failed else 0)

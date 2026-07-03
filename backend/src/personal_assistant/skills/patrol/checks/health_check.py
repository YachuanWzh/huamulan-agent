import json
import operator
import sys


OPS = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
}


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    findings = []
    for check in payload.get("checks", []):
        metric = check.get("metric")
        op = check.get("operator", ">")
        value = float(check.get("value", 0))
        threshold = float(check.get("threshold", 0))
        failed = OPS.get(op, operator.gt)(value, threshold)
        findings.append(
            {
                "metric": metric,
                "status": "fail" if failed else "pass",
                "value": value,
                "threshold": threshold,
                "reason": f"{metric} {op} {threshold} evaluated with value {value}",
            }
        )
    print(json.dumps({"findings": findings}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

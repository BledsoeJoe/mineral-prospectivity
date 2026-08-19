"""Assemble the experiment report dict shared by the CLI and the ETL pipeline."""

from __future__ import annotations

from mpm.config import Config


def build_report(result: dict[str, object], cfg: Config, cube_summary: dict[str, float]) -> dict:
    """Everything a reader (human or frontend) needs about one experiment."""
    report: dict[str, object] = {
        "config": cfg.to_dict(),
        "cube": cube_summary,
        "bias_audit": result["bias_audit"],
        "optimism": result["optimism"],
        "geology_features": result["geology_features"],
        "effort_features": result["effort_features"],
    }
    if "discovery_audit" in result:
        report["discovery_audit"] = result["discovery_audit"]

    for name, run in result["runs"].items():  # type: ignore[union-attr]
        report[name] = {
            "pooled": run.pooled,
            "summary": {k: list(v) for k, v in run.summary.items()},
            "c_hat": run.c_hat,
            "top_features": dict(sorted(run.importance.items(), key=lambda kv: -kv[1])[:12]),
        }
    return report

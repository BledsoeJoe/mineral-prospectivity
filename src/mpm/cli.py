"""Command line interface.

    python -m mpm synth   --out data/cube.parquet
    python -m mpm run     --cube data/cube.parquet --out results/
    python -m mpm targets --scores results/geology_scores.parquet --top 0.02

    python -m mpm etl run      --profile us-cu --data-root data
    python -m mpm etl status   --data-root data
    python -m mpm etl profiles
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from mpm.config import Config
from mpm.ingest import clean_cube, summarize
from mpm.pipeline import run_experiment
from mpm.plots import capture_curves, feature_importance, prospectivity_map
from mpm.report import build_report
from mpm.synth import make_synthetic_cube

log = logging.getLogger("mpm.cli")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_synth(args: argparse.Namespace) -> None:
    df = make_synthetic_cube(resolution=args.resolution, n_true_deposits=args.n_deposits, seed=args.seed)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.out, index=False)
    log.info("wrote %s", args.out)
    log.info("cube: %s", summarize(df))
    log.info("hidden truth: %d true deposits, %d discovered",
                 int(df.truth_deposit.sum()), int(df.deposit.sum()))


def cmd_run(args: argparse.Namespace) -> None:
    cfg = Config.from_file(args.config) if args.config else Config()
    df = pd.read_parquet(args.cube)
    clean, features = clean_cube(
        df,
        cell_col=cfg.cell_col,
        label_col=cfg.label_col,
        drop_cols=cfg.drop_cols,
        max_missing_frac=cfg.max_missing_frac,
    )
    log.info("cleaned cube: %s", summarize(clean, cfg.label_col))
    log.info("%d covariates after cleaning", len(features))

    result = run_experiment(clean, features, cfg)
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    report = build_report(result, cfg, summarize(clean, cfg.label_col))
    for name, run in result["runs"].items():  # type: ignore[union-attr]
        run.scores.to_parquet(outdir / f"{name}_scores.parquet", index=False)

    if not args.no_figures:
        figdir = outdir / "figures"
        figdir.mkdir(exist_ok=True)
        geo = result["runs"]["geology"]  # type: ignore[index]
        prospectivity_map(geo.scores, figdir / "prospectivity_map.png",
                          "Geology-only model, spatial block CV")
        capture_curves(
            {n: r.scores for n, r in result["runs"].items()},  # type: ignore[union-attr]
            figdir / "capture_curves.png", cfg.label_col,
        )
        feature_importance(result["runs"]["combined"].importance,  # type: ignore[index]
                           figdir / "feature_importance.png")
        log.info("wrote figures to %s", figdir)

    (outdir / "report.json").write_text(json.dumps(report, indent=2, default=float))
    _print_report(report)
    log.info("wrote %s", outdir / "report.json")


def cmd_targets(args: argparse.Namespace) -> None:
    s = pd.read_parquet(args.scores)
    k = max(1, round(args.top * len(s)))
    cols = [c for c in ("h3", "lat", "lon", "score", "score_std", "deposit") if c in s.columns]
    print(s.nlargest(k, "score")[cols].to_string(index=False))


def _print_report(report: dict) -> None:
    def fmt(v):
        return f"{v:.4f}" if isinstance(v, float) else str(v)

    print("\n" + "=" * 68)
    print("HEADLINE  (geology covariates, spatial block CV, pooled out-of-fold)")
    print("=" * 68)
    pooled = report["geology"]["pooled"]
    spread = report["geology"]["summary"]
    for k in ("capture@5%", "capture@10%", "lift@5%", "pr_auc", "roc_auc", "capture_auc"):
        if k in pooled:
            sd = spread.get(k, [float("nan"), float("nan")])[1]
            print(f"  {k:<16} {pooled[k]:8.4f}   (per-fold sd {sd:.4f})")
    print(f"\n  base rate {pooled['base_rate']:.5f} -- 1 deposit per "
          f"{1 / max(pooled['base_rate'], 1e-12):,.0f} cells")
    print(f"  held-out positive recognition (Elkan-Noto c_hat): "
          f"{report['geology']['c_hat']:.3f}")

    print("\n" + "-" * 68)
    print("OPTIMISM FROM NAIVE VALIDATION")
    print("-" * 68)
    for k, v in report["optimism"].items():
        print(f"  {k:<26} {fmt(v)}")

    print("\n" + "-" * 68)
    print("EXPLORATION-BIAS AUDIT")
    print("-" * 68)
    for k, v in report["bias_audit"].items():
        print(f"  {k:<32} {fmt(v)}")

    if "discovery_audit" in report:
        print("\n" + "-" * 68)
        print("DISCOVERY AUDIT (synthetic ground truth)")
        print("-" * 68)
        for k, v in report["discovery_audit"].items():
            print(f"  {k:<26} {fmt(v)}")

    print("\n" + "-" * 68)
    print("TOP COVARIATES (geology model)")
    print("-" * 68)
    for k, v in list(report["geology"]["top_features"].items())[:8]:
        print(f"  {k:<26} {v:.3f}")
    print()


def cmd_etl_run(args: argparse.Namespace) -> None:
    from mpm.etl.flow import run_pipeline

    summary = run_pipeline(
        args.profile,
        Path(args.data_root),
        force=tuple(args.force or ()),
        refresh=args.refresh,
    )
    print(json.dumps(summary, indent=2))


def cmd_etl_status(args: argparse.Namespace) -> None:
    from mpm.etl.flow import pipeline_status

    print(json.dumps(pipeline_status(Path(args.data_root)), indent=2))


def cmd_etl_profiles(args: argparse.Namespace) -> None:
    from mpm.etl.profiles import PROFILES

    for p in PROFILES.values():
        print(f"{p.name:<10} [{p.kind}]  {p.description}")


def cmd_etl_backfill(args: argparse.Namespace) -> None:
    from mpm.etl.flow import backfill_catalog

    n = backfill_catalog(Path(args.data_root))
    print(f"registered {n} runs in the catalog")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="mpm", description="Mineral prospectivity experiment")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("synth", help="generate a synthetic datacube")
    s.add_argument("--out", default="data/cube.parquet")
    s.add_argument("--resolution", type=int, default=6)
    s.add_argument("--n-deposits", type=int, default=400)
    s.add_argument("--seed", type=int, default=17)
    s.set_defaults(func=cmd_synth)

    r = sub.add_parser("run", help="run the cross-validated experiment")
    r.add_argument("--cube", required=True)
    r.add_argument("--config", default=None)
    r.add_argument("--out", default="results")
    r.add_argument("--no-figures", action="store_true")
    r.set_defaults(func=cmd_run)

    t = sub.add_parser("targets", help="print the top-ranked cells")
    t.add_argument("--scores", required=True)
    t.add_argument("--top", type=float, default=0.02)
    t.set_defaults(func=cmd_targets)

    e = sub.add_parser("etl", help="structured ingest pipeline (acquire -> ... -> publish)")
    esub = e.add_subparsers(dest="etl_cmd", required=True)

    er = esub.add_parser("run", help="run a pipeline profile end to end")
    er.add_argument("--profile", default="synth")
    er.add_argument("--data-root", default="data")
    er.add_argument(
        "--force", nargs="*", default=None, metavar="STAGE",
        help="rebuild these stages even if fresh "
             "(labels geophys geochem extras cube train, or 'all')",
    )
    er.add_argument("--refresh", action="store_true", help="re-download raw sources")
    er.set_defaults(func=cmd_etl_run)

    es = esub.add_parser("status", help="stage freshness and latest runs")
    es.add_argument("--data-root", default="data")
    es.set_defaults(func=cmd_etl_status)

    eb = esub.add_parser("backfill-db", help="register published runs in the Postgres catalog")
    eb.add_argument("--data-root", default="data")
    eb.set_defaults(func=cmd_etl_backfill)

    ep = esub.add_parser("profiles", help="list available profiles")
    ep.set_defaults(func=cmd_etl_profiles)

    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    args.func(args)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Convert a Parquet file to CSV."""

import argparse
from pathlib import Path

import pandas as pd


def parquet_to_csv(input_path: Path, output_path: Path) -> None:
    """Read a Parquet file and write it as CSV."""
    df = pd.read_parquet(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    print(f"Converted {input_path} -> {output_path} ({len(df)} rows)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert a Parquet file to CSV.")
    parser.add_argument("input", type=Path, help="Path to the input .parquet file")
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        default=None,
        help="Path to the output .csv file. Defaults to same name with .csv extension.",
    )
    args = parser.parse_args()

    output_path = args.output if args.output is not None else args.input.with_suffix(".csv")
    parquet_to_csv(args.input, output_path)


if __name__ == "__main__":
    main()

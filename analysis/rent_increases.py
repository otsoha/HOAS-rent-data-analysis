"""Run this from root"""

import pandas as pd
import os
import logging
import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)
DATA_DIR = "analysis/data"


def read_datasets():
    """Reads all CSV files in the data directory and returns a dictionary of DataFrames."""
    datasets = {}
    for file in os.listdir(DATA_DIR):
        if file.endswith(".csv"):
            file_path = os.path.join(DATA_DIR, file)
            df = pd.read_csv(file_path)
            datasets[file.removesuffix(".csv")] = df
    return datasets


if __name__ == "__main__":
    datasets = read_datasets()
    for name, df in datasets.items():
        logger.info(f"Dataset: {name}, Shape: {df.shape}")

    hist_prices = pd.DataFrame()
    for name, df in datasets.items():
        # The timestamp is not recorded in the data, so we need to use the filename to get the date of the dataset
        logger.info(f"Date of dataset: {name}")
        date = pd.to_datetime(name, format="%Y-%m-%d")
        logger.info(f"Converted date: {date}")
        # each row in the dataset represents a property, we add the date as a column that contains the rent price at that date
        # original columns as: location,energy_class,condition,renovation_year,building_year,type,address,rooms,surface_area,count,rent,rating,Internet: Trinet,Remontoitu tai uusi,Hissi,Vesimaksu kulutuksen mukaan,Internet: DNA,Pesutupa,Kerhotila,Internet: Telia,Sauna

        for index, row in df.iterrows():
            hist_prices = pd.concat(
                [
                    hist_prices,
                    pd.DataFrame(
                        {
                            "date": [date],
                            "location": [row["location"]],
                            "address": [row["address"]],
                            "rent": [row["rent"]],
                            "type": [row["type"]],
                            "rooms": [row["rooms"]],
                            "surface_area": [row["surface_area"]],
                        }
                    ),
                ],
                ignore_index=True,
            )

    dates_sorted = sorted(hist_prices["date"].unique())
    date_labels = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in dates_sorted]

    # real time-scaled x positions: matplotlib's internal "days since epoch"
    # float representation, so gaps between violins are proportional to the
    # actual time elapsed rather than evenly spaced categories
    x_numeric = mdates.date2num([pd.Timestamp(d) for d in dates_sorted])

    def violin_width(x_pos, fallback_days=14):
        """Pick a violin width (in days) proportional to the tightest gap
        between neighboring points, so violins never overlap into each
        other even when some transitions are much closer together than
        others."""
        if len(x_pos) > 1:
            gaps = np.diff(np.sort(x_pos))
            return max(gaps.min() * 0.6, 1.0)
        return fallback_days

    def add_jitter(vals, width):
        """Small horizontal jitter so overlapping points fan out sideways,
        giving a scatter-style view of density instead of one solid blob."""
        if len(vals) == 0:
            return np.array([])
        return np.random.uniform(-width * 0.35, width * 0.35, size=len(vals))

    # ------------------------------------------------------------------
    # Plot 1: absolute rent distribution in each dataset, no type grouping.
    # Violin shows the density shape (skew, multi-modality); jittered
    # scatter underneath shows the actual raw points.
    # ------------------------------------------------------------------
    rent_by_date = [
        hist_prices.loc[hist_prices["date"] == d, "rent"].dropna().values
        for d in dates_sorted
    ]
    for label, vals in zip(date_labels, rent_by_date):
        logger.info(f"Rent distribution on {label}: n={len(vals)}")

    width1 = violin_width(x_numeric)

    fig1, ax1 = plt.subplots(figsize=(12, 6))

    non_empty = [(x, v) for x, v in zip(x_numeric, rent_by_date) if len(v) > 0]
    if non_empty:
        parts = ax1.violinplot(
            [v for _, v in non_empty],
            positions=[x for x, _ in non_empty],
            widths=width1,
            showmedians=True,
            showextrema=True,
            side="high",
        )
        for body in parts["bodies"]:
            body.set_alpha(0.35)

    ax1.xaxis_date()
    locator1 = mdates.AutoDateLocator()
    ax1.xaxis.set_major_locator(locator1)
    ax1.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator1))
    ax1.set_xlabel("Date")
    ax1.set_ylabel("Rent (EUR)")
    ax1.set_title("Rent Distribution by Dataset")
    fig1.autofmt_xdate()
    fig1.savefig("analysis/rent_distribution.png", dpi=150, bbox_inches="tight")
    plt.close(fig1)

    # ------------------------------------------------------------------
    # Plot 2: per-apartment rent increases between consecutive datasets,
    # no type grouping. Apartments are matched across snapshots by their
    # (location, address, type, rooms) key so each data point is one
    # unit's actual pct change, not an aggregate.
    # ------------------------------------------------------------------
    apt_key = ["location", "address", "type", "rooms"]

    wide_rent = hist_prices.pivot_table(
        index=apt_key, columns="date", values="rent", aggfunc="mean"
    )
    wide_rent = wide_rent.reindex(sorted(wide_rent.columns), axis=1)

    pct_changes = wide_rent.pct_change(axis=1) * 100

    # one violin per transition (date[i-1] -> date[i]), skip the first
    # column since pct_change has no prior value to compare against.
    # Each transition is positioned at the midpoint of its two dates, so
    # transitions that happened close together sit close together on the
    # x-axis, and a long gap between snapshots visibly stretches out.
    transition_dates = wide_rent.columns[1:]
    transition_labels = [
        f"{pd.Timestamp(wide_rent.columns[i]).strftime('%Y-%m-%d')} -> "
        f"{pd.Timestamp(wide_rent.columns[i + 1]).strftime('%Y-%m-%d')}"
        for i in range(len(wide_rent.columns) - 1)
    ]
    change_data = [pct_changes[col].dropna().values for col in transition_dates]
    for label, vals in zip(transition_labels, change_data):
        logger.info(f"Rent change {label}: n={len(vals)}")

    transition_x = mdates.date2num(
        [
            pd.Timestamp(wide_rent.columns[i])
            + (
                pd.Timestamp(wide_rent.columns[i + 1])
                - pd.Timestamp(wide_rent.columns[i])
            )
            / 2
            for i in range(len(wide_rent.columns) - 1)
        ]
    )

    width2 = violin_width(transition_x)

    fig2, ax2 = plt.subplots(figsize=(12, 6))

    non_empty2 = [(x, v) for x, v in zip(transition_x, change_data) if len(v) > 0]
    if non_empty2:
        parts2 = ax2.violinplot(
            [v for _, v in non_empty2],
            positions=[x for x, _ in non_empty2],
            widths=width2,
            showmedians=True,
            showextrema=True,
            side="high",
        )
        for body in parts2["bodies"]:
            body.set_alpha(0.35)
            body.set_facecolor("tab:orange")

    ax2.axhline(0, color="grey", linewidth=1, linestyle="--")
    ax2.xaxis_date()
    locator2 = mdates.AutoDateLocator()
    ax2.xaxis.set_major_locator(locator2)
    ax2.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator2))

    # add vertical lines to dataset scraping dates
    for d in dates_sorted:
        ax2.axvline(
            mdates.date2num(pd.Timestamp(d)),
            color="grey",
            linewidth=0.5,
            linestyle=":",
        )
    ax2.set_xlabel("Transition midpoint")
    ax2.set_ylabel("Per-apartment % Change in Rent")
    ax2.set_title("Distribution of Rent Increases Between Datasets")
    fig2.autofmt_xdate()
    fig2.savefig("analysis/rent_increases_dist.png", dpi=150, bbox_inches="tight")
    plt.close(fig2)

    # print outliers with apartment type, address for change data:
    wide_rent["pct_change"] = pct_changes.mean(axis=1)
    logger.info(wide_rent.head())
    # print top 10 abs changes
    wide_rent["abs_pct_change"] = wide_rent["pct_change"].abs()
    top_outliers = wide_rent.nlargest(10, "abs_pct_change").reset_index()
    print(
        f"Top outliers:\n{top_outliers[['location', 'address', 'type', 'rooms', 'pct_change']]}\n"
    )

    # print apartment types with price drops
    drops = wide_rent.reset_index()
    drops = drops[drops["pct_change"] < 0]

    print(f"Apartments with price drops (n={len(drops)}):")
    print(
        drops[["location", "address", "type", "rooms", "pct_change"]].sort_values(
            "pct_change"
        )
    )

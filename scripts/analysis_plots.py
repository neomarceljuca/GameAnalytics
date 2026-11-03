"""Static Matplotlib figures for the descriptive sample report."""

COLORS = {
    "shooter": "#167C9B",
    "simulation-tycoon": "#D57638",
    "deathrun-platformer": "#5C4EA4",
}
LABELS = {
    "shooter": "Shooter",
    "simulation-tycoon": "Simulation & Tycoon",
    "deathrun-platformer": "Deathrun & Platformer",
}


def render_figures(daily, summary, genre_daily, memberships, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import StrMethodFormatter

    output.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.labelcolor": "#26384A",
            "text.color": "#26384A",
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
        }
    )
    groups = memberships.groupby("island_code")["genre"].agg(list).to_dict()

    def color(code):
        return (
            COLORS.get(groups[code][0], "#687786")
            if len(groups[code]) == 1
            else "#687786"
        )

    handles = [
        Line2D(
            [0],
            [0],
            color=COLORS.get(g, "#687786"),
            marker="o",
            linestyle="",
            label=LABELS.get(g, g),
        )
        for g in sorted(memberships["genre"].unique())
    ]
    if any(len(g) > 1 for g in groups.values()):
        handles.append(
            Line2D(
                [0],
                [0],
                color="#687786",
                marker="o",
                linestyle="",
                label="Multiple genres",
            )
        )
    start, end = daily["day"].min().date(), daily["day"].max().date()
    subtitle = (
        f"{start} to {end} UTC | Fixed rank-selected sample; descriptive comparisons"
    )

    frame = summary.dropna(subset=["median_unique_players"]).sort_values(
        ["median_unique_players", "island_code"]
    )
    fig, ax = plt.subplots(figsize=(12, 9))
    if len(frame):
        bars = ax.barh(
            frame["island_code"],
            frame["median_unique_players"],
            color=[color(c) for c in frame["island_code"]],
        )
        ax.bar_label(
            bars,
            labels=[f"{n:,.0f}" for n in frame["median_unique_players"]],
            padding=5,
            fontsize=9,
        )
        ax.set_xlim(0, max(1, frame["median_unique_players"].max()) * 1.24)
    else:
        ax.text(
            0.5,
            0.5,
            "No islands have complete reach coverage",
            ha="center",
            transform=ax.transAxes,
        )
    ax.set_xlabel("Median daily unique players (not period unique audience)")
    ax.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    ax.grid(axis="x", alpha=0.16)
    ax.set_axisbelow(True)
    ax.set_title(
        "Daily reach varies across sampled islands",
        loc="left",
        fontsize=17,
        pad=20,
        fontweight="bold",
    )
    fig.text(0.05, 0.95, subtitle, fontsize=10)
    fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False)
    fig.subplots_adjust(left=0.19, right=0.94, top=0.88, bottom=0.12)
    fig.savefig(output / "reach.png", dpi=170)
    plt.close(fig)

    latest = daily.loc[daily["day"] == daily["day"].max()].dropna(
        subset=["unique_players", "average_minutes_per_player"]
    )
    fig, ax = plt.subplots(figsize=(11, 7))
    for _, row in latest.iterrows():
        ax.scatter(
            row["unique_players"],
            row["average_minutes_per_player"],
            c=color(row["island_code"]),
            s=75,
            alpha=0.85,
            edgecolor="white",
            linewidth=0.6,
        )
    if len(latest):
        labels = {
            latest["unique_players"].idxmax(),
            latest["average_minutes_per_player"].idxmax(),
        }
        for index in labels:
            row = latest.loc[index]
            ax.annotate(
                row["island_code"],
                (row["unique_players"], row["average_minutes_per_player"]),
                xytext=(-6, 12),
                textcoords="offset points",
                ha="right",
                fontsize=9,
            )
        if (latest["unique_players"] > 0).all():
            ax.set_xscale("log")
    else:
        ax.text(
            0.5,
            0.5,
            "No valid paired observations",
            ha="center",
            transform=ax.transAxes,
        )
    ax.set_ylim(bottom=0)
    ax.margins(x=0.15, y=0.18)
    ax.set_xlabel(
        "Daily unique players" + (" (log scale)" if ax.get_xscale() == "log" else "")
    )
    ax.set_ylabel("Published daily minutes per player")
    ax.grid(alpha=0.16)
    fig.suptitle(
        "Reach and engagement describe different dimensions",
        x=0.09,
        y=0.97,
        ha="left",
        fontsize=16,
        fontweight="bold",
    )
    fig.text(
        0.09,
        0.91,
        f"{end} UTC | Each point is one island; labels mark reach/depth maxima",
        fontsize=10,
        va="top",
    )
    fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False)
    fig.subplots_adjust(left=0.09, right=0.96, top=0.83, bottom=0.15)
    fig.savefig(output / "engagement.png", dpi=170)
    plt.close(fig)

    fig, axes = plt.subplots(3, 1, figsize=(12, 11), sharex=True)
    metrics = [
        ("unique_players", "Daily unique players"),
        ("average_minutes_per_player", "Minutes per player"),
        ("plays_per_player", "Plays per player"),
    ]
    for ax, (metric, label) in zip(axes, metrics, strict=True):
        for genre, frame in genre_daily.groupby("genre", sort=True):
            count = int(frame[f"eligible_{metric}"].iloc[0])
            ax.plot(
                frame["day"],
                frame[f"median_{metric}"],
                marker="o",
                markersize=5,
                linewidth=2,
                color=COLORS.get(genre, "#687786"),
                label=f"{LABELS.get(genre, genre)} (n={count})",
            )
        if (
            metric == "unique_players"
            and (genre_daily[f"median_{metric}"].dropna() > 0).all()
            and genre_daily[f"median_{metric}"].notna().any()
        ):
            ax.set_yscale("log")
            label += " (log scale)"
        else:
            ax.set_ylim(bottom=0)
        ax.set_ylabel(label)
        ax.grid(alpha=0.18)
        ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1), frameon=False, fontsize=9)
        ax.xaxis.set_major_locator(
            mdates.DayLocator(interval=max(1, len(daily["day"].unique()) // 8))
        )
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    axes[-1].set_xlabel("UTC day")
    fig.suptitle(
        "Daily genre medians in fixed eligible cohorts",
        x=0.08,
        y=0.97,
        ha="left",
        fontsize=17,
        fontweight="bold",
    )
    fig.text(
        0.08,
        0.93,
        subtitle
        + f"\n{len(daily['day'].unique())} daily buckets | Metric-specific cohorts; no causal claims.",
        fontsize=10,
        va="top",
    )
    fig.subplots_adjust(left=0.09, right=0.72, top=0.85, bottom=0.08, hspace=0.28)
    fig.savefig(output / "trends.png", dpi=170)
    plt.close(fig)

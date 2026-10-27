
import argparse

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from utils.console import make_console

# Console object from the rich library
# All print statements go through this to get colored terminal output
console = make_console()


# ─── Core Calculation Function ────────────────────────────────────────────────

def calculate_revenue(
    num_products: int,
    units_per_product: int,
    wholesale_price: float,
    retail_price: float,
    platform_commission: float,
    months: int,
) -> dict:
    """
    Calculates revenue projection for a supplier joining ecomdropship.ai.

    Args:
        num_products        : How many different products the supplier lists
        units_per_product   : How many units of each product sell per month
        wholesale_price     : What the supplier currently charges wholesale (e.g. $4)
        retail_price        : What the product sells for on dropship model (e.g. $8 = 2x)
        platform_commission : Our platform's cut in percentage (e.g. 10%)
        months              : How many months to project forward

    Returns:
        A dict containing monthly figures, annual figures, chart data, and a summary string
    """

    # Total units sold across all products per month
    # Example: 10 products × 50 units each = 500 units/month
    total_units_monthly = num_products * units_per_product

    # ── Monthly Revenue Calculations ──────────────────────────────────────

    # Wholesale revenue = units sold × wholesale price per unit
    # Example: 500 units × $4 = $2,000/month
    wholesale_monthly = total_units_monthly * wholesale_price

    # Dropship revenue = units sold × retail price, minus our platform commission
    # Example: 500 units × $8 × (1 - 10/100) = 500 × $8 × 0.90 = $3,600/month
    dropship_monthly = total_units_monthly * retail_price * (1 - platform_commission / 100)

    # Extra monthly profit = dropship revenue - wholesale revenue
    # Example: $3,600 - $2,000 = $1,600/month extra by joining us
    extra_monthly = dropship_monthly - wholesale_monthly

    # ── Annual Revenue Calculations ────────────────────────────────────────

    # Just multiply monthly figures by the number of months in the projection
    wholesale_annual = wholesale_monthly * months
    dropship_annual  = dropship_monthly  * months
    extra_annual     = extra_monthly     * months

    # ── Month-by-Month Chart Data ──────────────────────────────────────────
    # Builds a list of cumulative totals for each month
    # This is used by the React frontend to draw a line/bar chart
    chart_data = []
    for i in range(months):
        chart_data.append({
            "month":      i + 1,
            "wholesale":  round(wholesale_monthly * (i + 1), 2),  # Cumulative wholesale total
            "dropship":   round(dropship_monthly  * (i + 1), 2),  # Cumulative dropship total
            "extra":      round(extra_monthly     * (i + 1), 2),  # Cumulative extra profit
        })

    # ── Revenue Multiplier ─────────────────────────────────────────────────
    # Shows how many times more the supplier earns on dropship vs wholesale
    # Example: $3,600 / $2,000 = 1.8x
    multiplier = round(dropship_monthly / wholesale_monthly, 2) if wholesale_monthly > 0 else 0

    # Return everything as a single dictionary
    return {
        "inputs": {
            "num_products":        num_products,
            "units_per_product":   units_per_product,
            "total_units_monthly": total_units_monthly,
            "wholesale_price":     wholesale_price,
            "retail_price":        retail_price,
            "platform_commission": platform_commission,
            "months":              months,
        },
        "monthly": {
            "wholesale": round(wholesale_monthly, 2),
            "dropship":  round(dropship_monthly, 2),
            "extra":     round(extra_monthly, 2),
        },
        "annual": {
            "wholesale": round(wholesale_annual, 2),
            "dropship":  round(dropship_annual, 2),
            "extra":     round(extra_annual, 2),
        },
        "multiplier":        multiplier,
        "break_even_months": 0,       # No upfront cost to join ecomdropship.ai
        "chart_data":        chart_data,
        "summary": (
            f"By joining ecomdropship.ai, you could earn an extra "
            f"${round(extra_monthly):,}/month — "
            f"that's ${round(extra_annual):,} over {months} months."
        ),
    }


# ─── Terminal Display ─────────────────────────────────────────────────────────

def display_results(result: dict) -> None:
    """
    Prints the revenue calculation results as formatted tables in the terminal.
    Uses the 'rich' library for colors and table borders.

    Args:
        result: The dict returned by calculate_revenue()
    """

    # Unpack the dict into shorter variable names for readability
    inputs  = result["inputs"]
    monthly = result["monthly"]
    annual  = result["annual"]
    chart   = result["chart_data"]

    # Print a horizontal divider line as a header
    console.print()
    console.rule("[bold cyan]ecomdropship.ai — Revenue Calculator")
    console.print()

    # ── Table 1: Input Summary ─────────────────────────────────────────────
    # Shows back the values the user entered, so they can confirm them
    input_table = Table(
        title="Your Inputs",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
    )
    input_table.add_column("Setting", style="cyan",  min_width=30)
    input_table.add_column("Value",   style="white", justify="right")

    input_table.add_row("Number of products",       str(inputs["num_products"]))
    input_table.add_row("Units per product/month",  str(inputs["units_per_product"]))
    input_table.add_row("Total units/month",        str(inputs["total_units_monthly"]))
    input_table.add_row("Your wholesale price",     f"${inputs['wholesale_price']:.2f}")
    input_table.add_row("Dropship retail price",    f"${inputs['retail_price']:.2f}")
    input_table.add_row("Platform commission",      f"{inputs['platform_commission']}%")
    input_table.add_row("Projection period",        f"{inputs['months']} months")

    console.print(input_table)
    console.print()

    # ── Table 2: Monthly vs Annual Comparison ─────────────────────────────
    # Side-by-side comparison of wholesale vs dropship earnings
    monthly_table = Table(
        title="Monthly Revenue Comparison",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
    )
    monthly_table.add_column("Model",           style="cyan",  min_width=25)
    monthly_table.add_column("Monthly Revenue", style="white", justify="right")
    monthly_table.add_column("Annual Revenue",  style="white", justify="right")

    # Row 1: Current wholesale model (shown in red = lower earnings)
    monthly_table.add_row(
        "Wholesale only (current)",
        f"[red]${monthly['wholesale']:,.2f}[/red]",
        f"[red]${annual['wholesale']:,.2f}[/red]",
    )
    # Row 2: Dropship model via our platform (shown in green = higher earnings)
    monthly_table.add_row(
        "Dropship via ecomdropship.ai",
        f"[green]${monthly['dropship']:,.2f}[/green]",
        f"[green]${annual['dropship']:,.2f}[/green]",
    )
    # Row 3: The difference — extra profit by joining us (shown in yellow = highlight)
    monthly_table.add_row(
        "[bold]Extra profit (joining bonus)[/bold]",
        f"[bold yellow]${monthly['extra']:,.2f}[/bold yellow]",
        f"[bold yellow]${annual['extra']:,.2f}[/bold yellow]",
    )

    console.print(monthly_table)
    console.print()

    # ── Table 3: Month-by-Month Projection ────────────────────────────────
    # Shows cumulative earnings growing over time for both models
    # We cap display at 12 months even if user requested more, to keep table readable
    show_months = min(len(chart), 12)

    projection_table = Table(
        title=f"Month-by-Month Cumulative Projection (first {show_months} months)",
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold blue",
    )
    projection_table.add_column("Month",     justify="center", style="dim", width=8)
    projection_table.add_column("Wholesale", justify="right",  style="red")
    projection_table.add_column("Dropship",  justify="right",  style="green")
    projection_table.add_column("Extra $$$", justify="right",  style="bold yellow")

    # Loop through each month's data and add a row
    for row in chart[:show_months]:
        projection_table.add_row(
            str(row["month"]),
            f"${row['wholesale']:,.0f}",
            f"${row['dropship']:,.0f}",
            f"+${row['extra']:,.0f}",
        )

    console.print(projection_table)
    console.print()

    # ── Summary Panel ──────────────────────────────────────────────────────
    # A highlighted box with the key takeaway message
    # This same message text is also embedded in cold emails sent to suppliers
    console.print(Panel(
        f"[bold green]{result['summary']}[/bold green]\n\n"
        f"[dim]Revenue multiplier: {result['multiplier']}x your current wholesale earnings[/dim]\n"
        f"[dim]No upfront cost to join — pure revenue upside[/dim]",
        title="[bold]Summary[/bold]",
        border_style="green",
    ))
    console.print()


# ─── Quick Presets for Common Niches ─────────────────────────────────────────

def run_niche_presets() -> None:
    """
    Runs quick revenue calculations for 5 common supplier niches.
    Useful as a fast reference for the sales team during outreach calls.
    Run with: python calculator.py --presets
    """

    # Each tuple: (niche_name, num_products, units_per_product, wholesale_price, retail_price, commission)
    presets = [
        ("Toys",         10,  80,  4.0,  8.0,  10),
        ("Electronics",   5,  30, 15.0, 30.0,  12),
        ("Clothing",     20, 100,  6.0, 14.0,  10),
        ("Home Decor",   15,  60,  8.0, 18.0,  10),
        ("Pet Supplies", 12,  90,  5.0, 11.0,  10),
    ]

    console.rule("[bold cyan]Quick Niche Presets — Monthly Extra Revenue")

    # Build one summary table comparing all niches side by side
    table = Table(box=box.ROUNDED, header_style="bold magenta")
    table.add_column("Niche",        style="cyan",        min_width=15)
    table.add_column("Wholesale/mo", justify="right",     style="red")
    table.add_column("Dropship/mo",  justify="right",     style="green")
    table.add_column("Extra/mo",     justify="right",     style="bold yellow")
    table.add_column("Annual extra", justify="right",     style="bold yellow")
    table.add_column("Multiplier",   justify="center",    style="magenta")

    # Calculate for each niche and add a row
    for name, prods, units, ws, rt, comm in presets:
        r = calculate_revenue(prods, units, ws, rt, comm, 12)
        table.add_row(
            name,
            f"${r['monthly']['wholesale']:,.0f}",
            f"${r['monthly']['dropship']:,.0f}",
            f"${r['monthly']['extra']:,.0f}",
            f"${r['annual']['extra']:,.0f}",
            f"{r['multiplier']}x",
        )

    console.print(table)
    console.print()


# ─── CLI Entry Point ──────────────────────────────────────────────────────────
# This block only runs when you execute: python calculator.py
# It will NOT run if this file is imported by another file (e.g. app.py)

if __name__ == "__main__":

    # argparse reads arguments from the command line
    # Example: python calculator.py --products 10 --units 50
    parser = argparse.ArgumentParser(
        description="ecomdropship.ai Revenue Calculator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python calculator.py                                     # Run with default values
  python calculator.py --products 20 --units 100          # More products and units
  python calculator.py --wholesale 6 --retail 14          # Different price points
  python calculator.py --commission 12 --months 24        # Custom commission, 2-year view
  python calculator.py --presets                          # Quick comparison for 5 niches
        """,
    )

    # Define each accepted argument, its type, default value, and help text
    parser.add_argument("--products",   type=int,   default=10,   help="Number of products listed (default: 10)")
    parser.add_argument("--units",      type=int,   default=50,   help="Units sold per product per month (default: 50)")
    parser.add_argument("--wholesale",  type=float, default=4.0,  help="Wholesale price per unit in USD (default: 4.0)")
    parser.add_argument("--retail",     type=float, default=8.0,  help="Dropship retail price in USD (default: 8.0)")
    parser.add_argument("--commission", type=float, default=10.0, help="Platform commission percentage (default: 10.0)")
    parser.add_argument("--months",     type=int,   default=12,   help="Number of months to project (default: 12)")
    parser.add_argument("--presets",    action="store_true",      help="Show quick revenue estimates for 5 common niches")

    args = parser.parse_args()

    if args.presets:
        # Show the niche comparison table and exit
        run_niche_presets()
    else:
        # Run the full detailed calculation with the user's provided values
        result = calculate_revenue(
            num_products        = args.products,
            units_per_product   = args.units,
            wholesale_price     = args.wholesale,
            retail_price        = args.retail,
            platform_commission = args.commission,
            months              = args.months,
        )
        display_results(result)
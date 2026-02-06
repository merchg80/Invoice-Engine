from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from dateutil.relativedelta import relativedelta
from reportlab.pdfgen import canvas
from pypdf import PdfReader, PdfWriter


# Repo paths
ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "templates" / "filled_example.pdf"
FIELDS_JSON = ROOT / "templates" / "fields.json"
DATA = ROOT / "data_inputs"
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)


# -------------------------
# Helpers
# -------------------------
def parse_date(s: str) -> date:
    return date.fromisoformat(str(s).strip())


def days_in_month(month_start: date) -> int:
    # month_start is YYYY-MM-01
    return (month_start + relativedelta(months=1) - month_start).days


def fmt_money_plain(x: float) -> str:
    # Template seems to show $ with 2 decimals
    return f"${x:,.2f}"


def fmt_qty_0(x: float) -> str:
    # Template appears to show integer MMBtu in cells
    return f"{x:.0f}"


def fmt_price_2(x: float) -> str:
    # Template appears to show price with 2 decimals
    return f"${x:,.2f}"


def fmt_date_mdy(d: date) -> str:
    # Use M/D/YYYY (no leading zeros) to match typical invoice style
    return f"{d.month}/{d.day}/{d.year}"


def draw_aligned(c: canvas.Canvas, x: float, y: float, text: str, size: int, align: str):
    c.setFont("Helvetica", size)
    if align == "right":
        c.drawRightString(x, y, text)
    elif align == "center":
        c.drawCentredString(x, y, text)
    else:
        c.drawString(x, y, text)


def load_fields() -> dict:
    if not FIELDS_JSON.exists():
        raise FileNotFoundError(f"Missing fields JSON: {FIELDS_JSON}")
    return json.loads(FIELDS_JSON.read_text(encoding="utf-8"))


def load_customers() -> pd.DataFrame:
    fp = DATA / "customers.csv"
    if not fp.exists():
        raise FileNotFoundError(f"Missing customers.csv: {fp}")
    return pd.read_csv(fp)


def load_contracts() -> pd.DataFrame:
    fp = DATA / "contracts.csv"
    if not fp.exists():
        raise FileNotFoundError(f"Missing contracts.csv: {fp}")
    return pd.read_csv(fp)


def load_index_prices(month_start: date) -> pd.DataFrame:
    fp = DATA / "index_prices" / f"{month_start:%Y-%m}.csv"
    if not fp.exists():
        raise FileNotFoundError(f"Missing index prices file: {fp}")
    df = pd.read_csv(fp)
    df["billing_month"] = df["billing_month"].apply(parse_date)
    return df[df["billing_month"] == month_start].copy()


def load_volumes(month_start: date) -> pd.DataFrame:
    fp = DATA / "monthly_volumes" / f"{month_start:%Y-%m}.csv"
    if not fp.exists():
        raise FileNotFoundError(f"Missing monthly volumes file: {fp}")
    df = pd.read_csv(fp)
    df["billing_month"] = df["billing_month"].apply(parse_date)
    df = df[df["billing_month"] == month_start].copy()
    if df.empty:
        raise ValueError(f"No volume rows found for {month_start} in {fp}")
    return df


# -------------------------
# PDF overlay + merge
# -------------------------
def build_overlay_pdf(
    base_width: float,
    base_height: float,
    fields_cfg: dict,
    values: dict,
    out_path: Path,
):
    c = canvas.Canvas(str(out_path), pagesize=(base_width, base_height))

    # 1) White-out rectangles (mask)
    c.setFillColorRGB(1, 1, 1)
    for r in fields_cfg.get("whiteout_rects", []):
        x, y, w, h = float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"])
        if w > 0 and h > 0:
            c.rect(x, y, w, h, fill=1, stroke=0)

    # IMPORTANT FIX:
    # After drawing white rectangles, reset fill color to black for text.
    c.setFillColorRGB(0, 0, 0)

    # 2) Print text fields
    for key, spec in fields_cfg.get("text_fields", {}).items():
        if key not in values:
            # If you want to catch missing mappings, uncomment this:
            # print(f"NOTE: No value provided for field '{key}'")
            continue
        x, y = float(spec["x"]), float(spec["y"])
        size = int(spec.get("size", 10))
        align = str(spec.get("align", "left"))
        draw_aligned(c, x, y, str(values[key]), size, align)

    c.save()


def merge(base_pdf: Path, overlay_pdf: Path, out_pdf: Path, page_index: int = 0):
    base = PdfReader(str(base_pdf))
    overlay = PdfReader(str(overlay_pdf))
    writer = PdfWriter()

    base_page = base.pages[page_index]
    base_page.merge_page(overlay.pages[0])
    writer.add_page(base_page)

    with open(out_pdf, "wb") as f:
        writer.write(f)


# -------------------------
# Main generation
# -------------------------
def main(billing_month: str):
    # billing_month can be "YYYY-MM" or "YYYY-MM-01"
    month_start = parse_date(billing_month + "-01") if len(billing_month) == 7 else parse_date(billing_month)

    # Template existence check (matches your prior workflow error)
    if not TEMPLATE.exists():
        raise FileNotFoundError(
            f"Template PDF not found at {TEMPLATE}. "
            f"Make sure it is committed to the repo at templates/filled_example.pdf (case-sensitive)."
        )

    fields_cfg = load_fields()
    page_index = int(fields_cfg.get("page", 0))

    # Determine template page size
    reader = PdfReader(str(TEMPLATE))
    page = reader.pages[page_index]
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)

    customers = load_customers()
    contracts = load_contracts()
    idx = load_index_prices(month_start)
    vols = load_volumes(month_start)

    # Join: volumes -> contracts -> customers
    df = vols.merge(contracts, on="contract_id", how="left")
    if df["customer_id"].isna().any():
        missing = df[df["customer_id"].isna()]["contract_id"].tolist()
        raise ValueError(f"Missing contract rows for contract_id(s): {missing}. Check data_inputs/contracts.csv")

    df = df.merge(customers, on="customer_id", how="left")
    if df["customer_name"].isna().any():
        missing = df[df["customer_name"].isna()]["customer_id"].tolist()
        raise ValueError(f"Missing customer rows for customer_id(s): {missing}. Check data_inputs/customers.csv")

    # Active filter (defaults to active if column missing)
    if "active" in df.columns:
        df = df[df["active"].fillna(1).astype(int) == 1].copy()

    meter_start = month_start
    meter_end = month_start + relativedelta(months=1) - timedelta(days=1)
    billing_days = days_in_month(month_start)

    produced = []

    for _, r in df.iterrows():
        pricing_type = str(r.get("pricing_type", "")).upper().strip()
        adder = float(r.get("adder", 0.0) if pd.notna(r.get("adder", 0.0)) else 0.0)

        # Rates / parameters
        upstream_fuel_pct = float(r.get("upstream_fuel_pct", 0.015) if pd.notna(r.get("upstream_fuel_pct", 0.015)) else 0.015)
        upstream_transport_rate = float(r.get("upstream_transport_rate", 0.0) if pd.notna(r.get("upstream_transport_rate", 0.0)) else 0.0)
        distribution_rate = float(r.get("distribution_rate", 0.0) if pd.notna(r.get("distribution_rate", 0.0)) else 0.0)
        utility_admin_rate = float(r.get("utility_admin_rate", 4.13) if pd.notna(r.get("utility_admin_rate", 4.13)) else 4.13)

        delivered_mmbtu = float(r.get("delivered_mmbtu", 0.0))
        if delivered_mmbtu <= 0:
            raise ValueError(f"Invalid delivered_mmbtu for contract {r['contract_id']}: {delivered_mmbtu}")

        # upstream transport volume may differ
        upstream_transport_mmbtu = float(r.get("upstream_transport_mmbtu", delivered_mmbtu))
        if upstream_transport_mmbtu <= 0:
            upstream_transport_mmbtu = delivered_mmbtu

        # Determine contract price
        if pricing_type == "INDEX_PLUS":
            index_name = str(r.get("index_name", "")).strip()
            if not index_name:
                raise ValueError(f"Contract {r['contract_id']} pricing_type INDEX_PLUS but index_name is blank.")
            match = idx[idx["index_name"] == index_name]
            if match.empty:
                raise ValueError(f"Missing index price for {index_name} {month_start}. Check data_inputs/index_prices/{month_start:%Y-%m}.csv")
            index_price = float(match.iloc[0]["settlement_price"])
            contract_price = index_price + adder
        elif pricing_type == "FIXED":
            if pd.isna(r.get("fixed_price")):
                raise ValueError(f"Contract {r['contract_id']} pricing_type FIXED but fixed_price is blank.")
            contract_price = float(r.get("fixed_price"))
        else:
            raise ValueError(f"Unsupported pricing_type for contract {r['contract_id']}: '{pricing_type}'")

        # ---- Compute amounts ----
        commodity_amount = delivered_mmbtu * contract_price

        # Your rule: Upstream fuel = 1.5% * delivered volume * delivery price
        upstream_fuel_amount = upstream_fuel_pct * commodity_amount

        # Volumetric lines (MMBtu-based)
        upstream_transport_amount = upstream_transport_mmbtu * upstream_transport_rate
        distribution_amount = delivered_mmbtu * distribution_rate

        # Utility admin qty = avg daily MMBtu (since meter dates are 1st/last of month)
        utility_admin_qty = delivered_mmbtu / billing_days
        utility_admin_amount = utility_admin_qty * utility_admin_rate

        # Fixed charges (from customers.csv)
        utility_customer_charge_amount = float(r.get("utility_customer_charge_amount", 0.0) if pd.notna(r.get("utility_customer_charge_amount", 0.0)) else 0.0)
        reimb_ff = float(r.get("reimb_franchise_fee_amount", 0.0) if pd.notna(r.get("reimb_franchise_fee_amount", 0.0)) else 0.0)
        reimb_pt = float(r.get("reimb_pipeline_tax_amount", 0.0) if pd.notna(r.get("reimb_pipeline_tax_amount", 0.0)) else 0.0)

        # If you later add prior balance / late fee, include here (and map fields)
        total_due = (
            commodity_amount
            + upstream_transport_amount
            + upstream_fuel_amount
            + utility_customer_charge_amount
            + utility_admin_amount
            + distribution_amount
            + reimb_ff
            + reimb_pt
        )

        # Dates/IDs
        invoice_date = date.today()
        terms = int(r.get("payment_terms_days", 10) if pd.notna(r.get("payment_terms_days", 10)) else 10)
        due_date = invoice_date + timedelta(days=terms)

        # Values to print — keys must match templates/fields.json text_fields keys
        values = {
            "invoice_number": f"{r['customer_id']}-{r['contract_id']}-{month_start:%Y%m}",
            "invoice_date": fmt_date_mdy(invoice_date),
            "due_date": fmt_date_mdy(due_date),
            "meter_dates_line": f"Meter Dates: {fmt_date_mdy(meter_start)}-{fmt_date_mdy(meter_end)}",

            # Natural Gas Sales row
            "ng_qty": fmt_qty_0(delivered_mmbtu),
            "ng_rate": fmt_price_2(contract_price),
            "ng_amount": fmt_money_plain(commodity_amount),

            # Upstream Transportation row
            "upstream_transport_qty": fmt_qty_0(upstream_transport_mmbtu),
            "upstream_transport_rate": fmt_price_2(upstream_transport_rate),
            "upstream_transport_amount": fmt_money_plain(upstream_transport_amount),

            # Upstream Fuel amount only (per your template)
            "upstream_fuel_amount": fmt_money_plain(upstream_fuel_amount),

            # Utility customer charge
            "utility_customer_charge_amount": fmt_money_plain(utility_customer_charge_amount),

            # Utility admin (qty/rate/amount)
            "utility_admin_qty": fmt_qty_0(utility_admin_qty),
            "utility_admin_rate": fmt_price_2(utility_admin_rate),
            "utility_admin_amount": fmt_money_plain(utility_admin_amount),

            # Distribution (qty/rate/amount)
            "distribution_qty": fmt_qty_0(delivered_mmbtu),
            "distribution_rate": fmt_price_2(distribution_rate),
            "distribution_amount": fmt_money_plain(distribution_amount),

            # Total due
            "total_due": fmt_money_plain(total_due),
        }

        # Overlay + final output
        overlay_pdf = OUT / f"overlay_{r['customer_id']}_{r['contract_id']}_{month_start:%Y-%m}.pdf"
        final_pdf = OUT / f"Invoice_{r['customer_id']}_{r['contract_id']}_{month_start:%Y-%m}.pdf"

        build_overlay_pdf(width, height, fields_cfg, values, overlay_pdf)
        merge(TEMPLATE, overlay_pdf, final_pdf, page_index=page_index)

        produced.append(str(final_pdf))
        print(f"Created: {final_pdf}")

    if not produced:
        print("No invoices generated (check active flags and monthly volume inputs).")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("Usage: python src/invoice_engine/stamp.py YYYY-MM")
    main(sys.argv[1])

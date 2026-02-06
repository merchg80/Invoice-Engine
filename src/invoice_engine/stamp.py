from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from dateutil.relativedelta import relativedelta
from reportlab.pdfgen import canvas
from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "templates" / "filled_example.pdf"
FIELDS_JSON = ROOT / "templates" / "fields.json"
DATA = ROOT / "data_inputs"
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)

def parse_date(s: str) -> date:
    return date.fromisoformat(s.strip())

def days_in_month(month_start: date) -> int:
    return (month_start + relativedelta(months=1) - month_start).days

def fmt_money(x: float) -> str:
    return f"{x:,.2f}"

def fmt_qty(x: float) -> str:
    return f"{x:,.3f}"

def fmt_price(x: float) -> str:
    return f"{x:,.4f}"

def draw_aligned(c: canvas.Canvas, x: float, y: float, text: str, size: int, align: str):
    c.setFont("Helvetica", size)
    if align == "right":
        c.drawRightString(x, y, text)
    elif align == "center":
        c.drawCentredString(x, y, text)
    else:
        c.drawString(x, y, text)

def load_fields():
    return json.loads(FIELDS_JSON.read_text(encoding="utf-8"))

def load_customers():
    return pd.read_csv(DATA / "customers.csv")

def load_contracts():
    return pd.read_csv(DATA / "contracts.csv")

def load_index_prices(month_start: date):
    fp = DATA / "index_prices" / f"{month_start:%Y-%m}.csv"
    df = pd.read_csv(fp)
    df["billing_month"] = df["billing_month"].apply(parse_date)
    return df[df["billing_month"] == month_start].copy()

def load_volumes(month_start: date):
    fp = DATA / "monthly_volumes" / f"{month_start:%Y-%m}.csv"
    df = pd.read_csv(fp)
    df["billing_month"] = df["billing_month"].apply(parse_date)
    return df[df["billing_month"] == month_start].copy()

def build_overlay_pdf(base_width: float, base_height: float, fields_cfg: dict, values: dict, out_path: Path):
    c = canvas.Canvas(str(out_path), pagesize=(base_width, base_height))

    # 1) White-out rectangles (mask)
    c.setFillColorRGB(1, 1, 1)
    for r in fields_cfg.get("whiteout_rects", []):
        x, y, w, h = float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"])
        if w > 0 and h > 0:
            c.rect(x, y, w, h, fill=1, stroke=0)

    # 2) Print text fields
    for key, spec in fields_cfg.get("text_fields", {}).items():
        if key not in values:
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

def main(billing_month: str):
    month_start = parse_date(billing_month + "-01") if len(billing_month) == 7 else parse_date(billing_month)

    customers = load_customers()
    contracts = load_contracts()
    idx = load_index_prices(month_start)
    vols = load_volumes(month_start)
    fields_cfg = load_fields()
    page_index = int(fields_cfg.get("page", 0))

    # Read template size
    reader = PdfReader(str(TEMPLATE))
    page = reader.pages[page_index]
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)

    # Join vols -> contracts -> customers
    df = vols.merge(contracts, on="contract_id", how="left").merge(customers, on="customer_id", how="left")
    df = df[df.get("active", 1) == 1]

    meter_start = month_start
    meter_end = month_start + relativedelta(months=1) - timedelta(days=1)
    billing_days = days_in_month(month_start)

    for _, r in df.iterrows():
        pricing_type = str(r["pricing_type"]).upper()
        adder = float(r.get("adder", 0.0))
        upstream_fuel_pct = float(r.get("upstream_fuel_pct", 0.015))
        utility_admin_rate = float(r.get("utility_admin_rate", 4.13))

        delivered_mmbtu = float(r["delivered_mmbtu"])

        # Price
        if pricing_type == "INDEX_PLUS":
            index_name = str(r["index_name"])
            match = idx[idx["index_name"] == index_name]
            if match.empty:
                raise ValueError(f"Missing index price for {index_name} {month_start}")
            index_price = float(match.iloc[0]["settlement_price"])
            contract_price = index_price + adder
        elif pricing_type == "FIXED":
            contract_price = float(r["fixed_price"])
        else:
            raise ValueError(f"Unsupported pricing_type: {pricing_type}")

        # Compute
        commodity_amount = delivered_mmbtu * contract_price
        upstream_fuel_amount = upstream_fuel_pct * commodity_amount

        # Utility admin qty is avg daily MMBtu (since meter dates are 1st/last, this is deterministic)
        utility_admin_qty = delivered_mmbtu / billing_days
        utility_admin_amount = utility_admin_qty * utility_admin_rate

        fixed_utility = float(r.get("utility_customer_charge_amount", 0.0))
        reimb_ff = float(r.get("reimb_franchise_fee_amount", 0.0))
        reimb_pt = float(r.get("reimb_pipeline_tax_amount", 0.0))

        total_due = (
            commodity_amount
            + upstream_fuel_amount
            + utility_admin_amount
            + fixed_utility
            + reimb_ff
            + reimb_pt
        )

        invoice_date = date.today()
        terms = int(r.get("payment_terms_days", 10))
        due_date = invoice_date + timedelta(days=terms)

        # Values to print
        values = {
            "invoice_number": f'{r["customer_id"]}-{r["contract_id"]}-{month_start:%Y%m}',
            "invoice_date": invoice_date.isoformat(),
            "due_date": due_date.isoformat(),
            "meter_start": meter_start.isoformat(),
            "meter_end": meter_end.isoformat(),

            "delivered_mmbtu": fmt_qty(delivered_mmbtu),
            "contract_price": fmt_price(contract_price),
            "commodity_amount": fmt_money(commodity_amount),

            "upstream_fuel_amount": fmt_money(upstream_fuel_amount),
            "utility_admin_qty": fmt_qty(utility_admin_qty),
            "utility_admin_rate": fmt_price(utility_admin_rate),
            "utility_admin_amount": fmt_money(utility_admin_amount),

            "total_due": fmt_money(total_due),
        }

        overlay_pdf = OUT / f"overlay_{r['customer_id']}_{r['contract_id']}_{month_start:%Y-%m}.pdf"
        final_pdf = OUT / f"Invoice_{r['customer_id']}_{r['contract_id']}_{month_start:%Y-%m}.pdf"

        build_overlay_pdf(width, height, fields_cfg, values, overlay_pdf)
        merge(TEMPLATE, overlay_pdf, final_pdf, page_index=page_index)

        print(f"Created: {final_pdf}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python src/invoice_engine/stamp.py YYYY-MM")
    main(sys.argv[1])


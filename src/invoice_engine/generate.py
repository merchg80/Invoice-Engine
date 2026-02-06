from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from dateutil.relativedelta import relativedelta
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[2]  # repo root
DATA = ROOT / "data_inputs"
OUT = ROOT / "out"


def parse_date(s: str) -> date:
    return date.fromisoformat(s.strip())


def days_in_month(month_start: date) -> int:
    next_month = month_start + relativedelta(months=1)
    return (next_month - month_start).days


@dataclass
class Customer:
    customer_id: str
    customer_name: str
    customer_address: str
    service_address: str
    business_address: str
    customer_id_number: str | None
    account_number: str | None
    payment_terms_days: int
    utility_customer_charge_amount: float
    reimb_franchise_fee_amount: float
    reimb_pipeline_tax_amount: float


@dataclass
class Contract:
    contract_id: str
    customer_id: str
    contract_name: str
    pricing_type: str
    index_name: str | None
    adder: float
    fixed_price: float | None
    upstream_fuel_pct: float
    distribution_rate: float
    active: int


def load_customers() -> dict[str, Customer]:
    df = pd.read_csv(DATA / "customers.csv")
    out = {}
    for _, r in df.iterrows():
        out[r["customer_id"]] = Customer(
            customer_id=str(r["customer_id"]),
            customer_name=str(r["customer_name"]),
            customer_address=str(r["customer_address"]),
            service_address=str(r["service_address"]),
            business_address=str(r["business_address"]),
            customer_id_number=None if pd.isna(r.get("customer_id_number")) else str(r.get("customer_id_number")),
            account_number=None if pd.isna(r.get("account_number")) else str(r.get("account_number")),
            payment_terms_days=int(r.get("payment_terms_days", 10)),
            utility_customer_charge_amount=float(r.get("utility_customer_charge_amount", 0.0)),
            reimb_franchise_fee_amount=float(r.get("reimb_franchise_fee_amount", 0.0)),
            reimb_pipeline_tax_amount=float(r.get("reimb_pipeline_tax_amount", 0.0)),
        )
    return out


def load_contracts() -> dict[str, Contract]:
    df = pd.read_csv(DATA / "contracts.csv")
    out = {}
    for _, r in df.iterrows():
        out[str(r["contract_id"])] = Contract(
            contract_id=str(r["contract_id"]),
            customer_id=str(r["customer_id"]),
            contract_name=str(r.get("contract_name", "")),
            pricing_type=str(r["pricing_type"]),
            index_name=None if pd.isna(r.get("index_name")) else str(r.get("index_name")),
            adder=float(r.get("adder", 0.0)),
            fixed_price=None if pd.isna(r.get("fixed_price")) else float(r.get("fixed_price")),
            upstream_fuel_pct=float(r.get("upstream_fuel_pct", 0.015)),
            distribution_rate=float(r.get("distribution_rate", 4.13)),
            active=int(r.get("active", 1)),
        )
    return out


def load_index_price(index_name: str, billing_month: date) -> float:
    fp = DATA / "index_prices" / f"{billing_month:%Y-%m}.csv"
    df = pd.read_csv(fp)
    df["billing_month"] = df["billing_month"].apply(parse_date)
    match = df[(df["index_name"] == index_name) & (df["billing_month"] == billing_month)]
    if match.empty:
        raise ValueError(f"Missing index price for {index_name} {billing_month} in {fp}")
    return float(match.iloc[0]["settlement_price"])


def load_monthly_volumes(billing_month: date) -> pd.DataFrame:
    fp = DATA / "monthly_volumes" / f"{billing_month:%Y-%m}.csv"
    df = pd.read_csv(fp)
    df["billing_month"] = df["billing_month"].apply(parse_date)
    df = df[df["billing_month"] == billing_month].copy()
    if df.empty:
        raise ValueError(f"No volumes found for {billing_month} in {fp}")
    return df


def money(x: float) -> float:
    # standard currency rounding
    return float(f"{x:.2f}")


def render_pdf(
    *,
    out_path: Path,
    invoice_no: str,
    invoice_date: date,
    due_date: date,
    billing_month: date,
    customer: Customer,
    contract: Contract,
    delivered_mmbtu: float,
    contract_price: float,
    lines: list[dict],
    total_due: float,
):
    OUT.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(out_path), pagesize=letter)
    w, h = letter

    y = h - 50
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, y, "INVOICE")
    c.setFont("Helvetica", 10)
    y -= 18
    c.drawString(50, y, f"Invoice #: {invoice_no}")
    y -= 14
    c.drawString(50, y, f"Invoice Date: {invoice_date.isoformat()}")
    y -= 14
    c.drawString(50, y, f"Due Date: {due_date.isoformat()}")
    y -= 14
    c.drawString(50, y, f"Billing Month: {billing_month:%Y-%m}")

    y -= 22
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "Bill To:")
    c.setFont("Helvetica", 10)
    y -= 14
    c.drawString(50, y, customer.customer_name)
    y -= 14
    c.drawString(50, y, customer.customer_address)

    y -= 22
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "Service Address:")
    c.setFont("Helvetica", 10)
    y -= 14
    c.drawString(50, y, customer.service_address)

    y -= 22
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "Contract / Usage:")
    c.setFont("Helvetica", 10)
    y -= 14
    c.drawString(50, y, f"Contract: {contract.contract_name} ({contract.contract_id})")
    y -= 14
    c.drawString(50, y, f"Delivered: {delivered_mmbtu:,.3f} MMBtu @ ${contract_price:.4f}/MMBtu")

    # Lines table
    y -= 26
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "Description")
    c.drawString(360, y, "Qty")
    c.drawString(430, y, "Rate")
    c.drawString(510, y, "Amount")
    y -= 8
    c.line(50, y, 560, y)

    c.setFont("Helvetica", 10)
    for ln in lines:
        y -= 16
        if y < 90:
            c.showPage()
            y = h - 60
        c.drawString(50, y, ln["description"])
        qty = "" if ln.get("qty") is None else f'{ln["qty"]:,.3f}'
        rate = "" if ln.get("rate") is None else f'{ln["rate"]:.4f}'
        amt = f'{ln["amount"]:,.2f}'
        c.drawRightString(420, y, qty)
        c.drawRightString(490, y, rate)
        c.drawRightString(560, y, amt)

    y -= 20
    c.line(350, y, 560, y)
    y -= 18
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(490, y, "Total Due:")
    c.drawRightString(560, y, f"{total_due:,.2f}")

    y -= 40
    c.setFont("Helvetica", 9)
    c.drawString(50, y, "Payment Terms: Net " + str(customer.payment_terms_days))
    y -= 14
    c.drawString(50, y, "Business Address: " + customer.business_address)

    c.save()


def generate_for_month(month_str: str):
    billing_month = parse_date(month_str + "-01") if len(month_str) == 7 else parse_date(month_str)
    customers = load_customers()
    contracts = load_contracts()
    vols = load_monthly_volumes(billing_month)

    OUT.mkdir(exist_ok=True)

    run_ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    produced = []

    for _, row in vols.iterrows():
        contract_id = str(row["contract_id"])
        delivered_mmbtu = float(row["delivered_mmbtu"])

        contract = contracts[contract_id]
        if contract.active != 1:
            continue
        customer = customers[contract.customer_id]

        # pricing
        if contract.pricing_type.upper() == "INDEX_PLUS":
            if not contract.index_name:
                raise ValueError(f"Contract {contract_id} missing index_name")
            idx = load_index_price(contract.index_name, billing_month)
            contract_price = idx + contract.adder
        elif contract.pricing_type.upper() == "FIXED":
            if contract.fixed_price is None:
                raise ValueError(f"Contract {contract_id} missing fixed_price")
            contract_price = contract.fixed_price
        else:
            raise ValueError(f"Unknown pricing_type: {contract.pricing_type}")

        # period dates: first/last day of month
        meter_start = billing_month
        meter_end = billing_month + relativedelta(months=1) - timedelta(days=1)
        billing_days = days_in_month(billing_month)

        # compute lines
        commodity_amount = money(delivered_mmbtu * contract_price)
        upstream_fuel = money(contract.upstream_fuel_pct * (delivered_mmbtu * contract_price))
        avg_daily = delivered_mmbtu / billing_days
        distribution_amount = money(avg_daily * contract.distribution_rate)

        fixed_utility = money(customer.utility_customer_charge_amount)
        reimb1 = money(customer.reimb_franchise_fee_amount)
        reimb2 = money(customer.reimb_pipeline_tax_amount)

        lines = [
            {"category": "Commodity", "description": "Natural Gas Sales", "qty": delivered_mmbtu, "rate": contract_price, "amount": commodity_amount},
            {"category": "Other", "description": f"Upstream Fuel ({contract.upstream_fuel_pct*100:.2f}%)", "qty": None, "rate": None, "amount": upstream_fuel},
            {"category": "Other", "description": f"Distribution Charge ({billing_days} days)", "qty": avg_daily, "rate": contract.distribution_rate, "amount": distribution_amount},
        ]
        if fixed_utility != 0.0:
            lines.append({"category": "Fixed", "description": "Utility Customer Charge", "qty": None, "rate": None, "amount": fixed_utility})
        if reimb1 != 0.0:
            lines.append({"category": "Fixed", "description": "Reimbursement - Franchise Fee", "qty": None, "rate": None, "amount": reimb1})
        if reimb2 != 0.0:
            lines.append({"category": "Fixed", "description": "Reimbursement - Pipeline Tax", "qty": None, "rate": None, "amount": reimb2})

        total_due = money(sum(l["amount"] for l in lines))

        # invoice header
        invoice_date = date.today()
        due_date = invoice_date + timedelta(days=customer.payment_terms_days)
        invoice_no = f"{customer.customer_id}-{billing_month:%Y%m}-{run_ts}"

        out_pdf = OUT / f"Invoice_{customer.customer_id}_{contract_id}_{billing_month:%Y-%m}.pdf"
        render_pdf(
            out_path=out_pdf,
            invoice_no=invoice_no,
            invoice_date=invoice_date,
            due_date=due_date,
            billing_month=billing_month,
            customer=customer,
            contract=contract,
            delivered_mmbtu=delivered_mmbtu,
            contract_price=contract_price,
            lines=lines,
            total_due=total_due,
        )
        produced.append(str(out_pdf))

    print("Generated PDFs:")
    for p in produced:
        print(" -", p)


if __name__ == "__main__":
    import sys
    # usage: python -m src.invoice_engine.generate 2026-01
    month = sys.argv[1] if len(sys.argv) > 1 else "2026-01"
    generate_for_month(month)


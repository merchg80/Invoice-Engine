from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "templates" / "filled_example.pdf"
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)

def make_grid_overlay(width, height, step=25):
    overlay_path = OUT / "grid_overlay.pdf"
    c = canvas.Canvas(str(overlay_path), pagesize=(width, height))

    c.setFont("Helvetica", 6)

    # draw grid
    x = 0
    while x <= width:
        c.line(x, 0, x, height)
        c.drawString(x + 2, 2, str(int(x)))
        x += step

    y = 0
    while y <= height:
        c.line(0, y, width, y)
        c.drawString(2, y + 2, str(int(y)))
        y += step

    c.save()
    return overlay_path

def main():
    reader = PdfReader(str(TEMPLATE))
    page = reader.pages[0]
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)

    grid_overlay = make_grid_overlay(width, height, step=25)

    overlay_reader = PdfReader(str(grid_overlay))
    overlay_page = overlay_reader.pages[0]

    out_pdf = OUT / "template_with_grid.pdf"
    writer = PdfWriter()

    base_page = reader.pages[0]
    base_page.merge_page(overlay_page)
    writer.add_page(base_page)

    with open(out_pdf, "wb") as f:
        writer.write(f)

    print(f"Created: {out_pdf}")

if __name__ == "__main__":
    main()


"""
make_test_fixtures.py — generates small synthetic AMFI-style workbooks so
you can run the whole pipeline once, right now, before you have a real
downloaded file. Mimics real-world messiness on purpose: title rows above
the header, a subtotal row, a disclaimer row, and two AMCs using slightly
different column names for the same fields — all of which the parser
needs to handle. Run this once, then see README.md "Try it right now".
"""
import openpyxl

"""
Builds a synthetic AMFI-style monthly disclosure workbook to test the
parser's logic against, since the real amfiindia.com site isn't reachable
from this sandbox. Mimics the real-world messiness: title/metadata rows
above the header, a slightly odd column name, a subtotal row with no ISIN,
and one row with a stray blank cell.
"""

wb = openpyxl.Workbook()
wb.remove(wb.active)

# --- Sheet 1: a large-cap scheme ---
ws1 = wb.create_sheet("HDFC Top 100 Fund")
ws1.append(["HDFC Mutual Fund"])
ws1.append(["Portfolio as on March 31, 2026"])
ws1.append([])  # blank row before header, as real files often have
ws1.append([
    "Name of the Instrument", "ISIN", "Industry+/ Rating",
    "Quantity", "Market Value (Rs. in Lakhs)", "% to NAV",
])
ws1.append(["Reliance Industries Ltd.", "INE002A01018", "Refineries", 120000, 45230.50, 8.21])
ws1.append(["HDFC Bank Ltd.", "INE040A01034", "Banks", 300000, 51200.00, 9.30])
ws1.append(["Infosys Ltd.", "INE009A01021", "IT - Software", 85000, 12750.75, 2.31])
ws1.append(["Total", None, None, None, 550000.00, 100.0])  # subtotal row, no valid ISIN

# --- Sheet 2: a mid-cap scheme, with a slightly different column name for market value ---
ws2 = wb.create_sheet("HDFC Mid-Cap Opportunities Fund")
ws2.append(["HDFC Mutual Fund"])
ws2.append(["Portfolio as on March 31, 2026"])
ws2.append([])
ws2.append([
    "Name of the Instrument", "ISIN", "Industry",
    "Quantity", "Market/Fair Value(Rs. in Lakhs)", "% to Net Assets",
])
ws2.append(["Tata Motors Ltd.", "INE155A01022", "Automobiles", 200000, 18400.00, 5.10])
ws2.append(["Persistent Systems Ltd.", "INE262H01021", "IT - Software", 40000, 9800.00, 2.72])
ws2.append(["Notes: figures are unaudited"])  # disclaimer row at the bottom

wb.save("test_hdfc_march2026.xlsx")
print("Wrote test_hdfc_march2026.xlsx")
"""Second month + a second AMC, to exercise deltas and cross-AMC consensus."""

# HDFC AMC, April: Reliance added to, HDFC Bank trimmed, Infosys exited, TCS new
wb1 = openpyxl.Workbook()
wb1.remove(wb1.active)
ws1 = wb1.create_sheet("HDFC Top 100 Fund")
ws1.append(["HDFC Mutual Fund"]); ws1.append(["Portfolio as on April 30, 2026"]); ws1.append([])
ws1.append(["Name of the Instrument", "ISIN", "Industry+/ Rating", "Quantity", "Market Value (Rs. in Lakhs)", "% to NAV"])
ws1.append(["Reliance Industries Ltd.", "INE002A01018", "Refineries", 140000, 53500.00, 9.10])
ws1.append(["HDFC Bank Ltd.", "INE040A01034", "Banks", 260000, 44400.00, 7.55])
ws1.append(["Tata Consultancy Services Ltd.", "INE467B01029", "IT - Software", 30000, 11400.00, 1.94])
ws1.append(["Total", None, None, None, 550000.00, 100.0])

ws2 = wb1.create_sheet("HDFC Mid-Cap Opportunities Fund")
ws2.append(["HDFC Mutual Fund"]); ws2.append(["Portfolio as on April 30, 2026"]); ws2.append([])
ws2.append(["Name of the Instrument", "ISIN", "Industry", "Quantity", "Market/Fair Value(Rs. in Lakhs)", "% to Net Assets"])
ws2.append(["Tata Motors Ltd.", "INE155A01022", "Automobiles", 200000, 18500.00, 5.05])
ws2.append(["Persistent Systems Ltd.", "INE262H01021", "IT - Software", 55000, 13100.00, 3.58])
ws2.append(["Infosys Ltd.", "INE009A01021", "IT - Software", 25000, 3750.25, 1.02])
wb1.save("test_hdfc_april2026.xlsx")

# A second AMC (SBI) also buying Reliance in April — for cross-AMC consensus
wb2 = openpyxl.Workbook()
wb2.remove(wb2.active)
ws3 = wb2.create_sheet("SBI Bluechip Fund")
ws3.append(["SBI Mutual Fund"]); ws3.append(["Portfolio as on April 30, 2026"]); ws3.append([])
ws3.append(["Name of the Instrument", "ISIN", "Industry", "Quantity", "Market Value (Rs. in Lakhs)", "% to NAV"])
ws3.append(["Reliance Industries Ltd.", "INE002A01018", "Refineries", 90000, 34400.00, 6.80])
wb2.save("test_sbi_april2026.xlsx")

print("Wrote April fixtures for HDFC AMC and SBI AMC")

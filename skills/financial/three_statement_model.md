# Three-Statement Financial Model Skill

## Purpose
Build a linked Income Statement, Balance Sheet, and Cash Flow Statement for a startup business plan.

## Time Horizon
- Year 1: Monthly granularity (12 periods)
- Years 2-3: Annual granularity (2 periods)

## Income Statement Structure

### Revenue
- Units sold per period = addressable_leads × conversion_rate × (1 - churn_rate)^months
- Revenue per period = units_sold × price_per_unit
- Apply sales_cycle_months lag before first revenue appears
- Growth rate: use volume_year1, volume_year2, volume_year3 from marketing assumptions

### Cost of Goods Sold (COGS)
- Variable cost per unit from cost_structure.variable_costs
- Scale linearly with units sold
- COGS margin = variable_cost_per_unit / price_per_unit

### Operating Expenses
- Personnel: from headcount_plan (salary × headcount per period, with hiring ramp)
- Marketing/CAC: cac × new_customers_acquired per period
- Infrastructure: from cost_structure.fixed_costs (monthly)
- General & Admin: estimate at 10-15% of total opex if not specified

### EBITDA
- Revenue - COGS - Operating Expenses

### Net Income
- EBITDA - Depreciation (if capex) - Interest (if debt) - Tax (apply 0% until profitable for startups)

## Balance Sheet Structure

### Assets
- Cash: opening balance + net cash flow each period
- Accounts Receivable: revenue × (collection_days / 30)
- Fixed Assets: any capex minus depreciation

### Liabilities
- Accounts Payable: COGS × (payment_days / 30)
- Debt: if any funding is debt-based
- Deferred Revenue: if subscription model with annual prepay

### Equity
- Paid-in Capital: funding raised
- Retained Earnings: cumulative net income

## Cash Flow Statement Structure

### Operating
- Net Income + Depreciation + Changes in Working Capital (AR, AP, Deferred Revenue)

### Investing
- Capex (negative)

### Financing
- Equity raised (positive)
- Debt drawn / repaid

### Net Cash Flow
- Operating + Investing + Financing
- Ending Cash = Beginning Cash + Net Cash Flow

## Linking Rules
- Balance Sheet must balance every period: Assets = Liabilities + Equity
- Cash on Balance Sheet must equal ending cash from Cash Flow Statement
- Retained Earnings change = Net Income from Income Statement
- Working capital changes flow from Balance Sheet deltas to Cash Flow Statement

## Startup-Specific Adjustments
- J-curve: expect negative cash flow months 1-N before break-even
- Hiring ramp: don't assume full headcount from month 1
- Revenue lag: apply sales_cycle_months before first revenue hits
- Seasonality: flag if business type suggests seasonal patterns (don't model unless specified)

## Output Format
Return as nested dict with keys: pl_monthly_year1, pl_annual_years2_3, balance_sheet, cash_flow
Each containing arrays of period objects with all line items.

"""FastMCP tools for invoices, expenses, and financial summaries."""

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, EmailStr, Field, ValidationError, field_validator

from .store import FinanceStore

mcp = FastMCP("Invoice and Expense Management")
store = FinanceStore()


class InvoiceInput(BaseModel):
    customer_name: str = Field(min_length=1, max_length=200)
    customer_email: EmailStr | None = None
    description: str = Field(min_length=1, max_length=2000)
    subtotal: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    tax: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=14, decimal_places=2)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    issue_date: date
    due_date: date
    notes: str | None = None

    @field_validator("due_date")
    @classmethod
    def due_date_not_before_issue(cls, value: date, info: Any) -> date:
        issue_date = info.data.get("issue_date")
        if issue_date and value < issue_date:
            raise ValueError("due_date must be on or after issue_date")
        return value


class ExpenseInput(BaseModel):
    description: str = Field(min_length=1, max_length=2000)
    category: str = Field(min_length=1, max_length=100)
    vendor: str | None = Field(default=None, max_length=200)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    expense_date: date
    receipt_reference: str | None = Field(default=None, max_length=300)


class PaymentInput(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)


@mcp.tool()
def create_invoice(customer_name: str, description: str, subtotal: Decimal,
                   issue_date: date, due_date: date, customer_email: str | None = None,
                   tax: Decimal = Decimal("0.00"), currency: str = "USD",
                   notes: str | None = None) -> dict[str, Any]:
    """Create an unpaid invoice; total is calculated as subtotal plus tax."""
    try:
        item = InvoiceInput(customer_name=customer_name, customer_email=customer_email,
                            description=description, subtotal=subtotal, tax=tax,
                            currency=currency, issue_date=issue_date, due_date=due_date, notes=notes)
        return store.create_invoice(**item.model_dump())
    except (ValidationError, ValueError, InvalidOperation) as exc:
        return {"error": str(exc)}


@mcp.tool()
def get_invoice(invoice_id: int | None = None, invoice_number: str | None = None) -> dict[str, Any]:
    """Retrieve an invoice by its database ID or generated invoice number."""
    if (invoice_id is None) == (invoice_number is None):
        return {"error": "Provide exactly one of invoice_id or invoice_number."}
    result = store.get_invoice(invoice_id, invoice_number)
    return result or {"error": "Invoice not found."}


@mcp.tool()
def record_invoice_payment(amount: Decimal, invoice_id: int | None = None,
                           invoice_number: str | None = None) -> dict[str, Any]:
    """Record a payment against an invoice and update its outstanding balance."""
    if (invoice_id is None) == (invoice_number is None):
        return {"error": "Provide exactly one of invoice_id or invoice_number."}
    try:
        payment = PaymentInput(amount=amount)
        result = store.record_invoice_payment(invoice_id=invoice_id,
                                              invoice_number=invoice_number,
                                              amount=payment.amount)
        return result or {"error": "Invoice not found."}
    except (ValidationError, ValueError, InvalidOperation) as exc:
        return {"error": str(exc)}


@mcp.tool()
def record_expense(description: str, category: str, amount: Decimal, expense_date: date,
                   vendor: str | None = None, currency: str = "USD",
                   receipt_reference: str | None = None) -> dict[str, Any]:
    """Record a business expense with a category and transaction date."""
    try:
        item = ExpenseInput(description=description, category=category, vendor=vendor,
                            amount=amount, currency=currency, expense_date=expense_date,
                            receipt_reference=receipt_reference)
        return store.record_expense(**item.model_dump())
    except (ValidationError, ValueError, InvalidOperation) as exc:
        return {"error": str(exc)}


@mcp.tool()
def list_unpaid_invoices(include_overdue_only: bool = False, as_of: date | None = None,
                         currency: str | None = None, limit: int = 100) -> list[dict[str, Any]] | dict[str, str]:
    """List unpaid invoices; optionally restrict to overdue invoices and currency."""
    if not 1 <= limit <= 500:
        return {"error": "limit must be between 1 and 500."}
    if currency is not None and (len(currency) != 3 or not currency.isalpha() or currency.upper() != currency):
        return {"error": "currency must be a three-letter uppercase code."}
    return store.list_unpaid_invoices(include_overdue_only=include_overdue_only,
                                      as_of=as_of or date.today(), currency=currency, limit=limit)


@mcp.tool()
def calculate_monthly_expenses(year: int, month: int, currency: str | None = None) -> dict[str, Any]:
    """Calculate recorded expense totals by currency and category for a calendar month."""
    if not 1 <= year <= 9999 or not 1 <= month <= 12:
        return {"error": "year must be 1–9999 and month must be 1–12."}
    if currency is not None and (len(currency) != 3 or not currency.isalpha() or currency.upper() != currency):
        return {"error": "currency must be a three-letter uppercase code."}
    return store.calculate_monthly_expenses(year=year, month=month, currency=currency)


@mcp.tool()
def generate_financial_report(start_date: date, end_date: date,
                              currency: str | None = None) -> dict[str, Any]:
    """Summarize invoices issued, payments received, outstanding amounts, and expenses by currency."""
    if start_date > end_date:
        return {"error": "start_date must be on or before end_date."}
    if currency is not None and (len(currency) != 3 or not currency.isalpha() or currency.upper() != currency):
        return {"error": "currency must be a three-letter uppercase code."}
    return store.generate_financial_report(start_date=start_date, end_date=end_date, currency=currency)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

"""SQLAlchemy persistence for invoices and expenses."""

import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, Integer, Numeric, String, Text, create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_number: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    customer_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    customer_email: Mapped[str | None] = mapped_column(String(320))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    tax: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD", index=True)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="unpaid", index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    vendor: Mapped[str | None] = mapped_column(String(200), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD", index=True)
    expense_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    receipt_reference: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


def make_engine(database_url: str | None = None) -> Engine:
    url = database_url or os.getenv("FINANCE_DATABASE_URL", "sqlite:///./finance.db")
    options: dict[str, Any] = {"pool_pre_ping": True}
    if url.startswith("sqlite:"):
        options["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **options)


class FinanceStore:
    def __init__(self, engine: Engine | None = None):
        self.engine = engine or make_engine()
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def create_invoice(self, *, customer_name: str, customer_email: str | None, description: str,
                       subtotal: Decimal, tax: Decimal, currency: str, issue_date: date,
                       due_date: date, notes: str | None) -> dict[str, Any]:
        number = f"INV-{issue_date:%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
        invoice = Invoice(invoice_number=number, customer_name=customer_name, customer_email=customer_email,
                          description=description, subtotal=subtotal, tax=tax, total=subtotal + tax,
                          currency=currency, issue_date=issue_date, due_date=due_date,
                          status="unpaid", notes=notes)
        with self.sessions.begin() as session:
            session.add(invoice)
            session.flush()
            return self._invoice_dict(invoice)

    def get_invoice(self, invoice_id: int | None, invoice_number: str | None) -> dict[str, Any] | None:
        with self.sessions() as session:
            invoice = session.get(Invoice, invoice_id) if invoice_id is not None else session.scalar(
                select(Invoice).where(Invoice.invoice_number == invoice_number)
            )
            return self._invoice_dict(invoice) if invoice else None

    def record_expense(self, *, description: str, category: str, vendor: str | None,
                       amount: Decimal, currency: str, expense_date: date,
                       receipt_reference: str | None) -> dict[str, Any]:
        expense = Expense(description=description, category=category, vendor=vendor, amount=amount,
                          currency=currency, expense_date=expense_date, receipt_reference=receipt_reference)
        with self.sessions.begin() as session:
            session.add(expense)
            session.flush()
            return self._expense_dict(expense)

    def list_unpaid_invoices(self, *, include_overdue_only: bool, as_of: date,
                             currency: str | None, limit: int) -> list[dict[str, Any]]:
        with self.sessions() as session:
            statement = select(Invoice).where(Invoice.status != "paid")
            if include_overdue_only:
                statement = statement.where(Invoice.due_date < as_of)
            if currency:
                statement = statement.where(Invoice.currency == currency)
            invoices = session.scalars(statement.order_by(Invoice.due_date, Invoice.id).limit(limit)).all()
            results = [self._invoice_dict(invoice) for invoice in invoices]
            for item in results:
                item["overdue"] = date.fromisoformat(item["due_date"]) < as_of
            return results

    def calculate_monthly_expenses(self, *, year: int, month: int, currency: str | None) -> dict[str, Any]:
        start = date(year, month, 1)
        end = date(year + (month == 12), (month % 12) + 1, 1)
        with self.sessions() as session:
            statement = select(Expense).where(Expense.expense_date >= start, Expense.expense_date < end)
            if currency:
                statement = statement.where(Expense.currency == currency)
            expenses = session.scalars(statement.order_by(Expense.expense_date, Expense.id)).all()
            totals: dict[str, Decimal] = {}
            categories: dict[str, dict[str, Decimal]] = {}
            for expense in expenses:
                totals[expense.currency] = totals.get(expense.currency, Decimal("0")) + expense.amount
                category_totals = categories.setdefault(expense.category, {})
                category_totals[expense.currency] = category_totals.get(expense.currency, Decimal("0")) + expense.amount
            return {
                "year": year, "month": month,
                "total_by_currency": {key: self._money(value) for key, value in sorted(totals.items())},
                "by_category": {name: {key: self._money(value) for key, value in sorted(values.items())}
                                for name, values in sorted(categories.items())},
                "expense_count": len(expenses),
            }

    def record_invoice_payment(self, *, invoice_id: int | None, invoice_number: str | None,
                               amount: Decimal) -> dict[str, Any] | None:
        with self.sessions.begin() as session:
            statement = select(Invoice).where(Invoice.id == invoice_id) if invoice_id is not None else select(Invoice).where(Invoice.invoice_number == invoice_number)
            invoice = session.scalar(statement)
            if invoice is None:
                return None
            balance = invoice.total - invoice.amount_paid
            if amount > balance:
                raise ValueError(f"Payment exceeds the outstanding balance of {self._money(balance)} {invoice.currency}.")
            invoice.amount_paid += amount
            invoice.status = "paid" if invoice.amount_paid == invoice.total else "partial"
            session.flush()
            return self._invoice_dict(invoice)

    def generate_financial_report(self, *, start_date: date, end_date: date,
                                  currency: str | None) -> dict[str, Any]:
        with self.sessions() as session:
            invoice_query = select(Invoice).where(Invoice.issue_date >= start_date, Invoice.issue_date <= end_date)
            expense_query = select(Expense).where(Expense.expense_date >= start_date, Expense.expense_date <= end_date)
            if currency:
                invoice_query = invoice_query.where(Invoice.currency == currency)
                expense_query = expense_query.where(Expense.currency == currency)
            invoices = session.scalars(invoice_query).all()
            expenses = session.scalars(expense_query).all()
            currency_codes = {row.currency for row in invoices} | {row.currency for row in expenses}
            by_currency: dict[str, Any] = {}
            for code in sorted(currency_codes):
                inv = [row for row in invoices if row.currency == code]
                exp = [row for row in expenses if row.currency == code]
                invoiced = sum((row.total for row in inv), Decimal("0"))
                outstanding = sum((row.total - row.amount_paid for row in inv), Decimal("0"))
                paid = sum((row.amount_paid for row in inv), Decimal("0"))
                spent = sum((row.amount for row in exp), Decimal("0"))
                by_currency[code] = {
                    "invoices_issued": len(inv), "expenses_recorded": len(exp),
                    "invoiced_total": self._money(invoiced),
                    "paid_total": self._money(paid),
                    "outstanding_total": self._money(outstanding),
                    "expenses_total": self._money(spent),
                    "net_billed_after_expenses": self._money(invoiced - spent),
                }
            return {"period": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
                    "by_currency": by_currency}

    @staticmethod
    def _money(value: Decimal | int | float) -> str:
        return f"{Decimal(value):.2f}"

    @classmethod
    def _invoice_dict(cls, invoice: Invoice) -> dict[str, Any]:
        return {"id": invoice.id, "invoice_number": invoice.invoice_number,
                "customer_name": invoice.customer_name, "customer_email": invoice.customer_email,
                "description": invoice.description, "subtotal": cls._money(invoice.subtotal),
                "tax": cls._money(invoice.tax), "total": cls._money(invoice.total),
                "amount_paid": cls._money(invoice.amount_paid),
                "currency": invoice.currency, "issue_date": invoice.issue_date.isoformat(),
                "due_date": invoice.due_date.isoformat(), "status": invoice.status,
                "notes": invoice.notes, "created_at": invoice.created_at.isoformat()}

    @classmethod
    def _expense_dict(cls, expense: Expense) -> dict[str, Any]:
        return {"id": expense.id, "description": expense.description,
                "category": expense.category, "vendor": expense.vendor,
                "amount": cls._money(expense.amount), "currency": expense.currency,
                "expense_date": expense.expense_date.isoformat(),
                "receipt_reference": expense.receipt_reference,
                "created_at": expense.created_at.isoformat()}

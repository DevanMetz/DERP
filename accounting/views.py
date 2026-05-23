from datetime import date as date_cls

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from core.permissions import write_required

from .forms import (
    BalanceSheetFilterForm, GLFilterForm, IncomeStatementFilterForm,
    JournalEntryHeaderForm, JournalLineFormSet, TrialBalanceFilterForm,
)
from .models import Account, JournalEntry, ZERO
from .reports import balance_sheet, general_ledger, income_statement, trial_balance
from .services import LineSpec, post_transaction


@login_required
def journal_list(request):
    from core.views import apply_filters
    from django.db.models import Q
    qs = JournalEntry.objects.all()

    status = request.GET.get("status", "")
    memo = request.GET.get("memo", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")

    if status:
        qs = qs.filter(status=status)
    if memo:
        qs = qs.filter(Q(memo__icontains=memo) | Q(number__icontains=memo))
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)

    SORT_FIELDS = ["date", "number", "status"]
    qs, sort, direction = apply_filters(qs, request, SORT_FIELDS)
    # Default limit if no sort applied
    if not sort:
        qs = qs.order_by("-date", "-id")[:200]

    active_filters = []
    if status:
        active_filters.append({"label": f"Status: {dict(JournalEntry.Status.choices).get(status, status)}", "remove": "status"})
    if memo:
        active_filters.append({"label": f"Search: {memo}", "remove": "memo"})
    if date_from:
        active_filters.append({"label": f"From: {date_from}", "remove": "date_from"})
    if date_to:
        active_filters.append({"label": f"To: {date_to}", "remove": "date_to"})

    return render(request, "accounting/journal_list.html", {
        "entries": qs,
        "status_choices": JournalEntry.Status.choices,
        "filters": {"status": status, "memo": memo, "date_from": date_from, "date_to": date_to},
        "active_filters": active_filters,
        "sort": sort,
        "dir": direction,
    })


@login_required
@write_required
def journal_create(request):
    if request.method == "POST":
        header = JournalEntryHeaderForm(request.POST)
        formset = JournalLineFormSet(request.POST)
        if header.is_valid() and formset.is_valid():
            specs = []
            for line in formset.cleaned_data:
                acct = line.get("account")
                d = line.get("debit") or ZERO
                c = line.get("credit") or ZERO
                if not acct or (d == ZERO and c == ZERO):
                    continue
                specs.append(LineSpec(
                    account_code=acct.code, debit=d, credit=c,
                    memo=line.get("memo", ""),
                ))
            try:
                entry = post_transaction(
                    date=header.cleaned_data["date"],
                    memo=header.cleaned_data.get("memo", ""),
                    lines=specs,
                    user=request.user,
                )
            except ValidationError as e:
                messages.error(request, "; ".join(e.messages))
            except ValueError as e:
                messages.error(request, str(e))
            else:
                messages.success(request, f"Posted {entry.number}.")
                return redirect("journal_detail", pk=entry.pk)
    else:
        header = JournalEntryHeaderForm(initial={"date": date_cls.today()})
        formset = JournalLineFormSet()
    return render(request, "accounting/journal_form.html", {
        "header": header, "formset": formset,
    })


@login_required
def journal_detail(request, pk):
    entry = JournalEntry.objects.prefetch_related("lines__account").get(pk=pk)
    is_reversed = JournalEntry.objects.filter(source_doc_type="JournalEntry.Reversal", source_doc_id=entry.pk).exists()
    is_reversal = entry.source_doc_type == "JournalEntry.Reversal"
    can_be_reversed = (entry.status == JournalEntry.Status.POSTED) and not is_reversed and not is_reversal
    return render(request, "accounting/journal_detail.html", {
        "entry": entry,
        "can_be_reversed": can_be_reversed,
    })


@login_required
@write_required
def journal_reverse(request, pk):
    entry = get_object_or_404(JournalEntry, pk=pk)
    if not request.user.can_void:
        messages.error(request, "Only Administrators can void/reverse journal entries.")
        return redirect("journal_detail", pk=pk)

    is_reversed = JournalEntry.objects.filter(source_doc_type="JournalEntry.Reversal", source_doc_id=entry.pk).exists()
    is_reversal = entry.source_doc_type == "JournalEntry.Reversal"
    if is_reversed or is_reversal or entry.status != JournalEntry.Status.POSTED:
        messages.error(request, "This journal entry cannot be reversed.")
        return redirect("journal_detail", pk=pk)

    if request.method == "POST":
        try:
            reversing_entry = reverse_entry(
                entry,
                date=date_cls.today(),
                memo=f"Reversal of {entry.number}",
                user=request.user,
            )
            messages.success(request, f"Successfully reversed {entry.number} with new entry {reversing_entry.number}.")
            return redirect("journal_detail", pk=reversing_entry.pk)
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        except ValueError as e:
            messages.error(request, str(e))

    return redirect("journal_detail", pk=pk)


@login_required
def trial_balance_view(request):
    form = TrialBalanceFilterForm(request.GET or {"as_of": date_cls.today().isoformat()})
    rows = []
    total_d = total_c = ZERO
    if form.is_valid():
        rows = trial_balance(as_of=form.cleaned_data["as_of"])
        total_d = sum((r.debit_total for r in rows), ZERO)
        total_c = sum((r.credit_total for r in rows), ZERO)
    return render(request, "accounting/trial_balance.html", {
        "form": form, "rows": rows, "total_d": total_d, "total_c": total_c,
    })


@login_required
def income_statement_view(request):
    today = date_cls.today()
    defaults = {"start": today.replace(month=1, day=1).isoformat(), "end": today.isoformat()}
    form = IncomeStatementFilterForm(request.GET or defaults)
    report = None
    if form.is_valid():
        report = income_statement(
            start=form.cleaned_data["start"],
            end=form.cleaned_data["end"],
        )
    return render(request, "accounting/income_statement.html", {
        "form": form, "report": report,
    })


@login_required
def balance_sheet_view(request):
    form = BalanceSheetFilterForm(request.GET or {"as_of": date_cls.today().isoformat()})
    report = None
    if form.is_valid():
        report = balance_sheet(as_of=form.cleaned_data["as_of"])
    return render(request, "accounting/balance_sheet.html", {
        "form": form, "report": report,
    })


@login_required
def general_ledger_view(request):
    data = request.GET.copy() if request.GET else None
    if data and "account" in data:
        if "start" not in data or not data["start"]:
            from django.utils import timezone
            import datetime
            data["start"] = datetime.date(timezone.now().year, 1, 1).strftime("%Y-%m-%d")
        if "end" not in data or not data["end"]:
            from django.utils import timezone
            data["end"] = timezone.now().date().strftime("%Y-%m-%d")

    form = GLFilterForm(data or None)
    lines = []
    account = None
    if form.is_valid():
        account = form.cleaned_data["account"]
        lines = general_ledger(
            account=account,
            start=form.cleaned_data["start"],
            end=form.cleaned_data["end"],
        )
    return render(request, "accounting/general_ledger.html", {
        "form": form, "lines": lines, "account": account,
    })

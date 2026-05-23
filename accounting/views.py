from datetime import date as date_cls

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from .forms import (
    GLFilterForm, JournalEntryHeaderForm, JournalLineFormSet,
    TrialBalanceFilterForm,
)
from .models import Account, JournalEntry, ZERO
from .reports import general_ledger, trial_balance
from .services import LineSpec, post_transaction


@login_required
def journal_list(request):
    entries = JournalEntry.objects.order_by("-date", "-id")[:100]
    return render(request, "accounting/journal_list.html", {"entries": entries})


@login_required
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
    return render(request, "accounting/journal_detail.html", {"entry": entry})


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
def general_ledger_view(request):
    form = GLFilterForm(request.GET or None)
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

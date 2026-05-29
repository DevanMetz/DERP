from django import forms
from django.contrib.auth import get_user_model
from .models import InspectionTemplate, InspectionFieldTemplate, QualityInspection, NonConformance, CAPA
from inventory.models import Product, Lot, Location

User = get_user_model()


class InspectionTemplateForm(forms.ModelForm):
    class Meta:
        model = InspectionTemplate
        fields = ["name", "product", "trigger_type", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g., Electronic Quality Check"}),
            "trigger_type": forms.Select(),
            "is_active": forms.CheckboxInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(is_active=True).order_by("sku")
        self.fields["product"].required = False


class InspectionFieldTemplateForm(forms.ModelForm):
    class Meta:
        model = InspectionFieldTemplate
        fields = ["name", "field_type", "min_value", "max_value", "is_required", "sort_order"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g., Operating Voltage"}),
            "field_type": forms.Select(),
            "min_value": forms.NumberInput(attrs={"step": "any", "placeholder": "Min (Optional)"}),
            "max_value": forms.NumberInput(attrs={"step": "any", "placeholder": "Max (Optional)"}),
            "is_required": forms.CheckboxInput(),
            "sort_order": forms.NumberInput(),
        }


class NonConformanceForm(forms.ModelForm):
    class Meta:
        model = NonConformance
        fields = [
            "title", "description", "severity", "status", "disposition",
            "lot", "location", "disposition_notes"
        ]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Brief summary of issue"}),
            "description": forms.Textarea(attrs={"rows": 4, "placeholder": "Describe the non-conformance in detail..."}),
            "severity": forms.Select(),
            "status": forms.Select(),
            "disposition": forms.Select(),
            "disposition_notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Notes explaining final disposition decision..."}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lot"].queryset = Lot.objects.all().order_by("product__sku", "lot_number")
        self.fields["lot"].required = False
        self.fields["location"].queryset = Location.objects.filter(is_active=True).order_by("name")
        self.fields["location"].required = False


class CAPAForm(forms.ModelForm):
    class Meta:
        model = CAPA
        fields = [
            "non_conformance", "title", "root_cause_analysis",
            "corrective_action", "preventive_action", "status",
            "assigned_to", "target_date"
        ]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Action Title"}),
            "root_cause_analysis": forms.Textarea(attrs={"rows": 3, "placeholder": "Root Cause Analysis (RCA)..."}),
            "corrective_action": forms.Textarea(attrs={"rows": 3, "placeholder": "Immediate corrective actions..."}),
            "preventive_action": forms.Textarea(attrs={"rows": 3, "placeholder": "Preventive actions to stop recurrence..."}),
            "status": forms.Select(),
            "target_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["non_conformance"].queryset = NonConformance.objects.exclude(
            status=NonConformance.Status.CLOSED
        ).order_by("-id")
        
        self.fields["assigned_to"].queryset = User.objects.filter(is_active=True).order_by("username")
        self.fields["assigned_to"].required = False

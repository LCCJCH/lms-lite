from django import forms
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone

from accounts.roles import APPLICANT, LAB_STAFF

from .models import Request, TestItem


class TestItemCheckboxes(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        label = f"{obj.name}（{obj.unit}）"
        if obj.description:
            label += f"－{obj.description}"
        if not obj.is_active:
            label += "［已停用］"
        return label


class RequestForm(forms.ModelForm):
    """申請者的申請表單；「至少一個項目」由送出服務檢查，存草稿可不選。"""

    test_items = TestItemCheckboxes(
        label="檢測項目",
        queryset=TestItem.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )

    class Meta:
        model = Request
        fields = ["sample_name", "sample_description", "note"]
        widgets = {
            "sample_description": forms.Textarea(attrs={"rows": 3, "placeholder": "外觀、數量、保存條件等"}),
            "note": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        selected = []
        if self.instance.pk:
            selected = list(self.instance.items.values_list("test_item_id", flat=True))
            self.initial.setdefault("test_items", selected)
        # 只列啟用的項目；已選而後被停用的項目仍保留（簡報 §4.1）
        self.fields["test_items"].queryset = TestItem.objects.filter(
            Q(is_active=True) | Q(pk__in=selected)
        )

    def service_data(self):
        return {f: self.cleaned_data[f] for f in self._meta.fields}

    def test_item_groups(self):
        """給 template 依分類分組輸出：[(分類名稱, [(項目, 核取方塊), ...], 是否有已勾選), ...]。

        queryset 已依分類排序，只需合併相鄰的同類項目。
        """
        groups = []
        for cb in self["test_items"]:
            item = cb.data["value"].instance
            label = item.get_category_display()
            if not groups or groups[-1][0] != label:
                groups.append((label, [], []))
            groups[-1][1].append((item, cb))
            groups[-1][2].append(cb.data["selected"])
        return [(label, rows, any(selected)) for label, rows, selected in groups]


class ApplicantHistoryFilterForm(forms.Form):
    """申請者歷史申請的篩選（GET）：狀態、關鍵字、送出日期區間。"""

    status = forms.ChoiceField(label="狀態", required=False, choices=[("", "全部")] + Request.Status.choices)
    q = forms.CharField(
        label="關鍵字",
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "單號或樣品名稱"}),
    )
    submitted_from = forms.DateField(
        label="送出日期（起）", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    submitted_to = forms.DateField(
        label="送出日期（迄）", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )

    def clean(self):
        data = super().clean()
        start, end = data.get("submitted_from"), data.get("submitted_to")
        if start and end and start > end:
            raise forms.ValidationError("送出日期的起日不可晚於迄日。")
        return data

    def filter(self, queryset):
        if not self.is_valid():
            return queryset
        data = self.cleaned_data
        if data["status"]:
            queryset = queryset.filter(status=data["status"])
        if data["q"]:
            q = data["q"].strip()
            queryset = queryset.filter(Q(request_no__icontains=q) | Q(sample_name__icontains=q))
        if data["submitted_from"]:
            queryset = queryset.filter(submitted_at__date__gte=data["submitted_from"])
        if data["submitted_to"]:
            queryset = queryset.filter(submitted_at__date__lte=data["submitted_to"])
        return queryset


# ---------- Admin ----------


class UserChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.first_name or obj.username


class ReturnForm(forms.Form):
    reason = forms.CharField(
        label="退回理由",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "請說明需要申請者修改的地方"}),
    )


class ScheduleForm(forms.Form):
    """排程與改排程共用；服務層會再驗證一次指派人員。"""

    scheduled_date = forms.DateField(label="預定實驗日", widget=forms.DateInput(attrs={"type": "date"}))
    assigned_staff = UserChoiceField(
        label="指派實驗人員",
        queryset=User.objects.filter(is_active=True, groups__name=LAB_STAFF).order_by("username"),
        empty_label="請選擇",
    )

    def clean_scheduled_date(self):
        value = self.cleaned_data["scheduled_date"]
        if value < timezone.localdate():
            raise forms.ValidationError("預定實驗日不可早於今天。")
        return value


class AdminRequestFilterForm(forms.Form):
    """Admin 全部申請單的篩選（GET）。草稿不列入（Alan 確認）。"""

    status = forms.ChoiceField(
        label="狀態",
        required=False,
        choices=[("", "全部")] + [c for c in Request.Status.choices if c[0] != Request.Status.DRAFT],
    )
    applicant = UserChoiceField(
        label="申請者",
        required=False,
        queryset=User.objects.filter(groups__name=APPLICANT).order_by("username"),
        empty_label="全部",
    )
    q = forms.CharField(
        label="關鍵字",
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "單號或樣品名稱"}),
    )

    def filter(self, queryset):
        if not self.is_valid():
            return queryset
        data = self.cleaned_data
        if data["status"]:
            queryset = queryset.filter(status=data["status"])
        if data["applicant"]:
            queryset = queryset.filter(applicant=data["applicant"])
        if data["q"]:
            q = data["q"].strip()
            queryset = queryset.filter(Q(request_no__icontains=q) | Q(sample_name__icontains=q))
        return queryset


# ---------- 實驗人員 ----------


class ResultForm(forms.Form):
    """結果輸入表：每個申請項目一列（結果值、備註），皆可留空暫存。"""

    def __init__(self, *args, items, **kwargs):
        super().__init__(*args, **kwargs)
        self.items = list(items)
        for item in self.items:
            self.fields[f"value_{item.pk}"] = forms.CharField(
                label=f"{item.test_item.name} 結果值",
                max_length=32,
                required=False,
                initial=item.result_value,
            )
            self.fields[f"note_{item.pk}"] = forms.CharField(
                label=f"{item.test_item.name} 備註",
                max_length=200,
                required=False,
                initial=item.result_note,
            )

    def rows(self):
        """給 template 逐列輸出：(項目, 結果值欄位, 備註欄位)。"""
        return [(item, self[f"value_{item.pk}"], self[f"note_{item.pk}"]) for item in self.items]

    def results(self):
        return {
            item.pk: (self.cleaned_data[f"value_{item.pk}"], self.cleaned_data[f"note_{item.pk}"])
            for item in self.items
        }

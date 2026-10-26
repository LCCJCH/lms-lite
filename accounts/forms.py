import os
import secrets

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group, User
from django.db import transaction

from .roles import APPLICANT


class RegisterForm(UserCreationForm):
    """申請者註冊（僅展示版）。沒有角色選項，註冊成功一律加入 Applicant。"""

    email = forms.EmailField(label="Email")
    first_name = forms.CharField(label="顯示名稱", max_length=150)
    invite_code = forms.CharField(label="邀請碼", max_length=100)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "first_name")

    def clean_invite_code(self):
        expected = os.environ.get("DEMO_INVITE_CODE", "")
        given = self.cleaned_data["invite_code"].strip()
        if not expected:
            raise forms.ValidationError("目前不開放註冊。")
        if not secrets.compare_digest(given.encode(), expected.encode()):
            raise forms.ValidationError("邀請碼不正確。")
        return given

    @transaction.atomic
    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_staff = False
        user.is_superuser = False
        user.save()
        group, _ = Group.objects.get_or_create(name=APPLICANT)
        user.groups.set([group])
        return user

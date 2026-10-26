"""角色判定（簡報 §3）。

角色以 Django Group 表示；superuser 視同 Admin。
同時屬於多個群組時，依 Admin > LabStaff > Applicant 的順序選擇。
"""

ADMIN = "Admin"
LAB_STAFF = "LabStaff"
APPLICANT = "Applicant"

ROLE_PRIORITY = (ADMIN, LAB_STAFF, APPLICANT)

ROLE_LABELS = {
    ADMIN: "Admin",
    LAB_STAFF: "實驗人員",
    APPLICANT: "申請者",
}

ROLE_DASHBOARD = {
    ADMIN: "lab:admin_dashboard",
    LAB_STAFF: "lab:lab_dashboard",
    APPLICANT: "lab:applicant_dashboard",
}


def get_role(user):
    """回傳使用者的角色名稱；未登入或不屬於任何角色群組時回傳 None。"""
    if not user.is_authenticated:
        return None
    if user.is_superuser:
        return ADMIN
    # 同一個 request 內重複呼叫時不重查資料庫
    if not hasattr(user, "_role_cache"):
        names = set(user.groups.values_list("name", flat=True))
        user._role_cache = next((r for r in ROLE_PRIORITY if r in names), None)
    return user._role_cache

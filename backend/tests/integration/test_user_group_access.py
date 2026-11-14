from __future__ import annotations

import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.main import app
from backend.src.models.identity import Group, GroupRole, Role, User, UserGroup, UserRole, UserStatus
from backend.src.models.rbac import PermissionAction, PermissionEffect, PermissionRule, ResourceType
from backend.src.services.auth.jwt_service import auth_service


@pytest.mark.asyncio
async def test_groups_and_user_access_lifecycle(test_db_session: AsyncSession) -> None:
    # 1. Create a superadmin user
    superadmin = User(
        id=uuid.uuid4(),
        username=f"super_{uuid.uuid4().hex[:6]}",
        email=f"super_{uuid.uuid4().hex[:6]}@test.com",
        full_name="Super Administrator",
        hashed_password=auth_service.hash_password("SuperSecret123!"),
        status=UserStatus.ACTIVE,
        is_superadmin=True,
    )
    test_db_session.add(superadmin)

    # 2. Create standard user
    member_user = User(
        id=uuid.uuid4(),
        username=f"member_{uuid.uuid4().hex[:6]}",
        email=f"member_{uuid.uuid4().hex[:6]}@test.com",
        full_name="Team Member",
        phone="09121234567",
        hashed_password=auth_service.hash_password("MemberPass123!"),
        status=UserStatus.ACTIVE,
        is_superadmin=False,
    )
    test_db_session.add(member_user)

    # 3. Create a custom role
    custom_role = Role(
        id=uuid.uuid4(),
        name=f"AnalyticsRole_{uuid.uuid4().hex[:6]}",
        description="Role for analytics team",
        is_system_role=False,
    )
    test_db_session.add(custom_role)
    await test_db_session.commit()

    token = auth_service.create_access_token({"sub": str(superadmin.id), "username": superadmin.username})
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 4. Permissions registry endpoint
        reg_resp = await client.get("/api/v1/roles/permissions-registry", headers=headers)
        assert reg_resp.status_code == 200
        reg_data = reg_resp.json()
        assert len(reg_data) >= 3

        # 5. Create Group
        group_name = f"DataOps_{uuid.uuid4().hex[:6]}"
        grp_create_resp = await client.post(
            "/api/v1/groups",
            headers=headers,
            json={
                "name": group_name,
                "description": "Data Operations team group",
                "is_active": True,
                "role_ids": [str(custom_role.id)],
                "user_ids": [str(member_user.id)],
            },
        )
        assert grp_create_resp.status_code == 201
        group_data = grp_create_resp.json()
        group_id = group_data["id"]
        assert group_data["name"] == group_name
        assert group_data["user_count"] == 1
        assert group_data["role_count"] == 1

        # 6. List Groups (paginated)
        list_grp_resp = await client.get("/api/v1/groups?page=1&page_size=10", headers=headers)
        assert list_grp_resp.status_code == 200
        paginated_groups = list_grp_resp.json()
        assert "items" in paginated_groups
        assert paginated_groups["total"] >= 1

        # 7. Get Group Members
        members_resp = await client.get(f"/api/v1/groups/{group_id}/members", headers=headers)
        assert members_resp.status_code == 200
        members = members_resp.json()
        assert len(members) == 1
        assert members[0]["username"] == member_user.username

        # 8. User details endpoint
        u_det_resp = await client.get(f"/api/v1/users/{member_user.id}/details", headers=headers)
        assert u_det_resp.status_code == 200
        u_det = u_det_resp.json()
        assert u_det["user"]["phone"] == "09121234567"
        assert len(u_det["groups_detail"]) == 1
        assert u_det["groups_detail"][0]["name"] == group_name

        # 9. Reset password
        reset_resp = await client.post(
            f"/api/v1/users/{member_user.id}/reset-password",
            headers=headers,
            json={"new_password": "NewSecurePassword#2026"},
        )
        assert reset_resp.status_code == 200

        # 10. Bulk assign direct permissions to user
        dummy_db_id = uuid.uuid4()
        bulk_perm_resp = await client.post(
            f"/api/v1/users/{member_user.id}/permissions/bulk",
            headers=headers,
            json={
                "permissions": [
                    {
                        "resource_type": "DATABASE",
                        "resource_id": str(dummy_db_id),
                        "action": "VIEW_METADATA",
                        "effect": "ALLOW",
                    },
                    {
                        "resource_type": "DATABASE",
                        "resource_id": str(dummy_db_id),
                        "action": "EDIT_DESCRIPTION",
                        "effect": "ALLOW",
                    },
                ]
            },
        )
        assert bulk_perm_resp.status_code == 200
        user_perms = bulk_perm_resp.json()
        assert len(user_perms) == 2

        # 11. Effective permissions
        eff_resp = await client.get(f"/api/v1/users/{member_user.id}/effective-permissions", headers=headers)
        assert eff_resp.status_code == 200
        eff_perms = eff_resp.json()
        assert isinstance(eff_perms, list)

        # 12. User activity logs
        act_resp = await client.get(f"/api/v1/users/{member_user.id}/activity", headers=headers)
        assert act_resp.status_code == 200
        activities = act_resp.json()
        assert isinstance(activities, list)
        assert len(activities) >= 1

        # 13. Update role permissions
        up_role_perm_resp = await client.put(
            f"/api/v1/roles/{custom_role.id}/permissions",
            headers=headers,
            json={
                "permissions": [
                    {
                        "resource_type": "GLOBAL",
                        "action": "VIEW_METADATA",
                        "effect": "ALLOW",
                    }
                ]
            },
        )
        assert up_role_perm_resp.status_code == 200

        # 14. Get role users
        role_users_resp = await client.get(f"/api/v1/roles/{custom_role.id}/users", headers=headers)
        assert role_users_resp.status_code == 200
        role_users = role_users_resp.json()
        # member_user is assigned via group!
        assert any(ru["id"] == str(member_user.id) for ru in role_users)

        # 15. Delete single direct permission
        rule_to_del = user_perms[0]["id"]
        del_rule_resp = await client.delete(f"/api/v1/users/{member_user.id}/permissions/{rule_to_del}", headers=headers)
        assert del_rule_resp.status_code == 204

        # 16. Verify 1 rule remaining
        get_perms_resp = await client.get(f"/api/v1/users/{member_user.id}/permissions", headers=headers)
        assert len(get_perms_resp.json()) == 1

        # 17. User pagination with search
        users_page_resp = await client.get(f"/api/v1/users?page=1&page_size=10&search={member_user.username}", headers=headers)
        assert users_page_resp.status_code == 200
        u_page = users_page_resp.json()
        assert u_page["total"] == 1
        assert u_page["items"][0]["username"] == member_user.username

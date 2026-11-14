import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { UserManagementService } from '../services/user-management.service';
import { RoleManagementService } from '../services/role-management.service';
import { GroupManagementService } from '../services/group-management.service';
import {
  DirectPermissionRule,
  EffectivePermissionRow,
  GroupItem,
  PhysicalDatabaseNode,
  RoleItem,
  UserActivityItem,
  UserCreateDto,
  UserDetailData,
  UserItem,
  UserUpdateDto,
} from '../models/access-control.models';

@Component({
  selector: 'app-user-management',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './user-management.component.html',
  styleUrls: ['./user-management.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class UserManagementComponent implements OnInit {
  private userService = inject(UserManagementService);
  private roleService = inject(RoleManagementService);
  private groupService = inject(GroupManagementService);

  // Users grid state
  users = signal<UserItem[]>([]);
  totalUsers = signal<number>(0);
  currentPage = signal<number>(1);
  pageSize = signal<number>(10);
  totalPages = signal<number>(1);
  searchQuery = signal<string>('');
  selectedRoleId = signal<string>('');
  selectedGroupId = signal<string>('');
  selectedStatus = signal<string>('');
  isLoading = signal<boolean>(false);
  errorMessage = signal<string | null>(null);
  successMessage = signal<string | null>(null);

  // Reference data
  allRoles = signal<RoleItem[]>([]);
  allGroups = signal<GroupItem[]>([]);

  // Selected User & Details Panel (6 Tabs)
  selectedUser = signal<UserItem | null>(null);
  activeTab = signal<'info' | 'roles' | 'groups' | 'databases' | 'effective' | 'activity'>('info');
  userDetails = signal<UserDetailData | null>(null);
  isLoadingDetails = signal<boolean>(false);

  // Tab 4: Database Access Tree
  physicalTree = signal<PhysicalDatabaseNode[]>([]);
  expandedNodes = signal<Set<string>>(new Set());
  // Mapping: `${resourceType}:${resourceId}` -> Set of actions (VIEW_METADATA, EDIT_DESCRIPTION, MANAGE_TAGS, MANAGE_CONNECTIONS)
  permissionDraft = signal<Map<string, Set<string>>>(new Map());
  isSavingPermissions = signal<boolean>(false);

  // Tab 5: Effective Permissions Matrix
  effectivePermissions = signal<EffectivePermissionRow[]>([]);
  effSearch = signal<string>('');
  effAction = signal<string>('');
  isLoadingEffective = signal<boolean>(false);

  // Tab 6: User Audit Activity
  userActivities = signal<UserActivityItem[]>([]);
  isLoadingActivity = signal<boolean>(false);

  // Modals state
  showCreateModal = signal<boolean>(false);
  showEditModal = signal<boolean>(false);
  showResetPasswordModal = signal<boolean>(false);

  // Create User Form
  newUser: UserCreateDto = {
    username: '',
    email: '',
    full_name: '',
    password: '',
    phone: '',
    is_superadmin: false,
    role_ids: [],
    group_ids: [],
  };

  // Edit User Form
  editUserDto: UserUpdateDto = {};

  // Reset Password Form
  newPassword = '';
  confirmPassword = '';
  passwordError = '';

  // Role / Group Quick Assignment in Tabs
  newRoleIdToAssign = '';
  newGroupIdToAssign = '';

  readonly permissionActions = [
    { key: 'VIEW_METADATA', label: 'مشاهده متاداده' },
    { key: 'EDIT_DESCRIPTION', label: 'ویرایش توضیحات' },
    { key: 'MANAGE_TAGS', label: 'مدیریت برچسب‌ها' },
    { key: 'MANAGE_CONNECTIONS', label: 'مدیریت اتصال' },
  ];

  readonly Math = Math;
  readonly Object = Object;

  ngOnInit(): void {
    this.loadUsers();
    this.loadReferenceData();
    this.loadPhysicalTree();
  }

  loadReferenceData(): void {
    this.roleService.getAllRolesList().subscribe({
      next: (roles) => this.allRoles.set(roles),
      error: () => {},
    });
    this.groupService.getGroups({}).subscribe({
      next: (res) => {
        const items = Array.isArray(res) ? res : res.items || [];
        this.allGroups.set(items);
      },
      error: () => {},
    });
  }

  loadPhysicalTree(): void {
    this.userService.getPhysicalTree().subscribe({
      next: (tree) => {
        this.physicalTree.set(tree);
        // Expand first connection by default
        if (tree.length > 0) {
          const s = new Set<string>();
          s.add(`db:${tree[0].id}`);
          this.expandedNodes.set(s);
        }
      },
      error: () => {},
    });
  }

  loadUsers(): void {
    this.isLoading.set(true);
    this.errorMessage.set(null);

    this.userService
      .getUsers({
        page: this.currentPage(),
        page_size: this.pageSize(),
        search: this.searchQuery() || undefined,
        role_id: this.selectedRoleId() || undefined,
        group_id: this.selectedGroupId() || undefined,
        status: this.selectedStatus() || undefined,
      })
      .subscribe({
        next: (resp) => {
          this.isLoading.set(false);
          if (Array.isArray(resp)) {
            this.users.set(resp);
            this.totalUsers.set(resp.length);
            this.totalPages.set(1);
          } else {
            this.users.set(resp.items);
            this.totalUsers.set(resp.total);
            this.totalPages.set(resp.total_pages);
          }

          // If currently selected user was reloaded, update reference
          const current = this.selectedUser();
          if (current) {
            const updated = this.users().find((u) => u.id === current.id);
            if (updated) {
              this.selectedUser.set(updated);
            }
          }
        },
        error: (err) => {
          this.isLoading.set(false);
          this.errorMessage.set(err?.error?.detail || 'خطا در بارگذاری لیست کاربران.');
        },
      });
  }

  onSearch(): void {
    this.currentPage.set(1);
    this.loadUsers();
  }

  onFilterChange(): void {
    this.currentPage.set(1);
    this.loadUsers();
  }

  goToPage(p: number): void {
    if (p >= 1 && p <= this.totalPages()) {
      this.currentPage.set(p);
      this.loadUsers();
    }
  }

  selectUser(user: UserItem): void {
    this.selectedUser.set(user);
    this.loadUserDetails(user.id);
    this.loadUserDirectPermissions(user.id);
    if (this.activeTab() === 'effective') {
      this.loadEffectivePermissions(user.id);
    } else if (this.activeTab() === 'activity') {
      this.loadUserActivity(user.id);
    }
  }

  switchTab(tab: 'info' | 'roles' | 'groups' | 'databases' | 'effective' | 'activity'): void {
    this.activeTab.set(tab);
    const user = this.selectedUser();
    if (!user) return;

    if (tab === 'effective') {
      this.loadEffectivePermissions(user.id);
    } else if (tab === 'activity') {
      this.loadUserActivity(user.id);
    } else if (tab === 'databases') {
      this.loadUserDirectPermissions(user.id);
    }
  }

  loadUserDetails(userId: string): void {
    this.isLoadingDetails.set(true);
    this.userService.getUserDetails(userId).subscribe({
      next: (details) => {
        this.userDetails.set(details);
        this.isLoadingDetails.set(false);
      },
      error: () => this.isLoadingDetails.set(false),
    });
  }

  loadUserDirectPermissions(userId: string): void {
    this.userService.getUserDirectPermissions(userId).subscribe({
      next: (rules) => {
        const draft = new Map<string, Set<string>>();
        for (const r of rules) {
          if (r.resource_id) {
            const key = `${r.resource_type}:${r.resource_id}`;
            if (!draft.has(key)) {
              draft.set(key, new Set());
            }
            draft.get(key)!.add(r.action);
          }
        }
        this.permissionDraft.set(draft);
      },
      error: () => {},
    });
  }

  loadEffectivePermissions(userId: string): void {
    this.isLoadingEffective.set(true);
    this.userService
      .getUserEffectivePermissions(userId, this.effSearch() || undefined, this.effAction() || undefined)
      .subscribe({
        next: (rows) => {
          this.effectivePermissions.set(rows);
          this.isLoadingEffective.set(false);
        },
        error: () => this.isLoadingEffective.set(false),
      });
  }

  loadUserActivity(userId: string): void {
    this.isLoadingActivity.set(true);
    this.userService.getUserActivity(userId, 50).subscribe({
      next: (logs) => {
        this.userActivities.set(logs);
        this.isLoadingActivity.set(false);
      },
      error: () => this.isLoadingActivity.set(false),
    });
  }

  // --- Tree & Checkboxes (Tab 4) ---
  toggleTreeNode(nodeKey: string): void {
    const current = new Set(this.expandedNodes());
    if (current.has(nodeKey)) {
      current.delete(nodeKey);
    } else {
      current.add(nodeKey);
    }
    this.expandedNodes.set(current);
  }

  isNodeExpanded(nodeKey: string): boolean {
    return this.expandedNodes().has(nodeKey);
  }

  hasPermission(resourceType: string, resourceId: string, action: string): boolean {
    const key = `${resourceType}:${resourceId}`;
    return this.permissionDraft().get(key)?.has(action) ?? false;
  }

  togglePermission(
    resourceType: 'DATABASE' | 'SCHEMA' | 'TABLE',
    resourceId: string,
    action: string,
    event: Event
  ): void {
    const checked = (event.target as HTMLInputElement).checked;
    const newDraft = new Map(this.permissionDraft());
    const key = `${resourceType}:${resourceId}`;

    if (!newDraft.has(key)) {
      newDraft.set(key, new Set());
    }
    const actions = new Set(newDraft.get(key));

    if (checked) {
      actions.add(action);
    } else {
      actions.delete(action);
    }
    newDraft.set(key, actions);

    // Cascading selection down to descendants if requested
    if (resourceType === 'DATABASE') {
      const conn = this.physicalTree().find((c) => c.id === resourceId);
      if (conn) {
        for (const sch of conn.schemas) {
          const schKey = `SCHEMA:${sch.id}`;
          if (!newDraft.has(schKey)) newDraft.set(schKey, new Set());
          const sAct = new Set(newDraft.get(schKey));
          if (checked) sAct.add(action);
          else sAct.delete(action);
          newDraft.set(schKey, sAct);

          for (const tbl of sch.tables) {
            const tblKey = `TABLE:${tbl.id}`;
            if (!newDraft.has(tblKey)) newDraft.set(tblKey, new Set());
            const tAct = new Set(newDraft.get(tblKey));
            if (checked) tAct.add(action);
            else tAct.delete(action);
            newDraft.set(tblKey, tAct);
          }
        }
      }
    } else if (resourceType === 'SCHEMA') {
      for (const conn of this.physicalTree()) {
        const sch = conn.schemas.find((s) => s.id === resourceId);
        if (sch) {
          for (const tbl of sch.tables) {
            const tblKey = `TABLE:${tbl.id}`;
            if (!newDraft.has(tblKey)) newDraft.set(tblKey, new Set());
            const tAct = new Set(newDraft.get(tblKey));
            if (checked) tAct.add(action);
            else tAct.delete(action);
            newDraft.set(tblKey, tAct);
          }
          break;
        }
      }
    }

    this.permissionDraft.set(newDraft);
  }

  saveDatabasePermissions(): void {
    const user = this.selectedUser();
    if (!user) return;

    this.isSavingPermissions.set(true);
    const permissions: { resource_type: string; resource_id: string; action: string; effect: string }[] = [];

    this.permissionDraft().forEach((actions, key) => {
      const [rType, rId] = key.split(':');
      actions.forEach((act) => {
        permissions.push({
          resource_type: rType,
          resource_id: rId,
          action: act,
          effect: 'ALLOW',
        });
      });
    });

    this.userService.bulkUpdateUserPermissions(user.id, permissions).subscribe({
      next: () => {
        this.isSavingPermissions.set(false);
        this.showSuccess('تغییرات دسترسی با موفقیت در پایگاه‌داده ذخیره شد.');
        this.loadUserDetails(user.id);
        this.loadEffectivePermissions(user.id);
      },
      error: (err) => {
        this.isSavingPermissions.set(false);
        this.showError(err?.error?.detail || 'خطا در ذخیره دسترسی‌ها.');
      },
    });
  }

  // --- User CRUD & Status Actions ---
  toggleStatus(user: UserItem): void {
    const nextStatus = user.status === 'ACTIVE' ? 'SUSPENDED' : 'ACTIVE';
    this.userService.updateUserStatus(user.id, nextStatus).subscribe({
      next: (up) => {
        this.showSuccess(`وضعیت کاربر «${user.full_name}» به ${nextStatus === 'ACTIVE' ? 'فعال' : 'معلق'} تغییر یافت.`);
        this.loadUsers();
      },
      error: (err) => this.showError(err?.error?.detail || 'خطا در تغییر وضعیت کاربر.'),
    });
  }

  openCreateModal(): void {
    this.newUser = {
      username: '',
      email: '',
      full_name: '',
      password: '',
      phone: '',
      is_superadmin: false,
      role_ids: [],
      group_ids: [],
    };
    this.showCreateModal.set(true);
  }

  closeCreateModal(): void {
    this.showCreateModal.set(false);
  }

  submitCreateUser(): void {
    if (!this.newUser.username || !this.newUser.email || !this.newUser.full_name || !this.newUser.password) {
      this.showError('لطفاً کلیه فیلدهای الزامی را تکمیل فرمایید.');
      return;
    }
    if (this.newUser.password.length < 8) {
      this.showError('کلمه عبور باید حداقل شامل ۸ کاراکتر باشد.');
      return;
    }

    this.userService.createUser(this.newUser).subscribe({
      next: (created) => {
        this.showSuccess(`کاربر «${created.full_name}» با موفقیت تعریف شد.`);
        this.closeCreateModal();
        this.loadUsers();
        this.selectUser(created);
      },
      error: (err) => this.showError(err?.error?.detail || 'خطا در ایجاد کاربر جدید.'),
    });
  }

  openEditModal(user: UserItem): void {
    this.selectedUser.set(user);
    this.editUserDto = {
      full_name: user.full_name,
      email: user.email,
      phone: user.phone || '',
      status: user.status as any,
      is_superadmin: user.is_superadmin,
      role_ids: [...user.role_ids],
      group_ids: [...user.group_ids],
    };
    this.showEditModal.set(true);
  }

  closeEditModal(): void {
    this.showEditModal.set(false);
  }

  submitEditUser(): void {
    const user = this.selectedUser();
    if (!user) return;

    this.userService.updateUser(user.id, this.editUserDto).subscribe({
      next: (updated) => {
        this.showSuccess(`اطلاعات کاربر «${updated.full_name}» با موفقیت به‌روزرسانی شد.`);
        this.closeEditModal();
        this.loadUsers();
        this.selectUser(updated);
      },
      error: (err) => this.showError(err?.error?.detail || 'خطا در ویرایش اطلاعات کاربر.'),
    });
  }

  openResetPasswordModal(user: UserItem): void {
    this.selectedUser.set(user);
    this.newPassword = '';
    this.confirmPassword = '';
    this.passwordError = '';
    this.showResetPasswordModal.set(true);
  }

  closeResetPasswordModal(): void {
    this.showResetPasswordModal.set(false);
  }

  submitResetPassword(): void {
    this.passwordError = '';
    if (!this.newPassword || this.newPassword.length < 8) {
      this.passwordError = 'کلمه عبور باید حداقل ۸ کاراکتر باشد.';
      return;
    }
    if (this.newPassword !== this.confirmPassword) {
      this.passwordError = 'کلمه عبور و تکرار آن یکسان نیستند.';
      return;
    }

    const user = this.selectedUser();
    if (!user) return;

    this.userService.resetPassword(user.id, this.newPassword).subscribe({
      next: () => {
        this.showSuccess(`کلمه عبور کاربر «${user.full_name}» با موفقیت تغییر یافت.`);
        this.closeResetPasswordModal();
      },
      error: (err) => this.showError(err?.error?.detail || 'خطا در تغییر کلمه عبور.'),
    });
  }

  deleteUser(user: UserItem): void {
    if (!confirm(`آیا از حذف کامل حساب کاربری «${user.full_name} (${user.username})» اطمینان دارید؟`)) {
      return;
    }

    this.userService.deleteUser(user.id).subscribe({
      next: () => {
        this.showSuccess(`حساب کاربری «${user.username}» حذف گردید.`);
        if (this.selectedUser()?.id === user.id) {
          this.selectedUser.set(null);
        }
        this.loadUsers();
      },
      error: (err) => this.showError(err?.error?.detail || 'خطا در حذف کاربر.'),
    });
  }

  // --- Role & Group Tab Operations ---
  assignRoleToUser(): void {
    const user = this.selectedUser();
    if (!user || !this.newRoleIdToAssign) return;

    const currentRoles = new Set(user.role_ids);
    currentRoles.add(this.newRoleIdToAssign);

    this.userService.updateUser(user.id, { role_ids: Array.from(currentRoles) }).subscribe({
      next: (up) => {
        this.newRoleIdToAssign = '';
        this.showSuccess('نقش جدید به کاربر افزوده شد.');
        this.selectUser(up);
        this.loadUsers();
      },
      error: (err) => this.showError(err?.error?.detail || 'خطا در انتساب نقش.'),
    });
  }

  removeRoleFromUser(roleId: string): void {
    const user = this.selectedUser();
    if (!user) return;

    const currentRoles = user.role_ids.filter((r) => r !== roleId);
    this.userService.updateUser(user.id, { role_ids: currentRoles }).subscribe({
      next: (up) => {
        this.showSuccess('نقش از کاربر حذف گردید.');
        this.selectUser(up);
        this.loadUsers();
      },
      error: (err) => this.showError(err?.error?.detail || 'خطا در حذف نقش.'),
    });
  }

  assignGroupToUser(): void {
    const user = this.selectedUser();
    if (!user || !this.newGroupIdToAssign) return;

    const currentGroups = new Set(user.group_ids);
    currentGroups.add(this.newGroupIdToAssign);

    this.userService.updateUserGroups(user.id, Array.from(currentGroups)).subscribe({
      next: () => {
        this.newGroupIdToAssign = '';
        this.showSuccess('کاربر به گروه اضافه شد.');
        this.loadUserDetails(user.id);
        this.loadUsers();
      },
      error: (err) => this.showError(err?.error?.detail || 'خطا در عضویت در گروه.'),
    });
  }

  removeGroupFromUser(groupId: string): void {
    const user = this.selectedUser();
    if (!user) return;

    const currentGroups = user.group_ids.filter((g) => g !== groupId);
    this.userService.updateUserGroups(user.id, currentGroups).subscribe({
      next: () => {
        this.showSuccess('عضویت کاربر در گروه لغو شد.');
        this.loadUserDetails(user.id);
        this.loadUsers();
      },
      error: (err) => this.showError(err?.error?.detail || 'خطا در لغو عضویت گروه.'),
    });
  }

  // --- Notification helpers ---
  showSuccess(msg: string): void {
    this.successMessage.set(msg);
    setTimeout(() => this.successMessage.set(null), 4000);
  }

  showError(msg: string): void {
    this.errorMessage.set(msg);
    setTimeout(() => this.errorMessage.set(null), 5000);
  }

  formatDate(dateStr?: string | null): string {
    if (!dateStr) return '—';
    try {
      const d = new Date(dateStr);
      return new Intl.DateTimeFormat('fa-IR', {
        dateStyle: 'medium',
        timeStyle: 'short',
      }).format(d);
    } catch {
      return dateStr;
    }
  }

  getSourceBadgeClass(sourceType: string): string {
    switch (sourceType) {
      case 'SUPERADMIN':
        return 'badge-superadmin';
      case 'DIRECT_USER':
        return 'badge-direct';
      case 'ROLE':
        return 'badge-role';
      case 'GROUP':
        return 'badge-group';
      case 'PARENT_RESOURCE':
        return 'badge-parent';
      case 'CATEGORY':
        return 'badge-category';
      default:
        return 'badge-deny';
    }
  }
}

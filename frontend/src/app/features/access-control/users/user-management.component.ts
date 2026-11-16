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
import { RouterModule } from '@angular/router';
import { UserManagementService } from '../services/user-management.service';
import { RoleManagementService } from '../services/role-management.service';
import {
  PhysicalDatabaseNode,
  PhysicalSchemaNode,
  RoleItem,
  UserCreateDto,
  UserItem,
  UserUpdateDto,
} from '../models/access-control.models';

export function splitFullName(fullName: string): { firstName: string; lastName: string } {
  const parts = (fullName || '').trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return { firstName: '', lastName: '' };
  if (parts.length === 1) return { firstName: parts[0], lastName: '' };
  if (parts.length === 2) return { firstName: parts[0], lastName: parts[1] };
  if (parts.length === 3) {
    return { firstName: `${parts[0]} ${parts[1]}`, lastName: parts[2] };
  }
  return {
    firstName: `${parts[0]} ${parts[1]}`,
    lastName: parts.slice(2).join(' '),
  };
}

export interface UserRowViewModel {
  user: UserItem;
  firstName: string;
  lastName: string;
}

@Component({
  selector: 'app-user-management',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './user-management.component.html',
  styleUrls: ['./user-management.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class UserManagementComponent implements OnInit {
  private userService = inject(UserManagementService);
  private roleService = inject(RoleManagementService);

  // Users grid state
  rawUsers = signal<UserItem[]>([]);
  totalUsers = signal<number>(0);
  currentPage = signal<number>(1);
  pageSize = signal<number>(5);
  availablePageSizes = [5, 10, 20, 50];

  // Search & Filter Toolbar
  searchTerm = signal<string>('');
  filterStatus = signal<string>('ALL');

  // Column search filters (legacy / direct signals)
  filterFirstName = signal<string>('');
  filterLastName = signal<string>('');
  filterUsername = signal<string>('');
  filterEmail = signal<string>('');

  isLoading = signal<boolean>(false);
  isSaving = signal<boolean>(false);
  errorMessage = signal<string | null>(null);
  successMessage = signal<string | null>(null);

  // Available roles for selection
  allRoles = signal<RoleItem[]>([]);
  selectedRoleToAdd = signal<string>('');

  // Category & Physical Databases Tree
  categoryTree = signal<any[]>([]);
  allConnections = signal<any[]>([]);
  physicalTree = signal<PhysicalDatabaseNode[]>([]);
  expandedDbNodes = signal<Set<string>>(new Set());
  expandedCategoryIds = signal<Set<string>>(new Set());

  // Virtual branch for uncategorized databases
  displayCategoryTree = computed(() => {
    const tree = this.categoryTree();
    const conns = this.allConnections().length > 0
      ? this.allConnections()
      : this.physicalTree();
    if (!conns || conns.length === 0) return tree;

    const assignedDbIds = new Set<string>();
    const collectAssigned = (nodes: any[]) => {
      for (const node of nodes) {
        if (node.id === 'uncategorized') continue;
        if (node.databases && Array.isArray(node.databases)) {
          for (const db of node.databases) {
            if (db?.id) assignedDbIds.add(db.id);
          }
        }
        if (node.children && Array.isArray(node.children)) {
          collectAssigned(node.children);
        }
      }
    };
    collectAssigned(tree);

    const uncategorizedDbs = conns
      .filter((c: any) => !assignedDbIds.has(c.id))
      .map((c: any) => ({
        id: c.id,
        name: c.name,
        database_name: c.database_name,
        engine_type: c.engine_type,
      }));

    if (uncategorizedDbs.length > 0) {
      const uncategorizedNode = {
        id: 'uncategorized',
        name: 'پایگاه‌های داده دسته‌بندی‌نشده (Uncategorized)',
        description: 'پایگاه‌های داده‌ای که به هیچ موضوعی انتساب داده نشده‌اند',
        databases: uncategorizedDbs,
        children: [],
      };
      return [...tree, uncategorizedNode];
    }

    return tree;
  });

  // Sub-tabs in bottom detail panel
  activeDetailTab = signal<'databases' | 'permissions'>('databases');

  // Direct 8-Module Administrative Permissions
  readonly directPermissionDefs = [
    { action: 'MANAGE_CATEGORIES', label: 'مدیریت موضوعات و دسته‌بندی‌ها', desc: 'تعریف، ویرایش و حذف ساختار درختی موضوعات' },
    { action: 'MANAGE_CONNECTIONS', label: 'مدیریت پایگاه‌های داده', desc: 'افزودن و ویرایش اطلاعات اتصال پایگاه‌های داده' },
    { action: 'MANAGE_USERS', label: 'مدیریت کاربران', desc: 'افزودن، ویرایش و غیرفعال‌سازی حساب‌های کاربری' },
    { action: 'MANAGE_ROLES', label: 'مدیریت نقش‌ها و مجوزها', desc: 'تعریف و ویرایش نقش‌های سازمانی و انتساب مجوزها' },
    { action: 'MANAGE_SCHEDULES', label: 'مدیریت زمان‌بندی کاتالوگ', desc: 'تنظیم الگوهای کرون و اجرای همگام‌سازی بلادرنگ' },
    { action: 'VIEW_AUDIT_LOGS', label: 'مشاهده لاگ‌های ممیزی', desc: 'مشاهده تاریخچه ورود، تغییرات امنیتی و رخدادها' },
    { action: 'EXPORT_REPORTS', label: 'گزارش‌گیری کاتالوگ داده', desc: 'خروجی اکسل و JSON از متاداده پایگاه‌های داده' },
    { action: 'EDIT_DESCRIPTION', label: 'ویرایش توضیحات و هوش مصنوعی', desc: 'ویرایش توضیحات جداول/ستون‌ها و تایید پیشنهادهای هوش مصنوعی' },
  ];
  userDirectPermissions = signal<Set<string>>(new Set());

  // Master-Detail selection & Form state
  selectedUser = signal<UserItem | null>(null);
  isCreateMode = signal<boolean>(false);
  isEditMode = signal<boolean>(false);
  isFormEditable = computed<boolean>(() => this.isCreateMode() || this.isEditMode());

  // Form Fields
  formFirstName = signal<string>('');
  formLastName = signal<string>('');
  formEmail = signal<string>('');
  formUsername = signal<string>('');
  formPhone = signal<string>('');
  formPassword = signal<string>('');
  formSelectedRoleIds = signal<string[]>([]);
  formSelectedDatabaseIds = signal<Set<string>>(new Set());

  // Filtered and Paginated View Models
  filteredUsers = computed<UserRowViewModel[]>(() => {
    const list = this.rawUsers() || [];
    const search = this.searchTerm().trim().toLowerCase();
    const status = this.filterStatus();
    const fnFilter = this.filterFirstName().trim().toLowerCase();
    const lnFilter = this.filterLastName().trim().toLowerCase();
    const unFilter = this.filterUsername().trim().toLowerCase();
    const emFilter = this.filterEmail().trim().toLowerCase();

    return list
      .map((u) => {
        const { firstName, lastName } = splitFullName(u.full_name);
        return { user: u, firstName, lastName };
      })
      .filter((row) => {
        if (status && status !== 'ALL' && row.user.status !== status) {
          return false;
        }
        if (search) {
          const matchSearch =
            (row.firstName || '').toLowerCase().includes(search) ||
            (row.lastName || '').toLowerCase().includes(search) ||
            (row.user.username || '').toLowerCase().includes(search) ||
            (row.user.email || '').toLowerCase().includes(search) ||
            (row.user.full_name || '').toLowerCase().includes(search);
          if (!matchSearch) return false;
        }
        if (fnFilter && !(row.firstName || '').toLowerCase().includes(fnFilter)) return false;
        if (lnFilter && !(row.lastName || '').toLowerCase().includes(lnFilter)) return false;
        if (unFilter && !(row.user.username || '').toLowerCase().includes(unFilter)) return false;
        if (emFilter && !(row.user.email || '').toLowerCase().includes(emFilter)) return false;
        return true;
      });
  });

  paginatedUsers = computed<UserRowViewModel[]>(() => {
    const all = this.filteredUsers();
    const start = (this.currentPage() - 1) * this.pageSize();
    return all.slice(start, start + this.pageSize());
  });

  totalPages = computed<number>(() => {
    const count = this.filteredUsers().length;
    return Math.max(1, Math.ceil(count / this.pageSize()));
  });

  pageDisplayStart = computed<number>(() => {
    if (this.filteredUsers().length === 0) return 0;
    return (this.currentPage() - 1) * this.pageSize() + 1;
  });

  pageDisplayEnd = computed<number>(() => {
    return Math.min(this.currentPage() * this.pageSize(), this.filteredUsers().length);
  });

  readonly Math = Math;

  ngOnInit(): void {
    this.loadUsers();
    this.loadRoles();
    this.loadPhysicalTree();
  }

  loadUsers(): void {
    this.isLoading.set(true);
    this.errorMessage.set(null);

    this.userService.getUsers({ page: 1, page_size: 1000 }).subscribe({
      next: (resp) => {
        this.isLoading.set(false);
        const items = Array.isArray(resp) ? resp : resp.items || [];
        this.rawUsers.set(items);
        this.totalUsers.set(items.length);

        // If a user was previously selected, refresh selection
        const current = this.selectedUser();
        if (current) {
          const fresh = items.find((u) => u.id === current.id);
          if (fresh) {
            this.selectUser(fresh);
          }
        } else if (items.length > 0 && !this.isCreateMode()) {
          this.selectUser(items[0]);
        }
      },
      error: (err) => {
        this.isLoading.set(false);
        this.showError(err?.error?.detail || 'خطا در بارگذاری لیست کاربران.');
      },
    });
  }

  loadRoles(): void {
    this.roleService.getAllRolesList().subscribe({
      next: (roles) => this.allRoles.set(roles),
      error: () => {},
    });
  }

  loadPhysicalTree(): void {
    this.userService.getCategoryTree().subscribe({
      next: (tree) => {
        this.categoryTree.set(tree || []);
        const rootIds = new Set<string>();
        rootIds.add('uncategorized');
        const collectIds = (nodes: any[]) => {
          for (const n of nodes) {
            rootIds.add(n.id);
            if (n.children && n.children.length > 0) {
              collectIds(n.children);
            }
          }
        };
        collectIds(tree || []);
        this.expandedCategoryIds.set(rootIds);
      },
      error: () => {},
    });

    this.userService.getAllConnections().subscribe({
      next: (conns) => this.allConnections.set(conns || []),
      error: () => {},
    });

    this.userService.getPhysicalTree().subscribe({
      next: (tree) => {
        this.physicalTree.set(tree);
        const initial = new Set<string>();
        tree.forEach((db) => initial.add(`db:${db.id}`));
        this.expandedDbNodes.set(initial);
      },
      error: () => {},
    });
  }

  toggleCategory(catId: string): void {
    const current = new Set(this.expandedCategoryIds());
    if (current.has(catId)) {
      current.delete(catId);
    } else {
      current.add(catId);
    }
    this.expandedCategoryIds.set(current);
  }

  toggleDirectPermission(action: string, event: Event): void {
    if (!this.isFormEditable()) return;
    const checked = (event.target as HTMLInputElement).checked;
    const current = new Set(this.userDirectPermissions());
    if (checked) {
      current.add(action);
    } else {
      current.delete(action);
    }
    this.userDirectPermissions.set(current);
  }

  selectUser(user: UserItem): void {
    this.isCreateMode.set(false);
    this.isEditMode.set(false);
    this.selectedUser.set(user);
    this.populateFormWithUser(user);
  }

  startEditUser(user: UserItem, event?: Event): void {
    if (event) {
      event.stopPropagation();
    }
    this.selectUser(user);
    this.isEditMode.set(true);
  }

  enableEditMode(): void {
    if (this.selectedUser()) {
      this.isEditMode.set(true);
    }
  }

  private populateFormWithUser(user: UserItem): void {
    const { firstName, lastName } = splitFullName(user.full_name);
    this.formFirstName.set(firstName);
    this.formLastName.set(lastName);
    this.formEmail.set(user.email);
    this.formUsername.set(user.username);
    this.formPhone.set(user.phone || '');
    this.formPassword.set('');
    this.formSelectedRoleIds.set([...(user.role_ids || [])]);

    // Load assigned databases for this user
    this.userService.getUserAssignedDatabases(user.id).subscribe({
      next: (dbIds) => {
        this.formSelectedDatabaseIds.set(new Set(dbIds));
      },
      error: () => {
        this.formSelectedDatabaseIds.set(new Set());
      },
    });

    // Load direct permissions for this user
    this.userService.getUserDirectPermissions(user.id).subscribe({
      next: (rules) => {
        const perms = new Set<string>();
        rules
          .filter((r) => r.resource_type === 'GLOBAL')
          .forEach((r) => perms.add(r.action));
        this.userDirectPermissions.set(perms);
      },
      error: () => {
        this.userDirectPermissions.set(new Set());
      },
    });
  }

  startCreateNewUser(): void {
    this.isCreateMode.set(true);
    this.isEditMode.set(false);
    this.selectedUser.set(null);
    this.formFirstName.set('');
    this.formLastName.set('');
    this.formEmail.set('');
    this.formUsername.set('');
    this.formPhone.set('');
    this.formPassword.set('');
    this.formSelectedRoleIds.set([]);
    this.formSelectedDatabaseIds.set(new Set());
    this.userDirectPermissions.set(new Set());
    this.activeDetailTab.set('databases');
  }

  generateRandomPassword(): void {
    const chars = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789!@#$%';
    let pwd = '';
    for (let i = 0; i < 10; i++) {
      pwd += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    this.formPassword.set(pwd);
  }

  cancelDetailEdit(): void {
    this.isEditMode.set(false);
    if (this.isCreateMode()) {
      this.isCreateMode.set(false);
      if (this.rawUsers().length > 0) {
        this.selectUser(this.rawUsers()[0]);
      } else {
        this.selectedUser.set(null);
      }
    } else if (this.selectedUser()) {
      this.populateFormWithUser(this.selectedUser()!);
    }
  }

  onFilterChanged(): void {
    this.currentPage.set(1);
  }

  goToPage(p: number): void {
    if (p >= 1 && p <= this.totalPages()) {
      this.currentPage.set(p);
    }
  }

  changePageSize(newSize: number): void {
    this.pageSize.set(newSize);
    this.currentPage.set(1);
  }

  // --- Role Management in Form ---
  addRoleToUser(roleId: string): void {
    if (!this.isFormEditable() || !roleId) return;
    const current = new Set(this.formSelectedRoleIds());
    current.add(roleId);
    this.formSelectedRoleIds.set(Array.from(current));
    this.selectedRoleToAdd.set('');
  }

  removeRoleFromUser(roleId: string): void {
    if (!this.isFormEditable()) return;
    const updated = this.formSelectedRoleIds().filter((id) => id !== roleId);
    this.formSelectedRoleIds.set(updated);
  }

  getRoleName(roleId: string): string {
    const role = this.allRoles().find((r) => r.id === roleId);
    return role ? (role.description || role.name) : roleId;
  }

  getAvailableRolesToAdd(): RoleItem[] {
    const assigned = new Set(this.formSelectedRoleIds());
    return this.allRoles().filter((r) => !assigned.has(r.id));
  }

  // --- Database Tree Toggle & Checkbox Actions ---
  toggleNodeExpanded(key: string): void {
    const current = new Set(this.expandedDbNodes());
    if (current.has(key)) {
      current.delete(key);
    } else {
      current.add(key);
    }
    this.expandedDbNodes.set(current);
  }

  isNodeExpanded(key: string): boolean {
    return this.expandedDbNodes().has(key);
  }

  isDatabaseSelected(dbId: string): boolean {
    return this.formSelectedDatabaseIds().has(dbId);
  }

  toggleDatabaseSelection(dbId: string, event: Event): void {
    if (!this.isFormEditable()) return;
    const checked = (event.target as HTMLInputElement).checked;
    const updated = new Set(this.formSelectedDatabaseIds());
    if (checked) {
      updated.add(dbId);
    } else {
      updated.delete(dbId);
    }
    this.formSelectedDatabaseIds.set(updated);
  }

  getDescendantDatabaseIds(cat: any): string[] {
    const ids: string[] = [];
    const collect = (node: any) => {
      if (!node) return;
      if (node.databases && Array.isArray(node.databases)) {
        for (const db of node.databases) {
          if (db && db.id) {
            ids.push(db.id);
          }
        }
      }
      if (node.children && Array.isArray(node.children)) {
        for (const child of node.children) {
          collect(child);
        }
      }
    };
    collect(cat);
    return ids;
  }

  isCategoryChecked(cat: any): boolean {
    const dbIds = this.getDescendantDatabaseIds(cat);
    if (dbIds.length === 0) return false;
    const selected = this.formSelectedDatabaseIds();
    return dbIds.every((id) => selected.has(id));
  }

  isCategoryIndeterminate(cat: any): boolean {
    const dbIds = this.getDescendantDatabaseIds(cat);
    if (dbIds.length === 0) return false;
    const selected = this.formSelectedDatabaseIds();
    let selectedCount = 0;
    for (const id of dbIds) {
      if (selected.has(id)) {
        selectedCount++;
      }
    }
    return selectedCount > 0 && selectedCount < dbIds.length;
  }

  toggleCategorySelection(cat: any, event: Event): void {
    event.stopPropagation();
    if (!this.isFormEditable()) return;
    const target = event.target as HTMLInputElement;
    const checked = target.checked;
    const dbIds = this.getDescendantDatabaseIds(cat);
    if (dbIds.length === 0) return;

    const updated = new Set(this.formSelectedDatabaseIds());
    if (checked) {
      dbIds.forEach((id) => updated.add(id));
    } else {
      dbIds.forEach((id) => updated.delete(id));
    }
    this.formSelectedDatabaseIds.set(updated);
  }

  // --- Save / Submit Form ---
  saveUser(): void {
    const firstName = this.formFirstName().trim();
    const lastName = this.formLastName().trim();
    const username = this.formUsername().trim();
    const email = this.formEmail().trim();
    const fullName = `${firstName} ${lastName}`.trim() || firstName || lastName || username;

    if (!fullName) {
      this.showError('لطفاً نام یا نام خانوادگی را وارد نمایید.');
      return;
    }

    if (!email) {
      this.showError('لطفاً ایمیل سازمانی کاربر را وارد نمایید.');
      return;
    }

    if (!username) {
      this.showError('لطفاً نام کاربری را وارد نمایید.');
      return;
    }

    if (username.length < 3) {
      this.showError('نام کاربری باید حداقل شامل ۳ کاراکتر باشد.');
      return;
    }

    this.isSaving.set(true);
    this.errorMessage.set(null);

    const cleanPhone = this.formPhone().trim() ? this.formPhone().trim() : undefined;

    if (this.isCreateMode()) {
      if (!this.formPassword() || this.formPassword().length < 8) {
        this.isSaving.set(false);
        this.showError('کلمه عبور کاربر جدید باید حداقل شامل ۸ کاراکتر باشد.');
        return;
      }

      const createDto: UserCreateDto = {
        username,
        email,
        full_name: fullName,
        phone: cleanPhone,
        password: this.formPassword(),
        role_ids: this.formSelectedRoleIds(),
      };

      this.userService.createUser(createDto).subscribe({
        next: (created) => {
          // Assign databases
          const dbIds = Array.from(this.formSelectedDatabaseIds());
          this.userService.updateUserAssignedDatabases(created.id, dbIds).subscribe({
            next: () => {
              // Assign direct permissions
              const permPayload = Array.from(this.userDirectPermissions()).map((act) => ({
                resource_type: 'GLOBAL',
                resource_id: null,
                action: act,
                effect: 'ALLOW',
              }));
              this.userService.bulkUpdateUserPermissions(created.id, permPayload).subscribe({
                next: () => {
                  this.isSaving.set(false);
                  this.showSuccess(`کاربر «${created.full_name}» با موفقیت تعریف و دسترسی‌ها ثبت گردید.`);
                  this.isCreateMode.set(false);
                  this.currentPage.set(1);
                  this.loadUsers();
                  this.selectUser(created);
                },
                error: (err) => {
                  this.isSaving.set(false);
                  this.showError(`کاربر تعریف شد اما در ذخیره مجوزهای اختصاصی خطایی رخ داد: ${err?.error?.detail || err?.message || ''}`);
                  this.isCreateMode.set(false);
                  this.currentPage.set(1);
                  this.loadUsers();
                  this.selectUser(created);
                },
              });
            },
            error: (err) => {
              this.isSaving.set(false);
              this.showError(`کاربر «${created.full_name}» تعریف شد، اما در تخصیص دیتابیس خطایی رخ داد: ${err?.error?.detail || err?.message || ''}`);
              this.isCreateMode.set(false);
              this.currentPage.set(1);
              this.loadUsers();
              this.selectUser(created);
            },
          });
        },
        error: (err) => {
          this.isSaving.set(false);
          this.showError(err?.error?.detail || err?.message || 'خطا در ثبت کاربر جدید.');
        },
      });
    } else {
      const user = this.selectedUser();
      if (!user) {
        this.isSaving.set(false);
        this.showError('لطفاً ابتدا یک کاربر را از جدول انتخاب نمایید.');
        return;
      }

      const updateDto: UserUpdateDto = {
        username,
        full_name: fullName,
        email,
        phone: this.formPhone().trim() ? this.formPhone().trim() : '',
        role_ids: this.formSelectedRoleIds(),
      };

      this.userService.updateUser(user.id, updateDto).subscribe({
        next: (updated) => {
          const dbIds = Array.from(this.formSelectedDatabaseIds());
          this.userService.updateUserAssignedDatabases(updated.id, dbIds).subscribe({
            next: () => {
              const permPayload = Array.from(this.userDirectPermissions()).map((act) => ({
                resource_type: 'GLOBAL',
                resource_id: null,
                action: act,
                effect: 'ALLOW',
              }));
              this.userService.bulkUpdateUserPermissions(updated.id, permPayload).subscribe({
                next: () => {
                  // If password was provided, reset it
                  if (this.formPassword() && this.formPassword().length >= 8) {
                    this.userService.resetPassword(user.id, this.formPassword()).subscribe({
                      next: () => {
                        this.isSaving.set(false);
                        this.isEditMode.set(false);
                        this.showSuccess(`اطلاعات، مجوزها و دسترسی‌های کاربر «${updated.full_name}» با موفقیت ذخیره شد.`);
                        this.loadUsers();
                      },
                      error: (err) => {
                        this.isSaving.set(false);
                        this.isEditMode.set(false);
                        this.showError(`اطلاعات کاربر ذخیره شد اما بازنشانی رمز عبور با خطا مواجه شد: ${err?.error?.detail || err?.message || ''}`);
                        this.loadUsers();
                      },
                    });
                  } else {
                    this.isSaving.set(false);
                    this.isEditMode.set(false);
                    this.showSuccess(`اطلاعات، مجوزها و دسترسی‌های کاربر «${updated.full_name}» با موفقیت به‌روزرسانی شد.`);
                    this.loadUsers();
                  }
                },
                error: (err) => {
                  this.isSaving.set(false);
                  this.showError(`اطلاعات کاربر ذخیره شد اما در ثبت مجوزهای اختصاصی خطایی رخ داد: ${err?.error?.detail || err?.message || ''}`);
                  this.loadUsers();
                },
              });
            },
            error: (err) => {
              this.isSaving.set(false);
              this.showError(`اطلاعات کاربر ذخیره شد اما به‌روزرسانی دسترسی‌های پایگاه داده با خطا مواجه گردید: ${err?.error?.detail || err?.message || ''}`);
              this.loadUsers();
            },
          });
        },
        error: (err) => {
          this.isSaving.set(false);
          this.showError(err?.error?.detail || err?.message || 'خطا در ویرایش اطلاعات کاربر.');
        },
      });
    }
  }

  // --- Row Actions: Delete & Status ---
  toggleUserStatus(user: UserItem, event: Event): void {
    event.stopPropagation();
    const nextStatus = user.status === 'ACTIVE' ? 'SUSPENDED' : 'ACTIVE';
    this.userService.updateUserStatus(user.id, nextStatus).subscribe({
      next: () => {
        this.showSuccess(`وضعیت کاربر «${user.full_name}» به ${nextStatus === 'ACTIVE' ? 'فعال' : 'معلق'} تغییر یافت.`);
        this.loadUsers();
      },
      error: (err) => this.showError(err?.error?.detail || 'خطا در تغییر وضعیت کاربر.'),
    });
  }

  // Delete confirmation dialog signal
  readonly userToDelete = signal<UserItem | null>(null);

  requestDeleteUser(user: UserItem, event?: Event): void {
    if (event) event.stopPropagation();
    if (user.is_superadmin) {
      this.showError('حساب کاربری مدیر ارشد سیستم (SuperAdmin) قابل حذف نیست.');
      return;
    }
    this.userToDelete.set(user);
  }

  cancelDeleteUser(): void {
    this.userToDelete.set(null);
  }

  confirmDeleteUser(): void {
    const user = this.userToDelete();
    if (!user) return;

    this.userService.deleteUser(user.id).subscribe({
      next: () => {
        this.userToDelete.set(null);
        this.showSuccess(`حساب کاربری «${user.username}» حذف گردید.`);
        if (this.selectedUser()?.id === user.id) {
          this.selectedUser.set(null);
        }
        this.loadUsers();
      },
      error: (err) => {
        this.userToDelete.set(null);
        this.showError(err?.error?.detail || 'خطا در حذف کاربر.');
      },
    });
  }

  deleteUser(user: UserItem, event: Event): void {
    this.requestDeleteUser(user, event);
  }

  // --- Helpers ---
  showSuccess(msg: string): void {
    this.successMessage.set(msg);
    setTimeout(() => this.successMessage.set(null), 4000);
  }

  showError(msg: string): void {
    this.errorMessage.set(msg);
    setTimeout(() => this.errorMessage.set(null), 5000);
  }
}

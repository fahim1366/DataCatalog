import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from "@angular/core";

import { FormsModule } from "@angular/forms";
import { RouterModule } from "@angular/router";
import { HasPermissionDirective } from "../../../core/directives/has-permission.directive";
import { RoleManagementService } from "../services/role-management.service";
import {
  PermissionDefinition,
  PermissionModule,
  RoleCreateDto,
  RoleItem,
  RoleUpdateDto,
  RoleUserItem,
} from "../models/access-control.models";

export interface FlatPermissionItem {
  moduleKey: string;
  moduleName: string;
  action: string;
  name: string;
  description: string;
}

@Component({
  selector: "app-role-management",
  standalone: true,
  imports: [FormsModule, RouterModule, HasPermissionDirective],
  templateUrl: "./role-management.component.html",
  styleUrls: ["./role-management.component.css"],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class RoleManagementComponent implements OnInit {
  private roleService = inject(RoleManagementService);

  // Roles list state
  rawRoles = signal<RoleItem[]>([]);
  currentPage = signal<number>(1);
  pageSize = signal<number>(5);
  availablePageSizes = [5, 10, 20, 50];

  // Column search filters & Unified search
  searchTerm = signal<string>("");
  filterPersianName = signal<string>("");
  filterEnglishName = signal<string>("");

  isLoading = signal<boolean>(false);
  isSaving = signal<boolean>(false);
  errorMessage = signal<string | null>(null);
  successMessage = signal<string | null>(null);

  // Master-Detail selection & Form state
  selectedRole = signal<RoleItem | null>(null);
  isCreateMode = signal<boolean>(false);
  isEditMode = signal<boolean>(false);
  isFormEditable = computed<boolean>(
    () => this.isCreateMode() || this.isEditMode(),
  );

  // Form Fields (English system name & Persian display title)
  formEnglishName = signal<string>("");
  formPersianName = signal<string>("");

  // Sub-tabs in detail panel: 'permissions' | 'users'
  activeSubTab = signal<"permissions" | "users">("permissions");

  // Permissions registry & current role permissions
  permissionModules = signal<PermissionModule[]>([]);
  roleSelectedActions = signal<Set<string>>(new Set());
  permissionFilterQuery = signal<string>("");

  // Users assigned to selected role
  roleUsers = signal<RoleUserItem[]>([]);
  isLoadingRoleUsers = signal<boolean>(false);

  // Filtered and Paginated View Models
  filteredRoles = computed<RoleItem[]>(() => {
    const list = this.rawRoles();
    const search = this.searchTerm().trim().toLowerCase();
    const faFilter = this.filterPersianName().trim().toLowerCase();
    const enFilter = this.filterEnglishName().trim().toLowerCase();

    return list.filter((r) => {
      const pName = (r.description || r.name).toLowerCase();
      const eName = r.name.toLowerCase();
      if (search && !pName.includes(search) && !eName.includes(search))
        return false;
      if (faFilter && !pName.includes(faFilter)) return false;
      if (enFilter && !eName.includes(enFilter)) return false;
      return true;
    });
  });

  paginatedRoles = computed<RoleItem[]>(() => {
    const all = this.filteredRoles();
    const start = (this.currentPage() - 1) * this.pageSize();
    return all.slice(start, start + this.pageSize());
  });

  totalPages = computed<number>(() => {
    const count = this.filteredRoles().length;
    return Math.max(1, Math.ceil(count / this.pageSize()));
  });

  pageDisplayStart = computed<number>(() => {
    if (this.filteredRoles().length === 0) return 0;
    return (this.currentPage() - 1) * this.pageSize() + 1;
  });

  pageDisplayEnd = computed<number>(() => {
    return Math.min(
      this.currentPage() * this.pageSize(),
      this.filteredRoles().length,
    );
  });

  // Flat list of permissions for table
  flatPermissions = computed<FlatPermissionItem[]>(() => {
    const query = this.permissionFilterQuery().trim().toLowerCase();
    const flat: FlatPermissionItem[] = [];

    for (const mod of this.permissionModules()) {
      for (const p of mod.permissions) {
        if (
          !query ||
          p.name.toLowerCase().includes(query) ||
          p.description.toLowerCase().includes(query) ||
          p.action.toLowerCase().includes(query) ||
          mod.module_name.toLowerCase().includes(query)
        ) {
          flat.push({
            moduleKey: mod.module_key,
            moduleName: mod.module_name,
            action: p.action,
            name: p.name,
            description: p.description,
          });
        }
      }
    }
    return flat;
  });

  readonly Math = Math;

  ngOnInit(): void {
    this.loadRoles();
    this.loadPermissionsRegistry();
  }

  loadRoles(): void {
    this.isLoading.set(true);
    this.errorMessage.set(null);

    this.roleService.getAllRolesList().subscribe({
      next: (roles) => {
        this.isLoading.set(false);
        this.rawRoles.set(roles);

        const current = this.selectedRole();
        if (current) {
          const fresh = roles.find((r) => r.id === current.id);
          if (fresh) {
            this.selectRole(fresh);
          }
        } else if (roles.length > 0 && !this.isCreateMode()) {
          this.selectRole(roles[0]);
        }
      },
      error: (err) => {
        this.isLoading.set(false);
        this.showError(err?.error?.detail || "خطا در فراخوانی لیست نقش‌ها.");
      },
    });
  }

  loadPermissionsRegistry(): void {
    this.roleService.getPermissionsRegistry().subscribe({
      next: (modules) => this.permissionModules.set(modules),
      error: () => {},
    });
  }

  selectRole(role: RoleItem): void {
    this.isCreateMode.set(false);
    this.isEditMode.set(false);
    this.selectedRole.set(role);
    this.formEnglishName.set(role.name);
    this.formPersianName.set(role.description || role.name);

    // Load permissions for this role
    this.roleService.getRolePermissions(role.id).subscribe({
      next: (rules) => {
        const actions = new Set<string>();
        rules.forEach((r) => actions.add(r.action));
        this.roleSelectedActions.set(actions);
      },
      error: () => this.roleSelectedActions.set(new Set()),
    });

    // Load users for this role
    this.loadRoleUsers(role.id);
  }

  startEditRole(role: RoleItem, event?: Event): void {
    if (event) {
      event.stopPropagation();
    }
    this.selectRole(role);
    this.isEditMode.set(true);
  }

  enableEditMode(): void {
    this.isEditMode.set(true);
  }

  loadRoleUsers(roleId: string): void {
    this.isLoadingRoleUsers.set(true);
    this.roleService.getRoleUsers(roleId).subscribe({
      next: (users) => {
        this.isLoadingRoleUsers.set(false);
        this.roleUsers.set(users);
      },
      error: () => {
        this.isLoadingRoleUsers.set(false);
        this.roleUsers.set([]);
      },
    });
  }

  startCreateNewRole(): void {
    this.isCreateMode.set(true);
    this.isEditMode.set(false);
    this.selectedRole.set(null);
    this.formEnglishName.set("");
    this.formPersianName.set("");
    this.roleSelectedActions.set(new Set());
    this.roleUsers.set([]);
    this.activeSubTab.set("permissions");
  }

  cancelDetailEdit(): void {
    if (this.isCreateMode()) {
      if (this.rawRoles().length > 0) {
        this.selectRole(this.rawRoles()[0]);
      } else {
        this.isCreateMode.set(false);
        this.selectedRole.set(null);
      }
    } else {
      const current = this.selectedRole();
      this.isEditMode.set(false);
      if (current) {
        this.selectRole(current);
      }
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

  switchSubTab(tab: "permissions" | "users"): void {
    this.activeSubTab.set(tab);
  }

  // --- Checkbox toggle for permissions ---
  hasPermission(action: string): boolean {
    return this.roleSelectedActions().has(action);
  }

  togglePermission(action: string, event: Event): void {
    if (!this.isFormEditable()) {
      return;
    }
    const checked = (event.target as HTMLInputElement).checked;
    const current = new Set(this.roleSelectedActions());
    if (checked) {
      current.add(action);
    } else {
      current.delete(action);
    }
    this.roleSelectedActions.set(current);
  }

  // --- Save / Submit Role ---
  saveRole(): void {
    const englishName = this.formEnglishName().trim();
    const persianName = this.formPersianName().trim();

    if (!englishName || !persianName) {
      this.showError(
        "لطفاً هم نام انگلیسی (کد سیستمی) و هم نام فارسی نقش را وارد نمایید.",
      );
      return;
    }

    this.isSaving.set(true);

    const permissionPayload = Array.from(this.roleSelectedActions()).map(
      (act) => ({
        resource_type: "GLOBAL",
        action: act,
        effect: "ALLOW",
      }),
    );

    if (this.isCreateMode()) {
      const createDto: RoleCreateDto = {
        name: englishName,
        description: persianName,
      };

      this.roleService.createRole(createDto).subscribe({
        next: (created) => {
          this.roleService
            .updateRolePermissions(created.id, permissionPayload)
            .subscribe({
              next: () => {
                this.isSaving.set(false);
                this.isEditMode.set(false);
                this.showSuccess(
                  `نقش سازمانی «${persianName}» با موفقیت ثبت شد.`,
                );
                this.loadRoles();
                this.selectRole(created);
              },
              error: () => {
                this.isSaving.set(false);
                this.isEditMode.set(false);
                this.showSuccess(
                  `نقش ثبت شد، اما در ذخیره مجوزها خطایی رخ داد.`,
                );
                this.loadRoles();
                this.selectRole(created);
              },
            });
        },
        error: (err) => {
          this.isSaving.set(false);
          this.showError(err?.error?.detail || "خطا در ثبت نقش جدید.");
        },
      });
    } else {
      const role = this.selectedRole();
      if (!role) {
        this.isSaving.set(false);
        return;
      }

      const updateDto: RoleUpdateDto = {
        name: englishName,
        description: persianName,
      };

      this.roleService.updateRole(role.id, updateDto).subscribe({
        next: (updated) => {
          this.roleService
            .updateRolePermissions(updated.id, permissionPayload)
            .subscribe({
              next: () => {
                this.isSaving.set(false);
                this.isEditMode.set(false);
                this.showSuccess(
                  `اطلاعات و مجوزهای نقش «${persianName}» با موفقیت به‌روزرسانی شد.`,
                );
                this.loadRoles();
              },
              error: () => {
                this.isSaving.set(false);
                this.isEditMode.set(false);
                this.showError(
                  "اطلاعات نقش ذخیره شد اما در ذخیره مجوزها خطایی رخ داد.",
                );
                this.loadRoles();
              },
            });
        },
        error: (err) => {
          this.isSaving.set(false);
          this.showError(err?.error?.detail || "خطا در ویرایش اطلاعات نقش.");
        },
      });
    }
  }

  // Delete confirmation dialog signal
  readonly roleToDelete = signal<RoleItem | null>(null);

  requestDeleteRole(role: RoleItem, event?: Event): void {
    if (event) event.stopPropagation();
    if (role.is_system_role) {
      this.showError("نقش‌های سیستمی و پیش‌فرض سامانه قابل حذف نمی‌باشند.");
      return;
    }
    this.roleToDelete.set(role);
  }

  cancelDeleteRole(): void {
    this.roleToDelete.set(null);
  }

  confirmDeleteRole(): void {
    const role = this.roleToDelete();
    if (!role) return;

    this.roleService.deleteRole(role.id).subscribe({
      next: () => {
        this.roleToDelete.set(null);
        this.showSuccess(
          `نقش «${role.description || role.name}» با موفقیت حذف گردید.`,
        );
        if (this.selectedRole()?.id === role.id) {
          this.selectedRole.set(null);
        }
        this.loadRoles();
      },
      error: (err) => {
        this.roleToDelete.set(null);
        this.showError(err?.error?.detail || "خطا در حذف نقش سازمانی.");
      },
    });
  }

  deleteRole(role: RoleItem, event: Event): void {
    this.requestDeleteRole(role, event);
  }

  showSuccess(msg: string): void {
    this.successMessage.set(msg);
    setTimeout(() => this.successMessage.set(null), 4000);
  }

  showError(msg: string): void {
    this.errorMessage.set(msg);
    setTimeout(() => this.errorMessage.set(null), 5000);
  }
}

import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RoleManagementService } from '../services/role-management.service';
import {
  PermissionModule,
  RoleCreateDto,
  RoleItem,
  RoleUpdateDto,
  RoleUserItem,
} from '../models/access-control.models';

@Component({
  selector: 'app-role-management',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="roles-container" dir="rtl">
      @if (successMessage()) {
        <div class="alert alert-success animate-fade">
          <span>{{ successMessage() }}</span>
          <button class="alert-close" (click)="successMessage.set(null)">×</button>
        </div>
      }
      @if (errorMessage()) {
        <div class="alert alert-danger animate-fade">
          <span>{{ errorMessage() }}</span>
          <button class="alert-close" (click)="errorMessage.set(null)">×</button>
        </div>
      }

      <header class="page-header">
        <div>
          <h1 class="page-title">
            <span>🛡️</span>
            مدیریت نقش‌های سازمانی (Role-Based Access Control)
          </h1>
          <p class="page-subtitle">
            تعریف و پیکربندی نقش‌های کاربری، تنظیم بسته‌های مجوز دسترسی و بازرسی کاربران دارای نقش
          </p>
        </div>
        <button class="btn btn-primary" (click)="openCreateModal()">
          <span>+ تعریف نقش سفارشی جدید</span>
        </button>
      </header>

      <!-- Roles Grid -->
      <div class="grid-card">
        <div class="table-responsive">
          <table class="data-table">
            <thead>
              <tr>
                <th style="width: 50px;">ردیف</th>
                <th>نام نقش سازمانی</th>
                <th>توضیحات و دامنه عملکرد</th>
                <th class="th-center">نوع نقش</th>
                <th class="th-center">کاربران منتسب</th>
                <th>تاریخ ایجاد</th>
                <th class="th-center" style="width: 240px;">عملیات</th>
              </tr>
            </thead>
            <tbody>
              @if (isLoading()) {
                <tr>
                  <td colspan="7" class="td-loading">در حال بارگذاری اطلاعات نقش‌ها...</td>
                </tr>
              } @else if (roles().length === 0) {
                <tr>
                  <td colspan="7" class="td-empty">نقشی یافت نشد.</td>
                </tr>
              } @else {
                @for (r of roles(); track r.id; let idx = $index) {
                  <tr>
                    <td class="td-muted">{{ idx + 1 }}</td>
                    <td class="font-bold">
                      <span class="role-icon-mini">🛡️</span>
                      {{ r.name }}
                    </td>
                    <td class="text-desc">{{ r.description || '—' }}</td>
                    <td class="th-center">
                      @if (r.is_system_role) {
                        <span class="badge badge-system">سیستمی (پیش‌فرض)</span>
                      } @else {
                        <span class="badge badge-custom">سفارشی</span>
                      }
                    </td>
                    <td class="th-center">
                      <button class="pill-btn pill-blue" (click)="openUsersModal(r)">
                        👥 {{ r.user_count }} کاربر
                      </button>
                    </td>
                    <td class="td-date">{{ formatDate(r.created_at) }}</td>
                    <td class="th-center td-actions">
                      <button class="btn btn-primary btn-xs" (click)="openPermissionsModal(r)">
                        🔑 مجوزها
                      </button>
                      <button class="btn btn-outline btn-xs" (click)="openUsersModal(r)">
                        کاربران
                      </button>
                      <button class="btn btn-outline btn-xs" (click)="openEditModal(r)">
                        ✏️
                      </button>
                      @if (!r.is_system_role) {
                        <button class="btn btn-outline-danger btn-xs" (click)="deleteRole(r)">
                          🗑️
                        </button>
                      }
                    </td>
                  </tr>
                }
              }
            </tbody>
          </table>
        </div>
      </div>

      <!-- CREATE ROLE MODAL -->
      @if (showCreateModal()) {
        <div class="modal-backdrop animate-fade">
          <div class="modal-card">
            <div class="modal-header">
              <h3>+ تعریف نقش سازمانی سفارشی</h3>
              <button class="modal-close" (click)="showCreateModal.set(false)">✕</button>
            </div>
            <div class="modal-body">
              <div class="form-group mb-3">
                <label>نام نقش (الزامی) *</label>
                <input
                  type="text"
                  class="form-control"
                  placeholder="مانند: ComplianceAuditor"
                  [(ngModel)]="newRole.name"
                />
              </div>
              <div class="form-group">
                <label>توضیحات و حیطه اختیارات</label>
                <textarea
                  class="form-control"
                  rows="3"
                  placeholder="هدف از تعریف این نقش و دامنه دسترسی‌های آن..."
                  [(ngModel)]="newRole.description"
                ></textarea>
              </div>
            </div>
            <div class="modal-footer">
              <button class="btn btn-secondary" (click)="showCreateModal.set(false)">انصراف</button>
              <button class="btn btn-primary" (click)="submitCreateRole()">ایجاد نقش</button>
            </div>
          </div>
        </div>
      }

      <!-- EDIT ROLE MODAL -->
      @if (showEditModal()) {
        <div class="modal-backdrop animate-fade">
          <div class="modal-card">
            <div class="modal-header">
              <h3>ویرایش نقش: {{ activeRole()?.name }}</h3>
              <button class="modal-close" (click)="showEditModal.set(false)">✕</button>
            </div>
            <div class="modal-body">
              <div class="form-group mb-3">
                <label>نام نقش</label>
                <input type="text" class="form-control" [(ngModel)]="editRoleDto.name" />
              </div>
              <div class="form-group">
                <label>توضیحات</label>
                <textarea class="form-control" rows="3" [(ngModel)]="editRoleDto.description"></textarea>
              </div>
            </div>
            <div class="modal-footer">
              <button class="btn btn-secondary" (click)="showEditModal.set(false)">انصراف</button>
              <button class="btn btn-primary" (click)="submitEditRole()">ثبت تغییرات</button>
            </div>
          </div>
        </div>
      }

      <!-- PERMISSIONS MODAL -->
      @if (showPermissionsModal()) {
        <div class="modal-backdrop animate-fade">
          <div class="modal-card modal-lg">
            <div class="modal-header">
              <h3>🔑 پیکربندی مجوزهای نقش: {{ activeRole()?.name }}</h3>
              <button class="modal-close" (click)="showPermissionsModal.set(false)">✕</button>
            </div>
            <div class="modal-body">
              <p class="text-sm text-muted mb-3">
                مجوزهای زیر دسترسی‌های پایه‌ای این نقش در سامانه را تعیین می‌کنند:
              </p>

              <div class="modules-accordion">
                @for (mod of permissionsRegistry(); track mod.module_key) {
                  <div class="module-group-card">
                    <div class="module-group-header">
                      <h4>{{ mod.module_name }}</h4>
                      <p class="text-xs text-muted mb-0">{{ mod.description }}</p>
                    </div>
                    <div class="module-perms-list">
                      @for (p of mod.permissions; track p.action) {
                        <label class="perm-row-item">
                          <input
                            type="checkbox"
                            [checked]="selectedPermissionActions().has(p.action)"
                            (change)="togglePermissionAction(p.action, $event)"
                          />
                          <div>
                            <strong>{{ p.name }} (<code>{{ p.action }}</code>)</strong>
                            <p class="text-xs text-muted mb-0">{{ p.description }}</p>
                          </div>
                        </label>
                      }
                    </div>
                  </div>
                }
              </div>
            </div>
            <div class="modal-footer">
              <button class="btn btn-secondary" (click)="showPermissionsModal.set(false)">انصراف</button>
              <button class="btn btn-primary" (click)="saveRolePermissions()">ذخیره مجوزها</button>
            </div>
          </div>
        </div>
      }

      <!-- ROLE USERS MODAL -->
      @if (showUsersModal()) {
        <div class="modal-backdrop animate-fade">
          <div class="modal-card">
            <div class="modal-header">
              <h3>👥 کاربران دارای نقش: {{ activeRole()?.name }}</h3>
              <button class="modal-close" (click)="showUsersModal.set(false)">✕</button>
            </div>
            <div class="modal-body">
              <table class="data-table data-table-sm">
                <thead>
                  <tr>
                    <th>نام کاربر</th>
                    <th>نام کاربری</th>
                    <th>ایمیل</th>
                    <th class="th-center">نوع انتساب</th>
                  </tr>
                </thead>
                <tbody>
                  @for (u of roleUsers(); track u.id) {
                    <tr>
                      <td class="font-bold">{{ u.full_name }}</td>
                      <td><code>{{ u.username }}</code></td>
                      <td>{{ u.email }}</td>
                      <td class="th-center">
                        @if (u.assignment_type === 'DIRECT') {
                          <span class="badge badge-direct">صریح (مستقیم)</span>
                        } @else {
                          <span class="badge badge-group">از طریق گروه</span>
                        }
                      </td>
                    </tr>
                  }
                  @if (roleUsers().length === 0) {
                    <tr>
                      <td colspan="4" class="td-empty">هیچ کاربری با این نقش وجود ندارد.</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
            <div class="modal-footer">
              <button class="btn btn-secondary" (click)="showUsersModal.set(false)">بستن</button>
            </div>
          </div>
        </div>
      }
    </div>
  `,
  styles: [
    `
      .roles-container {
        font-family: var(--font-family-sans, 'Vazirmatn', sans-serif);
        padding: 1.5rem;
        display: flex;
        flex-direction: column;
        gap: 1.5rem;
        color: #1e293b;
      }
      .page-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        background: white;
        padding: 1.25rem 1.75rem;
        border-radius: 12px;
        border: 1px solid #e2e8f0;
      }
      .page-title {
        font-size: 1.35rem;
        font-weight: 700;
        margin: 0 0 0.35rem 0;
        display: flex;
        align-items: center;
        gap: 0.5rem;
      }
      .page-subtitle {
        font-size: 0.85rem;
        color: #64748b;
        margin: 0;
      }
      .grid-card {
        background: white;
        border-radius: 12px;
        border: 1px solid #e2e8f0;
        overflow: hidden;
      }
      .data-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.85rem;
        text-align: right;
      }
      .data-table th {
        background: #f8fafc;
        color: #475569;
        font-weight: 600;
        padding: 0.85rem 1rem;
        border-bottom: 2px solid #e2e8f0;
      }
      .data-table td {
        padding: 0.85rem 1rem;
        border-bottom: 1px solid #f1f5f9;
        vertical-align: middle;
      }
      .th-center { text-align: center; }
      .td-muted { color: #94a3b8; font-size: 0.8rem; }
      .td-date { font-size: 0.8rem; color: #64748b; }
      .text-desc { color: #475569; max-width: 320px; }
      .badge {
        font-size: 0.75rem;
        padding: 0.2rem 0.55rem;
        border-radius: 6px;
        font-weight: 500;
      }
      .badge-system { background: #fef3c7; color: #92400e; border: 1px solid #fde68a; }
      .badge-custom { background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; }
      .badge-direct { background: #dcfce7; color: #15803d; }
      .badge-group { background: #e0f2fe; color: #0369a1; }
      .pill-btn {
        font-family: inherit;
        font-size: 0.75rem;
        font-weight: 600;
        padding: 0.25rem 0.65rem;
        border-radius: 9999px;
        border: 1px solid transparent;
        cursor: pointer;
      }
      .pill-blue { background: #e0f2fe; color: #0369a1; border-color: #bae6fd; }
      .btn {
        font-family: inherit;
        font-size: 0.85rem;
        font-weight: 600;
        padding: 0.5rem 1rem;
        border-radius: 8px;
        border: 1px solid transparent;
        cursor: pointer;
      }
      .btn-primary { background: #2563eb; color: white; }
      .btn-secondary { background: #f1f5f9; color: #334155; border-color: #cbd5e1; }
      .btn-outline { background: transparent; border-color: #cbd5e1; color: #475569; }
      .btn-outline-danger { background: transparent; border-color: #fecdd3; color: #e11d48; }
      .btn-xs { padding: 0.2rem 0.5rem; font-size: 0.75rem; }
      .btn-sm { padding: 0.35rem 0.75rem; font-size: 0.8rem; }
      .td-actions { display: flex; gap: 0.35rem; justify-content: center; }
      .modal-backdrop {
        position: fixed;
        inset: 0;
        background: rgba(15, 23, 42, 0.5);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 1050;
      }
      .modal-card {
        background: white;
        border-radius: 12px;
        width: 100%;
        max-width: 580px;
        overflow: hidden;
      }
      .modal-lg { max-width: 720px; }
      .modal-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 1rem 1.5rem;
        background: #f8fafc;
        border-bottom: 1px solid #e2e8f0;
      }
      .modal-header h3 { margin: 0; font-size: 1.05rem; }
      .modal-close { background: none; border: none; font-size: 1.25rem; cursor: pointer; }
      .modal-body { padding: 1.5rem; max-height: 70vh; overflow-y: auto; }
      .modal-footer {
        display: flex;
        justify-content: flex-end;
        gap: 0.5rem;
        padding: 1rem 1.5rem;
        background: #f8fafc;
        border-top: 1px solid #e2e8f0;
      }
      .modules-accordion { display: flex; flex-direction: column; gap: 1rem; }
      .module-group-card {
        border: 1px solid #e2e8f0;
        border-radius: 10px;
        overflow: hidden;
      }
      .module-group-header {
        background: #f8fafc;
        padding: 0.75rem 1rem;
        border-bottom: 1px solid #e2e8f0;
      }
      .module-group-header h4 { margin: 0 0 0.2rem 0; font-size: 0.95rem; }
      .module-perms-list {
        padding: 0.75rem 1rem;
        display: flex;
        flex-direction: column;
        gap: 0.75rem;
      }
      .perm-row-item {
        display: flex;
        align-items: flex-start;
        gap: 0.75rem;
        cursor: pointer;
      }
      .perm-row-item input[type='checkbox'] { margin-top: 3px; accent-color: #2563eb; }
      .form-group { display: flex; flex-direction: column; gap: 0.35rem; }
      .form-group label { font-size: 0.8rem; font-weight: 600; color: #475569; }
      .form-control {
        font-family: inherit;
        font-size: 0.85rem;
        padding: 0.5rem 0.75rem;
        border-radius: 8px;
        border: 1px solid #cbd5e1;
      }
      .alert {
        padding: 0.75rem 1.25rem;
        border-radius: 8px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 0.85rem;
      }
      .alert-success { background: #f0fdf4; border: 1px solid #bbf7d0; color: #166534; }
      .alert-danger { background: #fef2f2; border: 1px solid #fecaca; color: #991b1b; }
      .alert-close { background: none; border: none; cursor: pointer; font-size: 1.1rem; }
      .mb-0 { margin-bottom: 0; }
      .mb-3 { margin-bottom: 1rem; }
      .text-xs { font-size: 0.75rem; }
      .text-sm { font-size: 0.85rem; }
      .text-muted { color: #64748b; }
      .animate-fade { animation: fadeIn 0.2s ease-in-out; }
      @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
    `,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class RoleManagementComponent implements OnInit {
  private roleService = inject(RoleManagementService);

  roles = signal<RoleItem[]>([]);
  isLoading = signal<boolean>(false);
  successMessage = signal<string | null>(null);
  errorMessage = signal<string | null>(null);

  permissionsRegistry = signal<PermissionModule[]>([]);
  activeRole = signal<RoleItem | null>(null);
  selectedPermissionActions = signal<Set<string>>(new Set());
  roleUsers = signal<RoleUserItem[]>([]);

  // Modals
  showCreateModal = signal<boolean>(false);
  showEditModal = signal<boolean>(false);
  showPermissionsModal = signal<boolean>(false);
  showUsersModal = signal<boolean>(false);

  // Forms
  newRole: RoleCreateDto = { name: '', description: '' };
  editRoleDto: RoleUpdateDto = { name: '', description: '' };

  ngOnInit(): void {
    this.loadRoles();
    this.loadRegistry();
  }

  loadRoles(): void {
    this.isLoading.set(true);
    this.roleService.getAllRolesList().subscribe({
      next: (roles) => {
        this.isLoading.set(false);
        this.roles.set(roles);
      },
      error: (err) => {
        this.isLoading.set(false);
        this.showError(err?.error?.detail || 'خطا در بارگذاری نقش‌ها.');
      },
    });
  }

  loadRegistry(): void {
    this.roleService.getPermissionsRegistry().subscribe({
      next: (reg) => this.permissionsRegistry.set(reg),
      error: () => {},
    });
  }

  openCreateModal(): void {
    this.newRole = { name: '', description: '' };
    this.showCreateModal.set(true);
  }

  submitCreateRole(): void {
    if (!this.newRole.name) {
      this.showError('لطفاً نام نقش را وارد نمایید.');
      return;
    }
    this.roleService.createRole(this.newRole).subscribe({
      next: (created) => {
        this.showSuccess(`نقش «${created.name}» با موفقیت ایجاد گردید.`);
        this.showCreateModal.set(false);
        this.loadRoles();
      },
      error: (err) => this.showError(err?.error?.detail || 'خطا در ایجاد نقش.'),
    });
  }

  openEditModal(r: RoleItem): void {
    this.activeRole.set(r);
    this.editRoleDto = { name: r.name, description: r.description };
    this.showEditModal.set(true);
  }

  submitEditRole(): void {
    const r = this.activeRole();
    if (!r) return;

    this.roleService.updateRole(r.id, this.editRoleDto).subscribe({
      next: () => {
        this.showSuccess('اطلاعات نقش به‌روزرسانی شد.');
        this.showEditModal.set(false);
        this.loadRoles();
      },
      error: (err) => this.showError(err?.error?.detail || 'خطا در ویرایش نقش.'),
    });
  }

  deleteRole(r: RoleItem): void {
    if (!confirm(`آیا از حذف نقش «${r.name}» اطمینان دارید؟`)) return;

    this.roleService.deleteRole(r.id).subscribe({
      next: () => {
        this.showSuccess(`نقش «${r.name}» حذف گردید.`);
        this.loadRoles();
      },
      error: (err) => this.showError(err?.error?.detail || 'خطا در حذف نقش.'),
    });
  }

  openPermissionsModal(r: RoleItem): void {
    this.activeRole.set(r);
    this.roleService.getRolePermissions(r.id).subscribe({
      next: (rules) => {
        const set = new Set<string>();
        for (const rule of rules) {
          set.add(rule.action);
        }
        this.selectedPermissionActions.set(set);
        this.showPermissionsModal.set(true);
      },
      error: () => this.showError('خطا در بارگذاری مجوزهای نقش.'),
    });
  }

  togglePermissionAction(action: string, event: Event): void {
    const checked = (event.target as HTMLInputElement).checked;
    const current = new Set(this.selectedPermissionActions());
    if (checked) current.add(action);
    else current.delete(action);
    this.selectedPermissionActions.set(current);
  }

  saveRolePermissions(): void {
    const r = this.activeRole();
    if (!r) return;

    const payload = Array.from(this.selectedPermissionActions()).map((action) => ({
      resource_type: 'GLOBAL',
      action,
      effect: 'ALLOW',
    }));

    this.roleService.updateRolePermissions(r.id, payload).subscribe({
      next: () => {
        this.showSuccess(`مجوزهای نقش «${r.name}» با موفقیت ذخیره شد.`);
        this.showPermissionsModal.set(false);
      },
      error: (err) => this.showError(err?.error?.detail || 'خطا در ذخیره مجوزها.'),
    });
  }

  openUsersModal(r: RoleItem): void {
    this.activeRole.set(r);
    this.roleService.getRoleUsers(r.id).subscribe({
      next: (users) => {
        this.roleUsers.set(users);
        this.showUsersModal.set(true);
      },
      error: () => this.showError('خطا در بارگذاری کاربران نقش.'),
    });
  }

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
      return new Intl.DateTimeFormat('fa-IR', { dateStyle: 'medium' }).format(new Date(dateStr));
    } catch {
      return dateStr;
    }
  }
}

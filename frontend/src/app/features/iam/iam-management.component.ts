import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';

interface UserItem {
  id: string;
  username: string;
  email: string;
  full_name: string;
  status: string;
  is_superadmin: boolean;
  roles: string[];
  role_ids: string[];
  created_at: string;
}

interface RoleItem {
  id: string;
  name: string;
  description?: string;
  is_system_role: boolean;
  user_count: number;
}

interface PermissionRuleItem {
  id: string;
  role_id: string;
  resource_type: string;
  resource_id?: string | null;
  action: string;
  effect: 'ALLOW' | 'DENY';
  created_at: string;
}

@Component({
  selector: 'app-iam-management',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="iam-layout">
      <!-- Page Header -->
      <header class="page-header">
        <div>
          <h1 class="page-title">مدیریت کاربران، نقش‌ها و سطوح دسترسی (RBAC)</h1>
          <p class="page-subtitle">کنترل جامع دسترسی دومنظوره (منابع فیزیکی دیتابیس و دسته‌بندی‌های موضوعی) با اولویت قوانین DENY</p>
        </div>

        <div class="header-actions">
          @if (activeTab() === 'users') {
            <button class="btn btn-primary" (click)="openCreateUserModal()">
              <span>+ ایجاد کاربر سازمانی جدید</span>
            </button>
          } @else if (activeTab() === 'roles') {
            <button class="btn btn-primary" (click)="openCreateRoleModal()">
              <span>+ ایجاد نقش سفارشی</span>
            </button>
          } @else {
            <button class="btn btn-primary" (click)="openCreateRuleModal()">
              <span>+ ثبت قانون دسترسی (Rule)</span>
            </button>
          }
        </div>
      </header>

      <!-- Navigation Tabs -->
      <div class="tabs-nav">
        <button
          class="tab-link"
          [class.active]="activeTab() === 'users'"
          (click)="activeTab.set('users')"
        >
          👥 کاربران سامانه ({{ users().length }})
        </button>
        <button
          class="tab-link"
          [class.active]="activeTab() === 'roles'"
          (click)="activeTab.set('roles')"
        >
          🛡️ نقش‌های سازمانی ({{ roles().length }})
        </button>
        <button
          class="tab-link"
          [class.active]="activeTab() === 'permissions'"
          (click)="activeTab.set('permissions')"
        >
          ⚖️ قوانین کنترل دسترسی دومنظوره ({{ permissionRules().length }})
        </button>
      </div>

      <!-- Tab Content Area -->
      <div class="tab-content">
        <!-- TAB 1: USERS -->
        @if (activeTab() === 'users') {
          <div class="table-card">
            <table class="data-table">
              <thead>
                <tr>
                  <th>نام کاربری</th>
                  <th>نام و نام خانوادگی</th>
                  <th>پست الکترونیکی</th>
                  <th>نقش‌های منتسب</th>
                  <th>وضعیت</th>
                  <th>مدیر ارشد</th>
                  <th>عملیات</th>
                </tr>
              </thead>
              <tbody>
                @for (u of users(); track u.id) {
                  <tr>
                    <td class="mono-font fw-bold">{{ u.username }}</td>
                    <td>{{ u.full_name }}</td>
                    <td class="mono-font">{{ u.email }}</td>
                    <td>
                      <div class="roles-flex">
                        @for (r of u.roles; track r) {
                          <span class="badge badge-internal">{{ r }}</span>
                        }
                      </div>
                    </td>
                    <td>
                      <span
                        class="badge"
                        [class.badge-active]="u.status === 'ACTIVE'"
                        [class.badge-deleted]="u.status !== 'ACTIVE'"
                      >
                        {{ u.status === 'ACTIVE' ? 'فعال' : 'مسدود' }}
                      </span>
                    </td>
                    <td>
                      @if (u.is_superadmin) {
                        <span class="badge badge-deny">مدیر ارشد</span>
                      } @else {
                        <span class="text-muted">عادی</span>
                      }
                    </td>
                    <td>
                      <div class="actions-row">
                        <button
                          class="btn btn-secondary btn-sm"
                          (click)="toggleUserStatus(u)"
                        >
                          {{ u.status === 'ACTIVE' ? 'تعلیق' : 'فعال‌سازی' }}
                        </button>
                        <button
                          class="btn btn-secondary btn-sm"
                          (click)="openAssignRolesModal(u)"
                        >
                          تخصیص نقش
                        </button>
                      </div>
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        }

        <!-- TAB 2: ROLES -->
        @if (activeTab() === 'roles') {
          <div class="table-card">
            <table class="data-table">
              <thead>
                <tr>
                  <th>نام نقش</th>
                  <th>شرح وظایف و مجوزها</th>
                  <th>نوع نقش</th>
                  <th>تعداد کاربران منتسب</th>
                  <th>عملیات</th>
                </tr>
              </thead>
              <tbody>
                @for (r of roles(); track r.id) {
                  <tr>
                    <td class="mono-font fw-bold">{{ r.name }}</td>
                    <td>{{ r.description || 'بدون توضیح' }}</td>
                    <td>
                      @if (r.is_system_role) {
                        <span class="badge badge-internal">سیستمی (غیرقابل حذف)</span>
                      } @else {
                        <span class="badge badge-public">سفارشی</span>
                      }
                    </td>
                    <td>
                      <span class="badge badge-internal">{{ r.user_count }} کاربر</span>
                    </td>
                    <td>
                      @if (!r.is_system_role) {
                        <button class="btn btn-danger btn-sm" (click)="deleteRole(r)">حذف</button>
                      } @else {
                        <span class="text-muted">-</span>
                      }
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        }

        <!-- TAB 3: PERMISSION RULES (ALLOW / DENY) -->
        @if (activeTab() === 'permissions') {
          <div class="table-card">
            <div class="card-header">
              <h2>ماتریس قوانین دسترسی (Policy Rules)</h2>
              <span class="text-muted">قوانین مسدودسازی (DENY) همواره بر قوانین مجاز (ALLOW) اولویت داشته و در صورت تعارض تقدم دارند</span>
            </div>

            <table class="data-table">
              <thead>
                <tr>
                  <th>نقش مرتبط</th>
                  <th>سطح منبع (Hierarchy Scope)</th>
                  <th>شناسه منبع (Resource ID)</th>
                  <th>عملیات مجاز/ممنوع (Action)</th>
                  <th>اثر قانون (Effect)</th>
                  <th>تاریخ ایجاد</th>
                  <th>عملیات</th>
                </tr>
              </thead>
              <tbody>
                @for (rule of permissionRules(); track rule.id) {
                  <tr>
                    <td>
                      <span class="badge badge-internal mono-font">{{ getRoleName(rule.role_id) }}</span>
                    </td>
                    <td>
                      <span class="code-badge">{{ rule.resource_type }}</span>
                    </td>
                    <td class="mono-font text-muted">
                      {{ rule.resource_id ? rule.resource_id.substring(0, 8) + '...' : 'عمومی (GLOBAL)' }}
                    </td>
                    <td>
                      <strong class="mono-font">{{ rule.action }}</strong>
                    </td>
                    <td>
                      <span
                        class="badge"
                        [class.badge-allow]="rule.effect === 'ALLOW'"
                        [class.badge-deny]="rule.effect === 'DENY'"
                      >
                        {{ rule.effect === 'ALLOW' ? 'مجاز (ALLOW)' : 'مسدود (DENY)' }}
                      </span>
                    </td>
                    <td class="mono-font text-muted">{{ rule.created_at | date:'yyyy-MM-dd HH:mm' }}</td>
                    <td>
                      <button class="btn btn-danger btn-sm" (click)="revokeRule(rule.id)">ابطال قانون</button>
                    </td>
                  </tr>
                } @empty {
                  <tr>
                    <td colspan="7" class="text-center">هیچ قانون دسترسی ثبت نشده است.</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        }
      </div>
    </div>

    <!-- MODAL: CREATE USER -->
    @if (isUserModalOpen()) {
      <div class="modal-backdrop" (click)="isUserModalOpen.set(false)">
        <div class="modal-dialog" (click)="$event.stopPropagation()">
          <div class="modal-header">
            <h3 class="modal-title">ایجاد کاربر سازمانی جدید</h3>
            <button class="btn-close" (click)="isUserModalOpen.set(false)">✕</button>
          </div>
          <div class="modal-body">
            <div class="form-group">
              <label class="form-label">نام کاربری:</label>
              <input type="text" class="form-control mono-font" [(ngModel)]="newUserUsername" placeholder="مثال: j_doe" />
            </div>

            <div class="form-group">
              <label class="form-label">نام و نام خانوادگی:</label>
              <input type="text" class="form-control" [(ngModel)]="newUserFullName" placeholder="مثال: جان دو" />
            </div>

            <div class="form-group">
              <label class="form-label">ایمیل سازمانی:</label>
              <input type="email" class="form-control mono-font" [(ngModel)]="newUserEmail" placeholder="j_doe@company.internal" />
            </div>

            <div class="form-group">
              <label class="form-label">گذرواژه اولیه (رمزنگاری با Argon2id):</label>
              <input type="password" class="form-control mono-font" [(ngModel)]="newUserPassword" placeholder="حداقل ۸ کاراکتر" />
            </div>

            <div class="form-group">
              <label class="form-label">انتخاب نقش‌های سازمانی:</label>
              <div class="checkbox-list">
                @for (r of roles(); track r.id) {
                  <label class="checkbox-label">
                    <input
                      type="checkbox"
                      [checked]="newUserRoleIds.has(r.id)"
                      (change)="toggleRoleSelection(r.id)"
                    />
                    <span>{{ r.name }} ({{ r.description || 'بدون توضیح' }})</span>
                  </label>
                }
              </div>
            </div>

            <div class="form-group">
              <label class="checkbox-label">
                <input type="checkbox" [(ngModel)]="newUserIsSuperadmin" />
                <strong>دسترسی کامل مدیر ارشد (SuperAdmin Bypass)</strong>
              </label>
            </div>
          </div>
          <div class="modal-footer">
            <button class="btn btn-secondary" (click)="isUserModalOpen.set(false)">انصراف</button>
            <button class="btn btn-primary" (click)="saveUser()">ثبت کاربر</button>
          </div>
        </div>
      </div>
    }

    <!-- MODAL: ASSIGN ROLES TO USER -->
    @if (isAssignRolesModalOpen()) {
      <div class="modal-backdrop" (click)="isAssignRolesModalOpen.set(false)">
        <div class="modal-dialog" (click)="$event.stopPropagation()">
          <div class="modal-header">
            <h3 class="modal-title">تخصیص نقش‌ها به {{ selectedUserForRoles()?.username }}</h3>
            <button class="btn-close" (click)="isAssignRolesModalOpen.set(false)">✕</button>
          </div>
          <div class="modal-body">
            <div class="checkbox-list">
              @for (r of roles(); track r.id) {
                <label class="checkbox-label">
                  <input
                    type="checkbox"
                    [checked]="editingUserRoleIds().has(r.id)"
                    (change)="toggleEditingRole(r.id)"
                  />
                  <span>{{ r.name }}</span>
                </label>
              }
            </div>
          </div>
          <div class="modal-footer">
            <button class="btn btn-secondary" (click)="isAssignRolesModalOpen.set(false)">انصراف</button>
            <button class="btn btn-primary" (click)="saveUserRoles()">ذخیره نقش‌ها</button>
          </div>
        </div>
      </div>
    }

    <!-- MODAL: CREATE ROLE -->
    @if (isRoleModalOpen()) {
      <div class="modal-backdrop" (click)="isRoleModalOpen.set(false)">
        <div class="modal-dialog" (click)="$event.stopPropagation()">
          <div class="modal-header">
            <h3 class="modal-title">ایجاد نقش سازمانی جدید</h3>
            <button class="btn-close" (click)="isRoleModalOpen.set(false)">✕</button>
          </div>
          <div class="modal-body">
            <div class="form-group">
              <label class="form-label">نام نقش:</label>
              <input type="text" class="form-control mono-font" [(ngModel)]="newRoleName" placeholder="مثال: ComplianceOfficer" />
            </div>

            <div class="form-group">
              <label class="form-label">توضیحات و حوزه دسترسی:</label>
              <textarea class="form-control" rows="3" [(ngModel)]="newRoleDescription"></textarea>
            </div>
          </div>
          <div class="modal-footer">
            <button class="btn btn-secondary" (click)="isRoleModalOpen.set(false)">انصراف</button>
            <button class="btn btn-primary" (click)="saveRole()">ایجاد نقش</button>
          </div>
        </div>
      </div>
    }

    <!-- MODAL: CREATE PERMISSION RULE -->
    @if (isRuleModalOpen()) {
      <div class="modal-backdrop" (click)="isRuleModalOpen.set(false)">
        <div class="modal-dialog" (click)="$event.stopPropagation()">
          <div class="modal-header">
            <h3 class="modal-title">ثبت قانون کنترل دسترسی دومنظوره (RBAC Rule)</h3>
            <button class="btn-close" (click)="isRuleModalOpen.set(false)">✕</button>
          </div>
          <div class="modal-body">
            <div class="form-group">
              <label class="form-label">نقش سازمانی هدف:</label>
              <select class="form-select mono-font" [(ngModel)]="ruleRoleId">
                @for (r of roles(); track r.id) {
                  <option [value]="r.id">{{ r.name }}</option>
                }
              </select>
            </div>

            <div class="form-group">
              <label class="form-label">سطح منبع (Resource Scope):</label>
              <select class="form-select" [(ngModel)]="ruleResourceType" (change)="onResourceTypeChange()">
                <option value="GLOBAL">سراسری (GLOBAL)</option>
                <option value="DATABASE">پایگاه داده (DATABASE)</option>
                <option value="SCHEMA">اسکیما (SCHEMA)</option>
                <option value="TABLE">جدول (TABLE)</option>
                <option value="CATEGORY">دسته‌بندی موضوعی (CATEGORY)</option>
              </select>
            </div>

            <div class="form-group">
              <label class="form-label">منبع هدف (Resource Target):</label>
              @if (ruleResourceType === 'GLOBAL') {
                <div class="info-alert">
                  <span>🌐 قانون به صورت سراسری (بر روی تمام منابع) اعمال خواهد شد.</span>
                </div>
              } @else if (ruleResourceType === 'DATABASE') {
                <select class="form-select mono-font" [(ngModel)]="ruleResourceId">
                  <option value="">-- لطفاً یک پایگاه داده انتخاب نمایید --</option>
                  @for (db of resourceDatabases(); track db.id) {
                    <option [value]="db.id">{{ db.name }} ({{ db.database_name }}) — {{ db.id }}</option>
                  }
                </select>
              } @else if (ruleResourceType === 'SCHEMA') {
                <select class="form-select mono-font" [(ngModel)]="ruleResourceId">
                  <option value="">-- لطفاً یک اسکیما انتخاب نمایید --</option>
                  @for (sch of resourceSchemas(); track sch.id) {
                    <option [value]="sch.id">{{ sch.name }} (اتصال: {{ sch.connection_name }}) — {{ sch.id }}</option>
                  }
                </select>
              } @else if (ruleResourceType === 'TABLE') {
                <select class="form-select mono-font" [(ngModel)]="ruleResourceId">
                  <option value="">-- لطفاً یک جدول کاتالوگ انتخاب نمایید --</option>
                  @for (tbl of resourceTables(); track tbl.id) {
                    <option [value]="tbl.id">{{ tbl.name }} (اسکیما: {{ tbl.schema_name }}) — {{ tbl.id }}</option>
                  }
                </select>
              } @else if (ruleResourceType === 'CATEGORY') {
                <select class="form-select mono-font" [(ngModel)]="ruleResourceId">
                  <option value="">-- لطفاً یک حوزه موضوعی انتخاب نمایید --</option>
                  @for (cat of resourceCategories(); track cat.id) {
                    <option [value]="cat.id">{{ cat.name }} — {{ cat.id }}</option>
                  }
                </select>
              }
            </div>

            <div class="form-group">
              <label class="form-label">عملیات مجاز/مسدود (Action):</label>
              <select class="form-select mono-font" [(ngModel)]="ruleAction">
                <option value="VIEW_METADATA">VIEW_METADATA</option>
                <option value="EDIT_DESCRIPTION">EDIT_DESCRIPTION</option>
                <option value="MANAGE_TAGS">MANAGE_TAGS</option>
                <option value="MANAGE_CONNECTIONS">MANAGE_CONNECTIONS</option>
                <option value="VIEW_AUDIT_LOGS">VIEW_AUDIT_LOGS</option>
                <option value="EXPORT_REPORTS">EXPORT_REPORTS</option>
              </select>
            </div>

            <div class="form-group">
              <label class="form-label">اثر قانون (Effect):</label>
              <select class="form-select" [(ngModel)]="ruleEffect">
                <option value="ALLOW">مجاز (ALLOW)</option>
                <option value="DENY">مسدود و ممنوع (DENY - اولویت بالاتر)</option>
              </select>
            </div>
          </div>
          <div class="modal-footer">
            <button class="btn btn-secondary" (click)="isRuleModalOpen.set(false)">انصراف</button>
            <button class="btn btn-primary" (click)="saveRule()">ثبت قطعی قانون و ثبت ممیزی</button>
          </div>
        </div>
      </div>
    }

    <!-- TOAST -->
    @if (toastMessage()) {
      <div class="toast-container">
        <div class="toast" [class.toast-success]="toastType() === 'success'" [class.toast-error]="toastType() === 'error'">
          {{ toastMessage() }}
        </div>
      </div>
    }
  `,
  styles: [`
    .iam-layout {
      padding: 24px 32px;
      direction: rtl;
      display: flex;
      flex-direction: column;
      gap: 20px;
    }

    .page-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .page-title {
      font-size: 1.4rem;
      font-weight: 800;
      color: var(--slate-900);
    }

    .page-subtitle {
      font-size: 0.85rem;
      color: var(--slate-500);
      margin-top: 4px;
    }

    .tabs-nav {
      display: flex;
      border-bottom: 2px solid var(--slate-200);
      gap: 8px;
    }

    .tab-link {
      padding: 10px 18px;
      background: transparent;
      border: none;
      border-bottom: 3px solid transparent;
      margin-bottom: -2px;
      font-family: var(--font-family-sans);
      font-size: 0.92rem;
      font-weight: 700;
      color: var(--slate-600);
      cursor: pointer;
      transition: all 0.15s ease;
    }

    .tab-link.active {
      color: var(--primary-700);
      border-bottom-color: var(--primary-600);
    }

    .table-card {
      background: #ffffff;
      border: 1px solid var(--slate-200);
      border-radius: var(--radius-lg);
      box-shadow: var(--shadow-sm);
      overflow: hidden;
    }

    .card-header {
      padding: 16px 20px;
      border-bottom: 1px solid var(--slate-200);
      background-color: var(--slate-50);
    }

    .card-header h2 {
      font-size: 1.05rem;
      font-weight: 700;
      color: var(--slate-800);
    }

    .data-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.85rem;
    }

    .data-table th, .data-table td {
      padding: 12px 18px;
      border-bottom: 1px solid var(--slate-200);
      text-align: right;
    }

    .data-table th {
      background-color: var(--slate-100);
      font-weight: 700;
      color: var(--slate-700);
    }

    .roles-flex {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
    }

    .actions-row {
      display: flex;
      gap: 6px;
    }

    .checkbox-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
      max-height: 180px;
      overflow-y: auto;
      border: 1px solid var(--slate-200);
      padding: 10px;
      border-radius: var(--radius-md);
    }

    .checkbox-label {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 0.85rem;
      cursor: pointer;
    }

    .btn-close {
      background: none;
      border: 1px solid var(--slate-300);
      border-radius: 50%;
      width: 28px;
      height: 28px;
      cursor: pointer;
    }

    .info-alert {
      padding: 10px 14px;
      background-color: #f1f5f9;
      border: 1px solid #cbd5e1;
      border-radius: var(--radius-md);
      color: var(--slate-600);
      font-size: 0.85rem;
    }
  `],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class IamManagementComponent implements OnInit {
  private readonly http = inject(HttpClient);

  readonly activeTab = signal<'users' | 'roles' | 'permissions'>('users');
  readonly users = signal<UserItem[]>([]);
  readonly roles = signal<RoleItem[]>([]);
  readonly permissionRules = signal<PermissionRuleItem[]>([]);

  // User modal
  readonly isUserModalOpen = signal<boolean>(false);
  newUserUsername = '';
  newUserFullName = '';
  newUserEmail = '';
  newUserPassword = '';
  newUserIsSuperadmin = false;
  newUserRoleIds = new Set<string>();

  // Assign roles modal
  readonly isAssignRolesModalOpen = signal<boolean>(false);
  readonly selectedUserForRoles = signal<UserItem | null>(null);
  readonly editingUserRoleIds = signal<Set<string>>(new Set());

  // Role modal
  readonly isRoleModalOpen = signal<boolean>(false);
  newRoleName = '';
  newRoleDescription = '';

  // Permission rule modal
  readonly isRuleModalOpen = signal<boolean>(false);
  ruleRoleId = '';
  ruleResourceType = 'GLOBAL';
  ruleResourceId = '';
  ruleAction = 'VIEW_METADATA';
  ruleEffect: 'ALLOW' | 'DENY' = 'ALLOW';

  // Resource options for dynamic dropdowns
  readonly resourceDatabases = signal<Array<{ id: string; name: string; database_name?: string }>>([]);
  readonly resourceSchemas = signal<Array<{ id: string; name: string; connection_name?: string }>>([]);
  readonly resourceTables = signal<Array<{ id: string; name: string; schema_name?: string }>>([]);
  readonly resourceCategories = signal<Array<{ id: string; name: string; description?: string }>>([]);

  readonly toastMessage = signal<string | null>(null);
  readonly toastType = signal<'success' | 'error'>('success');

  ngOnInit(): void {
    this.loadUsers();
    this.loadRoles();
    this.loadPermissions();
  }

  loadUsers(): void {
    this.http.get<UserItem[]>('/api/v1/users').subscribe({
      next: (data) => this.users.set(data),
      error: () => this.showToast('خطا در دریافت لیست کاربران', 'error'),
    });
  }

  loadRoles(): void {
    this.http.get<RoleItem[]>('/api/v1/roles').subscribe({
      next: (data) => {
        this.roles.set(data);
        if (data.length > 0 && !this.ruleRoleId) {
          this.ruleRoleId = data[0].id;
        }
      },
      error: () => this.showToast('خطا در دریافت لیست نقش‌ها', 'error'),
    });
  }

  loadPermissions(): void {
    this.http.get<PermissionRuleItem[]>('/api/v1/permissions').subscribe({
      next: (data) => this.permissionRules.set(data),
      error: () => this.showToast('خطا در دریافت ماتریس قوانین دسترسی', 'error'),
    });
  }

  getRoleName(roleId: string): string {
    const r = this.roles().find((item) => item.id === roleId);
    return r ? r.name : roleId.substring(0, 8);
  }

  // User Actions
  openCreateUserModal(): void {
    this.newUserUsername = '';
    this.newUserFullName = '';
    this.newUserEmail = '';
    this.newUserPassword = '';
    this.newUserIsSuperadmin = false;
    this.newUserRoleIds = new Set();
    this.isUserModalOpen.set(true);
  }

  toggleRoleSelection(roleId: string): void {
    if (this.newUserRoleIds.has(roleId)) {
      this.newUserRoleIds.delete(roleId);
    } else {
      this.newUserRoleIds.add(roleId);
    }
  }

  saveUser(): void {
    if (!this.newUserUsername || !this.newUserEmail || !this.newUserPassword) return;

    const payload = {
      username: this.newUserUsername,
      full_name: this.newUserFullName,
      email: this.newUserEmail,
      password: this.newUserPassword,
      is_superadmin: this.newUserIsSuperadmin,
      role_ids: Array.from(this.newUserRoleIds),
    };

    this.http.post('/api/v1/users', payload).subscribe({
      next: () => {
        this.showToast('کاربر سازمانی با موفقیت ایجاد شد');
        this.isUserModalOpen.set(false);
        this.loadUsers();
      },
      error: () => this.showToast('خطا در ایجاد کاربر', 'error'),
    });
  }

  toggleUserStatus(user: UserItem): void {
    const nextStatus = user.status === 'ACTIVE' ? 'SUSPENDED' : 'ACTIVE';
    this.http.patch(`/api/v1/users/${user.id}/status`, { status: nextStatus }).subscribe({
      next: () => {
        this.showToast(`وضعیت کاربر به ${nextStatus === 'ACTIVE' ? 'فعال' : 'معلق'} تغییر یافت`);
        this.loadUsers();
      },
      error: () => this.showToast('خطا در تغییر وضعیت کاربر', 'error'),
    });
  }

  openAssignRolesModal(user: UserItem): void {
    this.selectedUserForRoles.set(user);
    this.editingUserRoleIds.set(new Set(user.role_ids || []));
    this.isAssignRolesModalOpen.set(true);
  }

  toggleEditingRole(roleId: string): void {
    this.editingUserRoleIds.update((set) => {
      const next = new Set(set);
      if (next.has(roleId)) next.delete(roleId);
      else next.add(roleId);
      return next;
    });
  }

  saveUserRoles(): void {
    const user = this.selectedUserForRoles();
    if (!user) return;

    this.http.put(`/api/v1/users/${user.id}/roles`, {
      role_ids: Array.from(this.editingUserRoleIds()),
    }).subscribe({
      next: () => {
        this.showToast('نقش‌های کاربر با موفقیت ذخیره شد');
        this.isAssignRolesModalOpen.set(false);
        this.loadUsers();
      },
      error: () => this.showToast('خطا در ذخیره نقش‌های کاربر', 'error'),
    });
  }

  // Role Actions
  openCreateRoleModal(): void {
    this.newRoleName = '';
    this.newRoleDescription = '';
    this.isRoleModalOpen.set(true);
  }

  saveRole(): void {
    if (!this.newRoleName) return;

    this.http.post('/api/v1/roles', {
      name: this.newRoleName,
      description: this.newRoleDescription,
    }).subscribe({
      next: () => {
        this.showToast('نقش جدید با موفقیت ایجاد شد');
        this.isRoleModalOpen.set(false);
        this.loadRoles();
      },
      error: () => this.showToast('خطا در ایجاد نقش', 'error'),
    });
  }

  deleteRole(role: RoleItem): void {
    if (!confirm(`آیا از حذف نقش "${role.name}" اطمینان دارید؟`)) return;

    this.http.delete(`/api/v1/roles/${role.id}`).subscribe({
      next: () => {
        this.showToast('نقش با موفقیت حذف شد');
        this.loadRoles();
      },
      error: () => this.showToast('خطا در حذف نقش', 'error'),
    });
  }

  // Permission Rule Actions
  openCreateRuleModal(): void {
    this.ruleResourceId = '';
    this.ruleResourceType = 'GLOBAL';
    this.ruleAction = 'VIEW_METADATA';
    this.ruleEffect = 'ALLOW';
    if (this.roles().length > 0 && !this.ruleRoleId) {
      this.ruleRoleId = this.roles()[0].id;
    }
    this.loadResourceOptions();
    this.isRuleModalOpen.set(true);
  }

  onResourceTypeChange(): void {
    this.ruleResourceId = '';
  }

  loadResourceOptions(): void {
    // 1. Databases from /api/v1/connections
    this.http.get<any[]>('/api/v1/connections').subscribe({
      next: (conns) => {
        this.resourceDatabases.set(
          (conns || []).map((c) => ({
            id: c.id,
            name: c.name,
            database_name: c.database_name,
          }))
        );
      },
      error: () => {},
    });

    // 2. Schemas from physical tree /api/v1/tables/tree/physical
    this.http.get<any[]>('/api/v1/tables/tree/physical').subscribe({
      next: (tree) => {
        const schemas: Array<{ id: string; name: string; connection_name?: string }> = [];
        (tree || []).forEach((conn: any) => {
          (conn.schemas || []).forEach((sch: any) => {
            schemas.push({
              id: sch.id,
              name: sch.name,
              connection_name: conn.name,
            });
          });
        });
        this.resourceSchemas.set(schemas);
      },
      error: () => {},
    });

    // 3. Tables from /api/v1/search?limit=100
    this.http.get<any>('/api/v1/search?limit=100').subscribe({
      next: (res) => {
        const tables = (res?.items || []).map((t: any) => ({
          id: t.id,
          name: t.name,
          schema_name: t.schema_name,
        }));
        this.resourceTables.set(tables);
      },
      error: () => {},
    });

    // 4. Categories from /api/v1/categories
    this.http.get<any[]>('/api/v1/categories').subscribe({
      next: (cats) => {
        this.resourceCategories.set(
          (cats || []).map((c) => ({
            id: c.id,
            name: c.name,
            description: c.description,
          }))
        );
      },
      error: () => {},
    });
  }

  saveRule(): void {
    if (!this.ruleRoleId) return;

    const payload = {
      role_id: this.ruleRoleId,
      resource_type: this.ruleResourceType,
      resource_id: this.ruleResourceId.trim() ? this.ruleResourceId.trim() : null,
      action: this.ruleAction,
      effect: this.ruleEffect,
    };

    this.http.post('/api/v1/permissions', payload).subscribe({
      next: () => {
        this.showToast('قانون دسترسی با موفقیت در ماتریس امنیتی و ممیزی ثبت شد');
        this.isRuleModalOpen.set(false);
        this.loadPermissions();
      },
      error: () => this.showToast('خطا در ثبت قانون دسترسی', 'error'),
    });
  }

  revokeRule(ruleId: string): void {
    if (!confirm('آیا از ابطال این قانون دسترسی اطمینان دارید؟')) return;

    this.http.delete(`/api/v1/permissions/${ruleId}`).subscribe({
      next: () => {
        this.showToast('قانون دسترسی ابطال گردید');
        this.loadPermissions();
      },
      error: () => this.showToast('خطا در ابطال قانون', 'error'),
    });
  }

  showToast(msg: string, type: 'success' | 'error' = 'success'): void {
    this.toastMessage.set(msg);
    this.toastType.set(type);
    setTimeout(() => this.toastMessage.set(null), 3500);
  }
}

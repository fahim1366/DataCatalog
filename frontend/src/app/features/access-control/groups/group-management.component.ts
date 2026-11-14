import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { GroupManagementService } from '../services/group-management.service';
import { RoleManagementService } from '../services/role-management.service';
import { UserManagementService } from '../services/user-management.service';
import {
  GroupCreateDto,
  GroupItem,
  GroupMemberItem,
  GroupUpdateDto,
  RoleItem,
  UserItem,
} from '../models/access-control.models';

@Component({
  selector: 'app-group-management',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="groups-container" dir="rtl">
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
            <span>🏢</span>
            مدیریت گروه‌های سازمانی (Group Management)
          </h1>
          <p class="page-subtitle">
            دسته‌بندی پرسنل در گروه‌های کاری، انتساب دسته‌جمعی نقش‌ها و اعمال قوانین دسترسی به تیم‌ها
          </p>
        </div>
        <button class="btn btn-primary" (click)="openCreateModal()">
          <span>+ تعریف گروه جدید</span>
        </button>
      </header>

      <!-- Toolbar -->
      <div class="toolbar-card">
        <div class="toolbar-grid">
          <div class="form-group search-group">
            <label>جستجوی گروه</label>
            <input
              type="text"
              class="form-control"
              placeholder="جستجو بر اساس نام یا توضیحات گروه..."
              [ngModel]="searchQuery()"
              (ngModelChange)="searchQuery.set($event)"
              (keyup.enter)="loadGroups()"
            />
          </div>
          <div class="form-group">
            <label>وضعیت فعالیت</label>
            <select
              class="form-select"
              [ngModel]="statusFilter()"
              (ngModelChange)="statusFilter.set($event); loadGroups()"
            >
              <option value="">همه گروه‌ها</option>
              <option [ngValue]="true">فعال</option>
              <option [ngValue]="false">غیرفعال</option>
            </select>
          </div>
          <div class="form-group toolbar-btn">
            <button class="btn btn-secondary" (click)="loadGroups()">🔄 بازخوانی</button>
          </div>
        </div>
      </div>

      <!-- Groups Grid -->
      <div class="grid-card">
        <div class="table-responsive">
          <table class="data-table">
            <thead>
              <tr>
                <th style="width: 50px;">ردیف</th>
                <th>نام گروه سازمانی</th>
                <th>شرح و ماموریت سازمانی</th>
                <th class="th-center">تعداد اعضا</th>
                <th class="th-center">نقش‌های گروه</th>
                <th class="th-center">وضعیت</th>
                <th>تاریخ ایجاد</th>
                <th class="th-center" style="width: 220px;">عملیات</th>
              </tr>
            </thead>
            <tbody>
              @if (isLoading()) {
                <tr>
                  <td colspan="8" class="td-loading">در حال بارگذاری اطلاعات گروه‌ها...</td>
                </tr>
              } @else if (groups().length === 0) {
                <tr>
                  <td colspan="8" class="td-empty">گروهی یافت نشد.</td>
                </tr>
              } @else {
                @for (g of groups(); track g.id; let idx = $index) {
                  <tr>
                    <td class="td-muted">{{ (currentPage() - 1) * pageSize() + idx + 1 }}</td>
                    <td class="font-bold">
                      <span class="group-icon-mini">🏢</span>
                      {{ g.name }}
                    </td>
                    <td class="text-desc">{{ g.description || '—' }}</td>
                    <td class="th-center">
                      <button class="pill-btn pill-blue" (click)="openMembersModal(g)">
                        👥 {{ g.user_count }} کاربر
                      </button>
                    </td>
                    <td class="th-center">
                      <button class="pill-btn pill-purple" (click)="openRolesModal(g)">
                        🛡️ {{ g.role_count }} نقش
                      </button>
                    </td>
                    <td class="th-center">
                      @if (g.is_active) {
                        <span class="status-pill status-active">فعال</span>
                      } @else {
                        <span class="status-pill status-deactivated">غیرفعال</span>
                      }
                    </td>
                    <td class="td-date">{{ formatDate(g.created_at) }}</td>
                    <td class="th-center td-actions">
                      <button class="btn btn-outline btn-xs" (click)="openMembersModal(g)">
                        اعضا
                      </button>
                      <button class="btn btn-outline btn-xs" (click)="openRolesModal(g)">
                        نقش‌ها
                      </button>
                      <button class="btn btn-outline btn-xs" (click)="openEditModal(g)">
                        ✏️
                      </button>
                      <button class="btn btn-outline-danger btn-xs" (click)="deleteGroup(g)">
                        🗑️
                      </button>
                    </td>
                  </tr>
                }
              }
            </tbody>
          </table>
        </div>

        <div class="pagination-footer">
          <span>مجموع: {{ totalGroups() }} گروه</span>
          <div class="pagination-controls">
            <button class="btn-page" [disabled]="currentPage() === 1" (click)="goToPage(currentPage() - 1)">
              قبلی
            </button>
            <span>صفحه {{ currentPage() }} از {{ totalPages() }}</span>
            <button class="btn-page" [disabled]="currentPage() >= totalPages()" (click)="goToPage(currentPage() + 1)">
              بعدی
            </button>
          </div>
        </div>
      </div>

      <!-- CREATE GROUP MODAL -->
      @if (showCreateModal()) {
        <div class="modal-backdrop animate-fade">
          <div class="modal-card">
            <div class="modal-header">
              <h3>+ تعریف گروه سازمانی جدید</h3>
              <button class="modal-close" (click)="showCreateModal.set(false)">✕</button>
            </div>
            <div class="modal-body">
              <div class="form-group mb-3">
                <label>نام گروه (الزامی) *</label>
                <input
                  type="text"
                  class="form-control"
                  placeholder="مانند: تیم مهندسی داده"
                  [(ngModel)]="newGroup.name"
                />
              </div>
              <div class="form-group mb-3">
                <label>توضیحات و ماموریت گروه</label>
                <textarea
                  class="form-control"
                  rows="3"
                  placeholder="شرح وظایف و مسئولیت‌های این گروه کاری..."
                  [(ngModel)]="newGroup.description"
                ></textarea>
              </div>
              <div class="form-group mb-3">
                <label>انتساب اولیه نقش‌ها به اعضای گروه</label>
                <div class="checkbox-list">
                  @for (r of allRoles(); track r.id) {
                    <label class="checkbox-item">
                      <input
                        type="checkbox"
                        [checked]="isRoleSelectedForNew(r.id)"
                        (change)="toggleRoleForNew(r.id, $event)"
                      />
                      <span>{{ r.name }}</span>
                    </label>
                  }
                </div>
              </div>
            </div>
            <div class="modal-footer">
              <button class="btn btn-secondary" (click)="showCreateModal.set(false)">انصراف</button>
              <button class="btn btn-primary" (click)="submitCreateGroup()">ایجاد گروه</button>
            </div>
          </div>
        </div>
      }

      <!-- EDIT GROUP MODAL -->
      @if (showEditModal()) {
        <div class="modal-backdrop animate-fade">
          <div class="modal-card">
            <div class="modal-header">
              <h3>ویرایش گروه: {{ activeGroup()?.name }}</h3>
              <button class="modal-close" (click)="showEditModal.set(false)">✕</button>
            </div>
            <div class="modal-body">
              <div class="form-group mb-3">
                <label>نام گروه</label>
                <input type="text" class="form-control" [(ngModel)]="editGroupDto.name" />
              </div>
              <div class="form-group mb-3">
                <label>توضیحات</label>
                <textarea class="form-control" rows="3" [(ngModel)]="editGroupDto.description"></textarea>
              </div>
              <div class="form-group">
                <label class="checkbox-item">
                  <input type="checkbox" [(ngModel)]="editGroupDto.is_active" />
                  <span>وضعیت فعال بودن گروه</span>
                </label>
              </div>
            </div>
            <div class="modal-footer">
              <button class="btn btn-secondary" (click)="showEditModal.set(false)">انصراف</button>
              <button class="btn btn-primary" (click)="submitEditGroup()">ثبت تغییرات</button>
            </div>
          </div>
        </div>
      }

      <!-- MEMBERS MODAL -->
      @if (showMembersModal()) {
        <div class="modal-backdrop animate-fade">
          <div class="modal-card">
            <div class="modal-header">
              <h3>👥 اعضای گروه: {{ activeGroup()?.name }}</h3>
              <button class="modal-close" (click)="showMembersModal.set(false)">✕</button>
            </div>
            <div class="modal-body">
              <div class="quick-add-member mb-3">
                <select class="form-select select-compact" [(ngModel)]="userIdToAdd">
                  <option value="">-- افزودن کاربر به این گروه --</option>
                  @for (u of allUsers(); track u.id) {
                    @if (!isUserInGroup(u.id)) {
                      <option [value]="u.id">{{ u.full_name }} ({{ u.username }})</option>
                    }
                  }
                </select>
                <button
                  class="btn btn-primary btn-sm"
                  [disabled]="!userIdToAdd"
                  (click)="addUserToActiveGroup()"
                >
                  + افزودن
                </button>
              </div>

              <div class="members-table-wrap">
                <table class="data-table data-table-sm">
                  <thead>
                    <tr>
                      <th>نام و نام خانوادگی</th>
                      <th>نام کاربری</th>
                      <th>ایمیل</th>
                      <th class="th-center">عملیات</th>
                    </tr>
                  </thead>
                  <tbody>
                    @for (m of groupMembers(); track m.id) {
                      <tr>
                        <td>{{ m.full_name }}</td>
                        <td><code>{{ m.username }}</code></td>
                        <td>{{ m.email }}</td>
                        <td class="th-center">
                          <button
                            class="btn btn-outline-danger btn-xs"
                            (click)="removeUserFromActiveGroup(m.id)"
                          >
                            حذف از گروه
                          </button>
                        </td>
                      </tr>
                    }
                    @if (groupMembers().length === 0) {
                      <tr>
                        <td colspan="4" class="td-empty">این گروه هنوز هیچ عضوی ندارد.</td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
            </div>
            <div class="modal-footer">
              <button class="btn btn-secondary" (click)="showMembersModal.set(false)">بستن</button>
            </div>
          </div>
        </div>
      }

      <!-- ROLES MODAL -->
      @if (showRolesModal()) {
        <div class="modal-backdrop animate-fade">
          <div class="modal-card">
            <div class="modal-header">
              <h3>🛡️ نقش‌های سازمانی گروه: {{ activeGroup()?.name }}</h3>
              <button class="modal-close" (click)="showRolesModal.set(false)">✕</button>
            </div>
            <div class="modal-body">
              <p class="text-sm text-muted mb-3">
                نقش‌های انتخاب شده به صورت خودکار به تمام اعضای فعلی و آینده این گروه ارث می‌رسد:
              </p>
              <div class="checkbox-list">
                @for (r of allRoles(); track r.id) {
                  <label class="checkbox-item-card">
                    <input
                      type="checkbox"
                      [checked]="selectedGroupRoleIds().has(r.id)"
                      (change)="toggleRoleForGroup(r.id, $event)"
                    />
                    <div>
                      <strong>{{ r.name }}</strong>
                      <p class="text-xs text-muted mb-0">{{ r.description || 'بدون شرح' }}</p>
                    </div>
                  </label>
                }
              </div>
            </div>
            <div class="modal-footer">
              <button class="btn btn-secondary" (click)="showRolesModal.set(false)">انصراف</button>
              <button class="btn btn-primary" (click)="saveGroupRoles()">ذخیره نقش‌ها</button>
            </div>
          </div>
        </div>
      }
    </div>
  `,
  styles: [
    `
      .groups-container {
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
      .toolbar-card, .grid-card {
        background: white;
        border-radius: 12px;
        border: 1px solid #e2e8f0;
        padding: 1.25rem 1.5rem;
      }
      .grid-card { padding: 0; overflow: hidden; }
      .toolbar-grid {
        display: grid;
        grid-template-columns: 2fr 1fr auto;
        gap: 1rem;
        align-items: flex-end;
      }
      .form-group { display: flex; flex-direction: column; gap: 0.35rem; }
      .form-group label { font-size: 0.8rem; font-weight: 600; color: #475569; }
      .form-control, .form-select {
        font-family: inherit;
        font-size: 0.85rem;
        padding: 0.5rem 0.75rem;
        border-radius: 8px;
        border: 1px solid #cbd5e1;
        outline: none;
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
      .text-desc { color: #475569; max-width: 300px; }
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
      .pill-purple { background: #ede9fe; color: #6d28d9; border-color: #ddd6fe; }
      .status-pill {
        font-size: 0.75rem;
        font-weight: 600;
        padding: 0.2rem 0.6rem;
        border-radius: 9999px;
      }
      .status-active { background: #dcfce7; color: #166534; }
      .status-deactivated { background: #fee2e2; color: #991b1b; }
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
      .btn-xs { padding: 0.2rem 0.45rem; font-size: 0.75rem; }
      .btn-sm { padding: 0.35rem 0.75rem; font-size: 0.8rem; }
      .td-actions { display: flex; gap: 0.35rem; justify-content: center; }
      .pagination-footer {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 0.75rem 1.5rem;
        background: #f8fafc;
        border-top: 1px solid #e2e8f0;
        font-size: 0.85rem;
      }
      .pagination-controls { display: flex; align-items: center; gap: 0.5rem; }
      .btn-page {
        font-family: inherit;
        padding: 0.3rem 0.6rem;
        border: 1px solid #cbd5e1;
        background: white;
        border-radius: 6px;
        cursor: pointer;
      }
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
      .checkbox-list { display: flex; flex-direction: column; gap: 0.5rem; }
      .checkbox-item { display: flex; align-items: center; gap: 0.5rem; font-size: 0.85rem; cursor: pointer; }
      .checkbox-item-card {
        display: flex;
        align-items: flex-start;
        gap: 0.75rem;
        padding: 0.75rem;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        cursor: pointer;
      }
      .quick-add-member { display: flex; gap: 0.5rem; }
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
      .mb-3 { margin-bottom: 1rem; }
      .animate-fade { animation: fadeIn 0.2s ease-in-out; }
      @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
    `,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class GroupManagementComponent implements OnInit {
  private groupService = inject(GroupManagementService);
  private roleService = inject(RoleManagementService);
  private userService = inject(UserManagementService);

  groups = signal<GroupItem[]>([]);
  totalGroups = signal<number>(0);
  currentPage = signal<number>(1);
  pageSize = signal<number>(10);
  totalPages = signal<number>(1);
  searchQuery = signal<string>('');
  statusFilter = signal<boolean | ''>('');
  isLoading = signal<boolean>(false);
  successMessage = signal<string | null>(null);
  errorMessage = signal<string | null>(null);

  allRoles = signal<RoleItem[]>([]);
  allUsers = signal<UserItem[]>([]);

  // Active group for modals
  activeGroup = signal<GroupItem | null>(null);
  groupMembers = signal<GroupMemberItem[]>([]);
  selectedGroupRoleIds = signal<Set<string>>(new Set());

  // Modals
  showCreateModal = signal<boolean>(false);
  showEditModal = signal<boolean>(false);
  showMembersModal = signal<boolean>(false);
  showRolesModal = signal<boolean>(false);

  // Forms
  newGroup: GroupCreateDto = { name: '', description: '', is_active: true, role_ids: [] };
  newGroupRoleIds: Set<string> = new Set();
  editGroupDto: GroupUpdateDto = {};
  userIdToAdd = '';

  ngOnInit(): void {
    this.loadGroups();
    this.loadReferences();
  }

  loadReferences(): void {
    this.roleService.getAllRolesList().subscribe({
      next: (r) => this.allRoles.set(r),
      error: () => {},
    });
    this.userService.getUsers({}).subscribe({
      next: (res) => {
        const items = Array.isArray(res) ? res : res.items || [];
        this.allUsers.set(items);
      },
      error: () => {},
    });
  }

  loadGroups(): void {
    this.isLoading.set(true);
    this.groupService
      .getGroups({
        page: this.currentPage(),
        page_size: this.pageSize(),
        search: this.searchQuery() || undefined,
        is_active: this.statusFilter() === '' ? undefined : (this.statusFilter() as boolean),
      })
      .subscribe({
        next: (resp) => {
          this.isLoading.set(false);
          if (Array.isArray(resp)) {
            this.groups.set(resp);
            this.totalGroups.set(resp.length);
            this.totalPages.set(1);
          } else {
            this.groups.set(resp.items);
            this.totalGroups.set(resp.total);
            this.totalPages.set(resp.total_pages);
          }
        },
        error: (err) => {
          this.isLoading.set(false);
          this.showError(err?.error?.detail || 'خطا در بارگذاری گروه‌ها.');
        },
      });
  }

  goToPage(p: number): void {
    if (p >= 1 && p <= this.totalPages()) {
      this.currentPage.set(p);
      this.loadGroups();
    }
  }

  openCreateModal(): void {
    this.newGroup = { name: '', description: '', is_active: true, role_ids: [] };
    this.newGroupRoleIds = new Set();
    this.showCreateModal.set(true);
  }

  isRoleSelectedForNew(roleId: string): boolean {
    return this.newGroupRoleIds.has(roleId);
  }

  toggleRoleForNew(roleId: string, event: Event): void {
    const checked = (event.target as HTMLInputElement).checked;
    if (checked) this.newGroupRoleIds.add(roleId);
    else this.newGroupRoleIds.delete(roleId);
  }

  submitCreateGroup(): void {
    if (!this.newGroup.name) {
      this.showError('لطفاً نام گروه را وارد نمایید.');
      return;
    }
    this.newGroup.role_ids = Array.from(this.newGroupRoleIds);
    this.groupService.createGroup(this.newGroup).subscribe({
      next: (g) => {
        this.showSuccess(`گروه «${g.name}» با موفقیت ایجاد شد.`);
        this.showCreateModal.set(false);
        this.loadGroups();
      },
      error: (err) => this.showError(err?.error?.detail || 'خطا در ایجاد گروه.'),
    });
  }

  openEditModal(g: GroupItem): void {
    this.activeGroup.set(g);
    this.editGroupDto = { name: g.name, description: g.description, is_active: g.is_active };
    this.showEditModal.set(true);
  }

  submitEditGroup(): void {
    const g = this.activeGroup();
    if (!g) return;

    this.groupService.updateGroup(g.id, this.editGroupDto).subscribe({
      next: () => {
        this.showSuccess('اطلاعات گروه به‌روزرسانی شد.');
        this.showEditModal.set(false);
        this.loadGroups();
      },
      error: (err) => this.showError(err?.error?.detail || 'خطا در ویرایش گروه.'),
    });
  }

  deleteGroup(g: GroupItem): void {
    if (!confirm(`آیا از حذف گروه «${g.name}» اطمینان دارید؟`)) return;

    this.groupService.deleteGroup(g.id).subscribe({
      next: () => {
        this.showSuccess(`گروه «${g.name}» حذف گردید.`);
        this.loadGroups();
      },
      error: (err) => this.showError(err?.error?.detail || 'خطا در حذف گروه.'),
    });
  }

  openMembersModal(g: GroupItem): void {
    this.activeGroup.set(g);
    this.userIdToAdd = '';
    this.groupService.getGroupMembers(g.id).subscribe({
      next: (members) => {
        this.groupMembers.set(members);
        this.showMembersModal.set(true);
      },
      error: () => this.showError('خطا در بارگذاری اعضای گروه.'),
    });
  }

  isUserInGroup(userId: string): boolean {
    return this.groupMembers().some((m) => m.id === userId);
  }

  addUserToActiveGroup(): void {
    const g = this.activeGroup();
    if (!g || !this.userIdToAdd) return;

    this.groupService.addGroupMember(g.id, this.userIdToAdd).subscribe({
      next: () => {
        this.showSuccess('کاربر به گروه اضافه شد.');
        this.userIdToAdd = '';
        this.openMembersModal(g);
        this.loadGroups();
      },
      error: (err) => this.showError(err?.error?.detail || 'خطا در افزودن کاربر به گروه.'),
    });
  }

  removeUserFromActiveGroup(userId: string): void {
    const g = this.activeGroup();
    if (!g) return;

    this.groupService.removeGroupMember(g.id, userId).subscribe({
      next: () => {
        this.showSuccess('کاربر از گروه حذف شد.');
        this.openMembersModal(g);
        this.loadGroups();
      },
      error: (err) => this.showError(err?.error?.detail || 'خطا در حذف کاربر از گروه.'),
    });
  }

  openRolesModal(g: GroupItem): void {
    this.activeGroup.set(g);
    this.selectedGroupRoleIds.set(new Set(g.role_ids));
    this.showRolesModal.set(true);
  }

  toggleRoleForGroup(roleId: string, event: Event): void {
    const checked = (event.target as HTMLInputElement).checked;
    const current = new Set(this.selectedGroupRoleIds());
    if (checked) current.add(roleId);
    else current.delete(roleId);
    this.selectedGroupRoleIds.set(current);
  }

  saveGroupRoles(): void {
    const g = this.activeGroup();
    if (!g) return;

    const roleIds = Array.from(this.selectedGroupRoleIds());
    this.groupService.updateGroupRoles(g.id, roleIds).subscribe({
      next: () => {
        this.showSuccess('نقش‌های گروه با موفقیت به‌روزرسانی شد.');
        this.showRolesModal.set(false);
        this.loadGroups();
      },
      error: (err) => this.showError(err?.error?.detail || 'خطا در ذخیره نقش‌های گروه.'),
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

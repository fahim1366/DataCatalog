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
import { TagChipComponent } from '../catalog/components/tag-chip/tag-chip.component';
import { TagItem, CategoryTreeNode } from '../catalog/models/catalog.models';

interface CategoryItem {
  id: string;
  name: string;
  description?: string;
  parent_id?: string | null;
  assigned_tables_count: number;
}

@Component({
  selector: 'app-taxonomy-management',
  standalone: true,
  imports: [CommonModule, FormsModule, TagChipComponent],
  template: `
    <div class="taxonomy-layout">
      <!-- Header -->
      <header class="page-header">
        <div>
          <h1 class="page-title">مدیریت تاکسونومی، تگ‌ها و دسته‌بندی‌های موضوعی</h1>
          <p class="page-subtitle">تعریف ساختار موضوعی سازمان، واژه‌نامه داده‌ها و برچسب‌های حاکمیتی متادیتا</p>
        </div>

        <div class="header-actions">
          @if (activeTab() === 'tags') {
            <button class="btn btn-primary" (click)="openCreateTagModal()">
              <span>+ ثبت برچسب سازمانی جدید</span>
            </button>
          } @else {
            <button class="btn btn-primary" (click)="openCreateCategoryModal(null)">
              <span>+ ایجاد دسته‌بندی اصلی جدید</span>
            </button>
          }
        </div>
      </header>

      <!-- Tabs Nav -->
      <div class="tabs-nav">
        <button
          class="tab-link"
          [class.active]="activeTab() === 'tags'"
          (click)="activeTab.set('tags')"
        >
          🏷️ برچسب‌ها و تگ‌های حاکمیتی (Governed Tags)
        </button>
        <button
          class="tab-link"
          [class.active]="activeTab() === 'categories'"
          (click)="activeTab.set('categories')"
        >
          📂 درخت دسته‌بندی‌های موضوعی (Category Hierarchy)
        </button>
      </div>

      <!-- Tab Content Area -->
      <div class="tab-content">
        <!-- TAB 1: GOVERNED TAGS -->
        @if (activeTab() === 'tags') {
          <div class="table-card">
            <div class="card-header">
              <h2>فهرست برچسب‌های سازمانی ({{ tags().length }})</h2>
              <span class="text-muted">برچسب‌های استاندارد جهت رده‌بندی امنیتی، مالکیت و نیازمندی‌های انطباق قانونی</span>
            </div>

            <table class="data-table">
              <thead>
                <tr>
                  <th>نمایش برچسب</th>
                  <th>نام تگ</th>
                  <th>رنگ شناسه</th>
                  <th>توضیحات حاکمیتی</th>
                  <th>تعداد موجودیت‌های متصل</th>
                  <th>عملیات</th>
                </tr>
              </thead>
              <tbody>
                @for (tag of tags(); track tag.id) {
                  <tr>
                    <td>
                      <app-tag-chip [tag]="tag" [removable]="false"></app-tag-chip>
                    </td>
                    <td class="mono-font fw-bold">{{ tag.name }}</td>
                    <td>
                      <div class="color-badge" [style.background-color]="tag.color"></div>
                      <span class="mono-font">{{ tag.color }}</span>
                    </td>
                    <td>{{ tag.description || 'بدون توضیح' }}</td>
                    <td>
                      <span class="badge badge-internal">{{ tag.assignment_count || 0 }} مورد</span>
                    </td>
                    <td>
                      <div class="actions-row">
                        <button class="btn btn-secondary btn-sm" (click)="openEditTagModal(tag)">ویرایش</button>
                        <button class="btn btn-danger btn-sm" (click)="deleteTag(tag)">حذف</button>
                      </div>
                    </td>
                  </tr>
                } @empty {
                  <tr>
                    <td colspan="6" class="text-center">هیچ تگی ثبت نشده است.</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        }

        <!-- TAB 2: CATEGORY TREE -->
        @if (activeTab() === 'categories') {
          <div class="table-card">
            <div class="card-header">
              <h2>درخت سلسله‌مراتبی حوزه‌های کسب‌وکار (Enterprise Core Domains)</h2>
              <span class="text-muted">ارث‌بری قوانین امنیتی به صورت آبشاری از گره‌های والد به گره‌های فرزند اعمال می‌شود</span>
            </div>

            <div class="category-tree-wrapper">
              @for (cat of categoryTree(); track cat.id) {
                <div class="cat-tree-node">
                  <div class="node-row root-node">
                    <span class="folder-icon">📂</span>
                    <strong class="cat-title">{{ cat.name }}</strong>
                    <span class="text-muted cat-desc">{{ cat.description }}</span>
                    <span class="badge badge-internal">{{ cat.tables.length }} جدول</span>
                    <div class="node-actions">
                      <button class="btn btn-secondary btn-sm" (click)="openCreateCategoryModal(cat.id)">+ زیردسته</button>
                      <button class="btn btn-secondary btn-sm" (click)="openEditCategoryModal(cat)">ویرایش</button>
                      <button class="btn btn-danger btn-sm" (click)="deleteCategory(cat.id)">حذف</button>
                    </div>
                  </div>

                  <!-- Children Nodes -->
                  @if (cat.children && cat.children.length > 0) {
                    <div class="sub-tree">
                      @for (sub of cat.children; track sub.id) {
                        <div class="cat-tree-node sub-node">
                          <div class="node-row">
                            <span class="folder-icon">↳ 📁</span>
                            <span class="cat-title">{{ sub.name }}</span>
                            <span class="text-muted cat-desc">{{ sub.description }}</span>
                            <span class="badge badge-internal">{{ sub.tables.length }} جدول</span>
                            <div class="node-actions">
                              <button class="btn btn-secondary btn-sm" (click)="openEditCategoryModal(sub)">ویرایش</button>
                              <button class="btn btn-danger btn-sm" (click)="deleteCategory(sub.id)">حذف</button>
                            </div>
                          </div>
                        </div>
                      }
                    </div>
                  }
                </div>
              } @empty {
                <div class="empty-state">دسته‌بندی موضوعی تعریف نشده است.</div>
              }
            </div>
          </div>
        }
      </div>
    </div>

    <!-- MODAL: CREATE / EDIT TAG -->
    @if (isTagModalOpen()) {
      <div class="modal-backdrop" (click)="closeTagModal()">
        <div class="modal-dialog" (click)="$event.stopPropagation()">
          <div class="modal-header">
            <h3 class="modal-title">{{ editingTagId() ? 'ویرایش برچسب سازمانی' : 'ایجاد برچسب جدید' }}</h3>
            <button class="btn-close" (click)="closeTagModal()">✕</button>
          </div>
          <div class="modal-body">
            <div class="form-group">
              <label class="form-label">نام برچسب (انگلیسی یا فارسی):</label>
              <input type="text" class="form-control" [(ngModel)]="tagFormName" placeholder="مثال: PII یا Financial" />
            </div>

            <div class="form-group">
              <label class="form-label">رنگ نشانگر:</label>
              <div class="color-presets">
                @for (c of colorPresets; track c) {
                  <button
                    type="button"
                    class="preset-btn"
                    [style.background-color]="c"
                    [class.selected]="tagFormColor === c"
                    (click)="tagFormColor = c"
                  ></button>
                }
              </div>
              <input type="text" class="form-control mono-font" [(ngModel)]="tagFormColor" style="margin-top: 8px;" />
            </div>

            <div class="form-group">
              <label class="form-label">شرح حاکمیتی و حوزه شمول برچسب:</label>
              <textarea class="form-control" rows="3" [(ngModel)]="tagFormDescription"></textarea>
            </div>
          </div>
          <div class="modal-footer">
            <button class="btn btn-secondary" (click)="closeTagModal()">انصراف</button>
            <button class="btn btn-primary" (click)="saveTag()">ذخیره برچسب</button>
          </div>
        </div>
      </div>
    }

    <!-- MODAL: CREATE / EDIT CATEGORY -->
    @if (isCategoryModalOpen()) {
      <div class="modal-backdrop" (click)="closeCategoryModal()">
        <div class="modal-dialog" (click)="$event.stopPropagation()">
          <div class="modal-header">
            <h3 class="modal-title">{{ editingCategoryId() ? 'ویرایش دسته‌بندی موضوعی' : 'ایجاد دسته‌بندی موضوعی جدید' }}</h3>
            <button class="btn-close" (click)="closeCategoryModal()">✕</button>
          </div>
          <div class="modal-body">
            <div class="form-group">
              <label class="form-label">نام دسته‌بندی:</label>
              <input type="text" class="form-control" [(ngModel)]="catFormName" placeholder="مثال: Customer Data" />
            </div>

            <div class="form-group">
              <label class="form-label">توضیحات دسته‌بندی:</label>
              <textarea class="form-control" rows="3" [(ngModel)]="catFormDescription"></textarea>
            </div>
          </div>
          <div class="modal-footer">
            <button class="btn btn-secondary" (click)="closeCategoryModal()">انصراف</button>
            <button class="btn btn-primary" (click)="saveCategory()">ذخیره دسته‌بندی</button>
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
    .taxonomy-layout {
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
      padding: 18px 24px;
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

    .color-badge {
      display: inline-block;
      width: 14px;
      height: 14px;
      border-radius: 50%;
      margin-left: 6px;
      vertical-align: middle;
      border: 1px solid rgba(0, 0, 0, 0.15);
    }

    .actions-row {
      display: flex;
      gap: 6px;
    }

    .color-presets {
      display: flex;
      gap: 8px;
      margin-top: 4px;
    }

    .preset-btn {
      width: 28px;
      height: 28px;
      border-radius: 6px;
      border: 2px solid transparent;
      cursor: pointer;
    }

    .preset-btn.selected {
      border-color: #0f172a;
      transform: scale(1.1);
    }

    .category-tree-wrapper {
      padding: 20px 24px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    .cat-tree-node {
      border: 1px solid var(--slate-200);
      border-radius: var(--radius-md);
      overflow: hidden;
    }

    .node-row {
      padding: 12px 16px;
      display: flex;
      align-items: center;
      gap: 12px;
      background-color: #ffffff;
    }

    .root-node {
      background-color: var(--slate-50);
      border-bottom: 1px solid var(--slate-200);
    }

    .cat-title {
      font-size: 0.92rem;
      font-weight: 700;
      color: var(--slate-800);
    }

    .cat-desc {
      font-size: 0.82rem;
      flex: 1;
    }

    .node-actions {
      display: flex;
      gap: 6px;
      margin-right: auto;
    }

    .sub-tree {
      padding: 8px 24px 8px 12px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      background-color: #fafafa;
    }

    .btn-close {
      background: none;
      border: 1px solid var(--slate-300);
      border-radius: 50%;
      width: 28px;
      height: 28px;
      cursor: pointer;
    }

    .empty-state {
      padding: 40px;
      text-align: center;
      color: var(--slate-500);
    }
  `],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TaxonomyManagementComponent implements OnInit {
  private readonly http = inject(HttpClient);

  readonly activeTab = signal<'tags' | 'categories'>('tags');
  readonly tags = signal<TagItem[]>([]);
  readonly categoryTree = signal<CategoryTreeNode[]>([]);

  // Modals state
  readonly isTagModalOpen = signal<boolean>(false);
  readonly editingTagId = signal<string | null>(null);
  tagFormName = '';
  tagFormColor = '#3b82f6';
  tagFormDescription = '';

  readonly isCategoryModalOpen = signal<boolean>(false);
  readonly editingCategoryId = signal<string | null>(null);
  catFormParentId: string | null = null;
  catFormName = '';
  catFormDescription = '';

  readonly colorPresets = ['#ef4444', '#dc2626', '#f59e0b', '#eab308', '#10b981', '#3b82f6', '#8b5cf6', '#64748b'];

  readonly toastMessage = signal<string | null>(null);
  readonly toastType = signal<'success' | 'error'>('success');

  ngOnInit(): void {
    this.loadTags();
    this.loadCategories();
  }

  loadTags(): void {
    this.http.get<TagItem[]>('/api/v1/tags').subscribe({
      next: (data) => this.tags.set(data),
      error: () => this.showToast('خطا در دریافت لیست تگ‌ها', 'error'),
    });
  }

  loadCategories(): void {
    this.http.get<CategoryTreeNode[]>('/api/v1/categories/tree').subscribe({
      next: (tree) => this.categoryTree.set(tree),
      error: () => this.showToast('خطا در دریافت درخت دسته‌بندی‌ها', 'error'),
    });
  }

  // Tag Handlers
  openCreateTagModal(): void {
    this.editingTagId.set(null);
    this.tagFormName = '';
    this.tagFormColor = '#3b82f6';
    this.tagFormDescription = '';
    this.isTagModalOpen.set(true);
  }

  openEditTagModal(tag: TagItem): void {
    this.editingTagId.set(tag.id);
    this.tagFormName = tag.name;
    this.tagFormColor = tag.color;
    this.tagFormDescription = tag.description || '';
    this.isTagModalOpen.set(true);
  }

  closeTagModal(): void {
    this.isTagModalOpen.set(false);
  }

  saveTag(): void {
    if (!this.tagFormName) return;

    const payload = {
      name: this.tagFormName,
      color: this.tagFormColor,
      description: this.tagFormDescription,
    };

    if (this.editingTagId()) {
      this.http.put(`/api/v1/tags/${this.editingTagId()}`, payload).subscribe({
        next: () => {
          this.showToast('برچسب با موفقیت بروزرسانی شد');
          this.closeTagModal();
          this.loadTags();
        },
        error: () => this.showToast('خطا در بروزرسانی تگ', 'error'),
      });
    } else {
      this.http.post('/api/v1/tags', payload).subscribe({
        next: () => {
          this.showToast('برچسب جدید با موفقیت ایجاد شد');
          this.closeTagModal();
          this.loadTags();
        },
        error: () => this.showToast('خطا در ثبت تگ جدید', 'error'),
      });
    }
  }

  deleteTag(tag: TagItem): void {
    if (!confirm(`آیا از حذف برچسب "${tag.name}" اطمینان دارید؟`)) return;

    this.http.delete(`/api/v1/tags/${tag.id}`).subscribe({
      next: () => {
        this.showToast('برچسب با موفقیت حذف شد');
        this.loadTags();
      },
      error: () => this.showToast('خطا در حذف برچسب', 'error'),
    });
  }

  // Category Handlers
  openCreateCategoryModal(parentId: string | null): void {
    this.editingCategoryId.set(null);
    this.catFormParentId = parentId;
    this.catFormName = '';
    this.catFormDescription = '';
    this.isCategoryModalOpen.set(true);
  }

  openEditCategoryModal(cat: any): void {
    this.editingCategoryId.set(cat.id);
    this.catFormParentId = cat.parent_id || null;
    this.catFormName = cat.name;
    this.catFormDescription = cat.description || '';
    this.isCategoryModalOpen.set(true);
  }

  closeCategoryModal(): void {
    this.isCategoryModalOpen.set(false);
  }

  saveCategory(): void {
    if (!this.catFormName) return;

    const payload = {
      name: this.catFormName,
      description: this.catFormDescription,
      parent_id: this.catFormParentId,
    };

    if (this.editingCategoryId()) {
      this.http.put(`/api/v1/categories/${this.editingCategoryId()}`, payload).subscribe({
        next: () => {
          this.showToast('دسته‌بندی با موفقیت بروزرسانی شد');
          this.closeCategoryModal();
          this.loadCategories();
        },
        error: () => this.showToast('خطا در بروزرسانی دسته‌بندی', 'error'),
      });
    } else {
      this.http.post('/api/v1/categories', payload).subscribe({
        next: () => {
          this.showToast('دسته‌بندی با موفقیت ایجاد شد');
          this.closeCategoryModal();
          this.loadCategories();
        },
        error: () => this.showToast('خطا در ایجاد دسته‌بندی', 'error'),
      });
    }
  }

  deleteCategory(catId: string): void {
    if (!confirm('آیا از حذف این دسته‌بندی اطمینان دارید؟')) return;

    this.http.delete(`/api/v1/categories/${catId}`).subscribe({
      next: () => {
        this.showToast('دسته‌بندی با موفقیت حذف شد');
        this.loadCategories();
      },
      error: () => this.showToast('خطا در حذف دسته‌بندی', 'error'),
    });
  }

  showToast(msg: string, type: 'success' | 'error' = 'success'): void {
    this.toastMessage.set(msg);
    this.toastType.set(type);
    setTimeout(() => this.toastMessage.set(null), 3500);
  }
}
